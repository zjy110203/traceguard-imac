"""TraceGuard 端到端演示（真实执行，全部通过 HTTP API）。

前置：
    1) TeamFlow  : http://127.0.0.1:8001   (scripts/run_teamflow.sh)
    2) TraceGuard: http://127.0.0.1:8000   (scripts/run_api.sh)
运行：
    .venv/bin/python scripts/final_demo.py
产物：
    tests/reports/demo_evidence.json（每步真实返回摘要）
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

API = "http://127.0.0.1:8000/api/v1"
OUT = Path(__file__).resolve().parent.parent / "tests" / "reports" / "demo_evidence.json"
EVIDENCE: list[dict[str, Any]] = []


def call(method: str, path: str, body: dict | None = None, timeout: int = 180) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        API + path, data=data, method=method, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            raw = resp.read().decode()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        raise RuntimeError(f"{method} {path} -> {exc.code}: {detail[:300]}") from exc


def step(name: str, fn: Callable[[], Any]) -> Any:
    t = time.perf_counter()
    try:
        result = fn()
        ms = int((time.perf_counter() - t) * 1000)
        EVIDENCE.append({"step": name, "ok": True, "ms": ms, "result": result})
        print(f"  ✓ {name} ({ms} ms)")
        return result
    except Exception as exc:  # noqa: BLE001
        ms = int((time.perf_counter() - t) * 1000)
        EVIDENCE.append({"step": name, "ok": False, "ms": ms, "error": str(exc)})
        print(f"  ✗ {name}: {exc}")
        return None


def main() -> int:
    print("TraceGuard 端到端演示\n" + "=" * 56)

    print("\n[1] 环境自检")
    step("health", lambda: call("GET", "/system/health"))
    browser = step("browser", lambda: call("GET", "/system/browser?probe=true", timeout=60))
    if browser and not browser.get("available"):
        print("浏览器不可用，终止演示：", browser.get("error"))
        return 2

    print("\n[2] 演示资产")
    step("seed", lambda: call("POST", "/system/seed"))
    cases = call("GET", "/testcases?workspace_id=default")
    approved = [c for c in cases if c["review_status"] == "APPROVED"]
    print(f"  已审核用例：{len(approved)}")

    print("\n[3] 真实执行 4 个场景")
    runs: dict[str, dict] = {}
    for case_id in [
        "tcase_tf_create_normal",
        "tcase_tf_false_pass",
        "tcase_tf_ui_missing",
        "tcase_tf_api_error",
    ]:
        out = step(
            f"execute {case_id}",
            lambda cid=case_id: call(
                "POST", f"/testcases/{cid}/execute", {"sync": True}, timeout=240
            ),
        )
        if out:
            runs[case_id] = out
            print(f"      verdict = {out.get('execution', {}).get('test_verdict')}")

    print("\n[4] 诊断 / 证据图 / 探针")
    failed = next(
        (
            r["execution"]["run_id"]
            for r in runs.values()
            if r.get("execution", {}).get("test_verdict") == "FAILED"
        ),
        None,
    )
    if failed:
        diag = step("diagnose", lambda: call("POST", f"/runs/{failed}/diagnose"))
        if diag:
            print(f"      outcome={diag['outcome']} disposition={diag['disposition']}")
            for h in diag["hypotheses"]:
                print(
                    f"      hypothesis {h['status']} {h['failure_mechanism']}: {h['description']}"
                )
        step("graph", lambda: call("POST", f"/runs/{failed}/graph"))
        suggestions = call("GET", f"/runs/{failed}/probe-suggestions")
        if suggestions:
            top = suggestions[0]
            step(
                f"probe {top['probe_id']}",
                lambda: call(
                    "POST",
                    "/probes/execute",
                    {"probe_id": top["probe_id"], "params": {**top["params"]}},
                ),
            )

    print("\n[5] 自愈闭环（定位器修复）")
    latest = next(
        c["latest_version"]
        for c in call("GET", "/testcases?workspace_id=default")
        if c["testcase_id"] == "tcase_tf_create_normal"
    )
    repair = step(
        "propose",
        lambda: call(
            "POST",
            "/repairs",
            {
                "testcase_id": "tcase_tf_create_normal",
                "base_version": latest,
                "changes": [
                    {
                        "path": "steps[].target",
                        "step_index": 4,
                        "value": {"strategy": "role", "value": "button:创建"},
                        "reason": "演示：test_id → role+accessible name",
                    }
                ],
                "reason": "演示自愈",
            },
        ),
    )
    if repair:
        pid = repair["proposal_id"]
        step("validate", lambda: call("POST", f"/repairs/{pid}/validate"))
        step("sandbox-run", lambda: call("POST", f"/repairs/{pid}/sandbox-run", timeout=240))
        step("submit", lambda: call("POST", f"/repairs/{pid}/submit"))
        step(
            "approve",
            lambda: call(
                "POST",
                f"/repairs/{pid}/approve",
                {"reviewer": "role:demo-qa", "note": "演示"},
            ),
        )
        promoted = step(
            "promote",
            lambda: call("POST", f"/repairs/{pid}/promote", {"idempotency_key": f"demo-{pid}"}),
        )
        if promoted:
            print(f"      promoted_version={promoted.get('promoted_version')}")
            v = promoted.get("promoted_version")
            step(
                "regression",
                lambda: call(
                    "POST",
                    f"/testcases/tcase_tf_create_normal/execute?version={v}",
                    {"sync": True},
                    timeout=240,
                ),
            )
        step(
            "rollback",
            lambda: call(
                "POST",
                f"/repairs/{pid}/rollback",
                {"operator": "role:demo-qa", "reason": "演示结束"},
            ),
        )

    print("\n[6] Benchmark（实验 B）")
    exp = step(
        "benchmark B", lambda: call("POST", "/benchmark/experiments", {"kind": "B"}, timeout=300)
    )
    if exp:
        for m in exp["metrics"]:
            print(
                f"      {m['baseline']:18s} {m['metric']:26s} "
                f"{m['value'] * 100:5.1f}% ({m['numerator']}/{m['denominator']})"
            )

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(EVIDENCE, ensure_ascii=False, indent=2))
    failed_steps = [e for e in EVIDENCE if not e["ok"]]
    print("\n" + "=" * 56)
    print(f"步骤 {len(EVIDENCE)}，失败 {len(failed_steps)}；证据写入 {OUT}")
    return 0 if not failed_steps else 1


if __name__ == "__main__":
    sys.exit(main())
