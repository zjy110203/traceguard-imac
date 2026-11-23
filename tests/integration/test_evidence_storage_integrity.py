"""RUN-007-FINALIZATION-2（WP2）：Evidence 持久化完整性真实验证。

重点：
1. 摘要语义分离：原始来源摘要 vs 持久化内容摘要 vs 附件摘要；
2. 服务端验证读取持久化记录与真实内容、附件字节重算；
3. 数据库重启后仍能复验（不是拿内存对象比对）；
4. 内容/附件被篡改必须 FAIL；
5. 派生证据来源引用完整性。
"""

from __future__ import annotations

import uuid

import pytest
from traceguard_api.runtime import Runtime
from traceguard_persistence import Database
from traceguard_persistence.models import EvidenceRow


@pytest.fixture()
def env(work_tmp, monkeypatch):  # type: ignore[no-untyped-def]
    base = work_tmp / f"evidence-{uuid.uuid4().hex[:8]}"
    base.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("TG_ARTIFACT_DIR", str(base / "artifacts"))
    from traceguard_core.config import reset_settings_cache

    reset_settings_cache()
    url = f"sqlite:///{base / 'evidence.db'}"
    db = Database(url)
    rt = Runtime(db)
    yield rt, url, base
    db.dispose()


def _add_with_attachment(rt: Runtime) -> tuple[str, dict]:
    art = rt.artifacts.put(
        b"\x89PNG\r\n\x1a\nreal-screenshot-bytes",
        workspace_id="default",
        media_type="image/png",
        original_name="shot.png",
    )
    evd = rt.evidence.add_raw(
        "evd_shot",
        "browser",
        "screenshot:s1",
        "default",
        {"run_id": "run_1", "artifact": art, "token": "secret-token"},
        attachment_ref=art["artifact_id"],
        attachment_sha256=art["sha256"],
        run_id="run_1",
    )
    return evd.evidence_id, art


def test_digests_are_semantically_separated(env) -> None:  # type: ignore[no-untyped-def]
    rt, _, _ = env
    evd_id, art = _add_with_attachment(rt)
    rec = rt.evidence.get(evd_id)
    assert rec is not None
    # 原始来源摘要 ≠ 持久化内容摘要（因为脱敏改变了内容）
    assert rec.sha256 and rec.stored_content_sha256 and rec.sha256 != rec.stored_content_sha256
    assert rec.redacted is True
    assert rec.content["token"] == "***REDACTED***"
    assert rec.attachment_sha256 == art["sha256"]


def test_storage_verify_passes_and_survives_restart(env) -> None:  # type: ignore[no-untyped-def]
    rt, url, _ = env
    evd_id, _ = _add_with_attachment(rt)
    first = rt.evidence.verify_storage_integrity(
        evd_id, expected_workspace="default", artifacts=rt.artifacts
    )
    assert first["status"] == "PASS", first
    assert first["checks"]["stored_content_match"] is True
    assert first["checks"]["attachment_sha256_match"] is True

    # 进程/数据库重启：重新打开同一个库与产物目录，必须仍能证明完整性
    db2 = Database(url)
    try:
        rt2 = Runtime(db2)
        again = rt2.evidence.verify_storage_integrity(
            evd_id, expected_workspace="default", artifacts=rt2.artifacts
        )
        assert again["status"] == "PASS", again
        assert again["stored_content_sha256"] == rec_sha(rt2, evd_id)
        row = rt2.evidence.get(evd_id)
        assert row is not None and row.integrity_check == "PASS"
    finally:
        db2.dispose()


def rec_sha(rt: Runtime, evidence_id: str) -> str:
    rec = rt.evidence.get(evidence_id)
    assert rec is not None
    return rec.stored_content_sha256


def test_storage_verify_detects_content_tampering(env) -> None:  # type: ignore[no-untyped-def]
    rt, _, _ = env
    evd_id, _ = _add_with_attachment(rt)
    with rt.db.session() as s:
        row = s.get(EvidenceRow, evd_id)
        assert row is not None
        doc = dict(row.doc or {})
        content = dict(doc["content"])
        content["run_id"] = "run_tampered"
        doc["content"] = content
        row.doc = doc
    result = rt.evidence.verify_storage_integrity(evd_id, artifacts=rt.artifacts)
    assert result["status"] == "FAIL"
    assert "STORED_CONTENT_MISMATCH" in result["reasons"]


def test_storage_verify_detects_attachment_tampering(env) -> None:  # type: ignore[no-untyped-def]
    rt, _, _ = env
    evd_id, art = _add_with_attachment(rt)
    # 直接改产物目录里的字节（模拟磁盘篡改）
    target = rt.artifacts.root / art["sha256"][:2] / art["sha256"]
    target.write_bytes(b"tampered-bytes")
    result = rt.evidence.verify_storage_integrity(evd_id, artifacts=rt.artifacts)
    assert result["status"] == "FAIL"
    assert "ATTACHMENT_TAMPERED" in result["reasons"]


def test_derived_evidence_source_integrity(env) -> None:  # type: ignore[no-untyped-def]
    rt, _, _ = env
    rt.evidence.add_raw("evd_raw", "browser", "s1", "default", {"dom": "<html/>"}, run_id="run_1")
    rt.evidence.add_derived(
        "evd_derived",
        "evd_raw",
        "default",
        {"summary": "banner missing"},
        derivation_rule="rule-v1",
        model_version="v1",
    )
    ok = rt.evidence.verify_storage_integrity("evd_derived", artifacts=rt.artifacts)
    assert ok["status"] == "PASS"
    assert ok["checks"]["derived_source_is_raw"] is True

    # 来源被删除 → 派生证据完整性必须 FAIL，而不是继续 PASS
    with rt.db.session() as s:
        row = s.get(EvidenceRow, "evd_raw")
        assert row is not None
        s.delete(row)
    broken = rt.evidence.verify_storage_integrity("evd_derived", artifacts=rt.artifacts)
    assert broken["status"] == "FAIL"
    assert "DERIVED_SOURCE_EXISTS" in broken["reasons"]


def test_legacy_rows_are_not_run_not_pass(env) -> None:  # type: ignore[no-untyped-def]
    rt, _, _ = env
    evd_id, _ = _add_with_attachment(rt)
    # 模拟历史行：没有记录持久化内容摘要
    with rt.db.session() as s:
        row = s.get(EvidenceRow, evd_id)
        assert row is not None
        row.stored_content_sha256 = ""
        doc = dict(row.doc or {})
        doc.pop("stored_content_sha256", None)
        row.doc = doc
    result = rt.evidence.verify_storage_integrity(evd_id, artifacts=rt.artifacts)
    assert result["status"] == "NOT_RUN"
    assert "STORED_DIGEST_NOT_RECORDED_AT_CAPTURE" in result["reasons"]
