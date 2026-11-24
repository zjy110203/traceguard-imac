"""RUN-007-FINALIZATION-2（WP1-B）：幂等回执必须严格绑定动作身份。

覆盖的故障：
1. Proposal A 用 key-A 晋升成功；
2. Proposal B 复用同一个 key-A 晋升 → 必须被显式拒绝（CONFLICT）；
3. B 不得被标记为 PROMOTED；
4. B 不得继承 A 的 promoted_version；
5. B 的正式版本指针不得被错误修改；
6. PROMOTE 与 ROLLBACK 不得共享同一回执；
7. 不同幂等键重试同一 Proposal 仍保持幂等语义（不新增版本）；
8. 历史 PROMOTED 记录补回执必须真正落库（不是内存临时字典）；
9. 直接调用 Catalog 与经 RepairService 两条路径行为一致。
"""

from __future__ import annotations

import uuid

import pytest
from traceguard_api.runtime import Runtime
from traceguard_api.services.repair_service import RepairService
from traceguard_core.errors import AppError, ErrorCode
from traceguard_persistence import Database
from traceguard_persistence.catalog import CatalogService


def _payload() -> dict:
    return {
        "testcase_id": "tcase_receipt",
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
                "oracle_id": "oracle_receipt_a1",
            }
        ],
        "run_config": {"base_url": "http://127.0.0.1:8011"},
        "compile_source": "human",
        "created_at": "2026-01-01T00:00:00Z",
    }


@pytest.fixture()
def rt(work_tmp, monkeypatch):  # type: ignore[no-untyped-def]
    base = work_tmp / f"receipt-{uuid.uuid4().hex[:8]}"
    base.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("TG_ARTIFACT_DIR", str(base / "artifacts"))
    from traceguard_core.config import reset_settings_cache

    reset_settings_cache()
    db = Database(f"sqlite:///{base / 'receipt.db'}")
    runtime = Runtime(db)
    runtime.catalog.create_oracle(
        {
            "oracle_id": "oracle_receipt_a1",
            "assertion_id": "a1",
            "expected_behavior": "标题必须存在",
            "requirement_ref": "REQ-RECEIPT-1",
            "version": 1,
        },
        approved=True,
        approved_by="role:qa-lead",
    )
    runtime.catalog.create_testcase(_payload(), title="receipt", review_status="PENDING")
    runtime.catalog.review_testcase("tcase_receipt", 1, "APPROVED", reviewer="role:qa-lead")
    yield runtime
    db.dispose()


