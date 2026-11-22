"""平台 ORM 表定义（单一 Schema，SQLite/PostgreSQL 通用）。

约定：
- 业务聚合（TestCase 版本、Oracle、Evidence、Diagnosis、Repair 等）保存完整 JSON 文档，
  同时把需要查询/约束的字段提升为列（状态、版本、workspace、时间）。
- 事件表有两条硬约束：`(producer_instance_id, sequence)` 唯一、`(producer_id, idempotency_key)` 唯一。
- JSON 使用 SQLAlchemy `JSON` 类型：SQLite 存 TEXT，PostgreSQL 存 JSONB。
"""

from __future__ import annotations

import time
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Float,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column
from traceguard_core.ids import utc_now_iso

from .db import Base


def _ts() -> float:
    return time.time()


class WorkspaceRow(Base):
    __tablename__ = "tg_workspace"
    workspace_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[str] = mapped_column(String(40), default=utc_now_iso)
    created_by: Mapped[str] = mapped_column(String(80), default="system")


class IntentRow(Base):
    __tablename__ = "tg_intent"
    intent_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(64), index=True)
    title: Mapped[str] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(20), default="DRAFT")
    version: Mapped[int] = mapped_column(Integer, default=1)
    doc: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[str] = mapped_column(String(40), default=utc_now_iso)
    updated_at: Mapped[str] = mapped_column(String(40), default=utc_now_iso)


class TestCaseRow(Base):
    __tablename__ = "tg_testcase"
    testcase_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(64), index=True)
    intent_ref: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(300), default="")
    review_status: Mapped[str] = mapped_column(String(20), default="DRAFT")
    latest_version: Mapped[int] = mapped_column(Integer, default=0)
    compile_source: Mapped[str] = mapped_column(String(20), default="human")
    doc: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[str] = mapped_column(String(40), default=utc_now_iso)
    updated_at: Mapped[str] = mapped_column(String(40), default=utc_now_iso)


class TestCaseVersionRow(Base):
    __tablename__ = "tg_testcase_version"
    id: Mapped[str] = mapped_column(String(90), primary_key=True)  # tcase_x@1
    testcase_id: Mapped[str] = mapped_column(String(64), index=True)
    workspace_id: Mapped[str] = mapped_column(String(64), index=True)
    version: Mapped[int] = mapped_column(Integer)
    review_status: Mapped[str] = mapped_column(String(20), default="DRAFT")
    immutable: Mapped[bool] = mapped_column(Boolean, default=True)
    doc: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[str] = mapped_column(String(40), default=utc_now_iso)
    __table_args__ = (UniqueConstraint("testcase_id", "version", name="uq_tcase_version"),)


class OracleRow(Base):
    __tablename__ = "tg_oracle"
    id: Mapped[str] = mapped_column(String(90), primary_key=True)  # oracle_x@1
    oracle_id: Mapped[str] = mapped_column(String(64), index=True)
    workspace_id: Mapped[str] = mapped_column(String(64), index=True)
    version: Mapped[int] = mapped_column(Integer)
    assertion_id: Mapped[str] = mapped_column(String(64), default="")
    approved: Mapped[bool] = mapped_column(Boolean, default=False)
    approved_by: Mapped[str] = mapped_column(String(80), default="")
    doc: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[str] = mapped_column(String(40), default=utc_now_iso)
    __table_args__ = (UniqueConstraint("oracle_id", "version", name="uq_oracle_version"),)


class TaskRow(Base):
    __tablename__ = "tg_task"
    task_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(64), index=True, default="default")
    testcase_ref: Mapped[str] = mapped_column(String(80))
    testcase_version_id: Mapped[str] = mapped_column(String(90), default="")
    application: Mapped[str] = mapped_column(String(80), default="teamflow")
    status: Mapped[str] = mapped_column(String(20), default="CREATED")
    idempotency_key: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    mode: Mapped[str] = mapped_column(String(20), default="playwright")
    created_at: Mapped[float] = mapped_column(Float, default=_ts)
    updated_at: Mapped[float] = mapped_column(Float, default=_ts)


class RunRow(Base):
    __tablename__ = "tg_run"
    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(64), index=True)
    workspace_id: Mapped[str] = mapped_column(String(64), index=True, default="default")
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    testcase_version_id: Mapped[str] = mapped_column(String(90))
    execution_status: Mapped[str] = mapped_column(String(24), default="QUEUED")
    test_verdict: Mapped[str] = mapped_column(String(24), default="NOT_EVALUATED")
    observer_mode: Mapped[str] = mapped_column(String(24), default="")
    fencing_token: Mapped[int] = mapped_column(Integer, default=0)
    lease_owner: Mapped[str | None] = mapped_column(String(120), nullable=True)
    lease_expires_at: Mapped[float] = mapped_column(Float, default=0.0)
    report_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[float] = mapped_column(Float, default=_ts)
    started_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    ended_at: Mapped[float | None] = mapped_column(Float, nullable=True)


