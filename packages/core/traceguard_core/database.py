"""数据库会话与 Base。通过 DATABASE_URL 抽象 SQLite / PostgreSQL。

阶段0/1 因本机无 Docker/psql，默认 SQLite；生产/完整验收用 PostgreSQL。
架构约束：PostgreSQL 是业务状态权威来源，SQLite 仅为本地开发降级。
"""

from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import ensure_sqlite_dir, get_settings

_settings = get_settings()
ensure_sqlite_dir(_settings.database_url)

_is_sqlite = _settings.database_url.startswith("sqlite")
_connect_args = {"check_same_thread": False} if _is_sqlite else {}
_engine = create_engine(
    _settings.database_url,
    echo=_settings.db_echo,
    connect_args=_connect_args,
    future=True,
)
SessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def engine():
    return _engine