def _approved_proposal(rt: Runtime, *, base_version: int, locator: str) -> str:
    svc = RepairService(rt)
    prop = svc.propose(
        testcase_id="tcase_receipt",
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
    # 关注点在回执身份，不重复跑真实沙箱（沙箱另有专项测试）
    doc = svc.get(prop["proposal_id"])
    doc["status"] = "APPROVED"
    doc["sandbox_status"] = "PASSED"
    doc["approved_by"] = "role:qa-lead"
    rt.docs.put(
        doc_id=doc["proposal_id"],
        kind="repair",
        doc=doc,
        workspace_id=doc["workspace_id"],
        ref_id="tcase_receipt",
        status="APPROVED",
    )
    return doc["proposal_id"]


def _candidate(rt: Runtime, version: int, locator: str) -> dict:
    doc = dict(rt.catalog.get_version("tcase_receipt", version) or {})
    doc["steps"] = [dict(s) for s in doc["steps"]]
    doc["steps"][1]["target"] = {"strategy": "test_id", "value": locator, "source": "healed"}
    return doc


def _version_numbers(rt: Runtime) -> set[int]:
    return {int(v["version"]) for v in rt.catalog.list_versions("tcase_receipt")}


# ------------------------------------------------------------------ 服务层路径
def test_key_reuse_across_proposals_is_rejected(rt: Runtime) -> None:
    svc = RepairService(rt)
    pid_a = _approved_proposal(rt, base_version=1, locator="nav-a")
    promoted_a = svc.promote(pid_a, idempotency_key="key-A")
    assert promoted_a["status"] == "PROMOTED" and promoted_a["promoted_version"] == 2

    # B 基于 A 之后的正式版本 v2，因此不会先被 VERSION_CONFLICT 拦住，
    # 真正被检验的是"同一个幂等键能否被另一个 Proposal 复用"。
    pid_b = _approved_proposal(rt, base_version=2, locator="nav-b")
    with pytest.raises(AppError) as exc:
        svc.promote(pid_b, idempotency_key="key-A")
    assert exc.value.code is ErrorCode.CONFLICT
    assert "IDEMPOTENCY_KEY_REUSED" in exc.value.message

    doc_b = svc.get(pid_b)
    assert doc_b["status"] == "APPROVED"  # 未被标记 PROMOTED
    assert not doc_b.get("promoted_version")  # 没有继承 A 的 promoted_version
    assert rt.catalog.approved_version_number("tcase_receipt") == 2  # 正式指针未被改动
    assert _version_numbers(rt) == {1, 2}


def test_promote_and_rollback_do_not_share_receipt(rt: Runtime) -> None:
    svc = RepairService(rt)
    pid = _approved_proposal(rt, base_version=1, locator="nav-a")
    svc.promote(pid, idempotency_key="key-shared")

    with pytest.raises(AppError) as exc:
        svc.rollback(
            pid,
            operator="human:ops",
            reason="cross-kind 复用同一个键",
            idempotency_key="key-shared",
        )
    assert exc.value.code is ErrorCode.CONFLICT
    assert "IDEMPOTENCY_KEY_REUSED" in exc.value.message
    # 回滚未发生：正式版本仍是晋升版本
    assert svc.get(pid)["status"] == "PROMOTED"
    assert rt.catalog.approved_version_number("tcase_receipt") == 2


def test_different_key_retry_keeps_idempotent_semantics(rt: Runtime) -> None:
    svc = RepairService(rt)
    pid = _approved_proposal(rt, base_version=1, locator="nav-a")
    first = svc.promote(pid, idempotency_key="key-1")
    assert first["promoted_version"] == 2
    versions_before = _version_numbers(rt)

    replay = svc.promote(pid, idempotency_key="key-2")
    assert replay["status"] == "PROMOTED"
    assert replay["promoted_version"] == 2
    assert _version_numbers(rt) == versions_before
    assert rt.catalog.approved_version_number("tcase_receipt") == 2


def test_historical_promoted_backfills_persisted_receipt(rt: Runtime) -> None:
    """历史 PROMOTED 提案：补回执必须真正写库，重试才能命中。"""
    svc = RepairService(rt)
    pid = _approved_proposal(rt, base_version=1, locator="nav-a")
    svc.promote(pid, idempotency_key="key-normal")
    # 构造"历史数据"：删掉回执，只留 PROMOTED 提案
    with rt.db.session() as s:
        from traceguard_persistence.models import PromotionReceiptRow

        row = s.get(PromotionReceiptRow, "key-normal")
        assert row is not None
        s.delete(row)

    recovered = svc.promote(pid, idempotency_key="key-backfill")
    assert recovered["status"] == "PROMOTED"
    assert recovered["promoted_version"] == 2

    persisted = rt.catalog.get_promotion_receipt("key-backfill")
    assert persisted is not None, "补回执只写在内存里，没有持久化"
    assert persisted["kind"] == "PROMOTE"
    assert persisted["proposal_id"] == pid
    assert persisted["testcase_id"] == "tcase_receipt"
    assert persisted["target_version"] == 2

    # 再次使用同一个键：命中持久化回执，不新增版本
    versions = _version_numbers(rt)
    again = svc.promote(pid, idempotency_key="key-backfill")
    assert again["promoted_version"] == 2
    assert _version_numbers(rt) == versions


# ------------------------------------------------------------------ Catalog 路径
def test_catalog_rejects_key_reuse_across_proposals(rt: Runtime) -> None:
    catalog = CatalogService(rt.db)
    created = catalog.promote_version_cas(
        "tcase_receipt",
        base_version=1,
        candidate=_candidate(rt, 1, "nav-a"),
        idempotency_key="cat-key-A",
        proposal_id="prop-a",
    )
    assert created["version"] == 2

    with pytest.raises(AppError) as exc:
        catalog.promote_version_cas(
            "tcase_receipt",
            base_version=2,
            candidate=_candidate(rt, 2, "nav-b"),
            idempotency_key="cat-key-A",
            proposal_id="prop-b",
        )
    assert exc.value.code is ErrorCode.CONFLICT
    assert "IDEMPOTENCY_KEY_REUSED" in exc.value.message
    assert catalog.approved_version_number("tcase_receipt") == 2
    assert _version_numbers(rt) == {1, 2}


def test_catalog_receipt_is_bound_to_testcase_and_workspace(rt: Runtime) -> None:
    catalog = CatalogService(rt.db)
    catalog.promote_version_cas(
        "tcase_receipt",
        base_version=1,
        candidate=_candidate(rt, 1, "nav-a"),
        idempotency_key="cat-key-B",
        proposal_id="prop-a",
        workspace_id="default",
    )
    # 同一 key、同一 proposal，但换了 workspace → 不是同一个动作
    with pytest.raises(AppError) as exc:
        catalog.promote_version_cas(
            "tcase_receipt",
            base_version=2,
            candidate=_candidate(rt, 2, "nav-b"),
            idempotency_key="cat-key-B",
            proposal_id="prop-a",
            workspace_id="other-ws",
        )
    assert exc.value.code is ErrorCode.CONFLICT
    assert rt.catalog.approved_version_number("tcase_receipt") == 2


def test_promotion_fails_closed_on_invalid_result(rt: Runtime, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Catalog 返回没有有效版本号时不得宣告 PROMOTED（fail-closed）。"""
    svc = RepairService(rt)
    pid = _approved_proposal(rt, base_version=1, locator="nav-a")
    monkeypatch.setattr(rt.catalog, "promote_version_cas", lambda *a, **k: {})
    doc = svc.promote(pid, idempotency_key="key-invalid")
    assert doc["status"] == "PROMOTION_FAILED"
    assert doc["failure_reason"] == "PROMOTION_RESULT_INVALID"
    assert not doc.get("promoted_version")
    assert rt.catalog.approved_version_number("tcase_receipt") == 1
