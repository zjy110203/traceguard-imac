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


def _is_transport_error(data: Any) -> bool:
    """`_get`/`_post` 吞掉异常后返回的错误包，不得被当作健康响应当成成功。"""
    return isinstance(data, dict) and "__error__" in data


def _teamflow_health(state: Any) -> dict[str, Any]:
    """TeamFlow 健康判定：真实 2xx 响应 + `ok is True` + 必要字段 `count`。"""
    if isinstance(state, dict) and not _is_transport_error(state):
        if state.get("ok") is True and "count" in state:
            return {"ok": True, "count": state["count"], "error": None}
    return {"ok": False, "count": None, "error": str(state)[:200]}


def _api_health(data: Any) -> dict[str, Any]:
    """API 健康判定：区分传输错误、状态异常和内嵌 Worker 未关闭。"""
    if not isinstance(data, dict) or _is_transport_error(data):
        return {
            "ok": False,
            "status": None,
            "database": None,
            "inline_running": None,
            "error": str(data)[:200],
        }
    worker = data.get("worker") or {}
    inline = bool(worker.get("inline_running")) if isinstance(worker, dict) else None
    status = data.get("status")
    ok = status == "ok" and inline is False
    return {
        "ok": ok,
        "status": status,
        "database": data.get("database"),
        "inline_running": inline,
        "error": None if ok else f"status={status!r}, inline_running={inline!r}",
    }


def _has_db_errors(snap: dict[str, Any]) -> bool:
    """只读连接失败时绝不能把 `ERR:*` 当作有效计数。"""
    return bool(snap.get("error")) or any(
        isinstance(v, str) and v.startswith("ERR:") for v in snap.values()
    )


def read_samples_tail(path: Path) -> tuple[int, float, int]:
    """从已有 JSONL 采样文件读取续跑起点；返回 (next_cycle, elapsed_s, line_count)。

    文件不存在返回 (0, 0.0, 0)。任一行损坏则拒绝续跑，避免把证据写坏。
    """
    if not path.exists():
        return 0, 0.0, 0
    cycle = 0
    elapsed = 0.0
    count = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        cycle = max(cycle, int(rec["cycle"]) + 1)
        elapsed = max(elapsed, float(rec["since_start_s"]))
        count += 1
    return cycle, elapsed, count


