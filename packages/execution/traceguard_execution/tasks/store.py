"""Task/Run/Step 持久化存储（阶段3）。

权威状态存 SQLite（可切 PostgreSQL）。核心不变量：
- Task 是逻辑任务，Run 是执行尝试；重试产生新 Run，不覆盖历史。
- execution_status 与 test_verdict 独立。
- Run 有租约（lease_owner/lease_expires_at）与执行代次（fencing_token），旧代次提交被拒绝（F06）。
"""

from __future__ import annotations

import time

from sqlalchemy import (
    Integer,
    String,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

# 平台执行库（与 TeamFlow、平台主库隔离）
_URL = "sqlite:///./.traceguard/execution.db"
_engine = create_engine(_URL, connect_args={"check_same_thread": False}, future=True)
ExecSession = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False)


class ExecBase(DeclarativeBase):
    pass


class TaskRow(ExecBase):
    __tablename__ = "ex_task"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[str] = mapped_column(String, unique=True, index=True)
    testcase_ref: Mapped[str] = mapped_column(String)
    application: Mapped[str] = mapped_column(String, default="teamflow")
    status: Mapped[str] = mapped_column(String, default="CREATED")
    idempotency_key: Mapped[str] = mapped_column(String, unique=True, index=True)
    created_at: Mapped[float] = mapped_column(default=time.time)


class RunRow(ExecBase):
    __tablename__ = "ex_run"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(String, unique=True, index=True)
    task_id: Mapped[str] = mapped_column(String, index=True)
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    testcase_version_id: Mapped[str] = mapped_column(String)
    execution_status: Mapped[str] = mapped_column(String, default="QUEUED")
    test_verdict: Mapped[str] = mapped_column(String, default="NOT_EVALUATED")
    fencing_token: Mapped[int] = mapped_column(Integer, default=0)
    lease_owner: Mapped[str | None] = mapped_column(String, nullable=True)
    lease_expires_at: Mapped[float] = mapped_column(default=0.0)
    report_json: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[float] = mapped_column(default=time.time)


class StepRow(ExecBase):
    __tablename__ = "ex_step"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    step_id: Mapped[str] = mapped_column(String, unique=True, index=True)
    run_id: Mapped[str] = mapped_column(String, index=True)
    index: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String, default="PENDING")
    error: Mapped[str | None] = mapped_column(String, nullable=True)


def init_exec_db() -> None:
    import os

    os.makedirs(".traceguard", exist_ok=True)
    ExecBase.metadata.create_all(_engine)
