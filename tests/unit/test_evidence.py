"""阶段4 Event/Evidence 基础设施测试（真实行为，不用 Mock 冒充）。"""

from __future__ import annotations

import pytest
from traceguard_core.errors import AppError
from traceguard_evidence.event_store import EventStore
from traceguard_evidence.evidence_manager import EvidenceManager, _content_hash
from traceguard_evidence.inbox import ConsumerInbox


# ---------------- Event Store ----------------
def _ev(
    store: EventStore, iid: str, key: str, payload: dict | None = None, ws: str = "ws-1"
) -> object:  # type: ignore[no-untyped-def]
    return store.append(
        event_type="test.event",
        server_workspace_id=ws,
        producer_id="runner",
        producer_instance_id=iid,
        idempotency_key=key,
        payload=payload or {"v": 1},
        context_refs={"run_id": "run_1"},
    )


def test_event_workspace_injected_and_payload_cannot_override() -> None:
    store = EventStore()
    iid = store.new_producer_instance("runner")
    ev = store.append(
        event_type="x",
        server_workspace_id="trusted-ws",
        producer_id="runner",
        producer_instance_id=iid,
        idempotency_key="k1",
        payload={"workspace_id": "evil-ws", "data": "ok"},
    )
    assert ev.workspace_id == "trusted-ws"
    assert "workspace_id" not in ev.payload  # payload 中的伪造 ws 被剥离


def test_event_idempotent_replay() -> None:
    store = EventStore()
    iid = store.new_producer_instance("runner")
    e1 = _ev(store, iid, "idem-1")
    e2 = _ev(store, iid, "idem-1")
    assert e1.event_id == e2.event_id  # 重复投递返回同一事件
    assert len(store.list_events()) == 1


def test_event_same_key_different_payload_conflict() -> None:
    store = EventStore()
    iid = store.new_producer_instance("runner")
    _ev(store, iid, "k1", {"v": 1})
    with pytest.raises(AppError) as e:
        _ev(store, iid, "k1", {"v": 2})
    assert e.value.code.value == "CONFLICT"
    assert len(store.conflicts()) == 1
    assert len(store.list_events()) == 1  # 原事件未被覆盖


def test_event_sequence_per_instance_and_restart() -> None:
    store = EventStore()
    iid1 = store.new_producer_instance("runner")
    e1 = _ev(store, iid1, "k1")
    e2 = _ev(store, iid1, "k2")
    assert e1.sequence == 1 and e2.sequence == 2
    # 重启：新实例，序列空间重新计数
    iid2 = store.new_producer_instance("runner")
    e3 = _ev(store, iid2, "k3")
    assert e3.sequence == 1
    assert e3.producer_instance_id != e1.producer_instance_id


def test_event_gap_detected_but_marked_unconfirmed() -> None:
    store = EventStore()
    iid = store.new_producer_instance("runner")
    _ev(store, iid, "k1")
    _ev(store, iid, "k2")
    # 手动注入跳号事件（模拟合法跳号或外部序列），sequence=5
    store._seq_index[iid] = 4
    e3 = _ev(store, iid, "k3")
    assert e3.sequence == 5
    gaps = store.detect_gaps(iid)
    assert 3 in gaps and 4 in gaps  # 检测到缺口，但不判"永久丢失"


def test_event_context_refs_are_associations_not_causality() -> None:
    store = EventStore()
    iid = store.new_producer_instance("runner")
    e = store.append(
        event_type="x",
        server_workspace_id="ws",
        producer_id="p",
        producer_instance_id=iid,
        idempotency_key="k",
        context_refs={"run_id": "r1", "trace_id": "t1", "request_id": "req1"},
    )
    assert e.context_refs["run_id"] == "r1"
    # 语义上 context_refs 仅关联；store 不生成任何 direct_causal 关系


# ---------------- Consumer Inbox ----------------
def test_inbox_idempotent_same_consumer() -> None:
    inbox = ConsumerInbox()
    calls = []

    def handler() -> str:
        calls.append(1)
        return "done"

    r1 = inbox.consume("c1", "evt1", handler)
    r2 = inbox.consume("c1", "evt1", handler)
    assert r1.status == "SUCCEEDED" and r2.status == "SUCCEEDED"
    assert len(calls) == 1  # 副作用只执行一次


def test_inbox_multiple_consumers_independent() -> None:
    inbox = ConsumerInbox()
    inbox.consume("c1", "evt1", lambda: "a")
    inbox.consume("c2", "evt1", lambda: "b")
    recs = inbox.list_for_event("evt1")
    assert len(recs) == 2
    assert {r.consumer_id for r in recs} == {"c1", "c2"}


def test_inbox_failure_no_false_success() -> None:
    inbox = ConsumerInbox()

    def bad() -> None:
        raise RuntimeError("boom")

    r = inbox.consume("c1", "evt1", bad)
    assert r.status == "FAILED"
    assert "boom" in r.result


def test_inbox_external_side_effect_not_blindly_retried() -> None:
    inbox = ConsumerInbox()
    calls = []

    def flaky() -> str:
        calls.append(1)
        raise RuntimeError("external uncertain")

    inbox.consume("c1", "evt1", flaky, external_side_effect=True)
    inbox.consume("c1", "evt1", flaky, external_side_effect=True)  # 不应重试
    assert len(calls) == 1