def build_checks(
    *,
    duration_s: float,
    minutes: float,
    cycle: int,
    target_cycles: int,
    counts: dict[str, int],
    errors: list[str],
    worker_deaths: list[str],
    integrity: dict[str, Any],
    sse_stats: dict[str, int],
    api_alive: bool,
    api_health_failures: int,
    inline_worker_seen: int,
    metric_sources: set[str],
    db_path: Path,
) -> dict[str, Any]:
    """把验收条件集中到纯函数，避免恒真条件、便于自动回归测试。"""
    return {
        "duration_reached": duration_s >= minutes * 60 - 5,
        "cycles_reached": cycle >= target_cycles - 1,
        "api_inline_worker_off": inline_worker_seen == 0,
        "api_health_ok_all_cycles": api_health_failures == 0,
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
        "db_counts_from_current_instance": integrity.get("db_path") == str(db_path),
        "db_counts_readable": not _has_db_errors(integrity.get("final_counts", {})),
        "process_metrics_source_recorded": bool(metric_sources) and "unknown" not in metric_sources,
        "sse_received_events": sse_stats["cycles_with_events"] > 0,
        "sse_resume_verified": (sse_stats["resume_no_new"] + sse_stats["resume_ok"]) > 0,
        "api_survived": api_alive,
    }


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
    snap: dict[str, Any] = {"db_path": str(db)}
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
    ap.add_argument("--round", default="RUN-008")
    ap.add_argument(
        "--resume-samples",
        action="store_true",
        help="已有采样文件时从最后一行续写（不覆盖原始证据）",
    )
    args = ap.parse_args(argv)

    api = f"http://127.0.0.1:{args.port}"
    base = ROOT / args.root
    db_path = base / "api.db"
    artifacts = base / "artifacts"
    if base.exists() and not args.resume_samples:
        shutil.rmtree(base)  # 独立测试库：每次长稳都是干净环境（不碰正式库）
    artifacts.mkdir(parents=True, exist_ok=True)

    samples_path = ROOT / args.samples
    samples_path.parent.mkdir(parents=True, exist_ok=True)
    report_path = ROOT / args.out
    report_path.parent.mkdir(parents=True, exist_ok=True)
    if report_path.exists() and not args.resume_samples:
        print(f"汇总证据已存在，拒绝覆盖：{report_path}（续跑请加 --resume-samples 或更换 --out）")
        return 2
    if samples_path.exists() and not args.resume_samples:
        print(f"采样文件已存在，拒绝覆盖：{samples_path}（续跑请加 --resume-samples）")
        return 2

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

    health0 = _api_health(_get(f"{api}/api/v1/system/health"))
    if health0["inline_running"] is True:
        print("API 启用了内嵌 Worker：长稳必须由独立 Worker 执行，终止")
        api_proc.kill()
        return 2
    if not health0["ok"]:
        print(f"API 健康响应不完整，终止：{health0}")
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

    prior_cycle, prior_elapsed, prior_lines = (
        read_samples_tail(samples_path) if args.resume_samples else (0, 0.0, 0)
    )
    started = time.time() - prior_elapsed
    deadline = started + args.minutes * 60
    target_cycles = max(1, int(args.minutes * 60 / args.interval))
    cycle = prior_cycle
    print(f"采样续跑：已有 {prior_lines} 行，从 cycle {cycle} 继续（已运行 {prior_elapsed:.1f}s）")
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
    api_health_failures = 0
    inline_worker_seen = 0
    metric_sources: set[str] = set()

    with samples_path.open("a" if args.resume_samples else "w", encoding="utf-8") as sink:
        while time.time() < deadline:
            c0 = time.time()
            rec: dict[str, Any] = {
                "cycle": cycle,
                "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "since_start_s": round(c0 - started, 1),
            }

            # 1) 真实健康检查（API / TeamFlow / Worker）
            api_raw = _get(f"{api}/api/v1/system/health", timeout=15)
            api_health = _api_health(api_raw)
            rec["api_health"] = {
                k: api_health[k] for k in ("ok", "status", "database", "inline_running", "error")
            }
            if not api_health["ok"]:
                api_health_failures += 1
                errors.append(f"cycle {cycle}: API 健康检查失败 {api_health}")
            if api_health["inline_running"]:
                inline_worker_seen += 1
                errors.append(f"cycle {cycle}: API 内嵌 Worker 被启用")
            tf_state = _get(f"{args.teamflow}/api/state/projects", timeout=10)
            tf_health = _teamflow_health(tf_state)
            rec["teamflow_ok"] = tf_health["ok"]
            rec["teamflow_count"] = tf_health["count"]
            rec["teamflow_error"] = tf_health["error"]
            if not tf_health["ok"]:
                errors.append(f"cycle {cycle}: TeamFlow 状态接口异常 {tf_state}")
            alive = {wid: _ping(p.pid) for wid, p in workers.items()}
            rec["workers_alive"] = alive
            for wid, ok in alive.items():
                if not ok:
                    worker_deaths.append(f"cycle {cycle}: {wid} 已退出")
            rec["api_proc"] = _proc_stats(api_proc.pid)
            rec["teamflow_proc"] = _proc_stats(tf_pid)
            rec["worker_procs"] = {wid: _proc_stats(p.pid) for wid, p in workers.items()}
            for stat in (rec["api_proc"], rec["teamflow_proc"], *rec["worker_procs"].values()):
                metric_sources.add(str(stat.get("source", "unknown")))
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
        integrity["db_path"] = str(db_path)
        integrity["final_counts"] = _db_snapshot(db_path)
    except Exception as exc:  # noqa: BLE001
        integrity["error"] = str(exc)[:300]

    checks = build_checks(
        duration_s=duration_s,
        minutes=args.minutes,
        cycle=cycle,
        target_cycles=target_cycles,
        counts=counts,
        errors=errors,
        worker_deaths=worker_deaths,
        integrity=integrity,
        sse_stats=sse_stats,
        api_alive=api_alive,
        api_health_failures=api_health_failures,
        inline_worker_seen=inline_worker_seen,
        metric_sources=metric_sources,
        db_path=db_path,
    )
    report = {
        "round": args.round,
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
        "resumed_from": {
            "samples": prior_lines,
            "cycle_offset": prior_cycle,
            "elapsed_s": round(prior_elapsed, 1),
        },
        "cycles": cycle,
        "api_health_failures": api_health_failures,
        "inline_worker_seen": inline_worker_seen,
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
