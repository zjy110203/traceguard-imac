"""RUN-006 WP1-02 集成测试：探针证据回灌、动态重诊断与停止条件。

测试用确定性探针执行器（替换 ProbeEngine executor）驱动真实 Runtime（真实 SQLite、
真实 DocStore、真实 DiagnosisService / ProbeService / GraphService），
重点验证"判定语义"而不是 HTTP 传输：
- 新证据可以支持 / 反驳 / 排除原有假设；
- 探针失败不得当作目标系统故障证据；
- 证据冲突 → STOP_CONFLICT_UNRESOLVED；
- CONFIRMED 只能由确定性规则 + TRUSTED 确认证据产生（LLM 假设不得升级）；
- 每种停止原因都能明确给出。
"""

from __future__ import annotations

from typing import Any

import pytest
from traceguard_api.runtime import Runtime
from traceguard_api.services.diagnosis_service import DiagnosisService
from traceguard_api.services.graph_service import GraphService
from traceguard_api.services.probe_service import ProbeService
from traceguard_evidence.diagnosis.engine import (
    DiagnosisEngine,
    EvidenceRef,
    FailureDomain,
    FailureMechanism,
    Hypothesis,
    HypothesisStatus,
)
from traceguard_evidence.probe.feedback import ProbeSignal
from traceguard_persistence import Database
from traceguard_persistence.models import RunRow


def _report(*, biz_failed: bool = True, http_5xx: bool = False) -> dict[str, Any]:
    network = [{"url": "/api/projects", "status": 500 if http_5xx else 200, "failure": ""}]
    outcomes = [
        {"assertion_id": "a_ui", "kind": "ui", "status": "PASSED", "reason": ""},
    ]
    if biz_failed:
        outcomes.append(
            {
                "assertion_id": "a_biz",
                "kind": "business_state",
                "status": "FAILED",
                "reason": "count mismatch",
            }
        )
    return {
        "steps": [
            {
                "step_id": "s1",
                "action": "click",
                "status": "FAILED" if biz_failed else "PASSED",
                "error": "",
                "target": {"strategy": "test_id", "value": "create-submit"},
                "observation": {"network": network, "http_errors": network if http_5xx else []},
            },
            {
                "step_id": "s2",
                "action": "assert",
                "status": "PASSED",
                "target": {"strategy": "test_id", "value": "banner-success"},
                "value": "banner-success",
                "observation": {},
            },
        ],
        "assertion_outcomes": outcomes,
    }


@pytest.fixture()
def rt(work_tmp, monkeypatch):  # type: ignore[no-untyped-def]
    import uuid

    base = work_tmp / f"active-diag-{uuid.uuid4().hex[:8]}"
    base.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("TG_ARTIFACT_DIR", str(base / "artifacts"))
    from traceguard_core.config import reset_settings_cache

    reset_settings_cache()
    db = Database(f"sqlite:///{base / 'diag.db'}")
    runtime = Runtime(db)
    yield runtime
    db.dispose()


def _insert_run(rt: Runtime, run_id: str, report: dict[str, Any]) -> None:
    with rt.db.session() as s:
        s.add(
            RunRow(
                run_id=run_id,
                task_id=f"task_{run_id}",
                workspace_id="default",
                testcase_version_id="tcase_probe@1",
                execution_status="COMPLETED",
                test_verdict="FAILED",
                report_json=report,
            )
        )


def _patch_probe(svc: ProbeService, probe_id: str, data: dict[str, Any] | Exception) -> None:
    def executor(_params: dict[str, Any]) -> dict[str, Any]:
        if isinstance(data, Exception):
            raise data
        return data

    definition = svc.engine.definitions[probe_id]
    svc.engine.register(definition, executor)


def _diagnose(rt: Runtime, run_id: str) -> dict[str, Any]:
    svc = DiagnosisService(rt)
    return svc.diagnose(run_id, use_llm_advisor=False)


