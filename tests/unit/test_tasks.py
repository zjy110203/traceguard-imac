"""阶段3 Task/Run/Step 服务测试：幂等、状态机、多Run、租约代次、维度独立。"""

from __future__ import annotations

import time
import uuid

import pytest
from traceguard_core.errors import AppError
from traceguard_execution.tasks.service import TaskService


@pytest.fixture(autouse=True)
def _clean_db():  # type: ignore[no-untyped-def]
    # 每个测试用唯一 idempotency_key 避免干扰；不做破坏性清库（安全规则）
    yield


def _svc() -> TaskService:
    return TaskService()


def _mk_task(svc: TaskService, key: str) -> str:
    # 加随机后缀保证每次测试独立（execution.db 持久化，避免幂等命中旧数据）
    return svc.create_task("tcase_create001", key + "-" + uuid.uuid4().hex[:8])["task_id"]


def test_task_creation_idempotent() -> None:
    svc = _svc()
    k = "idem-unique-001-" + uuid.uuid4().hex[:8]
    a = svc.create_task("tcase_x", k)
    b = svc.create_task("tcase_x", k)
    assert a["task_id"] == b["task_id"]
    assert b["idempotent_hit"] is True


def test_illegal_task_transition_rejected() -> None:
    svc = _svc()
    tid = _mk_task(svc, "idem-unique-002")
    with pytest.raises(AppError) as e:
        svc.transition_task(tid, "COMPLETED")  # CREATED 不能直接 COMPLETED
    assert e.value.code.value == "CONFLICT"


def test_task_state_machine_happy_path() -> None:
    svc = _svc()
    tid = _mk_task(svc, "idem-unique-003")
    svc.transition_task(tid, "QUEUED")
    svc.transition_task(tid, "RUNNING")
    svc.transition_task(tid, "COMPLETED")
    assert svc.get_task(tid)["status"] == "COMPLETED"


def test_retry_creates_new_run_not_overwrite() -> None:
    svc = _svc()
    tid = _mk_task(svc, "idem-unique-004")
    r1 = svc.create_run(tid, "tcase_create001:v1")
    r2 = svc.create_run(tid, "tcase_create001:v1")  # 重试
    assert r1["run_id"] != r2["run_id"]
    assert r1["attempt"] == 1 and r2["attempt"] == 2
    runs = svc.list_runs(tid)
    assert len(runs) == 2
    assert svc.get_run(r1["run_id"]) is not None  # 历史未覆盖


def test_lease_fencing_rejects_stale_worker() -> None:
    """F06：租约过期后新 worker 接管（代次+1），旧 worker 提交被拒绝。"""
    svc = _svc()
    tid = _mk_task(svc, "idem-unique-005")
    rid = svc.create_run(tid, "tc:v1")["run_id"]
    lease1 = svc.acquire_lease(rid, "worker-A", ttl_seconds=0.1)
    assert lease1["fencing_token"] == 1
    time.sleep(0.15)  # 租约过期
    lease2 = svc.acquire_lease(rid, "worker-B", ttl_seconds=30)
    assert lease2["fencing_token"] == 2  # 单调递增

    # 旧 worker 提交 → 拒绝
    with pytest.raises(AppError) as e:
        svc.submit_result(rid, "worker-A", 1, execution_status="FINALIZING", test_verdict="PASSED")
    assert "STALE_FENCING_TOKEN" in str(e.value.message)

    # 新 worker 提交 → 成功（先经 RUNNING，遵守状态机）
    svc.submit_result(rid, "worker-B", 2, execution_status="RUNNING", test_verdict="NOT_EVALUATED")
    svc.submit_result(rid, "worker-B", 2, execution_status="FINALIZING", test_verdict="PASSED")
    assert svc.get_run(rid)["test_verdict"] == "PASSED"


def test_renew_lease_fails_after_losing_ownership() -> None:
    svc = _svc()
    tid = _mk_task(svc, "idem-unique-006")
    rid = svc.create_run(tid, "tc:v1")["run_id"]
    lease = svc.acquire_lease(rid, "worker-A", ttl_seconds=0.1)
    time.sleep(0.15)
    svc.acquire_lease(rid, "worker-B", ttl_seconds=30)
    assert svc.renew_lease(rid, "worker-A", lease["fencing_token"]) is False


def test_execution_status_and_verdict_independent() -> None:
    """Run 的 execution_status 与 test_verdict 是独立字段。"""
    svc = _svc()
    tid = _mk_task(svc, "idem-unique-007")
    rid = svc.create_run(tid, "tc:v1")["run_id"]
    svc.acquire_lease(rid, "worker-A")
    # execution=FINALIZING 但 verdict=FAILED，两者独立写入（先经 RUNNING）
    svc.submit_result(rid, "worker-A", 1, execution_status="RUNNING", test_verdict="NOT_EVALUATED")
    svc.submit_result(rid, "worker-A", 1, execution_status="FINALIZING", test_verdict="FAILED")
    r = svc.get_run(rid)
    assert r["execution_status"] == "FINALIZING"
    assert r["test_verdict"] == "FAILED"


def test_steps_recorded_and_listed() -> None:
    svc = _svc()
    tid = _mk_task(svc, "idem-unique-008")
    rid = svc.create_run(tid, "tc:v1")["run_id"]
    s1 = "step_" + uuid.uuid4().hex[:8]
    s2 = "step_" + uuid.uuid4().hex[:8]
    svc.record_step(rid, s1, 0, "PASSED")
    svc.record_step(rid, s2, 1, "FAILED", "boom")
    steps = svc.list_steps(rid)
    assert len(steps) == 2
    assert steps[1]["status"] == "FAILED" and steps[1]["error"] == "boom"


def test_task_summary_is_display_only() -> None:
    svc = _svc()
    tid = _mk_task(svc, "idem-unique-009")
    svc.create_run(tid, "tc:v1")
    svc.create_run(tid, "tc:v1")
    summary = svc.task_summary(tid)
    assert summary["run_count"] == 2
    assert "展示汇总" in summary["note"]
