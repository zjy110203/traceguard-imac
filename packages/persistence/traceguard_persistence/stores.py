"""持久化版仓储：与内存实现语义一致，可在进程重启后恢复。

覆盖：Event（幂等/序号唯一/冲突审计）、Evidence（不可变/派生链）、Consumer Inbox、
通用聚合文档（Graph/Diagnosis/Probe/Disposition/Repair/Experiment）、产物存储。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import socket
import threading
import time
import uuid
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import String, cast, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from traceguard_core.errors import AppError, ErrorCode
from traceguard_core.ids import new_id, utc_now_iso
from traceguard_core.logging import redact
from traceguard_evidence.event_store import StoredEvent, _hash_payload
from traceguard_evidence.evidence_manager import EvidenceRecord, _content_hash
from traceguard_evidence.inbox import InboxOutcome, InboxRecord

from .db import Database
from .fencing import fenced_guard
from .models import (
    ArtifactRow,
    DocRow,
    EventConflictRow,
    EventRow,
    EvidenceRow,
    InboxRow,
    RunRow,
)

#: Evidence `doc` JSON 中可能出现的产物引用（artifact_id = art_ + sha256[:20]）
_ARTIFACT_REF_RE = re.compile(r"art_[0-9a-f]{20}")


# ------------------------------------------------------- RUN-018 WP2 生命周期锁
def _artifact_lock_key(artifact_id: str) -> int:
    """PostgreSQL advisory lock 的 per-artifact 键（63-bit 正整数，避免与其他模块冲突）。"""
    h = hashlib.sha256(f"tg-artifact-lifecycle:{artifact_id}".encode()).hexdigest()
    return int(h[:15], 16) & 0x7FFF_FFFF_FFFF_FFFF


@contextmanager
def artifact_lifecycle_lock(
    db: Database, artifact_id: str, *, purpose: str = ""
) -> Iterator[Session]:
    """统一 Artifact Lifecycle Lock（RUN-018 WP2）：跨 OS 进程的 per-artifact 生命周期锁。

    锁与业务工作共用**同一事务**：锁必须覆盖真正的临界区（复核 + 文件移动 /
    复核 + 行删除 / 写文件 + 登记），而不是只保护一次 SELECT。
    事务提交或回滚时锁自动释放——不存在"忘记解锁"或"持锁进程崩溃后锁泄漏"
    （数据库连接断开即释放）。

    - PostgreSQL 16：`pg_advisory_xact_lock(per-artifact 键)`，真正的 per-artifact
      并发（不同产物互不阻塞）；
    - SQLite：事务内首先写入 `tg_artifact_lifecycle_lock` 锁表取得库级写锁
      （与 BEGIN IMMEDIATE 等价），全库串行——SQLite 单写者的天然约束下，
      全局串行即为正确的 per-artifact 语义；等待由 busy_timeout=30s 承担。

    仅使用 Python threading.Lock 不合格（不跨进程）；本协议保证跨 OS 进程有效。
    """
    with db.session() as s:
        if db.engine.dialect.name == "postgresql":
            s.execute(
                text("SELECT pg_advisory_xact_lock(:k)"), {"k": _artifact_lock_key(artifact_id)}
            )
        else:
            s.execute(
                text(
                    "INSERT OR REPLACE INTO tg_artifact_lifecycle_lock "
                    "(artifact_id, holder, purpose, locked_at) "
                    "VALUES (:aid, :holder, :purpose, :ts)"
                ),
                {
                    "aid": artifact_id[:80],
                    "holder": f"{socket.gethostname()}:{os.getpid()}",
                    "purpose": purpose[:40],
                    "ts": time.time(),
                },
            )
        yield s
        # db.session() 上下文退出时 commit（或异常时 rollback）→ 锁释放


def _acquire_extra_ref_locks(s: Session, db: Database, refs: Sequence[str]) -> None:
    """在同一事务内为附加的 art_* 引用补充锁（已排序调用，避免锁序环路）。

    PostgreSQL：逐条执行 advisory xact lock（每条引用一把锁，直到事务结束）。
    SQLite：本事务已通过锁表写入持有库级写锁，全库串行，无需逐条加锁。
    """
    if db.engine.dialect.name == "postgresql":
        for ref in refs:
            s.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _artifact_lock_key(ref)})


def verify_artifact_refs_exist(s: Session, refs: Iterable[str]) -> None:
    """引用创建的最终守卫：取得锁后重新检查 Artifact 登记是否仍然存在。

    任何引用创建方（Evidence / Run 报告）在取得生命周期锁之后调用本函数；
    产物已被 GC 回收时**拒绝引用创建**，绝不产生 dangling reference。
    """
    for ref in refs:
        if s.get(ArtifactRow, ref) is None:
            raise AppError(
                ErrorCode.CONFLICT,
                f"ARTIFACT_COLLECTED: 产物 {ref} 已被 GC 回收，引用创建被拒绝",
            )


def lock_and_verify_report_refs(db: Database, s: Session, report: Any) -> None:
    """Run 报告写入前的引用守卫（RUN-018 WP2 场景 B：Run.report_json 同样遵守锁协议）。

    在提交报告的**同一事务**内：对报告中每条 art_* 引用取得生命周期锁
    （按排序获取，避免与 GC 的单锁请求形成锁序环路），然后验证登记仍然存在；
    已回收 → 拒绝报告提交（CONFLICT），不产生悬空引用。
    """
    if not isinstance(report, dict) or not report:
        return
    refs = sorted(set(_ARTIFACT_REF_RE.findall(json.dumps(report, ensure_ascii=False))))
    if not refs:
        return
    _acquire_extra_ref_locks(s, db, refs)
    verify_artifact_refs_exist(s, refs)


def _iso_epoch(value: str | None) -> float:
    """ISO 时间戳 → epoch；解析失败按 0 处理（视为很旧，交由上层保留期判定）。"""
    if not value:
        return 0.0
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


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
        fence: tuple[str, int] | None = None,
        allow_own_submission: bool = False,
    ) -> StoredEvent:
        """追加事件。

        并发语义（RUN-005 修复）：
        - 序号由数据库侧原子分配器（tg_producer_sequence）递增，多进程并发不会重复；
        - 幂等检查先于序号分配，正常的重复投递不会烧序号；
        - 仍用唯一约束兜底：极端竞争下重试，绝不把正常竞争误报成业务冲突；
        - 只有"同键异载荷"才报 CONFLICT 并审计。

        RUN-014 WP2：传 `fence=(worker_id, fencing_token)` 时，事件写入与租约守卫
        在同一事务内；旧 Worker 被接管后无法再写入任何事件。
        `allow_own_submission=True` 仅用于提交终态之后的 `run.completed` 收尾事件。
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
                    if fence is not None:
                        if not fenced_guard(
                            s,
                            run_id=str(context_refs.get("run") or "") if context_refs else "",
                            worker_id=str(fence[0]),
                            fencing_token=int(fence[1]),
                            allow_own_submission=allow_own_submission,
                        ):
                            raise AppError(
                                ErrorCode.CONFLICT,
                                "LEASE_LOST: 租约已失效，事件写入被拒绝（fencing guard）",
                            )
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
        workspace_ids: Sequence[str] | None = None,
    ) -> list[StoredEvent]:
        with self.db.session() as s:
            q = select(EventRow)
            if workspace_id:
                q = q.where(EventRow.workspace_id == workspace_id)
            if workspace_ids is not None:
                q = q.where(EventRow.workspace_id.in_(list(workspace_ids)))
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
        workspace_ids: Sequence[str] | None = None,
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
            if workspace_ids is not None:
                # SSE 的授权范围（多 Workspace 并集）：下推到 SQL，避免“先取全库再过滤”
                # 造成游标饥饿或越权数据进入内存。
                q = q.where(EventRow.workspace_id.in_(list(workspace_ids)))
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
        attachment_sha256: str = "",
        fence: tuple[str, int] | None = None,
    ) -> EvidenceRecord:
        """采集原始证据：同时记录"原始来源摘要"和"持久化内容摘要"。

        `fence=(worker_id, fencing_token)`：证据写入与租约守卫在同一事务内，
        旧 Worker 恢复后无法向已完成的 Run 追加证据（RUN-014 WP2）。
        """
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
            stored_content_sha256=_content_hash(stored_content),
            attachment_sha256=attachment_sha256,
            source_trust=source_trust,
            redacted=do_redact,
            captured_at=captured_at or utc_now_iso(),
            attachment_ref=attachment_ref,
        )
        self._insert(rec, run_id, fence=fence)
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
            stored_content_sha256=_content_hash(content),
            raw_ref=raw_ref,
            derivation_rule=derivation_rule,
            model_version=model_version,
            source_trust=source_trust,
        )
        self._insert(rec, raw.run_id if hasattr(raw, "run_id") else None)
        return rec

    def _insert(
        self,
        rec: EvidenceRecord,
        run_id: str | None,
        *,
        fence: tuple[str, int] | None = None,
    ) -> None:
        doc_json = json.dumps(rec.to_dict(), ensure_ascii=False)
        # RUN-018 WP2：证据中引用的产物（attachment_ref + doc 内嵌 art_* 引用）
        # 必须与 GC 共享 per-artifact 生命周期锁；锁内重新检查 Artifact 登记存在，
        # 已被回收 → 拒绝创建（不产生 dangling reference）。锁按排序获取。
        refs = sorted(set(_ARTIFACT_REF_RE.findall(doc_json)))
        primary_ref = rec.attachment_ref or ""
        # 只有整串都是 artifact_id（art_+20hex）的 attachment_ref 才参与锁协议；
        # 其他格式的 attachment_ref（自定义外部引用）保持原有行为。
        if primary_ref and _ARTIFACT_REF_RE.fullmatch(primary_ref) and primary_ref not in refs:
            refs.append(primary_ref)
        if refs:
            with self._lock, artifact_lifecycle_lock(self.db, refs[0], purpose="evidence_ref") as s:
                self._insert_locked(rec, run_id, s, fence=fence, refs=refs)
        else:
            with self._lock, self.db.session() as s:
                self._insert_locked(rec, run_id, s, fence=fence, refs=[])

    def _insert_locked(
        self,
        rec: EvidenceRecord,
        run_id: str | None,
        s: Any,
        *,
        fence: tuple[str, int] | None = None,
        refs: list[str],
    ) -> None:
        if fence is not None and run_id:
            if not fenced_guard(
                s,
                run_id=run_id,
                worker_id=str(fence[0]),
                fencing_token=int(fence[1]),
            ):
                raise AppError(
                    ErrorCode.CONFLICT,
                    "LEASE_LOST: 租约已失效，证据写入被拒绝（fencing guard）",
                )
        if refs:
            # PostgreSQL：为其余引用补齐 advisory 锁（首把已由 lifecycle lock 取得，
            # 同事务内重复申请同键是安全的）。SQLite：锁表写事务已持有库级写锁。
            _acquire_extra_ref_locks(s, self.db, refs)
            # 锁内最终复核：全部引用的 Artifact 登记必须仍然存在
            verify_artifact_refs_exist(s, refs)
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
                stored_content_sha256=rec.stored_content_sha256,
                attachment_sha256=rec.attachment_sha256,
                attachment_ref=rec.attachment_ref,
                source_trust=rec.source_trust,
                integrity_check=rec.integrity_check,
                integrity_detail=dict(rec.integrity_detail),
                redacted=rec.redacted,
                doc=rec.to_dict(),
                captured_at=rec.captured_at or utc_now_iso(),
            )
        )

    def verify_integrity(self, evidence_id: str, original_content: dict[str, Any]) -> bool:
        """**旧语义**：调用者提交原始内容做来源摘要比对（不是存储完整性校验）。

        结果写入 `integrity_detail["source_payload_match"]`，不再冒用 `integrity_check`，
        避免把"来源内容可比对"当成"持久化内容未被篡改"。
        """
        rec = self.get(evidence_id)
        if not rec:
            return False
        ok = rec.sha256 == _content_hash(original_content)
        with self.db.session() as s:
            row = s.get(EvidenceRow, evidence_id)
            if row:
                doc = dict(row.doc or {})
                detail = dict(doc.get("integrity_detail") or {})
                detail["source_payload_match"] = "PASS" if ok else "FAIL"
                detail["source_payload_checked_with"] = "caller_supplied_content"
                doc["integrity_detail"] = detail
                row.doc = doc
        return ok

    def verify_storage_integrity(
        self,
        evidence_id: str,
        *,
        expected_workspace: str | None = None,
        artifacts: Any | None = None,
    ) -> dict[str, Any]:
        """服务端存储完整性：读持久化记录与内容 → 重算摘要 → 与保存时摘要比较。

        校验项（全部真实执行，不接收调用者提供的原始内容）：
        1. 记录存在；
        2. 持久化内容重算摘要 == stored_content_sha256（历史行无此摘要 → NOT_RUN）；
        3. Workspace 归属（调用方声明时）；
        4. 附件：ArtifactStore.read() 后对字节重算 SHA-256 == attachment_sha256；
        5. 派生证据：raw_ref 存在、是 raw、同工作区、记录 derivation_rule。
        """
        rec = self.get(evidence_id)
        if rec is None:
            return {"status": "FAIL", "reason": "EVIDENCE_NOT_FOUND", "checks": {}}
        checks: dict[str, Any] = {"record_found": True}
        reasons: list[str] = []
        if expected_workspace:
            checks["workspace_match"] = rec.workspace_id == expected_workspace
            if not checks["workspace_match"]:
                reasons.append("WORKSPACE_MISMATCH")

        recomputed = _content_hash(rec.content)
        recorded = rec.stored_content_sha256
        if not recorded:
            checks["stored_content_match"] = None
            reasons.append("STORED_DIGEST_NOT_RECORDED_AT_CAPTURE")
        else:
            checks["stored_content_match"] = recomputed == recorded
            if not checks["stored_content_match"]:
                reasons.append("STORED_CONTENT_MISMATCH")

        if rec.attachment_ref:
            checks["attachment_present"] = True
            checks["attachment_digest_recorded"] = bool(rec.attachment_sha256)
            if not rec.attachment_sha256:
                # 历史行没记附件摘要：既不能证明，也不能判成"已篡改"
                reasons.append("ATTACHMENT_DIGEST_NOT_RECORDED_AT_CAPTURE")
            if artifacts is None:
                checks["attachment_readable"] = None
                reasons.append("ARTIFACT_STORE_UNAVAILABLE")
            else:
                try:
                    data, meta = artifacts.read(rec.attachment_ref)
                except Exception as exc:  # noqa: BLE001
                    checks["attachment_readable"] = False
                    tampered = "integrity" in str(exc).lower()
                    reasons.append("ATTACHMENT_TAMPERED" if tampered else "ATTACHMENT_UNREADABLE")
                else:
                    actual = hashlib.sha256(data).hexdigest()
                    checks["attachment_readable"] = True
                    if rec.attachment_sha256:
                        checks["attachment_sha256_match"] = actual == rec.attachment_sha256
                        if not checks["attachment_sha256_match"]:
                            reasons.append("ATTACHMENT_TAMPERED")
                        checks["attachment_meta_sha256_match"] = (
                            meta.get("sha256") == rec.attachment_sha256
                        )
                        if not checks["attachment_meta_sha256_match"]:
                            reasons.append("ATTACHMENT_META_MISMATCH")

        if rec.kind == "derived":
            raw = self.get(rec.raw_ref or "")
            checks["derived_source_exists"] = raw is not None
            checks["derived_source_is_raw"] = bool(raw and raw.kind == "raw")
            checks["derived_source_same_workspace"] = bool(
                raw and raw.workspace_id == rec.workspace_id
            )
            checks["derivation_rule_recorded"] = bool(rec.derivation_rule)
            for key in (
                "derived_source_exists",
                "derived_source_is_raw",
                "derived_source_same_workspace",
                "derivation_rule_recorded",
            ):
                if checks[key] is False:
                    reasons.append(key.upper())

        # FAIL 只表示"发现不一致/被篡改"；"缺少记录、无法证明"一律 NOT_RUN。
        # attachment_digest_recorded=False 属于"记录缺失"，不是"检测到篡改"。
        soft_missing = {"attachment_digest_recorded"}
        hard_false = [k for k, v in checks.items() if v is False and k not in soft_missing]
        if hard_false:
            status = "FAIL"
        elif (
            any(v is None for v in checks.values())
            or checks.get("attachment_digest_recorded", True) is False
        ):
            status = "NOT_RUN"
        else:
            status = "PASS"

        with self.db.session() as s:
            row = s.get(EvidenceRow, evidence_id)
            if row:
                row.integrity_check = status
                row.integrity_detail = {
                    "storage_checks": checks,
                    "reasons": reasons,
                    "recomputed_sha256": recomputed,
                    "recorded_stored_content_sha256": recorded,
                }
                doc = dict(row.doc or {})
                doc["integrity_check"] = status
                doc["integrity_detail"] = row.integrity_detail
                row.doc = doc
        return {
            "evidence_id": evidence_id,
            "status": status,
            "reasons": reasons,
            "checks": checks,
            "stored_content_sha256": recorded,
            "recomputed_sha256": recomputed,
            "attachment_sha256": rec.attachment_sha256,
            "workspace_id": rec.workspace_id,
        }

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
        workspace_ids: Sequence[str] | None = None,
    ) -> list[EvidenceRecord]:
        with self.db.session() as s:
            q = select(EvidenceRow)
            if workspace_id:
                q = q.where(EvidenceRow.workspace_id == workspace_id)
            if workspace_ids is not None:
                q = q.where(EvidenceRow.workspace_id.in_(list(workspace_ids)))
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
        stored_content_sha256=row.stored_content_sha256 or "",
        attachment_sha256=row.attachment_sha256 or "",
        integrity_detail=dict(row.integrity_detail or doc.get("integrity_detail") or {}),
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
            existing = s.get(DocRow, doc_id)
            if existing is not None and _workspace_conflict(existing.workspace_id, workspace_id):
                raise AppError(
                    ErrorCode.AUTHORIZATION_ERROR,
                    f"CROSS_WORKSPACE_DOC_WRITE: {doc_id} belongs to workspace "
                    f"'{existing.workspace_id}', not '{workspace_id}'",
                )
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
        workspace_ids: Sequence[str] | None = None,
    ) -> list[DocRecord]:
        with self.db.session() as s:
            q = select(DocRow)
            if kind:
                q = q.where(DocRow.kind == kind)
            if workspace_id:
                q = q.where(DocRow.workspace_id == workspace_id)
            if workspace_ids is not None:
                q = q.where(DocRow.workspace_id.in_(list(workspace_ids)))
            if ref_id:
                q = q.where(DocRow.ref_id == ref_id)
            if status:
                q = q.where(DocRow.status == status)
            rows = s.scalars(q.order_by(DocRow.created_at.desc()).offset(offset).limit(limit)).all()
            return [_to_doc(r) for r in rows]

    def iter_all(
        self, *, kind: str, page_size: int = 500, workspace_ids: Sequence[str] | None = None
    ) -> Iterable[DocRecord]:
        """按键集分页遍历某类文档（RUN-014 WP3：避免 `limit=N` 静默截断统计）。

        以主键 `id` 作为游标翻页，**完整读完**才停止；调用方可据此计算全量聚合，
        也可以据 `len()` 与分页次数判断是否真的全量。不做一次性 `limit=1000` 截断。
        """
        last_id = ""
        while True:
            with self.db.session() as s:
                q = select(DocRow).where(DocRow.kind == kind)
                if workspace_ids is not None:
                    q = q.where(DocRow.workspace_id.in_(list(workspace_ids)))
                if last_id:
                    q = q.where(DocRow.id > last_id)
                rows = s.scalars(q.order_by(DocRow.id).limit(max(1, page_size))).all()
                docs = [_to_doc(r) for r in rows]
                last_id = rows[-1].id if rows else last_id
            yield from docs
            if len(docs) < page_size:
                return

    def count(
        self,
        *,
        kind: str | None = None,
        workspace_ids: Sequence[str] | None = None,
    ) -> int:
        """SQL COUNT(*)：不读文档体，不受任何 limit 影响。"""
        from sqlalchemy import func

        with self.db.session() as s:
            q = select(func.count()).select_from(DocRow)
            if kind:
                q = q.where(DocRow.kind == kind)
            if workspace_ids is not None:
                q = q.where(DocRow.workspace_id.in_(list(workspace_ids)))
            return int(s.scalar(q) or 0)

    def delete(self, doc_id: str) -> bool:
        with self.db.session() as s:
            row = s.get(DocRow, doc_id)
            if not row:
                return False
            s.delete(row)
            return True


