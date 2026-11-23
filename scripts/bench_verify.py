"""RUN-003-D：数据集 v2 全量场景真实执行验证（逐场景真实浏览器执行）。

每个场景：重置数据 → 激活故障（用例内置步骤会激活）→ 执行 → 记录 verdict 与耗时。
验证目标：场景可重复、故障注入真实生效、verdict 与 GT 预期一致。
产出：.workbuddy-run/bench-v2-verify.json（真实执行证据，不含 GT 内容——GT 由评测侧隔离）。
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from pathlib import Path

API = os.environ.get("TG_API_URL", "http://127.0.0.1:8000").rstrip("/") + "/api/v1"

EXPECTED = {
    # scenario_id → (testcase_id, expected_verdict)
    "TF-NORMAL-001": ("tcase_tf_create_normal", "PASSED"),
    "TF-NORMAL-002": ("tcase_bench_normal2", "PASSED"),
    "TF-DB-001": ("tcase_tf_false_pass", "FAILED"),
    "TF-DB-002": ("tcase_bench_db002", "FAILED"),
    "TF-DB-003": ("tcase_bench_db003", "PASSED"),
    "TF-DB-004": ("tcase_bench_db004", "FAILED"),
    "TF-DB-005": ("tcase_bench_db005", "PASSED"),
    "TF-UI-001": ("tcase_tf_ui_missing", "FAILED"),
    "TF-UI-002": ("tcase_bench_ui002", "FAILED"),
    "TF-UI-003": ("tcase_bench_ui003", "FAILED"),
    "TF-UI-004": ("tcase_bench_ui004", "PASSED"),
    "TF-API-001": ("tcase_tf_api_error", "FAILED"),
    "TF-API-002": ("tcase_bench_api002", "FAILED"),
    "TF-API-003": ("tcase_bench_api003", "PASSED"),
    "TF-API-004": ("tcase_bench_api004", "PASSED"),
    "TF-NET-001": ("tcase_bench_net001", "PASSED"),
    "TF-NET-001-FAST": ("tcase_bench_net001fast", "PASSED"),
    "TF-NET-002": ("tcase_bench_net002", "FAILED"),
    "TF-LOC-001": ("tcase_bench_loc001", "EXEC_FAILED"),
    "TF-LOC-002": ("tcase_bench_loc002", "EXEC_FAILED"),
    "TF-TIME-001": ("tcase_bench_time001", "PASSED"),
    "TF-TIME-002": ("tcase_bench_time002", "PASSED"),
    "TF-TIME-003": ("tcase_bench_time003", "EXEC_FAILED"),
    "TF-SEC-001": ("tcase_bench_sec001", "FAILED"),
    "TF-SEC-002": ("tcase_bench_sec002", "FAILED"),
    "TF-COMP-001": ("tcase_bench_comp001", "FAILED"),
    "TF-COMP-002": ("tcase_bench_comp002", "FAILED"),
    "TF-COMP-003": ("tcase_bench_comp003", "EXEC_FAILED"),
    # Flaky 场景：verdict 概率性，只要求"真实执行完成"且落在这两种之一
    "TF-FLAKY-001": ("tcase_bench_flaky001", "FLAKY_ANY"),
    "TF-FLAKY-002": ("tcase_bench_flaky002", "FLAKY_ANY"),
}


def api_call(method: str, path: str, body: dict | None = None, timeout: int = 120) -> dict:
    req = urllib.request.Request(
        API + path,
        method=method,
        data=json.dumps(body).encode() if body else None,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as res:
        return json.loads(res.read())


def main() -> int:
    results = []
    # 全量验证前重置数据与故障（TeamFlow 侧）
    try:
        urllib.request.urlopen(
            urllib.request.Request("http://127.0.0.1:8001/api/test/reset", method="POST"),
            timeout=10,
        )
    except Exception as exc:  # noqa: BLE001
        print("reset warn:", exc)

    for sid, (tcid, expect) in EXPECTED.items():
        t0 = time.monotonic()
        rec: dict = {"scenario_id": sid, "testcase_id": tcid, "expected_verdict": expect}
        try:
            out = api_call("POST", f"/testcases/{tcid}/execute", {"sync": True}, timeout=180)
            verdict = out.get("execution", {}).get("test_verdict") or out.get("test_verdict")
            run_id = out.get("run_id") or out.get("execution", {}).get("run_id")
            rec.update(
                run_id=run_id,
                verdict=verdict,
                duration_s=round(time.monotonic() - t0, 1),
            )
            exec_status = out.get("execution", {}).get("execution_status") or out.get("execution_status")
            if expect == "EXEC_FAILED":
                # 定位器失效场景：确定性执行失败 → exec FAILED + verdict NOT_EVALUATED（架构语义）
                ok = exec_status == "FAILED"
            elif expect == "FLAKY_ANY":
                ok = verdict in {"PASSED", "FAILED"}
            else:
                ok = verdict == expect
            rec["match"] = ok
        except Exception as exc:  # noqa: BLE001
            rec.update(error=f"{type(exc).__name__}: {exc}", match=False, duration_s=round(time.monotonic() - t0, 1))
        results.append(rec)
        mark = "OK " if rec.get("match") else "BAD"
        print(f"{mark} {sid:<16} verdict={rec.get('verdict')} expect={expect} ({rec.get('duration_s')}s)")
        # 每个场景后清理故障，保证可重复
        try:
            urllib.request.urlopen(
                urllib.request.Request("http://127.0.0.1:8001/api/faults/all/clear", method="POST"),
                timeout=5,
            )
        except Exception:  # noqa: BLE001
            pass

    matched = sum(1 for r in results if r.get("match"))
    report = {"total": len(results), "matched": matched, "results": results}
    Path(".workbuddy-run/bench-v2-verify.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2)
    )
    print(f"\n{matched}/{len(results)} 场景 verdict 与 GT 预期一致 → .workbuddy-run/bench-v2-verify.json")
    return 0 if matched == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
