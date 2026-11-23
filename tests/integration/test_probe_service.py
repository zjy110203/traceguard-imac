"""RUN-005 WP1.1 / WP1.2 集成测试：ProbeService 预算隔离 + Active Diagnosis 闭环。

使用真实 Runtime（SQLite 文件库 + DocStore 持久化），不启动真实浏览器；
探针执行器失败属预期（TeamFlow 未启动、无 DOM 证据），用于验证失败事实保留。
"""

from __future__ import annotations

from typing import Any

import pytest
from traceguard_api.runtime import Runtime
from traceguard_api.services.probe_service import ProbeService
from traceguard_persistence import Database
from traceguard_persistence.models import RunRow


def _fake_report() -> dict[str, Any]:
    return {
        "run_id": "run_probe",
        "steps": [
            {
                "step_id": "s1",
                "action": "click",
                "status": "FAILED",
                "error": "Locator.wait_for: Timeout 8000ms exceeded",
                "observation": {
                    "network": [
                        {"url": "/api/projects", "status": 200, "failure": ""},
                        {"url": "/api/projects", "status": 500, "failure": ""},
                    ]
                },
            }
        ],
        "assertion_outcomes": [
            {
                "assertion_id": "a_biz",
                "kind": "business_state",
                "status": "FAILED",
                "reason": "count mismatch",
            }
        ],
    }


def _insert_run(rt: Runtime, run_id: str) -> None:
    with rt.db.session() as s:
        s.add(
            RunRow(
                run_id=run_id,
                task_id="task_probe",
                workspace_id="default",
                testcase_version_id="tcase_probe@1",
                execution_status="COMPLETED",
                test_verdict="FAILED",
                report_json=_fake_report(),
            )
        )


@pytest.fixture()
def rt(work_tmp, monkeypatch):  # type: ignore[no-untyped-def]
    import uuid

    base = work_tmp / f"probe-{uuid.uuid4().hex[:8]}"
    base.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("TG_ARTIFACT_DIR", str(base / "artifacts"))
    from traceguard_core.config import reset_settings_cache

    reset_settings_cache()
    db = Database(f"sqlite:///{base / 'probe.db'}")
    runtime = Runtime(db)
    _insert_run(runtime, "run_probe")
    _insert_run(runtime, "run_probe_b")
    yield runtime
    db.dispose()


class FakeClock:
    def __init__(self) -> None:
        self.t = 1_000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, s: float) -> None:
        self.t += s


def test_service_budgets_are_per_run_and_persisted(rt: Runtime) -> None:
    svc = ProbeService(rt)
    b1 = svc.budget_for(run_id="run_probe", workspace_id="default")
    b2 = svc.budget_for(run_id="run_probe_b", workspace_id="default")
    assert b1.budget_id != b2.budget_id
    svc.execute("network_trace", {"template": "run_network", "run_id": "run_probe"}, "default",
                run_id="run_probe", budget=b1)
    assert b1.probes_used == 1 and b2.probes_used == 0

    # 预算文档已持久化（重启后可恢复，且不混用）
    stored = rt.docs.get(b1.budget_id)
    assert stored is not None and stored.doc["probes_used"] == 1

    svc2 = ProbeService(rt)
    restored = svc2.budget_for(run_id="run_probe", workspace_id="default")
    assert restored.probes_used == 1
    assert svc2.budget_for(run_id="run_probe_b", workspace_id="default").probes_used == 0


def test_long_running_service_does_not_expire_new_budgets(rt: Runtime) -> None:
    clock = FakeClock()
    svc = ProbeService(rt, clock=clock)
    clock.advance(3600)  # 服务已运行 1 小时
    budget = svc.budget_for(run_id="run_probe", workspace_id="default")
    assert budget.time_left() == pytest.approx(60.0)
    assert budget.exhausted_reason() == ""


def test_plan_and_probe_runs_real_loop_and_records_evidence(rt: Runtime) -> None:
    svc = ProbeService(rt)
    plan = svc.plan_and_probe("run_probe", max_iterations=3)
    assert plan["steps"], "至少应执行一个探针，而不是只返回静态建议"
    assert plan["stop_reason"]
    assert plan["budget"]["probes_used"] == len(plan["steps"])
    assert plan["gaps_before"], "失败 Run 必须存在证据缺口"
    # 每个步骤都能追溯到 execution 与预算
    for step in plan["steps"]:
        assert step["execution_id"] and step["budget"]["scope_key"] == "run:run_probe"
        doc = rt.docs.get(step["execution_id"])
        assert doc is not None and doc.doc["run_id"] == "run_probe"
    # 成功探针的证据必须关联原 Run
    if plan["evidence_ids"]:
        evd = rt.evidence.get(plan["evidence_ids"][0])
        assert evd is not None and evd.run_id == "run_probe"
        assert evd.content["budget_scope"] == "run:run_probe"
    # 计划本身持久化
    assert rt.docs.get(plan["plan_id"]) is not None


def test_plan_stops_immediately_when_root_cause_confirmed(rt: Runtime) -> None:
    rt.docs.put(
        doc_id="diag_confirmed",
        kind="diagnosis",
        doc={
            "diagnosis_id": "diag_confirmed",
            "run_id": "run_probe",
            "outcome": "CONFIRMED",
            "disposition": "PRODUCT_DEFECT",
        },
        workspace_id="default",
        ref_id="run_probe",
        status="CONFIRMED",
    )
    svc = ProbeService(rt)
    plan = svc.plan_and_probe("run_probe", "diag_confirmed")
    assert plan["steps"] == []
    assert plan["stop_reason"] == "STOP_CONFIRMED"
    assert plan["gaps_after"] == []
    assert plan["budget"]["closed"] is True
    # 关闭后再次探测必须被明确拒绝
    blocked = svc.execute(
        "network_trace", {"template": "run_network", "run_id": "run_probe"}, "default",
        run_id="run_probe", diagnosis_id="diag_confirmed",
    )
    assert blocked["status"] == "BLOCKED"
    assert blocked["reason"] == "PROBE_BUDGET_CLOSED"


def test_plan_links_diagnosis_and_records_plan(rt: Runtime) -> None:
    rt.docs.put(
        doc_id="diag_inconclusive",
        kind="diagnosis",
        doc={
            "diagnosis_id": "diag_inconclusive",
            "run_id": "run_probe",
            "outcome": "INCONCLUSIVE",
            "disposition": "NEEDS_MORE_EVIDENCE",
        },
        workspace_id="default",
        ref_id="run_probe",
        status="INCONCLUSIVE",
    )
    svc = ProbeService(rt)
    plan = svc.plan_and_probe("run_probe", "diag_inconclusive", max_iterations=2)
    assert plan["diagnosis_id"] == "diag_inconclusive"
    diag = rt.docs.get("diag_inconclusive")
    assert diag is not None
    assert diag.doc["last_probe_plan_id"] == plan["plan_id"]
    # 诊断级预算与 Run 级预算互不干扰
    assert plan["budget"]["scope_key"] == "diagnosis:diag_inconclusive"
    assert svc.budget_for(run_id="run_probe").probes_used == 0


def test_suggest_exposes_gaps_and_budget_scope(rt: Runtime) -> None:
    svc = ProbeService(rt)
    suggestions = svc.suggest("run_probe")
    assert suggestions
    assert all("gap_id" in s and "budget_scope" in s for s in suggestions)
    assert {s["probe_id"] for s in suggestions} >= {"network_trace"}
