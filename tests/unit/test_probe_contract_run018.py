"""RUN-018 WP5 单元测试：探针证据契约（§5.2）与预算/安全约束（§5.5）。

覆盖：
- 契约字段完整性（probe_id / probe_execution_id / run_id / diagnosis_id / workspace_id /
  probe_status / observed_at / captured_at / source_type / source_trust / time_alignment /
  evidence_refs / raw_result_ref / failure_reason / duration_ms / cost / idempotency_key）；
- 幂等键语义：显式键重放不重复扣预算；自动键唯一（延迟复测不被误去重）；
- ProbeUnavailable → UNKNOWN（零证据，不冒充目标系统故障）；
- 写入型探针必须声明隔离与清理策略；
- Ground Truth 永不进入 Runtime Diagnosis；Benchmark 评测代码不被 Runtime Diagnosis 导入。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
from traceguard_evidence.probe.contract import (
    PROBE_CONTRACT_FIELDS,
    build_probe_evidence_contract,
    derive_idempotency_key,
    validate_probe_evidence_contract,
)
from traceguard_evidence.probe.planner import (
    ProbeBudget,
    ProbeDefinition,
    ProbeEngine,
    ProbeUnavailable,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

# =============================================================== 契约（§5.2）


def test_contract_payload_covers_all_required_fields() -> None:
    payload = build_probe_evidence_contract(
        probe_id="http_state",
        probe_execution_id="probeexec_1",
        run_id="run_1",
        diagnosis_id="diag_1",
        workspace_id="ws_1",
        probe_status="SUCCEEDED",
        observed_at=1000.0,
        captured_at=1001.0,
        evidence_refs=["evd_1"],
        failure_reason="",
        duration_ms=12,
        cost=1.0,
        idempotency_key="k1",
        run_scope=True,
        run_ended_at=1100.0,
    )
    assert set(PROBE_CONTRACT_FIELDS) <= set(payload.keys())
    assert validate_probe_evidence_contract(payload) == []
    assert payload["source_type"] == "probe"
    assert payload["source_trust"] == "TRUSTED"
    assert payload["time_alignment"] == "ALIGNED"
    assert payload["raw_result_ref"] == "probe_execution:probeexec_1"


def test_contract_time_alignment_three_states() -> None:
    base = dict(
        probe_id="http_state",
        probe_execution_id="p",
        run_id="r",
        diagnosis_id="d",
        workspace_id="w",
        captured_at=0.0,
        evidence_refs=[],
    )
    post_hoc = build_probe_evidence_contract(
        **base, probe_status="SUCCEEDED", observed_at=2000.0, run_ended_at=1500.0
    )
    assert post_hoc["time_alignment"] == "POST_HOC"
    unknown = build_probe_evidence_contract(
        **base, probe_status="FAILED", observed_at=0.0, run_ended_at=1500.0, failure_reason="x"
    )
    assert unknown["time_alignment"] == "UNKNOWN"
    assert unknown["source_trust"] == "UNKNOWN", "非 SUCCEEDED 不得持有 TRUSTED"
    assert unknown["failure_reason"] == "x"


def test_contract_validation_reports_missing_and_invalid() -> None:
    problems = validate_probe_evidence_contract({"probe_status": "WEIRD", "time_alignment": "X"})
    assert "probe_id" in problems  # 缺失字段
    assert any("probe_status invalid" in p for p in problems)
    assert any("time_alignment invalid" in p for p in problems)


def test_idempotency_key_semantics() -> None:
    # 显式键：原样保留（同一执行请求的重放标识）
    assert (
        derive_idempotency_key("http_state", explicit="client-req-1", execution_id="e1")
        == "client-req-1"
    )
    # 自动键：每次执行唯一（延迟复测 = 新请求，即使参数完全相同）
    k1 = derive_idempotency_key("http_state", run_id="r", execution_id="e1")
    k2 = derive_idempotency_key("http_state", run_id="r", execution_id="e2")
    assert k1 != k2
    assert k1.startswith("auto:http_state:")


# ====================================================== ProbeUnavailable → UNKNOWN


def test_probe_unavailable_yields_unknown_status() -> None:
    engine = ProbeEngine(ProbeBudget(scope_key="t1", max_probes=5))
    engine.grant_permission("probe.read")

    def executor(_params: dict[str, Any]) -> dict[str, Any]:
        raise ProbeUnavailable("PROBE_TARGET_UNAVAILABLE")

    engine.register(
        ProbeDefinition(
            probe_id="http_state",
            capability="api_read",
            permission_required="probe.read",
            allowed_templates={"get_state_projects"},
        ),
        executor,
    )
    result = engine.execute(
        "http_state", {"template": "get_state_projects", "workspace": "default"}, "default"
    )
    assert result.status == "UNKNOWN"
    assert "PROBE_TARGET_UNAVAILABLE" in result.reason
    assert result.data == {}
    # 审计留痕
    assert any(a.get("decision") == "UNKNOWN" for a in engine.audit_log())


# ================================================= 写入型探针隔离与清理（§5.5）


def test_write_probe_requires_isolation_and_cleanup() -> None:
    engine = ProbeEngine(ProbeBudget(scope_key="t2", max_probes=5))
    engine.grant_permission("probe.write")

    def executor(_params: dict[str, Any]) -> dict[str, Any]:
        return {"rows": [{"id": 1}]}

    engine.register(
        ProbeDefinition(
            probe_id="write_probe",
            capability="database_write",
            permission_required="probe.write",
            read_only=False,  # 写入型
            isolation="",  # 未声明隔离
            cleanup_policy="",  # 未声明清理
        ),
        executor,
    )
    result = engine.execute("write_probe", {"workspace": "default", "row": {"id": 1}}, "default")
    assert result.status == "BLOCKED"
    assert result.reason == "WRITE_PROBE_REQUIRES_ISOLATION_AND_CLEANUP"


def test_write_probe_allowed_with_isolation_and_cleanup() -> None:
    engine = ProbeEngine(ProbeBudget(scope_key="t3", max_probes=5))
    engine.grant_permission("probe.write")

    def executor(_params: dict[str, Any]) -> dict[str, Any]:
        return {"rows": [{"id": 1}]}

    engine.register(
        ProbeDefinition(
            probe_id="write_probe_ok",
            capability="database_write",
            permission_required="probe.write",
            read_only=False,
            isolation="workspace:probe-sandbox",
            cleanup_policy="POST_RUN_DELETE_BY_TOKEN",
        ),
        executor,
    )
    result = engine.execute("write_probe_ok", {"workspace": "default", "row": {"id": 1}}, "default")
    assert result.status == "SUCCEEDED"


# ============================================= Ground Truth / Benchmark 隔离（§5.5）

#: Runtime Diagnosis 侧禁止出现的模块（GT 与 Benchmark 评测代码都不允许被导入）
_FORBIDDEN_IMPORT = re.compile(
    r"^\s*(?:import|from)\s+(?:traceguard_benchmark|traceguard_teamflow)\b", re.M
)

_RUNTIME_DIAGNOSIS_SOURCES = [
    "packages/evidence/traceguard_evidence",
    "apps/api/traceguard_api/services/diagnosis_service.py",
    "apps/api/traceguard_api/services/probe_service.py",
    "apps/api/traceguard_api/services/graph_service.py",
]


def test_runtime_diagnosis_does_not_import_benchmark_or_ground_truth() -> None:
    """Benchmark 独立评测代码 / Ground Truth 不得被 Runtime Diagnosis 导入使用。"""
    offenders: list[str] = []
    for rel in _RUNTIME_DIAGNOSIS_SOURCES:
        base = REPO_ROOT / rel
        files = [base] if base.is_file() else sorted(base.rglob("*.py"))
        for f in files:
            text = f.read_text(encoding="utf-8")
            if _FORBIDDEN_IMPORT.search(text):
                offenders.append(str(f.relative_to(REPO_ROOT)))
    assert offenders == [], f"Runtime Diagnosis 引用了评测侧/GT 代码: {offenders}"


def test_strip_ground_truth_recursive() -> None:
    from traceguard_api.services.diagnosis_service import _strip_ground_truth

    payload = {
        "step_id": "s1",
        "ground_truth": {"expected": "PERSISTENCE"},
        "nested": {"GroundTruth-Ref": "gt_1", "keep": 1, "deeper": [{"ground_truth_ref": "x"}]},
        "list": [1, {"groundTruth": 2}],
    }
    cleaned = _strip_ground_truth(payload)
    assert "ground_truth" not in cleaned
    assert cleaned["nested"]["keep"] == 1
    assert "GroundTruth-Ref" not in cleaned["nested"]
    assert cleaned["nested"]["deeper"] == [{}]
    assert cleaned["list"] == [1, {}]
    assert cleaned["step_id"] == "s1"


# ====================================================== 幂等重放（ProbeService 层）


@pytest.fixture()
def rt(work_tmp, monkeypatch):  # type: ignore[no-untyped-def]
    import uuid

    base = work_tmp / f"wp5-contract-{uuid.uuid4().hex[:8]}"
    base.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("TG_ARTIFACT_DIR", str(base / "artifacts"))
    from traceguard_core.config import reset_settings_cache

    reset_settings_cache()
    from traceguard_api.runtime import Runtime
    from traceguard_persistence import Database

    db = Database(f"sqlite:///{base / 'contract.db'}")
    runtime = Runtime(db)
    yield runtime
    db.dispose()


def _insert_run(rt: Any, run_id: str) -> None:
    from traceguard_persistence.models import RunRow

    with rt.db.session() as s:
        s.add(
            RunRow(
                run_id=run_id,
                task_id=f"task_{run_id}",
                workspace_id="default",
                testcase_version_id="tcase_contract@1",
                execution_status="COMPLETED",
                test_verdict="FAILED",
                report_json={"steps": [], "assertion_outcomes": []},
            )
        )


def test_explicit_idempotency_key_replays_without_double_charge(rt: Any) -> None:
    from traceguard_api.services.probe_service import ProbeService

    _insert_run(rt, "run_idem")
    svc = ProbeService(rt)

    def executor(_params: dict[str, Any]) -> dict[str, Any]:
        return {"template": "get_state_projects", "response": {"count": 1, "keys": ["a"]}}

    svc.engine.register(svc.engine.definitions["http_state"], executor)
    params = {"template": "get_state_projects", "idempotency_key": "client-req-42"}
    first = svc.execute("http_state", params, "default", run_id="run_idem")
    second = svc.execute("http_state", params, "default", run_id="run_idem")

    assert second["execution_id"] == first["execution_id"]
    assert second["idempotent_replay"] is True
    budget = svc.budget_for(run_id="run_idem")
    assert budget.probes_used == 1, "幂等重放不得重复扣预算"


def test_probe_execution_doc_and_evidence_carry_full_contract(rt: Any) -> None:
    from traceguard_api.services.probe_service import ProbeService
    from traceguard_evidence.probe.contract import validate_probe_evidence_contract

    _insert_run(rt, "run_contract")
    svc = ProbeService(rt)

    def executor(_params: dict[str, Any]) -> dict[str, Any]:
        return {"template": "get_state_projects", "response": {"count": 0, "keys": []}}

    svc.engine.register(svc.engine.definitions["http_state"], executor)
    doc = svc.execute(
        "http_state", {"template": "get_state_projects"}, "default", run_id="run_contract"
    )
    assert validate_probe_evidence_contract(doc["contract"]) == []
    for field in PROBE_CONTRACT_FIELDS:
        assert field in doc["contract"]
    assert doc["probe_execution_id"] == doc["execution_id"]
    assert doc["probe_status"] == doc["status"]
    assert doc["cost"] == 1.0
    assert doc["raw_result_ref"] == f"probe_execution:{doc['execution_id']}"
    assert doc["idempotency_key"].startswith("auto:http_state:")
    assert doc["time_alignment"] in {"ALIGNED", "POST_HOC", "UNKNOWN"}

    # 证据 content 携带同一契约（新证据与 Run / Workspace / 时间强关联）
    assert doc["evidence_refs"]
    evidence = rt.evidence.get(doc["evidence_refs"][0])
    assert evidence is not None
    contract = (evidence.content or {}).get("probe_evidence_contract") or {}
    assert validate_probe_evidence_contract(contract) == []
    assert contract["run_id"] == "run_contract"
    assert contract["workspace_id"] == "default"
    assert contract["probe_execution_id"] == doc["execution_id"]
    assert contract["observed_at"] > 0 and contract["captured_at"] > 0
    assert contract["evidence_refs"] == doc["evidence_refs"]


def test_auto_idempotency_keys_unique_for_delayed_retest(rt: Any) -> None:
    """延迟复测（参数完全相同）是两次独立观测，不得被幂等键误去重。"""
    from traceguard_api.services.probe_service import ProbeService

    _insert_run(rt, "run_retest")
    svc = ProbeService(rt)

    def executor(_params: dict[str, Any]) -> dict[str, Any]:
        return {"template": "get_state_projects", "response": {"count": 0, "keys": []}}

    svc.engine.register(svc.engine.definitions["http_state"], executor)
    params = {"template": "get_state_projects"}
    first = svc.execute("http_state", params, "default", run_id="run_retest")
    second = svc.execute("http_state", params, "default", run_id="run_retest")
    assert first["execution_id"] != second["execution_id"]
    assert first["idempotency_key"] != second["idempotency_key"]
    budget = svc.budget_for(run_id="run_retest")
    assert budget.probes_used == 2
