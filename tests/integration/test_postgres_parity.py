"""PostgreSQL 语义一致性测试（只读环境变量触发；本地无 PG 时自动跳过）。

本地默认环境没有 Docker/psql，因此真实 PG 验证在 GitHub Actions 的
PostgreSQL Service Container 中执行（见 .github/workflows/nightly-gate.yml）。

运行方式：
    TG_TEST_POSTGRES_URL=postgresql+psycopg://user:pass@127.0.0.1:5432/traceguard \\
      pytest tests/integration/test_postgres_parity.py -q
"""

from __future__ import annotations

import os
import threading
import uuid

import pytest
from traceguard_core.errors import AppError
from traceguard_persistence import Database, PersistentEventStore
from traceguard_persistence.models import DocRow
from traceguard_persistence.stores import DocStore, PersistentConsumerInbox

PG_URL = os.environ.get("TG_TEST_POSTGRES_URL", "")

pytestmark = pytest.mark.skipif(not PG_URL, reason="TG_TEST_POSTGRES_URL 未配置（本地无 PG）")


@pytest.fixture(scope="module")
def db():  # type: ignore[no-untyped-def]
    database = Database(PG_URL)
    database.create_schema()
    yield database
    database.dispose()


def _suffix() -> str:
    return uuid.uuid4().hex[:8]


def test_event_sequences_are_unique_under_concurrency(db: Database) -> None:
    store = PersistentEventStore(db)
    instance = f"pg-inst-{_suffix()}"
    errors: list[str] = []

    def worker(start: int) -> None:
        try:
            for i in range(start, start + 10):
                store.append(
                    event_type="step.completed",
                    server_workspace_id="ws1",
                    producer_id=f"pg-{instance}",
                    producer_instance_id=instance,
                    idempotency_key=f"{instance}:{i}",
                    payload={"i": i},
                    context_refs={"run": "run_pg"},
                )
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{type(exc).__name__}: {exc}")

    threads = [threading.Thread(target=worker, args=(i * 10,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors
    events = store.list_events(limit=1000)
    seqs = sorted(e.sequence for e in events if e.producer_instance_id == instance)
    assert len(seqs) == 40 and len(set(seqs)) == 40
    assert store.detect_gaps(instance) == []


def test_idempotency_and_payload_conflict(db: Database) -> None:
    store = PersistentEventStore(db)
    key = f"pg-key-{_suffix()}"
    args = {
        "event_type": "run.started",
        "server_workspace_id": "ws1",
        "producer_id": "pg-producer",
        "producer_instance_id": f"pg-{key}",
        "idempotency_key": key,
    }
    first = store.append(**args, payload={"run_id": "r1"})
    second = store.append(**args, payload={"run_id": "r1"})
    assert first.event_id == second.event_id
    with pytest.raises(AppError):
        store.append(**args, payload={"run_id": "r2"})
    assert store.conflicts()


def test_inbox_claim_is_atomic_across_connections() -> None:
    boxes = [PersistentConsumerInbox(Database(PG_URL)) for _ in range(3)]
    for box in boxes:
        box.db.create_schema()
    event_id = f"evt_pg_{_suffix()}"
    barrier = threading.Barrier(len(boxes))
    claimed: list[bool] = []
    lock = threading.Lock()

    def worker(box: PersistentConsumerInbox) -> None:
        barrier.wait()
        try:
            _, got = box.claim("pg-consumer", event_id, lease_seconds=30)
        except Exception:  # noqa: BLE001
            got = False
        with lock:
            claimed.append(got)

    threads = [threading.Thread(target=worker, args=(b,)) for b in boxes]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert claimed.count(True) == 1
    for box in boxes:
        box.db.dispose()


def test_doc_store_cas_conflict(db: Database) -> None:
    store = DocStore(db)
    doc_id = f"doc_{_suffix()}"
    store.put(doc_id=doc_id, kind="test", doc={"v": 1})
    updated = store.put(doc_id=doc_id, kind="test", doc={"v": 2}, version=1)
    assert updated.version == 2
    with pytest.raises(AppError):
        store.put(doc_id=doc_id, kind="test", doc={"v": 3}, version=1)


def test_doc_json_roundtrip_preserves_types(db: Database) -> None:
    store = DocStore(db)
    doc_id = f"doc_{_suffix()}"
    payload = {"n": 3, "flag": True, "nested": {"a": [1, 2, 3]}, "text": "中文"}
    store.put(doc_id=doc_id, kind="test", doc=payload)
    rec = store.get(doc_id)
    assert rec is not None and rec.doc == payload
    with db.session() as s:
        assert s.get(DocRow, doc_id) is not None
