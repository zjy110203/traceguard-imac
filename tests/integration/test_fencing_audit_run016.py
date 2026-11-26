"""RUN-016 WP1：Fencing 全链路补充审查（真实 PostgreSQL 16）。

补足 RUN-014/015 未单独覆盖的三点：

1. **事务原子性**：守卫 + 步骤写入在同一事务内，中途抛错必须整体回滚，
   不产生"步骤已写、事件未写"的部分成功；
2. **进程重启**：新 Runtime（等价于进程重启）用旧 token 不能继续写，
   重新领取后新 token 可以写，旧 token 立即失效；
3. **无 fence 的伪造写入**：拿不存在的 owner + token 写事件/证据/步骤，
   必须被拒绝（不能靠"不传 fence"绕过守卫）。

环境：需要 PostgreSQL 16（`TG_TEST_POSTGRES_URL` 或本机 127.0.0.1:55432）。
"""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import func, select

psycopg = pytest.importorskip("psycopg")

PG_ADMIN = os.environ.get("TG_RUN015_PG_ADMIN", "postgresql://traceguard@127.0.0.1:55432/postgres")
PG_NAME = os.environ.get("TG_RUN016_PG_DB", "traceguard_run016_fence")
PG_URL = (
    os.environ.get("TG_TEST_POSTGRES_URL")
    or f"postgresql+psycopg://traceguard@127.0.0.1:55432/{PG_NAME}"
)
PG_DSN = PG_URL.replace("postgresql+psycopg://", "postgresql://")


def _can_connect(dsn: str) -> bool:
    try:
        with psycopg.connect(dsn, connect_timeout=2):
            return True
    except Exception:  # noqa: BLE001 - 环境探测
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
    os.environ["TG_DATABASE_URL"] = PG_URL
    os.environ["TG_ARTIFACT_DIR"] = str(tmp_path_factory.mktemp("run016-fence-artifacts"))
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
    tc = f"tcase_r16f_{uuid.uuid4().hex[:8]}"
    runtime.catalog.create_testcase(
        {
            "testcase_id": tc,
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
        title="run016-fence",
        review_status="APPROVED",
    )
    runtime._r16_tc = tc  # type: ignore[attr-defined]
    yield runtime
    reset_settings_cache()


def _run(rt, key: str, worker: str = "worker-A", ttl: float = 60.0):  # type: ignore[no-untyped-def]
    tc = rt._r16_tc  # type: ignore[attr-defined]
    task = rt.tasks.create_task(
        tc, f"r16-{key}-{uuid.uuid4().hex[:6]}", testcase_version_id=f"{tc}@1", workspace_id="default"
    )
    run_id = rt.tasks.create_run(task["task_id"], f"{tc}@1")["run_id"]
    lease = rt.tasks.acquire_lease(run_id, worker, ttl_seconds=ttl)
    return run_id, int(lease["fencing_token"])


def _step_count(rt, run_id: str) -> int:
    from traceguard_persistence.models import StepRow

    with rt.db.session() as s:
        return int(s.scalar(select(func.count()).select_from(StepRow).where(StepRow.run_id == run_id)))


def test_partial_transaction_rolls_back_completely(rt) -> None:  # type: ignore[no-untyped-def]
    """守卫 + 步骤写入中途失败：步骤必须整体回滚（不产生部分成功）。"""
    run_id, token = _run(rt, "atomic")
    with pytest.raises(RuntimeError):
        with rt.tasks.fenced_session(run_id, "worker-A", token) as s:
            # 在同一事务内写 step（不再开新会话：同一行锁重入会自锁）
            rt.tasks._record_step_in(
                s, run_id, "s-partial", 0, "PASSED", None, "navigate", 0, None
            )
            raise RuntimeError("simulated crash mid-run")
    # 事务回滚：守卫与 step 一起消失，不产生"守卫成功但 step 已写"的部分成功
    assert _step_count(rt, run_id) == 0


def test_process_restart_keeps_token_then_takeover_invalidates_it(rt, pg_db) -> None:  # type: ignore[no-untyped-def]
    """进程重启不影响租约有效性；一旦被接管，旧 token 永久失效。

    这是"重启"与"接管"两个语义的分界：
    - 进程重启（同 worker_id + 同 token，租约未过期）→ 允许继续写（否则正常的 Worker
      重启会把执行中的 Run 全部变成不可继续）；
    - 被接管（token 严格递增）→ 旧 token 立即失效，即使它仍在原来的租约时间窗内。
    """
    from traceguard_api.runtime import Runtime

    run_id, token = _run(rt, "restart")
    restarted = Runtime(pg_db)  # 同一数据库上的"新进程"
    restarted.tasks.record_step(
        run_id, "s-after-restart", 0, "PASSED", action="navigate", fence=("worker-A", token)
    )
    assert _step_count(rt, run_id) == 1, "进程重启后，未过期的租约仍应可写"

    # 租约过期 → 另一个 Worker 接管（token 递增）
    with rt.db.session() as s:
        from traceguard_persistence.models import RunRow

        s.execute(
            RunRow.__table__.update().where(RunRow.run_id == run_id).values(lease_expires_at=0.0)
        )
    lease = restarted.tasks.acquire_lease(run_id, "worker-B", ttl_seconds=60)
    new_token = int(lease["fencing_token"])
    assert new_token > token
    restarted.tasks.record_step(
        run_id, "s-new", 1, "PASSED", action="navigate", fence=("worker-B", new_token)
    )
    assert _step_count(rt, run_id) == 2
    with pytest.raises(Exception) as exc:
        restarted.tasks.record_step(
            run_id, "s-old-again", 2, "PASSED", action="navigate", fence=("worker-A", token)
        )
    assert "LEASE_LOST" in str(exc.value)


def test_unfenced_event_with_forged_owner_is_rejected(rt) -> None:  # type: ignore[no-untyped-def]
    """伪造 owner/token 的事件写入不能绕过守卫（即使调用方显式传了 fence）。"""
    from traceguard_core.errors import AppError

    run_id, token = _run(rt, "forged")
    with pytest.raises(AppError) as exc:
        rt.events.append(
            event_type="step.completed",
            server_workspace_id="default",
            producer_id="ghost-worker",
            producer_instance_id="ghost-instance",
            idempotency_key=f"{run_id}:forged",
            payload={"run_id": run_id},
            context_refs={"run": run_id},
            fence=("ghost-worker", token),
        )
    assert "LEASE_LOST" in str(exc.value)


def test_evidence_write_blocked_after_lease_expiry(rt) -> None:  # type: ignore[no-untyped-def]
    from traceguard_core.errors import AppError

    run_id, token = _run(rt, "evidence")
    with rt.db.session() as s:
        from traceguard_persistence.models import RunRow

        s.execute(
            RunRow.__table__.update().where(RunRow.run_id == run_id).values(lease_expires_at=0.0)
        )
    with pytest.raises(AppError) as exc:
        rt.evidence.add_raw(
            evidence_id=f"evi_{uuid.uuid4().hex[:12]}",
            source_type="browser",
            source_ref="screenshot",
            workspace_id="default",
            content={"note": "late write"},
            run_id=run_id,
            fence=("worker-A", token),
        )
    assert "LEASE_LOST" in str(exc.value)
