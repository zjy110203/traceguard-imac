"""RUN-017 WP4.2：Artifact GC 扫描的规模测试（1 万 / 5 万 / 10 万条 Evidence）。

回答三个问题：

1. `scan()` 在 1e4 / 5e4 / 1e5 条 Evidence 下的耗时、峰值内存、SQL 查询次数；
2. 扫描期间是否阻塞业务写入（真实并发写一条 Evidence，测其延迟）；
3. 引用判定是否仍然正确（有引用的产物不得进入孤儿列表）。

用法：
    .venv/bin/python scripts/artifact_gc_bench_run017.py --counts 10000,50000,100000 \
      --out docs/assets/run017/wp4
    # 可选：--db-url postgresql+psycopg://traceguard@127.0.0.1:55432/traceguard_run017_gc
"""

from __future__ import annotations

import argparse
import json
import resource
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for _p in (
    "packages/core",
    "packages/contracts",
    "packages/execution",
    "packages/agents",
    "packages/evidence",
    "packages/persistence",
):
    sys.path.insert(0, str(ROOT / _p))

from sqlalchemy import event, insert  # noqa: E402
from traceguard_persistence import ArtifactStore, Database  # noqa: E402
from traceguard_persistence.models import ArtifactRow, EvidenceRow, RunRow  # noqa: E402


def _rss_mb() -> float:
    usage = resource.getrusage(resource.RUSAGE_SELF)
    value = float(usage.ru_maxrss)
    if sys.platform != "darwin":
        value *= 1024  # Linux: KiB → bytes
    return round(value / (1024 * 1024), 1)


