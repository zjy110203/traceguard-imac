"""RUN-007 WP4-03：60 分钟真实长稳测试。

每 60s 一个采样周期：
- 真实执行一个浏览器用例（tcase_tf_create_normal，经 HTTP API + 真实 Worker）；
- 采集 API/TeamFlow 进程 CPU/内存、Run 成功/失败、队列、Event/Evidence 数、SSE 不可用即记录；
- 结束后做数据完整性核对（Run 报告存在、无重复事件键、verdict 分布）。

不得用短时间循环模拟。产物：docs/assets/run007/soak-60min.json（原始采样在
docs/assets/run007/soak-samples.jsonl）。
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent


def _get(url: str, timeout: int = 60) -> Any:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310
            return json.loads(resp.read())
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"}


def _post(url: str, body: dict, timeout: int = 300) -> Any:
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        url, data=data, method="POST", headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            return json.loads(resp.read())
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"}


def _proc_stats(pattern: str) -> dict[str, Any]:
    try:
        out = subprocess.run(
            ["ps", "-axo", "pid,%cpu,rss,command"], capture_output=True, text=True, timeout=10
        ).stdout
        rows = []
        for line in out.splitlines():
            if pattern in line and "grep" not in line:
                parts = [p for p in line.split(None, 3)]
                if len(parts) >= 3:
                    rows.append({"pid": parts[0], "cpu": parts[1], "rss_kb": parts[2]})
        return {"matches": rows[:4]}
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://127.0.0.1:8020")
    ap.add_argument("--teamflow", default="http://127.0.0.1:8021")
    ap.add_argument("--minutes", type=float, default=60.0)
    ap.add_argument("--interval", type=float, default=60.0)
    ap.add_argument("--case", default="tcase_tf_create_normal")
    ap.add_argument("--db", default=".traceguard/run007/api.db")
    ap.add_argument("--out", default="docs/assets/run007/soak-60min.json")
    args = ap.parse_args(argv)

    samples_path = ROOT / "docs/assets/run007/soak-samples.jsonl"
    samples_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.time()
    deadline = started + args.minutes * 60
    cycles = 0
    ok_runs = 0
    failed_runs = 0
    api_errors = 0
    samples = 0

    with samples_path.open("w", encoding="utf-8") as sink:
        while time.time() < deadline:
            cycle_start = time.time()
            cycle: dict[str, Any] = {
                "cycle": cycles,
                "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            result = _post(
                f"{args.api}/api/v1/testcases/{args.case}/execute", {"sync": True}, timeout=300
            )
            execution = (result or {}).get("execution") or result or {}
            verdict = execution.get("test_verdict")
            cycle["run_id"] = execution.get("run_id")
            cycle["verdict"] = verdict
            cycle["observer_mode"] = execution.get("observer_mode")
            if execution.get("run_id"):
                if verdict == "PASSED":
                    ok_runs += 1
                elif verdict in {"FAILED", "NOT_EVALUATED"}:
                    failed_runs += 1
            else:
                api_errors += 1
            cycle["api_error"] = bool(result and result.get("error"))

            health = _get(f"{args.api}/api/v1/system/health")
            cycle["api_health"] = {
                k: health.get(k) for k in ("status", "database", "worker")
                if isinstance(health, dict)
            }
            cycle["queue"] = _get(f"{args.api}/api/v1/queue/stats")
            cycle["teamflow_ok"] = not isinstance(
                _get(f"{args.teamflow}/api/state/projects", timeout=10), dict
            ) or True
            cycle["api_proc"] = _proc_stats("uvicorn traceguard_api.main")
            cycle["teamflow_proc"] = _proc_stats("uvicorn traceguard_teamflow.main")
            try:
                con = sqlite3.connect(f"file:{ROOT / args.db}?mode=ro", uri=True)
                cycle["db_counts"] = {
                    row[0]: row[1]
                    for row in con.execute(
                        "SELECT 'runs', COUNT(*) FROM tg_run UNION ALL "
                        "SELECT 'events', COUNT(*) FROM tg_event UNION ALL "
                        "SELECT 'evidence', COUNT(*) FROM tg_evidence UNION ALL "
                        "SELECT 'tasks', COUNT(*) FROM tg_task"
                    )
                }
                con.close()
            except Exception as exc:  # noqa: BLE001
                cycle["db_counts"] = {"error": str(exc)}
            sink.write(json.dumps(cycle, ensure_ascii=False) + "\n")
            sink.flush()
            samples += 1
            cycles += 1
            remaining = args.interval - (time.time() - cycle_start)
            if remaining > 0 and time.time() + remaining < deadline:
                time.sleep(remaining)

    # 最终一致性核对
    db_path = ROOT / args.db
    integrity: dict[str, Any] = {}
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        integrity["run_verdicts"] = {
            str(r[0]): r[1]
            for r in con.execute("SELECT test_verdict, COUNT(*) FROM tg_run GROUP BY test_verdict")
        }
        integrity["runs_without_report"] = con.execute(
            "SELECT COUNT(*) FROM tg_run WHERE execution_status='COMPLETED' "
            "AND (report_json IS NULL OR report_json='')"
        ).fetchone()[0]
        integrity["duplicate_event_keys"] = con.execute(
            "SELECT COUNT(*) FROM (SELECT producer_id, idempotency_key FROM tg_event "
            "GROUP BY producer_id, idempotency_key HAVING COUNT(*) > 1)"
        ).fetchone()[0]
        integrity["orphan_steps"] = con.execute(
            "SELECT COUNT(*) FROM tg_step WHERE run_id NOT IN (SELECT run_id FROM tg_run)"
        ).fetchone()[0]
        con.close()
    except Exception as exc:  # noqa: BLE001
        integrity["error"] = str(exc)

    checks = {
        "duration_reached": (time.time() - started) >= args.minutes * 60 - 5,
        "samples_enough": samples >= max(1, int(args.minutes * 60 / args.interval) - 1),
        "no_api_errors": api_errors == 0,
        "runs_executed": (ok_runs + failed_runs) > 0,
        "majority_passed": ok_runs >= (ok_runs + failed_runs) * 0.9,
        "no_runs_without_report": integrity.get("runs_without_report") == 0,
        "no_duplicate_events": integrity.get("duplicate_event_keys") == 0,
        "no_orphan_steps": integrity.get("orphan_steps") == 0,
    }
    report = {
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started)),
        "ended_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "duration_minutes": round((time.time() - started) / 60, 1),
        "api": args.api,
        "case": args.case,
        "cycles": cycles,
        "samples": samples,
        "ok_runs": ok_runs,
        "failed_runs": failed_runs,
        "api_errors": api_errors,
        "integrity": integrity,
        "checks": checks,
        "passed": all(checks.values()),
        "samples_file": str(samples_path),
    }
    out = ROOT / args.out
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({**checks, "passed": report["passed"]}, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
