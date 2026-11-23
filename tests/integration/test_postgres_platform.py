"""RUN-007 WP4-01：PostgreSQL 16 平台级验证（Service Container 内执行）。

与 `test_postgres_parity.py`（存储语义一致性）互补，本模块验证 **平台契约** 在真实
PG 上同样成立，而不是只在 SQLite 上成立：

1. 全新库建表 + 迁移（含 v8 `promotion_receipts`）只应用一次，重复启动幂等；
2. 多连接并发启动建表不产生重复迁移记录、不崩溃；
3. `claim_next_run` 的租约 CAS 在多连接竞争下只有一个赢家，代次从 1 开始；
4. 租约过期 → reap → 新 Worker 接管，`fencing_token` 严格递增，旧代次提交被拒；
5. 晋升 CAS 的多连接并发只有一个赢家，幂等回执重放不产生额外版本（v8 表）；
6. 生产方序列分配器在并发下不重号。

本地无 PG 时整体跳过；CI 由 `.github/workflows/nightly-gate.yml` 的 PostgreSQL
Service Container 提供 `TG_TEST_POSTGRES_URL`。
"""

from __future__ import annotations

import os
import threading
import time
import uuid
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from traceguard_core.errors import AppError, ErrorCode
from traceguard_persistence import Database
from traceguard_persistence.catalog import CatalogService
from traceguard_persistence.db import MIGRATIONS
from traceguard_persistence.models import RunRow

PG_URL = os.environ.get("TG_TEST_POSTGRES_URL", "")

pytestmark = pytest.mark.skipif(not PG_URL, reason="TG_TEST_POSTGRES_URL 未配置（本地无 PG）")

EXPECTED_VERSIONS = {v for v, _n, _sql in MIGRATIONS}


# --------------------------------------------------------------- fresh database
def _admin_engine():  # type: ignore[no-untyped-def]
    url = make_url(PG_URL).set(database="postgres")
    return create_engine(url, isolation_level="AUTOCOMMIT", future=True)


def _fresh_database(prefix: str) -> str:
    """在 Service Container 里创建一个独立空库，返回其 URL（用例之间彻底隔离）。"""
    name = f"tg_{prefix}_{uuid.uuid4().hex[:10]}"
    engine = _admin_engine()
    try:
        with engine.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{name}"'))
    finally:
        engine.dispose()
    return str(make_url(PG_URL).set(database=name))


def _drop_database(url: str) -> None:
    name = make_url(url).database
    engine = _admin_engine()
    try:
        with engine.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    finally:
        engine.dispose()


@pytest.fixture()
def pg_url() -> Any:
    url = _fresh_database("plat")
    yield url
    _drop_database(url)


@pytest.fixture()
def db(pg_url: str) -> Any:
    database = Database(pg_url)
    database.create_schema()
    yield database
    database.dispose()


def _insert_run(db: Database, run_id: str, *, task_id: str = "task_pg") -> None:
    with db.session() as s:
        s.add(
            RunRow(
                run_id=run_id,
                task_id=task_id,
                workspace_id="default",
                testcase_version_id="tcase_pg@1",
                execution_status="QUEUED",
                test_verdict="NOT_EVALUATED",
            )
        )


def _migration_rows(db: Database) -> list[tuple[int, str]]:
    with db.session() as s:
        return [
            (int(r[0]), str(r[1]))
            for r in s.execute(text("SELECT version, name FROM tg_schema_migration")).all()
        ]


# ------------------------------------------------------------------- migrations
def test_fresh_pg_schema_applies_v8_once_and_is_idempotent(pg_url: str) -> None:
    """全新库：v8 建表成功；重复 create_schema（含新进程/新实例）不重复应用迁移。"""
    db = Database(pg_url)
    try:
        db.create_schema()
        rows = _migration_rows(db)
        assert {v for v, _n in rows} == EXPECTED_VERSIONS
        assert len(rows) == len(EXPECTED_VERSIONS), "迁移记录出现重复"
        assert (8, "promotion_receipts") in rows

        # 字段齐全（v8 表用于晋升/回滚幂等回执）
        with db.session() as s:
            cols = {
                str(r[0])
                for r in s.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = 'tg_promotion_receipt'"
                    )
                ).all()
            }
        assert {
            "idempotency_key",
            "kind",
            "proposal_id",
            "testcase_id",
            "base_version",
            "target_version",
            "result",
        } <= cols

        # 同一实例重复调用
        db.create_schema()
        assert len(_migration_rows(db)) == len(EXPECTED_VERSIONS)
    finally:
        db.dispose()

    # 模拟"第二次进程启动"：新实例再建一次 schema
    db2 = Database(pg_url)
    try:
        db2.create_schema()
        rows2 = _migration_rows(db2)
        assert len(rows2) == len(EXPECTED_VERSIONS), "重启后迁移被重复应用"
        assert sorted(v for v, _n in rows2) == sorted(EXPECTED_VERSIONS)
    finally:
        db2.dispose()


