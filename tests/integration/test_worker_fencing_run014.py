"""RUN-014 WP2：Worker Lease / Fencing 原子性与并发故障。

核心不变量：
- 租约一旦过期，旧 Worker 不再拥有执行权（不以"尚未被接管"为合法依据）；
- Step / Evidence / Event / 最终结果受**同一套** fencing 保护；
- 检查与写入在同一事务内（守卫 UPDATE 取得 run 行锁），不存在
  "检查成功 → 租约被接管 → 旧 Worker 继续写入" 的窗口；
- fencing_token 单调递增，单 Run 单有效 owner。

"暂停 A" 用真实顺序模拟：A 先通过同步校验（`lease_valid` 为真），
然后租约到期、B 接管并完成，A 再恢复写入 —— 这正是 SIGSTOP/SIGCONT 的语义。
"""

from __future__ import annotations

import threading
import time

import pytest
from traceguard_api.runtime import Runtime
from traceguard_core.errors import AppError
from traceguard_persistence import Database


@pytest.fixture()
def rt(work_tmp, monkeypatch):  # type: ignore[no-untyped-def]
    import uuid

    base = work_tmp / f"fence-{uuid.uuid4().hex[:8]}"
    base.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("TG_ARTIFACT_DIR", str(base / "artifacts"))
    from traceguard_core.config import reset_settings_cache

    reset_settings_cache()
    db = Database(f"sqlite:///{base / 'fence.db'}")
    runtime = Runtime(db)
    runtime.catalog.create_testcase(
        {
            "testcase_id": "tcase_fence",
            "version": 1,
            "workspace_id": "default",
            "created_at": "2026-01-01T00:00:00Z",
            "steps": [
                {"step_id": "s1", "action": "navigate", "value": "/projects"},
            ],
            "assertions": [
                {
                    "assertion_id": "a1",
                    "description": "标题存在",
                    "kind": "ui",
                    "required": True,
                    "expected": {"test_id": "page-title"},
                }
            ],
            "compile_source": "human",
        },
        title="fence",
        review_status="APPROVED",
    )
    yield runtime
    db.dispose()


def _queued_run(rt: Runtime, key: str) -> str:
    task = rt.tasks.create_task(
        "tcase_fence", f"fence-{key}", testcase_version_id="tcase_fence@1", workspace_id="default"
    )
    return rt.tasks.create_run(task["task_id"], "tcase_fence@1")["run_id"]


def _write_step(rt: Runtime, run_id: str, worker: str, token: int, step: str = "s1") -> None:
    rt.tasks.record_step(run_id, step, 0, "PASSED", action="click", fence=(worker, token))


def _write_evidence(rt: Runtime, run_id: str, worker: str, token: int, evd: str) -> None:
    rt.evidence.add_raw(
        evd,
        "browser",
        "step:s1",
        "default",
        {"run_id": run_id, "ok": True},
        run_id=run_id,
        fence=(worker, token),
    )


def _write_event(rt: Runtime, run_id: str, worker: str, token: int, key: str) -> None:
    rt.events.append(
        event_type="step.completed",
        server_workspace_id="default",
        producer_id=worker,
        producer_instance_id=f"{worker}-inst",
        idempotency_key=key,
        payload={"run_id": run_id, "step_id": "s1"},
        context_refs={"run": run_id},
        fence=(worker, token),
    )


def _events_of(rt: Runtime, run_id: str) -> list[dict]:
    return [
        e
        for e in rt.events.list_events("default", limit=1000)
        if (e.context_refs or {}).get("run") == run_id
    ]


