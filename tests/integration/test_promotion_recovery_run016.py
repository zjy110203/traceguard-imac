"""RUN-016 WP2.3：Promotion Failed 的安全恢复能力。

覆盖：
1. 版本漂移（别人已晋升新版本）→ 恢复评估为 BLOCKED_VERSION_DRIFT，不盲目重试；
2. 真实可恢复场景 → 必须显式 confirm + 人类 operator，重试后只新增 1 个版本；
3. CAS 已提交但提案没落库 → 评估为 NOOP_ALREADY_PROMOTED，只收敛不重复晋升；
4. 缺少人工审批 / Oracle 不满足 → BLOCKED，不允许绕过；
5. 重复调用恢复不会重复创建 TestCase 版本（幂等）。
"""

from __future__ import annotations

import uuid

import pytest
from traceguard_api.runtime import Runtime
from traceguard_api.services.repair_service import RepairService
from traceguard_core.errors import AppError
from traceguard_persistence import Database

ORACLE = "oracle_p16_a1"


@pytest.fixture()
def env(work_tmp, monkeypatch):  # type: ignore[no-untyped-def]
    base = work_tmp / f"promo16-{uuid.uuid4().hex[:8]}"
    base.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("TG_ARTIFACT_DIR", str(base / "artifacts"))
    monkeypatch.setenv("TG_LLM_PROVIDER", "")
    from traceguard_core.config import reset_settings_cache

    reset_settings_cache()
    db = Database(f"sqlite:///{base / 'promo.db'}")
    rt = Runtime(db)
    rt.catalog.create_oracle(
        {
            "oracle_id": ORACLE,
            "assertion_id": "a1",
            "expected_behavior": "标题必须存在",
            "requirement_ref": "REQ-16",
            "version": 1,
        },
        approved=True,
        approved_by="role:qa-lead",
    )
    tc = "tcase_promo16"
    rt.catalog.create_testcase(_payload(tc), title="promo16", review_status="PENDING")
    rt.catalog.review_testcase(tc, 1, "APPROVED", reviewer="role:qa-lead")
    yield rt, tc
    db.dispose()
    reset_settings_cache()


def _payload(tc: str) -> dict:
    return {
        "testcase_id": tc,
        "version": 1,
        "workspace_id": "default",
        "steps": [
            {"step_id": "s1", "action": "navigate", "value": "/projects"},
            {"step_id": "s2", "action": "click", "target": {"strategy": "test_id", "value": "nav-a"}},
        ],
        "assertions": [
            {
                "assertion_id": "a1",
                "description": "标题存在",
                "kind": "ui",
                "required": True,
                "expected": {"test_id": "page-title"},
                "oracle_id": ORACLE,
            }
        ],
        "run_config": {"base_url": "http://127.0.0.1:8011"},
        "compile_source": "human",
        "created_at": "2026-01-01T00:00:00Z",
    }


def _approved_proposal(rt: Runtime, tc: str, key: str = "a") -> str:
    svc = RepairService(rt)
    doc = svc.propose(
        testcase_id=tc,
        base_version=1,
        changes=[
            {
                "path": "steps[].target",
                "step_index": 1,
                "value": {"strategy": "test_id", "value": f"nav-{key}"},
                "reason": "locator fix",
            }
        ],
        reason=key,
        created_by="human:dev",
        actor="human:dev",
        repair_source="human:dev",
    )
    svc.validate(doc["proposal_id"])
    doc = svc.get(doc["proposal_id"])
    doc["sandbox_status"] = "PASSED"
    doc["sandbox_verdict"] = "PASSED"
    doc["sandbox_run_id"] = f"run_sandbox_{key}"
    svc._save(doc)
    svc.submit_for_approval(doc["proposal_id"])
    svc.approve(doc["proposal_id"], "human:lead", "ok")
    return doc["proposal_id"]


def _force_failed(rt: Runtime, proposal_id: str) -> None:
    svc = RepairService(rt)
    doc = svc.get(proposal_id)
    doc["status"] = "PROMOTION_FAILED"
    doc["failure_reason"] = "INJECTED_TRANSIENT_CAS_FAILURE"
    doc["promotion_record"] = {
        "idempotency_key": "promo-failed",
        "result": "CAS_ERROR",
        "current_version": doc["base_version"],
        "base_version": doc["base_version"],
        "detail": "simulated transient DB failure",
        "at": "2026-01-01T00:00:00Z",
    }
    svc._save(doc)


def test_version_drift_is_blocked(env) -> None:  # type: ignore[no-untyped-def]
    rt, tc = env
    svc = RepairService(rt)
    proposal = _approved_proposal(rt, tc, "drift")
    # 另一个提案先晋升成功 → 正式版本变成 v2
    other = _approved_proposal(rt, tc, "other")
    svc.promote(other, idempotency_key="promo-other")
    assert rt.catalog.approved_version_number(tc) == 2
    svc.promote(proposal, idempotency_key="promo-drift")  # 产生 VERSION_CONFLICT → PROMOTION_FAILED
    plan = svc.promotion_recovery_plan(proposal)
    assert plan["action"] == "BLOCKED_VERSION_DRIFT"
    with pytest.raises(AppError) as exc:
        svc.promote_recover(
            proposal, idempotency_key="promo-drift-recover", confirm=True, actor="human:lead"
        )
    assert "PROMOTION_RECOVERY_BLOCKED" in str(exc.value)


