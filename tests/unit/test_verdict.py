"""阶段2 Verdict 四态聚合测试——覆盖路线图全部合法/非法样例与 8 条补充规则。

所有断言来自已冻结规则，不用 Mock 冒充真实聚合。
"""

from __future__ import annotations

import pytest
from traceguard_contracts.common import AssertionStatus as AS
from traceguard_contracts.common import ExecutionStatus as ES
from traceguard_contracts.common import TestVerdict as TV
from traceguard_execution.verdict import AssertionOutcome, VerdictInput, aggregate


def _o(status: AS, required: bool = True, **kw) -> AssertionOutcome:  # type: ignore[no-untyped-def]
    return AssertionOutcome(assertion_id="a", status=status, required=required, **kw)


def _agg(outcomes: list[AssertionOutcome], completed: bool = True, status: ES = ES.COMPLETED) -> TV:
    return aggregate(
        VerdictInput(
            assertion_outcomes=outcomes, execution_status=status, execution_completed=completed
        )
    )


# ---------- 合法样例 ----------
def test_all_required_passed() -> None:
    assert _agg([_o(AS.PASSED), _o(AS.PASSED)]) == TV.PASSED


def test_one_required_failed() -> None:
    assert _agg([_o(AS.PASSED), _o(AS.FAILED)]) == TV.FAILED


def test_partial_not_evaluated() -> None:
    assert _agg([_o(AS.PASSED), _o(AS.NOT_EVALUATED)]) == TV.INCONCLUSIVE


def test_all_not_evaluated() -> None:
    assert _agg([_o(AS.NOT_EVALUATED), _o(AS.NOT_EVALUATED)]) == TV.NOT_EVALUATED


def test_oracle_unavailable_all() -> None:
    outs = [_o(AS.NOT_EVALUATED, oracle_available=False)]
    assert _agg(outs) == TV.NOT_EVALUATED


def test_oracle_unavailable_partial() -> None:
    outs = [_o(AS.PASSED), _o(AS.NOT_EVALUATED, oracle_available=False)]
    assert _agg(outs) == TV.INCONCLUSIVE


def test_oracle_conflict_unresolved() -> None:
    outs = [_o(AS.PASSED, oracle_conflict_unresolved=True)]
    assert _agg(outs) == TV.INCONCLUSIVE


def test_optional_failure_does_not_decide() -> None:
    """规则1补充：Optional 断言失败但必需断言全部通过 → PASSED，Optional 单独保留。"""
    outs = [_o(AS.PASSED, required=True), _o(AS.FAILED, required=False)]
    assert _agg(outs) == TV.PASSED


# ---------- 非法样例 ----------
def test_empty_required_assertions_rejected() -> None:
    with pytest.raises(ValueError, match="空集不得判 PASSED"):
        _agg([_o(AS.PASSED, required=False)])


def test_failed_not_masked_by_interruption() -> None:
    """规则5：已知必需失败不得因后续中断而判 PASSED。"""
    assert _agg([_o(AS.FAILED)], completed=False) == TV.FAILED


def test_incomplete_execution_not_passed() -> None:
    """规则6：未完成执行但已通过部分断言 → 不得 PASSED。"""
    assert _agg([_o(AS.PASSED)], completed=False) == TV.INCONCLUSIVE


# ---------- 四态区分与维度独立 ----------
def test_inconclusive_vs_not_evaluated_distinct() -> None:
    assert _agg([_o(AS.PASSED), _o(AS.NOT_EVALUATED)]) == TV.INCONCLUSIVE
    assert _agg([_o(AS.NOT_EVALUATED)]) == TV.NOT_EVALUATED


def test_execution_status_independent_of_verdict() -> None:
    """规则8：execution_status 与 test_verdict 独立，COMPLETED 可对应 FAILED。"""
    v = _agg([_o(AS.FAILED)], status=ES.COMPLETED)
    assert v == TV.FAILED and ES.COMPLETED == ES.COMPLETED  # 两维度各自独立存在

    from traceguard_execution.verdict import verify_execution_verdict_independence

    assert verify_execution_verdict_independence(TV.FAILED, ES.COMPLETED)
    assert verify_execution_verdict_independence(TV.PASSED, ES.FAILED)
