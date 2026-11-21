"""阶段9 Active Diagnosis Planner 测试：五项校验、安全边界、评分、停止条件。"""

from __future__ import annotations

from traceguard_evidence.probe.planner import (
    ActivePlanner,
    ProbeBudget,
    ProbeDefinition,
    ProbeEngine,
    ScoreWeights,
)


def _engine(**kw) -> ProbeEngine:  # type: ignore[no-untyped-def]
    e = ProbeEngine(**kw)
    e.grant_permission("probe.read")
    d = ProbeDefinition(
        probe_id="db_read", capability="database_read", permission_required="probe.read",
        allowed_templates={"get_project_by_key"}, allowed_tables={"tf_project"},
        max_rows=2, timeout_ms=5000, estimated_cost=1.0,
    )
    e.register(d, lambda p: {"rows": [{"id": i, "password": "secret"} for i in range(5)]})
    return e


def test_permission_denied_and_audited() -> None:
    e = ProbeEngine()  # 不授予权限
    e.register(ProbeDefinition("p", "api_read", "probe.read"), lambda p: {})
    r = e.execute("p", {"workspace": "ws"}, "ws")
    assert r.status == "BLOCKED" and r.reason == "PROBE_PERMISSION_DENIED"
    assert any(a["decision"] == "AUTHORIZATION_ERROR" for a in e.audit_log())


def test_cross_workspace_access_denied() -> None:
    e = _engine()
    r = e.execute("db_read", {"workspace": "other-ws", "template": "get_project_by_key"}, "my-ws")
    assert r.status == "BLOCKED" and r.reason == "CROSS_WORKSPACE_ACCESS_DENIED"


def test_arbitrary_write_sql_denied() -> None:
    e = _engine()
    r = e.execute("db_read", {"workspace": "ws", "query": "DROP TABLE tf_project"}, "ws")
    assert r.status == "BLOCKED" and r.reason == "WRITE_SQL_FORBIDDEN"


def test_unwhitelisted_template_denied() -> None:
    e = _engine()
    r = e.execute("db_read", {"workspace": "ws", "template": "select_all_secrets"}, "ws")
    assert r.status == "BLOCKED" and r.reason == "QUERY_TEMPLATE_NOT_WHITELISTED"


def test_unwhitelisted_table_denied() -> None:
    e = _engine()
    r = e.execute("db_read", {"workspace": "ws", "table": "user_credentials"}, "ws")
    assert r.status == "BLOCKED" and r.reason == "TABLE_NOT_WHITELISTED"


def test_successful_probe_redacts_and_limits_rows() -> None:
    e = _engine()
    r = e.execute("db_read", {"workspace": "ws", "template": "get_project_by_key"}, "ws")
    assert r.status == "SUCCEEDED"
    assert r.redacted is True
    assert len(r.data["rows"]) == 2  # max_rows=2
    assert all(row["password"] == "***REDACTED***" for row in r.data["rows"])


def test_probe_result_has_traceable_source() -> None:
    e = _engine()
    r = e.execute("db_read", {"workspace": "ws", "template": "get_project_by_key"}, "ws")
    assert r.source["probe_id"] == "db_read"
    assert r.source["template"] == "get_project_by_key"
    assert "timestamp" in r.source


def test_probe_count_budget() -> None:
    e = _engine(budget=ProbeBudget(max_probes=1, max_total_cost=100))
    e.execute("db_read", {"workspace": "ws", "template": "get_project_by_key"}, "ws")
    r = e.execute("db_read", {"workspace": "ws", "template": "get_project_by_key"}, "ws")
    assert r.status == "BLOCKED" and r.reason == "PROBE_COUNT_BUDGET_EXHAUSTED"


def test_probe_cost_budget() -> None:
    e = _engine(budget=ProbeBudget(max_probes=10, max_total_cost=0.5))
    r = e.execute("db_read", {"workspace": "ws", "template": "get_project_by_key"}, "ws")
    assert r.status == "BLOCKED" and r.reason == "PROBE_COST_BUDGET_EXHAUSTED"


def test_high_risk_probe_requires_approval() -> None:
    e = ProbeEngine()
    e.grant_permission("probe.read")
    e.register(ProbeDefinition("risky", "backend_log_read", "probe.read", risk_level="HIGH"), lambda p: {})
    r = e.execute("risky", {"workspace": "ws"}, "ws")
    assert r.status == "BLOCKED" and r.reason == "HIGH_RISK_PROBE_REQUIRES_APPROVAL"


# --------------- Planner ---------------
def _planner() -> ActivePlanner:  # type: ignore[no-untyped-def]
    return ActivePlanner(_engine(), ScoreWeights())


def test_eig_score_formula() -> None:
    p = ActivePlanner(_engine(), ScoreWeights(w_i=1.0, w_c=0.3, w_r=0.5))
    low = ProbeDefinition("p", "api_read", "probe.read", risk_level="LOW", estimated_cost=1.0)
    high = ProbeDefinition("q", "database_read", "probe.read", risk_level="HIGH", estimated_cost=5.0)
    # Score = 1*EIG - 0.3*C - 0.5*R
    assert abs(p.score(low, 1.0) - 0.7) < 1e-6  # 1 - 0.3 - 0
    assert p.score(high, 1.0) < p.score(low, 1.0)  # 高风险高成本高成本分


def test_stop_on_confirmed() -> None:
    p = _planner()
    probe, reason = p.choose([], confirmed=True)
    assert probe is None and reason == "STOP_CONFIRMED"


def test_stop_on_unresolved_conflict() -> None:
    p = _planner()
    d = ProbeDefinition("p", "api_read", "probe.read")
    probe, reason = p.choose([(d, 1.0)], conflicts_unresolved=True)
    assert probe is None and reason == "STOP_CONFLICT_UNRESOLVED"


def test_stop_on_no_candidates() -> None:
    p = _planner()
    probe, reason = p.choose([])
    assert probe is None and reason == "STOP_NO_HIGH_VALUE_PROBE"


def test_choose_highest_value_probe() -> None:
    p = _planner()
    low = ProbeDefinition("low", "api_read", "probe.read", risk_level="LOW", estimated_cost=1.0)
    high = ProbeDefinition("high", "database_read", "probe.read", risk_level="HIGH", estimated_cost=8.0)
    probe, reason = p.choose([(low, 0.5), (high, 1.0)])
    assert reason == "PROBE_SELECTED" and probe.probe_id == "low"


def test_stop_when_marginal_value_insufficient() -> None:
    p = _planner()
    costly = ProbeDefinition("costly", "database_read", "probe.read", risk_level="HIGH", estimated_cost=10.0)
    probe, reason = p.choose([(costly, 0.1)])
    assert probe is None and reason == "STOP_MARGINAL_VALUE_INSUFFICIENT"
