"""阶段10 Self-Healing 测试：断言保护、Oracle完整性、状态机、CAS、审批、回滚。"""

from __future__ import annotations

import pytest
from traceguard_agents.healing.service import RepairStatus, SelfHealingService
from traceguard_contracts.models import AssertionSpec, LocatorSpec
from traceguard_contracts.models import TestCaseVersion as TCVersion
from traceguard_contracts.models import TestStep as Step
from traceguard_core.errors import AppError


def _tc(version: int = 1, name: str = "UI Auto") -> TCVersion:
    return TCVersion(
        testcase_id="tcase_heal",
        version=version,
        steps=[
            Step(
                step_id="s1",
                action="click",
                target=LocatorSpec(strategy="test_id", value="old-id", source="human"),
            )
        ],
        assertions=[
            AssertionSpec(
                assertion_id="a1",
                description="must pass",
                kind="ui",
                required=True,
                expected={"test_id": "banner", "text_contains": "成功"},
                oracle_id="oracle_1",
            )
        ],
        immutable=True,
        review_status="APPROVED",
        created_at="2026-11-21T00:00:00Z",
    )


def _candidate(base: TCVersion, *, change_locator: bool = True) -> TCVersion:
    steps = []
    for s in base.steps:
        steps.append(
            s.model_copy(
                update={"target": LocatorSpec(strategy="test_id", value="new-id", source="healed")}
            )
            if change_locator
            else s
        )
    return base.model_copy(update={"version": base.version + 1, "steps": steps})


def _prop(svc: SelfHealingService, *, candidate=None, diff=None):  # type: ignore[no-untyped-def]
    base = _tc()
    cand = candidate or _candidate(base)
    d = diff or [{"path": "steps[].target", "from": "old-id", "to": "new-id"}]
    return svc.propose(base, cand, d)


# ---------- 断言保护 ----------
def test_assertion_preservation_pass() -> None:
    svc = SelfHealingService()
    svc.register_official(_tc())
    prop = _prop(svc)
    assert svc.check_assertion_preservation(prop) is True
    assert prop.assertion_preservation == "PASS"


def test_delete_required_assertion_rejected() -> None:
    svc = SelfHealingService()
    base = _tc()
    svc.register_official(base)
    # 候选删除了必需断言 → 保护失败
    cand = base.model_copy(update={"version": 2, "assertions": []})
    prop = svc.propose(base, cand, [{"path": "steps[].target", "from": "a", "to": "b"}])
    assert svc.check_assertion_preservation(prop) is False
    assert prop.assertion_preservation == "FAIL"


def test_weaken_expected_rejected() -> None:
    svc = SelfHealingService()
    base = _tc()
    svc.register_official(base)
    weak_assertion = AssertionSpec(
        assertion_id="a1",
        description="weakened",
        kind="ui",
        required=True,
        expected={"test_id": "banner", "text_contains": ""},
        oracle_id="oracle_1",
    )
    cand = base.model_copy(update={"version": 2, "assertions": [weak_assertion]})
    prop = svc.propose(base, cand, [{"path": "steps[].target", "from": "a", "to": "b"}])
    assert svc.check_assertion_preservation(prop) is False


# ---------- Oracle 完整性 ----------
def test_oracle_change_rejected() -> None:
    svc = SelfHealingService()
    base = _tc()
    svc.register_official(base)
    changed = [a.model_copy(update={"oracle_id": "oracle_HACKED"}) for a in base.assertions]
    cand = base.model_copy(update={"version": 2, "assertions": changed})
    prop = svc.propose(base, cand, [{"path": "steps[].target", "from": "a", "to": "b"}])
    assert svc.check_oracle_integrity(prop) is False


# ---------- Diff 白名单 ----------
def test_diff_forbidden_field_rejected() -> None:
    svc = SelfHealingService()
    svc.register_official(_tc())
    prop = _prop(svc, diff=[{"path": "assertions[0].required", "from": True, "to": False}])
    assert svc.validate_diff(prop) is False
    prop2 = _prop(svc, diff=[{"path": "product_code", "from": "x", "to": "y"}])
    assert svc.validate_diff(prop2) is False


# ---------- 状态机 ----------
def test_full_lifecycle() -> None:
    svc = SelfHealingService()
    svc.register_official(_tc())
    prop = _prop(svc)
    assert prop.status == RepairStatus.DRAFT
    prop = svc.validate(prop.proposal_id)
    assert prop.status == RepairStatus.VALIDATED and prop.sandbox_status == "PASSED"
    prop = svc.submit_for_approval(prop.proposal_id)
    assert prop.status == RepairStatus.PENDING_APPROVAL
    prop = svc.approve(prop.proposal_id, "human:qa")
    assert prop.status == RepairStatus.APPROVED and prop.approval_ref
    prop = svc.promote(prop.proposal_id, idempotency_key="idem-1")
    assert prop.status == RepairStatus.PROMOTED
    assert prop.promotion_record["check"] == "CAS_OK"
    assert svc.official_version("tcase_heal") == "tcase_heal:v2"


