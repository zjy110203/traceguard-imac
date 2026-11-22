"""持久化层测试：事件幂等/序号/冲突、证据不可变、产物完整性、目录版本与 Oracle 审批。"""

from __future__ import annotations

import uuid

import pytest
from traceguard_core.errors import AppError
from traceguard_persistence import (
    ArtifactStore,
    CatalogService,
    Database,
    PersistentEventStore,
    PersistentEvidenceManager,
)
from traceguard_persistence.models import EventRow


@pytest.fixture()
def db(work_tmp):  # type: ignore[no-untyped-def]
    d = Database(f"sqlite:///{work_tmp / 'tg.db'}")
    d.create_schema()
    yield d
    d.dispose()


def _ev_args(store: PersistentEventStore, iid: str, key: str, payload: dict | None = None) -> dict:
    return {
        "event_type": "run.started",
        "server_workspace_id": "ws1",
        "producer_id": "worker-1",
        "producer_instance_id": iid,
        "idempotency_key": key,
        "payload": payload or {"run_id": "run_1"},
        "context_refs": {"run": "run_1"},
    }


def test_event_idempotent_and_conflict(db: Database) -> None:
    store = PersistentEventStore(db)
    iid = store.new_producer_instance("worker-1")
    a = store.append(**_ev_args(store, iid, "k1"))
    b = store.append(**_ev_args(store, iid, "k1"))
    assert a.event_id == b.event_id
    with pytest.raises(AppError) as exc:
        store.append(**_ev_args(store, iid, "k1", {"run_id": "run_2"}))
    assert "IDEMPOTENCY_KEY_PAYLOAD_CONFLICT" in str(exc.value.message)
    assert len(store.conflicts()) == 1


def test_event_sequence_unique_per_instance(db: Database) -> None:
    store = PersistentEventStore(db)
    iid = store.new_producer_instance("worker-1")
    e1 = store.append(**_ev_args(store, iid, "s1"))
    e2 = store.append(**_ev_args(store, iid, "s2"))
    assert (e1.sequence, e2.sequence) == (1, 2)
    # 重启换新实例，序列空间重新计数
    iid2 = store.new_producer_instance("worker-1")
    e3 = store.append(**_ev_args(store, iid2, "s3"))
    assert e3.sequence == 1
    assert store.detect_gaps(iid) == []


def test_events_survive_restart(work_tmp) -> None:  # type: ignore[no-untyped-def]
    url = f"sqlite:///{work_tmp / 'persist.db'}"
    db1 = Database(url)
    db1.create_schema()
    store1 = PersistentEventStore(db1)
    iid = store1.new_producer_instance("w")
    ev = store1.append(**_ev_args(store1, iid, "restart-1"))
    db1.dispose()

    db2 = Database(url)
    db2.create_schema()
    store2 = PersistentEventStore(db2)
    got = store2.get(ev.event_id)
    assert got is not None and got.sequence == 1
    store2.append(**_ev_args(store2, iid, "restart-2"))
    assert sorted(e.sequence for e in store2.list_events("ws1")) == [1, 2]
    db2.dispose()


def test_sequence_unique_constraint_enforced(db: Database) -> None:
    """绕过应用层直接插入重复序号，数据库必须拒绝。"""
    from sqlalchemy.exc import IntegrityError

    store = PersistentEventStore(db)
    iid = store.new_producer_instance("w")
    store.append(**_ev_args(store, iid, "uq-1"))
    s = db.session_factory()
    try:
        s.add(
            EventRow(
                event_id="evt_manual",
                event_type="x",
                schema_version="0.1.0",
                workspace_id="ws1",
                context_refs={},
                producer_id="other",
                producer_instance_id=iid,
                sequence=1,
                occurred_at="now",
                observed_at="now",
                idempotency_key="manual",
                payload={},
                payload_hash="h",
            )
        )
        with pytest.raises(IntegrityError):
            s.commit()
    finally:
        s.rollback()
        s.close()