def _seed(
    db: Database,
    store: ArtifactStore,
    count: int,
    *,
    artifacts: int = 500,
    runs: int = 500,
) -> dict[str, Any]:
    """批量写入 Evidence / Artifact / Run 报告（固定种子，可重复）。"""
    store.root.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    artifact_ids: list[str] = []
    artifact_rows: list[dict[str, Any]] = []
    for _idx in range(artifacts):
        aid = f"art_{uuid.uuid4().hex[:20]}"
        sha = uuid.uuid4().hex + uuid.uuid4().hex[:32]
        path = store.root / "default" / sha
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * 32)
        artifact_ids.append(aid)
        artifact_rows.append(
            {
                "artifact_id": aid,
                "workspace_id": "default",
                "sha256": sha,
                "size": 32,
                "media_type": "text/plain",
                "path": str(path),
                "backend": "local",
                "original_name": "bench.txt",
                "created_at": "2026-01-01T00:00:00Z",
            }
        )
    with db.session() as s:
        s.execute(insert(ArtifactRow), artifact_rows)

        batch: list[dict[str, Any]] = []
        for idx in range(count):
            # 前 10% 引用真实产物（必须被保护），其余只带普通 JSON
            if idx % 10 == 0:
                doc = {"note": "referenced", "artifact": artifact_ids[idx // 10 % artifacts]}
                attachment_ref = artifact_ids[idx // 10 % artifacts]
            else:
                doc = {"note": "plain", "idx": idx, "payload": "z" * 64}
                attachment_ref = None
            batch.append(
                {
                    "evidence_id": f"evd_{idx:08d}",
                    "workspace_id": "default",
                    "run_id": None,
                    "kind": "snapshot",
                    "source_type": "pipeline",
                    "sha256": f"{idx:064d}",
                    "stored_content_sha256": f"{idx:064d}",
                    "attachment_sha256": "",
                    "attachment_ref": attachment_ref,
                    "source_trust": "TRUSTED",
                    "integrity_check": "PASSED",
                    "integrity_detail": {},
                    "redacted": False,
                    "doc": doc,
                    "captured_at": "2026-01-01T00:00:00Z",
                }
            )
            if len(batch) >= 2000:
                s.execute(insert(EvidenceRow), batch)
                batch.clear()
        if batch:
            s.execute(insert(EvidenceRow), batch)

        run_batch: list[dict[str, Any]] = []
        for idx in range(runs):
            run_batch.append(
                {
                    "run_id": f"run_bench_{idx:05d}",
                    "task_id": f"task_{idx:05d}",
                    "workspace_id": "default",
                    "attempt": 1,
                    "testcase_version_id": "tcase@1",
                    "execution_status": "COMPLETED",
                    "test_verdict": "PASSED",
                    "report_json": {
                        "run_id": f"run_bench_{idx:05d}",
                        # 报告里引用产物（RUN-016 发现：这类引用只存在于 report_json）
                        "trace_artifact": artifact_ids[idx % artifacts],
                    },
                    "created_at": time.time(),
                }
            )
        s.execute(insert(RunRow), run_batch)
    return {
        "evidence": count,
        "artifacts": artifacts,
        "runs": runs,
        "seed_seconds": round(time.perf_counter() - started, 2),
        "rss_mb_after_seed": _rss_mb(),
    }


def _instrument(db: Database) -> dict[str, int]:
    counter = {"queries": 0}
    engine = db.engine

    @event.listens_for(engine, "before_cursor_execute")
    def _count(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001, ARG001
        counter["queries"] += 1

    return counter


def run_case(count: int, db_url: str | None, out_dir: Path) -> dict[str, Any]:
    base = out_dir / f"gc-{count}"
    base.mkdir(parents=True, exist_ok=True)
    url = db_url or f"sqlite:///{base / 'gc.db'}"
    if db_url and db_url.startswith("postgresql"):
        url = f"{db_url}_{count}"
    db = Database(url)
    db.create_schema()
    store = ArtifactStore(db, root=base / "artifacts")
    seeded = _seed(db, store, count)
    counter = _instrument(db)

    # 并发业务写入：扫描进行时插入一条 Evidence，测量其延迟（是否被长事务/大查询阻塞）
    write_latency: dict[str, Any] = {}

    def writer() -> None:
        time.sleep(0.05)
        started = time.perf_counter()
        with db.session() as s:
            s.execute(
                insert(EvidenceRow).values(
                    evidence_id="evd_concurrent_probe",
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
                    doc={"note": "concurrent"},
                    captured_at="2026-01-01T00:00:00Z",
                )
            )
        write_latency["ms"] = round((time.perf_counter() - started) * 1000, 1)

    thread = threading.Thread(target=writer)
    queries_before = counter["queries"]
    rss_before = _rss_mb()
    started = time.perf_counter()
    thread.start()
    scan = store.scan(retention_days=7.0, now=time.time() + 30 * 86400)
    elapsed = time.perf_counter() - started
    thread.join(timeout=60)
    rss_after = _rss_mb()

    with db.session() as s:
        refs = store._referenced_artifact_ids(s)
    referenced_orphans = [item for item in scan["orphan_rows"] if item["artifact_id"] in refs]
    result = {
        "case": f"evidence={count}",
        "seeded": seeded,
        "scan_seconds": round(elapsed, 3),
        "scan_queries": counter["queries"] - queries_before,
        "rss_before_scan_mb": rss_before,
        "rss_after_scan_mb": rss_after,
        "rss_delta_mb": round(rss_after - rss_before, 1),
        "concurrent_write_ms": write_latency.get("ms"),
        "scanned_rows": scan["scanned_rows"],
        "scanned_files": scan["scanned_files"],
        "referenced_artifacts": scan["referenced_artifacts"],
        "orphan_rows": len(scan["orphan_rows"]),
        "orphan_files": len(scan["orphan_files"]),
        "protected_fresh": len(scan["protected_fresh"]),
        "referenced_but_orphaned": len(referenced_orphans),
        "consistent": not referenced_orphans,
    }
    db.dispose()
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--counts", default="10000,50000,100000")
    parser.add_argument("--db-url", default="")
    parser.add_argument("--out", default="docs/assets/run017/wp4")
    parser.add_argument(
        "--fail-on-inconsistent",
        action="store_true",
        help="任一规模下出现'被引用却进孤儿列表'时返回非 0",
    )
    args = parser.parse_args()
    out_dir = ROOT / args.out
    out_dir.mkdir(parents=True, exist_ok=True)
    cases = [int(x) for x in args.counts.split(",") if x.strip()]
    results = []
    for count in cases:
        print(f"[gc] seeding {count} evidence ...", flush=True)
        case = run_case(count, args.db_url or None, out_dir)
        results.append(case)
        print(json.dumps(case, ensure_ascii=False), flush=True)
    payload = {
        "run": "RUN-017 WP4.2",
        "db_url": args.db_url or "sqlite (per-case temp)",
        "cases": results,
        "note": ("scan() 只读；referenced_but_orphaned 必须为 0（有引用的产物不得进入孤儿列表）"),
    }
    (out_dir / "gc-bench.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if args.fail_on_inconsistent and not all(c["consistent"] for c in results):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
