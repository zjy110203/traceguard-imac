"""阶段13 全链路集成测试：把各阶段模块串成完整闭环（真实调用，非 Mock）。

闭环：
Explorer Trace → Compiler Candidate → Human Review → Formal TestCaseVersion
→ Execution(Trusted Oracle) → Event/Evidence → Graph → Diagnosis → Disposition
→ RepairProposal → Sandbox → Approval → Promotion/Rollback。

每个环节复用前序阶段已实现的真实模块，验证接口契约衔接正确。
"""

from __future__ import annotations

from traceguard_agents.compiler.compiler import TestCaseCompiler as Compiler
from traceguard_agents.healing.service import SelfHealingService
from traceguard_contracts.models import TestCaseVersion as TCVersion
from traceguard_core.ids import new_id
from traceguard_evidence.diagnosis.engine import (
    DiagnosisEngine,
    EvidenceRef,
    FailureDomain,
    FailureMechanism,
    Hypothesis,
)
from traceguard_evidence.event_store import EventStore
from traceguard_evidence.evidence_manager import EvidenceManager
from traceguard_evidence.graph.graph import EvidenceGraph
from traceguard_evidence.probe.planner import (
    ProbeBudget,
    ProbeDefinition,
    ProbeEngine,
)
from traceguard_execution.tasks.service import TaskService


def _exploration_trace() -> dict:
    return {
        "exploration_id": new_id("exploration"),
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
            {
                "action": "click",
                "params": {"test_id": "login-submit", "workspace": "test"},
                "gateway": "allowed",
            },
        ],
        "business_state_api_available": "/api/state/projects?workspace=default",
    }


def test_full_test_generation_chain() -> None:
    """TestIntent → Explorer Trace → Compiler → Review → Formal TestCaseVersion。"""
    trace = _exploration_trace()
    compiler = Compiler()
    result = compiler.compile(trace, "create project")

    assert result.candidate.review_status == "DRAFT"
    # 人工审核 + Oracle 绑定 → 正式版本
    bindings = {a.assertion_id: "oracle_x" for a in result.candidate.assertions}
    formal = compiler.review(
        result.candidate, approver="human:qa", approve=True, oracle_bindings=bindings
    )
    assert formal is not None and formal.review_status == "APPROVED"
    # 步骤可追溯到 trace
    assert result.source_exploration_id == trace["exploration_id"]
    assert any("business_state" == a.kind for a in formal.assertions)


def test_full_evidence_diagnosis_chain() -> None:
    """Event → Evidence → Graph → Diagnosis → Disposition。"""
    # Event
    store = EventStore()
    iid = store.new_producer_instance("runner")
    ev = store.append(
        event_type="api.response.500",
        server_workspace_id="ws-1",
        producer_id="runner",
        producer_instance_id=iid,
        idempotency_key="k1",
        payload={"status": 500},
        context_refs={"run_id": "run_1"},
    )
    # Evidence（权威来源）
    em = EvidenceManager()
    raw = em.add_raw(
        "evd_500",
        "api",
        "resp_1",
        "ws-1",
        {"status": 500, "token": "secret"},
        source_trust="TRUSTED",
        do_redact=True,
    )
    # Graph
    g = EvidenceGraph()
    ev_node = g.add_node("Event", ev.event_id)
    evd_node = g.add_node("Evidence", raw.evidence_id)
    g.add_edge(
        ev_node.node_id,
        evd_node.node_id,
        "emitted_by",
        basis="event.payload.response",
        workspace_id="ws-1",
    )
    # Diagnosis
    h = Hypothesis(
        hypothesis_id="h1",
        description="API 500",
        failure_domain=FailureDomain.PRODUCT,
        failure_mechanism=FailureMechanism.API,
        supporting=[EvidenceRef(raw.evidence_id, "TRUSTED", True)],
    )
    diag = DiagnosisEngine().diagnose("创建失败", [h])
    assert diag.root_causes == ["h1"]
    assert diag.disposition.value == "PRODUCT_DEFECT"
    assert g.query(relation="emitted_by")  # 图可查询


