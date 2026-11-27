"""RUN-007 WP1-04 / RUN-018 WP1：ProbeBudget 真正的多 OS 进程并发验证。

不是线程模拟：每个 worker 都是独立 Python 进程（`spawn`，无共享内存），
共享同一个 SQLite/PostgreSQL 数据库与同一个 Budget Scope。

验证目标：
1. 总扣减量不超过预算（不超发）；
2. 某个进程被 SIGKILL 后，预算状态不会被重置或回退；
3. 已关闭的预算不能被新进程绕过；
4. 能读到最终文档（数据完整、可解析、probes_used 与回执一致）。

RUN-018 WP1 确定性 kill 协议（替代旧的固定 sleep(0.6)）：
1. 目标子进程完成第一次有效预算预留后写出 ready 证据文件并发出 READY 事件；
2. 目标子进程进入明确的等待状态（wait_event）；
3. 父进程等待 READY（超时即确定性失败，不 SKIP）；
4. 父进程确认子进程仍然存活；
5. 父进程执行真实 SIGKILL；
6. 回读共享数据库，记录 kill 前的 probes_used；
7. 启动其余子进程继续工作；
8. 验证预算状态没有重置、其他进程继续扣减；
9. 保存真实 killed_pid、退出码（-9）和最终数据库状态。

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
    ready_path = cfg.get("ready_path")
    release_path = cfg.get("release_path")

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
                # 确定性 READY 协议（文件版，不共享 mp 锁）：
                # 第一次有效预留后写出 ready 证据文件并进入明确等待状态。
                # 注意：绝不与会被 SIGKILL 的进程共享 multiprocessing 锁——
                # 被杀进程若持有 POSIX 信号量会把父进程永久锁死。
                if ready_path and not Path(ready_path).exists():
                    Path(ready_path).write_text(
                        json.dumps(
                            {
                                "pid": os.getpid(),
                                "reserved": result["reserved"],
                                "scope": scope,
                                "ready_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                            },
                            ensure_ascii=False,
                        ),
                        encoding="utf-8",
                    )
                    if release_path:
                        # 明确等待状态：轮询 release 文件（父进程 kill 后会写出）。
                        deadline = time.monotonic() + 120.0
                        released = False
                        while time.monotonic() < deadline:
                            if Path(release_path).exists():
                                released = True
                                break
                            time.sleep(0.05)
                        if released:
                            break  # 父进程放行且未 kill（兜底路径），继续剩余 attempts
                        result["error"] = "release_timeout_without_parent_release"
                        break
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


def _spawn(cfg: dict[str, Any], ctx: mp.context.BaseContext) -> mp.Process:
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
    ap.add_argument("--kill-one", action="store_true", help="确定性 SIGKILL 一个 worker")
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

    ctx = mp.get_context("spawn")
    kill_diagnostics: dict[str, Any] = {}
    killed_pid = 0
    killed_exitcode: int | None = None
    killed_reserved = 0
    probes_used_before_kill: int | None = None
    ready_timeout = False

    if args.kill_one:
        # 确定性 kill 协议（文件同步版）：先只启动目标进程，等它完成第一次有效预留并 READY。
        # 不使用 mp.Event：被 SIGKILL 的进程可能持有信号量锁，会把父进程永久锁死。
        ready_path = out_dir / f"kill-target-ready-{time.time_ns()}.json"
        release_path = out_dir / f"kill-target-release-{time.time_ns()}.json"
        target_cfg = {
            "db_url": db_url,
            "scope": args.scope,
            "attempts": args.attempts,
            "reserve_delay": 0.01,
            "out_path": str(out_dir / f"worker-0-{time.time_ns()}.json"),
            "ready_path": str(ready_path),
            "release_path": str(release_path),
        }
        target = _spawn(target_cfg, ctx)

        # 步骤 3：父进程轮询等待 READY 文件（确定性，不允许用固定 sleep 替代）
        deadline = time.monotonic() + 60.0
        got_ready = False
        while time.monotonic() < deadline:
            if ready_path.exists():
                got_ready = True
                break
            time.sleep(0.05)
        if not got_ready:
            ready_timeout = True
            kill_diagnostics["ready_timeout"] = True
            kill_diagnostics["target_alive_after_timeout"] = target.is_alive()
        # 步骤 4：确认子进程仍然存活
        if got_ready and target.is_alive():
            killed_pid = target.pid or 0
            kill_diagnostics["confirmed_alive"] = True
        elif got_ready:
            kill_diagnostics["confirmed_alive"] = False

        # 步骤 6：kill 前回读共享数据库
        if got_ready:
            pre = _read_budget(db_url, args.scope)
            doc = pre or {}
            probes_used_before_kill = int(doc.get("probes_used", 0))

        # 步骤 5：真实 SIGKILL（不是模拟退出）
        if killed_pid > 0:
            os.kill(killed_pid, signal.SIGKILL)
        release_path.write_text(
            "released", encoding="utf-8"
        )  # 兜底放行，避免目标进程意外存活时永久挂起
        target.join(timeout=60)
        killed_exitcode = target.exitcode
        ready_file = Path(ready_path)
        if ready_file.exists():
            try:
                killed_reserved = int(
                    json.loads(ready_file.read_text(encoding="utf-8"))["reserved"]
                )
            except Exception:  # noqa: BLE001
                killed_reserved = 0

        # 步骤 7：其余子进程在 kill 之后启动并继续工作
        procs: list[tuple[mp.Process, str]] = []
        for i in range(1, args.procs):
            child_out = str(out_dir / f"worker-{i}-{time.time_ns()}.json")
            proc = _spawn(
                {
                    "db_url": db_url,
                    "scope": args.scope,
                    "attempts": args.attempts,
                    "reserve_delay": 0.01,
                    "out_path": child_out,
                },
                ctx,
            )
            procs.append((proc, child_out))
    else:
        procs = []
        for i in range(args.procs):
            child_out = str(out_dir / f"worker-{i}-{time.time_ns()}.json")
            proc = _spawn(
                {
                    "db_url": db_url,
                    "scope": args.scope,
                    "attempts": args.attempts,
                    "reserve_delay": 0.01,
                    "out_path": child_out,
                },
                ctx,
            )
            procs.append((proc, child_out))

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
    if args.kill_one and ready_timeout:
        errors.append("READY_TIMEOUT: 目标子进程 60s 内未完成第一次有效预留")

    final = _read_budget(db_url, args.scope)
    # DocRecord.to_dict() 把 doc 字段平铺在顶层
    doc = final or {}
    probes_used = int(doc.get("probes_used", 0))

    # kill 场景：被杀进程的真实预留数必须计入总账（来自 ready 证据文件）
    reserved_all = reserved_total + killed_reserved

    checks = {
        "no_overdraft": probes_used <= args.budget,
        "counter_persisted": probes_used == reserved_all,
        "not_reset_after_kill": (
            (probes_used_before_kill is not None and probes_used >= probes_used_before_kill > 0)
            if args.kill_one
            else (probes_used > 0 if reserved_all > 0 else True)
        ),
        "doc_readable": bool(final),
        "closed_not_bypassed": (
            probes_used == 0 and all(c.get("rejected", 0) > 0 for c in children)
            if args.close_first
            else True
        ),
        # RUN-018 WP1 新增：kill 确定性检查
        "killed_for_real": ((killed_pid > 0 and killed_exitcode == -9) if args.kill_one else True),
        "others_continue_work": (
            (reserved_total > 0 and len(children) >= args.procs - 1) if args.kill_one else True
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
        "killed_exitcode": killed_exitcode,
        "killed_reserved": killed_reserved,
        "probes_used_before_kill": probes_used_before_kill,
        "kill_diagnostics": kill_diagnostics,
        "close_first": args.close_first,
        "reserved_total": reserved_all,
        "reserved_from_survivors": reserved_total,
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
            {k: report[k] for k in ("passed", "reserved_total", "final_probes_used", "killed_pid")},
            ensure_ascii=False,
        )
    )
    print(json.dumps(checks, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
