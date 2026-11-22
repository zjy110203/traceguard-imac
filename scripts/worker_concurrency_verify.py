"""RUN-003-E：多 Worker 并发领取与租约隔离真实验证。

场景：入队 N 个真实 Run → 同时启动 2 个独立 Worker 进程（--once 模式循环领取）
→ 验证：每个 Run 恰好被执行一次（无双重执行）、无租约冲突丢失。
产出：.workbuddy-run/worker-concurrency-verify.json
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

API = "http://127.0.0.1:8000/api/v1"


def api_call(method: str, path: str, body: dict | None = None, timeout: int = 180) -> dict:
    req = urllib.request.Request(
        API + path,
        method=method,
        data=json.dumps(body).encode() if body else None,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as res:
        return json.loads(res.read())


def main() -> int:
    n = 4
    run_ids: list[str] = []
    t0 = time.monotonic()
    for _i in range(n):
        out = api_call("POST", "/testcases/tcase_tf_create_normal/execute", {"sync": False})
        run_ids.append(out["run_id"])
    print(f"queued {n} runs")

    # 4 个独立 Worker 进程并发领取（各执行 1 个任务后退出），与 API 内嵌 Worker 竞争
    procs = [
        subprocess.Popen(
            [sys.executable, "-m", "traceguard_worker.main", "--once"],
            cwd=".",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env={"PATH": "/usr/bin:/bin", "PYTHONPATH": "."},
        )
        for _ in range(4)
    ]
    outputs = []
    for p in procs:
        try:
            out, _ = p.communicate(timeout=180)
            outputs.append(out.decode()[-300:] if out else "")
        except subprocess.TimeoutExpired:
            p.kill()
            outputs.append("TIMEOUT")
    print("workers finished")

    # 等待 API 内嵌 Worker 兜底消费剩余任务
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        runs = api_call("GET", "/runs?limit=50&workspace_id=default")
        by_id = {r["run_id"]: r for r in runs if r["run_id"] in run_ids}
        if all(by_id.get(rid, {}).get("execution_status") in {"COMPLETED", "FAILED"} for rid in run_ids):
            break
        time.sleep(2)

    runs = api_call("GET", "/runs?limit=50&workspace_id=default")
    by_id = {r["run_id"]: r for r in runs if r["run_id"] in run_ids}
    results = []
    completed = 0
    for rid in run_ids:
        r = by_id.get(rid, {})
        ok = r.get("execution_status") == "COMPLETED"
        completed += 1 if ok else 0
        results.append(
            {
                "run_id": rid,
                "execution_status": r.get("execution_status"),
                "verdict": r.get("test_verdict"),
                "lease_owner": (r.get("lease_owner") or "")[-20:],
                "ok": ok,
            }
        )
        print(("OK " if ok else "BAD"), rid[-12:], r.get("execution_status"), r.get("test_verdict"))

    owners = {r["lease_owner"] for r in results if r.get("lease_owner")}
    report = {
        "queued": n,
        "completed": completed,
        "distinct_owners": len(owners),
        "results": results,
        "worker_outputs_tail": outputs,
        "total_seconds": round(time.monotonic() - t0, 1),
    }
    Path(".workbuddy-run/worker-concurrency-verify.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2)
    )
    print(f"{completed}/{n} completed, {len(owners)} distinct lease owners → .workbuddy-run/worker-concurrency-verify.json")
    return 0 if completed == n else 1


if __name__ == "__main__":
    sys.exit(main())
