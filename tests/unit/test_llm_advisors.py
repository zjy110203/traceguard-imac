"""RUN-005 WP2：三类 LLM 顾问离线单测（FakeProvider，不访问真实模型）。

覆盖安全边界：
- 编译器不得虚构页面元素、不得绑定/批准 Oracle、失败必须回退规则并标记；
- 探针规划不得发明探针/模板，参数白名单过滤；
- 修复顾问不得触及断言/Oracle/业务语义；
- 三类顾问共用同一 PromptManager 且注册幂等。
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from traceguard_agents.compiler.compiler import compile_candidate
from traceguard_agents.gateway.llm_gateway import LLMError, ModelRouter, PromptManager
from traceguard_agents.gateway.llm_planners import (
    LLMProbePlannerAdvisor,
    LLMRepairAdvisor,
    ensure_advisor_prompts,
)


class FakeProvider:
    def __init__(self, payload: dict | None = None, error: LLMError | None = None) -> None:
        self.payload = payload or {}
        self.error = error
        self.calls = 0
        self.last_kwargs: dict = {}

    def chat_json(self, messages, **kw):  # noqa: ANN001, ANN003
        self.calls += 1
        self.last_kwargs = kw
        if self.error:
            raise self.error
        return self.payload, {"content": json.dumps(self.payload), "latency_ms": 10}


TRACE = {
    "exploration_id": "expl_1",
    "business_state_api_available": "/api/state/projects",
    "action_sequence": [
        {"action": "navigate", "params": {"url": "http://127.0.0.1:8011/projects"}},
        {"action": "fill", "params": {"test_id": "project-name", "value": "Alpha"}},
        {"action": "click", "params": {"test_id": "create-project"}},
    ],
}


def _router() -> ModelRouter:
    return ModelRouter(default_model="deepseek-flash", reasoning_model="deepseek-v4-pro")


def _prompts() -> PromptManager:
    pm = PromptManager()
    ensure_advisor_prompts(pm)
    ensure_advisor_prompts(pm)  # 幂等
    return pm


def _stack(payload: dict | None = None, error: LLMError | None = None) -> dict:
    return {
        "provider": FakeProvider(payload, error),
        "router": _router(),
        "prompts": _prompts(),
        "tracker": None,
    }


# ---------------------------------------------------------------- compiler


def test_compiler_accepts_trace_backed_steps() -> None:
    payload = {
        "steps": [
            {"action": "navigate", "value": "http://127.0.0.1:8011/projects", "source": "trace#0"},
            {
                "action": "fill",
                "target": {"strategy": "test_id", "value": "project-name"},
                "value": "Alpha",
                "source": "trace#1",
            },
            {
                "action": "click",
                "target": {"strategy": "test_id", "value": "create-project"},
                "source": "trace#2",
            },
        ],
        "assertions": [
            {
                "description": "创建成功后列表出现项目",
                "kind": "business_state",
                "expected": {
                    "api": "/api/state/projects",
                    "field": "count",
                    "op": "gte",
                    "value": 1,
                },
            }
        ],
        "warnings": ["证据有限"],
    }
    result = compile_candidate(
        TRACE, "创建项目", mode="llm_plus_rule_validation", stack=_stack(payload)
    )
    assert result.compiler_version == "compiler-v2-llm"
    assert result.llm_meta["fallback"] is False
    assert len(result.candidate.steps) == 3
    assert all(s.description.startswith("来源：") for s in result.candidate.steps)
    # 规则断言保留 + LLM 断言追加；Oracle 全部未绑定
    assert len(result.candidate.assertions) >= 2
    assert all(a.oracle_id is None for a in result.candidate.assertions)
    assert result.candidate.review_status == "DRAFT"
    assert any(w.startswith("llm:") for w in result.warnings)


def test_compiler_drops_fabricated_locator_and_business_claim() -> None:
    payload = {
        "steps": [
            {
                "action": "click",
                "target": {"strategy": "test_id", "value": "ghost-button"},
                "source": "猜测",
            }
        ],
        "assertions": [
            {"description": "随意断言", "kind": "ui", "expected": {"test_id": "ghost-banner"}},
            {
                "description": "业务断言",
                "kind": "business_state",
                "expected": {"api": "/api/state/projects"},
            },
        ],
    }
    result = compile_candidate(TRACE, "创建项目", mode="llm_only", stack=_stack(payload))
    # 步骤全被丢弃 → 回退规则编译，绝不用虚构元素生成用例
    assert result.llm_meta["fallback"] is True
    assert any("LLM_COMPILER_FALLBACK" in w for w in result.warnings)
    assert all(
        (s.target is None) or (s.target.value != "ghost-button") for s in result.candidate.steps
    )
    assert all(a.oracle_id is None for a in result.candidate.assertions)


def test_compiler_llm_error_falls_back_to_rule() -> None:
    stack = _stack(error=LLMError("server", "boom"))
    result = compile_candidate(TRACE, "创建项目", mode="llm_only", stack=stack)
    assert result.compiler_version == "compiler-v1"  # 规则编译器版本
    assert result.llm_meta["fallback"] is True
    assert result.llm_meta["error"] == "server"
    assert result.candidate.steps  # 规则兜底仍产出可用候选


def test_compiler_without_credentials_marks_fallback() -> None:
    result = compile_candidate(TRACE, "创建项目", mode="llm_plus_rule_validation", stack=None)
    assert result.llm_meta["fallback"] is True
    assert result.llm_meta["error"] == "not_configured"


def test_compiler_rule_only_never_calls_model() -> None:
    stack = _stack({"steps": []})
    result = compile_candidate(TRACE, "创建项目", mode="rule_only", stack=stack)
    assert stack["provider"].calls == 0
    assert result.llm_meta == {"mode": "rule_only", "called": False, "fallback": False}


# ------------------------------------------------------------- probe planner


def _defs() -> dict:
    def d(pid, templates, cost=0.2):
        return SimpleNamespace(
            probe_id=pid,
            capability="api_read",
            risk_level="LOW",
            allowed_templates=set(templates),
            estimated_cost=cost,
        )

    return {
        "network_trace": d("network_trace", {"run_network"}),
        "http_state": d("http_state", {"get_state_projects"}, cost=1.0),
    }


def test_probe_advisor_filters_unregistered_and_bad_template() -> None:
    payload = {
        "selections": [
            {"probe_id": "run_arbitrary_sql", "template": "select_all", "info_gain": 0.9},
            {"probe_id": "network_trace", "template": "select_all", "info_gain": 0.9},
            {
                "probe_id": "http_state",
                "template": "get_state_projects",
                "params": {"template": "get_state_projects", "query": "DROP TABLE x"},
                "why": "确认是否持久化",
                "info_gain": 0.8,
            },
        ]
    }
    advisor = LLMProbePlannerAdvisor(FakeProvider(payload), _router(), _prompts())
    advice = advisor.suggest(
        [{"gap_id": "g1", "probe_id": "http_state", "why": "状态未知", "info_gain": 0.7}], _defs()
    )
    assert advice.called and advice.dropped == 2
    assert len(advice.selections) == 1
    sel = advice.selections[0]
    assert sel["probe_id"] == "http_state"
    # 任意 SQL / 越界参数被过滤掉
    assert "query" not in sel["params"]
    assert sel["params"]["template"] == "get_state_projects"


def test_probe_advisor_error_is_marked_fallback() -> None:
    advisor = LLMProbePlannerAdvisor(
        FakeProvider(error=LLMError("rate_limit", "429")), _router(), _prompts()
    )
    advice = advisor.suggest(
        [{"gap_id": "g1", "probe_id": "http_state", "why": "x", "info_gain": 0.7}], _defs()
    )
    assert advice.called and advice.fallback and advice.error == "rate_limit"
    assert advice.selections == []


# ------------------------------------------------------------ repair planner


def test_repair_advisor_only_allows_safe_paths() -> None:
    payload = {
        "changes": [
            {
                "path": "steps[0].target",
                "step_index": 0,
                "value": {"strategy": "label", "value": "名称"},
                "reason": "定位器变化",
            },
            {"path": "steps[0].expected", "step_index": 0, "value": {"x": 1}, "reason": "改预期"},
            {"path": "assertions[0].expected", "step_index": None, "value": {}, "reason": "绕过"},
            {"path": "steps[9].target", "step_index": 9, "value": {}, "reason": "越界"},
            {
                "path": "run_config.wait_timeout",
                "step_index": None,
                "value": 15000,
                "reason": "等待不足",
            },
        ],
        "warnings": ["仅建议"],
    }
    advisor = LLMRepairAdvisor(FakeProvider(payload), _router(), _prompts())
    advice = advisor.suggest(
        symptom="locator timeout", steps=[{"step_id": "s0"}], assertions=[], step_count=2
    )
    paths = [c["path"] for c in advice.changes]
    assert paths == ["steps[0].target", "run_config.wait_timeout"]
    assert all(c["suggested_by"].startswith("llm:") for c in advice.changes)
    assert any("触及禁止字段" in w for w in advice.warnings)


def test_repair_advisor_no_valid_change_is_fallback() -> None:
    advisor = LLMRepairAdvisor(
        FakeProvider({"changes": [{"path": "assertions[0].expected", "value": {}}]}),
        _router(),
        _prompts(),
    )
    advice = advisor.suggest(symptom="x", steps=[], assertions=[], step_count=1)
    assert advice.fallback is True and advice.changes == []


def test_advisor_prompt_versions_are_independent() -> None:
    pm = _prompts()
    for name in ("compile_advisor", "probe_planner", "repair_planner"):
        prompt = pm.get(name, "v1")
        assert prompt["system"] and prompt["user"]
    with pytest.raises(LLMError):
        pm.get("probe_planner", "v2")
