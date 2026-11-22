"""阶段2 演示：真实运行 TeamFlow + 执行内核，输出 Run 报告到 tests/reports。

用法（先手动启动 TeamFlow :8011）：
  .venv/bin/python scripts/run_demo.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [
    str(ROOT / "apps" / "api"),
    str(ROOT / "packages" / "core"),
    str(ROOT / "apps" / "teamflow"),
    str(ROOT / "packages" / "contracts"),
    str(ROOT / "packages" / "execution"),
]

from traceguard_contracts.models import OracleSpec  # noqa: E402
from traceguard_execution.oracle.manager import OracleManager  # noqa: E402
from traceguard_execution.runtime.http_observer import HttpObserver  # noqa: E402
from traceguard_execution.runtime.orchestrator import LocalExecutor  # noqa: E402
from traceguard_execution.testcase.loader import load_testcase_version  # noqa: E402


def main() -> None:
    mgr = OracleManager()
    for f in sorted((ROOT / "tests" / "fixtures" / "oracles").glob("*.json")):
        mgr.register(OracleSpec(**json.loads(f.read_text())))

    outdir = ROOT / "tests" / "reports"
    outdir.mkdir(parents=True, exist_ok=True)
    cases = sorted((ROOT / "tests" / "fixtures" / "testcases").glob("*.json"))
    results: list[dict] = []
    for i, cf in enumerate(cases, start=1):
        tc = load_testcase_version(cf)
        rep = LocalExecutor(HttpObserver(), mgr).run(tc, f"run_demo_{i:02d}")
        results.append(rep.to_dict())
        print(f"[{rep.testcase_id}] verdict={rep.test_verdict.value} mode={rep.observer_mode}")

    report_path = outdir / "stage2_demo_runs.json"
    report_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n报告已写入：{report_path}")


if __name__ == "__main__":
    main()
