"""RUN-007 WP1-04：真实多 OS 进程的 ProbeBudget 并发验证（调用脚本，不是线程模拟）。

用 `spawn` 启动独立 Python 进程，共享同一 SQLite 文件与同一 budget scope：
- 总扣减不超过预算；
- 其中一个进程被 SIGKILL 后预算不被重置；
- 关闭后的预算不能被新进程绕过。

本测试只做"小规模可重复"版本；完整证据（4 进程 × 更大次数）在
`docs/assets/run007/probe-budget-multiprocess.json`。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "probe_budget_multiprocess.py"


def _run(tmp_path: Path, *args: str) -> dict:
    db = tmp_path / "budget.db"
    out = tmp_path / "report.json"
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--db", str(db), "--out", str(out), *args],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert out.exists(), proc.stderr + proc.stdout
    report = json.loads(out.read_text(encoding="utf-8"))
    report["_exit"] = proc.returncode
    report["_stdout"] = proc.stdout
    return report


def test_multiprocess_reservations_stay_within_budget(tmp_path: Path) -> None:
    report = _run(
        tmp_path,
        "--scope",
        "pytest-mp",
        "--procs",
        "3",
        "--budget",
        "6",
        "--attempts",
        "5",
    )
    assert report["_exit"] == 0, report["_stdout"]
    assert report["reserved_total"] <= 6
    assert report["final_probes_used"] == report["reserved_total"]
    assert len(report["children"]) == 3
    assert report["checks"]["no_overdraft"] is True
    unknown = [c for c in report["children"] if c.get("error")]
    assert not unknown, unknown


def test_multiprocess_state_survives_killed_worker(tmp_path: Path) -> None:
    report = _run(
        tmp_path,
        "--scope",
        "pytest-kill",
        "--procs",
        "3",
        "--budget",
        "4",
        "--attempts",
        "4",
        "--kill-one",
    )
    assert report["_exit"] == 0, report["_stdout"]
    assert report["killed_pid"] > 0
    assert report["final_probes_used"] <= 4
    assert report["final_probes_used"] == report["reserved_total"]
    assert report["checks"]["not_reset_after_kill"] is True


def test_closed_budget_cannot_be_bypassed_by_new_process(tmp_path: Path) -> None:
    report = _run(
        tmp_path,
        "--scope",
        "pytest-closed",
        "--procs",
        "2",
        "--budget",
        "5",
        "--attempts",
        "3",
        "--close-first",
    )
    assert report["_exit"] == 0, report["_stdout"]
    assert report["final_probes_used"] == 0
    for child in report["children"]:
        assert child["reserved"] == 0
        assert child["reasons"].get("PROBE_BUDGET_CLOSED") == 3
