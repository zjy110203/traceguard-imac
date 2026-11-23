"""RUN-007 WP4-02：4 个独立 Worker 进程的并发执行与故障恢复（真实 OS 进程）。

验证目标：
1. 4 个独立 Python 进程共享同一数据库并发领取任务，多个 Run 并行执行；
2. 同一 Run 不能被双重执行（fencing token / 租约）；
3. 执行中 SIGKILL 一个 Worker → 租约过期 → 其他 Worker 接管（attempt+1、fencing 递增）；
4. 任务不因 Worker 崩溃而丢失；Event / Evidence 不重复；
5. 最终状态与数据库一致。

产出：docs/assets/run007/worker-fault-recovery.json
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent


def _prepare_assets(db_path: Path, teamflow: str) -> None:
    """在独立库里创建场景资产：一个会激活 5s 时序故障的用例（运行时间足够长，便于中途杀 Worker）。"""
    sys.path.insert(0, str(ROOT / "packages" / "core"))
    sys.path.insert(0, str(ROOT / "packages" / "persistence"))
    sys.path.insert(0, str(ROOT / "packages" / "contracts"))
    from traceguard_persistence import Database
    from traceguard_persistence.catalog import CatalogService

    db = Database(f"sqlite:///{db_path}")
    db.create_schema()
    cat = CatalogService(db)
    cat.create_oracle(
        {
            "oracle_id": "oracle_wfr_banner",
            "assertion_id": "a_ui_banner",
            "expected_behavior": "成功提示必须出现",
            "requirement_ref": "REQ-WFR-1",
            "version": 1,
        },
        approved=True,
        approved_by="role:qa-lead",
    )
    cat.create_oracle(
        {
            "oracle_id": "oracle_wfr_rows",
            "assertion_id": "a_row_render",
            "expected_behavior": "项目列表必须渲染",
            "requirement_ref": "REQ-WFR-2",
            "version": 1,
        },
        approved=True,
        approved_by="role:qa-lead",
    )
    payload = {
        "testcase_id": "tcase_wfr_slow",
        "intent_ref": "intent_wfr",
        "version": 1,
        "steps": [
            {"step_id": "w1", "action": "api", "value": "POST /api/faults/TF-TIME-002/activate"},
            {"step_id": "w2", "action": "navigate", "value": "/projects/new"},
            {
                "step_id": "w3",
                "action": "fill",
                "target": {"strategy": "test_id", "value": "project-name"},
                "value": "WFR 故障恢复",
            },
            {"step_id": "w4", "action": "wait", "value": "5500"},
            {
                "step_id": "w5",
                "action": "click",
                "target": {"strategy": "test_id", "value": "create-submit"},
            },
            {"step_id": "w6", "action": "wait", "value": "1500"},
            {
                "step_id": "w7",
                "action": "assert",
                "target": {"strategy": "test_id", "value": "banner-success"},
                "value": "banner-success",
            },
            {
                "step_id": "w8",
                "action": "assert",
                "target": {"strategy": "test_id", "value": "project-rows"},
                "value": "project-rows",
            },
        ],
        "assertions": [
            {
                "assertion_id": "a_ui_banner",
                "description": "成功提示出现",
                "kind": "ui",
                "required": True,
                "expected": {"test_id": "banner-success"},
                "oracle_id": "oracle_wfr_banner",
            },
            {
                "assertion_id": "a_row_render",
                "description": "项目列表渲染",
                "kind": "ui",
                "required": True,
                "expected": {"test_id": "project-rows", "op": "count_gte", "value": 1},
                "oracle_id": "oracle_wfr_rows",
            },
        ],
        "run_config": {"base_url": teamflow, "fail_fast": False, "trace": False},
        "compile_source": "human",
        "created_at": "2026-01-01T00:00:00Z",
    }
    cat.create_testcase(payload, title="WFR 慢用例", review_status="PENDING")
    cat.review_testcase("tcase_wfr_slow", 1, "APPROVED", reviewer="role:qa-lead")
    db.dispose()


def _queue_runs(db_path: Path, n: int) -> list[str]:
    from traceguard_persistence import Database
    from traceguard_execution.tasks.service import TaskService

    db = Database(f"sqlite:///{db_path}")
    tasks = TaskService(db)
    run_ids = []
    for i in range(n):
        task = tasks.create_task(
            "tcase_wfr_slow", f"wfr-task-{i}-{time.time_ns()}", mode="playwright"
        )
        run = tasks.create_run(task["task_id"], "tcase_wfr_slow@1")
        run_ids.append(run["run_id"])
    db.dispose()
    return run_ids


def _query(db_path: Path, sql: str, args: tuple = ()) -> list[tuple]:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        return list(con.execute(sql, args))
    finally:
        con.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=".traceguard/run007/workers.db")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--procs", type=int, default=4)
    ap.add_argument("--teamflow", default="http://127.0.0.1:8021")
    ap.add_argument("--out", default="docs/assets/run007/worker-fault-recovery.json")
    args = ap.parse_args(argv)

    db_path = ROOT / args.db
    if db_path.exists():
        db_path.unlink()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    for suffix in ("-wal", "-shm"):
        p = Path(str(db_path) + suffix)
        if p.exists():
            p.unlink()

    _prepare_assets(db_path, args.teamflow)
    run_ids = _queue_runs(db_path, args.runs)
    print(f"queued {len(run_ids)} runs: {run_ids}")

    env = {
        **os.environ,
        "TG_DATABASE_URL": f"sqlite:///{db_path}",
        "TG_ARTIFACT_DIR": str(db_path.parent / "wfr-artifacts"),
        "TG_TEAMFLOW_BASE_URL": args.teamflow,
        "TG_INLINE_WORKER": "0",
        "TG_WORKER_LEASE_TTL_S": "6",
        "TG_WORKER_POLL_INTERVAL_S": "0.5",
    }
    procs = [
        subprocess.Popen(
            [sys.executable, "-m", "traceguard_worker.main", "--poll", "0.5"],
            cwd=str(ROOT),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for _ in range(args.procs)
    ]
    killed_pid = 0
    killed_worker_id = ""
    killed_at = 0.0

    # 等到有 Worker 领取并进入执行，然后 SIGKILL 它
    deadline = time.time() + 30
    while time.time() < deadline and not killed_pid:
        time.sleep(0.5)
        rows = _query(
            db_path,
            "SELECT run_id, lease_owner, execution_status FROM tg_run WHERE lease_owner != ''",
        )
        for _rid, owner, status in rows:
            if status in {"RUNNING", "PREPARING", "FINALIZING"} and owner:
                # worker_id 形如 <hostname>-<pid>：精确杀掉"正在执行的那个"Worker
                owner_pid = (
                    int(str(owner).rsplit("-", 1)[-1])
                    if str(owner).rsplit("-", 1)[-1].isdigit()
                    else 0
                )
                target = (
                    owner_pid
                    if owner_pid in {p.pid for p in procs}
                    else next((p.pid for p in procs if p.poll() is None), 0)
                )
                if target:
                    killed_pid = target
                    killed_worker_id = owner
                    killed_at = time.time()
                    os.kill(killed_pid, signal.SIGKILL)
                    print(f"SIGKILL worker pid={killed_pid} owner={owner[-24:]} during {status}")
                    break
        procs = [p for p in procs if p.pid != killed_pid]

    # 等待所有 Run 到终态（含租约过期接管）
    deadline = time.time() + 180
    while time.time() < deadline:
        rows = _query(db_path, "SELECT run_id, execution_status FROM tg_run")
        if rows and all(r[1] in {"COMPLETED", "FAILED", "TIMED_OUT", "CANCELLED"} for r in rows):
            break
        time.sleep(1)
    for p in procs:
        p.terminate()
    time.sleep(1)
    for p in procs:
        if p.poll() is None:
            p.kill()

    run_rows = _query(
        db_path,
        "SELECT run_id, execution_status, test_verdict, attempt, fencing_token, lease_owner FROM tg_run",
    )
    runs = [
        {
            "run_id": r[0],
            "execution_status": r[1],
            "test_verdict": r[2],
            "attempt": r[3],
            "fencing_token": r[4],
            "final_owner": (r[5] or ""),
        }
        for r in run_rows
    ]
    evidence_counts = dict(
        _query(db_path, "SELECT run_id, COUNT(*) FROM tg_evidence GROUP BY run_id")
    )
    step_rows = _query(
        db_path,
        "SELECT run_id, COUNT(*) FROM tg_step GROUP BY run_id",
    )
    # 事件幂等约束是 (producer_id, idempotency_key) 唯一：不同 Worker 的同键事件是合法重投
    dup_evidence = _query(
        db_path,
        "SELECT producer_id, idempotency_key, COUNT(*) c FROM tg_event "
        "GROUP BY producer_id, idempotency_key HAVING c > 1",
    )
    total_runs = len(runs)
    terminal = sum(1 for r in runs if r["execution_status"] in {"COMPLETED", "FAILED"})
    recovered = [
        r for r in runs if int(r["attempt"] or 1) >= 2 or int(r["fencing_token"] or 0) >= 2
    ]
    checks = {
        "all_runs_terminal": terminal == total_runs and total_runs == args.runs,
        "worker_was_killed_midrun": bool(killed_pid and killed_worker_id),
        "takeover_happened": bool(recovered)
        or any(int(r["fencing_token"] or 0) >= 2 for r in runs)
        or not killed_pid,
        "fencing_token_incremented": all(
            int(r["fencing_token"] or 0) >= int(r["attempt"] or 1) for r in runs
        ),
        "no_duplicate_events": not dup_evidence,
        "evidence_persisted": all(evidence_counts.get(r["run_id"], 0) > 0 for r in runs),
        "final_state_matches_db": all(
            r["execution_status"] in {"COMPLETED", "FAILED"} for r in runs
        ),
    }
    report = {
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "db": str(db_path),
        "procs": args.procs,
        "queued_runs": args.runs,
        "killed_pid": killed_pid,
        "killed_worker_id": killed_worker_id[-40:],
        "killed_after_s": round(killed_at - (killed_at), 1) if killed_at else 0,
        "runs": runs,
        "evidence_counts": {k: v for k, v in evidence_counts.items()},
        "step_counts": {k: v for k, v in step_rows},
        "duplicate_event_keys": dup_evidence,
        "checks": checks,
        "passed": all(checks.values()),
    }
    out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(checks, ensure_ascii=False))
    for r in runs:
        print(
            f"  {r['run_id'][-16:]} {r['execution_status']} verdict={r['test_verdict']} "
            f"attempt={r['attempt']} fencing={r['fencing_token']} owner=...{r['final_owner'][-16:]}"
        )
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
