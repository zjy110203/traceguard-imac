"""RUN-007 WP4-02（RESUME 重写）：4 个独立 Worker 进程的故障恢复严格验证。

与旧版的差别（旧版的问题已修复）：
1. 记录实验真实 start_time，`killed_after_s` 不再是恒定 0；
2. 记录被杀 Worker 的 PID / Worker ID / 当时处理的 Run ID / 被杀前 fencing token；
3. 接管验证绑定到**同一个 Run**（旧版可被其它 Run 的 token 增长误判为接管成功）；
4. 严格验证：租约过期 → 同一 Run 被另一个 Worker 领取 → 该 Run fencing token 严格增加；
5. 验证旧 Worker 的写入/续租被拒绝（陈旧代次）；不再错误要求 attempt 必须增加
   （attempt 是 Run 级执行尝试编号，Worker 接管由 fencing token 区分）；
6. 验证被杀 Run 的 Evidence / Step / Event 完整、无重复、有报告、终态一致。

产出：docs/assets/run007/worker-fault-recovery-verified.json
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

#: 被杀用例的执行窗口（秒）：足够长，保证 SIGKILL 发生在"执行中"而不是收尾阶段
SLOW_WAIT_MS = 14000


def _prepare_assets(db_path: Path, teamflow: str) -> None:
    """在独立库里创建场景资产：一个执行 ~15s 的用例（运行时间足够长，便于中途杀 Worker）。"""
    for p in ("core", "persistence", "contracts"):
        sys.path.insert(0, str(ROOT / "packages" / p))
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
            {"step_id": "w4", "action": "wait", "value": str(SLOW_WAIT_MS)},
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
    from traceguard_execution.tasks.service import TaskService
    from traceguard_persistence import Database

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
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=10)
    try:
        return list(con.execute(sql, args))
    finally:
        con.close()


def _run_row(db_path: Path, run_id: str) -> dict[str, Any]:
    rows = _query(
        db_path,
        "SELECT run_id, execution_status, test_verdict, attempt, fencing_token, lease_owner, "
        "lease_expires_at, started_at, ended_at, report_json FROM tg_run WHERE run_id = ?",
        (run_id,),
    )
    if not rows:
        return {}
    r = rows[0]
    return {
        "run_id": r[0],
        "execution_status": r[1],
        "test_verdict": r[2],
        "attempt": r[3],
        "fencing_token": r[4],
        "lease_owner": r[5] or "",
        "lease_expires_at": r[6],
        "started_at": r[7],
        "ended_at": r[8],
        "has_report": bool(r[9]),
    }


def _wait_for(predicate, timeout_s: float, interval_s: float = 0.2) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval_s)
    return bool(predicate())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=".traceguard/run007resume/wfr/workers.db")
    ap.add_argument("--runs", type=int, default=4)
    ap.add_argument("--procs", type=int, default=4)
    ap.add_argument("--teamflow", default="http://127.0.0.1:8021")
    ap.add_argument("--lease-ttl", type=float, default=6.0)
    ap.add_argument("--out", default="docs/assets/run007/worker-fault-recovery-verified.json")
    args = ap.parse_args(argv)

    started_at = time.time()
    started_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started_at))
    db_path = ROOT / args.db
    db_path.parent.mkdir(parents=True, exist_ok=True)
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(db_path) + suffix)
        if p.exists():
            p.unlink()

    _prepare_assets(db_path, args.teamflow)
    run_ids = _queue_runs(db_path, args.runs)
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
    worker_ids = [f"wfr-w{i + 1}" for i in range(args.procs)]
    procs: dict[str, subprocess.Popen] = {}
    for wid in worker_ids:
        procs[wid] = subprocess.Popen(
            [sys.executable, "-m", "traceguard_worker.main", "--poll", "0.2"],
            cwd=str(ROOT),
            env={**env, "TG_WORKER_ID": wid},
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    pid_by_worker = {wid: p.pid for wid, p in procs.items()}
    print(f"workers: {pid_by_worker}")

    # ---------------------------------------------------------------- 选中被杀目标
    victim: dict[str, Any] = {}

    def _pick_victim() -> bool:
        rows = _query(
            db_path,
            "SELECT run_id, lease_owner, fencing_token, attempt, execution_status "
            "FROM tg_run WHERE lease_owner != '' AND execution_status IN ('RUNNING','PREPARING')",
        )
        for run_id, owner, token, attempt, status in rows:
            if owner not in procs:
                continue
            steps = _query(db_path, "SELECT COUNT(*) FROM tg_step WHERE run_id = ?", (run_id,))[0][
                0
            ]
            if steps < 1:  # 等到确实进入执行（已有步骤落库）
                continue
            row = _run_row(db_path, run_id)
            victim.update(
                {
                    "run_id": run_id,
                    "worker_id": owner,
                    "pid": pid_by_worker[owner],
                    "fencing_token_before": token,
                    "attempt_before": attempt,
                    "status_before": status,
                    "steps_before_kill": steps,
                    "started_at": row.get("started_at"),
                    "lease_expires_at_before": row.get("lease_expires_at"),
                }
            )
            return True
        return False

    picked = _wait_for(_pick_victim, timeout_s=90, interval_s=0.2)
    kill_ts = time.time()
    victim_pid = int(victim.get("pid") or 0)
    if picked and victim_pid:
        os.kill(victim_pid, signal.SIGKILL)
        print(
            f"SIGKILL pid={victim_pid} worker={victim['worker_id']} "
            f"run=...{victim['run_id'][-12:]} fencing={victim['fencing_token_before']} "
            f"steps={victim['steps_before_kill']}"
        )
    kill_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(kill_ts))

    # 等待被杀进程真实退出（证明 SIGKILL 生效）
    killed_exit_code = None
    if victim_pid:
        victim_proc = procs.get(str(victim.get("worker_id")))
        if victim_proc is not None:
            try:
                killed_exit_code = victim_proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                killed_exit_code = -999

    # ---------------------------------------------------------------- 接管验证
    victim_run = str(victim.get("run_id") or "")

    def _taken_over() -> bool:
        if not victim_run:
            return False
        row = _run_row(db_path, victim_run)
        return bool(
            row
            and row["lease_owner"]
            and row["lease_owner"] != victim.get("worker_id")
            and int(row["fencing_token"] or 0) > int(victim.get("fencing_token_before") or 0)
        )

    taken = _wait_for(_taken_over, timeout_s=90, interval_s=0.2)
    takeover_ts = time.time()
    victim_after = _run_row(db_path, victim_run) if victim_run else {}
    takeover_worker = victim_after.get("lease_owner", "")
    takeover_fencing = int(victim_after.get("fencing_token") or 0)

    # 陈旧代次写入必须被拒绝（等价于"被杀 Worker 复活后再提交"）
    stale_write: dict[str, Any] = {"checked": False}
    if victim_run and victim.get("worker_id"):
        try:
            from traceguard_execution.tasks.service import TaskService
            from traceguard_persistence import Database

            db = Database(f"sqlite:///{db_path}")
            tasks = TaskService(db)
            renewed = tasks.renew_lease(
                victim_run, str(victim["worker_id"]), int(victim.get("fencing_token_before") or 0)
            )
            try:
                tasks.submit_result(
                    victim_run,
                    str(victim["worker_id"]),
                    int(victim.get("fencing_token_before") or 0),
                    execution_status="COMPLETED",
                    test_verdict="PASSED",
                )
                rejected = False
                detail = "旧代次写入被接受（严重缺陷）"
            except Exception as exc:  # noqa: BLE001
                rejected = "STALE_FENCING_TOKEN" in str(exc) or "CONFLICT" in str(exc)
                detail = str(exc)[:200]
            db.dispose()
            stale_write = {
                "checked": True,
                "renew_rejected": renewed is False,
                "submit_rejected": rejected,
                "detail": detail,
            }
        except Exception as exc:  # noqa: BLE001
            stale_write = {"checked": False, "error": str(exc)[:200]}

    # ---------------------------------------------------------------- 等待全部终态
    terminal = {"COMPLETED", "FAILED", "TIMED_OUT", "CANCELLED"}

    def _all_terminal() -> bool:
        rows = _query(db_path, "SELECT execution_status FROM tg_run")
        return bool(rows) and all(r[0] in terminal for r in rows)

    finished = _wait_for(_all_terminal, timeout_s=240, interval_s=1.0)
    ended_at = time.time()

    for wid, p in procs.items():
        if wid == victim.get("worker_id"):
            continue
        if p.poll() is None:
            p.terminate()
    for wid, p in procs.items():
        if wid == victim.get("worker_id"):
            continue
        try:
            p.wait(timeout=8)
        except subprocess.TimeoutExpired:
            p.kill()

    # ---------------------------------------------------------------- 数据核对
    runs = [
        {
            "run_id": r[0],
            "execution_status": r[1],
            "test_verdict": r[2],
            "attempt": r[3],
            "fencing_token": r[4],
            "final_owner": (r[5] or ""),
            "has_report": bool(r[6]),
        }
        for r in _query(
            db_path,
            "SELECT run_id, execution_status, test_verdict, attempt, fencing_token, "
            "lease_owner, report_json FROM tg_run ORDER BY created_at",
        )
    ]
    evidence_counts = dict(
        _query(db_path, "SELECT run_id, COUNT(*) FROM tg_evidence GROUP BY run_id")
    )
    step_counts = dict(_query(db_path, "SELECT run_id, COUNT(*) FROM tg_step GROUP BY run_id"))
    event_counts = dict(
        _query(
            db_path,
            "SELECT context_refs, COUNT(*) FROM tg_event GROUP BY context_refs",
        )
    )
    dup_events = _query(
        db_path,
        "SELECT producer_id, idempotency_key, COUNT(*) c FROM tg_event "
        "GROUP BY producer_id, idempotency_key HAVING c > 1",
    )
    orphan_steps = _query(
        db_path, "SELECT COUNT(*) FROM tg_step WHERE run_id NOT IN (SELECT run_id FROM tg_run)"
    )[0][0]
    dup_steps = _query(
        db_path,
        "SELECT run_id, step_id, COUNT(*) c FROM tg_step GROUP BY run_id, step_id HAVING c > 1",
    )
    victim_final = _run_row(db_path, victim_run) if victim_run else {}
    victim_events = (
        _query(
            db_path, "SELECT COUNT(*) FROM tg_event WHERE context_refs LIKE ?", (f"%{victim_run}%",)
        )[0][0]
        if victim_run
        else 0
    )
    worker_exit_codes = {wid: p.returncode for wid, p in procs.items()}
    survivors_alive = [
        wid for wid, p in procs.items() if wid != victim.get("worker_id") and p.returncode is None
    ]

    checks = {
        "victim_selected_while_running": bool(picked and victim.get("steps_before_kill", 0) >= 1),
        "kill_recorded": bool(victim_pid and kill_ts),
        "killed_after_s_positive": (kill_ts - started_at) > 0 if victim_pid else False,
        "worker_process_died": killed_exit_code is not None and killed_exit_code != 0,
        "same_run_taken_over": bool(taken) and takeover_worker not in ("", victim.get("worker_id")),
        "takeover_worker_is_other_process": takeover_worker in pid_by_worker
        and takeover_worker != victim.get("worker_id"),
        "fencing_token_strictly_increased": takeover_fencing
        > int(victim.get("fencing_token_before") or 0),
        "victim_run_reached_terminal": victim_final.get("execution_status") in terminal,
        "all_runs_terminal": finished and all(r["execution_status"] in terminal for r in runs),
        "no_duplicate_events": not dup_events,
        "no_duplicate_steps": not dup_steps,
        "no_orphan_steps": orphan_steps == 0,
        "evidence_persisted": all(evidence_counts.get(r["run_id"], 0) > 0 for r in runs),
        "steps_persisted": all(step_counts.get(r["run_id"], 0) > 0 for r in runs),
        "victim_run_has_events": victim_events > 0,
        "reports_present": all(r["has_report"] for r in runs),
        "stale_write_rejected": bool(
            stale_write.get("submit_rejected") and stale_write.get("renew_rejected")
        ),
    }
    report = {
        "round": "RUN-007-RESUME",
        "started_at": started_iso,
        "kill_at": kill_iso,
        "ended_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ended_at)),
        "duration_s": round(ended_at - started_at, 1),
        "killed_after_s": round(kill_ts - started_at, 2) if victim_pid else 0,
        "takeover_latency_s": round(takeover_ts - kill_ts, 2) if taken else None,
        "lease_ttl_s": args.lease_ttl,
        "db": str(db_path.relative_to(ROOT)),
        "procs": args.procs,
        "worker_pids": pid_by_worker,
        "queued_runs": len(run_ids),
        "victim": victim,
        "killed_pid": victim_pid,
        "killed_worker_id": victim.get("worker_id", ""),
        "killed_worker_exit_code": killed_exit_code,
        "takeover_worker": takeover_worker,
        "takeover_fencing_token": takeover_fencing,
        "stale_write": stale_write,
        "survivors_alive_before_shutdown": survivors_alive,
        "worker_exit_codes": worker_exit_codes,
        "runs": runs,
        "victim_run_final": victim_final,
        "evidence_counts": evidence_counts,
        "step_counts": step_counts,
        "event_counts_by_context": {str(k): v for k, v in list(event_counts.items())[:40]},
        "duplicate_event_keys": dup_events,
        "duplicate_steps": dup_steps,
        "orphan_steps": orphan_steps,
        "checks": checks,
        "passed": all(checks.values()),
    }
    out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps(checks, ensure_ascii=False, indent=2))
    for r in runs:
        print(
            f"  ...{r['run_id'][-12:]} {r['execution_status']} verdict={r['test_verdict']} "
            f"attempt={r['attempt']} fencing={r['fencing_token']} owner={r['final_owner']}"
        )
    print(f"\n证据：{out}  passed={report['passed']}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
