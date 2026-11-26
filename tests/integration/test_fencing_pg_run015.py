"""RUN-015 WP1：PostgreSQL 16 上的租约锁等待与终态提交竞态验证。

重点验证 RUN-014 未覆盖的"数据库锁等待导致租约时间失真"：

- 旧 Worker 在租约仍有效时发起续租/守卫写入；
- 语句因另一个事务持有 Run 行锁而阻塞；
- 等锁期间租约真实过期，锁释放后条件 UPDATE 仍必须失败。

若用 Python `time.time()` 作为 SQL 参数，拿锁后会用调用时的旧时间命中条件，
从而"复活"已经过期的租约。修复后 PG 使用 `CLOCK_TIMESTAMP()`（执行时求值）。

运行：需要本机 PostgreSQL 16（默认 127.0.0.1:55432）或 `TG_TEST_POSTGRES_URL`。
"""

from __future__ import annotations

import os
import threading
import time
import uuid
from typing import Any

import pytest

psycopg = pytest.importorskip("psycopg")

PG_ADMIN = os.environ.get("TG_RUN015_PG_ADMIN", "postgresql://traceguard@127.0.0.1:55432/postgres")
PG_NAME = os.environ.get("TG_RUN015_PG_DB", "traceguard_run015_fence")
PG_URL = (
    os.environ.get("TG_TEST_POSTGRES_URL")
    or f"postgresql+psycopg://traceguard@127.0.0.1:55432/{PG_NAME}"
)
PG_DSN = PG_URL.replace("postgresql+psycopg://", "postgresql://")


def _can_connect(dsn: str) -> bool:
    try:
        with psycopg.connect(dsn, connect_timeout=2):
            return True
    except Exception:  # noqa: BLE001 - 环境探测，失败即 skip
        return False


pytestmark = pytest.mark.skipif(
    not _can_connect(PG_ADMIN if not os.environ.get("TG_TEST_POSTGRES_URL") else PG_DSN),
    reason="PostgreSQL 16 不可达（需 TG_TEST_POSTGRES_URL 或本机 55432）",
)


@pytest.fixture(scope="module")
def pg_db(tmp_path_factory):  # type: ignore[no-untyped-def]
    if not os.environ.get("TG_TEST_POSTGRES_URL"):
        with psycopg.connect(PG_ADMIN, autocommit=True, connect_timeout=3) as conn:
            conn.execute(f'DROP DATABASE IF EXISTS "{PG_NAME}" WITH (FORCE)')
            conn.execute(f'CREATE DATABASE "{PG_NAME}"')
    artifacts = tmp_path_factory.mktemp("run015-pg-artifacts")
    os.environ["TG_DATABASE_URL"] = PG_URL
    os.environ["TG_ARTIFACT_DIR"] = str(artifacts)
    os.environ["TG_LLM_PROVIDER"] = ""
    from traceguard_core.config import reset_settings_cache
    from traceguard_persistence import Database

    reset_settings_cache()
    db = Database(PG_URL)
    db.create_schema()
    yield db
    db.dispose()
    reset_settings_cache()


