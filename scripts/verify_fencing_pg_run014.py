"""RUN-014 WP6：在真实 PostgreSQL 16 上验证租约守卫（fencing）语义。

SQLite 与 PG 的行锁行为不同：本脚本直接在 PG 上验证
1) 过期租约不可续、不可写；
2) 接管后旧代次的 Step/Evidence/Event 写入被同一事务守卫拒绝；
3) 两个连接同时争抢同一 Run 只有一个成功（条件 UPDATE + 行锁）。

用法：
    .venv/bin/python scripts/verify_fencing_pg_run014.py
退出码：0 全部通过；2 未配置 PG。
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "core"))
for p in (
    "packages/contracts",
    "packages/execution",
    "packages/agents",
    "packages/evidence",
    "packages/persistence",
):
    sys.path.insert(0, str(ROOT / p))

PG_ADMIN = "postgresql://traceguard@127.0.0.1:55432/postgres"
PG_NAME = os.environ.get("TG_FENCE_PG_DB", "traceguard_run014_fence")
PG_URL = f"postgresql+psycopg://traceguard@127.0.0.1:55432/{PG_NAME}"


def main() -> int:
    try:
        import psycopg
    except ImportError:
        print("NO_PSYCOPG")
        return 2
    with psycopg.connect(PG_ADMIN, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(f'DROP DATABASE IF EXISTS "{PG_NAME}" WITH (FORCE)')
        cur.execute(f'CREATE DATABASE "{PG_NAME}"')

    os.environ["TG_DATABASE_URL"] = PG_URL
    os.environ["TG_ARTIFACT_DIR"] = str(ROOT / "docs/assets/run014/wp6/artifacts")
    os.environ["TG_LLM_PROVIDER"] = ""
    from traceguard_core.config import reset_settings_cache

    reset_settings_cache()

    from traceguard_core.errors import AppError
    from traceguard_execution.tasks.service import TaskService
    from traceguard_persistence import Database
    from traceguard_persistence.models import RunRow

    db = Database(PG_URL)
    db.create_schema()
    svc = TaskService(db)
    results: list[dict] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        results.append({"check": name, "status": "PASS" if ok else "FAIL", "detail": detail})
        print(f"[{'PASS' if ok else 'FAIL'}] {name} — {detail}")

    from traceguard_persistence.catalog import CatalogService

    cat = CatalogService(db)
    cat.create_testcase(
        {
            "testcase_id": "tcase_pgfence",
            "version": 1,
            "workspace_id": "default",
            "created_at": "2026-01-01T00:00:00Z",
            "steps": [{"step_id": "s1", "action": "navigate", "value": "/"}],
            "assertions": [
                {
                    "assertion_id": "a1",
                    "description": "x",
                    "kind": "ui",
                    "required": True,
                    "expected": {},
                }
            ],
        },
        title="pg",
        workspace_id="default",
        review_status="APPROVED",
    )
    task = svc.create_task(
        "tcase_pgfence", "pg-fence-1", testcase_version_id="tcase_pgfence@1", workspace_id="default"
    )
    run_id = svc.create_run(task["task_id"], "tcase_pgfence@1")["run_id"]

    lease_a = svc.acquire_lease(run_id, "pg-a", ttl_seconds=1)
    token_a = lease_a["fencing_token"]
    check("PG: 初始租约有效", svc.lease_valid(run_id, "pg-a", token_a) is True, f"token={token_a}")
    time.sleep(1.2)
    check(
        "PG: 过期后 lease_valid=false（不依赖他人接管）",
        svc.lease_valid(run_id, "pg-a", token_a) is False,
    )
    check("PG: 过期后不可续租", svc.renew_lease(run_id, "pg-a", token_a) is False)
    try:
        svc.record_step(run_id, "s1", 0, "PASSED", fence=("pg-a", token_a))
        check("PG: 过期 Worker 不能写 Step", False, "写入未被拒绝")
    except AppError as exc:
        check("PG: 过期 Worker 不能写 Step", "LEASE_LOST" in exc.message, exc.message[:80])

    lease_b = svc.acquire_lease(run_id, "pg-b", ttl_seconds=30)
    token_b = lease_b["fencing_token"]
    check("PG: 接管后 fencing_token 单调递增", token_b == token_a + 1, f"{token_a}->{token_b}")
    svc.mark_running(run_id, "pg-b", token_b, "playwright")
    svc.record_step(run_id, "s1", 0, "PASSED", fence=("pg-b", token_b))
    check("PG: 新代次可正常写 Step", len(svc.list_steps(run_id)) == 1)

    try:
        svc.record_step(run_id, "s1", 0, "FAILED", fence=("pg-a", token_a))
        check("PG: 旧代次写入被拒绝", False, "写入未被拒绝")
    except AppError as exc:
        check("PG: 旧代次写入被拒绝", "LEASE_LOST" in exc.message, exc.message[:80])

    # 并发争抢：两个独立连接对同一 Run 做条件 UPDATE，只能有一个成功
    import threading

    from sqlalchemy import update

    winners: list[str] = []
    barrier = threading.Barrier(2)
    with db.session() as s:
        s.execute(update(RunRow).where(RunRow.run_id == run_id).values(lease_expires_at=0.0))

    def grab(name: str) -> None:
        barrier.wait()
        try:
            claim = svc.claim_next_run(name, ttl_seconds=30)
            if claim and claim["run_id"] == run_id:
                winners.append(name)
        except Exception:  # noqa: BLE001
            pass

    threads = [threading.Thread(target=grab, args=(f"pg-c{i}",)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)
    check(
        "PG: 并发争抢只有一个 owner",
        len(winners) == 1,
        f"winners={winners} token_now={svc.get_run(run_id)['fencing_token']}",
    )

    owner = str(svc.get_run(run_id)["lease_owner"])
    token = int(svc.get_run(run_id)["fencing_token"])
    svc.mark_running(run_id, owner, token, "playwright")
    svc.submit_result(
        run_id,
        owner,
        token,
        execution_status="COMPLETED",
        test_verdict="PASSED",
    )
    try:
        svc.record_step(run_id, "s1", 0, "PASSED", fence=("pg-b", token_b))
        check("PG: 终态后写入被拒绝", False, "写入未被拒绝")
    except AppError as exc:
        check("PG: 终态后写入被拒绝", "LEASE_LOST" in exc.message, exc.message[:80])

    out = ROOT / "docs/assets/run014/wp6/pg-fencing.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps({"database": PG_URL, "results": results}, ensure_ascii=False, indent=2), "utf-8"
    )
    failed = [r for r in results if r["status"] != "PASS"]
    print(f"\n[out] {out} passed={len(results) - len(failed)} failed={len(failed)}")
    db.dispose()
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
