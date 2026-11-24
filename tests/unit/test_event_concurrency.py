"""RUN-005 WP1.5：Event Store 多进程并发测试。

用**真实多进程**（不是单线程模拟）验证：
- 并发序号分配不重复、不丢失已确认事件；
- 同键同载荷并发 → 返回同一事件（幂等），只落一行；
- 同键异载荷 → 明确 CONFLICT 并审计，且之后仍可继续写入（失败可恢复）；
- 序号缺口检测仍可用。
"""

from __future__ import annotations

import multiprocessing as mp

import pytest
from traceguard_core.errors import AppError
from traceguard_persistence import Database, PersistentEventStore


def _append_worker(url: str, instance_id: str, producer: str, start: int, count: int, q) -> None:
    db = Database(url)
    db.create_schema()
    store = PersistentEventStore(db)
    try:
        for i in range(start, start + count):
            store.append(
                event_type="step.completed",
                server_workspace_id="ws1",
                producer_id=producer,
                producer_instance_id=instance_id,
                idempotency_key=f"{instance_id}:{i}",
                payload={"i": i},
                context_refs={"run": "run_mp"},
            )
        q.put(("ok", start))
    except Exception as exc:  # noqa: BLE001
        q.put(("error", f"{type(exc).__name__}: {exc}"))
    finally:
        db.dispose()


def _duplicate_worker(url: str, key: str, q) -> None:
    db = Database(url)
    db.create_schema()
    store = PersistentEventStore(db)
    try:
        ev = store.append(
            event_type="run.started",
            server_workspace_id="ws1",
            producer_id="dup-producer",
            producer_instance_id="dup-inst",
            idempotency_key=key,
            payload={"run_id": "run_dup"},
        )
        q.put(("ok", ev.event_id))
    except Exception as exc:  # noqa: BLE001
        q.put(("error", f"{type(exc).__name__}: {exc}"))
    finally:
        db.dispose()


@pytest.fixture()
def db_url(work_tmp):  # type: ignore[no-untyped-def]
    import uuid

    base = work_tmp / f"evt-{uuid.uuid4().hex[:8]}"
    base.mkdir(parents=True, exist_ok=True)
    url = f"sqlite:///{base / 'events.db'}"
    seed = Database(url)
    seed.create_schema()
    seed.dispose()
    return url


def test_multiprocess_sequence_allocation_is_unique(db_url: str) -> None:
    ctx = mp.get_context("fork")
    q = ctx.Queue()
    procs = [
        ctx.Process(target=_append_worker, args=(db_url, "inst-shared", "worker-1", i * 25, 25, q))
        for i in range(4)
    ]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=90)
    results = [q.get(timeout=10) for _ in range(4)]
    assert all(r[0] == "ok" for r in results), results

    db = Database(db_url)
    store = PersistentEventStore(db)
    events = store.list_events(limit=1000)
    assert len(events) == 100
    seqs = sorted(e.sequence for e in events)
    assert seqs == list(range(1, 101))  # 无重复、无丢失
    assert store.detect_gaps("inst-shared") == []
    db.dispose()


def test_concurrent_duplicate_delivery_returns_same_event(db_url: str) -> None:
    ctx = mp.get_context("fork")
    q = ctx.Queue()
    procs = [ctx.Process(target=_duplicate_worker, args=(db_url, "same-key", q)) for _ in range(4)]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=90)
    results = [q.get(timeout=10) for _ in range(4)]
    assert all(r[0] == "ok" for r in results), results
    event_ids = {r[1] for r in results}
    assert len(event_ids) == 1  # 幂等：同一事件

    db = Database(db_url)
    store = PersistentEventStore(db)
    assert store.count_all() == 1
    db.dispose()


def test_same_key_different_payload_conflicts_then_recovers(db_url: str) -> None:
    db = Database(db_url)
    store = PersistentEventStore(db)
    args = {
        "event_type": "run.started",
        "server_workspace_id": "ws1",
        "producer_id": "p1",
        "producer_instance_id": "i1",
        "idempotency_key": "k1",
    }
    first = store.append(**args, payload={"run_id": "r1"})
    with pytest.raises(AppError) as exc:
        store.append(**args, payload={"run_id": "DIFFERENT"})
    assert exc.value.code.value == "CONFLICT"
    assert store.conflicts(), "冲突必须留审计记录"
    # 失败后可恢复：换新键继续写入，序号继续递增
    second = store.append(
        event_type="run.completed",
        server_workspace_id="ws1",
        producer_id="p1",
        producer_instance_id="i1",
        idempotency_key="k2",
        payload={"run_id": "r1"},
    )
    assert second.sequence == first.sequence + 1
    assert store.count_all() == 2
    db.dispose()


def test_duplicate_append_is_idempotent_in_process(db_url: str) -> None:
    db = Database(db_url)
    store = PersistentEventStore(db)
    args = {
        "event_type": "run.started",
        "server_workspace_id": "ws1",
        "producer_id": "p1",
        "producer_instance_id": "i1",
        "idempotency_key": "k1",
        "payload": {"run_id": "r1"},
    }
    a = store.append(**args)
    b = store.append(**args)
    assert a.event_id == b.event_id and a.sequence == b.sequence
    assert store.count_all() == 1
    db.dispose()