# ------------------------------------------------------------------ CONFIRMED
def test_probe_confirms_persistence_and_stops(rt: Runtime) -> None:
    _insert_run(rt, "run_diag", _report())
    diag = _diagnose(rt, "run_diag")
    assert diag["outcome"] == "INCONCLUSIVE"
    persistence = [h for h in diag["hypotheses"] if h["failure_mechanism"] == "PERSISTENCE"]
    assert persistence  # 初始诊断已生成假设（而不是等探针才凭空出现）

    svc = ProbeService(rt)
    _patch_probe(
        svc,
        "http_state",
        {"template": "get_state_projects", "response": {"ok": True, "count": 0, "keys": []}},
    )
    plan = svc.plan_and_probe("run_diag", diag["diagnosis_id"], max_iterations=3)

    assert plan["stop_reason"] == "STOP_CONFIRMED"
    assert plan["diagnosis_before"]["outcome"] == "INCONCLUSIVE"
    assert plan["diagnosis_after"]["outcome"] == "CONFIRMED"
    assert plan["diagnosis_after"]["root_causes"]
    assert plan["steps"][0]["applied_to_diagnosis"] is True

    updated = rt.docs.get(diag["diagnosis_id"]).doc
    assert updated["outcome"] == "CONFIRMED"
    assert updated["evaluation_history"]  # 重诊断留痕
    assert updated["probe_feedback"]
    confirmed = [h for h in updated["hypotheses"] if h["status"] == "CONFIRMED"]
    assert confirmed and confirmed[0]["failure_mechanism"] == "PERSISTENCE"

    # Disposition 随结论更新（不再停留在 NEEDS_MORE_EVIDENCE）
    disp = rt.docs.list(kind="disposition", ref_id=diag["diagnosis_id"], limit=1)[0]
    assert disp.doc["kind"] != "NEEDS_MORE_EVIDENCE"

    # 图上出现探针节点与它评估的假设
    graph = GraphService(rt).build_for_run("run_diag")
    kinds = {n["node_type"] for n in graph["nodes"]}
    assert "ProbeExecution" in kinds
    assert any(e["relation"] == "evaluates" for e in graph["edges"])


# ------------------------------------------------- 事后观测不得冒充历史状态
def test_post_hoc_probe_does_not_refute_run_scope_hypothesis(rt: Runtime) -> None:
    """RUN-010 WP4：事后探针与 Run 时"未落库"假设冲突 → 生成 TIMING 新假设，
    而不是错误反驳/排除 Run 作用域假设（RUN-009 误差分析的主根因修复）。

    本测试的 Run 有 ended_at：探针 observed_at(现在) > ended_at → POST_HOC，
    裁决权降级（不确认、不排除），并生成"状态查询滞后"新假设。
    """
    _insert_run(rt, "run_diag2", _report())
    # 补充 ended_at（真实 COMPLETED Run 都有）：让探针成为可判定的事后观测
    import time as _time

    from sqlalchemy import update as _update

    with rt.db.session() as s:
        s.execute(
            _update(RunRow).where(RunRow.run_id == "run_diag2").values(ended_at=_time.time() - 30)
        )
    diag = _diagnose(rt, "run_diag2")

    svc = ProbeService(rt)
    _patch_probe(
        svc,
        "http_state",
        {
            "template": "get_state_projects",
            "response": {"ok": True, "count": 2, "keys": ["a", "b"]},
        },
    )
    plan = svc.plan_and_probe("run_diag2", diag["diagnosis_id"], max_iterations=3)

    assert plan["diagnosis_after"]["outcome"] != "CONFIRMED"
    updated = rt.docs.get(diag["diagnosis_id"]).doc
    persistence = [h for h in updated["hypotheses"] if h["failure_mechanism"] == "PERSISTENCE"][0]
    # POST_HOC 反驳：附着证据但不排除、不确权
    assert persistence["contradicting"], "反驳证据应附着（可追溯）"
    assert all(
        ref.get("trust") == "POST_HOC" or ref.get("time_alignment") == "POST_HOC"
        for ref in persistence["contradicting"]
    )
    assert persistence["excluded_by"] == []  # 事后观测不得排除 Run 时假设
    # 生成"状态查询滞后（TIMING）"新假设
    timing = [
        h
        for h in updated["hypotheses"]
        if h["failure_mechanism"] == "TIMING" and "滞后" in h["description"]
    ]
    assert timing, updated["hypotheses"]
    # 回灌留痕：time_alignment 全部记录
    assert updated["probe_feedback"]
    assert all(fb.get("time_alignment") == "POST_HOC" for fb in updated["probe_feedback"])
    assert updated["root_causes"] == []


# ------------------------------------------------------- 探针失败不是证据
def test_probe_failure_is_not_target_failure_evidence(rt: Runtime) -> None:
    _insert_run(rt, "run_diag3", _report())
    diag = _diagnose(rt, "run_diag3")
    baseline = {
        h["hypothesis_id"]: (len(h["supporting"]), len(h["contradicting"]))
        for h in diag["hypotheses"]
    }

    svc = ProbeService(rt)
    _patch_probe(svc, "http_state", ConnectionError("TEAMFLOW_STATE_QUERY_FAILED"))
    _patch_probe(svc, "network_trace", RuntimeError("no observation"))

    plan = svc.plan_and_probe("run_diag3", diag["diagnosis_id"], max_iterations=4)
    attempts = [s for s in plan["steps"] if s["status"] in {"FAILED", "TIMED_OUT"}]
    assert attempts, plan["steps"]
    assert all(s["applied_to_diagnosis"] is False for s in attempts)

    updated = rt.docs.get(diag["diagnosis_id"]).doc
    after = {
        h["hypothesis_id"]: (len(h["supporting"]), len(h["contradicting"]))
        for h in updated["hypotheses"]
    }
    assert after == baseline  # 探针失败没有污染任何假设
    assert updated["outcome"] != "CONFIRMED"