# ---------------- Evidence Manager ----------------
def test_evidence_raw_hash_and_integrity() -> None:
    em = EvidenceManager()
    content = {"text": "screenshot-data", "token": "secret123"}
    rec = em.add_raw("evd1", "browser", "step_1", "ws-1", content, do_redact=False)
    assert len(rec.sha256) == 64
    assert rec.stored_content_sha256 == rec.sha256  # 未脱敏时两者一致
    assert em.verify_integrity("evd1", content) is True
    assert em.get("evd1").integrity_detail["source_payload_match"] == "PASS"
    storage = em.verify_storage_integrity("evd1", expected_workspace="ws-1")
    assert storage["status"] == "PASS"
    assert em.get("evd1").integrity_check == "PASS"
    assert em.verify_integrity("evd1", {"text": "tampered"}) is False


def test_evidence_redaction_and_hash_based_on_original() -> None:
    em = EvidenceManager()
    original = {"password": "hunter2", "msg": "ok"}
    rec = em.add_raw("evd1", "api", "req_1", "ws-1", original, do_redact=True)
    assert rec.redacted is True
    assert rec.content["password"] == "***REDACTED***"
    # 来源摘要基于脱敏前原始内容
    assert rec.sha256 == _content_hash(original)
    assert em.verify_integrity("evd1", original) is True
    # 持久化内容摘要基于脱敏后内容：两者必须不同，不能混用
    assert rec.stored_content_sha256 != rec.sha256
    assert em.verify_storage_integrity("evd1")["status"] == "PASS"


def test_storage_integrity_detects_tampered_persisted_content() -> None:
    em = EvidenceManager()
    em.add_raw("evd1", "api", "req_1", "ws-1", {"v": 1}, do_redact=False)
    rec = em.get("evd1")
    assert rec is not None
    rec.content = {"v": 2}  # 模拟库内内容被改动
    result = em.verify_storage_integrity("evd1")
    assert result["status"] == "FAIL"
    assert "STORED_CONTENT_MISMATCH" in result["reasons"]


def test_storage_integrity_not_run_for_legacy_rows() -> None:
    em = EvidenceManager()
    rec = em.add_raw("evd1", "api", "req_1", "ws-1", {"v": 1}, do_redact=False)
    rec.stored_content_sha256 = ""  # 历史行没有记录持久化摘要
    result = em.verify_storage_integrity("evd1")
    assert result["status"] == "NOT_RUN"
    assert "STORED_DIGEST_NOT_RECORDED_AT_CAPTURE" in result["reason"]


def test_storage_integrity_checks_workspace_ownership() -> None:
    em = EvidenceManager()
    em.add_raw("evd1", "api", "req_1", "ws-1", {"v": 1})
    result = em.verify_storage_integrity("evd1", expected_workspace="ws-2")
    assert result["status"] == "FAIL"
    assert result["checks"]["workspace_match"] is False


def test_source_check_alone_does_not_claim_storage_integrity() -> None:
    """来源内容比对 ≠ 存储完整性：不能只凭调用者提交内容就给 PASS。"""
    em = EvidenceManager()
    em.add_raw("evd1", "api", "req_1", "ws-1", {"v": 1}, do_redact=False)
    assert em.verify_integrity("evd1", {"v": 1}) is True
    assert em.get("evd1").integrity_check == "NOT_RUN"


def test_evidence_integrity_and_trust_are_independent() -> None:
    em = EvidenceManager()
    rec = em.add_raw("evd1", "agent", "a1", "ws-1", {"x": 1}, source_trust="UNTRUSTED")
    em.verify_storage_integrity("evd1")
    assert rec.integrity_check == "PASS"  # 完整性通过
    assert rec.source_trust == "UNTRUSTED"  # 但来源不可信（独立维度）


def test_derived_evidence_must_reference_raw() -> None:
    em = EvidenceManager()
    em.add_raw("evd_raw", "browser", "s1", "ws-1", {"dom": "..."})
    d = em.add_derived(
        "evd_derived",
        "evd_raw",
        "ws-1",
        {"summary": "x"},
        derivation_rule="rule-v1",
        model_version="model-1",
    )
    assert d.kind == "derived"
    assert d.raw_ref == "evd_raw"
    assert d.derivation_rule == "rule-v1" and d.model_version == "model-1"
    # 派生证据不能冒充原始
    assert d.kind != "raw"
    result = em.verify_storage_integrity("evd_derived")
    assert result["status"] == "PASS"
    assert result["checks"]["derived_source_is_raw"] is True


def test_derived_from_non_raw_rejected() -> None:
    em = EvidenceManager()
    em.add_raw("r1", "browser", "s1", "ws-1", {"x": 1})
    em.add_derived("d1", "r1", "ws-1", {"y": 2}, derivation_rule="rule")
    with pytest.raises(AppError):
        em.add_derived("d2", "d1", "ws-1", {"z": 3}, derivation_rule="rule")


def test_evidence_id_immutable() -> None:
    em = EvidenceManager()
    em.add_raw("evd1", "api", "r1", "ws-1", {"x": 1})
    with pytest.raises(AppError) as e:
        em.add_raw("evd1", "api", "r2", "ws-1", {"x": 2})
    assert e.value.code.value == "CONFLICT"
