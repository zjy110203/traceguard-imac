"""Assertion Evaluator（阶段2）：对确定性观测做判定，输出 AssertionOutcome。

原则：
- 评估基于"观测数据"和"已批准 Oracle 规则"，不由 Agent 判断；
- Oracle 不可用 → NOT_EVALUATED（绝不 PASSED）；
- 多来源冲突 → 标记 unresolved → 由 Verdict 聚合为 INCONCLUSIVE；
- Evaluation Rule 来自 OracleSpec（确定性），本模块只执行规则不发明规则。
"""

from __future__ import annotations

from typing import Any

from traceguard_contracts.common import AssertionStatus
from traceguard_contracts.models import AssertionSpec, OracleSpec

from ..verdict import AssertionOutcome


def evaluate_assertion(
    spec: AssertionSpec,
    oracle: OracleSpec | None,
    observation: dict[str, Any],
    *,
    execution_interrupted: bool = False,
) -> AssertionOutcome:
    """基于观测与 Oracle 规则确定性评估单条 Assertion。"""
    # Oracle 不可用（缺失/未批准）→ 不得判 PASSED
    if oracle is None or not oracle.expected_behavior:
        return AssertionOutcome(
            assertion_id=spec.assertion_id,
            status=AssertionStatus.NOT_EVALUATED,
            required=spec.required,
            oracle_available=False,
            reason="ORACLE_UNAVAILABLE",
            observed=observation,
        )

    if execution_interrupted:
        return AssertionOutcome(
            assertion_id=spec.assertion_id,
            status=AssertionStatus.NOT_EVALUATED,
            required=spec.required,
            oracle_available=True,
            reason="EXECUTION_INTERRUPTED",
            observed=observation,
        )

    expected = spec.expected
    status, reason = _apply_rule(expected, observation, oracle.evaluation_rule)
    return AssertionOutcome(
        assertion_id=spec.assertion_id,
        status=status,
        required=spec.required,
        oracle_available=True,
        reason=reason,
        observed=observation,
    )


def _apply_rule(
    expected: dict[str, Any],
    observation: dict[str, Any],
    oracle_rule: dict[str, Any],
) -> tuple[AssertionStatus, str]:
    """执行确定性判定规则。只做比较，不做推断。"""
    # 业务状态字段比较（Trusted Oracle 主路径）
    if "api" in expected and "field" in expected:
        field = expected["field"]
        op = expected.get("op", "eq")
        want = expected.get("value")
        got = (
            observation.get(field, observation.get("json") or {}).get(field)
            if isinstance(observation.get("json"), dict)
            else observation.get(field)
        )
        if got is None and isinstance(observation.get("json"), dict):
            got = observation["json"].get(field)
        if got is None:
            return AssertionStatus.INCONCLUSIVE, "OBSERVATION_MISSING"
        ok = _compare(got, op, want)
        return (
            AssertionStatus.PASSED if ok else AssertionStatus.FAILED
        ), f"{field}{op}{want} got={got}"

    # DOM 文本包含
    if "text_contains" in expected:
        text = str(observation.get("text", ""))
        want = str(expected["text_contains"])
        # 若明确要求某文本不存在：want 出现 且 absent 不出现 → 通过
        if expected.get("expected_absent"):
            absent = str(expected["expected_absent"])
            ok = (want in text) and (absent not in text)
            reason = f"contains={want}, absent={absent}, got={text[:80]}"
            return (AssertionStatus.PASSED if ok else AssertionStatus.FAILED), reason
        ok = want in text
        return (AssertionStatus.PASSED if ok else AssertionStatus.FAILED), f"text_contains={want}"

    # 元素计数
    if expected.get("op", "").startswith("count") or "count" in expected:
        op = expected.get("op", "count_gte")
        want = expected.get("value", 1)
        count = observation.get("count")
        if count is None:
            return AssertionStatus.INCONCLUSIVE, "COUNT_MISSING"
        ok = _compare(count, op.replace("count_", ""), want)
        return (AssertionStatus.PASSED if ok else AssertionStatus.FAILED), f"count={count}"

    # 超时检测
    if expected.get("expect") == "timeout":
        timed_out = observation.get("timed_out")
        if timed_out is None:
            return AssertionStatus.INCONCLUSIVE, "TIMEOUT_UNKNOWN"
        return (
            AssertionStatus.PASSED if bool(timed_out) else AssertionStatus.FAILED
        ), f"timed_out={timed_out}"

    # 元素存在性（test_id）
    if "test_id" in expected and "text_contains" not in expected:
        present = observation.get("present")
        if present is None:
            return AssertionStatus.INCONCLUSIVE, "PRESENCE_UNKNOWN"
        return (
            AssertionStatus.PASSED if bool(present) else AssertionStatus.FAILED
        ), f"present={present}"

    return AssertionStatus.INCONCLUSIVE, "UNSUPPORTED_RULE"


def _compare(got: Any, op: str, want: Any) -> bool:
    try:
        if op == "eq":
            return got == want
        if op == "ne":
            return got != want
        if op == "gte":
            return got >= want
        if op == "gt":
            return got > want
        if op == "lte":
            return got <= want
        if op == "lt":
            return got < want
    except TypeError:
        return False
    return False
