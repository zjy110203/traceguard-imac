"""阶段6 TestCase Compiler 测试：可追溯、Locator、稳定性、审核门禁。"""

from __future__ import annotations

import json

import pytest
from traceguard_agents.compiler.compiler import TestCaseCompiler as Compiler


def _trace(**kw) -> dict:  # type: ignore[no-untyped-def]
    base = {
        "exploration_id": "exploration_1",
        "action_sequence": [
            {
                "action": "navigate",
                "params": {"url": "http://127.0.0.1:8011/login"},
                "gateway": "allowed",
            },
            {
                "action": "fill",
                "params": {"test_id": "username", "value": "tester"},
                "gateway": "allowed",
            },
            {
                "action": "fill",
                "params": {"test_id": "password", "value": "tester123"},
                "gateway": "allowed",
            },
            {"action": "click", "params": {"test_id": "login-submit"}, "gateway": "allowed"},
            {"action": "navigate", "params": {"url": "http://evil.com"}, "gateway": "denied"},
            {"action": "read_page", "params": {}, "gateway": "allowed"},
        ],
    }
    base.update(kw)
    return base


def test_compile_produces_draft_candidate() -> None:
    c = Compiler()
    r = c.compile(_trace(), "login to teamflow")
    assert r.candidate.review_status == "DRAFT"  # 候选，非正式
    assert r.candidate.compile_source == "compiler"
    assert r.candidate.steps  # 有步骤
    assert (
        len(r.candidate.steps) == 5
    )  # navigate + fill×2 + click + 断言观测步骤（RUN-007 WP2：UI 断言必须配 assert 步骤）
    assert r.candidate.steps[-1].action == "assert"
    assert r.candidate.steps[-1].target.value == "login-message"


def test_compile_trace_is_traceable() -> None:
    c = Compiler()
    r = c.compile(_trace(), "login")
    d = r.to_dict()
    for k in (
        "source_exploration_id",
        "source_trace_hash",
        "compiler_version",
        "prompt_version",
        "model_version",
        "locator_strategy_version",
    ):
        assert k in d and d[k], k
    assert r.source_exploration_id == "exploration_1"
    assert len(r.source_trace_hash) == 64


def test_candidate_independently_replayable() -> None:
    """候选产物是标准 TestCaseVersion JSON，可脱离 Explorer 由执行内核加载。"""
    import tempfile
    from pathlib import Path

    from traceguard_execution.testcase.loader import load_testcase_version

    c = Compiler()
    r = c.compile(_trace(), "login")
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "cand.json"
        p.write_text(json.dumps(r.candidate.model_dump(mode="json")), encoding="utf-8")
        loaded = load_testcase_version(p)
    assert loaded.testcase_id == r.candidate.testcase_id


def test_locator_priority() -> None:
    c = Compiler()
    loc = c._resolve_locator({"role": "button:登录", "test_id": "x", "css": "#y"})
    assert loc and loc.strategy == "role"  # role 优先
    loc2 = c._resolve_locator({"test_id": "login-submit", "css": "#y"})
    assert loc2 and loc2.strategy == "test_id"
    loc3 = c._resolve_locator({"xpath": "//div"})
    assert loc3 and loc3.strategy == "xpath"  # 有限兜底


def test_no_business_state_api_warning() -> None:
    c = Compiler()
    r = c.compile(_trace(), "create project")
    assert any("NO_BUSINESS_STATE_API" in w for w in r.warnings)
    # 不能宣称持久化已验证：断言里没有 business_state
    assert not any(a.kind == "business_state" for a in r.candidate.assertions)


def test_with_business_state_api_generates_trusted_assertion() -> None:
    c = Compiler()
    trace = _trace(business_state_api_available="/api/state/projects?workspace=default")
    r = c.compile(trace, "create project")
    assert any(a.kind == "business_state" for a in r.candidate.assertions)


def test_stability_classification() -> None:
    c = Compiler()
    assert c.validate_stability(["PASSED", "PASSED"])["classification"] == "STABLE_PASS"
    assert c.validate_stability(["FAILED", "FAILED"])["classification"] == "DETERMINISTIC_FAILURE"
    assert c.validate_stability(["PASSED", "FAILED"])["classification"] == "FLAKY_UNSTABLE"
    assert c.validate_stability([])["classification"] == "INSUFFICIENT_EVIDENCE"


def test_agent_cannot_self_approve() -> None:
    c = Compiler()
    r = c.compile(_trace(), "create project")
    with pytest.raises(ValueError, match="AGENT_CANNOT_SELF_APPROVE"):
        c.review(
            r.candidate,
            approver="agent-1",
            approve=True,
            oracle_bindings={a.assertion_id: "oracle_x" for a in r.candidate.assertions},
        )


def test_review_requires_oracle_binding_to_promote() -> None:
    c = Compiler()
    r = c.compile(_trace(), "create project")
    # 无 oracle 绑定 → 不晋升
    result = c.review(r.candidate, approver="human:qa", approve=True)
    assert result is None
    assert r.candidate.review_status == "DRAFT"
    # 有绑定 + 人工审批 → 晋升
    bindings = {a.assertion_id: "oracle_x" for a in r.candidate.assertions}
    result2 = c.review(r.candidate, approver="human:qa", approve=True, oracle_bindings=bindings)
    assert result2 is not None and result2.review_status == "APPROVED"


def test_review_rejection() -> None:
    c = Compiler()
    r = c.compile(_trace(), "login")
    result = c.review(r.candidate, approver="human:qa", approve=False)
    assert result is None and r.candidate.review_status == "REJECTED"


def test_intent_text_is_not_written_into_intent_ref() -> None:
    """RUN-007-FINALIZATION-2：需求正文不得写进引用字段。

    PG 的 varchar(64) 会直接拒绝长正文（SQLite 静默接受，所以只在 PG 暴露）；
    语义上 intent_ref 也只能是引用，正文放 intent_text。
    """
    c = Compiler()
    long_intent = "登录 TeamFlow 并检查 " + "很长的需求正文" * 20
    r = c.compile(_trace(), long_intent)
    assert r.candidate.intent_text == long_intent
    assert r.candidate.intent_ref is None  # 由探索服务补 intent_id