@pytest.fixture()
def rt(pg_db, monkeypatch):  # type: ignore[no-untyped-def]
    from traceguard_api.runtime import Runtime
    from traceguard_core.config import reset_settings_cache

    monkeypatch.setenv("TG_DATABASE_URL", PG_URL)
    monkeypatch.setenv("TG_LLM_PROVIDER", "")
    reset_settings_cache()
    runtime = Runtime(pg_db)
    suffix = uuid.uuid4().hex[:10]
    runtime.catalog.create_testcase(
        {
            "testcase_id": f"tcase_pg15_{suffix}",
            "version": 1,
            "workspace_id": "default",
            "created_at": "2026-01-01T00:00:00Z",
            "steps": [{"step_id": "s1", "action": "navigate", "value": "/projects"}],
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
        title="pg-fence15",
        review_status="APPROVED",
    )
    runtime._run015_tc = f"tcase_pg15_{suffix}"  # type: ignore[attr-defined]
    yield runtime
    reset_settings_cache()


def _queued_run(rt, key: str) -> str:  # type: ignore[no-untyped-def]
    tc = rt._run015_tc  # type: ignore[attr-defined]
    task = rt.tasks.create_task(
        tc,
        f"pg15-{key}-{uuid.uuid4().hex[:6]}",
        testcase_version_id=f"{tc}@1",
        workspace_id="default",
    )
    return rt.tasks.create_run(task["task_id"], f"{tc}@1")["run_id"]


class _RowLock:
    """在独立 PG 连接上持有 Run 行锁，直到 release()。"""

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self.acquired = threading.Event()
        self.release = threading.Event()
        self.expiry = 0.0
        self.error: BaseException | None = None
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        try:
            with psycopg.connect(PG_DSN, connect_timeout=5) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT lease_expires_at FROM tg_run WHERE run_id = %s FOR UPDATE",
                        (self.run_id,),
                    )
                    row = cur.fetchone()
                    self.expiry = float(row[0])
                self.acquired.set()
                self.release.wait(timeout=20)
        except BaseException as exc:  # noqa: BLE001 - 线程边界收集异常
            self.error = exc
            self.acquired.set()

    def __enter__(self) -> _RowLock:
        self.thread.start()
        assert self.acquired.wait(timeout=8), "未能取得 Run 行锁"
        assert self.error is None, f"锁连接失败: {self.error!r}"
        return self

    def __exit__(self, *args) -> None:  # type: ignore[no-untyped-def]
        self.release.set()
        self.thread.join(timeout=10)
        assert self.error is None, f"锁连接失败: {self.error!r}"


def _take_over(rt, run_id: str, worker_b: str = "worker-B") -> int:  # type: ignore[no-untyped-def]
    from sqlalchemy import update
    from traceguard_persistence.models import RunRow

    with rt.db.session() as s:
        s.execute(update(RunRow).where(RunRow.run_id == run_id).values(lease_expires_at=0.0))
    lease_b = rt.tasks.acquire_lease(run_id, worker_b, ttl_seconds=30)
    token_b = int(lease_b["fencing_token"])
    rt.tasks.mark_running(run_id, worker_b, token_b, "playwright")
    rt.tasks.submit_result(
        run_id,
        worker_b,
        token_b,
        execution_status="COMPLETED",
        test_verdict="PASSED",
        report={"owner": worker_b},
        idempotency_key=f"{run_id}:pg-b-final",
    )
    return token_b


@pytest.mark.parametrize("operation", ["renew", "mark_running", "step", "submit"])
def test_lock_wait_past_expiry_never_revives_lease(rt, operation: str) -> None:  # type: ignore[no-untyped-def]
    """调用发生在租约有效期内，拿锁时已过期：所有旧 Worker 写入都必须失败。"""
    run_id = _queued_run(rt, f"lock-{operation}")
    lease = rt.tasks.acquire_lease(run_id, "worker-A", ttl_seconds=3.0)
    token = int(lease["fencing_token"])
    outcome: dict[str, Any] = {}

    def old_worker_op() -> None:
        try:
            if operation == "renew":
                outcome["value"] = rt.tasks.renew_lease(run_id, "worker-A", token, ttl_seconds=60)
            elif operation == "mark_running":
                rt.tasks.mark_running(run_id, "worker-A", token, "playwright")
                outcome["value"] = "ok"
            elif operation == "step":
                rt.tasks.record_step(
                    run_id, "s1", 0, "PASSED", action="navigate", fence=("worker-A", token)
                )
                outcome["value"] = "ok"
            else:
                rt.tasks.submit_result(
                    run_id,
                    "worker-A",
                    token,
                    execution_status="COMPLETED",
                    test_verdict="FAILED",
                    idempotency_key=f"{run_id}:pg-a-late",
                )
                outcome["value"] = "ok"
        except BaseException as exc:  # noqa: BLE001 - 线程边界收集异常
            outcome["error"] = exc

    with _RowLock(run_id) as lock:
        worker = threading.Thread(target=old_worker_op, daemon=True)
        worker.start()
        time.sleep(0.4)  # 旧 Worker 已发起语句并阻塞在行锁上（调用时租约仍有效）
        assert lock.expiry > time.time(), "测试时序无效：调用前租约不应已过期"
        # 等锁期间租约真实过期后才释放行锁
        while time.time() <= lock.expiry + 0.25:
            time.sleep(0.05)
        lock.release.set()
        worker.join(timeout=10)

    assert not worker.is_alive(), "旧 Worker 语句未在合理时间内结束"
    if operation == "renew":
        assert outcome.get("value") is False, "过期租约不得被旧时间参数复活"
    else:
        error = outcome.get("error")
        assert error is not None, f"{operation} 应在租约过期后失败，实际 {outcome}"
        assert isinstance(error, Exception) and (
            "LEASE_LOST" in str(error) or "STALE_FENCING_TOKEN" in str(error)
        ), str(error)

    run = rt.tasks.get_run(run_id)
    assert run["lease_owner"] == "worker-A"  # 无人接管；旧 owner 仍在字段里，但租期已失效
    assert run["lease_expires_at"] <= time.time()
    if operation == "step":
        assert rt.tasks.list_steps(run_id) == []
    if operation == "submit":
        from sqlalchemy import select
        from traceguard_persistence.models import RunSubmissionRow

        with rt.db.session() as s:
            rows = s.scalars(
                select(RunSubmissionRow).where(RunSubmissionRow.run_id == run_id)
            ).all()
        assert rows == [], "过期 Worker 的终态回执不得落库"


