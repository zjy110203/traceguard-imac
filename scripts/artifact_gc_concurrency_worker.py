"""RUN-018 WP2：Artifact GC 并发安全多进程验证的子进程执行器。

每个子进程是独立 OS 进程 + 独立数据库连接，模式由 --mode 指定：

- `put`：向共享 ArtifactStore 写入指定 SHA 内容并登记（模拟 Worker put()）；
- `gc`：执行 collect_garbage(apply=True)（模拟 GC 进程）；
- `evidence-ref`：为指定 artifact 创建 Evidence 引用（走 PersistentEvidenceManager
  完整锁协议）；
- `report-ref`：为指定 artifact 写入 Run 报告引用（走 TaskService.submit 的
  lock_and_verify_report_refs 完整锁协议）；
- `wait-ready`：写入 READY 文件后挂起（供父进程 SIGKILL，验证 GC 中途被杀）；
- `recover`：执行 recover_journal() 并输出报告。

同步一律使用文件轮询（与 RUN-018 WP1 教训一致：绝不与会被 SIGKILL 的进程
共享 multiprocessing 锁）。输出 JSON 证据文件供父进程汇总断言。

用法（一般由 tests/integration/test_artifact_gc_concurrency_multiprocess.py 调用）：
    python scripts/artifact_gc_concurrency_worker.py --mode put \
        --db <url> --root <artifact_root> --sha <sha256> --out <json> \
        [--ready-file <p>] [--release-file <p>] [--pg]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for _base in ("packages", "apps"):
    _dir = ROOT / _base
    if _dir.is_dir():
        for _sub in sorted(_dir.iterdir()):
            if _sub.is_dir() and str(_sub) not in sys.path:
                sys.path.insert(0, str(_sub))


def _wait_file(path: Path, timeout: float, *, check_exists: bool = True) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists() == check_exists:
            return True
        time.sleep(0.02)
    return False


def _main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", required=True)
    ap.add_argument("--db", required=True)
    ap.add_argument("--root", required=True)
    ap.add_argument("--sha", default="")
    ap.add_argument("--data-text", default="")
    ap.add_argument("--artifact-id", default="")
    ap.add_argument("--run-id", default="")
    ap.add_argument("--out", required=True)
    ap.add_argument("--ready-file", default="")
    ap.add_argument("--release-file", default="")
    ap.add_argument("--retention-days", type=float, default=0.0)
    ap.add_argument("--kill-self-delay", type=float, default=0.0)
    args = ap.parse_args()

    from traceguard_persistence import Database
    from traceguard_persistence.stores import ArtifactStore, PersistentEvidenceManager

    result: dict[str, object] = {"mode": args.mode, "pid": os.getpid(), "error": ""}
    db = Database(args.db)
    db.create_schema()
    store = ArtifactStore(db, root=args.root)
    try:
        if args.mode == "put":
            data = args.data_text.encode()
            rec = store.put(data, workspace_id="default", original_name="gc-test")
            result["artifact_id"] = rec["artifact_id"]
            result["sha256"] = rec["sha256"]
        elif args.mode == "gc":
            scan = store.scan(retention_days=args.retention_days)
            result["scan"] = {
                "orphan_rows": len(scan["orphan_rows"]),
                "orphan_files": len(scan["orphan_files"]),
            }
            out = store.collect_garbage(scan, apply=True)
            result["actions"] = out.get("actions", [])
            result["skipped"] = out.get("skipped", [])
        elif args.mode == "evidence-ref":
            artifact_id = args.artifact_id
            ev = PersistentEvidenceManager(db)
            rec = ev.add_raw(
                f"evd_gc_{int(time.time_ns())}",
                "test",
                "gc-concurrency",
                "default",
                {"artifact_id": artifact_id, "note": "concurrent-ref-test"},
                run_id=args.run_id,
                attachment_ref=artifact_id,
            )
            result["evidence_id"] = rec.evidence_id
        elif args.mode == "report-ref":
            from traceguard_execution.tasks.service import TaskService
            from traceguard_persistence.models import RunRow

            artifact_id = args.artifact_id
            ts = TaskService(db)
            # 直接创建一个 RUNNING 的 Run 用于提交报告
            run_id = args.run_id if args.run_id else f"run_gc_{int(time.time_ns())}"
            with db.session() as s:
                s.add(
                    RunRow(
                        run_id=run_id,
                        task_id="task_gc",
                        testcase_version_id="tcv_gc_test",
                        attempt=1,
                        execution_status="RUNNING",
                        lease_owner="gc-test-worker",
                        fencing_token=1,
                        lease_expires_at=time.time() + 3600,
                    )
                )
            report = {"trace_artifact": artifact_id, "verdict": "PASSED"}
            ts.submit_result(
                run_id=run_id,
                worker_id="gc-test-worker",
                fencing_token=1,
                execution_status="COMPLETED",
                test_verdict="PASSED",
                report=report,
            )
            result["run_id"] = run_id
        elif args.mode == "wait-ready":
            # 写 READY 后进入等待状态（父进程 SIGKILL 目标）
            Path(args.ready_file).write_text(json.dumps({"pid": os.getpid()}), encoding="utf-8")
            released = _wait_file(Path(args.release_file), 120.0)
            result["released"] = released
        elif args.mode == "recover":
            result["recovery"] = store.recover_journal()
        else:
            raise SystemExit(f"unknown mode: {args.mode}")
    except Exception as exc:  # noqa: BLE001 - 失败必须写回证据文件
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        db.dispose()
    Path(args.out).write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: result.get(k) for k in ("mode", "error")}, ensure_ascii=False))
    return 1 if result.get("error") else 0


if __name__ == "__main__":
    sys.exit(_main())
