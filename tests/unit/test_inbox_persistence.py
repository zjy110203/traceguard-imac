"""RUN-005 WP1.4：Consumer Inbox 持久化可靠消费语义测试。

覆盖：PROCESSING / SUCCEEDED / FAILED / UNKNOWN、重复投递、多消费者独立、
崩溃恢复（租约过期接管）、并发抢占、本地副作用与消费记录同事务、迁移兼容。
"""

from __future__ import annotations

import threading

import pytest
from sqlalchemy import text
from traceguard_persistence import Database
from traceguard_persistence.models import DocRow
from traceguard_persistence.stores import PersistentConsumerInbox


class FakeClock:
    def __init__(self, start: float = 1_000.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


@pytest.fixture()
def db(work_tmp):  # type: ignore[no-untyped-def]
    import uuid

    base = work_tmp / f"inbox-{uuid.uuid4().hex[:8]}"
    base.mkdir(parents=True, exist_ok=True)
    d = Database(f"sqlite:///{base / 'inbox.db'}")
    d.create_schema()
    yield d
    d.dispose()


def test_success_is_not_executed_twice(db: Database) -> None:
    inbox = PersistentConsumerInbox(db)
    calls: list[int] = []

    def handler() -> dict:
        calls.append(1)
        return {"ok": True}

    first = inbox.run("proj", "evt1", handler)
    second = inbox.run("proj", "evt1", handler)
    assert first.executed and first.ok
    assert second.executed is False and second.skipped_reason == "ALREADY_SUCCEEDED"
    assert len(calls) == 1
    assert inbox.get("proj", "evt1").status == "SUCCEEDED"


def test_multiple_consumers_are_independent(db: Database) -> None:
    inbox = PersistentConsumerInbox(db)
    inbox.run("c1", "evt1", lambda: {"a": 1})
    inbox.run("c2", "evt1", lambda: {"b": 2})
    recs = inbox.list_for_event("evt1")
    assert {r.consumer_id for r in recs} == {"c1", "c2"}
    assert all(r.status == "SUCCEEDED" for r in recs)


def test_processing_lease_blocks_parallel_consumer(db: Database) -> None:
    clock = FakeClock()
    inbox = PersistentConsumerInbox(db, clock=clock)
    rec, claimed = inbox.claim("c1", "evt1", lease_seconds=30)
    assert claimed and rec.status == "PROCESSING" and rec.attempts == 1
    rec2, claimed2 = inbox.claim("c1", "evt1", lease_seconds=30)
    assert claimed2 is False and rec2.attempts == 1
    outcome = inbox.run("c1", "evt1", lambda: {"should": "not run"})
    assert outcome.executed is False and outcome.skipped_reason == "IN_FLIGHT"


def test_expired_lease_is_recovered_after_crash(db: Database) -> None:
    clock = FakeClock()
    inbox = PersistentConsumerInbox(db, clock=clock)
    inbox.claim("c1", "evt1", lease_seconds=10)
    clock.advance(11)  # 模拟进程崩溃，租约过期
    rec, claimed = inbox.claim("c1", "evt1", lease_seconds=10)
    assert claimed is True and rec.attempts == 2
    done = inbox.complete("c1", "evt1", {"ok": True})
    assert done.status == "SUCCEEDED"


def test_known_failure_can_retry_but_unknown_cannot(db: Database) -> None:
    inbox = PersistentConsumerInbox(db)

    def boom() -> None:
        raise RuntimeError("boom")

    first = inbox.run("c1", "evt1", boom)
    assert first.executed and not first.ok
    assert inbox.get("c1", "evt1").status == "FAILED"
    # 确定失败允许重试
    second = inbox.run("c1", "evt1", lambda: {"ok": True})
    assert second.executed is True and second.ok is True

    # 外部副作用不确定 → UNKNOWN，不得盲目重试
    third = inbox.run("c2", "evt1", boom, external_side_effect=True)
    assert third.executed and not third.ok
    assert inbox.get("c2", "evt1").status == "UNKNOWN"
    retry = inbox.run("c2", "evt1", lambda: {"danger": True}, external_side_effect=True)
    assert retry.executed is False
    assert retry.skipped_reason == "INDETERMINATE_SIDE_EFFECT"


def test_side_effect_and_consumption_commit_together(db: Database) -> None:
    inbox = PersistentConsumerInbox(db)

    def effect_fails(session):  # type: ignore[no-untyped-def]
        session.add(
            DocRow(
                id="doc_should_rollback",
                kind="test",
                workspace_id="default",
                doc={"x": 1},
                created_at="t",
                updated_at="t",
            )
        )
        session.flush()
        raise RuntimeError("after local write")

    outcome = inbox.consume_in_transaction("proj", "evt_bad", effect_fails)
    assert outcome.executed and not outcome.ok
    assert inbox.get("proj", "evt_bad").status == "FAILED"
    # 事务回滚：本地副作用不存在
    assert db is not None
    with db.session() as s:
        assert s.get(DocRow, "doc_should_rollback") is None

    def effect_ok(session):  # type: ignore[no-untyped-def]
        session.add(
            DocRow(
                id="doc_ok",
                kind="test",
                workspace_id="default",
                doc={"x": 2},
                created_at="t",
                updated_at="t",
            )
        )
        return {"written": "doc_ok"}

    ok = inbox.consume_in_transaction("proj", "evt_good", effect_ok)
    assert ok.ok
    with db.session() as s:
        assert s.get(DocRow, "doc_ok") is not None
    assert inbox.get("proj", "evt_good").status == "SUCCEEDED"


def test_concurrent_claim_across_connections_only_one_wins(db: Database, work_tmp) -> None:  # type: ignore[no-untyped-def]
    """用两个独立 Database（独立连接池）模拟两个进程并发抢占同一事件。"""
    db2 = Database(db.url)
    db2.create_schema()
    boxes = [PersistentConsumerInbox(db), PersistentConsumerInbox(db2)]
    barrier = threading.Barrier(4)
    results: list[bool] = []
    lock = threading.Lock()

    def worker(i: int) -> None:
        inbox = boxes[i % 2]
        barrier.wait()
        try:
            _, claimed = inbox.claim("proj", "evt_race", lease_seconds=30)
        except Exception:  # noqa: BLE001 - SQLite 写竞争可抛异常，视为未获得
            claimed = False
        with lock:
            results.append(claimed)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count(True) == 1
    db2.dispose()


def test_legacy_processed_status_is_migrated(db: Database) -> None:
    """旧版本只有 PROCESSED；迁移后必须按"已完成"处理，不能被重复消费。"""
    inbox = PersistentConsumerInbox(db)
    inbox.claim("proj", "evt_legacy")
    with db.session() as s:
        s.execute(
            text("UPDATE tg_consumer_inbox SET status='PROCESSED' WHERE event_id='evt_legacy'")
        )
    from traceguard_persistence.db import _migrate_inbox_states

    with db.engine.begin() as conn:
        _migrate_inbox_states(conn)
    assert inbox.get("proj", "evt_legacy").status == "SUCCEEDED"
    outcome = inbox.run("proj", "evt_legacy", lambda: {"should": "not run"})
    assert outcome.executed is False


def test_stats_reports_all_states(db: Database) -> None:
    inbox = PersistentConsumerInbox(db)
    inbox.run("c1", "e1", lambda: {"ok": 1})
    inbox.run("c1", "e2", lambda: (_ for _ in ()).throw(RuntimeError("x")))
    inbox.run(
        "c1", "e3", lambda: (_ for _ in ()).throw(RuntimeError("y")), external_side_effect=True
    )
    stats = inbox.stats()
    assert stats["SUCCEEDED"] == 1 and stats["FAILED"] == 1 and stats["UNKNOWN"] == 1