def test_validation_failure_state() -> None:
    svc = SelfHealingService()
    base = _tc()
    svc.register_official(base)
    cand = base.model_copy(update={"version": 2, "assertions": []})
    prop = svc.propose(base, cand, [{"path": "steps[].target", "from": "a", "to": "b"}])
    result = svc.validate(prop.proposal_id)
    assert result.status == RepairStatus.VALIDATION_FAILED
    assert result.sandbox_status == "FAILED"


@pytest.mark.parametrize(
    "actor", ["agent-1", "system:promotion", "llm:deepseek", "bot:ci", "worker:1"]
)
def test_non_human_cannot_self_approve(actor: str) -> None:
    """RUN-008 WP6：所有非人类命名空间都必须被主体类型门禁拒绝。"""
    svc = SelfHealingService()
    svc.register_official(_tc())
    prop = _prop(svc)
    svc.validate(prop.proposal_id)
    svc.submit_for_approval(prop.proposal_id)
    with pytest.raises(AppError) as e:
        svc.approve(prop.proposal_id, actor)
    assert e.value.code.value == "AUTHORIZATION_ERROR"


def test_non_human_cannot_reject() -> None:
    svc = SelfHealingService()
    svc.register_official(_tc())
    prop = _prop(svc)
    svc.validate(prop.proposal_id)
    svc.submit_for_approval(prop.proposal_id)
    with pytest.raises(AppError) as e:
        svc.reject(prop.proposal_id, "system:promotion")
    assert e.value.code.value == "AUTHORIZATION_ERROR"


def test_reject_is_terminal() -> None:
    svc = SelfHealingService()
    svc.register_official(_tc())
    prop = _prop(svc)
    svc.validate(prop.proposal_id)
    svc.submit_for_approval(prop.proposal_id)
    svc.reject(prop.proposal_id)
    with pytest.raises(AppError):
        svc.approve(prop.proposal_id, "human:qa")


# ---------- CAS 版本并发保护 ----------
def test_version_conflict_blocks_promotion() -> None:
    """base 版本已变（他人晋升），旧提案晋升必须被阻断，不覆盖新版本。"""
    svc = SelfHealingService()
    base = _tc()
    svc.register_official(base)
    prop = _prop(svc)
    svc.validate(prop.proposal_id)
    svc.submit_for_approval(prop.proposal_id)
    svc.approve(prop.proposal_id, "human:qa")

    # 另一个提案先晋升，把正式版本推到 v2
    other = svc.propose(
        base, _candidate(base), [{"path": "steps[].target", "from": "a", "to": "b"}]
    )
    svc.validate(other.proposal_id)
    svc.submit_for_approval(other.proposal_id)
    svc.approve(other.proposal_id, "human:other")
    svc.promote(other.proposal_id, idempotency_key="idem-other")

    # 旧提案晋升 → VERSION_CONFLICT
    result = svc.promote(prop.proposal_id, idempotency_key="idem-old")
    assert result.status == RepairStatus.PROMOTION_FAILED
    assert result.failure_reason == "VERSION_CONFLICT"
    assert svc.official_version("tcase_heal") == "tcase_heal:v2"  # 未被覆盖


def test_promotion_failed_not_disguised() -> None:
    svc = SelfHealingService()
    base = _tc()
    svc.register_official(base)
    prop = _prop(svc)
    svc.validate(prop.proposal_id)
    svc.submit_for_approval(prop.proposal_id)
    svc.approve(prop.proposal_id, "human:qa")
    # 手动制造版本冲突
    svc.register_official(_tc(version=3))
    r = svc.promote(prop.proposal_id, idempotency_key="k")
    assert r.status != RepairStatus.PROMOTED


# ---------- 回滚 ----------
def test_rollback_records_target_reason_operator() -> None:
    svc = SelfHealingService()
    base = _tc()
    svc.register_official(base)
    prop = _prop(svc)
    svc.validate(prop.proposal_id)
    svc.submit_for_approval(prop.proposal_id)
    svc.approve(prop.proposal_id, "human:qa")
    svc.promote(prop.proposal_id, idempotency_key="k")
    result = svc.rollback(prop.proposal_id, operator="human:ops", reason="regression failed")
    assert result.status == RepairStatus.ROLLED_BACK
    rb = result.promotion_record["rollback"]
    assert rb["target_version"] == "tcase_heal:v1"
    assert rb["operator"] == "human:ops" and rb["reason"] == "regression failed"
