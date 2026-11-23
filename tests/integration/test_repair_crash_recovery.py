"""RUN-007 WP1-02：Repair 晋升 / 回滚的幂等回执与崩溃恢复（故障注入）。

覆盖的崩溃窗口：
1. CAS 提交前崩溃（无回执）→ 重试必须能重新执行，不被 "PROMOTING 不可重入" 卡死；
2. CAS 提交后、提案状态保存前崩溃（有回执）→ 重试命中回执，**不误报 VERSION_CONFLICT**；
3. 同一幂等键重试 → 返回原晋升结果，不产生额外版本；
4. 不同幂等键重试同一提案 → 不重复创建版本；
5. 回滚提交后崩溃 → 重试幂等恢复；
6. 新版本已推进时旧提案回滚 → 仍然显式失败，不覆盖。
"""

from __future__ import annotations

import uuid

import pytest
from traceguard_api.runtime import Runtime
from traceguard_api.services.repair_service import RepairService
from traceguard_core.errors import AppError, ErrorCode
from traceguard_persistence import Database


def _payload() -> dict:
    return {
        "testcase_id": "tcase_crash",
        "version": 1,
        "steps": [
            {"step_id": "s1", "action": "navigate", "value": "/projects"},
            {
                "step_id": "s2",
                "action": "click",
                "target": {"strategy": "test_id", "value": "nav-new"},
            },
        ],
        "assertions": [
            {
                "assertion_id": "a1",
                "description": "标题存在",
                "kind": "ui",
                "required": True,
                "expected": {"test_id": "page-title"},
                "oracle_id": "oracle_crash_a1",
            }
        ],
        "run_config": {"base_url": "http://127.0.0.1:8011"},
        "compile_source": "human",
        "created_at": "2026-01-01T00:00:00Z",
    }


@pytest.fixture()
def rt(work_tmp, monkeypatch):  # type: ignore[no-untyped-def]
    base = work_tmp / f"crash-{uuid.uuid4().hex[:8]}"
    base.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("TG_ARTIFACT_DIR", str(base / "artifacts"))
    from traceguard_core.config import reset_settings_cache

    reset_settings_cache()
    db = Database(f"sqlite:///{base / 'crash.db'}")
    runtime = Runtime(db)
    runtime.catalog.create_oracle(
        {
            "oracle_id": "oracle_crash_a1",
            "assertion_id": "a1",
            "expected_behavior": "标题必须存在",
            "requirement_ref": "REQ-CRASH-1",
            "version": 1,
        },
        approved=True,
        approved_by="role:qa-lead",
    )
    runtime.catalog.create_testcase(_payload(), title="crash", review_status="PENDING")
    runtime.catalog.review_testcase("tcase_crash", 1, "APPROVED", reviewer="role:qa-lead")
    yield runtime
    db.dispose()


def _approved_proposal(rt: Runtime, *, base_version: int = 1, locator: str = "nav-v2") -> str:
    svc = RepairService(rt)
    prop = svc.propose(
        testcase_id="tcase_crash",
        base_version=base_version,
        changes=[
            {
                "path": "steps[].target",
                "step_index": 1,
                "value": {"strategy": "test_id", "value": locator},
            }
        ],
        reason="locator 失效修复",
    )
    doc = svc.get(prop["proposal_id"])
    doc["status"] = "APPROVED"
    doc["sandbox_status"] = "PASSED"
    doc["approved_by"] = "role:qa-lead"
    rt.docs.put(
        doc_id=doc["proposal_id"],
        kind="repair",
        doc=doc,
        workspace_id=doc["workspace_id"],
        ref_id="tcase_crash",
        status="APPROVED",
    )
    return doc["proposal_id"]


def _force_status(rt: Runtime, proposal_id: str, status: str, **extra) -> dict:
    """模拟"提案状态没来得及保存"：直接改库里的提案状态。"""
    doc = RepairService(rt).get(proposal_id)
    doc["status"] = status
    doc.update(extra)
    return rt.docs.put(
        doc_id=proposal_id,
        kind="repair",
        doc=doc,
        workspace_id=doc["workspace_id"],
        ref_id=doc["testcase_id"],
        status=status,
    ).to_dict()


# ---------------------------------------------------------------- 正常路径
def test_promotion_writes_durable_receipt(rt: Runtime) -> None:
    svc = RepairService(rt)
    pid = _approved_proposal(rt)
    promoted = svc.promote(pid, idempotency_key="idem-normal")
    assert promoted["status"] == "PROMOTED"
    receipt = rt.catalog.get_promotion_receipt("idem-normal")
    assert receipt is not None
    assert receipt["kind"] == "PROMOTE"
    assert receipt["proposal_id"] == pid
    assert receipt["target_version"] == promoted["promoted_version"]


def test_same_key_replay_returns_original_result(rt: Runtime) -> None:
    svc = RepairService(rt)
    pid = _approved_proposal(rt)
    first = svc.promote(pid, idempotency_key="idem-repeat")
    second = svc.promote(pid, idempotency_key="idem-repeat")
    assert second["promoted_version"] == first["promoted_version"]
    assert second["promotion_record"]["receipt_recovered"] is True
    assert rt.catalog.approved_version_number("tcase_crash") == first["promoted_version"]
    assert len(rt.catalog.list_versions("tcase_crash")) == 2  # 只多了一个版本