def test_full_repair_chain() -> None:
    """正式版本 → RepairProposal → Sandbox → Approval → Promotion → Rollback。"""
    svc = SelfHealingService()
    from traceguard_contracts.models import AssertionSpec, LocatorSpec, TestStep

    base = TCVersion(
        testcase_id="tcase_pipeline",
        version=1,
        steps=[
            TestStep(
                step_id="s1",
                action="click",
                target=LocatorSpec(strategy="test_id", value="old", source="human"),
            )
        ],
        assertions=[
            AssertionSpec(
                assertion_id="a1",
                description="x",
                kind="ui",
                required=True,
                expected={"test_id": "b", "text_contains": "ok"},
                oracle_id="o1",
            )
        ],
        immutable=True,
        review_status="APPROVED",
        created_at="2026-11-21T00:00:00Z",
    )
    svc.register_official(base)
    cand = base.model_copy(
        update={
            "version": 2,
            "steps": [
                base.steps[0].model_copy(
                    update={"target": LocatorSpec(strategy="test_id", value="new", source="healed")}
                )
            ],
        }
    )
    prop = svc.propose(base, cand, [{"path": "steps[].target", "from": "old", "to": "new"}])
    svc.validate(prop.proposal_id)
    assert prop.assertion_preservation == "PASS" and prop.oracle_integrity == "PASS"
    svc.submit_for_approval(prop.proposal_id)
    svc.approve(prop.proposal_id, "human:qa")
    svc.promote(prop.proposal_id, idempotency_key="pipe-1")
    assert svc.official_version("tcase_pipeline") == "tcase_pipeline:v2"
    svc.rollback(prop.proposal_id, operator="human:ops", reason="regression")
    assert svc.official_version("tcase_pipeline") == "tcase_pipeline:v1"


def test_full_task_run_chain() -> None:
    """Task → Run → 重试新 Run → 状态提交（执行平台闭环）。"""
    from traceguard_persistence.catalog import CatalogService

    svc = TaskService()
    # RUN-011-R1.1 WP1：Run 必须绑定真实存在且同 Workspace 的用例版本。
    CatalogService(svc.db).create_testcase(
        {
            "testcase_id": "tcase_x",
            "version": 1,
            "workspace_id": "default",
            "created_at": "2026-01-01T00:00:00Z",
            "steps": [
                {
                    "step_id": "s1",
                    "action": "navigate",
                    "target": {"strategy": "css", "value": "body"},
                }
            ],
            "assertions": [
                {
                    "assertion_id": "a1",
                    "description": "页面可见",
                    "kind": "ui",
                    "required": True,
                    "expected": {},
                }
            ],
        },
        title="tcase_x",
        workspace_id="default",
        review_status="DRAFT",
    )
    tid = svc.create_task("tcase_x", "pipeline-idem-" + new_id("k"), workspace_id="default")[
        "task_id"
    ]
    r1 = svc.create_run(tid, "tcase_x@1")
    lease = svc.acquire_lease(r1["run_id"], "worker-1")
    svc.submit_result(
        r1["run_id"],
        "worker-1",
        lease["fencing_token"],
        execution_status="RUNNING",
        test_verdict="NOT_EVALUATED",
    )
    svc.submit_result(
        r1["run_id"],
        "worker-1",
        lease["fencing_token"],
        execution_status="FINALIZING",
        test_verdict="FAILED",
    )
    r2 = svc.create_run(tid, "tcase_x@1")  # 重试
    assert r2["attempt"] == 2
    assert svc.get_run(r1["run_id"])["test_verdict"] == "FAILED"  # 历史保留
    assert svc.task_summary(tid)["run_count"] == 2


def test_probe_in_diagnosis_loop() -> None:
    """Active Diagnosis：证据缺口 → 选择探针 → 执行 → 新证据。"""
    eng = ProbeEngine(budget=ProbeBudget(max_probes=5, max_total_cost=50))
    eng.grant_permission("probe.read")
    probe = ProbeDefinition(
        "api_read",
        "api_read",
        "probe.read",
        allowed_templates={"get_project"},
        risk_level="LOW",
        estimated_cost=1.0,
    )
    eng.register(probe, lambda p: {"project": "TF-123", "password": "secret"})
    result = eng.execute("api_read", {"workspace": "ws", "template": "get_project"}, "ws")
    assert result.status == "SUCCEEDED"
    assert result.data["project"] == "TF-123"
    assert result.source["probe_id"] == "api_read"
