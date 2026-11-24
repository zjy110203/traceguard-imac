"""统一持久化层：SQLAlchemy 2.x，SQLite（本地）与 PostgreSQL（生产）同构。

设计要点：
- 单一 Database 对象承载所有平台表（执行/事件/证据/目录/文档聚合）；
- `Session` 通过 `db.session()` 上下文管理，提交/回滚/关闭确定；
- 轻量迁移：`tg_schema_migration` 记录已应用版本，升级按序执行且幂等；
- SQLite 内存库使用 StaticPool，保证同一连接、可测。
"""

from __future__ import annotations

import threading
import time
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
        """建表 + 应用迁移（幂等，且**并发启动安全**）。

        多进程同时启动（API + 多个 Worker 指向同一个库）时，`create_all` 内部的
        "先检查再建" 不是原子的，可能抛出 `table ... already exists`；迁移记录的
        `INSERT` 也可能撞主键。这里显式重试 + 容忍重复，避免启动期随机崩溃。
        """
        from . import models  # noqa: F401  确保模型注册到 metadata

        with self._schema_lock:
            if self._schema_ready:
                return
            last_error: Exception | None = None
            # 并发启动（API + N 个 Worker）时会同时建表：既可能撞"已存在"，
            # 也可能撞 SQLite 的 DDL 排他锁（database is locked）。两类都重试，
            # 退避采用指数形式，避免 CI 慢盘上 6 次×50ms 不够用。
            for attempt in range(8):
                try:
                    Base.metadata.create_all(self.engine)
                    last_error = None
                    break
                except Exception as exc:  # noqa: BLE001 - 并发建表的重复 DDL/锁竞争
                    retryable = _is_duplicate_ddl_error(exc) or _is_transient_lock_error(exc)
                    if not retryable or attempt == 7:
                        raise
                    last_error = exc
                    time.sleep(min(0.1 * (2**attempt), 2.0))
            if last_error is not None:  # pragma: no cover - 重试耗尽才会到这里
                raise last_error
            _apply_migrations(self)
            self._schema_ready = True

    def dispose(self) -> None:
        self.engine.dispose()


def _is_duplicate_ddl_error(exc: BaseException) -> bool:
    """并发建表/建索引时的"已存在"错误：可以容忍并重试。"""
    msg = str(exc).lower()
    return (
        "already exists" in msg
        or "duplicate table" in msg
        or "duplicate key" in msg
        or "unique constraint" in msg
        or "duplicate column" in msg
    )


