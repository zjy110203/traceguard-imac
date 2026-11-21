"""阶段5 Explorer Agent 测试：工具权限、预算、域名、Prompt Injection、Trace 完整。"""

from __future__ import annotations

import pytest
from traceguard_agents.explorer.workflow import Explorer
from traceguard_agents.gateway.model_gateway import RuleBasedPlanner
from traceguard_agents.gateway.tool_gateway import Budget, ToolCall, ToolGateway
from traceguard_core.errors import AppError


def _gateway(**kw) -> ToolGateway:  # type: ignore[no-untyped-def]
    gw = ToolGateway(**kw)
    gw.register_executor("navigate", lambda p: f"navigated:{p.get('url')}")
    gw.register_executor("fill", lambda p: f"filled:{p.get('test_id')}")
    gw.register_executor("click", lambda p: f"clicked:{p.get('test_id')}")
    gw.register_executor("read_page", lambda p: "page-content")
    return gw


# ---------------- Tool Gateway ----------------
def test_shell_tool_always_denied() -> None:
    gw = _gateway()
    d = gw.check(ToolCall("shell", {"cmd": "ls"}))
    assert d.allowed is False
    assert "TOOL_NOT_ALLOWED" in d.reason


def test_page_content_cannot_grant_permission() -> None:
    """Prompt Injection：页面内容触发的工具调用一律拒绝，不可提升权限。"""
    gw = _gateway()
    d = gw.check(
        ToolCall("navigate", {"url": "http://127.0.0.1:8011/login"}, source="page_content")
    )
    assert d.allowed is False
    assert d.reason == "PAGE_CONTENT_CANNOT_GRANT_TOOL_PERMISSION"


def test_ssrf_internal_target_blocked() -> None:
    gw = _gateway()
    # 内网/云元数据地址不在白名单
    for url in (
        "http://169.254.169.254/latest/meta-data",
        "http://10.0.0.1/",
        "http://192.168.1.1/",
    ):
        d = gw.check(ToolCall("navigate", {"url": url}))
        assert d.allowed is False, url
        assert d.reason == "UNAUTHORIZED_TARGET"


def test_approved_teamflow_host_allowed() -> None:
    gw = _gateway()
    d = gw.check(ToolCall("navigate", {"url": "http://127.0.0.1:8011/login"}))
    assert d.allowed is True


def test_wrong_port_blocked() -> None:
    gw = _gateway()
    d = gw.check(ToolCall("navigate", {"url": "http://127.0.0.1:9999/"}))
    assert d.allowed is False


def test_tool_call_budget_enforced() -> None:
    gw = _gateway(budget=Budget(max_tool_calls=1))
    gw.execute(ToolCall("navigate", {"url": "http://127.0.0.1:8011/login"}))
    with pytest.raises(AppError) as e:
        gw.execute(ToolCall("navigate", {"url": "http://127.0.0.1:8011/login"}))
    assert e.value.code.value == "AUTHORIZATION_ERROR"
    assert "BUDGET" in e.value.message


def test_write_outside_test_scope_blocked() -> None:
    gw = _gateway()
    d = gw.check(ToolCall("click", {"test_id": "x", "workspace": "production"}))
    assert d.allowed is False and d.reason == "WRITE_OUTSIDE_TEST_SCOPE"


def test_audit_log_records_all_calls() -> None:
    gw = _gateway()
    # execute 会先 check 再记审计：被拒绝的调用也要留痕
    with pytest.raises(AppError):
        gw.execute(ToolCall("shell", {"cmd": "ls"}))
    gw.execute(ToolCall("navigate", {"url": "http://127.0.0.1:8011/x"}))
    log = gw.audit_log()
    assert len(log) == 2
    assert log[0]["allowed"] is False and log[1]["allowed"] is True


# ---------------- Explorer Workflow ----------------
def test_explorer_login_trace_complete() -> None:
    gw = _gateway(budget=Budget(max_steps=10, max_tool_calls=20))
    planner = RuleBasedPlanner()

    def observer(params):  # type: ignore[no-untyped-def]
        return {"text": "TeamFlow", "url": params.get("url", "")}

    explorer = Explorer(gw, planner, observer)
    trace = explorer.explore("login to teamflow")

    d = trace.to_dict()
    # Trace 必需字段齐全
    for k in (
        "exploration_id",
        "test_intent_ref",
        "application_version",
        "browser_version",
        "agent_version",
        "prompt_version",
        "tool_version",
        "action_sequence",
        "page_state_refs",
        "event_refs",
        "evidence_refs",
        "stop_reason",
    ):
        assert k in d, k
    assert len(trace.action_sequence) >= 4  # navigate + fill×2 + click
    assert all(a["gateway"] == "allowed" for a in trace.action_sequence)
    assert trace.stop_reason == "目标完成"
    # 探索成功不是测试通过：Trace 不含 verdict
    assert "test_verdict" not in d


def test_explorer_step_budget_enforced() -> None:
    gw = _gateway(budget=Budget(max_steps=2, max_tool_calls=20))

    def observer(p):  # type: ignore[no-untyped-def]
        return {"text": "x"}

    explorer = Explorer(gw, RuleBasedPlanner(), observer)
    trace = explorer.explore("login to teamflow")
    assert len(trace.action_sequence) <= 2
    assert trace.stop_reason == "STEP_BUDGET_EXHAUSTED"


def test_explorer_gateway_denial_recorded_not_bypassed() -> None:
    """planner 产出被 Gateway 拒绝的动作时，记录并停止，不绕过。"""
    gw = _gateway()
    planner = RuleBasedPlanner()
    # 首次 navigate 到被禁域名
    original_plan = planner.plan

    def bad_plan(intent, obs, history):  # type: ignore[no-untyped-def]
        from traceguard_agents.gateway.model_gateway import ModelDecision

        if not history:
            return ModelDecision("navigate", {"url": "http://169.254.169.254/"}, "navigate")
        return original_plan(intent, obs, history)

    planner.plan = bad_plan  # type: ignore[method-assign]
    explorer = Explorer(gw, planner, lambda p: {"text": "x"})
    trace = explorer.explore("login to teamflow")
    assert any(a.get("gateway") == "denied" for a in trace.action_sequence)
    assert "BLOCKED" in trace.stop_reason