def test_concurrent_schema_bootstrap_on_pg_is_safe(pg_url: str) -> None:
    """4 个连接同时建表 + 迁移：不崩溃、不产生重复迁移记录（多 Worker 同时启动）。"""
    errors: list[BaseException] = []
    barrier = threading.Barrier(4)

    def boostrap() -> None:
        database = Database(pg_url)
        try:
            barrier.wait(timeout=20)
            database.create_schema()
        except BaseException as exc:  # noqa: BLE001 - 测试内收集
            errors.append(exc)
        finally:
            database.dispose()

    threads = [threading.Thread(target=boostrap) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    assert not errors, f"并发建表失败: {errors!r}"
    probe = Database(pg_url)
    try:
        rows = _migration_rows(probe)
        versions = [v for v, _n in rows]
        assert len(versions) == len(set(versions)) == len(EXPECTED_VERSIONS)
    finally:
        probe.dispose()


# ------------------------------------------------------------- lease / fencing
def test_claim_next_run_is_atomic_across_connections(db: Database) -> None:
    """12 个连接同时抢 1 个 Run：恰好 1 个成功，其余返回 None；代次为 1。"""
    _insert_run(db, "run_pg_claim")
    winners: list[dict[str, Any]] = []
    lock = threading.Lock()
    barrier = threading.Barrier(12)

    def worker(idx: int) -> None:
        from traceguard_execution.tasks.service import TaskService

        svc = TaskService(db)
        barrier.wait(timeout=30)
        got = svc.claim_next_run(f"pg-w{idx}", ttl_seconds=60)
        if got:
            with lock:
                winners.append(got)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    assert len(winners) == 1, f"并发领取出现 {len(winners)} 个赢家"
    assert winners[0]["run_id"] == "run_pg_claim"
    assert winners[0]["fencing_token"] == 1
    assert winners[0]["previous_status"] == "QUEUED"


def test_expired_lease_takeover_bumps_fencing_and_rejects_stale_submit(db: Database) -> None:
    """租约过期 → reap → 新 Worker 接管 → 旧代次提交必须被拒（跨连接）。"""
    from traceguard_execution.tasks.service import TaskService

    _insert_run(db, "run_pg_takeover")
    first = TaskService(db).claim_next_run("pg-w-old", ttl_seconds=0.2)
    assert first and first["fencing_token"] == 1

    time.sleep(0.45)
    reaped = TaskService(db).reap_expired_runs()
    assert any(r["run_id"] == "run_pg_takeover" for r in reaped)

    second = TaskService(db).claim_next_run("pg-w-new", ttl_seconds=60)
    assert second and second["run_id"] == "run_pg_takeover"
    assert second["fencing_token"] == 2, "接管后代次必须严格增加"

    # 旧代次提交：STALE_FENCING_TOKEN
    with pytest.raises(AppError) as exc:
        TaskService(db).submit_result(
            "run_pg_takeover",
            "pg-w-old",
            1,
            execution_status="COMPLETED",
            test_verdict="PASSED",
        )
    assert exc.value.code is ErrorCode.CONFLICT
    assert "STALE_FENCING_TOKEN" in str(exc.value)

    # 旧 Worker 也不能续租
    assert TaskService(db).renew_lease("run_pg_takeover", "pg-w-old", 1) is False

    # 新代次提交成功
    TaskService(db).submit_result(
        "run_pg_takeover",
        "pg-w-new",
        2,
        execution_status="COMPLETED",
        test_verdict="PASSED",
        report={"ok": True},
    )
    with db.session() as s:
        row = s.get(RunRow, "run_pg_takeover")
        assert row is not None
        assert row.execution_status == "COMPLETED"
        assert row.test_verdict == "PASSED"
        assert row.lease_expires_at == 0.0


# --------------------------------------------------- promotion CAS + receipts v8
def _testcase_payload() -> dict[str, Any]:
    return {
        "testcase_id": "tcase_pg_cas",
        "version": 1,
        "steps": [{"step_id": "s1", "action": "navigate", "value": "/projects"}],
        "assertions": [
            {
                "assertion_id": "a1",
                "description": "标题存在",
                "kind": "ui",
                "required": True,
                "expected": {"test_id": "page-title"},
                "oracle_id": "oracle_pg_a1",
            }
        ],
        "run_config": {"base_url": "http://127.0.0.1:8011"},
        "compile_source": "human",
        "created_at": "2026-01-01T00:00:00Z",
    }


def test_promotion_cas_has_single_winner_and_receipt_replay(db: Database) -> None:
    """两个连接基于同一 base_version 并发晋升：只有一个成功；幂等回执重放不加版本。"""
    catalog = CatalogService(db)
    catalog.create_testcase(_testcase_payload(), title="pg-cas", review_status="APPROVED")

    results: list[Any] = []
    lock = threading.Lock()
    barrier = threading.Barrier(2)

    def promote(key: str) -> None:
        svc = CatalogService(db)
        barrier.wait(timeout=20)
        try:
            doc = svc.promote_version_cas(
                "tcase_pg_cas",
                base_version=1,
                candidate={**_testcase_payload(), "compile_source": "human"},
                idempotency_key=key,
                proposal_id="prop_pg_cas",
            )
        except AppError as exc:
            with lock:
                results.append(exc)
        else:
            with lock:
                results.append(doc)

    threads = [threading.Thread(target=promote, args=(f"pg-cas-{i}",)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    ok = [r for r in results if isinstance(r, dict)]
    conflicts = [r for r in results if isinstance(r, AppError)]
    assert len(ok) == 1, f"并发晋升出现 {len(ok)} 个成功"
    assert len(conflicts) == 1
    assert conflicts[0].code is ErrorCode.CONFLICT
    assert "VERSION_CONFLICT" in str(conflicts[0])

    # 幂等重放：同一幂等键再提交一次，命中回执，不再产生新版本
    replay = CatalogService(db).promote_version_cas(
        "tcase_pg_cas",
        base_version=1,
        candidate={**_testcase_payload(), "compile_source": "human"},
        idempotency_key="pg-cas-replay",
        proposal_id="prop_pg_cas",
    )
    assert replay.get("idempotent_hit") is True
    assert replay.get("version") == 2

    assert CatalogService(db).approved_version_number("tcase_pg_cas") == 2
    with db.session() as s:
        versions = [
            int(r[0])
            for r in s.execute(
                text(
                    "SELECT version FROM tg_testcase_version "
                    "WHERE testcase_id = 'tcase_pg_cas' ORDER BY version"
                )
            ).all()
        ]
        receipts = int(s.execute(text("SELECT count(*) FROM tg_promotion_receipt")).scalar_one())
    assert versions == [1, 2], f"幂等重放产生了额外版本: {versions}"
    assert receipts >= 1


def test_producer_sequence_allocator_does_not_reuse_numbers(db: Database) -> None:
    """生产方序列分配器在 PG 上并发不重号（v6 表 upsert + RETURNING）。"""
    from traceguard_persistence import PersistentEventStore

    store = PersistentEventStore(db)
    instance_id = store.new_producer_instance("pg-producer")
    allocated: list[int] = []
    lock = threading.Lock()
    barrier = threading.Barrier(4)

    def allocate(n: int) -> None:
        barrier.wait(timeout=20)
        got: list[int] = []
        for i in range(5):
            ev = store.append(
                event_type="pg.sequence_probe",
                server_workspace_id="default",
                producer_id="pg-producer",
                producer_instance_id=instance_id,
                idempotency_key=f"pg-seq-{n}-{i}",
                payload={"n": n, "i": i},
            )
            got.append(ev.sequence)
        with lock:
            allocated.extend(got)

    threads = [threading.Thread(target=allocate, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    assert len(allocated) == 20
    assert len(set(allocated)) == 20, f"序列重复: {sorted(allocated)}"
    assert min(allocated) >= 1
