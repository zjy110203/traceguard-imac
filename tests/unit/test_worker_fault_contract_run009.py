"""RUN-009 WP4：Worker 故障恢复的契约级测试（不是真实进程故障注入）。

B-17 仍 BLOCKED，四个补充场景不得计为通过。本文件只验证状态机、租约代次、
重复提交与数据库一致性断言，作为未来安全批准后的故障矩阵验收前置条件。
"""

from __future__ import annotations

import time
import uuid

import pytest
from traceguard_core.errors import AppError
from traceguard_execution.tasks.service import TaskService
from traceguard_persistence import Database


def _svc() -> TaskService:
    # 每测试独立内存库，避免 claim_next_run 误领取共享库里的既有 Run。
    return TaskService(Database("sqlite:///:memory:"))


def _queued_run(svc: TaskService) -> tuple[str, str]:
    key = "run009-fault-contract-" + uuid.uuid4().hex[:10]
    task_id = svc.create_task("tcase_create001", key)["task_id"]
    svc.transition_task(task_id, "QUEUED")
    run_id = svc.create_run(task_id, "tcase_create001:v1")["run_id"]
    return task_id, run_id


def test_post_claim_exit_contract_reap_and_reassign() -> None:
    """场景1 契约：领取后退出 → 租约过期回收 → 新 worker 接管且 fencing 递增。"""
    svc = _svc()
    task_id, run_id = _queued_run(svc)

    claim_a = svc.claim_next_run("worker-A", ttl_seconds=0.05)
    assert claim_a is not None
    assert claim_a["run_id"] == run_id
    assert claim_a["fencing_token"] == 1
    assert claim_a["previous_status"] == "QUEUED"
    assert svc.get_run(run_id)["execution_status"] == "PREPARING"

    time.sleep(0.08)
    reaped = svc.reap_expired_runs()
    assert any(item["run_id"] == run_id for item in reaped)
    run = svc.get_run(run_id)
    assert run["execution_status"] == "QUEUED"
    assert run["lease_owner"] is None

    claim_b = svc.claim_next_run("worker-B", ttl_seconds=30)
    assert claim_b is not None
    assert claim_b["run_id"] == run_id
    assert claim_b["fencing_token"] == 2
    assert claim_b["previous_status"] == "QUEUED"

    with pytest.raises(AppError) as exc:
        svc.submit_result(
            run_id,
            "worker-A",
            claim_a["fencing_token"],
            execution_status="COMPLETED",
            test_verdict="PASSED",
        )
    assert "STALE_FENCING_TOKEN" in str(exc.value.message)

    assert svc.get_task(task_id)["status"] == "RUNNING"


def test_lease_expiry_contract_monotonic_token_and_stale_renew() -> None:
    """场景2 契约：租约过期不改变 attempt，fencing 单调递增，旧 owner 不能续租。"""
    svc = _svc()
    _, run_id = _queued_run(svc)

    lease_a = svc.acquire_lease(run_id, "worker-A", ttl_seconds=0.05)
    first_attempt = svc.get_run(run_id)["attempt"]
    time.sleep(0.08)
    lease_b = svc.acquire_lease(run_id, "worker-B", ttl_seconds=30)

    assert lease_a["fencing_token"] == 1
    assert lease_b["fencing_token"] == 2
    assert svc.get_run(run_id)["attempt"] == first_attempt
    assert svc.get_run(run_id)["lease_owner"] == "worker-B"
    assert svc.renew_lease(run_id, "worker-A", lease_a["fencing_token"]) is False
    assert svc.renew_lease(run_id, "worker-B", lease_b["fencing_token"]) is True


def test_api_restart_and_fleet_restart_contract_claim_is_idempotent_boundary() -> None:
    """场景3/4 契约：重建 TaskService（模拟 API/Worker 重启）后租约与代次仍持久化。"""
    db = Database("sqlite:///:memory:")
    svc = TaskService(db)
    _, run_id = _queued_run(svc)
    claim = svc.claim_next_run("worker-old", ttl_seconds=30)
    assert claim is not None

    restarted = TaskService(db)  # 新建服务实例，模拟进程重启后的数据库重连
    run = restarted.get_run(run_id)
    assert run["lease_owner"] == "worker-old"
    assert run["fencing_token"] == claim["fencing_token"]
    assert run["execution_status"] == "PREPARING"

    with pytest.raises(AppError) as exc:
        restarted.submit_result(
            run_id,
            "worker-new",
            claim["fencing_token"],
            execution_status="COMPLETED",
            test_verdict="PASSED",
        )
    assert "STALE_FENCING_TOKEN" in str(exc.value.message)


def test_duplicate_submit_contract_stale_rejected_same_owner_recorded() -> None:
    """重复提交契约：旧代次必须拒绝；当前代次重复提交保持可追溯且不改写运行身份。"""
    svc = _svc()
    task_id, run_id = _queued_run(svc)
    claim = svc.claim_next_run("worker-A", ttl_seconds=30)
    assert claim is not None
    token = claim["fencing_token"]

    svc.submit_result(run_id, "worker-A", token, execution_status="RUNNING", test_verdict="NOT_EVALUATED")
    svc.submit_result(run_id, "worker-A", token, execution_status="FINALIZING", test_verdict="PASSED")
    svc.submit_result(run_id, "worker-A", token, execution_status="COMPLETED", test_verdict="PASSED")

    run = svc.get_run(run_id)
    assert run["execution_status"] == "COMPLETED"
    assert run["test_verdict"] == "PASSED"
    assert run["fencing_token"] == token
    assert run["lease_owner"] == "worker-A"
    assert svc.get_task(task_id)["status"] == "COMPLETED"

    # 旧 owner / 旧代次即使在同一 Run 完成后再次提交，也必须被拒绝。
    with pytest.raises(AppError) as exc:
        svc.submit_result(
            run_id,
            "worker-B",
            token,
            execution_status="COMPLETED",
            test_verdict="FAILED",
        )
    assert "STALE_FENCING_TOKEN" in str(exc.value.message)