# ------------------------------------------------------------ 冲突不可裁决
def test_feedback_detects_conflicting_evidence(rt: Runtime) -> None:
    """同一假设同时拿到 TRUSTED 支持与 TRUSTED 反驳 → unresolved_conflicts。"""
    _insert_run(rt, "run_diag4", _report())
    doc = {
        "diagnosis_id": "diag_conflict",
        "run_id": "run_diag4",
        "workspace_id": "default",
        "symptom": "业务断言失败",
        "outcome": "PROBABLE",
        "disposition": "NEEDS_MORE_EVIDENCE",
        "root_causes": [],
        "hypotheses": [
            {
                "hypothesis_id": "hyp_persist",
                "description": "业务未持久化（待证据）",
                "failure_domain": "PRODUCT",
                "failure_mechanism": "PERSISTENCE",
                "supporting": [{"id": "evd_old", "trust": "TRUSTED", "confirming": False}],
                "contradicting": [],
                "status": "SUPPORTED",
                "contributing_factors": [],
                "flaky_characteristic": False,
                "suggested_by": "rule",
            }
        ],
    }
    rt.docs.put(
        doc_id="diag_conflict",
        kind="diagnosis",
        doc=doc,
        workspace_id="default",
        ref_id="run_diag4",
        status="PROBABLE",
    )
    svc = DiagnosisService(rt)
    signal = ProbeSignal(
        probe_id="http_state",
        execution_id="probeexec_test",
        status="SUCCEEDED",
        evidence_ids=["evd_probe_state"],
        data={"response": {"ok": True, "count": 3, "keys": ["a"]}},
    )
    out = svc.reassess("diag_conflict", [signal])
    assert out["after"]["unresolved_conflicts"] == ["hyp_persist"]
    assert out["after"]["outcome"] != "CONFIRMED"
    persisted = rt.docs.get("diag_conflict").doc
    assert persisted["unresolved_conflicts"] == ["hyp_persist"]
    # 冲突不裁决：交人工（P03 未决策，不得自行判定谁对）
    assert persisted["disposition"] == "MANUAL_REVIEW"


def test_conflict_stops_loop_before_spending_more_budget(rt: Runtime) -> None:
    _insert_run(rt, "run_diag4b", _report())
    doc = {
        "diagnosis_id": "diag_conflict2",
        "run_id": "run_diag4b",
        "workspace_id": "default",
        "symptom": "业务断言失败",
        "outcome": "INCONCLUSIVE",
        "disposition": "MANUAL_REVIEW",
        "root_causes": [],
        "unresolved_conflicts": ["hyp_persist"],
        "hypotheses": [],
    }
    rt.docs.put(
        doc_id="diag_conflict2",
        kind="diagnosis",
        doc=doc,
        workspace_id="default",
        ref_id="run_diag4b",
        status="INCONCLUSIVE",
    )
    svc = ProbeService(rt)
    plan = svc.plan_and_probe("run_diag4b", "diag_conflict2", max_iterations=3)
    assert plan["stop_reason"] == "STOP_CONFLICT_UNRESOLVED"
    assert plan["steps"] == []
    assert plan["budget"]["probes_used"] == 0


# ------------------------------------------------------------ 预算/权限停止
def test_budget_exhaustion_stops_with_explicit_reason(rt: Runtime) -> None:
    _insert_run(rt, "run_diag5", _report())
    diag = _diagnose(rt, "run_diag5")
    svc = ProbeService(rt)
    _patch_probe(
        svc,
        "http_state",
        {"template": "get_state_projects", "response": {"count": 1, "keys": ["a"]}},
    )
    budget = svc.budget_for(run_id="run_diag5", diagnosis_id=diag["diagnosis_id"])
    svc.budgets.close(budget.scope_key, "STOP_CONFIRMED")
    plan = svc.plan_and_probe("run_diag5", diag["diagnosis_id"], max_iterations=2)
    assert plan["stop_reason"] == "STOP_BUDGET_CLOSED"
    assert plan["steps"] == []


def test_permission_revoked_stops_with_permission_reason(rt: Runtime) -> None:
    _insert_run(rt, "run_diag6", _report())
    diag = _diagnose(rt, "run_diag6")
    svc = ProbeService(rt)
    svc.engine.revoke_permission("probe.read")
    plan = svc.plan_and_probe("run_diag6", diag["diagnosis_id"], max_iterations=2)
    assert plan["stop_reason"] == "STOP_PERMISSION_INVALID"


