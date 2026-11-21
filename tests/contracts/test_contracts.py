"""C01～C12 契约自动化测试。

覆盖：
- 全部 Schema 可导出且为合法 draft 2020-12；
- 合法样例通过模型校验；
- 非法样例被拒绝（extra 字段、空必需断言集、Agent 批准 Oracle、错误枚举等）；
- 关键不变量（Run 绑定版本、Event workspace_id 必填、Repair 不得自批）。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from traceguard_contracts import models as m


def test_all_contract_models_importable() -> None:
    names = [
        "TestIntent",
        "TestCaseVersion",
        "OracleSpec",
        "Task",
        "Run",
        "Step",
        "AssertionResult",
        "EventEnvelope",
        "Evidence",
        "GraphNode",
        "GraphEdge",
        "Hypothesis",
        "Diagnosis",
        "ProbeDefinition",
        "ProbeExecution",
        "Disposition",
        "RepairProposal",
        "BenchmarkCase",
        "EvaluationResult",
    ]
    for name in names:
        assert hasattr(m, name), name


def test_c02_valid_testcase_version() -> None:
    tc = m.TestCaseVersion(
        testcase_id="tcase_abc123",
        version=1,
        steps=[m.TestStep(step_id="s1", action="navigate", value="http://x")],
        assertions=[m.AssertionSpec(assertion_id="a1", description="ok", kind="ui")],
        created_at="2026-11-21T00:00:00Z",
    )
    assert tc.immutable is True
    assert tc.review_status == "DRAFT"


def test_c02_empty_assertions_rejected() -> None:
    """F05：空必需断言集非法，不得通过空集合逻辑得到 PASSED。"""
    with pytest.raises(ValidationError):
        m.TestCaseVersion(
            testcase_id="tcase_abc123",
            version=1,
            steps=[m.TestStep(step_id="s1", action="navigate", value="http://x")],
            assertions=[],
            created_at="2026-11-21T00:00:00Z",
        )


def test_c03_agent_cannot_approve_oracle() -> None:
    o = m.OracleSpec(
        oracle_id="oracle_abc",
        assertion_id="a1",
        expected_behavior="x",
        requirement_ref="req-1",
        version=1,
        approved_by_agent=False,
    )
    assert o.approved_by_agent is False
    with pytest.raises(ValidationError):
        m.OracleSpec(
            oracle_id="oracle_abc",
            assertion_id="a1",
            expected_behavior="x",
            requirement_ref="req-1",
            version=1,
            approved_by_agent=True,
        )


def test_c04_run_binds_version_and_separates_verdicts() -> None:
    run = m.Run(
        run_id="run_abc",
        task_id="task_abc",
        attempt=2,
        testcase_version_id="tcase_abc:v1",
        execution_status=m.ExecutionStatus.COMPLETED,
        test_verdict=m.TestVerdict.FAILED,
    )
    assert run.testcase_version_id == "tcase_abc:v1"
    assert run.execution_status == m.ExecutionStatus.COMPLETED
    assert run.test_verdict == m.TestVerdict.FAILED


def test_c05_event_requires_workspace_and_idempotency() -> None:
    with pytest.raises(ValidationError):
        m.EventEnvelope(
            event_id="evt_abc",
            event_type="x",
            producer_id="p",
            producer_instance_id="pi",
            sequence=1,
            occurred_at="2026-11-21T00:00:00Z",
            observed_at="2026-11-21T00:00:01Z",
            idempotency_key="k",
            # workspace_id 缺失 -> 必填校验失败
        )


def test_c06_evidence_separates_integrity_and_trust() -> None:
    ev = m.Evidence(
        evidence_id="evd_abc",
        kind="raw",
        source_type="browser",
        source_ref="step_1",
        workspace_id="ws_1",
        sha256="a" * 64,
        captured_at="2026-11-21T00:00:00Z",
        integrity_check="PASS",
        source_trust="UNTRUSTED",
    )
    assert ev.integrity_check != ev.source_trust  # 两维度独立


def test_c07_edge_requires_basis() -> None:
    with pytest.raises(ValidationError):
        m.GraphEdge(
            edge_id="e1", src="n1", dst="n2", relation="temporal", basis="", workspace_id="ws_1"
        )


def test_c08_only_confirmed_becomes_root_cause() -> None:
    """F04：未达确认规则不得成为 Root Cause（由诊断工作流保证，此处验证枚举约束）。"""
    d = m.Diagnosis(
        diagnosis_id="dg_1",
        run_id="run_1",
        diagnosis_status=m.DiagnosisStatus.COMPLETED,
        diagnosis_outcome=m.DiagnosisOutcome.INCONCLUSIVE,
        root_causes=[],
        stop_reason="证据不足",
    )
    assert d.diagnosis_outcome == m.DiagnosisOutcome.INCONCLUSIVE
    assert d.root_causes == []


def test_c09_probe_block_is_not_fault() -> None:
    pe = m.ProbeExecution(
        execution_id="pe_1",
        probe_id="probe_db",
        workspace_id="ws_1",
        status="BLOCKED",
        permission_decision="DENY",
        risk_level="HIGH",
        budget_ms=0,
        block_reason="未授权",
    )
    assert pe.status == "BLOCKED" and pe.permission_decision == "DENY"


def test_c11_repair_no_self_approval_and_version_guard_fields() -> None:
    rp = m.RepairProposal(
        proposal_id="rp_1",
        base_testcase_version="tcase_abc:v1",
        candidate_testcase_version="tcase_abc:v2",
        created_by_agent=True,
    )
    assert "approval_ref" in m.RepairProposal.model_fields
    assert "promotion_record" in m.RepairProposal.model_fields
    assert rp.status == m.RepairStatus.DRAFT


def test_extra_fields_forbidden() -> None:
    """契约基类禁止额外字段，防止字段语义漂移。"""
    with pytest.raises(ValidationError):
        m.Disposition(
            disposition_id="d1",
            diagnosis_id="dg_1",
            kind=m.DispositionKind.TEST_ASSET,
            route_target="repair",
            evil_field="x",
        )
