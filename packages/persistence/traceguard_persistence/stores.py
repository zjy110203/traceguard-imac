"""持久化版仓储：与内存实现语义一致，可在进程重启后恢复。

覆盖：Event（幂等/序号唯一/冲突审计）、Evidence（不可变/派生链）、Consumer Inbox、
通用聚合文档（Graph/Diagnosis/Probe/Disposition/Repair/Experiment）、产物存储。
"""

from __future__ import annotations

import hashlib
import json
import threading
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from traceguard_core.errors import AppError, ErrorCode
from traceguard_core.ids import new_id, utc_now_iso
from traceguard_core.logging import redact
from traceguard_evidence.event_store import StoredEvent, _hash_payload
from traceguard_evidence.evidence_manager import EvidenceRecord, _content_hash
from traceguard_evidence.inbox import InboxOutcome, InboxRecord

from .db import Database
from .models import (
    ArtifactRow,
    DocRow,
    EventConflictRow,
    EventRow,
    EvidenceRow,
    InboxRow,
)


# --------------------------------------------------------------------------- Event
class PersistentEventStore:
    """数据库版 Event Store。

    序号分配在数据库侧完成：使用行级 upsert（SQLite/PG 都支持 SELECT ... FOR UPDATE 语义差异下
    以唯一约束 + 重试保证正确性），保证多进程并发也不会重复。
    """

    def __init__(self, db: Database) -> None:
        self.db = db
        self._lock = threading.RLock()

    def new_producer_instance(self, producer_id: str) -> str:
        iid = f"{producer_id}-inst-{new_id('pi')}"
        return iid

    def append(
        self,
        *,
        event_type: str,
        server_workspace_id: str,
        producer_id: str,
        producer_instance_id: str,
        idempotency_key: str,
        payload: dict[str, Any] | None = None,
        context_refs: dict[str, str] | None = None,
        occurred_at: str | None = None,
        schema_version: str = "0.1.0",
    ) -> StoredEvent:
        """追加事件。

        并发语义（RUN-005 修复）：
        - 序号由数据库侧原子分配器（tg_producer_sequence）递增，多进程并发不会重复；
        - 幂等检查先于序号分配，正常的重复投递不会烧序号；
        - 仍用唯一约束兜底：极端竞争下重试，绝不把正常竞争误报成业务冲突；
        - 只有"同键异载荷"才报 CONFLICT 并审计。
        """
        payload = payload or {}
        clean_payload = {k: v for k, v in payload.items() if k != "workspace_id"}
        phash = _hash_payload(clean_payload)
        attempts = 5
        last_error: Exception | None = None

        def _seen_existing() -> StoredEvent | None:
            """幂等检查：命中则返回已有事件；同键异载荷则审计并抛 CONFLICT。"""
            with self.db.session() as s:
                existing = s.scalar(
                    select(EventRow).where(
                        EventRow.producer_id == producer_id,
                        EventRow.idempotency_key == idempotency_key,
                    )
                )
                if not existing:
                    return None
                if existing.payload_hash == phash:
                    return _to_stored(existing)
                self._record_conflict(s, producer_id, idempotency_key, existing.payload_hash, phash)
                s.commit()
            raise AppError(ErrorCode.CONFLICT, "IDEMPOTENCY_KEY_PAYLOAD_CONFLICT")

        for _ in range(attempts):
            hit = _seen_existing()
            if hit is not None:
                return hit
            seq = self._next_sequence(producer_instance_id)
            try:
                with self.db.session() as s:
                    row = EventRow(
                        event_id=new_id("evt"),
                        event_type=event_type,
                        schema_version=schema_version,
                        workspace_id=server_workspace_id,
                        context_refs=dict(context_refs or {}),
                        producer_id=producer_id,
                        producer_instance_id=producer_instance_id,
                        sequence=seq,
                        occurred_at=occurred_at or utc_now_iso(),
                        observed_at=utc_now_iso(),
                        idempotency_key=idempotency_key,
                        payload=clean_payload,
                        payload_hash=phash,
                        recorded_at=utc_now_iso(),
                    )
                    s.add(row)
                    s.flush()  # 唯一约束在这里暴露，便于重试
                    return _to_stored(row)
            except IntegrityError as exc:
                last_error = exc
                continue
        raise AppError(
            ErrorCode.CONFLICT, f"event append failed after {attempts} attempts"
        ) from last_error

    def _next_sequence(self, producer_instance_id: str) -> int:
        """原子分配实例内序号（SQLite / PostgreSQL 通用 upsert + RETURNING）。

        分配与事件插入不在同一事务：极端情况下（例如幂等命中）会烧掉一个序号，
        表现为合法跳号（不判丢失），但绝不会重复或倒退。
        """
        from sqlalchemy import text

        sql = text(
            "INSERT INTO tg_producer_sequence "
            "(producer_instance_id, last_sequence, updated_at) "
            "VALUES (:pid, 1, :ts) "
            "ON CONFLICT (producer_instance_id) DO UPDATE "
            "SET last_sequence = tg_producer_sequence.last_sequence + 1, updated_at = :ts "
            "RETURNING last_sequence"
        )
        with self.db.session() as s:
            value = s.execute(sql, {"pid": producer_instance_id, "ts": utc_now_iso()}).scalar_one()
            return int(value)

    def _record_conflict(
        self, s: Any, producer_id: str, idempotency_key: str, expected: str, got: str
    ) -> None:
        s.add(
            EventConflictRow(
                producer_id=producer_id,
                idempotency_key=idempotency_key,
                expected_hash=expected,
                got_hash=got,
                at=utc_now_iso(),
            )
        )

    def get(self, event_id: str) -> StoredEvent | None:
        with self.db.session() as s:
            row = s.get(EventRow, event_id)
            return _to_stored(row) if row else None

    def list_events(
        self,
        workspace_id: str | None = None,
        *,
        run_id: str | None = None,
        event_type: str | None = None,
        limit: int = 500,
        offset: int = 0,
    ) -> list[StoredEvent]:
        with self.db.session() as s:
            q = select(EventRow)
            if workspace_id:
                q = q.where(EventRow.workspace_id == workspace_id)
            if event_type:
                q = q.where(EventRow.event_type == event_type)
            rows = list(s.scalars(q.order_by(EventRow.recorded_at.desc()).limit(2000)))
            out = [_to_stored(r) for r in rows]
        if run_id:
            out = [e for e in out if e.context_refs.get("run") == run_id]
        return out[offset : offset + limit]

    def detect_gaps(self, producer_instance_id: str) -> list[int]:
        with self.db.session() as s:
            seqs = sorted(
                s.scalars(
                    select(EventRow.sequence).where(
                        EventRow.producer_instance_id == producer_instance_id
                    )
                ).all()
            )
        gaps: list[int] = []
        for a, b in zip(seqs, seqs[1:], strict=False):
            gaps.extend(range(a + 1, b))
        return gaps

    def list_events_after(
        self,
        *,
        after_at: str = "",
        after_event_id: str = "",
        workspace_id: str | None = None,
        run_id: str | None = None,
        event_type: str | None = None,
        limit: int = 200,
    ) -> list[StoredEvent]:
        """按存储追加顺序（recorded_at, event_id）取游标之后的事件，用于 SSE 续传。

        这是"追加顺序"，不是跨 producer 的全局业务顺序；同一 producer 实例内的
        序号语义仍由 sequence 表达。
        """
        from sqlalchemy import and_, or_

        with self.db.session() as s:
            q = select(EventRow)
            if after_at:
                q = q.where(
                    or_(
                        EventRow.recorded_at > after_at,
                        and_(
                            EventRow.recorded_at == after_at,
                            EventRow.event_id > after_event_id,
                        ),
                    )
                )
            if workspace_id:
                q = q.where(EventRow.workspace_id == workspace_id)
            if event_type:
                q = q.where(EventRow.event_type == event_type)
            # run 过滤在 JSON 列上跨方言不一致，统一在应用层过滤（同一 workspace 内数据量可控）
            scan = max(limit * 10, 500) if run_id else limit
            rows = list(
                s.scalars(
                    q.order_by(EventRow.recorded_at.asc(), EventRow.event_id.asc()).limit(scan)
                )
            )
        out = [_to_stored(r) for r in rows]
        if run_id:
            out = [e for e in out if e.context_refs.get("run") == run_id]
        return out[:limit]

    def latest_event(self, workspace_id: str | None = None) -> StoredEvent | None:
        with self.db.session() as s:
            q = select(EventRow)
            if workspace_id:
                q = q.where(EventRow.workspace_id == workspace_id)
            row = s.scalars(
                q.order_by(EventRow.recorded_at.desc(), EventRow.event_id.desc()).limit(1)
            ).first()
            return _to_stored(row) if row else None

    def conflicts(self) -> list[dict[str, Any]]:
        with self.db.session() as s:
            rows = s.scalars(select(EventConflictRow).order_by(EventConflictRow.id)).all()
            return [
                {
                    "producer_id": r.producer_id,
                    "idempotency_key": r.idempotency_key,
                    "expected_hash": r.expected_hash,
                    "got_hash": r.got_hash,
                    "at": r.at,
                }
                for r in rows
            ]

    def count_all(self, workspace_id: str | None = None) -> int:
        from sqlalchemy import func

        with self.db.session() as s:
            q = select(func.count()).select_from(EventRow)
            if workspace_id:
                q = q.where(EventRow.workspace_id == workspace_id)
            return int(s.scalar(q) or 0)

    def stats(self, workspace_id: str | None = None) -> dict[str, Any]:
        events = self.list_events(workspace_id, limit=100000)
        by_type: dict[str, int] = {}
        for e in events:
            by_type[e.event_type] = by_type.get(e.event_type, 0) + 1
        return {"total": len(events), "by_type": by_type, "conflicts": len(self.conflicts())}


