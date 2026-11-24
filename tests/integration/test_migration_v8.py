"""RUN-007 WP1 复核：数据库迁移 v8（`promotion_receipts`）幂等与旧版本兼容。

审计要求：
- 全新库建表时迁移按序应用，v8 恰好一次；
- 重复启动（新进程/新实例）不重复应用迁移，也不重复建表；
- **旧版本数据库**（已应用到 v7、没有 `tg_promotion_receipt`）在升级时必须能补上 v8，
  且不破坏既有数据；
- v8 的表结构满足幂等回执语义（主键 = idempotency_key）。
"""

from __future__ import annotations

import pytest
from sqlalchemy import inspect, text
from traceguard_persistence import Database
from traceguard_persistence.db import MIGRATIONS

EXPECTED = {v for v, _n, _m in MIGRATIONS}


def _versions(db: Database) -> list[int]:
    with db.session() as s:
        return [int(r[0]) for r in s.execute(text("SELECT version FROM tg_schema_migration")).all()]


def _has_table(db: Database, name: str) -> bool:
    return name in inspect(db.engine).get_table_names()


@pytest.fixture()
def db_url(tmp_path):  # type: ignore[no-untyped-def]
    return f"sqlite:///{tmp_path / 'migrate.db'}"


def test_fresh_database_applies_v8_exactly_once(db_url: str) -> None:
    db = Database(db_url)
    try:
        db.create_schema()
        versions = _versions(db)
        assert sorted(versions) == sorted(EXPECTED)
        assert len(versions) == len(set(versions)), "迁移记录重复"
        assert 8 in versions
        assert _has_table(db, "tg_promotion_receipt")
        columns = {c["name"] for c in inspect(db.engine).get_columns("tg_promotion_receipt")}
        assert {"idempotency_key", "kind", "proposal_id", "testcase_id"} <= columns
        assert {"result", "base_version", "target_version"} <= columns
    finally:
        db.dispose()


def test_repeat_create_schema_is_idempotent(db_url: str) -> None:
    db = Database(db_url)
    try:
        db.create_schema()
        db.create_schema()  # 同实例重复
        assert _versions(db) == sorted(set(_versions(db)))
    finally:
        db.dispose()

    db2 = Database(db_url)
    try:
        db2.create_schema()  # 模拟第二次进程启动
        versions = _versions(db2)
        assert sorted(versions) == sorted(EXPECTED)
        assert len(versions) == len(EXPECTED), "重启后迁移被重复应用"
    finally:
        db2.dispose()


def test_legacy_v7_database_upgrades_to_v8_without_data_loss(db_url: str) -> None:
    """模拟"刚升到 v7 的旧库"：没有 v8 表、迁移表里没有 8 → 启动后必须补上 v8。"""
    db = Database(db_url)
    try:
        db.create_schema()
        # 造一条业务数据，升级后必须仍在
        with db.session() as s:
            s.execute(
                text(
                    "INSERT INTO tg_testcase (testcase_id, workspace_id, intent_ref, title, "
                    "review_status, latest_version, compile_source, doc, created_at, updated_at) "
                    "VALUES ('tc_legacy', 'default', 'intent', 'legacy', 'APPROVED', 1, 'human', "
                    "'{}', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')"
                )
            )
        # 回退到 v7 状态
        with db.session() as s:
            s.execute(text("DROP TABLE tg_promotion_receipt"))
            s.execute(text("DELETE FROM tg_schema_migration WHERE version = 8"))
        assert not _has_table(db, "tg_promotion_receipt")
        assert 8 not in _versions(db)
        db._schema_ready = False  # 模拟"升级后重新启动"时的建表流程
        db.create_schema()
        assert 8 in _versions(db)
        assert _has_table(db, "tg_promotion_receipt")
        with db.session() as s:
            assert (
                s.execute(
                    text("SELECT count(*) FROM tg_testcase WHERE testcase_id='tc_legacy'")
                ).scalar_one()
                == 1
            )
            # v8 表可写幂等回执
            s.execute(
                text(
                    "INSERT INTO tg_promotion_receipt (idempotency_key, kind, proposal_id, "
                    "testcase_id, workspace_id, base_version, target_version, result, created_at) "
                    "VALUES ('k1', 'PROMOTE', 'p1', 'tc_legacy', 'default', 1, 2, '{}', '2026-01-01T00:00:00Z')"
                )
            )
        with pytest.raises(Exception):  # noqa: B017 - 主键冲突的具体异常类型由驱动决定
            with db.session() as s:
                s.execute(
                    text(
                        "INSERT INTO tg_promotion_receipt (idempotency_key, kind, proposal_id, "
                        "testcase_id, workspace_id, base_version, target_version, result, created_at) "
                        "VALUES ('k1', 'PROMOTE', 'p1', 'tc_legacy', 'default', 2, 3, '{}', '2026-01-01T00:00:00Z')"
                    )
                )
    finally:
        db.dispose()


def test_v8_migration_is_idempotent_when_reapplied_concurrently(db_url: str) -> None:
    """两个实例同时启动升级：迁移记录仍然只有一份（SQLite 串行化写，靠 IF NOT EXISTS 兜底）。"""
    import threading

    errors: list[BaseException] = []
    created = [Database(db_url), Database(db_url)]
    barrier = threading.Barrier(2)

    def run(database: Database) -> None:
        try:
            barrier.wait(timeout=10)
            database.create_schema()
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=run, args=(d,)) for d in created]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    try:
        assert not errors, f"并发建表失败：{errors!r}"
        db = Database(db_url)
        try:
            versions = _versions(db)
            assert sorted(versions) == sorted(EXPECTED)
            assert len(versions) == len(set(versions))
        finally:
            db.dispose()
    finally:
        for d in created:
            d.dispose()