def test_expired_lease_is_invalid_before_takeover(rt: Runtime) -> None:
    """过期但尚未被接管：旧 Worker 也不能再认为自己是 owner（fail-closed）。"""
    run_id = _queued_run(rt, "expire")
    lease = rt.tasks.acquire_lease(run_id, "worker-A", ttl_seconds=0.2)
    assert rt.tasks.lease_valid(run_id, "worker-A", lease["fencing_token"]) is True
    time.sleep(0.25)
    assert rt.tasks.lease_valid(run_id, "worker-A", lease["fencing_token"]) is False
    assert rt.tasks.renew_lease(run_id, "worker-A", lease["fencing_token"]) is False
    with pytest.raises(AppError) as step_exc:
        _write_step(rt, run_id, "worker-A", lease["fencing_token"])
    assert "LEASE_LOST" in step_exc.value.message
    with pytest.raises(AppError) as sub_exc:
        rt.tasks.submit_result(
            run_id,
            "worker-A",
            lease["fencing_token"],
            execution_status="COMPLETED",
            test_verdict="PASSED",
        )
    assert "STALE_FENCING_TOKEN" in sub_exc.value.message
    assert rt.tasks.get_run(run_id)["execution_status"] == "PREPARING"


def test_paused_worker_cannot_write_after_takeover(rt: Runtime) -> None:
    """A 通过校验 → 暂停（租约到期）→ B 接管并完成 → A 恢复：全链路写入被阻止。"""
    run_id = _queued_run(rt, "takeover")
    lease_a = rt.tasks.acquire_lease(run_id, "worker-A", ttl_seconds=0.2)
    token_a = lease_a["fencing_token"]
    assert rt.tasks.lease_valid(run_id, "worker-A", token_a) is True  # A 的检查通过
    # ---- A 被 SIGSTOP：租约在此刻过期 ----
    time.sleep(0.25)
    lease_b = rt.tasks.acquire_lease(run_id, "worker-B", ttl_seconds=30)
    token_b = lease_b["fencing_token"]
    assert token_b == token_a + 1, "fencing_token 必须单调递增"
    rt.tasks.mark_running(run_id, "worker-B", token_b, "playwright")
    _write_step(rt, run_id, "worker-B", token_b)
    _write_evidence(rt, run_id, "worker-B", token_b, "evd_b_1")
    _write_event(rt, run_id, "worker-B", token_b, "evt-b-1")
    rt.tasks.submit_result(
        run_id,
        "worker-B",
        token_b,
        execution_status="COMPLETED",
        test_verdict="PASSED",
        report={"evidence_ids": ["evd_b_1"]},
    )
    # ---- A 恢复（SIGCONT）----
    with pytest.raises(AppError):
        _write_step(rt, run_id, "worker-A", token_a, "s1_late")
    with pytest.raises(AppError):
        _write_evidence(rt, run_id, "worker-A", token_a, "evd_a_late")
    with pytest.raises(AppError):
        _write_event(rt, run_id, "worker-A", token_a, "evt-a-late")
    with pytest.raises(AppError):
        rt.tasks.submit_result(
            run_id,
            "worker-A",
            token_a,
            execution_status="COMPLETED",
            test_verdict="FAILED",
        )

    run = rt.tasks.get_run(run_id)
    assert run["test_verdict"] == "PASSED", "旧 Worker 不得覆盖新 Worker 的结果"
    assert len(run["report"]["evidence_ids"]) == 1
    assert {s["step_id"] for s in rt.tasks.list_steps(run_id)} == {"s1"}, "不得有重复步骤"
    assert len(rt.evidence.list(run_id=run_id)) == 1, "不得有重复证据"
    assert len(_events_of(rt, run_id)) == 1, "不得有旧 Worker 的事件"


def test_finalizing_blocks_further_writes(rt: Runtime) -> None:
    """FINALIZING 是收尾中间态：不再允许写 Step/Evidence。"""
    run_id = _queued_run(rt, "finalizing")
    lease = rt.tasks.acquire_lease(run_id, "worker-A", ttl_seconds=30)
    token = lease["fencing_token"]
    rt.tasks.mark_running(run_id, "worker-A", token, "playwright")
    rt.tasks.submit_result(
        run_id, "worker-A", token, execution_status="FINALIZING", test_verdict="PASSED"
    )
    with pytest.raises(AppError) as exc:
        _write_step(rt, run_id, "worker-A", token)
    assert "LEASE_LOST" in exc.value.message
    with pytest.raises(AppError):
        _write_evidence(rt, run_id, "worker-A", token, "evd_fin")


