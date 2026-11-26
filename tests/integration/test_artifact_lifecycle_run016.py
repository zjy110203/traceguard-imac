"""RUN-016 WP1：Artifact 生命周期、Fencing 登记约束与孤儿文件治理验收。

覆盖任务要求：
- 旧 Worker 失去租约后不得登记新的产物记录；
- 落盘文件与数据库登记一致（文件存在 + SHA-256 匹配）；
- 孤儿文件扫描可发现"未登记文件"与"无业务引用登记"；
- 回收默认 dry-run，真正执行时只处理超过保留期且确认无引用的对象；
- 回收移动而非删除（可回滚），并留下审计记录；
- 并发同哈希写入时，失败方不得删掉胜者已登记的文件。
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import update
from traceguard_core.errors import AppError
from traceguard_execution.tasks.service import TaskService
from traceguard_persistence import Database
from traceguard_persistence.catalog import CatalogService
from traceguard_persistence.models import ArtifactRow, RunRow
from traceguard_persistence.stores import ArtifactStore


@pytest.fixture()
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "artifacts.db"
    monkeypatch.setenv("TG_DATABASE_URL", f"sqlite:///{db_path}")
    from traceguard_core.config import reset_settings_cache

    reset_settings_cache()
    db = Database(f"sqlite:///{db_path}")
    db.create_schema()
    root = tmp_path / "artifacts"
    store = ArtifactStore(db, root=str(root))
    catalog = CatalogService(db)
    tasks = TaskService(db)
    tc = f"tcase_art16_{uuid.uuid4().hex[:8]}"
    catalog.create_testcase(
        {
            "testcase_id": tc,
            "version": 1,
            "workspace_id": "default",
            "created_at": "2026-01-01T00:00:00Z",
            "steps": [{"step_id": "s1", "action": "navigate", "value": "/"}],
            "assertions": [
                {
                    "assertion_id": "a1",
                    "description": "标题存在",
                    "kind": "ui",
                    "required": True,
                    "expected": {"test_id": "page-title"},
                }
            ],
            "compile_source": "human",
        },
        title="artifact-lifecycle",
        review_status="APPROVED",
    )
    task = tasks.create_task(
        tc, "artifact-lifecycle", testcase_version_id=f"{tc}@1", workspace_id="default"
    )
    run_id = tasks.create_run(task["task_id"], f"{tc}@1")["run_id"]
    lease = tasks.acquire_lease(run_id, "worker-A", ttl_seconds=60)
    yield {
        "db": db,
        "store": store,
        "root": root,
        "tasks": tasks,
        "run_id": run_id,
        "worker": "worker-A",
        "token": int(lease["fencing_token"]),
    }
    db.dispose()
    reset_settings_cache()


def _evidence_ref(env, artifact_id: str) -> str:
    """写入一条引用该产物的 Evidence（模拟管道落证据）。"""
    from traceguard_persistence.stores import PersistentEvidenceManager

    ev = PersistentEvidenceManager(env["db"]).add_raw(
        evidence_id=f"evi_{uuid.uuid4().hex[:12]}",
        source_type="browser",
        source_ref="screenshot",
        workspace_id="default",
        content={"artifact": {"artifact_id": artifact_id}},
        run_id=env["run_id"],
        attachment_ref=artifact_id,
    )
    return ev.evidence_id


def _expire_lease(env) -> None:
    with env["db"].session() as s:
        s.execute(
            update(RunRow)
            .where(RunRow.run_id == env["run_id"])
            .values(lease_expires_at=time.time() - 1)
        )


def _backdate_row(env, artifact_id: str, *, days: int) -> None:
    old_iso = (datetime.now(UTC) - timedelta(days=days)).isoformat().replace("+00:00", "Z")
    with env["db"].session() as s:
        s.execute(
            update(ArtifactRow)
            .where(ArtifactRow.artifact_id == artifact_id)
            .values(created_at=old_iso)
        )


def test_put_with_valid_fence_registers_and_is_consistent(env) -> None:
    meta = env["store"].put(
        b"hello-artifact",
        workspace_id="default",
        fence=(env["worker"], env["token"]),
        run_id=env["run_id"],
    )
    assert meta["artifact_id"].startswith("art_")
    report = env["store"].verify_consistency()
    assert report["checked"] == 1
    assert report["consistent"] is True


def test_put_rejected_after_lease_lost_leaves_no_orphan_file(env) -> None:
    _expire_lease(env)
    payload = b"lost-lease-payload"
    with pytest.raises(AppError) as exc:
        env["store"].put(
            payload,
            workspace_id="default",
            fence=(env["worker"], env["token"]),
            run_id=env["run_id"],
        )
    assert "LEASE_LOST" in str(exc.value)
    # 登记被拒绝 → 本次新建的文件必须被回滚，磁盘上不残留孤儿
    digest_files = list(env["root"].glob("*/*"))
    assert digest_files == []
    assert env["store"].verify_consistency()["checked"] == 0


def test_put_without_fence_still_allowed_for_api_side(env) -> None:
    """API 侧（如手动上传）不持有租约，保持原有可用性。"""
    meta = env["store"].put(b"api-side", workspace_id="default")
    assert env["store"].get_meta(meta["artifact_id"]) is not None


def test_consistency_detects_hash_mismatch(env) -> None:
    meta = env["store"].put(b"original-bytes", workspace_id="default")
    path = env["root"] / meta["sha256"][:2] / meta["sha256"]
    path.write_bytes(b"tampered-bytes")
    report = env["store"].verify_consistency()
    assert report["consistent"] is False
    assert report["hash_mismatch"][0]["artifact_id"] == meta["artifact_id"]


def test_consistency_detects_missing_file(env) -> None:
    meta = env["store"].put(b"will-be-removed", workspace_id="default")
    (env["root"] / meta["sha256"][:2] / meta["sha256"]).unlink()
    report = env["store"].verify_consistency()
    assert report["missing"][0]["artifact_id"] == meta["artifact_id"]


def test_scan_separates_orphans_from_referenced_and_fresh(env) -> None:
    referenced = env["store"].put(b"referenced-bytes", workspace_id="default")
    _evidence_ref(env, referenced["artifact_id"])
    orphan = env["store"].put(b"orphan-bytes", workspace_id="default")
    # 未登记文件：直接写到内容寻址目录
    stray = b"stray-file-written-by-old-worker"
    import hashlib

    digest = hashlib.sha256(stray).hexdigest()
    stray_path = env["root"] / digest[:2] / digest
    stray_path.parent.mkdir(parents=True, exist_ok=True)
    stray_path.write_bytes(stray)
    old = time.time() - 30 * 86400
    for path in (stray_path, env["root"] / orphan["sha256"][:2] / orphan["sha256"]):
        import os

        os.utime(path, (old, old))
    _backdate_row(env, orphan["artifact_id"], days=30)

    scan = env["store"].scan(retention_days=7.0)
    orphan_shas = {item["sha256"] for item in scan["orphan_files"]}
    assert digest in orphan_shas
    orphan_rows = {item["artifact_id"] for item in scan["orphan_rows"]}
    assert orphan["artifact_id"] in orphan_rows
    # 被 Evidence 引用的产物既不在孤儿文件里，也不在孤儿登记里
    assert referenced["sha256"] not in orphan_shas
    assert referenced["artifact_id"] not in orphan_rows


def test_scan_protects_objects_within_retention(env) -> None:
    fresh = env["store"].put(b"fresh-orphan", workspace_id="default")
    scan = env["store"].scan(retention_days=7.0)
    assert fresh["artifact_id"] not in {i["artifact_id"] for i in scan["orphan_rows"]}
    assert any(i.get("artifact_id") == fresh["artifact_id"] for i in scan["protected_fresh"])


def test_gc_dry_run_deletes_nothing(env) -> None:
    meta = env["store"].put(b"dry-run-bytes", workspace_id="default")
    import os

    old = time.time() - 30 * 86400
    path = env["root"] / meta["sha256"][:2] / meta["sha256"]
    os.utime(path, (old, old))
    scan = env["store"].scan(retention_days=7.0)
    result = env["store"].collect_garbage(scan, apply=False)
    assert result["dry_run"] is True
    assert path.is_file()
    assert env["store"].get_meta(meta["artifact_id"]) is not None


def test_gc_apply_quarantines_and_audits(env) -> None:
    meta = env["store"].put(b"gc-bytes", workspace_id="default")
    import os

    old = time.time() - 30 * 86400
    path = env["root"] / meta["sha256"][:2] / meta["sha256"]
    os.utime(path, (old, old))
    # created_at 必须也超过保留期，否则只会受"新鲜登记"保护
    _backdate_row(env, meta["artifact_id"], days=30)
    scan = env["store"].scan(retention_days=7.0)
    result = env["store"].collect_garbage(scan, apply=True)
    assert path.is_file() is False
    quarantine = Path(result["quarantine_dir"])
    assert (quarantine / meta["sha256"]).is_file()
    assert env["store"].get_meta(meta["artifact_id"]) is None
    audit = Path(result["audit_path"])
    lines = [json.loads(x) for x in audit.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert any(
        x["op"] == "delete_row"
        and x["sha256"] == meta["sha256"]
        and x.get("quarantine_path")
        and x["result"] == "deleted"
        for x in lines
    )


def test_gc_apply_keeps_referenced_artifact(env) -> None:
    meta = env["store"].put(b"still-referenced", workspace_id="default")
    _evidence_ref(env, meta["artifact_id"])
    import os

    old = time.time() - 30 * 86400
    path = env["root"] / meta["sha256"][:2] / meta["sha256"]
    os.utime(path, (old, old))
    scan = env["store"].scan(retention_days=7.0)
    assert meta["artifact_id"] not in {i["artifact_id"] for i in scan["orphan_rows"]}
    env["store"].collect_garbage(scan, apply=True)
    assert path.is_file()
    assert env["store"].get_meta(meta["artifact_id"]) is not None


def test_stale_writer_does_not_delete_registered_file(env, tmp_path: Path) -> None:
    """并发同哈希：胜者已登记，失败者（租约失效）不得删掉这份文件。"""
    payload = b"shared-bytes"
    first = env["store"].put(payload, workspace_id="default")
    _expire_lease(env)
    with pytest.raises(AppError):
        env["store"].put(
            payload,
            workspace_id="default",
            fence=(env["worker"], env["token"]),
            run_id=env["run_id"],
        )
    path = env["root"] / first["sha256"][:2] / first["sha256"]
    assert path.is_file(), "已登记的产物不得被失败方回滚删除"
    assert env["store"].get_meta(first["artifact_id"]) is not None


def test_scan_protects_artifact_referenced_only_by_run_report(env) -> None:
    """trace_artifact 只出现在 Run 报告里，不属于任何 Evidence——不得被判为孤儿。

    RUN-016 实测：长稳 729 个产物中有 181 个只被 `report_json` 引用；
    旧扫描口径会把它们在保留期后误删。
    """
    meta = env["store"].put(b"trace-zip-bytes", workspace_id="default")
    with env["db"].session() as s:
        s.execute(
            update(RunRow)
            .where(RunRow.run_id == env["run_id"])
            .values(
                report_json={
                    "run_id": env["run_id"],
                    "trace_artifact": {"artifact_id": meta["artifact_id"]},
                }
            )
        )
    import os

    old = time.time() - 30 * 86400
    path = env["root"] / meta["sha256"][:2] / meta["sha256"]
    os.utime(path, (old, old))
    _backdate_row(env, meta["artifact_id"], days=30)
    scan = env["store"].scan(retention_days=7.0)
    assert meta["artifact_id"] not in {i["artifact_id"] for i in scan["orphan_rows"]}
    assert meta["sha256"] not in {i["sha256"] for i in scan["orphan_files"]}


# ---------------------------------------------------------------------------
# RUN-017 WP4.2 / WP4.3 追加
# ---------------------------------------------------------------------------
def test_unsupported_artifact_backend_fails_fast(work_tmp) -> None:  # type: ignore[no-untyped-def]
    """配置要求 s3 时必须启动即失败，不能静默用本地目录冒充对象存储。"""
    from traceguard_core.errors import AppError
    from traceguard_persistence import ArtifactStore, Database

    db = Database(f"sqlite:///{work_tmp / 'backend.db'}")
    db.create_schema()
    try:
        with pytest.raises(AppError) as exc:
            ArtifactStore(db, root=work_tmp / "art", backend="s3")
        assert "ARTIFACT_BACKEND_UNSUPPORTED" in str(exc.value)
    finally:
        db.dispose()


def test_gc_skips_artifact_referenced_after_scan(work_tmp) -> None:  # type: ignore[no-untyped-def]
    """扫描快照已过期（之后新增引用）时，删除前的定向复核必须拦住。"""
    from traceguard_persistence import ArtifactStore, Database
    from traceguard_persistence.models import EvidenceRow

    db = Database(f"sqlite:///{work_tmp / 'gc-race.db'}")
    db.create_schema()
    store = ArtifactStore(db, root=work_tmp / "art")
    try:
        saved = store.put(b"payload", workspace_id="default", original_name="x.txt")
        with db.session() as s:
            row = s.get(ArtifactRow, saved["artifact_id"])
            row.created_at = "2020-01-01T00:00:00Z"
        scan = store.scan(retention_days=7.0)
        assert [i["artifact_id"] for i in scan["orphan_rows"]] == [saved["artifact_id"]]
        # 新增引用后，把"过期快照"直接喂给 collect_garbage（模拟扫描与回收之间的窗口）
        with db.session() as s:
            s.add(
                EvidenceRow(
                    evidence_id="evd_late_ref",
                    workspace_id="default",
                    run_id=None,
                    kind="snapshot",
                    source_type="pipeline",
                    sha256="0" * 64,
                    stored_content_sha256="0" * 64,
                    attachment_sha256="",
                    attachment_ref=None,
                    source_trust="TRUSTED",
                    integrity_check="PASSED",
                    integrity_detail={},
                    redacted=False,
                    doc={"artifact": saved["artifact_id"]},
                    captured_at="2026-01-01T00:00:00Z",
                )
            )
        result = store.collect_garbage(scan, apply=True)
        done = {a.get("artifact_id"): a.get("result") for a in result["actions"]}
        skipped = {a.get("artifact_id") for a in result["skipped"]}
        assert done.get(saved["artifact_id"]) != "deleted"
        assert saved["artifact_id"] in skipped
        with db.session() as s:
            assert s.get(ArtifactRow, saved["artifact_id"]) is not None
        # 文件仍可读（没有被 quarantine）
        data, _meta = store.read(saved["artifact_id"])
        assert data == b"payload"
    finally:
        db.dispose()