def _to_stored(row: EventRow) -> StoredEvent:
    return StoredEvent(
        event_id=row.event_id,
        event_type=row.event_type,
        schema_version=row.schema_version,
        workspace_id=row.workspace_id,
        context_refs=dict(row.context_refs or {}),
        producer_id=row.producer_id,
        producer_instance_id=row.producer_instance_id,
        sequence=row.sequence,
        occurred_at=row.occurred_at,
        observed_at=row.observed_at,
        idempotency_key=row.idempotency_key,
        payload=dict(row.payload or {}),
        payload_hash=row.payload_hash,
        recorded_at=row.recorded_at,
    )


# --------------------------------------------------------------------------- Evidence
class PersistentEvidenceManager:
    def __init__(self, db: Database) -> None:
        self.db = db
        self._lock = threading.RLock()

    def add_raw(
        self,
        evidence_id: str,
        source_type: str,
        source_ref: str,
        workspace_id: str,
        content: dict[str, Any],
        *,
        source_trust: str = "UNKNOWN",
        do_redact: bool = True,
        captured_at: str = "",
        run_id: str | None = None,
        attachment_ref: str | None = None,
    ) -> EvidenceRecord:
        raw_hash = _content_hash(content)
        stored_content = redact(content) if do_redact else content
        rec = EvidenceRecord(
            evidence_id=evidence_id,
            kind="raw",
            source_type=source_type,
            source_ref=source_ref,
            sha256=raw_hash,
            workspace_id=workspace_id,
            content=stored_content,
            source_trust=source_trust,
            redacted=do_redact,
            captured_at=captured_at or utc_now_iso(),
            attachment_ref=attachment_ref,
        )
        self._insert(rec, run_id)
        return rec

    def add_derived(
        self,
        evidence_id: str,
        raw_ref: str,
        workspace_id: str,
        content: dict[str, Any],
        *,
        derivation_rule: str,
        model_version: str | None = None,
        source_trust: str = "UNKNOWN",
    ) -> EvidenceRecord:
        raw = self.get(raw_ref)
        if not raw:
            raise AppError(ErrorCode.NOT_FOUND, "raw evidence not found")
        if raw.kind != "raw":
            raise AppError(ErrorCode.PRECONDITION_FAILED, "derivation source must be raw evidence")
        rec = EvidenceRecord(
            evidence_id=evidence_id,
            kind="derived",
            source_type="agent",
            source_ref=raw_ref,
            sha256=_content_hash(content),
            workspace_id=workspace_id,
            content=content,
            raw_ref=raw_ref,
            derivation_rule=derivation_rule,
            model_version=model_version,
            source_trust=source_trust,
        )
        self._insert(rec, raw.run_id if hasattr(raw, "run_id") else None)
        return rec

    def _insert(self, rec: EvidenceRecord, run_id: str | None) -> None:
        with self._lock, self.db.session() as s:
            if s.get(EvidenceRow, rec.evidence_id):
                raise AppError(ErrorCode.CONFLICT, "evidence_id exists (immutable)")
            s.add(
                EvidenceRow(
                    evidence_id=rec.evidence_id,
                    workspace_id=rec.workspace_id,
                    run_id=run_id,
                    kind=rec.kind,
                    source_type=rec.source_type,
                    sha256=rec.sha256,
                    attachment_ref=rec.attachment_ref,
                    source_trust=rec.source_trust,
                    integrity_check=rec.integrity_check,
                    redacted=rec.redacted,
                    doc=rec.to_dict(),
                    captured_at=rec.captured_at or utc_now_iso(),
                )
            )

    def verify_integrity(self, evidence_id: str, original_content: dict[str, Any]) -> bool:
        rec = self.get(evidence_id)
        if not rec:
            return False
        ok = rec.sha256 == _content_hash(original_content)
        with self.db.session() as s:
            row = s.get(EvidenceRow, evidence_id)
            if row:
                row.integrity_check = "PASS" if ok else "FAIL"
                doc = dict(row.doc or {})
                doc["integrity_check"] = row.integrity_check
                row.doc = doc
        return ok

    def get(self, evidence_id: str) -> EvidenceRecord | None:
        with self.db.session() as s:
            row = s.get(EvidenceRow, evidence_id)
            if not row:
                return None
            rec = _to_evidence(row)
            rec.run_id = row.run_id
            return rec

    def list_by_workspace(self, workspace_id: str) -> list[EvidenceRecord]:
        return self.list(workspace_id=workspace_id)

    def list(
        self,
        *,
        workspace_id: str | None = None,
        run_id: str | None = None,
        kind: str | None = None,
        source_type: str | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> list[EvidenceRecord]:
        with self.db.session() as s:
            q = select(EvidenceRow)
            if workspace_id:
                q = q.where(EvidenceRow.workspace_id == workspace_id)
            if run_id:
                q = q.where(EvidenceRow.run_id == run_id)
            if kind:
                q = q.where(EvidenceRow.kind == kind)
            if source_type:
                q = q.where(EvidenceRow.source_type == source_type)
            rows = s.scalars(
                q.order_by(EvidenceRow.captured_at.desc()).offset(offset).limit(limit)
            ).all()
            out = []
            for r in rows:
                rec = _to_evidence(r)
                rec.run_id = r.run_id
                out.append(rec)
            return out

    def count(self, **kw: Any) -> int:
        return len(self.list(limit=100000, **kw))

    def count_all(self, workspace_id: str | None = None) -> int:
        from sqlalchemy import func

        with self.db.session() as s:
            q = select(func.count()).select_from(EvidenceRow)
            if workspace_id:
                q = q.where(EvidenceRow.workspace_id == workspace_id)
            return int(s.scalar(q) or 0)


def _to_evidence(row: EvidenceRow) -> EvidenceRecord:
    doc = dict(row.doc or {})
    return EvidenceRecord(
        evidence_id=row.evidence_id,
        kind=row.kind,
        source_type=row.source_type,
        source_ref=doc.get("source_ref", ""),
        sha256=row.sha256,
        workspace_id=row.workspace_id,
        content=dict(doc.get("content", {})),
        raw_ref=doc.get("raw_ref"),
        derivation_rule=doc.get("derivation_rule"),
        model_version=doc.get("model_version"),
        attachment_ref=row.attachment_ref,
        integrity_check=row.integrity_check,
        source_trust=row.source_trust,
        redacted=bool(row.redacted),
        captured_at=row.captured_at,
    )


# --------------------------------------------------------------------------- Inbox
class PersistentConsumerInbox:
    """持久化 Consumer Inbox：与内存版同一状态机，支持多进程抢占与崩溃恢复。

    语义（RUN-005 补齐）：
    - `(consumer_id, event_id)` 唯一，成功消费不可重复执行；
    - 抢占使用"唯一约束 + 租约"实现，不依赖单进程内存锁；
    - PROCESSING 租约过期可被其他实例接管（崩溃恢复），attempts 递增；
    - FAILED（确定失败）允许重试；UNKNOWN（外部副作用不确定）不得盲目重试；
    - `consume_in_transaction()` 把本地副作用与消费记录放在同一事务提交。
    """

    def __init__(self, db: Database, *, clock: Any = None) -> None:
        self.db = db
        import time as _time

        self._clock = clock or _time.time

    # ------------------------------------------------------------------ helpers
    def _to_record(self, row: InboxRow) -> InboxRecord:
        return InboxRecord(
            consumer_id=row.consumer_id,
            event_id=row.event_id,
            status=row.status,
            attempts=int(row.attempts or 0),
            result=row.result if row.result is not None else {},
            error=row.last_error or "",
            indeterminate=bool(row.indeterminate),
            claim_expires_at=float(row.claim_expires_at or 0.0),
            consumed_at=row.consumed_at or "",
            updated_at=row.updated_at or "",
        )

    def _get_row(self, s: Any, consumer_id: str, event_id: str) -> InboxRow | None:
        return s.scalar(
            select(InboxRow).where(
                InboxRow.consumer_id == consumer_id, InboxRow.event_id == event_id
            )
        )

    # -------------------------------------------------------------------- claim
    def claim(
        self, consumer_id: str, event_id: str, *, lease_seconds: float = 60.0
    ) -> tuple[InboxRecord, bool]:
        now = self._clock()
        with self.db.session() as s:
            row = self._get_row(s, consumer_id, event_id)
            if row is None:
                row = InboxRow(
                    consumer_id=consumer_id,
                    event_id=event_id,
                    status="PROCESSING",
                    attempts=1,
                    claim_expires_at=now + lease_seconds,
                    result={},
                    consumed_at=utc_now_iso(),
                    updated_at=utc_now_iso(),
                )
                s.add(row)
                try:
                    s.flush()
                except IntegrityError:
                    # 另一实例抢到了同一 (consumer_id, event_id)
                    s.rollback()
                    existing = self._get_row(s, consumer_id, event_id)
                    if existing is None:  # 极端竞态：再读一次
                        raise
                    return self._to_record(existing), False
                return self._to_record(row), True

            if row.status == "SUCCEEDED" or row.status == "UNKNOWN":
                return self._to_record(row), False
            if row.status == "FAILED" and row.indeterminate:
                return self._to_record(row), False
            if row.status == "PROCESSING" and float(row.claim_expires_at or 0) > now:
                return self._to_record(row), False
            # FAILED（确定失败可重试）或 PROCESSING 租约过期 → 接管
            row.status = "PROCESSING"
            row.attempts = int(row.attempts or 0) + 1
            row.claim_expires_at = now + lease_seconds
            row.updated_at = utc_now_iso()
            s.flush()
            return self._to_record(row), True

    # ----------------------------------------------------------------- terminal
    def complete(self, consumer_id: str, event_id: str, result: Any = None) -> InboxRecord:
        with self.db.session() as s:
            row = self._get_row(s, consumer_id, event_id)
            if row is None:
                raise AppError(ErrorCode.NOT_FOUND, "inbox record not found")
            if row.status != "SUCCEEDED":
                row.status = "SUCCEEDED"
                row.result = result if isinstance(result, dict) else {"value": result}
                row.last_error = None
                row.claim_expires_at = 0.0
                row.updated_at = utc_now_iso()
                s.flush()
            return self._to_record(row)

    def fail(
        self,
        consumer_id: str,
        event_id: str,
        error: str,
        *,
        indeterminate: bool = False,
    ) -> InboxRecord:
        with self.db.session() as s:
            row = self._get_row(s, consumer_id, event_id)
            if row is None:
                raise AppError(ErrorCode.NOT_FOUND, "inbox record not found")
            row.status = "UNKNOWN" if indeterminate else "FAILED"
            row.indeterminate = bool(indeterminate)
            row.last_error = error[:2000]
            row.result = {"error": error[:2000], "indeterminate": bool(indeterminate)}
            row.claim_expires_at = 0.0
            row.updated_at = utc_now_iso()
            s.flush()
            return self._to_record(row)

    # -------------------------------------------------------------------- run
    def run(
        self,
        consumer_id: str,
        event_id: str,
        handler: Any,
        *,
        external_side_effect: bool = False,
        lease_seconds: float = 60.0,
    ) -> InboxOutcome:
        rec, claimed = self.claim(consumer_id, event_id, lease_seconds=lease_seconds)
        if not claimed:
            reason = (
                "ALREADY_SUCCEEDED"
                if rec.status == "SUCCEEDED"
                else ("INDETERMINATE_SIDE_EFFECT" if rec.status == "UNKNOWN" else "IN_FLIGHT")
            )
            return InboxOutcome(record=rec, executed=False, skipped_reason=reason)
        try:
            value = handler()
        except Exception as exc:  # noqa: BLE001
            rec = self.fail(
                consumer_id,
                event_id,
                f"{type(exc).__name__}: {exc}",
                indeterminate=external_side_effect,
            )
            return InboxOutcome(record=rec, executed=True, ok=False)
        rec = self.complete(consumer_id, event_id, result=value)
        return InboxOutcome(record=rec, executed=True, ok=True, value=value)

    def consume_in_transaction(
        self,
        consumer_id: str,
        event_id: str,
        effect: Any,  # Callable[[Session], dict | None]
        *,
        external_side_effect: bool = False,
        lease_seconds: float = 60.0,
    ) -> InboxOutcome:
        """本地副作用与消费记录在同一事务提交：要么都成功，要么都不生效。"""
        rec, claimed = self.claim(consumer_id, event_id, lease_seconds=lease_seconds)
        if not claimed:
            reason = (
                "ALREADY_SUCCEEDED"
                if rec.status == "SUCCEEDED"
                else ("INDETERMINATE_SIDE_EFFECT" if rec.status == "UNKNOWN" else "IN_FLIGHT")
            )
            return InboxOutcome(record=rec, executed=False, skipped_reason=reason)
        try:
            with self.db.session() as s:
                value = effect(s)
                row = self._get_row(s, consumer_id, event_id)
                if row is None:
                    raise AppError(ErrorCode.NOT_FOUND, "inbox record disappeared")
                row.status = "SUCCEEDED"
                row.result = value if isinstance(value, dict) else {"value": value}
                row.last_error = None
                row.claim_expires_at = 0.0
                row.updated_at = utc_now_iso()
                s.flush()
        except Exception as exc:  # noqa: BLE001
            # 事务已回滚：副作用未生效，消费记录回到可重试/不确定状态
            rec = self.fail(
                consumer_id,
                event_id,
                f"{type(exc).__name__}: {exc}",
                indeterminate=external_side_effect,
            )
            return InboxOutcome(record=rec, executed=True, ok=False)
        return InboxOutcome(
            record=self.get(consumer_id, event_id) or rec, executed=True, ok=True, value=value
        )

    # 兼容旧接口
    def consume(
        self, consumer_id: str, event_id: str, result: dict[str, Any] | None = None
    ) -> InboxRecord:
        return self.consume_in_transaction(consumer_id, event_id, lambda s: result or {}).record

    def get(self, consumer_id: str, event_id: str) -> InboxRecord | None:
        with self.db.session() as s:
            row = self._get_row(s, consumer_id, event_id)
            return self._to_record(row) if row else None

    def list_for_event(self, event_id: str) -> list[InboxRecord]:
        with self.db.session() as s:
            rows = s.scalars(select(InboxRow).where(InboxRow.event_id == event_id)).all()
            return [self._to_record(r) for r in rows]

    def stats(self) -> dict[str, int]:
        from sqlalchemy import func

        with self.db.session() as s:
            rows = s.execute(select(InboxRow.status, func.count()).group_by(InboxRow.status)).all()
            return {str(status): int(count) for status, count in rows}


# --------------------------------------------------------------------------- Doc store
@dataclass
class DocRecord:
    id: str
    kind: str
    workspace_id: str
    ref_id: str | None
    version: int
    status: str
    doc: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "workspace_id": self.workspace_id,
            "ref_id": self.ref_id,
            "version": self.version,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            **self.doc,
        }


