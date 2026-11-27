"""RUN-018 WP4：Artifact GC 并发安全测试。

场景 A（竞态 A — orphan_file）：
  GC 扫描发现旧 orphan file → Worker put() 写入同哈希内容并登记 ArtifactRow
  → GC 执行 apply → 新登记文件不能被错误隔离。

场景 B（竞态 B — orphan_row）：
  GC 扫描发现 orphan row → 新事务创建 Evidence 引用 → GC 同时回收
  → 不能出现已引用 Artifact 被删除。

使用线程 + Barrier 控制并发时序。
"""

from __future__ import annotations

import hashlib
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "packages" / "persistence"))

from traceguard_persistence.db import Database  # noqa: E402
from traceguard_persistence.models import ArtifactRow, EvidenceRow  # noqa: E402
from traceguard_persistence.stores import ArtifactStore  # noqa: E402


def _make_db(tmp_path: Path) -> Database:
    db_path = tmp_path / "test_gc.db"
    db = Database(f"sqlite:///{db_path}")
    db.create_schema()
    return db


def _make_store(tmp_path: Path) -> ArtifactStore:
    db = _make_db(tmp_path)
    root = tmp_path / "artifacts"
    return ArtifactStore(db, root=str(root))


def _write_artifact_file(store: ArtifactStore, data: bytes) -> str:
    """直接写文件到 store.root（模拟 put() 的落盘部分）。"""
    digest = hashlib.sha256(data).hexdigest()
    path = store.root / digest[:2] / digest
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return digest


def _register_row(store: ArtifactStore, digest: str) -> None:
    artifact_id = f"art_{digest[:20]}"
    path = store.root / digest[:2] / digest
    with store.db.session() as s:
        row = ArtifactRow(
            artifact_id=artifact_id,
            workspace_id="default",
            sha256=digest,
            size=path.stat().st_size if path.is_file() else 0,
            media_type="application/octet-stream",
            path=str(path),
        )
        s.add(row)


def _add_evidence_ref(store: ArtifactStore, artifact_id: str) -> None:
    with store.db.session() as s:
        ev = EvidenceRow(
            evidence_id=f"ev_test_{int(time.time() * 1000)}",
            workspace_id="default",
            run_id="run_test",
            kind="attachment",
            source_type="api",
            sha256="hash_test",
            attachment_ref=artifact_id,
            doc={"attachment_ref": artifact_id},
        )
        s.add(ev)


class TestGCConcurrencyA:
    """场景 A：GC 扫描后 Worker 登记新 ArtifactRow，GC 不能隔离文件。"""

    def test_new_registration_protected(self, tmp_path):
        """GC 发现 orphan file → Worker 登记 → GC apply 不能隔离。"""
        store = _make_store(tmp_path)
        data = b"concurrent-test-data-A"
        digest = _write_artifact_file(store, data)

        # 模拟 GC 扫描：发现文件不在登记表中（orphan file）
        # 手动构造扫描结果：把文件列为 orphan_files
        artifact_id = f"art_{digest[:20]}"
        scan_result = {
            "orphan_rows": [],
            "orphan_files": [
                {
                    "sha256": digest,
                    "path": str(store.root / digest[:2] / digest),
                    "mtime": time.time(),
                }
            ],
            "protected_fresh": [],
            "missing_files": [],
        }

        # 模拟 GC apply 阶段开始前的竞态窗口：
        # Worker put() 登记了 ArtifactRow
        _register_row(store, digest)

        # GC 执行 apply——必须检测到已登记，跳过隔离
        result = store.collect_garbage(scan_result, apply=True)
        actions = result.get("actions", [])

        # 文件不能被隔离
        for a in actions:
            if a.get("op") == "quarantine_file":
                assert a.get("result") == "skipped_referenced", (
                    f"新登记的 ArtifactRow 不应被隔离: {a}"
                )

        # 文件仍在原位
        file_path = store.root / digest[:2] / digest
        assert file_path.is_file(), "文件不应被移动"

        # ArtifactRow 仍在
        with store.db.session() as s:
            row = s.get(ArtifactRow, artifact_id)
            assert row is not None, "ArtifactRow 不应被删除"

    def test_thread_interleaved(self, tmp_path):
        """线程交错：GC apply 与 Worker put() 并发。"""
        store = _make_store(tmp_path)
        data = b"thread-interleave-test"
        digest = _write_artifact_file(store, data)

        scan_result = {
            "orphan_rows": [],
            "orphan_files": [
                {
                    "sha256": digest,
                    "path": str(store.root / digest[:2] / digest),
                    "mtime": time.time(),
                }
            ],
            "protected_fresh": [],
            "missing_files": [],
        }

        barrier = threading.Barrier(2, timeout=5)
        gc_result: dict = {}
        put_done = threading.Event()

        def _worker_put():
            barrier.wait()
            # Worker 登记 ArtifactRow
            _register_row(store, digest)
            put_done.set()

        def _gc_apply():
            barrier.wait()
            # 稍等 Worker 先完成登记
            put_done.wait(timeout=3)
            gc_result["result"] = store.collect_garbage(scan_result, apply=True)

        t1 = threading.Thread(target=_worker_put)
        t2 = threading.Thread(target=_gc_apply)
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

        # 文件不能被隔离
        file_path = store.root / digest[:2] / digest
        assert file_path.is_file(), "并发下文件不应被隔离"

        actions = gc_result.get("result", {}).get("actions", [])
        for a in actions:
            if a.get("op") == "quarantine_file":
                assert a.get("result") in ("skipped_referenced", "gone"), f"并发保护失效: {a}"


