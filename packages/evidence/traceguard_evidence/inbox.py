"""Consumer Inbox（阶段4，G02）：消费端幂等。

原则（路线图）：
- 每个消费者独立去重，依据 (consumer_id, event_id)；
- 同一 Event 可被多消费者独立处理，互不覆盖；
- 重复收到已成功处理的 Event 不重复执行副作用；
- 消费记录与本地副作用在同一事务提交（本实现用锁模拟原子性，生产用 DB 事务）；
- 处理失败不出现"消费记录成功但副作用未提交"；
- 外部副作用结果不确定时不得盲目重复执行。
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass
class InboxRecord:
    consumer_id: str
    event_id: str
    status: str  # PROCESSING / SUCCEEDED / FAILED
    attempts: int = 0
    result: Any = None


class ConsumerInbox:
    def __init__(self) -> None:
        self._records: dict[tuple[str, str], InboxRecord] = {}
        self._lock = threading.RLock()

    def consume(
        self,
        consumer_id: str,
        event_id: str,
        handler: Callable[[], Any],
        *,
        external_side_effect: bool = False,
    ) -> InboxRecord:
        """幂等消费：已 SUCCEEDED 直接返回，不重复执行 handler。

        若 external_side_effect=True 且 handler 抛出不确定错误，保留 FAILED 不重试，
        避免盲目重复外部副作用。
        """
        with self._lock:
            key = (consumer_id, event_id)
            rec = self._records.get(key)
            if rec and rec.status == "SUCCEEDED":
                return rec  # 幂等命中，不重复副作用
            if rec and rec.status == "FAILED" and external_side_effect:
                return rec  # 外部副作用不确定，不盲目重试

            if rec is None:
                rec = InboxRecord(consumer_id=consumer_id, event_id=event_id, status="PROCESSING")
                self._records[key] = rec
            rec.attempts += 1
            try:
                result = handler()  # 本地副作用
                rec.result = result
                rec.status = "SUCCEEDED"  # 与副作用在同一临界区提交
            except Exception as e:  # noqa: BLE001
                rec.status = "FAILED"
                rec.result = f"ERROR: {type(e).__name__}: {e}"
            return rec

    def get(self, consumer_id: str, event_id: str) -> InboxRecord | None:
        with self._lock:
            return self._records.get((consumer_id, event_id))

    def list_for_event(self, event_id: str) -> list[InboxRecord]:
        with self._lock:
            return [r for (_, eid), r in self._records.items() if eid == event_id]