def test_different_key_same_proposal_does_not_duplicate(rt: Runtime) -> None:
    svc = RepairService(rt)
    pid = _approved_proposal(rt)
    first = svc.promote(pid, idempotency_key="idem-a")
    second = svc.promote(pid, idempotency_key="idem-b")  # 不同幂等键
    assert second["promoted_version"] == first["promoted_version"]
    assert rt.catalog.approved_version_number("tcase_crash") == first["promoted_version"]
    assert len(rt.catalog.list_versions("tcase_crash")) == 2


# ------------------------------------------- 崩溃窗口 2：CAS 已提交、提案未保存
def test_crash_after_cas_commit_recovers_without_conflict(rt: Runtime) -> None:
    """CAS 提交后进程崩溃：提案停在 PROMOTING。重试必须命中回执恢复，而不是 VERSION_CONFLICT。"""
    svc = RepairService(rt)
    pid = _approved_proposal(rt)
    promoted = svc.promote(pid, idempotency_key="idem-crash-after")
    target = promoted["promoted_version"]

    # 模拟"CAS 已提交，但 PROMOTED 状态没写进提案"
    _force_status(rt, pid, "PROMOTING")
    stuck = svc.get(pid)
    assert stuck["status"] == "PROMOTING"
    assert rt.catalog.approved_version_number("tcase_crash") == target

    recovered = svc.promote(pid, idempotency_key="idem-crash-after")
    assert recovered["status"] == "PROMOTED"
    assert recovered["failure_reason"] != "VERSION_CONFLICT"
    assert recovered["promoted_version"] == target
    assert len(rt.catalog.list_versions("tcase_crash")) == 2


def test_reconcile_repairs_stuck_promoting(rt: Runtime) -> None:
    svc = RepairService(rt)
    pid = _approved_proposal(rt)
    svc.promote(pid, idempotency_key="idem-reconcile")
    _force_status(rt, pid, "PROMOTING")
    fixed = svc.reconcile(pid)
    assert fixed["status"] == "PROMOTED"
    assert fixed["promotion_record"]["receipt_recovered"] is True


# ------------------------------------------- 崩溃窗口 1：CAS 尚未提交
def test_crash_before_cas_commit_allows_retry(rt: Runtime) -> None:
    """无回执 + 停在 PROMOTING = CAS 没提交。重试必须能重新执行，而不是卡死。"""
    svc = RepairService(rt)
    pid = _approved_proposal(rt)
    _force_status(rt, pid, "PROMOTING", promotion_retry=1)
    assert rt.catalog.approved_version_number("tcase_crash") == 1

    recovered = svc.promote(pid, idempotency_key="idem-before-commit")
    assert recovered["status"] == "PROMOTED"
    assert rt.catalog.approved_version_number("tcase_crash") == 2
    assert recovered["promotion_retry"] == 2  # 记录了重试次数，不是静默覆盖


def test_reconcile_without_receipt_returns_to_approved(rt: Runtime) -> None:
    svc = RepairService(rt)
    pid = _approved_proposal(rt)
    _force_status(rt, pid, "PROMOTING")
    fixed = svc.reconcile(pid)
    assert fixed["status"] == "APPROVED"
    assert fixed["failure_reason"] == "PROMOTION_NOT_COMMITTED"
    # 之后仍可正常晋升
    promoted = svc.promote(pid, idempotency_key="idem-after-reconcile")
    assert promoted["status"] == "PROMOTED"


# ------------------------------------------------------------------ 回滚
def test_rollback_is_idempotent_after_crash(rt: Runtime) -> None:
    svc = RepairService(rt)
    pid = _approved_proposal(rt)
    svc.promote(pid, idempotency_key="idem-rb")
    rolled = svc.rollback(pid, operator="human:ops", reason="回归失败")
    assert rolled["status"] == "ROLLED_BACK"
    restored = rolled["rollback_record"]["restored_version"]

    # 模拟回滚已提交但状态没保存
    _force_status(rt, pid, "PROMOTED")
    again = svc.rollback(pid, operator="human:ops", reason="回归失败")
    assert again["status"] == "ROLLED_BACK"
    assert again["rollback_record"]["restored_version"] == restored
    assert again["rollback_record"]["receipt_recovered"] is True
    # 没有因为重试再产生新版本
    assert rt.catalog.approved_version_number("tcase_crash") == restored


def test_rollback_after_other_promotion_still_fails(rt: Runtime) -> None:
    svc = RepairService(rt)
    pid = _approved_proposal(rt, locator="nav-a")
    svc.promote(pid, idempotency_key="idem-x")
    other = _approved_proposal(rt, base_version=2, locator="nav-b")
    svc.promote(other, idempotency_key="idem-y")
    with pytest.raises(AppError) as exc:
        svc.rollback(pid, operator="human:ops", reason="覆盖后来版本")
    assert exc.value.code is ErrorCode.CONFLICT
    assert "ROLLBACK_CONFLICT" in exc.value.message


# ------------------------------------------------------- 并发竞争
def test_two_proposals_same_base_one_conflict(rt: Runtime) -> None:
    svc = RepairService(rt)
    first = _approved_proposal(rt, locator="nav-a")
    second = _approved_proposal(rt, locator="nav-b")
    ok = svc.promote(first, idempotency_key="idem-1st")
    assert ok["status"] == "PROMOTED"
    stale = svc.promote(second, idempotency_key="idem-2nd")
    assert stale["status"] == "PROMOTION_FAILED"
    assert stale["failure_reason"] == "VERSION_CONFLICT"
    assert (
        rt.catalog.latest_approved_version("tcase_crash")["steps"][1]["target"]["value"] == "nav-a"
    )
