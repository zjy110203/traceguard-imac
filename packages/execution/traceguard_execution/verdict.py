"""Test Verdict 聚合器——阶段2 已冻结规则（G01/F05）。

规则（资料/路线图 v1.1 阶段2，已冻结，不得篡改）：
- 正式 TestCase 至少一条必需 Assertion；空集非法，不得自动 PASSED。
- Assertion 状态：PASSED / FAILED / INCONCLUSIVE / NOT_EVALUATED。
- 聚合表：
  1. 至少一个必需 Assertion 明确失败 → FAILED
  2. 所有必需 Assertion 通过且执行正常、Oracle 有效 → PASSED
  3. 无明确失败，但部分必需 Assertion 无法裁决 → INCONCLUSIVE
  4. 没有任何必需 Assertion 得到有效评估 → NOT_EVALUATED
- 8 条补充规则全部实现，见 aggregate() 注释。
- execution_status 与 test_verdict 永远独立，不由一方推导另一方。
- Oracle 不可用不是 PASSED；多来源冲突无法裁决 → INCONCLUSIVE。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from traceguard_contracts.common import AssertionStatus, ExecutionStatus, TestVerdict


@dataclass
class AssertionOutcome:
    assertion_id: str
    status: AssertionStatus
    required: bool = True
    oracle_available: bool = True
    oracle_conflict_unresolved: bool = False
    reason: str = ""
    observed: dict[str, Any] = field(default_factory=dict)


@dataclass
class VerdictInput:
    assertion_outcomes: list[AssertionOutcome]
    execution_status: ExecutionStatus
    execution_completed: bool  # 执行是否正常走完（不由 execution_status 推导）


def _effective_evaluated(o: AssertionOutcome) -> bool:
    """是否得到有效评估：PASSED/FAILED/INCONCLUSIVE 都算有结果；NOT_EVALUATED 不算。"""
    return o.status in (
        AssertionStatus.PASSED,
        AssertionStatus.FAILED,
        AssertionStatus.INCONCLUSIVE,
    )


def aggregate(inp: VerdictInput) -> TestVerdict:
    """按已冻结规则聚合 Test Verdict。"""
    required = [o for o in inp.assertion_outcomes if o.required]
    # Optional Assertion 不参与 Verdict 聚合（规则1补充），但由调用方保留独立结果。

    # F05：空必需 Assertion 集合非法。抛错而非返回 PASSED。
    if not required:
        raise ValueError(
            "INVALID_TESTCASE: 正式 TestCase 至少需要一条必需 Assertion（空集不得判 PASSED）"
        )

    # 规则5：已知必需失败不得因后续中断被掩盖；规则1：任何必需失败 → FAILED
    if any(o.status == AssertionStatus.FAILED for o in required):
        return TestVerdict.FAILED

    # 规则2（Oracle 不可用）：必需 Assertion 的 Oracle 不可用时不得 PASSED
    # 规则3（冲突未裁决）：多来源冲突无法裁决 → INCONCLUSIVE
    unresolved = [o for o in required if (not o.oracle_available) or o.oracle_conflict_unresolved]
    if unresolved:
        # 规则：全部必需 Oracle 不可用且无任何有效评估 → NOT_EVALUATED；否则 INCONCLUSIVE
        evaluated = [o for o in required if _effective_evaluated(o)]
        if not evaluated and all(not o.oracle_available for o in required):
            return TestVerdict.NOT_EVALUATED
        return TestVerdict.INCONCLUSIVE

    # 规则6：执行未走完，不能仅因已完成部分断言通过就判 PASSED
    if not inp.execution_completed:
        evaluated = [o for o in required if _effective_evaluated(o)]
        if not evaluated:
            return TestVerdict.NOT_EVALUATED
        return TestVerdict.INCONCLUSIVE

    # 规则4：没有任何必需 Assertion 得到有效评估 → NOT_EVALUATED
    required_evaluated = [o for o in required if _effective_evaluated(o)]
    if not required_evaluated:
        return TestVerdict.NOT_EVALUATED

    # 规则3（聚合表）：无明确失败但部分必需 Assertion 无法裁决（INCONCLUSIVE/NOT_EVALUATED）
    if any(
        o.status in (AssertionStatus.INCONCLUSIVE, AssertionStatus.NOT_EVALUATED) for o in required
    ):
        return TestVerdict.INCONCLUSIVE

    # 规则2（聚合表）：所有必需 Assertion PASSED 且执行正常且 Oracle 有效 → PASSED
    if all(o.status == AssertionStatus.PASSED for o in required):
        # 规则1补充：Optional 断言不决定 Verdict，仅保留独立结果
        return TestVerdict.PASSED

    # 兜底：不应到达；保守判 INCONCLUSIVE，绝不误判 PASSED
    return TestVerdict.INCONCLUSIVE


def verify_execution_verdict_independence(verdict: TestVerdict, status: ExecutionStatus) -> bool:
    """规则8：execution_status 与 test_verdict 独立，任一组合都允许存在。返回 True 表示组合合法。"""
    # 所有组合都合法：例如 COMPLETED+FAILED、FAILED(execution)+PASSED(部分场景) 都可表达
    return isinstance(verdict, TestVerdict) and isinstance(status, ExecutionStatus)