class TestGCConcurrencyB:
    """场景 B：GC 发现 orphan row → Evidence 引用创建 → 不能删除已引用行。"""

    def test_evidence_ref_protects_row(self, tmp_path):
        """GC 删除前创建 Evidence 引用 → 不应删除。"""
        store = _make_store(tmp_path)
        data = b"concurrent-test-data-B"
        digest = _write_artifact_file(store, data)
        _register_row(store, digest)
        artifact_id = f"art_{digest[:20]}"

        scan_result = {
            "orphan_rows": [
                {
                    "artifact_id": artifact_id,
                    "sha256": digest,
                    "path": str(store.root / digest[:2] / digest),
                    "created_at": "2020-01-01T00:00:00+00:00",
                    "size": str(len(data)),
                }
            ],
            "orphan_files": [],
            "protected_fresh": [],
            "missing_files": [],
        }

        barrier = threading.Barrier(2, timeout=5)
        gc_result: dict = {}
        ref_done = threading.Event()

        def _create_ref():
            barrier.wait()
            _add_evidence_ref(store, artifact_id)
            ref_done.set()

        def _gc_apply():
            barrier.wait()
            ref_done.wait(timeout=3)
            gc_result["result"] = store.collect_garbage(scan_result, apply=True)

        t1 = threading.Thread(target=_create_ref)
        t2 = threading.Thread(target=_gc_apply)
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

        # ArtifactRow 不应被删除
        with store.db.session() as s:
            row = s.get(ArtifactRow, artifact_id)
            assert row is not None, "已引用的 ArtifactRow 不应被删除"

        actions = gc_result.get("result", {}).get("actions", [])
        for a in actions:
            if a.get("op") == "delete_row":
                assert a.get("result") in ("skipped_referenced", "gone"), (
                    f"已引用的行不应被删除: {a}"
                )

    def test_no_dangling_after_gc(self, tmp_path):
        """GC 后不产生 dangling reference。"""
        store = _make_store(tmp_path)

        # 创建 3 个产物，其中 1 个被引用
        for i in range(3):
            data = f"dangling-test-{i}".encode()
            digest = _write_artifact_file(store, data)
            _register_row(store, digest)

        # 第一个被引用
        digests = []
        for i in range(3):
            data = f"dangling-test-{i}".encode()
            digests.append(hashlib.sha256(data).hexdigest())
        _add_evidence_ref(store, f"art_{digests[0][:20]}")

        scan_result = store.scan(retention_days=0)
        result = store.collect_garbage(scan_result, apply=True)

        # 未被引用的产物应被回收（结果中应有 delete_row 动作）
        deleted_ids = {
            a.get("artifact_id") for a in result.get("actions", []) if a.get("op") == "delete_row"
        }
        assert f"art_{digests[1][:20]}" in deleted_ids, f"未引用产物应被删除: {result}"
        assert f"art_{digests[2][:20]}" in deleted_ids, f"未引用产物应被删除: {result}"

        # 被引用的产物不被删除
        with store.db.session() as s:
            row = s.get(ArtifactRow, f"art_{digests[0][:20]}")
            assert row is not None, "被引用的产物不应被删除"

        # 检查无 dangling：所有 Evidence 的 attachment_ref 都有对应 ArtifactRow
        with store.db.session() as s:
            evidences = s.query(EvidenceRow).all()
            for ev in evidences:
                ref = getattr(ev, "attachment_ref", None)
                if ref:
                    row = s.get(ArtifactRow, ref)
                    assert row is not None, f"dangling reference: {ref}"


class TestGCIdempotent:
    """重复回收幂等。"""

    def test_double_gc_safe(self, tmp_path):
        store = _make_store(tmp_path)
        data = b"idempotent-test"
        digest = _write_artifact_file(store, data)
        _register_row(store, digest)

        scan_result = store.scan(retention_days=0)
        result1 = store.collect_garbage(scan_result, apply=True)
        result2 = store.collect_garbage(scan_result, apply=True)

        # 第一次回收应删除未引用产物（无引用场景下全部删除）
        assert any(a.get("op") == "delete_row" for a in result1.get("actions", [])), (
            f"首次 GC 应有删除动作: {result1}"
        )
        # 第二次回收应全部 gone 或 skipped
        for a in result2.get("actions", []):
            assert a.get("result") in ("gone", "skipped_referenced"), f"重复回收不应报错: {a}"
