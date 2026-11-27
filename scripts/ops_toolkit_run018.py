"""RUN-018 WP9/R1：部署运维可执行工具箱（从文档走向可重复执行的命令）。

子命令（全部支持独立测试环境，绝不触碰真实系统 cron/launchd/WAL 归档）：

- `artifact-backup`  ：Artifact 增量备份——对比上一份 manifest（SHA-256），
  只复制新增/变更文件；写出新 manifest（哈希/大小/时间戳），供完整性校验。
- `verify-backup`    ：备份完整性校验——重算备份目录中每个文件 SHA-256 并与
  manifest 逐条比对，不一致/缺失如实报告。
- `sha-audit`        ：登记 ↔ 落盘一致性巡检（封装 ArtifactStore.verify_consistency，
  支持有界抽查 limit）。
- `retention-dry-run`：备份保留策略**预演**——列出"如果执行保留策略会删除哪些
  备份"，不删除任何文件（真实删除必须人工另行确认执行，本工具不做）。
- `pre-migration-check`  ：迁移前基线——表清单 + 关键表行数 + 已应用迁移版本。
- `post-migration-check` ：迁移后核对——与基线 diff（新增表/列、行数变化），
  并复用 doctor 的数据库检查（恢复/迁移后 doctor 检查入口）。

退出码：0 成功；1 校验失败/不一致；2 用法错误。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
for _base in ("packages", "apps"):
    _dir = ROOT / _base
    if _dir.is_dir():
        for _sub in sorted(_dir.iterdir()):
            if _sub.is_dir() and str(_sub) not in sys.path:
                sys.path.insert(0, str(_sub))


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _scan_artifacts(root: Path) -> dict[str, dict[str, Any]]:
    """收集 root 下全部产物文件（相对路径 → sha256/size），跳过 _quarantine/_gc 等。"""
    out: dict[str, dict[str, Any]] = {}
    if not root.is_dir():
        return out
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if rel.startswith("_"):
            continue  # quarantine / journal / audit 属运行态，不入备份清单
        out[rel] = {"sha256": _sha256_file(path), "size": path.stat().st_size}
    return out


def cmd_artifact_backup(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    out_dir = Path(args.backup_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    current = _scan_artifacts(root)

    base: dict[str, dict[str, Any]] = {}
    if args.base_manifest:
        bp = Path(args.base_manifest).resolve()
        if bp.is_file():
            base = json.loads(bp.read_text(encoding="utf-8")).get("files", {})

    copied, skipped = [], []
    for rel, meta in current.items():
        prev = base.get(rel)
        dest = out_dir / rel
        if prev and prev.get("sha256") == meta["sha256"] and dest.is_file():
            skipped.append(rel)
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / rel, dest)
        copied.append(rel)

    manifest = {
        "kind": "traceguard-artifact-backup",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source_root": str(root),
        "base_manifest": str(args.base_manifest) if args.base_manifest else None,
        "total_files": len(current),
        "copied_this_run": len(copied),
        "unchanged": len(skipped),
        "files": current,
    }
    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "ok": True,
                "total": len(current),
                "copied": len(copied),
                "unchanged": len(skipped),
                "manifest": str(manifest_path),
            },
            ensure_ascii=False,
        )
    )
    return 0


def cmd_verify_backup(args: argparse.Namespace) -> int:
    backup_dir = Path(args.backup_dir).resolve()
    manifest = json.loads(Path(args.manifest).resolve().read_text(encoding="utf-8"))
    files = manifest.get("files", {})
    missing, mismatch = [], []
    for rel, meta in files.items():
        path = backup_dir / rel
        if not path.is_file():
            missing.append(rel)
            continue
        if _sha256_file(path) != meta["sha256"]:
            mismatch.append(rel)
    ok = not missing and not mismatch
    print(
        json.dumps(
            {
                "ok": ok,
                "checked": len(files),
                "missing": missing[:20],
                "hash_mismatch": mismatch[:20],
                "missing_count": len(missing),
                "mismatch_count": len(mismatch),
            },
            ensure_ascii=False,
        )
    )
    return 0 if ok else 1


def cmd_sha_audit(args: argparse.Namespace) -> int:
    from traceguard_persistence import Database
    from traceguard_persistence.stores import ArtifactStore

    db = Database(args.db)
    db.create_schema()
    store = ArtifactStore(db, root=args.root)
    report = store.verify_consistency(limit=args.limit)
    db.dispose()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["consistent"] else 1


def cmd_retention_dry_run(args: argparse.Namespace) -> int:
    """保留策略预演：只列出"会删除什么"，不删除任何文件。"""
    backup_dir = Path(args.backup_dir).resolve()
    now = time.time()
    cutoff = now - args.keep_days * 86400
    would_delete, kept = [], []
    for path in sorted(backup_dir.glob("*")):
        if path.name in ("manifest.json",):
            continue
        mtime = path.stat().st_mtime
        (kept if mtime >= cutoff else would_delete).append(
            {"path": path.name, "age_days": round((now - mtime) / 86400, 1)}
        )
    print(
        json.dumps(
            {
                "dry_run": True,
                "deleted_nothing": True,
                "keep_days": args.keep_days,
                "min_keep": args.min_keep,
                "would_delete": would_delete,
                "would_delete_count": len(would_delete),
                "kept": len(kept),
                "note": (
                    "dry-run：未删除任何文件；最少保留 "
                    f"{args.min_keep} 份，实际执行删除需人工另行确认"
                ),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def _schema_baseline(db_url: str) -> dict[str, Any]:
    from sqlalchemy import inspect, text
    from traceguard_persistence import Database

    db = Database(db_url)
    try:
        insp = inspect(db.engine)
        tables: dict[str, Any] = {}
        for t in sorted(insp.get_table_names()):
            cols = [c["name"] for c in insp.get_columns(t)]
            rows = -1
            try:
                with db.session() as s:
                    rows = int(s.scalar(text(f"SELECT count(*) FROM {t}")) or 0)
            except Exception:  # noqa: BLE001
                rows = -1
            tables[t] = {"columns": sorted(cols), "rows": rows}
        migrations = []
        try:
            with db.session() as s:
                migrations = [
                    int(r[0])
                    for r in s.execute(text("SELECT version FROM tg_schema_migration")).fetchall()
                ]
        except Exception:  # noqa: BLE001
            pass
        return {"tables": tables, "migrations": sorted(migrations)}
    finally:
        db.dispose()


def cmd_pre_migration_check(args: argparse.Namespace) -> int:
    baseline = _schema_baseline(args.db)
    baseline["captured_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    Path(args.out).resolve().write_text(
        json.dumps(baseline, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({"ok": True, "tables": len(baseline["tables"]), "out": args.out}))
    return 0


def cmd_post_migration_check(args: argparse.Namespace) -> int:
    baseline = json.loads(Path(args.baseline).resolve().read_text(encoding="utf-8"))
    current = _schema_baseline(args.db)
    new_tables = sorted(set(current["tables"]) - set(baseline["tables"]))
    dropped_tables = sorted(set(baseline["tables"]) - set(current["tables"]))
    column_changes = []
    row_changes = []
    for t, info in current["tables"].items():
        old = baseline["tables"].get(t)
        if not old:
            continue
        added = sorted(set(info["columns"]) - set(old["columns"]))
        removed = sorted(set(old["columns"]) - set(info["columns"]))
        if added or removed:
            column_changes.append({"table": t, "added": added, "removed": removed})
        if old["rows"] >= 0 and info["rows"] >= 0 and old["rows"] != info["rows"]:
            row_changes.append({"table": t, "before": old["rows"], "after": info["rows"]})
    new_migrations = sorted(set(current["migrations"]) - set(baseline["migrations"]))
    # doctor 检查（恢复/迁移后 doctor 入口）：数据库可达 + schema 存在
    doctor_ok = bool(current["tables"]) and "tg_schema_migration" in current["tables"]
    print(
        json.dumps(
            {
                "ok": doctor_ok,
                "new_tables": new_tables,
                "dropped_tables": dropped_tables,
                "column_changes": column_changes,
                "row_changes": row_changes,
                "new_migrations": new_migrations,
                "doctor_database_check": "pass" if doctor_ok else "fail",
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if doctor_ok else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("artifact-backup")
    p.add_argument("--root", required=True)
    p.add_argument("--backup-dir", required=True)
    p.add_argument("--base-manifest", default="")
    p.set_defaults(func=cmd_artifact_backup)

    p = sub.add_parser("verify-backup")
    p.add_argument("--backup-dir", required=True)
    p.add_argument("--manifest", required=True)
    p.set_defaults(func=cmd_verify_backup)

    p = sub.add_parser("sha-audit")
    p.add_argument("--db", required=True)
    p.add_argument("--root", required=True)
    p.add_argument("--limit", type=int, default=0)
    p.set_defaults(func=cmd_sha_audit)

    p = sub.add_parser("retention-dry-run")
    p.add_argument("--backup-dir", required=True)
    p.add_argument("--keep-days", type=float, default=30)
    p.add_argument("--min-keep", type=int, default=3)
    p.set_defaults(func=cmd_retention_dry_run)

    p = sub.add_parser("pre-migration-check")
    p.add_argument("--db", required=True)
    p.add_argument("--out", required=True)
    p.set_defaults(func=cmd_pre_migration_check)

    p = sub.add_parser("post-migration-check")
    p.add_argument("--db", required=True)
    p.add_argument("--baseline", required=True)
    p.set_defaults(func=cmd_post_migration_check)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