def test_pg_submit_result_takeover_preserves_b_receipt(
    rt,
    monkeypatch: pytest.MonkeyPatch,  # type: ignore[no-untyped-def]
) -> None:
    """PG16 上同样验证：A 暂停 → B 接管提交 → A 恢复提交被原子守卫拒绝。"""
    from traceguard_execution.tasks import service as task_service

    run_id = _queued_run(rt, "submit-pg")
    lease_a = rt.tasks.acquire_lease(run_id, "worker-A", ttl_seconds=30)
    token_a = int(lease_a["fencing_token"])
    rt.tasks.mark_running(run_id, "worker-A", token_a, "playwright")

    original = task_service.fenced_guard
    done = False

    def guard_with_takeover(session, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal done
        if kwargs.get("worker_id") == "worker-A" and not done:
            done = True
            _take_over(rt, run_id)
        return original(session, **kwargs)

    monkeypatch.setattr(task_service, "fenced_guard", guard_with_takeover)
    with pytest.raises(Exception) as exc:
        rt.tasks.submit_result(
            run_id,
            "worker-A",
            token_a,
            execution_status="FAILED",
            test_verdict="FAILED",
            report={"owner": "A"},
            idempotency_key=f"{run_id}:pg-a-late",
        )
    assert "STALE_FENCING_TOKEN" in str(exc.value)
    run = rt.tasks.get_run(run_id)
    assert run["test_verdict"] == "PASSED" and run["report"] == {"owner": "worker-B"}
    assert run["fencing_token"] == token_a + 1


def test_pg_concurrent_claim_has_single_owner(rt) -> None:  # type: ignore[no-untyped-def]
    """PG16 上四个 Worker 同时领取同一 Run：只有一个合法 owner。"""
    run_id = _queued_run(rt, "claim-pg")
    # claim_next_run 取最早 5 个候选：把前面测试遗留的 Run 收口，确保本 Run 必然入选
    from sqlalchemy import update
    from traceguard_persistence.models import RunRow

    with rt.db.session() as s:
        s.execute(
            update(RunRow).where(RunRow.run_id != run_id).values(execution_status="COMPLETED")
        )
    results: list[dict | None] = []
    barrier = threading.Barrier(5)

    def claim(name: str) -> None:
        barrier.wait(timeout=10)
        results.append(rt.tasks.claim_next_run(name, ttl_seconds=30))

    threads = [threading.Thread(target=claim, args=(f"pg-w{i}",)) for i in range(4)]
    for t in threads:
        t.start()
    barrier.wait(timeout=10)
    for t in threads:
        t.join(timeout=15)

    winners = [c for c in results if c and c["run_id"] == run_id]
    assert len(winners) == 1
    run = rt.tasks.get_run(run_id)
    assert run["fencing_token"] == 1
    assert run["lease_owner"] in {f"pg-w{i}" for i in range(4)}