def _is_transient_lock_error(exc: BaseException) -> bool:
    """并发 DDL 的暂时性锁冲突：不是"已存在"，但等待后重试即可成功。

    SQLite 在多个连接同时执行 `CREATE TABLE` 时会抛 `database is locked`
    （busy_timeout 只覆盖普通写事务，DDL 的排他锁竞争仍可能直接返回）；
    PostgreSQL 在并发 DDL 下可能抛 `deadlock detected`。两者都属可重试。
    """
    msg = str(exc).lower()
    return (
        "database is locked" in msg
        or "database table is locked" in msg
        or "deadlock detected" in msg
        or "could not obtain lock" in msg
    )


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
                # 每个 DDL 包在 SAVEPOINT 里：PostgreSQL 下普通语句报错会让整个事务
                # 进入 aborted 状态，必须回滚到保存点才能继续执行后续迁移。
                try:
                    with conn.begin_nested():
                        if statement.startswith("__CALLABLE__:"):
                            _MIGRATION_CALLABLES[statement.split(":", 1)[1]](conn)
                        else:
                            conn.execute(text(statement))
                except Exception as exc:  # noqa: BLE001 - 并发启动下的重复 DDL
                    if not _is_duplicate_ddl_error(exc):
                        raise
            # 记录迁移版本：并发启动时可能已被另一进程写入同一 version（主键冲突），
            # 属于正常竞争，吞掉即可（绝不能因此启动失败）。
            try:
                with conn.begin_nested():
                    conn.execute(
                        text(
                            "INSERT INTO tg_schema_migration (version, name, applied_at) "
                            "VALUES (:v, :n, :a)"
                        ),
                        {"v": version, "n": name, "a": _now()},
                    )
            except Exception as exc:  # noqa: BLE001
                if not _is_duplicate_ddl_error(exc):
                    raise


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
    (
        8,
        "promotion_receipts",
        [
            "CREATE TABLE IF NOT EXISTS tg_promotion_receipt ("
            "idempotency_key VARCHAR(160) PRIMARY KEY, "
            "kind VARCHAR(20) NOT NULL, "
            "proposal_id VARCHAR(90) NOT NULL DEFAULT '', "
            "testcase_id VARCHAR(64) NOT NULL, "
            "workspace_id VARCHAR(64) NOT NULL DEFAULT 'default', "
            "base_version INTEGER NOT NULL DEFAULT 0, "
            "target_version INTEGER NOT NULL DEFAULT 0, "
            "result JSON, "
            "created_at VARCHAR(40) NOT NULL DEFAULT '')",
            "CREATE INDEX IF NOT EXISTS ix_tg_promotion_receipt_proposal "
            "ON tg_promotion_receipt (proposal_id)",
            "CREATE INDEX IF NOT EXISTS ix_tg_promotion_receipt_testcase "
            "ON tg_promotion_receipt (testcase_id)",
        ],
    ),
    (
        9,
        "evidence_digest_semantics",
        ["__CALLABLE__:_migrate_evidence_digest_semantics"],
    ),
    (
        10,
        "widen_testcase_intent_ref",
        ["__CALLABLE__:_migrate_widen_intent_ref"],
    ),
    (
        11,
        "auth_rbac_audit",
        [
            "CREATE TABLE IF NOT EXISTS tg_user ("
            "user_id VARCHAR(64) PRIMARY KEY, "
            "username VARCHAR(120) NOT NULL UNIQUE, "
            "password_hash VARCHAR(255) NOT NULL, "
            "roles VARCHAR(500) NOT NULL, "
            "active INTEGER NOT NULL DEFAULT 1, "
            "created_at VARCHAR(40) NOT NULL DEFAULT '', "
            "updated_at VARCHAR(40) NOT NULL DEFAULT '')",
            "CREATE TABLE IF NOT EXISTS tg_session ("
            "token_hash VARCHAR(64) PRIMARY KEY, "
            "user_id VARCHAR(64) NOT NULL, "
            "csrf_token VARCHAR(64) NOT NULL, "
            "expires_at FLOAT NOT NULL DEFAULT 0, "
            "revoked INTEGER NOT NULL DEFAULT 0, "
            "created_at VARCHAR(40) NOT NULL DEFAULT '')",
            "CREATE INDEX IF NOT EXISTS ix_tg_session_user ON tg_session (user_id)",
            "CREATE TABLE IF NOT EXISTS tg_audit_log ("
            "audit_id VARCHAR(64) PRIMARY KEY, "
            "actor_id VARCHAR(120) NOT NULL, "
            "role VARCHAR(120) NOT NULL DEFAULT '', "
            "action VARCHAR(120) NOT NULL, "
            "resource_type VARCHAR(120) NOT NULL DEFAULT '', "
            "resource_id VARCHAR(160) NOT NULL DEFAULT '', "
            "timestamp VARCHAR(40) NOT NULL DEFAULT '', "
            "result VARCHAR(40) NOT NULL DEFAULT '', "
            "request_id VARCHAR(120) NOT NULL DEFAULT '', "
            "before_json JSON, after_json JSON, "
            "prev_hash VARCHAR(64) NOT NULL DEFAULT '', "
            "entry_hash VARCHAR(64) NOT NULL DEFAULT '')",
            "CREATE INDEX IF NOT EXISTS ix_tg_audit_actor ON tg_audit_log (actor_id)",
            "CREATE INDEX IF NOT EXISTS ix_tg_audit_action ON tg_audit_log (action)",
        ],
    ),
    (
        12,
        "auth_run010_login_attempt_user_scope_audit_seq",
        [
            "CREATE TABLE IF NOT EXISTS tg_login_attempt ("
            "attempt_id VARCHAR(64) PRIMARY KEY, "
            "username VARCHAR(120) NOT NULL, "
            "client VARCHAR(120) NOT NULL DEFAULT '', "
            "ok INTEGER NOT NULL DEFAULT 0, "
            "created_at VARCHAR(40) NOT NULL DEFAULT '')",
            "CREATE INDEX IF NOT EXISTS ix_tg_login_attempt_key "
            "ON tg_login_attempt (username, client, created_at)",
            "__CALLABLE__:_migrate_audit_seq",
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_tg_audit_seq ON tg_audit_log (seq)",
            "ALTER TABLE tg_user ADD COLUMN workspaces VARCHAR(500) NOT NULL DEFAULT '[\"*\"]'",
        ],
    ),
    (
        13,
        "run_submission_receipts",
        [
            "CREATE TABLE IF NOT EXISTS tg_run_submission ("
            "idempotency_key VARCHAR(160) PRIMARY KEY, "
            "run_id VARCHAR(64) NOT NULL, "
            "fencing_token INTEGER NOT NULL DEFAULT 0, "
            "worker_id VARCHAR(120) NOT NULL DEFAULT '', "
            "execution_status VARCHAR(24) NOT NULL DEFAULT '', "
            "test_verdict VARCHAR(24) NOT NULL DEFAULT 'NOT_EVALUATED', "
            "report_json JSON, error TEXT, "
            "observer_mode VARCHAR(24) NOT NULL DEFAULT '', "
            "submitted_at FLOAT NOT NULL DEFAULT 0)",
            "CREATE INDEX IF NOT EXISTS ix_tg_run_submission_run ON tg_run_submission (run_id)",
        ],
    ),
]