def test_evidence_immutable_and_derived(db: Database) -> None:
    em = PersistentEvidenceManager(db)
    eid = "evd_" + uuid.uuid4().hex[:8]
    em.add_raw(eid, "browser", "navigate", "ws1", {"text": "hello", "password": "p@ss"})
    with pytest.raises(AppError):
        em.add_raw(eid, "browser", "again", "ws1", {"text": "x"})
    rec = em.get(eid)
    assert rec is not None and rec.kind == "raw"
    assert rec.content["password"] == "***REDACTED***"  # 脱敏后入库
    derived = em.add_derived(
        "evd_" + uuid.uuid4().hex[:8], eid, "ws1", {"summary": "ok"}, derivation_rule="rule:count"
    )
    assert derived.raw_ref == eid
    assert em.verify_integrity(eid, {"text": "hello", "password": "p@ss"}) is True
    assert em.verify_integrity(eid, {"text": "tampered"}) is False
    assert em.get(eid).integrity_check == "FAIL"


def test_evidence_survives_restart(work_tmp) -> None:  # type: ignore[no-untyped-def]
    url = f"sqlite:///{work_tmp / 'ev.db'}"
    db1 = Database(url)
    db1.create_schema()
    PersistentEvidenceManager(db1).add_raw(
        "evd_restart1", "browser", "step", "ws1", {"a": 1}, run_id="run_1"
    )
    db1.dispose()
    db2 = Database(url)
    db2.create_schema()
    rec = PersistentEvidenceManager(db2).get("evd_restart1")
    assert rec is not None and rec.content["a"] == 1
    assert rec.run_id == "run_1"
    db2.dispose()


def test_artifact_integrity(db: Database, work_tmp) -> None:  # type: ignore[no-untyped-def]
    store = ArtifactStore(db, root=str(work_tmp / "art"))
    meta = store.put(
        b"hello-png", workspace_id="ws1", media_type="image/png", original_name="a.png"
    )
    data, read_meta = store.read(meta["artifact_id"])
    assert data == b"hello-png"
    assert read_meta["media_type"] == "image/png"
    # 篡改磁盘文件 → 读取必须报错，不返回被污染内容
    path = work_tmp / "art" / meta["sha256"][:2] / meta["sha256"]
    path.write_bytes(b"tampered")
    with pytest.raises(AppError) as exc:
        store.read(meta["artifact_id"])
    assert "integrity" in str(exc.value.message).lower()


def test_catalog_versioning_and_review(db: Database) -> None:
    cat = CatalogService(db)
    base = {
        "testcase_id": "tcase_demo",
        "version": 1,
        "steps": [
            {"step_id": "s1", "action": "navigate", "value": "/"},
            {"step_id": "s2", "action": "assert", "value": "x"},
        ],
        "assertions": [
            {"assertion_id": "a1", "description": "d", "kind": "ui", "expected": {"test_id": "x"}}
        ],
        "created_at": "2026-01-01T00:00:00Z",
    }
    cat.create_testcase(base, title="demo")
    v2 = cat.create_testcase(base, title="demo")
    assert v2["version"] == 2
    assert len(cat.list_versions("tcase_demo")) == 2
    # 只有 APPROVED 版本才能被正式执行路径取到
    assert cat.latest_approved_version("tcase_demo") is None
    with pytest.raises(AppError):
        cat.review_testcase("tcase_demo", 1, "APPROVED", reviewer="agent")
    cat.review_testcase("tcase_demo", 1, "APPROVED", reviewer="role:qa")
    assert cat.latest_approved_version("tcase_demo")["version"] == 1


def test_oracle_agent_cannot_approve(db: Database) -> None:
    cat = CatalogService(db)
    payload = {
        "oracle_id": "oracle_demo",
        "assertion_id": "a1",
        "expected_behavior": "必须成功",
        "requirement_ref": "REQ-1",
        "version": 1,
    }
    with pytest.raises(AppError):
        cat.create_oracle(payload, approved=True, approved_by="agent")
    doc = cat.create_oracle(payload, approved=True, approved_by="role:qa-lead")
    assert doc["version"] == 1
    assert cat.get_oracle("oracle_demo")["approved"] is True
    # 新版本自动递增，不覆盖历史
    doc2 = cat.create_oracle(payload, approved=True, approved_by="role:qa-lead")
    assert doc2["version"] == 2