class DocStore:
    """通用聚合文档仓储（Graph/Diagnosis/Probe/Repair/Experiment…）。"""

    def __init__(self, db: Database) -> None:
        self.db = db

    def put(
        self,
        *,
        doc_id: str,
        kind: str,
        doc: dict[str, Any],
        workspace_id: str = "default",
        ref_id: str | None = None,
        status: str = "",
        version: int | None = None,
    ) -> DocRecord:
        """写入/更新聚合文档。

        RUN-007 WP1-04 修复：`version` 的 CAS 必须是**原子条件 UPDATE**。
        旧实现是"先 SELECT 到 ORM 对象、在 Python 里比较版本、再无条件 UPDATE"，
        在真正多进程并发下会 lost update（两个进程都读到 v5，都写成功），
        ProbeBudget / 幂等回执都依赖这里，所以必须修在存储层。

        语义：
        - `version=None`：无条件写（普通文档更新）；
        - `version=N` 且行存在：只有当前版本仍是 N 才写成功，否则抛 CONFLICT；
        - `version=N` 且行不存在：插入（并发插入由主键冲突转成 CONFLICT）。
        """
        now = utc_now_iso()
        with self.db.session() as s:
            if version is not None:
                result = s.execute(
                    update(DocRow)
                    .where(DocRow.id == doc_id, DocRow.version == version)
                    .values(
                        doc=doc,
                        version=DocRow.version + 1,
                        status=status or DocRow.status,
                        ref_id=ref_id if ref_id is not None else DocRow.ref_id,
                        updated_at=now,
                    )
                )
                if result.rowcount == 1:
                    row = s.get(DocRow, doc_id)
                    assert row is not None
                    return _to_doc(row)
                if s.get(DocRow, doc_id) is not None:
                    actual = s.scalar(select(DocRow.version).where(DocRow.id == doc_id))
                    raise AppError(
                        ErrorCode.CONFLICT,
                        f"doc version CAS failed: expected {version}, actual {actual}",
                    )
                try:
                    with s.begin_nested():
                        s.add(
                            DocRow(
                                id=doc_id,
                                kind=kind,
                                workspace_id=workspace_id,
                                ref_id=ref_id,
                                version=version or 1,
                                status=status,
                                doc=doc,
                                created_at=now,
                                updated_at=now,
                            )
                        )
                except IntegrityError as exc:
                    raise AppError(
                        ErrorCode.CONFLICT,
                        f"doc create conflict: {doc_id} 已被并发创建",
                    ) from exc
                s.flush()
                row = s.get(DocRow, doc_id)
                assert row is not None
                return _to_doc(row)

            row = s.get(DocRow, doc_id)
            if row is None:
                row = DocRow(
                    id=doc_id,
                    kind=kind,
                    workspace_id=workspace_id,
                    ref_id=ref_id,
                    version=1,
                    status=status,
                    doc=doc,
                    created_at=now,
                    updated_at=now,
                )
                s.add(row)
            else:
                row.doc = doc
                row.status = status or row.status
                row.ref_id = ref_id or row.ref_id
                row.updated_at = now
            s.flush()
            return _to_doc(row)

    def get(self, doc_id: str) -> DocRecord | None:
        with self.db.session() as s:
            row = s.get(DocRow, doc_id)
            return _to_doc(row) if row else None

    def list(
        self,
        *,
        kind: str | None = None,
        workspace_id: str | None = None,
        ref_id: str | None = None,
        status: str | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> list[DocRecord]:
        with self.db.session() as s:
            q = select(DocRow)
            if kind:
                q = q.where(DocRow.kind == kind)
            if workspace_id:
                q = q.where(DocRow.workspace_id == workspace_id)
            if ref_id:
                q = q.where(DocRow.ref_id == ref_id)
            if status:
                q = q.where(DocRow.status == status)
            rows = s.scalars(q.order_by(DocRow.created_at.desc()).offset(offset).limit(limit)).all()
            return [_to_doc(r) for r in rows]

    def delete(self, doc_id: str) -> bool:
        with self.db.session() as s:
            row = s.get(DocRow, doc_id)
            if not row:
                return False
            s.delete(row)
            return True


def _to_doc(row: DocRow) -> DocRecord:
    return DocRecord(
        id=row.id,
        kind=row.kind,
        workspace_id=row.workspace_id,
        ref_id=row.ref_id,
        version=row.version,
        status=row.status,
        doc=dict(row.doc or {}),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


# --------------------------------------------------------------------------- Artifacts
class ArtifactStore:
    """内容寻址产物存储。

    默认本地文件系统（`.traceguard/artifacts/<sha256前2位>/<sha256>`），
    预留 S3/MinIO 后端：配置 TG_ARTIFACT_BACKEND=s3 时使用 boto3（未安装则明确报错）。
    """

    def __init__(
        self,
        db: Database,
        root: str = ".traceguard/artifacts",
        *,
        backend: str = "local",
        bucket: str = "traceguard",
        endpoint: str | None = None,
    ) -> None:
        self.db = db
        self.root = Path(root)
        self.backend = backend
        self.bucket = bucket
        self.endpoint = endpoint
        if backend == "local":
            self.root.mkdir(parents=True, exist_ok=True)

    def put(
        self,
        data: bytes,
        *,
        workspace_id: str,
        media_type: str = "application/octet-stream",
        original_name: str = "",
    ) -> dict[str, Any]:
        digest = hashlib.sha256(data).hexdigest()
        artifact_id = f"art_{digest[:20]}"
        path = self._local_path(digest)
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_bytes(data)
        with self.db.session() as s:
            row = s.get(ArtifactRow, artifact_id)
            if row is None:
                s.add(
                    ArtifactRow(
                        artifact_id=artifact_id,
                        workspace_id=workspace_id,
                        sha256=digest,
                        size=len(data),
                        media_type=media_type,
                        path=str(path),
                        backend="local",
                        original_name=original_name,
                        created_at=utc_now_iso(),
                    )
                )
        return {
            "artifact_id": artifact_id,
            "sha256": digest,
            "size": len(data),
            "media_type": media_type,
            "path": f"artifacts/{digest[:2]}/{digest}",
            "original_name": original_name,
        }

    def _local_path(self, digest: str) -> Path:
        return self.root / digest[:2] / digest

    def read(self, artifact_id: str) -> tuple[bytes, dict[str, Any]]:
        with self.db.session() as s:
            row = s.get(ArtifactRow, artifact_id)
            if not row:
                raise AppError(ErrorCode.NOT_FOUND, "artifact not found")
            meta = {
                "artifact_id": row.artifact_id,
                "sha256": row.sha256,
                "size": row.size,
                "media_type": row.media_type,
                "original_name": row.original_name,
            }
            data = Path(row.path).read_bytes()
        ok = hashlib.sha256(data).hexdigest() == meta["sha256"]
        if not ok:
            raise AppError(ErrorCode.PRECONDITION_FAILED, "artifact integrity check failed")
        return data, meta

    def get_meta(self, artifact_id: str) -> dict[str, Any] | None:
        with self.db.session() as s:
            row = s.get(ArtifactRow, artifact_id)
            if not row:
                return None
            return {
                "artifact_id": row.artifact_id,
                "sha256": row.sha256,
                "size": row.size,
                "media_type": row.media_type,
                "original_name": row.original_name,
                "workspace_id": row.workspace_id,
            }

    def list(self, workspace_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        with self.db.session() as s:
            q = select(ArtifactRow)
            if workspace_id:
                q = q.where(ArtifactRow.workspace_id == workspace_id)
            rows = s.scalars(q.order_by(ArtifactRow.created_at.desc()).limit(limit)).all()
            return [
                {
                    "artifact_id": r.artifact_id,
                    "sha256": r.sha256,
                    "size": r.size,
                    "media_type": r.media_type,
                    "original_name": r.original_name,
                    "created_at": r.created_at,
                }
                for r in rows
            ]


def save_json_artifact(store: ArtifactStore, obj: Any, workspace_id: str, name: str) -> dict:
    data = json.dumps(obj, ensure_ascii=False, indent=2).encode()
    return store.put(
        data, workspace_id=workspace_id, media_type="application/json", original_name=name
    )


def save_text_artifact(store: ArtifactStore, text: str, workspace_id: str, name: str) -> dict:
    return store.put(
        text.encode(), workspace_id=workspace_id, media_type="text/html", original_name=name
    )


def iter_docs(db: Database, kind: str, workspace_id: str | None = None) -> Iterable[dict]:
    store = DocStore(db)
    for rec in store.list(kind=kind, workspace_id=workspace_id, limit=10000):
        yield rec.to_dict()


def ensure_dir(path: str | Path) -> Path:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def temp_name(prefix: str = "tmp") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


__all__ = [
    "ArtifactStore",
    "DocRecord",
    "DocStore",
    "PersistentConsumerInbox",
    "PersistentEventStore",
    "PersistentEvidenceManager",
    "ensure_dir",
    "iter_docs",
    "save_json_artifact",
    "save_text_artifact",
    "temp_name",
]