class StepRow(Base):
    __tablename__ = "tg_step"
    step_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    index: Mapped[int] = mapped_column(Integer, default=0)
    action: Mapped[str] = mapped_column(String(20), default="")
    status: Mapped[str] = mapped_column(String(24), default="PENDING")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    observation: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class EventRow(Base):
    __tablename__ = "tg_event"
    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(80), index=True)
    schema_version: Mapped[str] = mapped_column(String(20), default="0.1.0")
    workspace_id: Mapped[str] = mapped_column(String(64), index=True)
    context_refs: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)
    producer_id: Mapped[str] = mapped_column(String(80))
    producer_instance_id: Mapped[str] = mapped_column(String(120))
    sequence: Mapped[int] = mapped_column(BigInteger)
    occurred_at: Mapped[str] = mapped_column(String(40))
    observed_at: Mapped[str] = mapped_column(String(40))
    idempotency_key: Mapped[str] = mapped_column(String(160))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    payload_hash: Mapped[str] = mapped_column(String(64))
    recorded_at: Mapped[str] = mapped_column(String(40), default=utc_now_iso)
    __table_args__ = (
        UniqueConstraint("producer_instance_id", "sequence", name="uq_event_seq"),
        UniqueConstraint("producer_id", "idempotency_key", name="uq_event_idem"),
    )


class EventConflictRow(Base):
    __tablename__ = "tg_event_conflict"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    producer_id: Mapped[str] = mapped_column(String(80))
    idempotency_key: Mapped[str] = mapped_column(String(160))
    expected_hash: Mapped[str] = mapped_column(String(64))
    got_hash: Mapped[str] = mapped_column(String(64))
    at: Mapped[str] = mapped_column(String(40), default=utc_now_iso)


class EvidenceRow(Base):
    __tablename__ = "tg_evidence"
    evidence_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(64), index=True)
    run_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(20), index=True)
    source_type: Mapped[str] = mapped_column(String(30), index=True)
    sha256: Mapped[str] = mapped_column(String(64))
    attachment_ref: Mapped[str | None] = mapped_column(String(200), nullable=True)
    source_trust: Mapped[str] = mapped_column(String(20), default="UNKNOWN")
    integrity_check: Mapped[str] = mapped_column(String(12), default="NOT_RUN")
    redacted: Mapped[bool] = mapped_column(Boolean, default=False)
    doc: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    captured_at: Mapped[str] = mapped_column(String(40), default=utc_now_iso)


class InboxRow(Base):
    __tablename__ = "tg_consumer_inbox"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    consumer_id: Mapped[str] = mapped_column(String(80))
    event_id: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), default="PROCESSED")
    result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    consumed_at: Mapped[str] = mapped_column(String(40), default=utc_now_iso)
    __table_args__ = (UniqueConstraint("consumer_id", "event_id", name="uq_inbox"),)


class ArtifactRow(Base):
    __tablename__ = "tg_artifact"
    artifact_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(64), index=True)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    size: Mapped[int] = mapped_column(BigInteger, default=0)
    media_type: Mapped[str] = mapped_column(String(80), default="application/octet-stream")
    path: Mapped[str] = mapped_column(String(400))
    backend: Mapped[str] = mapped_column(String(20), default="local")
    original_name: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[str] = mapped_column(String(40), default=utc_now_iso)


class DocRow(Base):
    """通用聚合文档表：graph / diagnosis / probe / disposition / repair / experiment 等。

    复杂嵌套结构（如 Evidence Graph 的节点与边）以文档形式持久化，
    需要查询的维度（kind、workspace、关联 id）提升为列。
    """

    __tablename__ = "tg_doc"
    id: Mapped[str] = mapped_column(String(90), primary_key=True)
    kind: Mapped[str] = mapped_column(String(40))
    workspace_id: Mapped[str] = mapped_column(String(64), default="default")
    ref_id: Mapped[str | None] = mapped_column(String(90), nullable=True, index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(30), default="")
    doc: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[str] = mapped_column(String(40), default=utc_now_iso)
    updated_at: Mapped[str] = mapped_column(String(40), default=utc_now_iso)


class MetricRow(Base):
    __tablename__ = "tg_metric"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    experiment_id: Mapped[str] = mapped_column(String(64), index=True)
    baseline: Mapped[str] = mapped_column(String(80))
    metric: Mapped[str] = mapped_column(String(80))
    value: Mapped[float] = mapped_column(Float)
    numerator: Mapped[int | None] = mapped_column(Integer, nullable=True)
    denominator: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ci_low: Mapped[float | None] = mapped_column(Float, nullable=True)
    ci_high: Mapped[float | None] = mapped_column(Float, nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[str] = mapped_column(String(40), default=utc_now_iso)


Index("ix_tg_run_queue", RunRow.execution_status, RunRow.lease_expires_at)
Index("ix_tg_step_run_idx", StepRow.run_id, StepRow.index)