def test_recovery_requires_explicit_confirmation_and_human(env) -> None:  # type: ignore[no-untyped-def]
    rt, tc = env
    svc = RepairService(rt)
    proposal = _approved_proposal(rt, tc, "confirm")
    _force_failed(rt, proposal)
    with pytest.raises(AppError) as exc:
        svc.promote_recover(
            proposal, idempotency_key="k1", confirm=False, actor="human:lead"
        )
    assert "REQUIRES_HUMAN_CONFIRMATION" in str(exc.value)
    with pytest.raises(AppError) as exc2:
        svc.promote_recover(proposal, idempotency_key="k2", confirm=True, actor="agent:bot")
    assert "MUST_BE_HUMAN" in str(exc2.value)


def test_recovery_promotes_once_and_is_idempotent(env) -> None:  # type: ignore[no-untyped-def]
    rt, tc = env
    svc = RepairService(rt)
    proposal = _approved_proposal(rt, tc, "retry")
    _force_failed(rt, proposal)
    plan = svc.promotion_recovery_plan(proposal)
    assert plan["action"] == "RETRY_PROMOTE"
    assert plan["requires_confirmation"] is True
    before_versions = len(rt.catalog.list_versions(tc))

    result = svc.promote_recover(
        proposal, idempotency_key="promo-retry-1", confirm=True, actor="human:lead"
    )
    assert result["status"] == "PROMOTED"
    assert result["recovery"]["executed"] == "RETRY_PROMOTE"
    assert result["promoted_version"] == 2
    history = result.get("promotion_recovery_history") or []
    assert history and history[0]["operator"] == "human:lead"
    after_versions = len(rt.catalog.list_versions(tc))
    assert after_versions == before_versions + 1, "恢复只允许新增一个版本"

    # 同一提案再次恢复：已 PROMOTED → NOOP，不再新增版本
    again = svc.promote_recover(
        proposal, idempotency_key="promo-retry-2", confirm=True, actor="human:lead"
    )
    assert again["recovery"]["executed"] == "RECONCILED"
    assert len(rt.catalog.list_versions(tc)) == after_versions


def test_receipt_exists_means_already_promoted(env) -> None:  # type: ignore[no-untyped-def]
    rt, tc = env
    svc = RepairService(rt)
    proposal = _approved_proposal(rt, tc, "receipt")
    svc.promote(proposal, idempotency_key="promo-receipt")
    # 模拟"CAS 已提交、提案保存失败"：把文档改回 PROMOTING 且不保存回执信息
    doc = svc.get(proposal)
    doc["status"] = "PROMOTING"
    doc["promoted_version"] = 0
    svc._save(doc)
    plan = svc.promotion_recovery_plan(proposal)
    assert plan["action"] == "NOOP_ALREADY_PROMOTED"
    versions_before = len(rt.catalog.list_versions(tc))
    result = svc.promote_recover(
        proposal, idempotency_key="promo-receipt-recover", confirm=True, actor="human:lead"
    )
    assert result["status"] == "PROMOTED"
    assert result["recovery"]["executed"] == "RECONCILED"
    assert len(rt.catalog.list_versions(tc)) == versions_before


def test_missing_approval_blocks_recovery(env) -> None:  # type: ignore[no-untyped-def]
    rt, tc = env
    svc = RepairService(rt)
    proposal = _approved_proposal(rt, tc, "noapproval")
    doc = svc.get(proposal)
    doc["status"] = "PROMOTION_FAILED"
    doc.pop("approved_by", None)
    doc.pop("approval_ref", None)
    svc._save(doc)
    plan = svc.promotion_recovery_plan(proposal)
    assert plan["action"] == "BLOCKED_NEEDS_APPROVAL"


def test_oracle_problem_blocks_recovery(env) -> None:  # type: ignore[no-untyped-def]
    rt, tc = env
    svc = RepairService(rt)
    proposal = _approved_proposal(rt, tc, "oracle")
    doc = svc.get(proposal)
    doc["status"] = "PROMOTION_FAILED"
    doc["candidate_doc"]["assertions"][0]["oracle_id"] = "oracle_missing_16"
    svc._save(doc)
    plan = svc.promotion_recovery_plan(proposal)
    assert plan["action"] == "BLOCKED_ORACLE"
    assert plan["oracle_problems"]


def test_recover_endpoint_maps_to_promote_permission() -> None:
    """RBAC 映射：恢复接口与晋升同级，不能落到 repair.submit（qa-engineer）。"""
    from traceguard_api.auth import action_for_request

    assert action_for_request("POST", "/api/v1/repairs/prop_x/promote-recover") == "repair.promote"
    assert action_for_request("POST", "/api/v1/repairs/prop_x/promote") == "repair.promote"
    assert action_for_request("GET", "/api/v1/repairs/prop_x/promotion-recovery") == "read"
