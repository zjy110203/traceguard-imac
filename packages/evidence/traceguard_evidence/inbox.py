"""Consumer Inbox（阶段4，G02）：消费端幂等。

原则（路线图）：
- 每个消费者独立去重，依据 (consumer_id, event_id)；
- 同一 Event 可被多消费者独立处理，互不覆盖；
- 重复收到已成功处理的 Event 不重复执行副作用；
- 消费记录与本地副作用在同一事务提交（内存版用临界区，持久化版用 DB 事务）；
- 处理失败不出现"消费记录成功但副作用未提交"；
- 外部副作用结果不确定时不得盲目重复执行（状态 UNKNOWN）。

RUN-005 语义统一：内存版与持久化版使用同一组状态机
    PROCESSING → SUCCEEDED
               → FAILED     （确定失败，可重试）
               → UNKNOWN    （含外部副作用，结果不确定，不得盲目重试）
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

PROCESSING = "PROCESSING"
SUCCEEDED = "SUCCEEDED"
FAILED = "FAILED"
UNKNOWN = "UNKNOWN"

TERMINAL_STATUSES = {SUCCEEDED, UNKNOWN}


@dataclass
class InboxRecord:
    consumer_id: str
    event_id: str
    status: str  # PROCESSING / SUCCEEDED / FAILED / UNKNOWN
    attempts: int = 0
    result: Any = None
    error: str = ""
    indeterminate: bool = False
    claim_expires_at: float = 0.0
    consumed_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "consumer_id": self.consumer_id,
            "event_id": self.event_id,
            "status": self.status,
            "attempts": self.attempts,
            "result": self.result,
            "error": self.error,
            "indeterminate": self.indeterminate,
            "claim_expires_at": self.claim_expires_at,
            "consumed_at": self.consumed_at,
            "updated_at": self.updated_at,
        }


@dataclass
class InboxOutcome:
    record: InboxRecord
    executed: bool
    ok: bool = False
    skipped_reason: str = ""
    value: Any = field(default=None)


class ConsumerInbox:
    """内存实现（测试/单进程）；持久化实现见 PersistentConsumerInbox，语义完全一致。"""

    def __init__(self, *, clock: Callable[[], float] = time.time) -> None:
        self._records: dict[tuple[str, str], InboxRecord] = {}
        self._lock = threading.RLock()
        self._clock = clock

    # ------------------------------------------------------------------ claim
    def claim(
        self, consumer_id: str, event_id: str, *, lease_seconds: float = 60.0
    ) -> tuple[InboxRecord, bool]:
        """尝试获得消费权；返回 (记录, 是否获得)。

        不可获得的情形：已 SUCCEEDED / UNKNOWN / 外部副作用不确定 / 其他消费者持有未过期租约。
        """
        now = self._clock()
        with self._lock:
            key = (consumer_id, event_id)
            rec = self._records.get(key)
            if rec is None:
                rec = InboxRecord(
                    consumer_id=consumer_id,
                    event_id=event_id,
                    status=PROCESSING,
                    attempts=1,
                    claim_expires_at=now + lease_seconds,
                )
                self._records[key] = rec
                return rec, True
            if rec.status in TERMINAL_STATUSES:
                return rec, False
            if rec.status == FAILED and rec.indeterminate:
                return rec, False
            if rec.status == PROCESSING and rec.claim_expires_at > now:
                return rec, False
            # FAILED（可重试）或 PROCESSING 租约过期（崩溃恢复）
            rec.status = PROCESSING
            rec.attempts += 1
            rec.claim_expires_at = now + lease_seconds
            return rec, True

    def complete(
        self, consumer_id: str, event_id: str, result: Any = None
    ) -> InboxRecord:
        with self._lock:
            rec = self._records[(consumer_id, event_id)]
            if rec.status == SUCCEEDED:
                return rec
            rec.status = SUCCEEDED
            rec.result = result
            rec.error = ""
            rec.claim_expires_at = 0.0
            return rec

    def fail(
        self,
        consumer_id: str,
        event_id: str,
        error: str,
        *,
        indeterminate: bool = False,
    ) -> InboxRecord:
        """失败落库。indeterminate=True 表示外部副作用结果不确定 → UNKNOWN，不得盲目重试。"""
        with self._lock:
            rec = self._records[(consumer_id, event_id)]
            rec.status = UNKNOWN if indeterminate else FAILED
            rec.indeterminate = indeterminate
            rec.error = error
            rec.result = f"ERROR: {error}" if not indeterminate else f"UNKNOWN: {error}"
            rec.claim_expires_at = 0.0
            return rec

    # -------------------------------------------------------------------- run
    def run(
        self,
        consumer_id: str,
        event_id: str,
        handler: Callable[[], Any],
        *,
        external_side_effect: bool = False,
        lease_seconds: float = 60.0,
    ) -> InboxOutcome:
        rec, claimed = self.claim(consumer_id, event_id, lease_seconds=lease_seconds)
        if not claimed:
            reason = "ALREADY_SUCCEEDED" if rec.status == SUCCEEDED else (
                "INDETERMINATE_SIDE_EFFECT" if rec.status == UNKNOWN else "IN_FLIGHT"
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

    # 兼容旧接口：等价于 run()
    def consume(
        self,
        consumer_id: str,
        event_id: str,
        handler: Callable[[], Any],
        *,
        external_side_effect: bool = False,
    ) -> InboxRecord:
        return self.run(
            consumer_id, event_id, handler, external_side_effect=external_side_effect
        ).record

    # ------------------------------------------------------------------ query
    def get(self, consumer_id: str, event_id: str) -> InboxRecord | None:
        with self._lock:
            return self._records.get((consumer_id, event_id))

    def list_for_event(self, event_id: str) -> list[InboxRecord]:
        with self._lock:
            return [r for (_, eid), r in self._records.items() if eid == event_id]

    def stats(self) -> dict[str, int]:
        with self._lock:
            out: dict[str, int] = {}
            for rec in self._records.values():
                out[rec.status] = out.get(rec.status, 0) + 1
            return out