# ------------------------------------------------------- LLM 不得确认根因
@pytest.mark.parametrize("suggested_by", ["llm", "llm:deepseek-flash", "LLM:deepseek-v4-pro"])
def test_llm_hypothesis_cannot_be_confirmed(suggested_by: str) -> None:
    engine = DiagnosisEngine()
    h = Hypothesis(
        hypothesis_id="hyp_llm",
        description="LLM 猜测",
        failure_domain=FailureDomain.PRODUCT,
        failure_mechanism=FailureMechanism.API,
        supporting=[EvidenceRef("evd_1", "TRUSTED", True)],
        status=HypothesisStatus.CONFIRMED,  # 试图自带 CONFIRMED
        suggested_by=suggested_by,
    )
    result = engine.diagnose("symptom", [h])
    assert result.outcome.value != "CONFIRMED"
    assert result.root_causes == []
    assert h.status in {HypothesisStatus.CANDIDATE, HypothesisStatus.SUPPORTED}


def test_llm_advice_declares_params_not_applied(rt: Runtime) -> None:
    """LLM 返回的参数没有参与执行时，报告不得宣称"已应用"。"""
    from traceguard_api.services.probe_service import ProbeService as _PS
    from traceguard_evidence.probe.planner import ProbeDefinition

    class _Advice:
        called = True
        fallback = False
        error = ""
        model = "deepseek-flash"
        dropped: list = []
        selections = [{"probe_id": "http_state", "info_gain": 0.9, "rationale": "x"}]

        def suggest(self, gaps, definitions):  # type: ignore[no-untyped-def]
            return self

    svc = _PS(rt)
    d = ProbeDefinition(
        probe_id="http_state",
        capability="api_read",
        permission_required="probe.read",
        allowed_templates={"get_state_projects"},
        estimated_cost=1.0,
    )
    candidates = [(d, 0.5)]
    notes = svc._apply_llm_advice(_Advice(), candidates, {"http_state": {"probe_id": "http_state"}})
    assert notes["effect"] == "ranking_only"
    assert notes["params_used_in_execution"] is False
    assert notes["applied_to_execution"] is False


def test_dom_probe_uses_failed_step_target_when_assert_never_ran() -> None:
    """RUN-011 WP2/WP3：定位器失效时断言步骤未执行，DOM 探针仍必须能判定机制。

    真实缺陷：`_expected_test_ids()` 只看 assert 步骤；定位器在 click 步骤就失败，
    断言步骤不存在 → 期望集合为空 → DOM 探针直接 skipped，
    导致 LOCATOR 与 NETWORK 长期混淆（RUN-010 v4 的 V4-SIMILAR-002 即为此）。
    """
    from traceguard_evidence.probe.feedback import SUPPORT, assess

    report = {
        "steps": [
            {
                "step_id": "s4",
                "action": "click",
                "status": "FAILED",
                "error": "Locator.click: Timeout 8000ms exceeded",
                "target": {"strategy": "test_id", "value": "create-submit"},
            }
        ],
        "assertion_outcomes": [
            {
                "assertion_id": "a_ui",
                "kind": "ui",
                "status": "NOT_EVALUATED",
                "expected": {"test_id": "banner-success"},
            }
        ],
    }
    hypotheses = [
        {
            "hypothesis_id": "h_loc",
            "failure_mechanism": "LOCATOR",
            "status": "SUPPORTED",
            "supporting": [],
            "contradicting": [],
        },
        {
            "hypothesis_id": "h_ui",
            "failure_mechanism": "UI_RENDERING",
            "status": "CANDIDATE",
            "supporting": [],
            "contradicting": [],
        },
    ]
    signal = ProbeSignal(
        probe_id="dom_snapshot",
        execution_id="exec_dom",
        status="SUCCEEDED",
        evidence_ids=["evd_dom_wp2"],
        data={"excerpt": '<button data-testid="nav-new">新建</button><div data-testid="banner-success">ok</div>'},
    )
    fb = assess(signal, hypotheses, report, run_ended_at=None)
    updates = {u.hypothesis_id: u for u in fb.updates}
    assert "h_loc" in updates and updates["h_loc"].relation == SUPPORT, fb.to_dict()
    assert "create-submit" in updates["h_loc"].reason
    # 断言元素存在 → 不得把"渲染缺失"当作支持
    assert "h_ui" not in updates or updates["h_ui"].relation != SUPPORT, fb.to_dict()
    assert fb.notes["missing_failed_step_targets"] == ["create-submit"]
