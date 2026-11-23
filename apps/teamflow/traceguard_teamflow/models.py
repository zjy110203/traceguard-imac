"""TeamFlow 数据模型（业务状态权威存储）。"""

from __future__ import annotations

import os

from sqlalchemy import Boolean, Integer, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
from traceguard_core.config import get_settings

_settings = get_settings()
# TeamFlow 使用独立 SQLite 文件，与平台库隔离（避免污染平台业务状态）
# 允许通过 TG_TEAMFLOW_DB 指向隔离库（演示/验收必须使用独立数据库）
_tf_url = os.environ.get("TG_TEAMFLOW_DB") or "sqlite:///./.traceguard/teamflow.db"
_engine = create_engine(
    _tf_url,
    connect_args={"check_same_thread": False},
    future=True,
)
TeamFlowSession = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False)


class TFBase(DeclarativeBase):
    pass


class User(TFBase):
    __tablename__ = "tf_user"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(128))  # 测试账户用简单哈希，非生产
    display_name: Mapped[str] = mapped_column(String(64), default="")


class Workspace(TFBase):
    __tablename__ = "tf_workspace"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(128))
    owner_username: Mapped[str] = mapped_column(String(64))


class Project(TFBase):
    __tablename__ = "tf_project"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workspace_slug: Mapped[str] = mapped_column(String(64), index=True)
    key: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32), default="ACTIVE")
    created_by: Mapped[str] = mapped_column(String(64), default="")
    # 用于注入"持久化失败"：标记逻辑删除而不实际写入
    persistence_blocked: Mapped[bool] = mapped_column(Boolean, default=False)


def init_db() -> None:
    import os

    os.makedirs(".traceguard", exist_ok=True)
    TFBase.metadata.create_all(_engine)


def get_session() -> Session:
    return TeamFlowSession()
