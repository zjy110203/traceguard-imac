"""RUN-007 WP1-04：ProbeBudget 真正的多 OS 进程并发验证。

不是线程模拟：每个 worker 都是独立 Python 进程（`spawn`，无共享内存），
共享同一个 SQLite/PostgreSQL 数据库与同一个 Budget Scope。

验证目标：
1. 总扣减量不超过预算（不超发）；
2. 某个进程被 SIGKILL 后，预算状态不会被重置或回退；
3. 已关闭的预算不能被新进程绕过；
4. 能读到最终文档（数据完整、可解析、probes_used 与回执一致）。

用法：
    python scripts/probe_budget_multiprocess.py --db .traceguard/run007/budget.db \
        --scope demo --procs 4 --budget 20 --attempts 20 --out .traceguard/run007/budget.json
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import signal
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
# pytest 靠 pyproject 的 `pythonpath` 注入本地包；独立脚本与 spawn 子进程没有这个机制，
# 必须自行注入，否则子进程会 `ModuleNotFoundError`（CI backend-full 曾因此失败）。
# 用目录扫描而非硬编码清单：`traceguard_persistence` 会间接 import `traceguard_evidence`，
# 硬编码漏一个包就会在 CI 上炸。
for _base in ("packages", "apps"):
    _dir = ROOT / _base
    if _dir.is_dir():
        for _sub in sorted(_dir.iterdir()):
            if _sub.is_dir() and str(_sub) not in sys.path:
                sys.path.insert(0, str(_sub))


def _child_main(cfg: dict[str, Any]) -> None:
    db_url = cfg["db_url"]
    scope = cfg["scope"]
    attempts = int(cfg["attempts"])
    out_path = cfg["out_path"]
    delay = float(cfg.get("reserve_delay", 0.0))

    from traceguard_api.runtime import Runtime
    from traceguard_api.services.probe_service import ProbeService
    from traceguard_persistence import Database

    result: dict[str, Any] = {
        "pid": os.getpid(),
        "scope": scope,
        "reserved": 0,
        "rejected": 0,
        "reasons": {},
        "error": "",
    }
    db = Database(db_url)
    try:
        rt = Runtime(db)
        svc = ProbeService(rt)
        budget = svc.budgets.get_or_create(scope, workspace_id="default")
        for _ in range(attempts):
            reason = svc.budgets.reserve(budget, 1.0)
            if reason:
                result["rejected"] += 1
                result["reasons"][reason] = result["reasons"].get(reason, 0) + 1
            else:
                result["reserved"] += 1
            if delay:
                time.sleep(delay)
        snap = svc.budgets.get_or_create(scope, workspace_id="default").snapshot()
        result["probes_used_seen"] = snap.get("probes_used")
        result["closed_seen"] = snap.get("closed")
    except Exception as exc:  # noqa: BLE001 - 子进程必须把失败写回证据
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        db.dispose()
    Path(out_path).write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")


def _spawn(cfg: dict[str, Any]) -> mp.Process:
    ctx = mp.get_context("spawn")
    p = ctx.Process(target=_child_main, args=(cfg,))
    p.start()
    return p


def _read_budget(db_url: str, scope: str) -> dict[str, Any] | None:
    from traceguard_api.runtime import Runtime
    from traceguard_api.services.probe_service import ProbeService
    from traceguard_persistence import Database

    db = Database(db_url)
    try:
        rt = Runtime(db)
        svc = ProbeService(rt)
        rec = rt.docs.get(svc.budgets.get_or_create(scope, workspace_id="default").budget_id)
        return rec.to_dict() if rec else None
    finally:
        db.dispose()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--db", required=True, help="sqlite 文件路径，例如 .traceguard/run007/budget.db"
    )
    ap.add_argument("--scope", default="multiproc")
    ap.add_argument("--procs", type=int, default=4)
    ap.add_argument("--budget", type=int, default=20)
    ap.add_argument("--attempts", type=int, default=20)
    ap.add_argument("--cost", type=float, default=1.0)
    ap.add_argument("--kill-one", action="store_true", help="中途 SIGKILL 一个 worker")
    ap.add_argument("--close-first", action="store_true", help="先把预算关闭，再让子进程尝试申请")
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)

    db_path = Path(args.db).resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db_url = f"sqlite:///{db_path}"
    out_dir = Path(args.out).resolve().parent
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1) 父进程建立预算（固定上限，避免子进程各自用默认值）
    from traceguard_api.runtime import Runtime
    from traceguard_api.services.probe_service import ProbeService
    from traceguard_persistence import Database

    db = Database(db_url)
    rt = Runtime(db)
    svc = ProbeService(rt)
    budget = svc.budgets.get_or_create(
        args.scope,
        workspace_id="default",
        overrides={
            "max_probes": args.budget,
            "max_total_cost": float(args.budget) * 10,
            "max_duration_seconds": 3600.0,
        },
    )
    budget_id = budget.budget_id
    if args.close_first:
        svc.budgets.close(args.scope, "TEST_CLOSED")
    db.dispose()

    # 2) 启动独立进程
    cfg_base = {
        "db_url": db_url,
        "scope": args.scope,
        "attempts": args.attempts,
        "reserve_delay": 0.01,
    }
    procs: list[tuple[mp.Process, str]] = []
    for i in range(args.procs):
        child_out = str(out_dir / f"worker-{i}-{time.time_ns()}.json")
        proc = _spawn({**cfg_base, "out_path": child_out})
        procs.append((proc, child_out))

    killed_pid = 0
    if args.kill_one and procs:
        time.sleep(0.6)
        proc, _ = procs[0]
        if proc.is_alive():
            killed_pid = proc.pid or 0
            os.kill(proc.pid, signal.SIGKILL)

    for proc, _ in procs:
        proc.join(timeout=180)

    # 3) 汇总子进程证据
    children = []
    for _, path in procs:
        p = Path(path)
        if p.exists():
            children.append(json.loads(p.read_text(encoding="utf-8")))
    reserved_total = sum(int(c.get("reserved", 0)) for c in children)
    errors = [c["error"] for c in children if c.get("error")]

    final = _read_budget(db_url, args.scope)
    # DocRecord.to_dict() 把 doc 字段平铺在顶层
    doc = final or {}
    probes_used = int(doc.get("probes_used", 0))

    checks = {
        "no_overdraft": probes_used <= args.budget,
        "counter_persisted": probes_used == reserved_total,
        "not_reset_after_kill": (probes_used > 0) if reserved_total > 0 else True,
        "doc_readable": bool(final),
        "closed_not_bypassed": (
            probes_used == 0 and all(c.get("rejected", 0) > 0 for c in children)
            if args.close_first
            else True
        ),
    }
    report = {
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "db": str(db_path),
        "scope": args.scope,
        "budget_id": budget_id,
        "max_probes": args.budget,
        "procs": args.procs,
        "attempts_per_proc": args.attempts,
        "cost_per_probe": args.cost,
        "kill_one": args.kill_one,
        "killed_pid": killed_pid,
        "close_first": args.close_first,
        "reserved_total": reserved_total,
        "final_probes_used": probes_used,
        "final_snapshot": doc,
        "children": children,
        "errors": errors,
        "checks": checks,
        "passed": all(checks.values()) and not errors,
    }
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {k: report[k] for k in ("passed", "reserved_total", "final_probes_used")},
            ensure_ascii=False,
        )
    )
    print(json.dumps(checks, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
