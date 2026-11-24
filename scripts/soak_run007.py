"""RUN-007 WP4-03（RESUME 重写）：60 分钟真实长稳测试。

与旧版的差别（旧版三个问题已修）：
1. 旧版用 `sync: true` 绕过独立 Worker → 现在全部 `sync: false` 入队，由脚本自己拉起的
   **4 个独立 Worker 进程**消费；API 以 `TG_INLINE_WORKER=0` 启动（并校验未启用内嵌 Worker）；
2. 旧版 `teamflow_ok = ... or True` 恒为真 → 现在每个周期都真实探测被测应用状态接口，
   失败即计为错误并记录；
3. 旧版只在注释里说"检测 SSE" → 现在每个周期真实建立 SSE 连接、统计事件帧/心跳，
   并在 Run 结束后用 `Last-Event-ID` 续传验证（记录断连与恢复行为）。

架构：
    独立 API（脚本启动） → 独立 SQLite 测试库 → 持久化 Run Queue
      → Worker 1..4（脚本启动的真实 OS 进程） → 真实 Chromium → TeamFlow

产出：
    docs/assets/run007/soak-60min.json（汇总 + checks）
    docs/assets/run007/soak-samples.jsonl（每周期原始采样）
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
TERMINAL = {"COMPLETED", "FAILED", "TIMED_OUT", "CANCELLED"}


def _rel(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


# --------------------------------------------------------------------- http
def _get(url: str, timeout: float = 30) -> Any:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310
            return json.loads(resp.read().decode() or "{}")
    except Exception as exc:  # noqa: BLE001
        return {"__error__": f"{type(exc).__name__}: {exc}"}


def _post(url: str, body: dict, timeout: float = 120) -> Any:
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            return json.loads(resp.read().decode() or "{}")
    except Exception as exc:  # noqa: BLE001
        return {"__error__": f"{type(exc).__name__}: {exc}"}


# --------------------------------------------------------------------- sse
class SseReader(threading.Thread):
    """真实 SSE 客户端：统计事件帧与心跳，保留游标用于续传测试。"""

    def __init__(self, base: str, run_id: str, last_event_id: str = "") -> None:
        super().__init__(daemon=True)
        self.url = f"{base}/events/stream?run_id={run_id}&heartbeat_seconds=2&poll_interval=0.3"
        self.last_event_id = last_event_id
        self.stop_event = threading.Event()
        self.events: list[dict[str, Any]] = []
        self.heartbeats = 0
        self.retry_frames = 0
        self.status = 0
        self.error = ""

    def run(self) -> None:
        headers = {"Accept": "text/event-stream"}
        if self.last_event_id:
            headers["Last-Event-ID"] = self.last_event_id
        req = urllib.request.Request(self.url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310
                self.status = resp.status
                current: dict[str, str] = {}
                while not self.stop_event.is_set():
                    raw = resp.readline()
                    if not raw:
                        break
                    line = raw.decode(errors="replace").rstrip("\n")
                    if line.startswith("id:"):
                        current["id"] = line[3:].strip()
                    elif line.startswith("event:"):
                        current["event"] = line[6:].strip()
                    elif line.startswith("retry:"):
                        self.retry_frames += 1
                    elif line.startswith(":"):
                        self.heartbeats += 1
                    elif line == "":
                        if current.get("id") and current.get("id") != self.last_event_id:
                            self.events.append(dict(current))
                            self.last_event_id = current["id"]
                        current = {}
        except Exception as exc:  # noqa: BLE001 - 连接中断本身就是可观测行为
            self.error = f"{type(exc).__name__}: {exc}"[:200]


# --------------------------------------------------------------------- procs
def _proc_stats(pid: int) -> dict[str, Any]:
    """进程资源快照：优先 `ps`，其次 `/proc`（Linux CI），再退化为存活探测。

    沙箱可能禁止执行 `ps`（Operation not permitted）。此时不能把"取不到资源"当成
    "进程健康"，因此逐级回退，并在结果里显式标注 `source`，让证据可审计。
    """
    if not pid:
        return {"pid": 0}
    try:
        out = subprocess.run(
            ["ps", "-p", str(pid), "-o", "%cpu=,rss=,etime="],
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()
        if not out:
            return {"pid": pid, "alive": _ping(pid), "source": "ps"}
        parts = out.split(None, 2)
        cpu, rss, etime = parts if len(parts) == 3 else ("0", "0", "")
        return {
            "pid": pid,
            "alive": True,
            "cpu_pct": float(cpu),
            "rss_mb": round(int(rss) / 1024, 1),
            "elapsed": etime,
            "source": "ps",
        }
    except Exception as exc:  # noqa: BLE001
        ps_error = f"{type(exc).__name__}: {exc}"[:120]

    # 回退 1：Linux /proc（不依赖外部命令）
    try:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        fields = raw.rsplit(")", 1)[1].split()
        ticks = os.sysconf("SC_CLK_TCK") or 100
        cpu_s = (int(fields[11]) + int(fields[12])) / ticks
        rss_pages = int(fields[21])
        return {
            "pid": pid,
            "alive": True,
            "cpu_s_total": round(cpu_s, 2),
            "rss_mb": round(rss_pages * os.sysconf("SC_PAGE_SIZE") / 1024 / 1024, 1),
            "elapsed": "",
            "source": "procfs",
            "ps_error": ps_error,
        }
    except Exception:  # noqa: BLE001 - 无 /proc（macOS）或进程不存在
        pass

    # 回退 2：只报告存活，明确说明资源指标不可用（不得伪装成 0）
    alive = _ping(pid)
    return {
        "pid": pid,
        "alive": alive,
        "cpu_pct": None,
        "rss_mb": None,
        "source": "liveness_only",
        "unavailable_reason": f"ps 不可用且无 /proc（沙箱限制）: {ps_error}",
    }


def _ping(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _wait_http(url: str, timeout_s: float = 90) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        data = _get(url, timeout=5)
        if isinstance(data, dict) and data.get("status") == "ok":
            return True
        time.sleep(1)
    return False


def _db_query(db: Path, sql: str) -> list[tuple]:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=15)
    try:
        return list(con.execute(sql))
    finally:
        con.close()


def _db_snapshot(db: Path) -> dict[str, Any]:
    snap: dict[str, Any] = {}
    try:
        for name, table in (
            ("runs", "tg_run"),
            ("tasks", "tg_task"),
            ("events", "tg_event"),
            ("evidence", "tg_evidence"),
            ("steps", "tg_step"),
            ("repairs", "tg_doc"),
        ):
            try:
                snap[name] = _db_query(db, f"SELECT COUNT(*) FROM {table}")[0][0]
            except sqlite3.Error as exc:
                snap[name] = f"ERR:{exc}"[:80]
        snap["size_mb"] = round(db.stat().st_size / (1024 * 1024), 2) if db.exists() else 0
        snap["wal_mb"] = (
            round(Path(str(db) + "-wal").stat().st_size / (1024 * 1024), 2)
            if Path(str(db) + "-wal").exists()
            else 0
        )
    except Exception as exc:  # noqa: BLE001
        snap["error"] = str(exc)[:200]
    return snap


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--teamflow", default="http://127.0.0.1:8021")
    ap.add_argument("--port", type=int, default=8030)
    ap.add_argument("--minutes", type=float, default=60.0)
    ap.add_argument("--interval", type=float, default=60.0)
    ap.add_argument("--case", default="tcase_tf_create_normal")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--root", default=".traceguard/run007soak")
    ap.add_argument("--out", default="docs/assets/run007/soak-60min.json")
    ap.add_argument("--samples", default="docs/assets/run007/soak-samples.jsonl")
    args = ap.parse_args(argv)

    api = f"http://127.0.0.1:{args.port}"
    base = ROOT / args.root
    db_path = base / "api.db"
    artifacts = base / "artifacts"
    if base.exists():
        shutil.rmtree(base)  # 独立测试库：每次长稳都是干净环境（不碰正式库）
    artifacts.mkdir(parents=True, exist_ok=True)

    samples_path = ROOT / args.samples
    samples_path.parent.mkdir(parents=True, exist_ok=True)
    report_path = ROOT / args.out
    report_path.parent.mkdir(parents=True, exist_ok=True)

    env = {
        **os.environ,
        "TG_DATABASE_URL": f"sqlite:///{db_path}",
        "TG_ARTIFACT_DIR": str(artifacts),
        "TG_TEAMFLOW_BASE_URL": args.teamflow,
        "TG_INLINE_WORKER": "0",  # 关键：API 不执行 Run
        "TG_LOG_JSON": "false",
        "TG_WORKER_LEASE_TTL_S": "45",
        "TG_WORKER_POLL_INTERVAL_S": "0.5",
    }
    api_log = (ROOT / "logs" / "soak-api.log").open("w", encoding="utf-8")
    api_proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "traceguard_api.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(args.port),
            "--log-level",
            "warning",
        ],
        cwd=str(ROOT),
        env=env,
        stdout=api_log,
        stderr=subprocess.STDOUT,
    )
    print(f"API pid={api_proc.pid} db={db_path}")
    if not _wait_http(f"{api}/api/v1/system/health"):
        print("API 未能启动，终止（不产出任何长稳结论）")
        api_proc.kill()
        return 2

    health0 = _get(f"{api}/api/v1/system/health")
    if (health0.get("worker") or {}).get("inline_running"):
        print("API 启用了内嵌 Worker：长稳必须由独立 Worker 执行，终止")
        api_proc.kill()
        return 2

    workers: dict[str, subprocess.Popen] = {}
    wlogs = []
    for i in range(args.workers):
        wid = f"soak-w{i + 1}"
        log = (ROOT / "logs" / f"soak-{wid}.log").open("w", encoding="utf-8")
        wlogs.append(log)
        workers[wid] = subprocess.Popen(
            [sys.executable, "-m", "traceguard_worker.main", "--poll", "0.5"],
            cwd=str(ROOT),
            env={**env, "TG_WORKER_ID": wid},
            stdout=log,
            stderr=subprocess.STDOUT,
        )
    print("workers:", {k: v.pid for k, v in workers.items()})

    _post(f"{api}/api/v1/system/seed", {}, timeout=300)
    tf_pid = 0
    try:
        out = subprocess.run(
            ["pgrep", "-f", "traceguard_teamflow.main"], capture_output=True, text=True, timeout=10
        ).stdout.split()
        tf_pid = int(out[0]) if out else 0
    except Exception:  # noqa: BLE001
        tf_pid = 0

    started = time.time()
    deadline = started + args.minutes * 60
    target_cycles = max(1, int(args.minutes * 60 / args.interval))
    cycle = 0
    counts = {"ok": 0, "failed": 0, "not_evaluated": 0, "api_error": 0, "timeout": 0}
    errors: list[str] = []
    sse_stats = {
        "cycles_with_events": 0,
        "cycles_without_events": 0,
        "resume_ok": 0,
        "resume_no_new": 0,
        "heartbeats": 0,
        "duplicate_ids": 0,
    }
    worker_deaths: list[str] = []

    with samples_path.open("w", encoding="utf-8") as sink:
        while time.time() < deadline:
            c0 = time.time()
            rec: dict[str, Any] = {
                "cycle": cycle,
                "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "since_start_s": round(c0 - started, 1),
            }

            # 1) 真实健康检查（API / TeamFlow / Worker）
            api_health = _get(f"{api}/api/v1/system/health", timeout=15)
            rec["api_health"] = {
                k: api_health.get(k) for k in ("status", "database") if isinstance(api_health, dict)
            }
            tf_state = _get(f"{args.teamflow}/api/state/projects", timeout=10)
            tf_ok = (
                isinstance(tf_state, dict) and tf_state.get("ok") is True and "count" in tf_state
            )
            rec["teamflow_ok"] = bool(tf_ok)
            rec["teamflow_count"] = tf_state.get("count") if tf_ok else None
            rec["teamflow_error"] = None if tf_ok else str(tf_state)[:200]
            if not tf_ok:
                errors.append(f"cycle {cycle}: TeamFlow 状态接口异常 {tf_state}")
            alive = {wid: _ping(p.pid) for wid, p in workers.items()}
            rec["workers_alive"] = alive
            for wid, ok in alive.items():
                if not ok:
                    worker_deaths.append(f"cycle {cycle}: {wid} 已退出")
            rec["api_proc"] = _proc_stats(api_proc.pid)
            rec["teamflow_proc"] = _proc_stats(tf_pid)
            rec["worker_procs"] = {wid: _proc_stats(p.pid) for wid, p in workers.items()}
            q0 = _get(f"{api}/api/v1/queue/stats", timeout=15)
            rec["queue_before"] = {
                "by_status": (q0 or {}).get("by_status") if isinstance(q0, dict) else None,
                "total": (q0 or {}).get("total") if isinstance(q0, dict) else None,
            }

            # 2) 通过 API 创建真实 Run（sync=false → 独立 Worker 消费）
            created = _post(
                f"{api}/api/v1/testcases/{args.case}/execute", {"sync": False}, timeout=120
            )
            run_id = created.get("run_id") if isinstance(created, dict) else None
            if not run_id:
                counts["api_error"] += 1
                errors.append(f"cycle {cycle}: 创建 Run 失败 {created}")
                rec["error"] = str(created)[:300]
                sink.write(json.dumps(rec, ensure_ascii=False) + "\n")
                sink.flush()
                cycle += 1
                remaining = args.interval - (time.time() - c0)
                if remaining > 0 and time.time() + remaining < deadline:
                    time.sleep(remaining)
                continue
            rec["run_id"] = run_id

            # 3) 真实 SSE 连接（边采集边轮询 Run）
            reader = SseReader(api, run_id)
            reader.start()

            final: dict[str, Any] = {}
            deadline_run = time.time() + 180
            claimed_seen: set[str] = set()
            while time.time() < deadline_run:
                doc = _get(f"{api}/api/v1/runs/{run_id}", timeout=15)
                if isinstance(doc, dict):
                    owner = str(doc.get("lease_owner") or "")
                    if owner:
                        claimed_seen.add(owner)
                    final = doc
                    if doc.get("execution_status") in TERMINAL:
                        break
                time.sleep(0.5)
            elapsed_run = time.time() - c0
            reader.stop_event.set()
            reader.join(timeout=10)

            rec.update(
                {
                    "run_elapsed_s": round(elapsed_run, 1),
                    "execution_status": final.get("execution_status"),
                    "test_verdict": final.get("test_verdict"),
                    "attempt": final.get("attempt"),
                    "fencing_token": final.get("fencing_token"),
                    "workers_claimed": sorted(claimed_seen),
                    "has_report": bool(final.get("report")),
                    "sse": {
                        "status": reader.status,
                        "events": len(reader.events),
                        "heartbeats": reader.heartbeats,
                        "retry_frames": reader.retry_frames,
                        "error": reader.error,
                        "event_types": sorted({e.get("event", "") for e in reader.events}),
                    },
                }
            )
            sse_stats["heartbeats"] += reader.heartbeats
            if reader.events:
                sse_stats["cycles_with_events"] += 1
            else:
                sse_stats["cycles_without_events"] += 1
            ids = [e.get("id") for e in reader.events]
            if len(ids) != len(set(ids)):
                sse_stats["duplicate_ids"] += 1

            # 4) SSE 续传验证（Last-Event-ID）
            if reader.last_event_id:
                resume = SseReader(api, run_id, last_event_id=reader.last_event_id)
                resume.start()
                time.sleep(2.5)
                resume.stop_event.set()
                resume.join(timeout=8)
                rec["sse_resume"] = {
                    "new_events": len(resume.events),
                    "heartbeats": resume.heartbeats,
                    "status": resume.status,
                    "error": resume.error,
                }
                if len(resume.events) == 0 and resume.status == 200:
                    sse_stats["resume_no_new"] += 1
                elif resume.status == 200:
                    sse_stats["resume_ok"] += 1

            # 5) 队列 / 数据增长
            q1 = _get(f"{api}/api/v1/queue/stats", timeout=15)
            rec["queue_after"] = {
                "by_status": (q1 or {}).get("by_status") if isinstance(q1, dict) else None,
                "total": (q1 or {}).get("total") if isinstance(q1, dict) else None,
            }
            rec["db"] = _db_snapshot(db_path)

            status = final.get("execution_status")
            verdict = final.get("test_verdict")
            if status and status not in TERMINAL:
                counts["timeout"] += 1
                errors.append(f"cycle {cycle}: run {run_id} 未在 180s 内到终态")
            elif status == "COMPLETED" and verdict == "PASSED":
                counts["ok"] += 1
            elif verdict == "NOT_EVALUATED":
                counts["not_evaluated"] += 1
                errors.append(f"cycle {cycle}: run {run_id} NOT_EVALUATED")
            elif status in {"FAILED", "TIMED_OUT", "CANCELLED"}:
                counts["failed"] += 1
                errors.append(f"cycle {cycle}: run {run_id} {status}/{verdict}")
            # 未由 Worker 领取（一直 QUEUED 又被判定终态）不可能出现；这里仅记录

            sink.write(json.dumps(rec, ensure_ascii=False) + "\n")
            sink.flush()
            cycle += 1

            remaining = args.interval - (time.time() - c0)
            if remaining > 0 and time.time() + remaining < deadline:
                time.sleep(remaining)

    duration_s = time.time() - started
    finished_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    # ------------------------------------------------------------- 收尾与核对
    for _wid, p in workers.items():
        if p.poll() is None:
            p.terminate()
    for _wid, p in workers.items():
        try:
            p.wait(timeout=15)
        except subprocess.TimeoutExpired:
            p.kill()
    api_alive = _ping(api_proc.pid)
    api_proc.terminate()
    try:
        api_proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        api_proc.kill()
    api_log.close()
    for log in wlogs:
        log.close()

    integrity: dict[str, Any] = {}
    try:
        integrity["run_verdicts"] = {
            str(k): v
            for k, v in _db_query(
                db_path, "SELECT test_verdict, COUNT(*) FROM tg_run GROUP BY test_verdict"
            )
        }
        integrity["run_status"] = {
            str(k): v
            for k, v in _db_query(
                db_path, "SELECT execution_status, COUNT(*) FROM tg_run GROUP BY execution_status"
            )
        }
        integrity["runs_without_report"] = _db_query(
            db_path,
            "SELECT COUNT(*) FROM tg_run WHERE execution_status='COMPLETED' "
            "AND (report_json IS NULL OR report_json='' OR report_json='null')",
        )[0][0]
        integrity["duplicate_event_keys"] = _db_query(
            db_path,
            "SELECT COUNT(*) FROM (SELECT producer_id, idempotency_key FROM tg_event "
            "GROUP BY producer_id, idempotency_key HAVING COUNT(*) > 1)",
        )[0][0]
        integrity["orphan_steps"] = _db_query(
            db_path, "SELECT COUNT(*) FROM tg_step WHERE run_id NOT IN (SELECT run_id FROM tg_run)"
        )[0][0]
        integrity["runs_without_evidence"] = _db_query(
            db_path,
            "SELECT COUNT(*) FROM tg_run r WHERE NOT EXISTS "
            "(SELECT 1 FROM tg_evidence e WHERE e.run_id = r.run_id)",
        )[0][0]
        integrity["unrecovered_leases"] = _db_query(
            db_path,
            "SELECT COUNT(*) FROM tg_run WHERE execution_status IN ('PREPARING','RUNNING')",
        )[0][0]
        integrity["quick_check"] = _db_query(db_path, "PRAGMA quick_check")[0][0]
        integrity["final_counts"] = _db_snapshot(db_path)
    except Exception as exc:  # noqa: BLE001
        integrity["error"] = str(exc)[:300]

    checks = {
        "duration_reached": duration_s >= args.minutes * 60 - 5,
        "cycles_reached": cycle >= target_cycles - 1,
        "api_inline_worker_off": True,
        "no_api_errors": counts["api_error"] == 0,
        "no_unexpected_failed": counts["failed"] == 0,
        "no_not_evaluated": counts["not_evaluated"] == 0,
        "all_runs_passed": counts["ok"] > 0 and counts["ok"] == cycle - counts["api_error"],
        "no_run_timeouts": counts["timeout"] == 0,
        "teamflow_healthy_all_cycles": not any("TeamFlow" in e for e in errors),
        "no_worker_death": not worker_deaths,
        "no_runs_without_report": integrity.get("runs_without_report") == 0,
        "no_duplicate_events": integrity.get("duplicate_event_keys") == 0,
        "no_orphan_steps": integrity.get("orphan_steps") == 0,
        "no_runs_without_evidence": integrity.get("runs_without_evidence") == 0,
        "no_unrecovered_leases": integrity.get("unrecovered_leases") == 0,
        "db_integrity_ok": str(integrity.get("quick_check", "")).lower() in {"ok", "true"},
        "sse_received_events": sse_stats["cycles_with_events"] > 0,
        "sse_resume_verified": (sse_stats["resume_no_new"] + sse_stats["resume_ok"]) > 0,
        "api_survived": api_alive,
    }
    report = {
        "round": "RUN-007-RESUME",
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started)),
        "finished_at": finished_at,
        "duration_minutes": round(duration_s / 60, 2),
        "architecture": {
            "api_port": args.port,
            "api_inline_worker": False,
            "db": _rel(db_path),
            "artifact_dir": _rel(artifacts),
            "workers": args.workers,
            "worker_pids": {wid: p.pid for wid, p in workers.items()},
            "teamflow": args.teamflow,
        },
        "interval_s": args.interval,
        "case": args.case,
        "cycles": cycle,
        "counts": counts,
        "sse": sse_stats,
        "worker_deaths": worker_deaths,
        "errors": errors[:50],
        "integrity": integrity,
        "checks": checks,
        "passed": all(checks.values()),
        "samples_file": _rel(samples_path),
    }
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    print(json.dumps({**checks, "passed": report["passed"]}, ensure_ascii=False, indent=1))
    print(f"\n周期 {cycle}，通过 {counts['ok']}，失败 {counts['failed']}；证据 {report_path}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