def _workspace_conflict(existing: str | None, incoming: str | None) -> bool:
    """既有行与本次写入的 Workspace 是否冲突（空值视为未声明，不冲突）。"""
    a = (existing or "").strip()
    b = (incoming or "").strip()
    return bool(a and b and a != b)


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
        # RUN-017 WP4.3：能力边界必须显式。当前只实现了本地内容寻址存储；
        # 配置要求 s3/minio 时必须在**启动时**失败，不能静默用本地目录冒充对象存储
        # （否则备份/恢复、容量规划与安全边界全部失真）。
        if backend != "local":
            raise AppError(
                ErrorCode.PRECONDITION_FAILED,
                f"ARTIFACT_BACKEND_UNSUPPORTED: '{backend}' 未实现（当前只支持 local）",
            )
        self.root.mkdir(parents=True, exist_ok=True)

    def put(
        self,
        data: bytes,
        *,
        workspace_id: str,
        media_type: str = "application/octet-stream",
        original_name: str = "",
        fence: tuple[str, int] | None = None,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        """写入内容寻址产物。

        RUN-016 WP1：新增可选 fencing。
        - `fence=(worker_id, fencing_token)` 时，**文件落盘与数据库登记之间**先做租约守卫；
          失去执行权的旧 Worker 不能登记新的产物记录（否则会产生无人引用的孤儿产物）。
        - 文件先原子落盘（临时文件 + rename），守卫失败时若本次新建且库中仍无登记，
          立即删掉自己刚写的文件——不留孤儿，也不误删他人已登记的同哈希文件。

        RUN-018 WP2：整个「检查 + 写文件 + 登记」临界区持有统一生命周期锁
        （见 `artifact_lifecycle_lock`）。与 GC 的隔离动作（`_safe_quarantine_file`）
        共享同一把 per-artifact 锁：
        - GC 先持锁：put 等待 → GC 复核无登记 → 完成隔离 → put 取得锁后**重新写文件**
          并登记 → 最终 ArtifactRow 对应文件真实存在；
        - put 先持锁：写文件 + 登记完成后 GC 才取得锁 → GC 发现登记 → 不隔离。
        检查与移动之间不再有 unlocked 窗口（旧实现的竞态 A 根因）。
        """
        digest = hashlib.sha256(data).hexdigest()
        artifact_id = f"art_{digest[:20]}"
        path = self._local_path(digest)
        created_file = False
        try:
            with artifact_lifecycle_lock(self.db, artifact_id, purpose="put") as s:
                if fence is not None:
                    if not fenced_guard(
                        s,
                        run_id=str(run_id or ""),
                        worker_id=str(fence[0]),
                        fencing_token=int(fence[1]),
                    ):
                        raise AppError(
                            ErrorCode.CONFLICT,
                            "LEASE_LOST: 租约已失效，产物登记被拒绝（fencing guard）",
                        )
                row = s.get(ArtifactRow, artifact_id)
                if row is None:
                    if not path.exists():
                        path.parent.mkdir(parents=True, exist_ok=True)
                        tmp = path.with_name(f".{digest}.{uuid.uuid4().hex}.tmp")
                        tmp.write_bytes(data)
                        # os.replace 同分区原子；并发同哈希写入时后者覆盖前者，内容一致因此无害
                        tmp.replace(path)
                        created_file = True
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
                elif not path.exists():
                    # 已登记但文件缺失（历史不一致）：内容寻址语义下按传入内容自愈重建。
                    # 锁内重建不会被并发的 GC 隔离打断。
                    path.parent.mkdir(parents=True, exist_ok=True)
                    tmp = path.with_name(f".{digest}.{uuid.uuid4().hex}.tmp")
                    tmp.write_bytes(data)
                    tmp.replace(path)
                    created_file = True
        except Exception:
            if created_file:
                self._remove_unregistered_file(artifact_id, path)
            raise
        return {
            "artifact_id": artifact_id,
            "sha256": digest,
            "size": len(data),
            "media_type": media_type,
            "path": f"artifacts/{digest[:2]}/{digest}",
            "original_name": original_name,
        }

    def _remove_unregistered_file(self, artifact_id: str, path: Path) -> None:
        """删除本次新建但未成功登记的产物文件；若已有登记则保留（他人可能正在引用）。

        RUN-018 WP2：与 GC 隔离动作共享生命周期锁，检查与 unlink 之间无窗口。
        """
        try:
            with artifact_lifecycle_lock(self.db, artifact_id, purpose="put_cleanup") as s:
                registered = s.get(ArtifactRow, artifact_id) is not None
                if registered:
                    return
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    return
        except Exception:  # noqa: BLE001 - 数据库不可用时宁可留文件给 GC，也不误删
            return

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
                "workspace_id": row.workspace_id,
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

    def list(
        self,
        workspace_id: str | None = None,
        limit: int = 100,
        *,
        workspace_ids: Sequence[str] | None = None,
    ) -> list[dict[str, Any]]:
        with self.db.session() as s:
            q = select(ArtifactRow)
            if workspace_id:
                q = q.where(ArtifactRow.workspace_id == workspace_id)
            if workspace_ids is not None:
                q = q.where(ArtifactRow.workspace_id.in_(list(workspace_ids)))
            rows = s.scalars(q.order_by(ArtifactRow.created_at.desc()).limit(limit)).all()
            return [
                {
                    "artifact_id": r.artifact_id,
                    "sha256": r.sha256,
                    "size": r.size,
                    "media_type": r.media_type,
                    "original_name": r.original_name,
                    "workspace_id": r.workspace_id,
                    "created_at": r.created_at,
                }
                for r in rows
            ]

    # ------------------------------------------------------------ RUN-016 WP1 治理
    def verify_consistency(self, *, limit: int = 0) -> dict[str, Any]:
        """逐个校验「数据库登记 ↔ 落盘文件」一致性（文件存在 + 内容哈希匹配）。

        `limit>0` 时只抽查前 N 条（大库巡检用）；返回结构化报告，供运维/测试断言。
        """
        missing: list[dict[str, Any]] = []
        mismatched: list[dict[str, Any]] = []
        checked = 0
        with self.db.session() as s:
            q = select(ArtifactRow).order_by(ArtifactRow.created_at)
            if limit:
                q = q.limit(limit)
            rows = s.scalars(q).all()
            for row in rows:
                checked += 1
                path = Path(row.path)
                if not path.is_file():
                    missing.append({"artifact_id": row.artifact_id, "path": row.path})
                    continue
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                if digest != row.sha256:
                    mismatched.append(
                        {
                            "artifact_id": row.artifact_id,
                            "path": row.path,
                            "expected": row.sha256,
                            "actual": digest,
                        }
                    )
        return {
            "checked": checked,
            "missing": missing,
            "hash_mismatch": mismatched,
            "consistent": not missing and not mismatched,
        }

    def _referenced_artifact_ids(self, s: Any, *, batch_size: int = 500) -> set[str]:
        """收集仍被业务引用的产物 ID（keyset 分页，不一次性加载全部文档）。

        三个来源都要看，否则会误删真实引用：
        1. `tg_evidence.attachment_ref`（管道写入的截图/DOM/ARIA 附件）；
        2. Evidence `doc` JSON 内嵌的 `art_*` 引用（探针/诊断等派生证据）；
        3. `tg_run.report_json` 内嵌的引用（trace_artifact 等只在 Run 报告里出现，
           不属于任何一条 Evidence——RUN-016 实测：729 个产物中 181 个只被报告引用）。

        RUN-017 WP4.2：旧实现用 `select(EvidenceRow.doc)` / `select(RunRow.report_json)`
        一次性把**全部**文档读进 Python 内存，10 万条 Evidence 时峰值内存与查询时长
        不可控。现在按主键 keyset 分页，每批只保留 batch_size 条文档的瞬时内存；
        引用集合本身仍需要保存在内存中（这是判定"是否被引用"的最小状态）。
        """
        refs = {str(r) for r in s.scalars(select(EvidenceRow.attachment_ref).distinct()) if r}
        last_id = ""
        while True:
            rows = s.execute(
                select(EvidenceRow.evidence_id, EvidenceRow.doc)
                .where(EvidenceRow.evidence_id > last_id)
                .order_by(EvidenceRow.evidence_id)
                .limit(batch_size)
            ).all()
            if not rows:
                break
            for evidence_id, doc in rows:
                last_id = str(evidence_id)
                if isinstance(doc, dict):
                    refs.update(_ARTIFACT_REF_RE.findall(json.dumps(doc, ensure_ascii=False)))
        last_run = ""
        while True:
            rows = s.execute(
                select(RunRow.run_id, RunRow.report_json)
                .where(RunRow.run_id > last_run)
                .order_by(RunRow.run_id)
                .limit(batch_size)
            ).all()
            if not rows:
                break
            for run_id, report in rows:
                last_run = str(run_id)
                if isinstance(report, dict) and report:
                    refs.update(_ARTIFACT_REF_RE.findall(json.dumps(report, ensure_ascii=False)))
        return refs

    def _artifact_still_referenced(self, s: Any, artifact_id: str) -> bool:
        """删除前的**定向**复核：只查这一条产物是否被引用（不重扫全库）。

        与 `_referenced_artifact_ids()` 的全量扫描配合：
        - 扫描给出候选；
        - 真正删除前再用定向查询确认一次，缩小"扫描后到删除前"的竞态窗口。
        """
        pattern = f"%{artifact_id}%"
        if s.scalar(
            select(EvidenceRow.evidence_id)
            .where(EvidenceRow.attachment_ref == artifact_id)
            .limit(1)
        ):
            return True
        # Evidence.doc 内嵌引用（JSON 文本匹配，SQLite/PG 通用；只作用于单条候选）
        if s.scalar(
            select(EvidenceRow.evidence_id)
            .where(cast(EvidenceRow.doc, String).like(pattern))
            .limit(1)
        ):
            return True
        if s.scalar(
            select(RunRow.run_id).where(cast(RunRow.report_json, String).like(pattern)).limit(1)
        ):
            return True
        return False

    def scan(
        self,
        *,
        retention_days: float = 7.0,
        now: float | None = None,
    ) -> dict[str, Any]:
        """只读扫描：孤儿文件 / 孤儿登记 / 缺文件 / 哈希不一致（不删除任何东西）。"""
        now_ts = time.time() if now is None else now
        cutoff = now_ts - retention_days * 86400
        orphan_files: list[dict[str, Any]] = []
        orphan_rows: list[dict[str, Any]] = []
        missing_files: list[dict[str, Any]] = []
        fresh_orphans: list[dict[str, Any]] = []
        with self.db.session() as s:
            refs = self._referenced_artifact_ids(s)
            # RUN-017 WP4.2：只取必要列（不实例化 ORM 对象），10 万级登记下表内存大幅下降
            rows = s.execute(
                select(
                    ArtifactRow.artifact_id,
                    ArtifactRow.sha256,
                    ArtifactRow.path,
                    ArtifactRow.created_at,
                    ArtifactRow.size,
                )
            ).all()
            by_sha = {row[1]: row[0] for row in rows}
            for artifact_id, sha256, path_str, created_at, size in rows:
                path = Path(path_str)
                if not path.is_file():
                    missing_files.append({"artifact_id": artifact_id, "path": path_str})
                if artifact_id in refs:
                    continue
                item = {
                    "artifact_id": artifact_id,
                    "sha256": sha256,
                    "path": path_str,
                    "created_at": created_at,
                    "size": size,
                }
                if _iso_epoch(created_at) >= cutoff:
                    fresh_orphans.append(item)
                else:
                    orphan_rows.append(item)
            scanned_files = 0
            if self.root.is_dir():
                for path in sorted(self.root.glob("*/*")):
                    if not path.is_file() or path.parent.name.startswith("_"):
                        continue
                    name = path.name
                    if len(name) != 64:
                        continue
                    scanned_files += 1
                    if name in by_sha:
                        continue
                    if f"art_{name[:20]}" in refs:
                        continue
                    mtime = path.stat().st_mtime
                    item = {"sha256": name, "path": str(path), "mtime": mtime}
                    if mtime < cutoff:
                        orphan_files.append(item)
                    else:
                        fresh_orphans.append(item)
        return {
            "root": str(self.root),
            "retention_days": retention_days,
            "cutoff_iso": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(cutoff)),
            "scanned_rows": len(by_sha),
            "scanned_files": scanned_files,
            "referenced_artifacts": len(refs),
            "orphan_files": orphan_files,
            "orphan_rows": orphan_rows,
            "protected_fresh": fresh_orphans,
            "missing_files": missing_files,
        }

    def collect_garbage(
        self,
        scan: dict[str, Any],
        *,
        apply: bool = False,
        audit_path: Path | None = None,
        actor: str = "artifact-gc",
    ) -> dict[str, Any]:
        """按扫描报告回收孤儿产物（RUN-018 WP4 并发安全加固）。

        安全约束（任一不满足即跳过，不删）：
        - 默认 dry-run；只有 `apply=True` 才真正移动文件/删除登记；
        - 文件移动而非 `unlink`：统一放入 `root/_quarantine/<ts>/`，可人工回滚；
        - 删除前**重新校验**引用关系，扫描后被引用的对象自动跳过；
        - 每条动作都写审计记录（JSONL），包含 sha256/大小/原因/操作者/时间。

        RUN-018 WP4 并发安全（针对两个真实竞态窗口）：

        竞态 A（orphan_files）：扫描发现孤儿文件 → Worker 调用 put() 写入同哈希内容
        并登记 ArtifactRow → GC 移动文件到 quarantine → 新登记的 ArtifactRow 指向
        不存在的文件。
        修复：文件移动前**再次查询** ArtifactRow 是否已登记该 SHA（单条 SELECT，
        非全量扫描），若已登记则跳过。

        竞态 B（orphan_rows）：定向复核 `_artifact_still_referenced()` 通过后、
        `s.delete(row)` 之前，另一事务写入 Evidence/Run 引用。
        修复：将"复核 + 删除"绑定在同一数据库事务内（SQLite 的写事务天然互斥；
        PostgreSQL 的 READ COMMITTED 下用 SELECT FOR UPDATE 锁定行后复核+删除），
        并辅以进程内 per-artifact 锁避免同进程的 put() 与 GC 交错。
        """
        ts = time.strftime("%Y%m%dT%H%M%S")
        stamp_iso = utc_now_iso()
        actions: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        with self.db.session() as s:
            refs = self._referenced_artifact_ids(s)
            for item in scan.get("orphan_rows", []):
                if item["artifact_id"] in refs:
                    skipped.append({**item, "reason": "referenced_at_apply_time"})
                    continue
                actions.append({"op": "delete_row", **item, "reason": "no_business_reference"})
            for item in scan.get("orphan_files", []):
                if f"art_{item['sha256'][:20]}" in refs:
                    skipped.append({**item, "reason": "referenced_at_apply_time"})
                    continue
                actions.append({"op": "quarantine_file", **item, "reason": "unregistered_file"})
        if not apply:
            return {
                "apply": False,
                "dry_run": True,
                "actions": actions,
                "skipped": skipped,
                "audit_path": None,
            }
        quarantine = self.root / "_quarantine" / ts
        done: list[dict[str, Any]] = []
        for action in actions:
            record = {"at": stamp_iso, "actor": actor, **action}
            try:
                if action["op"] == "quarantine_file":
                    record.update(self._safe_quarantine_file(action, quarantine))
                else:
                    record.update(self._safe_delete_row(action, quarantine))
            except Exception as exc:  # noqa: BLE001 - 单条失败不得中断整批
                record["result"] = "error"
                record["error"] = f"{type(exc).__name__}: {exc}"[:300]
            done.append(record)
        applied = [d for d in done if d.get("result") != "gone"]
        audit_file = audit_path or (self.root / "_gc-audit.jsonl")
        if applied:
            audit_file.parent.mkdir(parents=True, exist_ok=True)
            with audit_file.open("a", encoding="utf-8") as fh:
                for record in applied:
                    fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        return {
            "apply": True,
            "dry_run": False,
            "actions": done,
            "skipped": skipped,
            "quarantine_dir": str(quarantine),
            "audit_path": str(audit_file),
        }

    def _safe_quarantine_file(self, action: dict[str, Any], quarantine: Path) -> dict[str, Any]:
        """RUN-018 WP2 竞态 A 修复（锁协议版）：隔离文件前持有统一生命周期锁。

        旧实现：检查（SELECT）与移动（replace）之间退出数据库事务 → 竞态窗口。
        新实现：`artifact_lifecycle_lock` 与 put() 共享 per-artifact 锁，
        「复核登记 + 复核引用 + 移动文件」在锁内一次完成：
        - put() 先持锁：登记完成后 GC 才进入 → 发现登记 → 跳过；
        - GC 先持锁：复核通过即移动 → put 等待锁，取得后重新写文件并登记。
        同步动作写入 GC journal（intent → done），进程崩溃后可由
        `recover_journal()` 恢复（§2.4 文件系统与数据库崩溃一致性）。
        """
        sha256 = action["sha256"]
        artifact_id = f"art_{sha256[:20]}"
        src = Path(action["path"])
        with artifact_lifecycle_lock(self.db, artifact_id, purpose="gc_quarantine") as s:
            row = s.get(ArtifactRow, artifact_id)
            if row is not None:
                return {
                    "result": "skipped_referenced",
                    "reason_detail": "artifact_row_registered_before_quarantine",
                }
            # 也查引用（防止通过其他途径产生引用）
            if self._artifact_still_referenced(s, artifact_id):
                return {
                    "result": "skipped_referenced",
                    "reason_detail": "referenced_before_quarantine",
                }
            if not src.is_file():
                return {"result": "gone"}
            quarantine.mkdir(parents=True, exist_ok=True)
            dest = quarantine / sha256
            jid = self._journal_intent(
                "quarantine_file", artifact_id, sha256, src, dest, extra=action
            )
            src.replace(dest)
            self._journal_done(jid)
            return {"result": "quarantined", "quarantine_path": str(dest)}

    def _safe_delete_row(self, action: dict[str, Any], quarantine: Path) -> dict[str, Any]:
        """RUN-018 WP2 竞态 B 修复（锁协议版）：复核与删除在同一把生命周期锁内完成。

        旧实现注释声称 SELECT FOR UPDATE，实际只是 `s.get()`，且 Evidence/Run
        引用写入方并不与 GC 共享锁 → PostgreSQL 并发安全无法证明。
        新实现：GC 与**所有引用创建方**（Evidence `_insert` / Run 报告提交）共享
        `artifact_lifecycle_lock`：
        - 引用先持锁：引用创建完成（同事务提交）→ GC 进入后发现引用 → 跳过；
        - GC 先持锁：完整复核 + 删除 → 引用写入方取得锁后重新检查 Artifact
          是否仍然存在，已回收则拒绝引用创建（不产生 dangling reference）。

        崩溃一致性（§2.4）：DB 删除提交成功后文件移动前崩溃 → journal 留有
        intent 记录，`recover_journal()` 会把残留文件移入 quarantine；
        文件移动后崩溃 → journal done 缺失同样可恢复判定。
        """
        artifact_id = action["artifact_id"]
        sha256 = str(action.get("sha256") or artifact_id[4:])
        src = Path(action.get("path") or "")
        dest = quarantine / sha256
        with artifact_lifecycle_lock(self.db, artifact_id, purpose="gc_delete") as s:
            row = s.get(ArtifactRow, artifact_id)
            if row is None:
                return {"result": "gone"}
            # 锁内定向复核：Evidence.attachment_ref / Evidence.doc / Run.report_json
            if self._artifact_still_referenced(s, artifact_id):
                return {"result": "skipped_referenced"}
            jid = self._journal_intent("delete_row", artifact_id, sha256, src, dest, extra=action)
            # 同事务删除（session 退出时提交 → 锁释放）
            s.delete(row)

        # DB 已提交；处理落盘文件。此顺序保证「登记删除」先于「文件隔离」：
        # 崩溃最坏留下一个孤儿文件（下一次 GC 会回收），绝不会出现
        # ArtifactRow 指向被隔离走的文件。
        if src.is_file():
            quarantine.mkdir(parents=True, exist_ok=True)
            src.replace(dest)
            self._journal_done(jid)
            return {"result": "deleted", "quarantine_path": str(dest)}
        self._journal_done(jid, note="source_file_absent")
        return {"result": "deleted"}

    # ------------------------------------------------------- RUN-018 WP2 GC journal
    def _journal_file(self) -> Path:
        return self.root / "_gc-journal.jsonl"

    def _journal_append(self, record: dict[str, Any]) -> None:
        """JSONL 追加（单行 < 4KB，O_APPEND 原子性足以跨进程并发追加）。"""
        try:
            f = self._journal_file()
            f.parent.mkdir(parents=True, exist_ok=True)
            with f.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception:  # noqa: BLE001 - journal 失败不阻塞 GC 主流程（审计另有 JSONL）
            return

    def _journal_intent(
        self,
        op: str,
        artifact_id: str,
        sha256: str,
        src: Path,
        dest: Path,
        *,
        extra: dict[str, Any] | None = None,
    ) -> str:
        jid = uuid.uuid4().hex
        self._journal_append(
            {
                "jid": jid,
                "at": utc_now_iso(),
                "op": op,
                "artifact_id": artifact_id,
                "sha256": sha256,
                "src": str(src),
                "dest": str(dest),
                "state": "intent",
                "extra": {k: extra.get(k) for k in ("path", "reason") if extra and k in extra},
            }
        )
        return jid

    def _journal_done(self, jid: str, *, note: str = "") -> None:
        self._journal_append({"jid": jid, "state": "done", "note": note, "at": utc_now_iso()})

    def recover_journal(self) -> dict[str, Any]:
        """崩溃恢复（RUN-018 WP2 §2.4）：处理 GC journal 中 intent 无 done 的条目。

        - quarantine_file：dest 已存在且 src 不存在 → 文件其实已移动，补记 done；
          src 仍存在且登记不存在 → pending（留给下一次 GC 重新复核后回收）；
          登记已存在 → abandoned（不该隔离）。
        - delete_row：登记已删除 → 按 src/dest 实际状态补完文件移动并补记 done；
          登记仍在 → abandoned（DB 删除未提交）。

        所有隔离都保留审计记录；本函数默认不删除任何文件，只做幂等恢复。
        """
        f = self._journal_file()
        if not f.is_file():
            return {"journal": str(f), "recovered": 0, "pending": 0, "abandoned": 0, "details": []}
        open_intents: dict[str, dict[str, Any]] = {}
        with f.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                jid = str(rec.get("jid") or "")
                if rec.get("state") == "intent":
                    open_intents[jid] = rec
                elif rec.get("state") == "done":
                    open_intents.pop(jid, None)
        details: list[dict[str, Any]] = []
        counts = {"recovered": 0, "pending": 0, "abandoned": 0}
        for jid, intent in open_intents.items():
            op = intent.get("op")
            src = Path(intent.get("src") or "")
            dest = Path(intent.get("dest") or "")
            artifact_id = str(intent.get("artifact_id") or "")
            resolution = ""
            with self.db.session() as s:
                row = s.get(ArtifactRow, artifact_id)
            if op == "quarantine_file":
                if dest.is_file() and not src.is_file():
                    counts["recovered"] += 1
                    resolution = "recovered_file_already_moved"
                elif src.is_file() and row is None and not dest.exists():
                    counts["pending"] += 1
                    resolution = "pending_next_gc_recheck"
                else:
                    counts["abandoned"] += 1
                    resolution = "abandoned_state_changed"
            elif op == "delete_row":
                if row is not None:
                    counts["abandoned"] += 1
                    resolution = "abandoned_db_delete_not_committed"
                elif src.is_file() and not dest.exists():
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    src.replace(dest)
                    counts["recovered"] += 1
                    resolution = "recovered_file_moved_post_commit"
                else:
                    counts["recovered"] += 1
                    resolution = "recovered_already_consistent"
            else:
                counts["abandoned"] += 1
                resolution = "unknown_op"
            self._journal_append(
                {
                    "jid": jid,
                    "state": "resolved",
                    "resolution": resolution,
                    "op": op,
                    "artifact_id": artifact_id,
                    "at": utc_now_iso(),
                }
            )
            details.append({"jid": jid, "op": op, "resolution": resolution})
        return {
            "journal": str(f),
            "recovered": counts["recovered"],
            "pending": counts["pending"],
            "abandoned": counts["abandoned"],
            "details": details,
        }


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
