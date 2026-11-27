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
    #: 需求引用（intent_id）。v10 从 64 放宽到 255：调用方可能传入较长引用；
    #: 需求正文不得写进这里（见 TestCaseVersion.intent_text）。
    intent_ref: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
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
    #: 审核修订号：并发审核的 CAS 令牌（RUN-006 WP1-01）
    review_revision: Mapped[int] = mapped_column(Integer, default=0)
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


class RunSubmissionRow(Base):
    """RUN-010 WP3：终态/中间态提交回执（幂等键 → 原回执重放）。

    与 Run 状态变更在同一事务内写入；同一幂等键重复提交返回原回执，
    不改写 verdict / report（冻结语义）。
    """

    __tablename__ = "tg_run_submission"
    idempotency_key: Mapped[str] = mapped_column(String(160), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    fencing_token: Mapped[int] = mapped_column(Integer, default=0)
    worker_id: Mapped[str] = mapped_column(String(120), default="")
    execution_status: Mapped[str] = mapped_column(String(24), default="")
    test_verdict: Mapped[str] = mapped_column(String(24), default="NOT_EVALUATED")
    report_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    observer_mode: Mapped[str] = mapped_column(String(24), default="")
    submitted_at: Mapped[float] = mapped_column(Float, default=_ts)


class RegressionClaimRow(Base):
    """RUN-015 WP2：Regression Run 的数据库级唯一认领回执。

    主键即确定性幂等键 ``repair-regression:{proposal_id}:v{version}:attempt{n}``。
    认领行与 Run 在**同一事务**插入：并发请求只有一个能提交，落败事务整体回滚，
    因此不会产生无人认领的重复 Run（多进程部署不依赖 Python 锁）。
    """

    __tablename__ = "tg_regression_claim"
    claim_key: Mapped[str] = mapped_column(String(180), primary_key=True)
    proposal_id: Mapped[str] = mapped_column(String(64), index=True)
    task_id: Mapped[str] = mapped_column(String(64), index=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[float] = mapped_column(Float, default=_ts)


class StepRow(Base):
    __tablename__ = "tg_step"
    # step_id 在同一用例的每次 Run 中会重复，因此主键必须是 run 范围的
    id: Mapped[str] = mapped_column(String(140), primary_key=True)
    step_id: Mapped[str] = mapped_column(String(64), index=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    step_index: Mapped[int] = mapped_column(Integer, default=0)
    action: Mapped[str] = mapped_column(String(20), default="")
    status: Mapped[str] = mapped_column(String(24), default="PENDING")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    observation: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    __table_args__ = (UniqueConstraint("run_id", "step_id", name="uq_step_run_step"),)


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


class ProducerSequenceRow(Base):
    """Producer 实例内序号分配器（数据库原子自增）。

    用一行记录 + `ON CONFLICT DO UPDATE ... RETURNING` 分配序号：
    SQLite 与 PostgreSQL 都支持，多进程并发下不会重复，也不会因为互相挤占而失败。
    """

    __tablename__ = "tg_producer_sequence"
    producer_instance_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    last_sequence: Mapped[int] = mapped_column(BigInteger, default=0)
    updated_at: Mapped[str] = mapped_column(String(40), default=utc_now_iso)


class EvidenceRow(Base):
    __tablename__ = "tg_evidence"
    evidence_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(64), index=True)
    run_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(20), index=True)
    source_type: Mapped[str] = mapped_column(String(30), index=True)
    #: 原始来源 payload（脱敏前）的摘要，兼容历史字段名
    sha256: Mapped[str] = mapped_column(String(64))
    #: 实际持久化内容（脱敏后）的摘要：服务端完整性校验必须以它为准
    stored_content_sha256: Mapped[str] = mapped_column(String(64), default="")
    #: 附件的摘要（ArtifactStore 落盘内容的 SHA-256）
    attachment_sha256: Mapped[str] = mapped_column(String(64), default="")
    attachment_ref: Mapped[str | None] = mapped_column(String(200), nullable=True)
    source_trust: Mapped[str] = mapped_column(String(20), default="UNKNOWN")
    integrity_check: Mapped[str] = mapped_column(String(12), default="NOT_RUN")
    integrity_detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    redacted: Mapped[bool] = mapped_column(Boolean, default=False)
    doc: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    captured_at: Mapped[str] = mapped_column(String(40), default=utc_now_iso)


class InboxRow(Base):
    __tablename__ = "tg_consumer_inbox"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    consumer_id: Mapped[str] = mapped_column(String(80))
    event_id: Mapped[str] = mapped_column(String(64))
    # PROCESSING / SUCCEEDED / FAILED / UNKNOWN
    status: Mapped[str] = mapped_column(String(20), default="PROCESSING")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    claim_expires_at: Mapped[float] = mapped_column(Float, default=0.0)
    indeterminate: Mapped[bool] = mapped_column(Boolean, default=False)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    consumed_at: Mapped[str] = mapped_column(String(40), default=utc_now_iso)
    updated_at: Mapped[str] = mapped_column(String(40), default=utc_now_iso)
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


class ArtifactLifecycleLockRow(Base):
    """RUN-018 WP2：SQLite 侧的跨进程 artifact 生命周期锁表。

    SQLite 没有 advisory lock；锁协议通过「事务内首先写入本表」取得库级写锁
    （BEGIN IMMEDIATE 等价语义），事务提交/回滚自动释放。持有期间，
    其他进程的同协议操作（put / GC / 引用创建）在 busy_timeout 内等待。
    PostgreSQL 不使用本表（改用 pg_advisory_xact_lock，真正的 per-artifact 键）。
    """

    __tablename__ = "tg_artifact_lifecycle_lock"
    artifact_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    holder: Mapped[str] = mapped_column(String(160), default="")
    purpose: Mapped[str] = mapped_column(String(40), default="")
    locked_at: Mapped[float] = mapped_column(Float, default=_ts)


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


class PromotionReceiptRow(Base):
    """晋升 / 回滚的持久化幂等回执（RUN-007 WP1-02）。

    与"版本指针推进"写在同一事务内，因此只要版本指针推进成功，回执必然存在。
    进程在 CAS 提交后崩溃时，重试可以靠回执确定性恢复修复提案状态，
    不会因为"正式版本已经不是 base"而误判为 VERSION_CONFLICT。
    """

    __tablename__ = "tg_promotion_receipt"
    idempotency_key: Mapped[str] = mapped_column(String(160), primary_key=True)
    kind: Mapped[str] = mapped_column(String(20))  # PROMOTE / ROLLBACK
    proposal_id: Mapped[str] = mapped_column(String(90), index=True, default="")
    testcase_id: Mapped[str] = mapped_column(String(64), index=True)
    workspace_id: Mapped[str] = mapped_column(String(64), default="default")
    base_version: Mapped[int] = mapped_column(Integer, default=0)
    target_version: Mapped[int] = mapped_column(Integer, default=0)
    result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[str] = mapped_column(String(40), default=utc_now_iso)


class WorkerHeartbeatRow(Base):
    """RUN-018 WP8：Worker 心跳登记（Worker 数量 / 在线状态 / 当前执行 Run 的数据源）。

    Worker 每个轮询周期 upsert 一次；`last_heartbeat_at` 与阈值比较得出在线/离线。
    阈值是 provisional 配置，不是 SLA（见 traceguard_persistence.monitoring）。
    """

    __tablename__ = "tg_worker_heartbeat"
    worker_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    #: inline = API 内嵌 Worker；standalone = 独立进程 Worker
    mode: Mapped[str] = mapped_column(String(20), default="standalone")
    #: IDLE / RUNNING / STOPPED（STOPPED 为优雅退出时的最后状态）
    status: Mapped[str] = mapped_column(String(20), default="IDLE")
    hostname: Mapped[str] = mapped_column(String(120), default="")
    pid: Mapped[int] = mapped_column(Integer, default=0)
    current_run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    current_task_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_expires_at: Mapped[float] = mapped_column(Float, default=0.0)
    claims_total: Mapped[int] = mapped_column(Integer, default=0)
    takeovers_total: Mapped[int] = mapped_column(Integer, default=0)
    failures_total: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[float] = mapped_column(Float, default=_ts)
    last_heartbeat_at: Mapped[float] = mapped_column(Float, default=_ts)
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class AlertRow(Base):
    """RUN-018 WP8：运行告警记录（状态机 OPEN → ACKNOWLEDGED → RESOLVED）。

    同一 `fingerprint` 的未解决告警只保留一条（应用层去重）；条件消失后由告警引擎
    自动 RESOLVED，人工处理走 acknowledge / resolve API 并记录处理主体与时间。
    """

    __tablename__ = "tg_alert"
    alert_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    #: 去重指纹：`{alert_type}:{维度}`，如 WORKER_OFFLINE:{worker_id}
    fingerprint: Mapped[str] = mapped_column(String(160), index=True)
    alert_type: Mapped[str] = mapped_column(String(40), index=True)
    #: INFO / WARNING / CRITICAL
    severity: Mapped[str] = mapped_column(String(12), default="WARNING")
    #: OPEN / ACKNOWLEDGED / RESOLVED
    status: Mapped[str] = mapped_column(String(16), default="OPEN", index=True)
    title: Mapped[str] = mapped_column(String(200), default="")
    message: Mapped[str] = mapped_column(Text, default="")
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    #: 告警归属 Workspace；None = 系统级（全员可见）
    scope_workspace: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    ref_run_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    ref_worker_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    occurrences: Mapped[int] = mapped_column(Integer, default=1)
    first_seen: Mapped[float] = mapped_column(Float, default=_ts)
    last_seen: Mapped[float] = mapped_column(Float, default=_ts)
    #: 处理主体（ACK）与时间
    acknowledged_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    acknowledged_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    #: 处理主体（RESOLVE）、时间与解决原因
    resolved_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    resolved_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    resolution_reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[float] = mapped_column(Float, default=_ts)
    updated_at: Mapped[float] = mapped_column(Float, default=_ts)


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
Index("ix_tg_step_run_idx", StepRow.run_id, StepRow.step_index)
Index("ix_tg_alert_status_seen", AlertRow.status, AlertRow.last_seen)
