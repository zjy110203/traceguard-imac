"""RUN-009 WP5：真实资源监控 + 多 Worker 性能基线（不使用 ps / /proc / psutil）。

安全约束：
- 不复制父进程完整环境（B-17），子进程 env 仅使用显式白名单变量；
- 不读取 .env / 不打印任何凭据；
- 采样使用 scripts/resource_monitor_run009.py（libproc.dylib）。

用法示例：
    .venv/bin/python scripts/perf_run009.py --workers 1 2 4 --runs-per-config 6
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from resource_monitor_run009 import DbProbe, LatencyTracker, ResourceSampler, write_jsonl  # noqa: E402

API_PORT = 8030
TF_PORT = 8012
TF_URL = f"http://127.0.0.1:{TF_PORT}"
API_URL = f"http://127.0.0.1:{API_PORT}"
FINAL_STATES = {"COMPLETED", "FAILED", "TIMED_OUT", "CANCELLED"}


def call(
    method: str, url: str, body: dict | None = None, timeout: float = 30.0
) -> tuple[dict, float, int | None]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method, headers={"Content-Type": "application/json"}
    )
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode()
            payload = json.loads(raw) if raw else {}
            status = resp.status
    except urllib.error.HTTPError as exc:
        payload, status = {}, exc.code
    ms = (time.perf_counter() - t0) * 1000
    return payload, ms, status


def _sub_env(**overrides: str) -> dict[str, str]:
    """显式白名单子进程环境，绝不复制父进程完整环境（B-17 禁令）。"""
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin:/usr/sbin:/sbin"),
        "PYTHONUNBUFFERED": "1",
        "TG_DATABASE_URL": "",
        "TG_ARTIFACT_DIR": "",
        "TG_TEAMFLOW_BASE_URL": TF_URL,
        "TG_INLINE_WORKER": "0",
        "TG_WORKER_POLL_INTERVAL_S": "0.2",
        "TG_WORKER_LEASE_TTL_S": "60",
        "TG_WORKER_ID": "",
        "TG_LLM_PROVIDER": "",
        "TG_LOG_JSON": "false",
        "TG_ENVIRONMENT": "local",
    }
    env.update(overrides)
    return env


def _wait_health(url: str, timeout_s: float = 90.0) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            payload, _, status = call("GET", f"{url}/health", timeout=3.0)
            if status == 200 and payload.get("status") == "ok":
                return True
        except Exception:
            pass
        time.sleep(0.3)
    return False


def _popen(args: list[str], env: dict[str, str], log_path: Path) -> subprocess.Popen:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log = log_path.open("ab")
    log.write(f"\n===== start {time.strftime('%H:%M:%S')} :: {' '.join(args)} =====\n".encode())
    log.flush()
    return subprocess.Popen(args, cwd=str(ROOT), env=env, stdout=log, stderr=subprocess.STDOUT)


def _stop(proc: subprocess.Popen | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def _approved_testcases(api: str) -> list[dict[str, Any]]:
    payload, _, status = call("GET", f"{api}/api/v1/testcases?workspace_id=default&limit=200")
    if status != 200:
        return []
    rows = payload if isinstance(payload, list) else payload.get("items", [])
    return [r for r in rows if r.get("review_status") == "APPROVED"]


def _load_and_measure(
    api: str,
    worker_pids: list[int],
    api_pid: int,
    db_path: Path,
    artifact_dir: Path,
    runs_per_config: int,
    timeout_s: float,
) -> dict[str, Any]:
    sampler = ResourceSampler()
    tracker = LatencyTracker()
    probe = DbProbe(db_path, artifact_dir)
    stop = threading.Event()
    frames: list[dict[str, Any]] = []

    def _sampler_loop() -> None:
        groups = {"api": [api_pid], "workers": worker_pids}
        while not stop.is_set():
            try:
                frame = sampler.sample(groups)
                frame["db"] = probe.snapshot()
                frames.append(frame)
            except Exception as exc:  # noqa: BLE001
                frames.append({"error": type(exc).__name__ + ": " + str(exc)[:160]})
            stop.wait(2.0)

    sampler_thread = threading.Thread(target=_sampler_loop, daemon=True)
    seed, ms, status = call("POST", f"{api}/api/v1/system/seed", {})
    tracker.record("POST", "/api/v1/system/seed", ms, status)
    if status != 200:
        raise RuntimeError(f"seed failed: {status} {seed}")
    cases = _approved_testcases(api)
    if not cases:
        raise RuntimeError("no APPROVED testcase available")
    case = next((c for c in cases if c.get("testcase_id") == "tcase_tf_create_normal"), cases[0])
    case_id = case["testcase_id"]

    t_start = time.perf_counter()
    sampler_thread.start()

    # 先把全部 Run 一次性入队，再统一轮询：只有这样才能测出 Worker 并行度带来的吞吐差异，
    # 逐个串行提交会把并发能力掩盖成"每分钟提交节奏"。
    pending: list[dict[str, Any]] = []
    run_results: list[dict[str, Any]] = []
    for i in range(runs_per_config):
        payload, ms, status = call(
            "POST", f"{api}/api/v1/testcases/{case_id}/execute", {"sync": False}
        )
        tracker.record("POST", f"/api/v1/testcases/{case_id}/execute", ms, status)
        if status != 201 or "run_id" not in payload:
            run_results.append({"index": i, "ok": False, "error": f"create failed {status}"})
            continue
        pending.append(
            {
                "index": i,
                "run_id": payload["run_id"],
                "submitted": time.perf_counter(),
                "queue_wait": None,
                "final_status": "",
                "test_verdict": "",
            }
        )

    deadline = time.time() + timeout_s
    remaining = list(pending)
    while remaining and time.time() < deadline:
        for item in list(remaining):
            run, ms, status = call("GET", f"{api}/api/v1/runs/{item['run_id']}")
            tracker.record("GET", f"/api/v1/runs/{item['run_id']}", ms, status)
            if status != 200:
                continue
            st = run.get("execution_status", "")
            if item["queue_wait"] is None and st not in {"QUEUED", ""}:
                item["queue_wait"] = time.perf_counter() - item["submitted"]
            if st in FINAL_STATES:
                item["final_status"] = st
                item["test_verdict"] = run.get("test_verdict", "")
                item["elapsed_s"] = round(time.perf_counter() - item["submitted"], 3)
                remaining.remove(item)
        if remaining:
            time.sleep(0.15)

    for item in pending:
        run_results.append(
            {
                "index": item["index"],
                "run_id": item["run_id"],
                "ok": item["final_status"] != "",
                "final_status": item["final_status"],
                "test_verdict": item["test_verdict"],
                "elapsed_s": item.get("elapsed_s"),
                "queue_wait_s": (
                    round(item["queue_wait"], 3) if item["queue_wait"] is not None else None
                ),
            }
        )
    wall_s = time.perf_counter() - t_start
    stop.set()
    sampler_thread.join(timeout=1.0)
    time.sleep(0.2)

    completed = [r for r in run_results if r.get("final_status") == "COMPLETED"]
    failed_runs = [r for r in run_results if r.get("final_status") in {"FAILED", "TIMED_OUT"}]
    queue_waits = [r["queue_wait_s"] for r in run_results if r.get("queue_wait_s") is not None]

    def _group_avg(key: str) -> dict[str, float]:
        vals: dict[str, list[float]] = {}
        for frame in frames:
            for group, stats in (frame.get("groups") or {}).items():
                if stats.get("alive"):
                    vals.setdefault(group, []).append(float(stats.get(key) or 0.0))
        if not vals:
            return {}
        return {
            group: {
                "avg": round(sum(v) / len(v), 2),
                "max": round(max(v), 2),
                "n": len(v),
            }
            for group, v in vals.items()
        }

    return {
        "config": {
            "workers": len(worker_pids),
            "runs_submitted": runs_per_config,
            "case_id": case_id,
            "api": api,
        },
        "wall_s": round(wall_s, 2),
        "completed": len(completed),
        "failed_or_timeout": len(failed_runs),
        "incomplete": len([r for r in run_results if not r.get("final_status")]),
        "completion_rate": round(len(completed) / max(1, runs_per_config), 3),
        "throughput_runs_per_min": round(len(completed) / max(0.001, wall_s) * 60, 2),
        "queue_wait_s": {
            "avg": round(sum(queue_waits) / len(queue_waits), 3) if queue_waits else None,
            "max": round(max(queue_waits), 3) if queue_waits else None,
            "n": len(queue_waits),
        },
        "api_latency_ms": tracker.summary(),
        "resource": {
            "avg": _group_avg("cpu_pct"),
            "rss": _group_avg("rss_mb"),
            "chromium_avg": {
                "cpu_pct": round(
                    sum(float(f["chromium"]["cpu_pct"]) for f in frames if "chromium" in f)
                    / max(1, len(frames)),
                    2,
                ),
                "rss_mb": round(
                    sum(float(f["chromium"]["rss_mb"]) for f in frames if "chromium" in f)
                    / max(1, len(frames)),
                    2,
                ),
                "browser_instance_count_max": max(
                    (
                        int(f["chromium"]["browser_instance_count"])
                        for f in frames
                        if "chromium" in f
                    ),
                    default=0,
                ),
            },
        },
        "db_final": probe.snapshot(),
        "runs": run_results,
        "samples": frames,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, nargs="+", default=[1, 2, 4])
    parser.add_argument("--runs-per-config", type=int, default=6)
    parser.add_argument("--timeout-s", type=float, default=240.0)
    parser.add_argument("--out-dir", default=str(ROOT / "docs/assets/run009/wp5"))
    args = parser.parse_args(argv)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    python_bin = str(ROOT / ".venv/bin/python")

    teamflow_proc: subprocess.Popen | None = None
    if not _wait_health(TF_URL, timeout_s=3):
        teamflow_proc = _popen(
            [
                python_bin,
                "-m",
                "uvicorn",
                "traceguard_teamflow.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(TF_PORT),
            ],
            _sub_env(),
            ROOT / "logs/run009/wp5-teamflow.log",
        )
        if not _wait_health(TF_URL):
            raise SystemExit("TeamFlow failed to start")

    results: list[dict[str, Any]] = []
    try:
        for worker_count in args.workers:
            stamp = time.strftime("%H%M%S")
            run_dir = ROOT / f".traceguard/run009/wp5/perf-{worker_count}w-{stamp}"
            run_dir.mkdir(parents=True, exist_ok=True)
            db_path = run_dir / "traceguard.db"
            artifact_dir = run_dir / "artifacts"
            api_env = _sub_env(
                TG_DATABASE_URL=f"sqlite:///{db_path}",
                TG_ARTIFACT_DIR=str(artifact_dir),
                TG_INLINE_WORKER="0",
            )
            api_proc = _popen(
                [
                    python_bin,
                    "-m",
                    "uvicorn",
                    "traceguard_api.main:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(API_PORT),
                ],
                api_env,
                ROOT / f"logs/run009/wp5-api-{worker_count}w.log",
            )
            worker_procs: list[subprocess.Popen] = []
            try:
                if not _wait_health(API_URL):
                    raise SystemExit("API failed to start")
                for i in range(worker_count):
                    worker_env = _sub_env(
                        TG_DATABASE_URL=f"sqlite:///{db_path}",
                        TG_ARTIFACT_DIR=str(artifact_dir),
                        TG_INLINE_WORKER="0",
                        TG_WORKER_ID=f"wp5-{worker_count}w-{i + 1}",
                        TG_WORKER_POLL_INTERVAL_S="0.2",
                        TG_WORKER_LEASE_TTL_S="60",
                    )
                    worker_procs.append(
                        _popen(
                            [python_bin, "-m", "traceguard_worker.main", "--poll", "0.2"],
                            worker_env,
                            ROOT / f"logs/run009/wp5-worker-{worker_count}w-{i + 1}.log",
                        )
                    )
                time.sleep(1.0)
                result = _load_and_measure(
                    API_URL,
                    [p.pid for p in worker_procs],
                    api_proc.pid,
                    db_path,
                    artifact_dir,
                    args.runs_per_config,
                    args.timeout_s,
                )
                results.append(result)
                write_jsonl(out_dir / f"perf-{worker_count}w-samples.jsonl", result.pop("samples"))
                (out_dir / f"perf-{worker_count}w.json").write_text(
                    json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                print(
                    f"[{worker_count}W] completed={result['completed']}/{args.runs_per_config} "
                    f"wall={result['wall_s']}s throughput={result['throughput_runs_per_min']} runs/min "
                    f"p95={result['api_latency_ms'].get('p95_ms')}ms"
                )
            finally:
                for proc in worker_procs:
                    _stop(proc)
                _stop(api_proc)
                time.sleep(1.0)
    finally:
        if teamflow_proc is not None:
            _stop(teamflow_proc)

    summary = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "source": "libproc.dylib + SQLite 只读探针 + API 实测",
        "configs": results,
    }
    (out_dir / "perf-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"summary -> {out_dir / 'perf-summary.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
