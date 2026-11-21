"""Assertion Evaluator 测试：确定性判定，Oracle 独立性，False Pass 检测。"""

from __future__ import annotations

from traceguard_contracts.common import AssertionStatus
from traceguard_contracts.models import AssertionSpec, OracleSpec
from traceguard_execution.runtime.assertions import evaluate_assertion


def _oracle(oid: str = "oracle_x") -> OracleSpec:
    return OracleSpec(
        oracle_id=oid,
        assertion_id="a1",
        expected_behavior="expected",
        requirement_ref="REQ-1",
        expectation_approval={"approved": True, "approver": "human:qa"},
        evaluation_rule={"type": "api_field"},
        version=1,
    )


def test_oracle_unavailable_never_passes() -> None:
    spec = AssertionSpec(
        assertion_id="a1", description="x", kind="ui", expected={"text_contains": "ok"}
    )
    out = evaluate_assertion(spec, None, {"text": "ok"})
    assert out.status == AssertionStatus.NOT_EVALUATED
    assert out.oracle_available is False
    assert out.reason == "ORACLE_UNAVAILABLE"


def test_text_contains_pass_and_fail() -> None:
    spec = AssertionSpec(
        assertion_id="a1", description="x", kind="ui", expected={"text_contains": "登录成功"}
    )
    out = evaluate_assertion(spec, _oracle(), {"text": "登录成功"})
    assert out.status == AssertionStatus.PASSED
    out2 = evaluate_assertion(spec, _oracle(), {"text": "登录失败"})
    assert out2.status == AssertionStatus.FAILED


def test_business_state_gte() -> None:
    spec = AssertionSpec(
        assertion_id="a1",
        description="persist",
        kind="business_state",
        expected={"api": "/api/state/projects", "field": "count", "op": "gte", "value": 1},
    )
    out = evaluate_assertion(spec, _oracle(), {"count": 1})
    assert out.status == AssertionStatus.PASSED
    out2 = evaluate_assertion(spec, _oracle(), {"count": 0})
    assert out2.status == AssertionStatus.FAILED  # 揭穿假成功


def test_missing_observation_is_inconclusive() -> None:
    spec = AssertionSpec(
        assertion_id="a1",
        description="persist",
        kind="business_state",
        expected={"api": "/x", "field": "count", "op": "gte", "value": 1},
    )
    out = evaluate_assertion(spec, _oracle(), {})
    assert out.status == AssertionStatus.INCONCLUSIVE
    assert out.reason == "OBSERVATION_MISSING"


def test_execution_interrupted_not_evaluated() -> None:
    spec = AssertionSpec(
        assertion_id="a1", description="x", kind="ui", expected={"text_contains": "ok"}
    )
    out = evaluate_assertion(spec, _oracle(), {"text": "ok"}, execution_interrupted=True)
    assert out.status == AssertionStatus.NOT_EVALUATED


def test_absent_element_detection() -> None:
    """UI 故障：成功横幅不应出现，错误横幅文本出现。"""
    spec = AssertionSpec(
        assertion_id="a1",
        description="ui fault",
        kind="ui",
        expected={"text_contains": "创建失败", "expected_absent": "项目创建成功"},
    )
    # 观察文本只有错误提示，没有成功提示 → 通过
    out = evaluate_assertion(spec, _oracle(), {"text": "创建失败（UI 表现异常）"})
    assert out.status == AssertionStatus.PASSED
    # 若成功提示仍出现 → 失败
    out2 = evaluate_assertion(spec, _oracle(), {"text": "项目创建成功"})
    assert out2.status == AssertionStatus.FAILED
