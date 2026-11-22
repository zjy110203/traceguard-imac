"""P0 验收脚本：真实浏览器执行 TraceGuard 用例（不 Mock、不降级）。

用法：
    .venv/bin/python scripts/smoke_playwright.py            # 全部场景
    .venv/bin/python scripts/smoke_playwright.py normal      # 指定场景
"""

from __future__ import annotations

import json
import sys

from traceguard_api.runtime import Runtime
from traceguard_api.seed import seed_teamflow_catalog


def run_case(rt: Runtime, testcase_id: str, *, mode: str = "playwright") -> dict:
    task = rt.tasks.create_task(
        testcase_id,
        f"smoke-{testcase_id}-{mode}-{__import__('uuid').uuid4().hex[:6]}",
        mode=mode,
        testcase_version_id=f"{testcase_id}@1",
    )
    run = rt.tasks.create_run(task["task_id"], f"{testcase_id}@1")
    return rt.execute_run(run["run_id"])


def main() -> int:
    rt = Runtime()
    seeded = seed_teamflow_catalog(rt)
    print("seed:", seeded)
    only = sys.argv[1] if len(sys.argv) > 1 else ""
    cases = (
        ["tcase_tf_create_normal"]
        if only == "normal"
        else [
            "tcase_tf_create_normal",
            "tcase_tf_false_pass",
            "tcase_tf_ui_missing",
            "tcase_tf_api_error",
        ]
    )
    results = []
    for cid in cases:
        try:
            out = run_case(rt, cid)
            rep = out["report"]
            results.append(
                {
                    "testcase": cid,
                    "verdict": out["test_verdict"],
                    "execution": out["execution_status"],
                    "observer": out["observer_mode"],
                    "browser": rep.get("browser", {}).get("channel"),
                    "steps": len(rep.get("steps", [])),
                    "evidence": len(rep.get("evidence_ids", [])),
                    "assertions": [
                        (a["assertion_id"], a["status"]) for a in rep.get("assertion_outcomes", [])
                    ],
                }
            )
            print(json.dumps(results[-1], ensure_ascii=False))
        except Exception as exc:  # noqa: BLE001
            print(f"CASE_FAILED {cid}: {type(exc).__name__}: {exc}")
    print("\n=== SUMMARY ===")
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0 if all(r["observer"] == "playwright" for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
