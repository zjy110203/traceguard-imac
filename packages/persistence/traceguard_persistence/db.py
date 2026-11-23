"""统一持久化层：SQLAlchemy 2.x，SQLite（本地）与 PostgreSQL（生产）同构。

设计要点：
- 单一 Database 对象承载所有平台表（执行/事件/证据/目录/文档聚合）；
- `Session` 通过 `db.session()` 上下文管理，提交/回滚/关闭确定；
- 轻量迁移：`tg_schema_migration` 记录已应用版本，升级按序执行且幂等；
- SQLite 内存库使用 StaticPool，保证同一连接、可测。
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Generator, Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool
from traceguard_core.config import get_settings


class Base(DeclarativeBase):
    pass


class Database:
    """数据库门面。生产使用 PostgreSQL 时仅需替换 DSN。"""

    def __init__(self, url: str | None = None, *, echo: bool = False) -> None:
        settings = get_settings()
        self.url = url or settings.database_url
        connect_args: dict[str, Any] = {}
        kwargs: dict[str, Any] = {}
        is_sqlite = self.url.startswith("sqlite")
        if is_sqlite:
            connect_args["check_same_thread"] = False
            # 多进程/多线程写竞争时等待而不是立刻报 "database is locked"
            connect_args["timeout"] = 30.0
            if ":memory:" in self.url:
                kwargs["poolclass"] = StaticPool
        self.engine: Engine = create_engine(
            self.url, echo=echo, future=True, connect_args=connect_args, **kwargs
        )
        if is_sqlite and ":memory:" not in self.url:
            self._enable_sqlite_concurrency()
        self.session_factory = sessionmaker(
            bind=self.engine, autoflush=False, autocommit=False, expire_on_commit=False
        )
        self._schema_lock = threading.RLock()
        self._schema_ready = False

    def _enable_sqlite_concurrency(self) -> None:
        """文件库启用 WAL + busy_timeout：允许并发读写，降低多进程写竞争失败率。"""
        from sqlalchemy import event

        @event.listens_for(self.engine, "connect")
        def _set_pragmas(dbapi_conn, _record):  # type: ignore[no-untyped-def]
            cur = dbapi_conn.cursor()
            try:
                cur.execute("PRAGMA journal_mode=WAL")
                cur.execute("PRAGMA busy_timeout=30000")
                cur.execute("PRAGMA synchronous=NORMAL")
            finally:
                cur.close()

    @contextmanager
    def session(self) -> Iterator[Session]:
        s = self.session_factory()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    def get_session(self) -> Generator[Session, None, None]:
        """FastAPI 依赖注入入口。"""
        s = self.session_factory()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    def create_schema(self) -> None:
        """建表 + 应用迁移（幂等）。"""
        from . import models  # noqa: F401  确保模型注册到 metadata

        with self._schema_lock:
            if self._schema_ready:
                return
            Base.metadata.create_all(self.engine)
            _apply_migrations(self)
            self._schema_ready = True

    def dispose(self) -> None:
        self.engine.dispose()


def _apply_migrations(db: Database) -> None:
    """按序应用迁移。当前 v1 由 metadata.create_all 完成；v2 起为增量 DDL。

    迁移必须是幂等的：多进程同时启动时使用 IF NOT EXISTS / 捕获重复错误。
    """
    with db.engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS tg_schema_migration ("
                "version INTEGER PRIMARY KEY, name VARCHAR(120) NOT NULL, "
                "applied_at VARCHAR(40) NOT NULL)"
            )
        )
        applied = {
            int(r[0])
            for r in conn.execute(text("SELECT version FROM tg_schema_migration")).fetchall()
        }
        for version, name, sql in MIGRATIONS:
            if version in applied:
                continue
            for statement in sql:
                statement = statement.strip()
                if not statement:
                    continue
                if statement.startswith("__CALLABLE__:"):
                    _MIGRATION_CALLABLES[statement.split(":", 1)[1]](conn)
                    continue
                try:
                    conn.execute(text(statement))
                except Exception as exc:  # noqa: BLE001 - 并发启动下的重复 DDL
                    if (
                        "already exists" not in str(exc).lower()
                        and "duplicate" not in str(exc).lower()
                    ):
                        raise
            conn.execute(
                text(
                    "INSERT INTO tg_schema_migration (version, name, applied_at) "
                    "VALUES (:v, :n, :a)"
                ),
                {"v": version, "n": name, "a": _now()},
            )


def _now() -> str:
    from traceguard_core.ids import utc_now_iso

    return utc_now_iso()


#: (version, name, [DDL...])
MIGRATIONS: list[tuple[int, str, list[str]]] = [
    (
        2,
        "event_and_evidence_indexes",
        [
            "CREATE INDEX IF NOT EXISTS ix_tg_event_ws_type ON tg_event (workspace_id, event_type)",
            "CREATE INDEX IF NOT EXISTS ix_tg_evidence_run ON tg_evidence (workspace_id, run_id)",
        ],
    ),
    (
        3,
        "doc_store_index",
        [
            "CREATE INDEX IF NOT EXISTS ix_tg_doc_kind_ws ON tg_doc (kind, workspace_id)",
        ],
    ),
    (
        4,
        "step_primary_key_scoped_by_run",
        ["__CALLABLE__:_migrate_step_primary_key"],
    ),
    (
        5,
        "consumer_inbox_states",
        ["__CALLABLE__:_migrate_inbox_states"],
    ),
    (
        6,
        "producer_sequence_allocator",
        [
            "CREATE TABLE IF NOT EXISTS tg_producer_sequence ("
            "producer_instance_id VARCHAR(120) PRIMARY KEY, "
            "last_sequence BIGINT NOT NULL DEFAULT 0, "
            "updated_at VARCHAR(40) NOT NULL DEFAULT '')",
        ],
    ),
    (
        7,
        "testcase_review_revision",
        ["__CALLABLE__:_migrate_review_revision"],
    ),
]


def _migrate_step_primary_key(conn) -> None:  # type: ignore[no-untyped-def]
    """把 tg_step 主键从 step_id 改为 (run_id, step_id)。

    旧模型里 step_id 全局主键，同一用例多次 Run 会互相覆盖（真实缺陷）。
    迁移必须兼容：全新库（已是新结构）、旧库（有 index 列）、PostgreSQL。
    """
    from sqlalchemy import inspect

    inspector = inspect(conn)
    if "tg_step" not in inspector.get_table_names():
        return
    cols = {c["name"] for c in inspector.get_columns("tg_step")}
    if "id" in cols:
        return  # 新结构，无需迁移
    conn.execute(text("ALTER TABLE tg_step RENAME TO tg_step_legacy"))
    conn.execute(
        text(
            "CREATE TABLE tg_step ("
            "id VARCHAR(140) PRIMARY KEY, "
            "step_id VARCHAR(64) NOT NULL, run_id VARCHAR(64) NOT NULL, "
            "step_index INTEGER NOT NULL DEFAULT 0, action VARCHAR(20) DEFAULT '', "
            "status VARCHAR(24) DEFAULT 'PENDING', error TEXT, "
            "duration_ms INTEGER DEFAULT 0, observation TEXT)"
        )
    )
    legacy_cols = {c["name"] for c in inspect(conn).get_columns("tg_step_legacy")}
    index_expr = '"index"' if "index" in legacy_cols else "0"
    select_sql = (
        "SELECT run_id || ':' || step_id, step_id, run_id, "
        f"{index_expr}, action, status, error, duration_ms, observation FROM tg_step_legacy"
    )
    insert_sql = (
        "INSERT INTO tg_step (id, step_id, run_id, step_index, action, status, error, "
        "duration_ms, observation) " + select_sql
    )
    if conn.dialect.name == "sqlite":
        conn.execute(text(insert_sql.replace("INSERT INTO", "INSERT OR IGNORE INTO", 1)))
    else:
        conn.execute(text(insert_sql + " ON CONFLICT DO NOTHING"))
    conn.execute(text("DROP TABLE tg_step_legacy"))


def _migrate_inbox_states(conn) -> None:  # type: ignore[no-untyped-def]
    """Consumer Inbox 补齐可靠消费语义所需的列（幂等）。

    旧版本只有 PROCESSED 单一状态；新版本区分 PROCESSING / SUCCEEDED / FAILED / UNKNOWN，
    并增加租约（claim_expires_at）以支持崩溃恢复与多进程抢占。
    """
    from sqlalchemy import inspect

    inspector = inspect(conn)
    if "tg_consumer_inbox" not in inspector.get_table_names():
        return
    cols = {c["name"] for c in inspector.get_columns("tg_consumer_inbox")}
    additions = [
        ("attempts", "INTEGER DEFAULT 0"),
        ("claim_expires_at", "FLOAT DEFAULT 0"),
        ("indeterminate", "BOOLEAN DEFAULT FALSE"),
        ("last_error", "TEXT"),
        ("updated_at", "VARCHAR(40) DEFAULT ''"),
    ]
    for name, ddl in additions:
        if name in cols:
            continue
        conn.execute(text(f"ALTER TABLE tg_consumer_inbox ADD COLUMN {name} {ddl}"))
    conn.execute(text("UPDATE tg_consumer_inbox SET status='SUCCEEDED' WHERE status='PROCESSED'"))


def _migrate_review_revision(conn) -> None:  # type: ignore[no-untyped-def]
    """为用例版本表增加审核修订号（并发审核 CAS 令牌），幂等。"""
    from sqlalchemy import inspect

    inspector = inspect(conn)
    if "tg_testcase_version" not in inspector.get_table_names():
        return
    cols = {c["name"] for c in inspector.get_columns("tg_testcase_version")}
    if "review_revision" in cols:
        return
    conn.execute(
        text("ALTER TABLE tg_testcase_version ADD COLUMN review_revision INTEGER DEFAULT 0")
    )


#: 迁移语句中 `__CALLABLE__:<name>` 的注册表（必须在函数定义之后）
_MIGRATION_CALLABLES = {
    "_migrate_step_primary_key": _migrate_step_primary_key,
    "_migrate_inbox_states": _migrate_inbox_states,
    "_migrate_review_revision": _migrate_review_revision,
}

_lock = threading.RLock()
_default_db: Database | None = None


def get_database(url: str | None = None, *, echo: bool | None = None) -> Database:
    """进程级默认 Database（可在测试中重定向 URL）。"""
    global _default_db
    with _lock:
        settings = get_settings()
        target = url or settings.database_url
        if _default_db is None or (_default_db.url != target and url is not None):
            _default_db = Database(target, echo=echo if echo is not None else settings.db_echo)
            _default_db.create_schema()
        elif not _default_db._schema_ready:
            _default_db.create_schema()
        return _default_db


def set_database(db: Database) -> None:
    """替换进程级 Database（测试/多租户部署用）。"""
    global _default_db
    with _lock:
        _default_db = db
        db.create_schema()


DbFactory = Callable[[], Database]