def _migrate_audit_seq(conn) -> None:  # type: ignore[no-untyped-def]
    """给 tg_audit_log 追加单调 seq 列并回填（RUN-010 WP1）。

    旧行没有 seq；按 (timestamp, audit_id) 排序回填 1..N，保证既有哈希链顺序
    与新的 seq 顺序一致。跨进程并发追加依赖 seq 上的唯一索引做乐观分配。
    """
    from sqlalchemy import inspect

    inspector = inspect(conn)
    if "tg_audit_log" not in inspector.get_table_names():
        return
    cols = {c["name"] for c in inspector.get_columns("tg_audit_log")}
    if "seq" in cols:
        return
    conn.execute(text("ALTER TABLE tg_audit_log ADD COLUMN seq INTEGER"))
    rows = conn.execute(
        text("SELECT audit_id FROM tg_audit_log ORDER BY timestamp ASC, audit_id ASC")
    ).fetchall()
    for idx, row in enumerate(rows, start=1):
        conn.execute(
            text("UPDATE tg_audit_log SET seq = :s WHERE audit_id = :a"),
            {"s": idx, "a": row[0]},
        )


def _migrate_evidence_digest_semantics(conn) -> None:  # type: ignore[no-untyped-def]
    """区分"原始来源摘要"与"持久化内容摘要"（RUN-007-FINALIZATION-2 / WP2）。

    历史行的 `sha256` 是脱敏前原始内容的摘要，而库里存的是脱敏后内容：
    两者混用会使"服务端用持久化内容重算摘要"必然对不上。新增列并存，
    历史行标记为 `legacy_backfilled`（内容摘要无法回溯证明，校验时如实报 NOT_RUN）。
    """
    from sqlalchemy import inspect

    inspector = inspect(conn)
    if "tg_evidence" not in inspector.get_table_names():
        return
    cols = {c["name"] for c in inspector.get_columns("tg_evidence")}
    additions = [
        ("stored_content_sha256", "VARCHAR(64) DEFAULT ''"),
        ("attachment_sha256", "VARCHAR(64) DEFAULT ''"),
        ("integrity_detail", "JSON"),
    ]
    for name, ddl in additions:
        if name in cols:
            continue
        conn.execute(text(f"ALTER TABLE tg_evidence ADD COLUMN {name} {ddl}"))


def _migrate_widen_intent_ref(conn) -> None:  # type: ignore[no-untyped-def]
    """把 tg_testcase.intent_ref 从 VARCHAR(64) 放宽到 VARCHAR(255)（RUN-007-FINALIZATION-2）。

    真实缺陷：探索编译曾把"需求正文"写进 intent_ref，SQLite 不校验 VARCHAR 长度所以
    一直正常，PostgreSQL 抛 `value too long for type character varying(64)` → 500。
    语义修复见 compiler/exploration_service；这里同时放宽列以兼容历史长引用。
    """
    from sqlalchemy import inspect

    inspector = inspect(conn)
    if "tg_testcase" not in inspector.get_table_names():
        return
    cols = {c["name"]: c for c in inspector.get_columns("tg_testcase")}
    col = cols.get("intent_ref")
    if col is None:
        return
    length = getattr(col.get("type"), "length", None)
    if length is not None and int(length) >= 255:
        return
    if conn.dialect.name == "sqlite":
        return  # SQLite 不强制 VARCHAR 长度，无需重建表
    conn.execute(text("ALTER TABLE tg_testcase ALTER COLUMN intent_ref TYPE VARCHAR(255)"))


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
    "_migrate_evidence_digest_semantics": _migrate_evidence_digest_semantics,
    "_migrate_widen_intent_ref": _migrate_widen_intent_ref,
    "_migrate_audit_seq": _migrate_audit_seq,
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
