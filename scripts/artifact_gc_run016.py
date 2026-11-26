"""RUN-016 WP1：Artifact 生命周期巡检 / 孤儿产物治理（默认 dry-run）。

设计约束（与 WP1 要求逐条对应）：

- **默认只读**：不带 `--apply` 时只输出扫描报告，不动任何文件、不删任何登记；
- **保留期**：只有早于 `--retention-days`（默认 7 天）的对象才进入候选；
- **重新校验引用**：删除前在数据库里再查一次 `tg_evidence` 引用，扫描后被引用的自动跳过；
- **可回滚**：文件不做 `unlink`，统一移到 `<artifact_dir>/_quarantine/<ts>/`；
- **审计**：每条动作写 `<artifact_dir>/_gc-audit.jsonl`（sha256/大小/原因/操作者/时间）。

用法：
    # 1) 巡检（默认，安全）
    .venv/bin/python scripts/artifact_gc_run016.py --out docs/assets/run016/wp1/artifact-scan.json

    # 2) 真正回收（需要显式 --apply）
    .venv/bin/python scripts/artifact_gc_run016.py --apply --retention-days 7
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for extra in (
    "packages/core",
    "packages/contracts",
    "packages/execution",
    "packages/agents",
    "packages/evidence",
    "packages/persistence",
    "apps/api",
):
    sys.path.insert(0, str(ROOT / extra))

from traceguard_core.config import get_settings  # noqa: E402
from traceguard_persistence.db import get_database  # noqa: E402
from traceguard_persistence.stores import ArtifactStore  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Artifact 孤儿巡检/回收（默认 dry-run）")
    ap.add_argument("--retention-days", type=float, default=7.0)
    ap.add_argument("--root", default="", help="产物根目录；缺省取 TG_ARTIFACT_DIR")
    ap.add_argument("--apply", action="store_true", help="真正执行回收（默认只巡检）")
    ap.add_argument("--out", default="", help="扫描/执行报告写入路径（JSON）")
    ap.add_argument("--actor", default="artifact-gc-cli")
    ap.add_argument(
        "--fail-on-inconsistent",
        action="store_true",
        help="巡检发现缺失/哈希不一致时返回非 0（CI 门禁用；默认只告警）",
    )
    args = ap.parse_args()

    settings = get_settings()
    db = get_database()
    db.create_schema()
    store = ArtifactStore(db, root=args.root or settings.artifact_dir)

    scan = store.scan(retention_days=args.retention_days)
    report: dict = {"scan": scan}
    if args.apply:
        report["gc"] = store.collect_garbage(scan, apply=True, actor=args.actor)
    else:
        report["gc"] = {"apply": False, "dry_run": True, "note": "未执行任何删除"}
        report["consistency"] = store.verify_consistency()

    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        out = (ROOT / args.out).resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        print(f"[out] {out}")
    print(
        "[scan] files={files} rows={rows} orphan_files={of} orphan_rows={orr} "
        "protected_fresh={pf} missing={mf} apply={apply}".format(
            files=scan["scanned_files"],
            rows=scan["scanned_rows"],
            of=len(scan["orphan_files"]),
            orr=len(scan["orphan_rows"]),
            pf=len(scan["protected_fresh"]),
            mf=len(scan["missing_files"]),
            apply=bool(args.apply),
        )
    )
    if report.get("consistency") and not report["consistency"]["consistent"]:
        print(
            "[warn] 一致性巡检发现问题：missing={} hash_mismatch={}".format(
                len(report["consistency"]["missing"]),
                len(report["consistency"]["hash_mismatch"]),
            )
        )
    if (
        args.fail_on_inconsistent
        and report.get("consistency")
        and not report["consistency"]["consistent"]
    ):
        return 1
    # dry-run 默认返回 0（巡检不是失败）；apply 时若有单条失败也不掩盖
    if args.apply:
        errors = [a for a in report["gc"]["actions"] if a.get("result") == "error"]
        return 1 if errors else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
