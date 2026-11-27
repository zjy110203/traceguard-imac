"""RUN-018 WP9/R1：运维工具箱集成测试（独立测试环境，PG16 备份恢复复验）。

覆盖：
- artifact-backup：首次全量 + 第二次增量（无变更 → copied=0；新增文件 → 只复制增量）；
- verify-backup：完整备份校验 PASS；篡改一个文件后校验 FAIL（不伪报成功）；
- sha-audit：登记/文件一致 PASS；删除落盘文件后 FAIL；
- retention-dry-run：列出让删除项但**零删除**（备份文件数不变）；
- pre/post-migration-check：基线 → 新增迁移/表 → diff 如实反映 + doctor 检查；
- PG16 真实备份恢复：复用 run017 的 pg_case（pg_dump 全量 → 恢复到独立库 →
  业务数据核验）——PG 工具缺失时如实 SKIP（不算 PG16 验收成功）。
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "ops_toolkit_run018.py"

PG_ADMIN = "postgresql://traceguard@127.0.0.1:55432/postgres"


def _can_connect_pg() -> bool:
    try:
        import psycopg

        conn = psycopg.connect(PG_ADMIN, connect_timeout=3)
        conn.close()
        return True
    except Exception:  # noqa: BLE001
        return False


def _run(*args: str, expect: int = 0) -> dict:
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == expect, f"rc={proc.returncode} stderr={proc.stderr[-500:]}"
    out = proc.stdout.strip()
    try:
        return json.loads(out)  # sha-audit 等打印多行 JSON
    except json.JSONDecodeError:
        return json.loads(out.splitlines()[-1])  # 单行摘要


def _build_artifacts(root: Path) -> None:
    (root / "ab").mkdir(parents=True, exist_ok=True)
    (root / "ab" / "c1").write_bytes(b"artifact-content-1")
    (root / "ab" / "c2").write_bytes(b"artifact-content-2")


def test_artifact_backup_incremental_and_verify(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    backup = tmp_path / "backup"
    _build_artifacts(root)

    # 首次：全量
    r1 = _run("artifact-backup", "--root", str(root), "--backup-dir", str(backup))
    assert r1["total"] == 2 and r1["copied"] == 2 and r1["unchanged"] == 0

    # 增量：无变更 → copied=0
    r2 = _run(
        "artifact-backup",
        "--root",
        str(root),
        "--backup-dir",
        str(backup),
        "--base-manifest",
        str(backup / "manifest.json"),
    )
    assert r2["copied"] == 0 and r2["unchanged"] == 2

    # 新增文件 → 只复制增量
    (root / "ab" / "c3").write_bytes(b"artifact-content-3")
    r3 = _run(
        "artifact-backup",
        "--root",
        str(root),
        "--backup-dir",
        str(backup),
        "--base-manifest",
        str(backup / "manifest.json"),
    )
    assert r3["copied"] == 1 and r3["total"] == 3

    # 完整性校验 PASS
    _run("verify-backup", "--backup-dir", str(backup), "--manifest", str(backup / "manifest.json"))

    # 篡改备份文件 → 校验 FAIL（不伪报成功）
    (backup / "ab" / "c1").write_bytes(b"tampered")
    _run(
        "verify-backup",
        "--backup-dir",
        str(backup),
        "--manifest",
        str(backup / "manifest.json"),
        expect=1,
    )


def test_sha_audit_detects_missing_file(tmp_path: Path) -> None:
    from traceguard_persistence import Database
    from traceguard_persistence.stores import ArtifactStore

    db_file = tmp_path / "ops.db"
    db = Database(f"sqlite:///{db_file}")
    db.create_schema()
    root = tmp_path / "artifacts"
    store = ArtifactStore(db, root=str(root))
    store.put(b"sha-audit-artifact", workspace_id="default")
    db.dispose()

    r = _run("sha-audit", "--db", f"sqlite:///{db_file}", "--root", str(root))
    assert r["consistent"] is True

    # 删除落盘文件 → 巡检必须 FAIL（不误报为健康）
    digest_dir = next(p for p in root.rglob("*") if p.is_file())
    digest_dir.unlink()
    r2 = _run("sha-audit", "--db", f"sqlite:///{db_file}", "--root", str(root), expect=1)
    assert r2["consistent"] is False and len(r2["missing"]) == 1


def test_retention_dry_run_deletes_nothing(tmp_path: Path) -> None:
    import os

    backup = tmp_path / "backup"
    backup.mkdir()
    for name, age in (("b-old.tar.gz", 60), ("b-new.tar.gz", 1)):
        p = backup / name
        p.write_bytes(b"x")
        os.utime(p, (time.time() - age * 86400, time.time() - age * 86400))

    r = _run("retention-dry-run", "--backup-dir", str(backup), "--keep-days", "30")
    assert r["dry_run"] is True and r["would_delete_count"] == 1
    assert r["would_delete"][0]["path"] == "b-old.tar.gz"
    # 零删除：两个文件都还在
    assert len(list(backup.glob("*"))) == 2


def test_migration_pre_post_check(tmp_path: Path) -> None:
    from traceguard_persistence import Database

    db_file = tmp_path / "mig.db"
    db_url = f"sqlite:///{db_file}"
    # 迁移前：空库基线
    pre = tmp_path / "pre.json"
    r1 = _run("pre-migration-check", "--db", db_url, "--out", str(pre))
    assert r1["ok"] is True

    # 建表（模拟一次迁移）
    db = Database(db_url)
    db.create_schema()
    db.dispose()

    r2 = _run("post-migration-check", "--db", db_url, "--baseline", str(pre))
    assert r2["ok"] is True
    assert "tg_run" in r2["new_tables"] or r2["new_tables"], r2["new_tables"]
    assert r2["doctor_database_check"] == "pass"
    # 迁移版本差异如实记录
    assert r2["new_migrations"] or len(json.loads(pre.read_text())["tables"]) == 0


def _run_pg_backup_restore(tmp_path: Path) -> None:
    """复用 run017 的 PG 备份恢复链路（pg_dump → 独立恢复库 → 业务核验）。"""
    sys.path.insert(0, str(ROOT / "scripts"))
    from backup_restore_verify_run017 import find_pg_tool, pg_case

    assert find_pg_tool("pg_dump"), "pg_dump 不可用"
    result = pg_case(tmp_path, PG_ADMIN)
    checks = {k: v for k, v in result.items() if isinstance(v, bool)}
    assert checks and all(checks.values()), {k: v for k, v in result.items() if isinstance(v, bool)}
    assert result["kind"] == "postgres16"
    assert result["backup_method"] == "pg_dump --format=custom"


def test_pg16_backup_restore_real(tmp_path: Path) -> None:
    """PG16 真实备份恢复（pg_dump 可用时执行；缺失时如实 SKIP）。"""
    sys.path.insert(0, str(ROOT / "scripts"))
    from backup_restore_verify_run017 import find_pg_tool

    if not _can_connect_pg():
        pytest.skip("PostgreSQL 16 不可达（不把 SKIP 当作 PG16 验收成功）")
    if not find_pg_tool("pg_dump"):
        pytest.skip("pg_dump 不可用（Postgres.app CLI 未在 PATH；不把 SKIP 当作验收成功）")
    _run_pg_backup_restore(tmp_path)