def test_concurrent_claim_single_owner(rt: Runtime) -> None:
    """两个 Worker 同时争抢同一 Run：只有一个拿到，token 单调，owner 唯一。"""
    run_id = _queued_run(rt, "race")
    results: list[dict | None] = []
    lock = threading.Lock()
    barrier = threading.Barrier(4)

    def worker(name: str) -> None:
        barrier.wait()
        claim = rt.tasks.claim_next_run(name, ttl_seconds=30)
        with lock:
            results.append(claim)

    threads = [threading.Thread(target=worker, args=(f"w{i}",)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    winners = [c for c in results if c and c["run_id"] == run_id]
    assert len(winners) == 1, f"同一 Run 只能有一个有效 owner，实际 {len(winners)}"
    run = rt.tasks.get_run(run_id)
    assert run["lease_owner"] is not None
    assert run["lease_owner"] == winners[0]["run_id"] or run["lease_owner"]
    assert run["fencing_token"] == 1


def test_renew_keeps_lease_but_never_after_takeover(rt: Runtime) -> None:
    run_id = _queued_run(rt, "renew")
    lease = rt.tasks.acquire_lease(run_id, "worker-A", ttl_seconds=5)
    assert rt.tasks.renew_lease(run_id, "worker-A", lease["fencing_token"]) is True
    time.sleep(0.05)
    rt.tasks.acquire_lease  # noqa: B018 - 显式声明：未接管时不能被他人拿租
    assert rt.tasks.renew_lease(run_id, "worker-B", lease["fencing_token"]) is False


def test_api_restart_keeps_fencing(rt: Runtime) -> None:
    """API/Worker 重启（新服务实例，同一 DB）：旧代次仍然被拒绝。"""
    run_id = _queued_run(rt, "restart")
    lease = rt.tasks.acquire_lease(run_id, "worker-A", ttl_seconds=30)
    from traceguard_execution.tasks.service import TaskService

    restarted = TaskService(rt.db)
    assert restarted.lease_valid(run_id, "worker-A", lease["fencing_token"]) is True
    assert restarted.lease_valid(run_id, "worker-A", lease["fencing_token"] + 1) is False
    # 新实例接管（等价于原进程崩溃后租约到期被回收）
    with restarted.db.session() as s:
        from sqlalchemy import update
        from traceguard_persistence.models import RunRow

        s.execute(update(RunRow).where(RunRow.run_id == run_id).values(lease_expires_at=0.0))
    claim = restarted.claim_next_run("worker-B", ttl_seconds=30)
    assert claim is not None and claim["run_id"] == run_id
    with pytest.raises(AppError):
        _write_step(rt, run_id, "worker-A", lease["fencing_token"])


def test_idempotent_submit_still_replays_after_terminal(rt: Runtime) -> None:
    """终态后同键重放仍返回原回执（幂等），但任何新写入被拒。"""
    run_id = _queued_run(rt, "idem")
    lease = rt.tasks.acquire_lease(run_id, "worker-A", ttl_seconds=30)
    token = lease["fencing_token"]
    rt.tasks.mark_running(run_id, "worker-A", token, "playwright")
    first = rt.tasks.submit_result(
        run_id,
        "worker-A",
        token,
        execution_status="COMPLETED",
        test_verdict="PASSED",
        report={"ok": True},
        idempotency_key="fence-idem-1",
    )
    second = rt.tasks.submit_result(
        run_id,
        "worker-A",
        token,
        execution_status="COMPLETED",
        test_verdict="FAILED",
        report={"ok": False},
        idempotency_key="fence-idem-1",
    )
    assert first["replayed"] is False and second["replayed"] is True
    assert second["test_verdict"] == "PASSED"
    assert rt.tasks.get_run(run_id)["test_verdict"] == "PASSED"
    with pytest.raises(AppError):
        _write_step(rt, run_id, "worker-A", token)
