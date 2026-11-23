"""Run 事件时间线投影（真实的内部消费场景）。

为什么需要这个消费者：Consumer Inbox 必须有**真实接入**的消费场景，否则只是未被使用的类。
本投影把只追加的 Event 聚合为按 Run 的时间线文档，供 UI / 报告使用：
- 消费幂等由 `(consumer_id, event_id)` 保证，重复投影不会重复写入条目；
- 本地副作用（tg_doc 写入）与 Inbox 消费记录**在同一事务提交**，不会出现
  "记成功但没生效"；
- 投影失败会留下 FAILED / UNKNOWN 事实，下一次投影按状态机重试或拒绝重试；
- 投影本身不是状态权威：Run 状态仍以 tg_run / API 为准。
"""

from __future__ import annotations

from typing import Any

from traceguard_core.ids import utc_now_iso
from traceguard_persistence.models import DocRow
from traceguard_persistence.stores import PersistentConsumerInbox

CONSUMER_ID = "run-timeline-projector"
DOC_KIND = "run_timeline"


def timeline_doc_id(run_id: str) -> str:
    return f"run_timeline_{run_id}"


class TimelineService:
    def __init__(self, rt: Any) -> None:
        self.rt = rt
        self.inbox = PersistentConsumerInbox(rt.db)

    def project_run(self, run_id: str, *, limit: int = 1000) -> dict[str, Any]:
        run = self.rt.tasks.get_run(run_id)
        if not run:
            from traceguard_core.errors import AppError, ErrorCode

            raise AppError(ErrorCode.NOT_FOUND, "run not found")
        events = self.rt.events.list_events(run_id=run_id, limit=limit)
        events.sort(key=lambda e: (e.recorded_at, e.event_id))

        projected = 0
        skipped = 0
        failed = 0
        for ev in events:
            outcome = self.inbox.consume_in_transaction(
                CONSUMER_ID,
                ev.event_id,
                lambda s, ev=ev: self._apply(s, run_id, ev),
            )
            if not outcome.executed:
                skipped += 1
            elif outcome.ok:
                projected += 1
            else:
                failed += 1

        doc = self._read(run_id) or self._empty(run_id, run.get("workspace_id", "default"))
        doc["projected_events"] = projected
        doc["skipped_events"] = skipped
        doc["failed_events"] = failed
        doc["total_events"] = len(events)
        doc["consumer"] = CONSUMER_ID
        doc["updated_at"] = utc_now_iso()
        doc["authority"] = "derived_view"  # 派生视图，不是业务状态权威
        return doc

    def get_timeline(self, run_id: str, *, catch_up: bool = True) -> dict[str, Any]:
        if catch_up:
            return self.project_run(run_id)
        doc = self._read(run_id)
        if doc is None:
            from traceguard_core.errors import AppError, ErrorCode

            raise AppError(ErrorCode.NOT_FOUND, "timeline not projected yet")
        return doc

    def inbox_stats(self) -> dict[str, Any]:
        return {"consumer_id": CONSUMER_ID, "status_counts": self.inbox.stats()}

    # ------------------------------------------------------------------ helpers
    def _empty(self, run_id: str, workspace_id: str) -> dict[str, Any]:
        return {
            "run_id": run_id,
            "workspace_id": workspace_id,
            "entries": [],
            "counts": {},
            "last_event_at": "",
            "created_at": utc_now_iso(),
        }

    def _read(self, run_id: str) -> dict[str, Any] | None:
        rec = self.rt.docs.get(timeline_doc_id(run_id))
        return dict(rec.doc) if rec else None

    def _apply(self, session: Any, run_id: str, ev: Any) -> dict[str, Any]:
        """在调用方事务内更新时间线文档（与 Inbox 记录同事务提交）。"""
        doc_id = timeline_doc_id(run_id)
        row = session.get(DocRow, doc_id)
        run = self.rt.tasks.get_run(run_id) or {}
        workspace_id = run.get("workspace_id", "default")
        doc = dict(row.doc) if row and row.doc else self._empty(run_id, workspace_id)
        entries = list(doc.get("entries") or [])
        entries.append(
            {
                "event_id": ev.event_id,
                "event_type": ev.event_type,
                "sequence": ev.sequence,
                "producer_instance_id": ev.producer_instance_id,
                "recorded_at": ev.recorded_at,
                "occurred_at": ev.occurred_at,
                "payload": ev.payload,
            }
        )
        counts = dict(doc.get("counts") or {})
        counts[ev.event_type] = int(counts.get(ev.event_type, 0)) + 1
        doc.update(
            {
                "run_id": run_id,
                "workspace_id": workspace_id,
                "entries": entries[-500:],
                "counts": counts,
                "last_event_at": ev.recorded_at,
                "updated_at": utc_now_iso(),
            }
        )
        if row is None:
            session.add(
                DocRow(
                    id=doc_id,
                    kind=DOC_KIND,
                    workspace_id=workspace_id,
                    ref_id=run_id,
                    version=1,
                    status="PROJECTED",
                    doc=doc,
                    created_at=utc_now_iso(),
                    updated_at=utc_now_iso(),
                )
            )
        else:
            row.doc = doc
            row.version = int(row.version or 1) + 1
            row.updated_at = utc_now_iso()
        session.flush()
        return {"entry_event_id": ev.event_id, "counts": counts}
