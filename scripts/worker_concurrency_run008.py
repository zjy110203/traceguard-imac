"""RUN-008 WP3：4 个独立 OS Worker 并发领取与数据一致性验证。

复用 RUN-007 严格故障恢复脚本的资产准备/入队/查询辅助，但**不击杀任何进程**：
验证多个 Run 真实并行、同一 Run 只被一个 Worker 有效持有、无重复执行/事件/证据、
正常退出不丢任务。中途 SIGKILL 与租约接管由 worker_fault_recovery.py 单独验证。

产出：docs/assets/run008/wp3/worker-concurrency.json
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

import worker_fault_recovery as wfr  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=".traceguard/run008/wfr-concurrency/workers.db")
    ap.add_argument("--runs", type=int, default=4)
    ap.add_argument("--procs", type=int, default=4)
    ap.add_argument("--teamflow", default="http://127.0.0.1:8043")
    ap.add_argument("--lease-ttl", type=float, default=12.0)
    ap.add_argument("--out", default="docs/assets/run008/wp3/worker-concurrency.json")
    args = ap.parse_args(argv)

    started = time.time()
    db_path = ROOT / args.db
    db_path.parent.mkdir(parents=True, exist_ok=True)
    for suffix in ("", "-wal", "-shm"):
        path = Path(str(db_path) + suffix)
        if path.exists():
            path.unlink()
    wfr._prepare_assets(db_path, args.teamflow)
    run_ids = wfr._queue_runs(db_path, args.runs)
    print(f"queued {len(run_ids)} runs: {[r[-12:] for r in run_ids]}")

    env = {
        **os.environ,
        "TG_DATABASE_URL": f"sqlite:///{db_path}",
        "TG_ARTIFACT_DIR": str(db_path.parent / "artifacts"),
        "TG_TEAMFLOW_BASE_URL": args.teamflow,
        "TG_INLINE_WORKER": "0",
        "TG_WORKER_LEASE_TTL_S": str(args.lease_ttl),
        "TG_WORKER_POLL_INTERVAL_S": "0.2",
        "TG_LOG_JSON": "false",
    }
    workers: dict[str, subprocess.Popen] = {}
    for i in range(args.procs):
        wid = f"wcc-w{i + 1}"
        workers[wid] = subprocess.Popen(
            [sys.executable, "-m", "traceguard_worker.main", "--poll", "0.2"],
            cwd=str(ROOT),
            env={**env, "TG_WORKER_ID": wid},
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    pid_by_worker = {wid: p.pid for wid, p in workers.items()}
    print("workers:", pid_by_worker)

    owners: dict[str, set[str]] = {run_id: set() for run_id in run_ids}
    peak_parallel = 0
    deaths_during_run: list[str] = []
    samples = 0
    terminal = {"COMPLETED", "FAILED", "TIMED_OUT", "CANCELLED"}

    def _sample() -> bool:
        nonlocal peak_parallel, samples
        samples += 1
        for wid, proc in workers.items():
            if proc.poll() is not None and wid not in deaths_during_run:
                deaths_during_run.append(wid)
        rows = wfr._query(
            db_path,
            "SELECT run_id, lease_owner, execution_status FROM tg_run",
        )
        active = 0
        for run_id, owner, status in rows:
            if owner and run_id in owners:
                owners[run_id].add(str(owner))
            if status in {"RUNNING", "PREPARING"}:
                active += 1
        peak_parallel = max(peak_parallel, active)
        return bool(rows) and all(r[2] in terminal for r in rows)

    finished = wfr._wait_for(_sample, timeout_s=300, interval_s=0.2)
    ended = time.time()

    for proc in workers.values():
        if proc.poll() is None:
            proc.terminate()
    for proc in workers.values():
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()

    runs = []
    for row in wfr._query(
        db_path,
        "SELECT run_id, execution_status, test_verdict, attempt, fencing_token, "
        "lease_owner, report_json, started_at, ended_at FROM tg_run ORDER BY created_at",
    ):
        runs.append(
            {
                "run_id": row[0],
                "execution_status": row[1],
                "test_verdict": row[2],
                "attempt": row[3],
                "fencing_token": row[4],
                "final_owner": row[5] or "",
                "has_report": bool(row[6]),
                "started_at": row[7],
                "ended_at": row[8],
            }
        )
    evidence_counts = dict(
        wfr._query(db_path, "SELECT run_id, COUNT(*) FROM tg_evidence GROUP BY run_id")
    )
    step_counts = dict(wfr._query(db_path, "SELECT run_id, COUNT(*) FROM tg_step GROUP BY run_id"))
    duplicate_steps = wfr._query(
        db_path,
        "SELECT run_id, step_id, COUNT(*) c FROM tg_step GROUP BY run_id, step_id HAVING c > 1",
    )
    duplicate_events = wfr._query(
        db_path,
        "SELECT producer_id, idempotency_key, COUNT(*) c FROM tg_event "
        "GROUP BY producer_id, idempotency_key HAVING c > 1",
    )
    orphan_steps = wfr._query(
        db_path, "SELECT COUNT(*) FROM tg_step WHERE run_id NOT IN (SELECT run_id FROM tg_run)"
    )[0][0]
    quick_check = wfr._query(db_path, "PRAGMA quick_check")[0][0]

    checks = {
        "four_distinct_os_workers": len(pid_by_worker) == 4
        and len(set(pid_by_worker.values())) == 4,
        "no_worker_died_during_runs": not deaths_during_run,
        "parallel_execution_observed": peak_parallel >= 2,
        "each_run_single_owner": all(len(v) <= 1 for v in owners.values()),
        "all_runs_terminal": finished and len(runs) == args.runs,
        "attempt_not_inflated": all(int(r["attempt"] or 0) == 1 for r in runs),
        "fencing_token_stable": all(int(r["fencing_token"] or 0) == 1 for r in runs),
        "no_duplicate_steps": not duplicate_steps,
        "no_duplicate_events": not duplicate_events,
        "no_orphan_steps": orphan_steps == 0,
        "all_runs_have_evidence": all(evidence_counts.get(r["run_id"], 0) > 0 for r in runs),
        "all_runs_have_steps": all(step_counts.get(r["run_id"], 0) > 0 for r in runs),
        "all_runs_have_report": all(r["has_report"] for r in runs),
        "db_integrity_ok": str(quick_check).lower() in {"ok", "true"},
    }
    report = {
        "round": "RUN-008",
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started)),
        "finished_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ended)),
        "duration_s": round(ended - started, 2),
        "db": str(db_path),
        "teamflow": args.teamflow,
        "runs_queued": len(run_ids),
        "worker_pids": pid_by_worker,
        "owner_history": {k: sorted(v) for k, v in owners.items()},
        "peak_parallel_runs": peak_parallel,
        "db_samples": samples,
        "deaths_during_run": deaths_during_run,
        "runs": runs,
        "evidence_counts": evidence_counts,
        "step_counts": step_counts,
        "duplicate_steps": duplicate_steps,
        "duplicate_events": duplicate_events,
        "orphan_steps": orphan_steps,
        "quick_check": quick_check,
        "checks": checks,
        "passed": all(checks.values()),
    }
    out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps(checks, ensure_ascii=False, indent=1))
    print(f"证据：{out}  passed={report['passed']}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
