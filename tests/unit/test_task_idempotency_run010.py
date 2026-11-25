"""RUN-010 WP3：Task 终态提交幂等、状态机冻结、Repair 重复操作防护。

覆盖指令 §3.1/§3.5 验收：
- 同一 owner + 同一 fencing token 的重复终态提交**不再覆盖** verdict/report；
- 同一幂等键重放返回原回执；
- 不同幂等键（或无键）的终态重提交按冻结语义拒绝；
- 旧 fencing token / 旧 owner 拒绝；
- 提交回执与终态记录原子存在；
- Report / Verdict 不发生静默变化；
- RUNNING→FINALIZING→COMPLETED 正常推进（多状态同 token 不误伤）；
- Repair：重复审批 / 重复晋升 / 幂等键校验。
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from traceguard_core.errors import AppError
from traceguard_execution.tasks.service import TaskService
from traceguard_persistence import Database, set_database
from traceguard_persistence.models import RunRow


def _svc(tmp_path: Path) -> TaskService:
    from traceguard_core.ids import utc_now_iso
    from traceguard_persistence.catalog import CatalogService

    db = Database(f"sqlite:///{tmp_path / f'idem-{uuid.uuid4().hex[:8]}.db'}")
    set_database(db)
    svc = TaskService(db)
    # RUN-011-R1.1 WP1：Run 只能绑定数据库中真实存在且同 Workspace 的版本。
    CatalogService(db).create_testcase(
        {
            "testcase_id": "tcase_idem",
            "version": 1,
            "workspace_id": "default",
            "created_at": utc_now_iso(),
            "steps": [
                {
                    "step_id": "s1",
                    "action": "navigate",
                    "target": {"strategy": "css", "value": "body"},
                }
            ],
            "assertions": [
                {
                    "assertion_id": "a1",
                    "description": "页面可见",
                    "kind": "ui",
                    "required": True,
                    "expected": {},
                }
            ],
        },
        title="tcase_idem",
        workspace_id="default",
        review_status="DRAFT",
    )
    return svc


def _claim_target(svc: TaskService, run_id: str) -> dict:
    """循环领取直到拿到目标 run（claim 顺序不保证 FIFO）。"""
    for _ in range(20):
        claim = svc.claim_next_run("worker-A", ttl_seconds=30)
        if claim is None:
            break
        if claim["run_id"] == run_id:
            return claim
    raise AssertionError(f"未能领取目标 run {run_id}")


def _queued_run(svc: TaskService) -> tuple[str, str]:
    task_id = svc.create_task(
        "tcase_idem", f"intent-{id(svc)}", mode="playwright", workspace_id="default"
    )["task_id"]
    run = svc.create_run(task_id, "tcase_idem@1")
    return task_id, run["run_id"]


def _claim_and_run(svc: TaskService) -> tuple[str, str, int]:
    """领取并把 Run 推进到 RUNNING，返回 (task_id, run_id, fencing_token)。"""
    _task, run_id = _queued_run(svc)
    # claim 顺序不保证 FIFO（created_at 同秒）：循环领取直到拿到目标 run
    token = None
    for _ in range(10):
        claim = svc.claim_next_run("worker-A", ttl_seconds=30)
        if claim and claim["run_id"] == run_id:
            token = claim["fencing_token"]
            break
        assert claim is None or svc.get_run(claim["run_id"])["execution_status"] != "RUNNING"
    assert token is not None, f"未能领取目标 run {run_id}"
    svc.submit_result(
        run_id, "worker-A", token, execution_status="RUNNING", test_verdict="NOT_EVALUATED"
    )
    return _task, run_id, token


# ---------------------------------------------------------------------------
# 1. 幂等键重放：同一 key 重复提交返回原回执，verdict/report 不变
# ---------------------------------------------------------------------------


def test_same_idempotency_key_replays_original_receipt(tmp_path) -> None:  # type: ignore[no-untyped-def]
    svc = _svc(tmp_path)
    _task, run_id, token = _claim_and_run(svc)
    first = svc.submit_result(
        run_id,
        "worker-A",
        token,
        execution_status="FINALIZING",
        test_verdict="PASSED",
        report={"summary": {"passed": 3}},
        idempotency_key="submit-001",
    )
    assert first["replayed"] is False
    # 同 key 重试（不同 verdict / report —— 模拟崩溃后重发）
    second = svc.submit_result(
        run_id,
        "worker-A",
        token,
        execution_status="FINALIZING",
        test_verdict="FAILED",
        report={"summary": {"passed": 0}},
        idempotency_key="submit-001",
    )
    assert second["replayed"] is True
    assert second["test_verdict"] == "PASSED", "重放必须返回原回执 verdict"
    assert second["report"] == {"summary": {"passed": 3}}
    run = svc.get_run(run_id)
    assert run["test_verdict"] == "PASSED", "Run 上的 verdict 不得被静默改写"
    assert run["report"] == {"summary": {"passed": 3}}


def test_auto_idempotency_key_makes_retry_safe(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """不传幂等键时使用确定性自动键：同代次同状态重试天然幂等。"""
    svc = _svc(tmp_path)
    _task, run_id, token = _claim_and_run(svc)
    first = svc.submit_result(
        run_id,
        "worker-A",
        token,
        execution_status="COMPLETED",
        test_verdict="PASSED",
        report={"v": 1},
    )
    # 崩溃重试：同 worker/token/状态，但 verdict 不同 → 自动同键 → 重放原回执
    retry = svc.submit_result(
        run_id,
        "worker-A",
        token,
        execution_status="COMPLETED",
        test_verdict="FAILED",
        report={"v": 2},
    )
    assert retry["replayed"] is True
    assert retry["test_verdict"] == "PASSED"
    assert retry["report"] == {"v": 1}
    assert svc.get_run(run_id)["report"] == {"v": 1}
    assert first["idempotency_key"] == retry["idempotency_key"]


# ---------------------------------------------------------------------------
# 2. 冻结语义：终态后的新提交被拒绝
# ---------------------------------------------------------------------------


def test_final_state_frozen_against_new_key_submission(tmp_path) -> None:  # type: ignore[no-untyped-def]
    svc = _svc(tmp_path)
    _task, run_id, token = _claim_and_run(svc)
    svc.submit_result(
        run_id,
        "worker-A",
        token,
        execution_status="COMPLETED",
        test_verdict="PASSED",
        report={"final": True},
        idempotency_key="key-first",
    )
    # 不同 key 的终态重提交 → 冻结拒绝（即使同 worker/token）
    with pytest.raises(AppError) as exc:
        svc.submit_result(
            run_id,
            "worker-A",
            token,
            execution_status="COMPLETED",
            test_verdict="FAILED",
            idempotency_key="key-other",
        )
    assert "FINAL_STATE_FROZEN" in str(exc.value.message)
    run = svc.get_run(run_id)
    assert run["test_verdict"] == "PASSED" and run["report"] == {"final": True}


def test_stale_token_and_old_owner_rejected_after_completion(tmp_path) -> None:  # type: ignore[no-untyped-def]
    svc = _svc(tmp_path)
    _task, run_id, token = _claim_and_run(svc)
    svc.submit_result(
        run_id, "worker-A", token, execution_status="COMPLETED", test_verdict="PASSED"
    )
    # 同 token 但换了 owner
    with pytest.raises(AppError) as e1:
        svc.submit_result(
            run_id, "worker-B", token, execution_status="COMPLETED", test_verdict="FAILED"
        )
    assert "STALE_FENCING_TOKEN" in str(e1.value.message)
    # 模拟新代次（抬升 token）：终态后仍不允许覆盖
    from sqlalchemy import update

    with svc.db.session() as s:
        s.execute(update(RunRow).where(RunRow.run_id == run_id).values(fencing_token=token + 1))
    with pytest.raises(AppError) as e2:
        svc.submit_result(
            run_id,
            "worker-A",
            token + 1,
            execution_status="COMPLETED",
            test_verdict="FAILED",
        )
    assert "FINAL_STATE_FROZEN" in str(e2.value.message)
    assert svc.get_run(run_id)["test_verdict"] == "PASSED"


# ---------------------------------------------------------------------------
# 3. 状态推进不误伤：多状态同 token 正常推进 + 回执原子存在
# ---------------------------------------------------------------------------


def test_multi_status_progression_with_receipts(tmp_path) -> None:  # type: ignore[no-untyped-def]
    svc = _svc(tmp_path)
    _task, run_id, token = _claim_and_run(svc)
    r2 = svc.submit_result(
        run_id, "worker-A", token, execution_status="FINALIZING", test_verdict="PASSED"
    )
    r3 = svc.submit_result(
        run_id, "worker-A", token, execution_status="COMPLETED", test_verdict="PASSED"
    )
    assert (r2["replayed"], r3["replayed"]) == (False, False)
    assert r2["idempotency_key"] != r3["idempotency_key"]
    from sqlalchemy import select
    from traceguard_persistence.models import RunSubmissionRow

    with svc.db.session() as s:
        rows = s.scalars(select(RunSubmissionRow).where(RunSubmissionRow.run_id == run_id)).all()
    assert {row.execution_status for row in rows} == {"RUNNING", "FINALIZING", "COMPLETED"}
    run = svc.get_run(run_id)
    assert run["execution_status"] == "COMPLETED" and run["test_verdict"] == "PASSED"
    assert run["ended_at"] is not None


def test_idempotency_key_cannot_be_reused_across_runs(tmp_path) -> None:  # type: ignore[no-untyped-def]
    svc = _svc(tmp_path)
    _t1, run_a = _queued_run(svc)
    _t2, run_b = _queued_run(svc)
    claim_a = _claim_target(svc, run_a)
    svc.submit_result(
        run_a,
        "worker-A",
        claim_a["fencing_token"],
        execution_status="RUNNING",
        test_verdict="NOT_EVALUATED",
        idempotency_key="shared-key",
    )
    claim_b = _claim_target(svc, run_b)
    with pytest.raises(AppError) as exc:
        svc.submit_result(
            run_b,
            "worker-A",
            claim_b["fencing_token"],
            execution_status="RUNNING",
            test_verdict="NOT_EVALUATED",
            idempotency_key="shared-key",
        )
    assert "IDEMPOTENCY_KEY_REUSED" in str(exc.value.message)


# ---------------------------------------------------------------------------
# 4. Repair：重复审批 / 重复晋升 / 幂等键校验
# ---------------------------------------------------------------------------


def _repair_env(tmp_path):  # type: ignore[no-untyped-def]
    from traceguard_api.runtime import Runtime
    from traceguard_api.services.repair_service import RepairService
    from traceguard_core.config import reset_settings_cache

    base = tmp_path / f"repair-{uuid.uuid4().hex[:8]}"
    base.mkdir(parents=True, exist_ok=True)
    reset_settings_cache()
    db = Database(f"sqlite:///{base / 'repair.db'}")
    set_database(db)
    rt = Runtime(db)
    rt.catalog.create_oracle(
        {
            "oracle_id": "oracle_idem_a1",
            "assertion_id": "a1",
            "expected_behavior": "标题必须存在",
            "requirement_ref": "REQ-IDEM-1",
            "version": 1,
        },
        approved=True,
        approved_by="role:qa-lead",
    )
    rt.catalog.create_testcase(_payload())
    rt.catalog.review_testcase("tcase_idem", 1, "APPROVED", reviewer="role:qa-lead")
    return rt, RepairService(rt)


def _payload() -> dict:
    return {
        "testcase_id": "tcase_idem",
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
                "oracle_id": "oracle_idem_a1",
            }
        ],
        "run_config": {"base_url": "http://127.0.0.1:8011"},
        "compile_source": "human",
        "created_at": "2026-01-01T00:00:00Z",
    }


def _approved_proposal(svc, rt):  # type: ignore[no-untyped-def]
    prop = svc.propose(
        testcase_id="tcase_idem",
        base_version=1,
        changes=[
            {
                "path": "steps[].target",
                "step_index": 1,
                "value": {"strategy": "test_id", "value": "nav-v2"},
            }
        ],
        reason="locator 失效修复",
        created_by="user:engineer",
    )
    pid = prop["proposal_id"]
    # 跳过真实沙箱执行（本测试关注状态机与幂等），显式构造"已审批待晋升"状态
    doc = svc.get(pid)
    doc["status"] = "PENDING_APPROVAL"
    doc["sandbox_status"] = "PASSED"
    rt.docs.put(
        doc_id=pid,
        kind="repair",
        doc=doc,
        workspace_id=doc["workspace_id"],
        ref_id="tcase_idem",
        status="PENDING_APPROVAL",
    )
    return pid


def test_repair_duplicate_approve_rejected(tmp_path) -> None:  # type: ignore[no-untyped-def]
    rt, svc = _repair_env(tmp_path)
    pid = _approved_proposal(svc, rt)
    first = svc.approve(pid, "user:lead")
    assert first["status"] == "APPROVED"
    # 重复审批：APPROVED -> APPROVED 非法转换
    with pytest.raises(AppError) as exc:
        svc.approve(pid, "user:lead")
    assert "illegal repair transition" in str(exc.value.message)
    # 审批后再次 reject 也被状态机拒绝
    with pytest.raises(AppError):
        svc.reject(pid, "user:lead")


def test_repair_promote_requires_idempotency_and_replay(tmp_path) -> None:  # type: ignore[no-untyped-def]
    rt, svc = _repair_env(tmp_path)
    pid = _approved_proposal(svc, rt)
    svc.approve(pid, "user:lead")
    # 晋升必须携带幂等键
    with pytest.raises(AppError):
        svc.promote(pid, idempotency_key="")
    first = svc.promote(pid, idempotency_key="promo-001")
    assert first["status"] == "PROMOTED"
    # 同键重放：命中回执，不再重复创建版本
    replay = svc.promote(pid, idempotency_key="promo-001")
    assert replay["status"] == "PROMOTED"
    assert replay.get("promotion_record", {}).get("idempotency_key") == "promo-001"
    versions = {v["version"] for v in rt.catalog.list_versions("tcase_idem")}
    assert versions == {1, 2}, "重放不得再次创建版本"
