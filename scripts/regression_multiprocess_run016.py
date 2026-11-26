"""RUN-016 WP2.1：Regression 并发唯一性 —— PostgreSQL 16 真实多进程验收。

与 RUN-015 的线程级测试的差别：

- **10 个独立操作系统进程**（`multiprocessing` spawn），不是线程；
- 每个进程**独立数据库连接 + 独立 Runtime / RepairService 实例**；
- 用 `multiprocessing.Barrier` 做同步屏障，让 10 个请求在同一时刻竞争同一个
  PROMOTED 提案的回归创建；
- 另有 1 个独立 Worker 进程消费队列（模拟真实部署），API 侧不内嵌执行。

判定（全部要求成立）：
1. 只有一个有效 Regression Claim；
2. 只有一个 Regression Run；
3. 所有请求最终指向同一 Run ID；
4. 只有一条 Regression History；
5. 无重复执行 / 无重复回执 / 无状态覆盖；
6. 新建 Runtime（等价重启）仍能恢复到原 Run；
7. 失败请求（跨 Workspace / 缺前置条件）不破坏并发赢家的状态。

用法：
    TG_REGRESSION_PG_DB=traceguard_run016_regression \
      .venv/bin/python scripts/regression_multiprocess_run016.py \
        --procs 10 --out docs/assets/run016/wp2/regression-multiprocess.json
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for extra in (
    "packages/core",
    "packages/contracts",
    "packages/execution",
    "packages/agents",
    "packages/evidence",
    "packages/persistence",
    "apps/api",
):
    sys.path.insert(0, str(ROOT / extra))

PG_ADMIN = os.environ.get("TG_R16_PG_ADMIN", "postgresql://traceguard@127.0.0.1:55432/postgres")
PG_NAME = os.environ.get("TG_REGRESSION_PG_DB", "traceguard_run016_regression")
PG_URL = f"postgresql+psycopg://traceguard@127.0.0.1:55432/{PG_NAME}"

ORACLE_ID = "oracle_r16_a1"
TC_ID = "tcase_reg16_mp"


# --------------------------------------------------------------------------- seed
def _payload() -> dict:
    return {
        "testcase_id": TC_ID,
        "version": 1,
        "workspace_id": "default",
        "steps": [
            {"step_id": "s1", "action": "navigate", "value": "/projects"},
            {
                "step_id": "s2",
                "action": "click",
                "target": {"strategy": "test_id", "value": "nav-a"},
            },
        ],
        "assertions": [
            {
                "assertion_id": "a1",
                "description": "标题存在",
                "kind": "ui",
                "required": True,
                "expected": {"test_id": "page-title"},
                "oracle_id": ORACLE_ID,
            }
        ],
        "run_config": {"base_url": "http://127.0.0.1:8011"},
        "compile_source": "human",
        "created_at": "2026-01-01T00:00:00Z",
    }


def _seed(url: str) -> str:
    from traceguard_api.runtime import Runtime
    from traceguard_api.services.repair_service import RepairService
    from traceguard_persistence import Database

    db = Database(url)
    db.create_schema()
    rt = Runtime(db)
    rt.catalog.create_oracle(
        {
            "oracle_id": ORACLE_ID,
            "assertion_id": "a1",
            "expected_behavior": "标题必须存在",
            "requirement_ref": "REQ-16",
            "version": 1,
        },
        approved=True,
        approved_by="role:qa-lead",
    )
    rt.catalog.create_testcase(_payload(), title="reg16-mp", review_status="PENDING")
    rt.catalog.review_testcase(TC_ID, 1, "APPROVED", reviewer="role:qa-lead")
    svc = RepairService(rt)
    doc = svc.propose(
        testcase_id=TC_ID,
        base_version=1,
        changes=[
            {
                "path": "steps[].target",
                "step_index": 1,
                "value": {"strategy": "test_id", "value": "nav-a"},
                "reason": "locator fix",
            }
        ],
        reason="run016-mp",
        created_by="human:dev",
        actor="human:dev",
        repair_source="human:dev",
    )
    svc.validate(doc["proposal_id"])
    doc = svc.get(doc["proposal_id"])
    doc["sandbox_status"] = "PASSED"
    doc["sandbox_verdict"] = "PASSED"
    doc["sandbox_run_id"] = "run_sandbox_r16"
    svc._save(doc)
    svc.submit_for_approval(doc["proposal_id"])
    svc.approve(doc["proposal_id"], "human:lead", "ok")
    promoted = svc.promote(doc["proposal_id"], idempotency_key="promo-r16-mp")
    db.dispose()
    return str(promoted["proposal_id"])


# --------------------------------------------------------------------------- children
def _child_request(idx: int, url: str, proposal_id: str, barrier, queue, timeout_s: float) -> None:
    from traceguard_api.runtime import Runtime
    from traceguard_api.services.repair_service import RepairService
    from traceguard_persistence import Database

    db = Database(url)
    rt = Runtime(db)  # 每个进程独立 Runtime / 独立连接池
    svc = RepairService(rt)
    try:
        barrier.wait(timeout=120)
        started = time.time()
        result = svc.regression_run(proposal_id, timeout_s=timeout_s)
        queue.put(
            {
                "idx": idx,
                "ok": True,
                "run_id": result.get("regression_run_id"),
                "status": result.get("regression_status"),
                "verdict": result.get("regression_verdict"),
                "elapsed_s": round(time.time() - started, 3),
                "pid": os.getpid(),
            }
        )
    except BaseException as exc:  # noqa: BLE001 - 进程边界收集
        queue.put({"idx": idx, "ok": False, "error": f"{type(exc).__name__}: {exc}"[:300]})
    finally:
        db.dispose()


def _child_worker(url: str, barrier, queue, stop_event, worker_id: str) -> None:
    from traceguard_api.runtime import Runtime
    from traceguard_persistence import Database

    db = Database(url)
    rt = Runtime(db)
    processed = 0
    try:
        barrier.wait(timeout=120)
        while not stop_event.is_set():
            claimed = rt.tasks.claim_next_run(worker_id, ttl_seconds=60)
            if not claimed:
                time.sleep(0.02)
                continue
            token = int(claimed["fencing_token"])
            run_id = claimed["run_id"]
            rt.tasks.mark_running(run_id, worker_id, token, "playwright")
            rt.tasks.record_step(
                run_id,
                "s1",
                0,
                "PASSED",
                action="navigate",
                fence=(worker_id, token),
            )
            rt.tasks.submit_result(
                run_id,
                worker_id,
                token,
                execution_status="COMPLETED",
                test_verdict="PASSED",
                report={
                    "run_id": run_id,
                    "evidence_ids": ["evd_r16_1", "evd_r16_2"],
                    "errors": [],
                    "worker_pid": os.getpid(),
                },
            )
            processed += 1
    except BaseException as exc:  # noqa: BLE001
        queue.put({"worker": worker_id, "error": f"{type(exc).__name__}: {exc}"[:300]})
    finally:
        queue.put({"worker": worker_id, "processed": processed, "pid": os.getpid()})
        db.dispose()


# --------------------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--procs", type=int, default=10)
    ap.add_argument("--timeout", type=float, default=45.0)
    ap.add_argument("--out", default="docs/assets/run016/wp2/regression-multiprocess.json")
    args = ap.parse_args()

    import psycopg

    with psycopg.connect(PG_ADMIN, autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{PG_NAME}" WITH (FORCE)')
        conn.execute(f'CREATE DATABASE "{PG_NAME}"')
    os.environ["TG_DATABASE_URL"] = PG_URL
    os.environ["TG_LLM_PROVIDER"] = ""
    os.environ.setdefault("TG_ARTIFACT_DIR", str(ROOT / ".traceguard/run016-regression-artifacts"))
    from traceguard_core.config import reset_settings_cache

    reset_settings_cache()

    proposal_id = _seed(PG_URL)
    ctx = mp.get_context("spawn")
    barrier = ctx.Barrier(args.procs + 1)
    queue = ctx.Queue()
    stop_event = ctx.Event()
    log: dict = {
        "workpackage": "RUN-016 WP2.1",
        "database": "PostgreSQL",
        "database_name": PG_NAME,
        "proposal_id": proposal_id,
        "processes": args.procs,
        "started_at_iso": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "started_at_epoch": time.time(),
    }

    worker = ctx.Process(
        target=_child_worker, args=(PG_URL, barrier, queue, stop_event, "r16-mp-w1")
    )
    worker.start()
    procs = [
        ctx.Process(
            target=_child_request, args=(i, PG_URL, proposal_id, barrier, queue, args.timeout)
        )
        for i in range(args.procs)
    ]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=args.timeout + 120)
    time.sleep(1.0)
    stop_event.set()
    worker.join(timeout=60)

    results: list[dict] = []
    worker_reports: list[dict] = []
    while not queue.empty():
        item = queue.get()
        if "worker" in item:
            worker_reports.append(item)
        else:
            results.append(item)
    results.sort(key=lambda r: r["idx"])
    log["requests"] = results
    log["worker_reports"] = worker_reports
    log["exit_codes"] = [p.exitcode for p in procs] + [worker.exitcode]

    # ---------------------------------------------------------------- invariant checks
    from sqlalchemy import func, select
    from traceguard_api.runtime import Runtime
    from traceguard_api.services.repair_service import RepairService
    from traceguard_persistence import Database
    from traceguard_persistence.models import RegressionClaimRow, RunSubmissionRow

    db = Database(PG_URL)
    rt = Runtime(db)
    svc = RepairService(rt)
    status = svc.regression_status(proposal_id)
    doc = svc.get(proposal_id)
    task_id = str(doc.get("regression_task_id") or "")
    runs = rt.tasks.list_runs(task_id=task_id, limit=100) if task_id else []
    with db.session() as s:
        claims = s.scalars(select(RegressionClaimRow)).all()
        claim_rows = [
            {"proposal_id": c.proposal_id, "run_id": c.run_id, "claim_key": c.claim_key}
            for c in claims
        ]
        subs = 0
        if runs:
            subs = int(
                s.scalar(
                    select(func.count())
                    .select_from(RunSubmissionRow)
                    .where(RunSubmissionRow.run_id == runs[0]["run_id"])
                )
                or 0
            )

    run_ids = {r.get("run_id") for r in results if r.get("ok")}
    checks = {
        "全部请求成功返回": all(r.get("ok") for r in results),
        "请求数等于进程数": len(results) == args.procs,
        "唯一有效 Regression Claim": len(claim_rows) == 1,
        "唯一 Regression Run": len(runs) == 1,
        "所有请求指向同一 Run": len(run_ids) == 1 and None not in run_ids,
        "唯一 Regression History": len(status.get("regression_history") or []) == 1,
        "每个 Run 至多一份提交回执": subs <= 1,
        "最终结论为 PASSED": status.get("regression_status") == "PASSED"
        and status.get("regression_verdict") == "PASSED",
        "无重复执行（Run 数 == 1）": len(runs) == 1,
        "Worker 实际消费了 Run": any(w.get("processed", 0) >= 1 for w in worker_reports),
    }

    # 重启恢复：新 Runtime 读同一 DB
    restarted = Runtime(Database(PG_URL))
    recovered = RepairService(restarted).regression_status(proposal_id)
    checks["新 Runtime 恢复原 Run"] = recovered.get("regression_run_id") == status.get(
        "regression_run_id"
    )
    checks["重复请求不创建新 Run"] = RepairService(restarted).regression_run(
        proposal_id, timeout_s=5
    ).get("regression_run_id") == status.get("regression_run_id")
    # 失败请求不破坏赢家状态
    before = RepairService(restarted).get(proposal_id)
    try:
        RepairService(restarted).regression_run("prop_does_not_exist", timeout_s=2)
        rejected = False
    except Exception:  # noqa: BLE001 - 期望被拒绝
        rejected = True
    after = RepairService(restarted).get(proposal_id)
    checks["失败请求不破坏赢家状态"] = (
        rejected
        and after["regression_status"] == before["regression_status"]
        and after.get("regression_run_id") == before.get("regression_run_id")
    )

    log["run_ids"] = sorted(x for x in run_ids if x)
    log["claim_rows"] = claim_rows
    log["runs_in_db"] = len(runs)
    log["submissions"] = subs
    log["regression_status"] = status.get("regression_status")
    log["regression_verdict"] = status.get("regression_verdict")
    log["history_len"] = len(status.get("regression_history") or [])
    log["checks"] = checks
    log["passed"] = sum(1 for v in checks.values() if v)
    log["failed"] = [k for k, v in checks.items() if not v]
    log["ended_at_iso"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    log["ended_at_epoch"] = time.time()
    log["status"] = "PASS" if not log["failed"] else "FAIL"

    out = (ROOT / args.out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[out] {out}")
    for key, ok in checks.items():
        print(f"[{'PASS' if ok else 'FAIL'}] {key}")
    db.dispose()
    return 0 if log["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
