"""Event Store（阶段4，C05）。

关键语义（路线图 F01/F02/G02/G05/G06，已冻结）：
- workspace_id 必填且由服务端可信上下文注入，禁止从 payload 自行决定；
- (producer_instance_id, sequence) 唯一，序号由实例内并发安全分配器产生；
- 重启换新 producer_instance_id，序列空间重新计数；
- idempotency_key 在 producer 维度唯一：(producer_id, idempotency_key)；重复投递返回已有事件；
- 同键异载荷 → 冲突拒绝并审计，不静默覆盖；
- 原始事件只追加，不可原地修改；
- 合法跳号不判丢失，缺口先标"待确认"；
- context_refs 仅关联，不表示因果。
"""

from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass, field
from typing import Any

from traceguard_core.errors import AppError, ErrorCode
from traceguard_core.ids import new_id, utc_now_iso


@dataclass
class StoredEvent:
    event_id: str
    event_type: str
    schema_version: str
    workspace_id: str
    context_refs: dict[str, str]
    producer_id: str
    producer_instance_id: str
    sequence: int
    occurred_at: str
    observed_at: str
    idempotency_key: str
    payload: dict[str, Any]
    payload_hash: str
    recorded_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "schema_version": self.schema_version,
            "workspace_id": self.workspace_id,
            "context_refs": dict(self.context_refs),
            "producer_id": self.producer_id,
            "producer_instance_id": self.producer_instance_id,
            "sequence": self.sequence,
            "occurred_at": self.occurred_at,
            "observed_at": self.observed_at,
            "idempotency_key": self.idempotency_key,
            "payload": dict(self.payload),
            "payload_hash": self.payload_hash,
            "recorded_at": self.recorded_at,
        }


def _hash_payload(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


class EventStore:
    """内存实现（阶段4）；生产可替换为 PostgreSQL 表，语义不变。

    线程安全：所有写操作加锁，保证序号分配并发安全。
    """

    def __init__(self) -> None:
        self._events: list[StoredEvent] = []
        self._idem_index: dict[tuple[str, str], StoredEvent] = {}  # (producer_id, key)
        self._seq_index: dict[str, int] = {}  # producer_instance_id -> last sequence
        self._conflicts: list[dict[str, Any]] = []
        self._instance_ids: set[str] = set()
        self._lock = threading.RLock()

    def new_producer_instance(self, producer_id: str) -> str:
        """Worker 重启调用：生成新的 producer_instance_id 与序列空间。"""
        iid = f"{producer_id}-inst-{new_id('pi')}"
        with self._lock:
            self._instance_ids.add(iid)
            self._seq_index[iid] = 0
        return iid

    def append(
        self,
        *,
        event_type: str,
        server_workspace_id: str,  # 服务端可信上下文注入，不接受客户端 payload 指定
        producer_id: str,
        producer_instance_id: str,
        idempotency_key: str,
        payload: dict[str, Any] | None = None,
        context_refs: dict[str, str] | None = None,
        occurred_at: str | None = None,
        schema_version: str = "0.1.0",
    ) -> StoredEvent:
        payload = payload or {}
        # 安全：禁止 payload 携带 workspace_id 覆盖服务端注入值
        clean_payload = {k: v for k, v in payload.items() if k != "workspace_id"}
        with self._lock:
            # 幂等：同 (producer_id, key) 已存在
            key = (producer_id, idempotency_key)
            existing = self._idem_index.get(key)
            if existing:
                if existing.payload_hash == _hash_payload(clean_payload):
                    return existing  # 重复投递，确认结果，不产生新副作用
                # 同键异载荷 → 冲突拒绝 + 审计
                self._conflicts.append(
                    {
                        "producer_id": producer_id,
                        "idempotency_key": idempotency_key,
                        "expected_hash": existing.payload_hash,
                        "got_hash": _hash_payload(clean_payload),
                        "at": utc_now_iso(),
                    }
                )
                raise AppError(ErrorCode.CONFLICT, "IDEMPOTENCY_KEY_PAYLOAD_CONFLICT")

            # 序号：实例内并发安全分配（合法跳号允许，但本实现连续分配）
            if producer_instance_id not in self._seq_index:
                self._seq_index[producer_instance_id] = 0
            self._seq_index[producer_instance_id] += 1
            seq = self._seq_index[producer_instance_id]

            ev = StoredEvent(
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
                payload_hash=_hash_payload(clean_payload),
            )
            self._events.append(ev)  # 只追加
            self._idem_index[key] = ev
            return ev

    def get(self, event_id: str) -> StoredEvent | None:
        with self._lock:
            return next((e for e in self._events if e.event_id == event_id), None)

    def list_events(self, workspace_id: str | None = None) -> list[StoredEvent]:
        with self._lock:
            if workspace_id:
                return [e for e in self._events if e.workspace_id == workspace_id]
            return list(self._events)

    def detect_gaps(self, producer_instance_id: str) -> list[int]:
        """检测序号缺口，先标记待确认（合法跳号/在途/丢失未区分，不伪造结论）。

        本实现的分配是连续的，正常无缺口；若外部注入跳号序列（测试），可检测。
        """
        with self._lock:
            seqs = sorted(
                e.sequence for e in self._events if e.producer_instance_id == producer_instance_id
            )
            if not seqs:
                return []
            gaps = []
            for a, b in zip(seqs, seqs[1:], strict=False):
                for n in range(a + 1, b):
                    gaps.append(n)  # 待确认，不直接判"丢失"
            return gaps

    def conflicts(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._conflicts)
