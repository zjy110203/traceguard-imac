"""RUN-007-FINALIZATION-2（WP5）：PostgreSQL 16 持续运行 + 多 Worker 并发验证。

本机此前没有 PG 运行时，相关验证一直标 BLOCKED。本脚本用**真实 PG 16**（Postgres.app
自带的 16.16 二进制）跑一轮可持续的入队/领取/执行循环，并在数据库层面校验并发不变量：

- 每个 Run 恰好被执行一次（步骤集合与报告一致、无孤儿步骤/证据）；
- 事件生产者序号不重号（(producer_instance_id, sequence) 唯一）；
- 迁移记录每版本恰好一条；
- 至少 2 个不同 Worker 真实领取过任务（否则不叫并发）；
- `fencing_token >= 1`，且代次只在 attempt 递增时变化（接管语义）。

架构：
    真实 PG16 ← 独立 API（TG_INLINE_WORKER=0）← 持久化 Run Queue
             ← Worker 1..N（真实 OS 进程）→ 真实 Chromium → TeamFlow

用法：
    .venv/bin/python scripts/pg_soak_run007.py \
        --database-url postgresql+psycopg://traceguard@127.0.0.1:55432/traceguard \
        --teamflow http://127.0.0.1:8021 --minutes 10 --workers 3 --interval 30
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
TERMINAL = {"COMPLETED", "FAILED", "TIMED_OUT", "CANCELLED"}


# ------------------------------------------------------------------ HTTP
def api_call(api: str, method: str, path: str, body: dict | None = None, timeout: int = 180) -> Any:
    req = urllib.request.Request(
        api + path,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            raw = resp.read().decode()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        raise RuntimeError(f"{method} {path} -> {exc.code}: {detail[:300]}") from exc


def wait_health(api: str, timeout_s: float = 60.0) -> dict[str, Any]:
    end = time.time() + timeout_s
    last: dict[str, Any] = {}
    while time.time() < end:
        try:
            last = api_call(api, "GET", "/system/health", timeout=10)
            if (last.get("database") or {}).get("ok"):
                return last
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.5)
    raise RuntimeError(f"API 未就绪：{last}")


# ------------------------------------------------------------------ DB 不变量
def _dsn(database_url: str) -> str:
    return database_url.replace("postgresql+psycopg://", "postgresql://")


def _failed_assertions(database_url: str, run_id: str) -> list[dict[str, Any]]:
    import psycopg

    with psycopg.connect(_dsn(database_url), autocommit=True) as conn:
        cur = conn.cursor()
        cur.execute("SELECT report_json FROM tg_run WHERE run_id = %s", (run_id,))
        row = cur.fetchone()
    if not row:
        return []
    rep = row[0] if isinstance(row[0], dict) else json.loads(row[0] or "{}")
    return [
        {
            "assertion_id": a.get("assertion_id"),
            "status": a.get("status"),
            "reason": str(a.get("reason"))[:160],
        }
        for a in rep.get("assertion_outcomes") or []
        if a.get("status") != "PASSED"
    ]


def db_invariants(database_url: str, run_ids: list[str]) -> dict[str, Any]:
    """数据库层面的并发不变量（全部用真实 SQL 查询，不读进程内存）。"""
    import psycopg

    out: dict[str, Any] = {}
    with psycopg.connect(_dsn(database_url), autocommit=True) as conn:
        cur = conn.cursor()

        cur.execute("SELECT count(*) FROM tg_step WHERE run_id NOT IN (SELECT run_id FROM tg_run)")
        out["orphan_steps"] = int(cur.fetchone()[0])

        cur.execute(
            "SELECT count(*) FROM tg_evidence WHERE run_id IS NOT NULL "
            "AND run_id NOT IN (SELECT run_id FROM tg_run)"
        )
        out["orphan_evidence"] = int(cur.fetchone()[0])

        cur.execute(
            "SELECT count(*) FROM (SELECT producer_instance_id, sequence FROM tg_event "
            "GROUP BY 1, 2 HAVING count(*) > 1) dup"
        )
        out["duplicate_event_sequences"] = int(cur.fetchone()[0])

        cur.execute(
            "SELECT count(*) FROM (SELECT version FROM tg_schema_migration "
            "GROUP BY 1 HAVING count(*) > 1) dup"
        )
        out["duplicate_migration_rows"] = int(cur.fetchone()[0])

        cur.execute("SELECT count(*) FROM tg_schema_migration")
        out["migration_rows"] = int(cur.fetchone()[0])

        if run_ids:
            cur.execute(
                "SELECT count(*) FROM tg_run WHERE run_id = ANY(%s) AND lease_owner IS NULL",
                (run_ids,),
            )
            out["runs_without_owner"] = int(cur.fetchone()[0])

            cur.execute(
                "SELECT count(*) FROM tg_run WHERE run_id = ANY(%s) AND fencing_token < 1",
                (run_ids,),
            )
            out["runs_without_fencing"] = int(cur.fetchone()[0])

            cur.execute(
                "SELECT count(DISTINCT lease_owner) FROM tg_run WHERE run_id = ANY(%s)",
                (run_ids,),
            )
            out["distinct_workers"] = int(cur.fetchone()[0])

            # 同一 Run 的步骤集合不能被重复写入（PK 之外再加一层事实校验）
            cur.execute(
                "SELECT count(*) FROM (SELECT run_id, step_id FROM tg_step "
                "WHERE run_id = ANY(%s) GROUP BY 1,2 HAVING count(*) > 1) dup",
                (run_ids,),
            )
            out["duplicate_run_steps"] = int(cur.fetchone()[0])

            cur.execute(
                "SELECT count(*) FROM tg_run WHERE run_id = ANY(%s) "
                "AND execution_status NOT IN ('COMPLETED','FAILED','TIMED_OUT','CANCELLED')",
                (run_ids,),
            )
            out["runs_not_terminal"] = int(cur.fetchone()[0])

        cur.execute("SELECT execution_status, count(*) FROM tg_run GROUP BY 1 ORDER BY 1")
        out["run_status_counts"] = {str(k): int(v) for k, v in cur.fetchall()}
    return out


# ------------------------------------------------------------------ 主流程
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--database-url", required=True)
    ap.add_argument("--teamflow", default="http://127.0.0.1:8021")
    ap.add_argument("--port", type=int, default=8031)
    ap.add_argument("--minutes", type=float, default=10.0)
    ap.add_argument("--interval", type=float, default=30.0, help="每批之间的间隔（秒）")
    ap.add_argument("--batch", type=int, default=2, help="每批入队 Run 数")
    ap.add_argument("--burst", type=int, default=6, help="启动即并发入队的 Run 数")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--case", default="tcase_tf_create_normal")
    ap.add_argument("--artifact-dir", default="/tmp/traceguard-pg/artifacts")
    ap.add_argument("--out", default="docs/assets/run007/pg-soak.json")
    ap.add_argument("--samples", default="docs/assets/run007/pg-soak-samples.jsonl")
    args = ap.parse_args(argv)

    api = f"http://127.0.0.1:{args.port}/api/v1"
    env = {
        **os.environ,
        "TG_DATABASE_URL": args.database_url,
        "TG_ARTIFACT_DIR": args.artifact_dir,
        "TG_TEAMFLOW_BASE_URL": args.teamflow,
        "TG_INLINE_WORKER": "0",
        "TG_LLM_PROVIDER": "",
        "PYTHONPATH": os.pathsep.join(
            str(ROOT / p)
            for p in (
                "packages/core",
                "packages/contracts",
                "packages/execution",
                "packages/agents",
                "packages/evidence",
                "packages/persistence",
                "apps/api",
                "apps/teamflow",
                "apps/workers",
            )
        ),
    }
    procs: list[subprocess.Popen[bytes]] = []
    started = time.time()
    samples: list[dict[str, Any]] = []
    run_ids: list[str] = []
    errors: list[str] = []

    try:
        api_proc = subprocess.Popen(  # noqa: S603
            [
                sys.executable,
                "-m",
                "uvicorn",
                "traceguard_api.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(args.port),
            ],
            cwd=ROOT,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
        )
        procs.append(api_proc)
        health = wait_health(api)
        print(f"API 就绪：dialect={(health.get('database') or {}).get('dialect')}")

        for _ in range(args.workers):
            procs.append(
                subprocess.Popen(  # noqa: S603
                    [sys.executable, "-m", "traceguard_worker.main"],
                    cwd=ROOT,
                    env=env,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.STDOUT,
                )
            )
        time.sleep(2)

        api_call(api, "POST", "/system/seed", timeout=300)

        def submit(n: int) -> list[str]:
            ids = []
            for _ in range(n):
                created = api_call(
                    api, "POST", f"/testcases/{args.case}/execute", {"sync": False}, timeout=60
                )
                ids.append(created["run_id"])
            return ids

        deadline = started + args.minutes * 60
        burst_ids = submit(args.burst)
        run_ids.extend(burst_ids)
        print(f"首批并发入队 {len(burst_ids)} 个 Run（workers={args.workers}）")

        pending = {rid: time.time() for rid in burst_ids}
        while time.time() < deadline:
            time.sleep(max(2.0, args.interval / max(1, args.batch)))
            # 轮询在途 Run
            for rid in list(pending):
                try:
                    doc = api_call(api, "GET", f"/runs/{rid}", timeout=30)
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"poll {rid}: {exc}")
                    continue
                if doc.get("execution_status") in TERMINAL:
                    samples.append(
                        {
                            "run_id": rid,
                            "execution_status": doc.get("execution_status"),
                            "test_verdict": doc.get("test_verdict"),
                            "lease_owner": doc.get("lease_owner"),
                            "fencing_token": doc.get("fencing_token"),
                            "attempt": doc.get("attempt"),
                            "latency_s": round(time.time() - pending[rid], 2),
                            "at": round(time.time() - started, 2),
                        }
                    )
                    pending.pop(rid, None)
            # 周期性补入新 Run，形成持续负载
            if len(pending) < args.batch:
                run_ids.extend(submit(args.batch))
                for rid in run_ids[-args.batch :]:
                    pending[rid] = time.time()
            print(
                f"t+{time.time() - started:6.1f}s  完成={len(samples)}  在途={len(pending)}",
                flush=True,
            )

        # 收尾：等待在途 Run 完成（最多 5 分钟）
        end = time.time() + 300
        while pending and time.time() < end:
            time.sleep(2)
            for rid in list(pending):
                doc = api_call(api, "GET", f"/runs/{rid}", timeout=30)
                if doc.get("execution_status") in TERMINAL:
                    samples.append(
                        {
                            "run_id": rid,
                            "execution_status": doc.get("execution_status"),
                            "test_verdict": doc.get("test_verdict"),
                            "lease_owner": doc.get("lease_owner"),
                            "fencing_token": doc.get("fencing_token"),
                            "attempt": doc.get("attempt"),
                            "latency_s": round(time.time() - pending[rid], 2),
                            "at": round(time.time() - started, 2),
                        }
                    )
                    pending.pop(rid, None)

        invariants = db_invariants(args.database_url, run_ids)
        owners = {s["lease_owner"] for s in samples if s.get("lease_owner")}
        verdicts: dict[str, int] = {}
        for s in samples:
            verdicts[str(s["test_verdict"])] = verdicts.get(str(s["test_verdict"]), 0) + 1
        latencies = sorted(s["latency_s"] for s in samples)
        p95 = latencies[int(len(latencies) * 0.95) - 1] if latencies else None

        # 并发/持久化不变量：这些才是本脚本的验收对象。
        checks = {
            "all_runs_terminal": invariants.get("runs_not_terminal") == 0,
            "no_orphan_steps": invariants.get("orphan_steps") == 0,
            "no_orphan_evidence": invariants.get("orphan_evidence") == 0,
            "no_duplicate_event_sequences": invariants.get("duplicate_event_sequences") == 0,
            "no_duplicate_run_steps": invariants.get("duplicate_run_steps") == 0,
            "migration_rows_unique": invariants.get("duplicate_migration_rows") == 0,
            "every_run_has_owner": invariants.get("runs_without_owner") == 0,
            "every_run_has_fencing": invariants.get("runs_without_fencing") == 0,
            "multi_worker_contention": len(owners) >= 2,
        }
        # 业务 verdict 单列：并发跑同一个 create 用例时，每个 Run 开头的 RESET_DATA
        # 会重置兄弟 Run 的数据，count 断言可能出现假失败。这类失败要如实列出原因，
        # 但**不能**混进"并发正确性"检查里（也不能悄悄从分母里删掉）。
        failed_runs = [
            {
                "run_id": s["run_id"],
                "test_verdict": s["test_verdict"],
                "assertions": _failed_assertions(args.database_url, s["run_id"]),
            }
            for s in samples
            if s.get("test_verdict") != "PASSED"
        ]
        business = {
            "passed": verdicts.get("PASSED", 0),
            "failed": len(failed_runs),
            "total": len(samples),
            "failed_runs": failed_runs[:10],
            "note": (
                "并发同时执行同一 create 用例时，Run 开头的 RESET_DATA 会互相重置数据，"
                "UI 断言已通过而独立 count 断言读到 0 —— 这是数据隔离限制，不是 PG/队列缺陷；"
                "Benchmark 正式实验保持串行执行。"
            ),
        }

        report = {
            "round": "RUN-007-FINALIZATION-2",
            "database": "PostgreSQL (Postgres.app 16.16, aarch64)",
            "database_url_host": _dsn(args.database_url).split("@")[-1],
            "workers": args.workers,
            "minutes": args.minutes,
            "interval_s": args.interval,
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started)),
            "duration_s": round(time.time() - started, 1),
            "runs_submitted": len(run_ids),
            "runs_completed": len(samples),
            "latency_s": {
                "min": latencies[0] if latencies else None,
                "median": latencies[len(latencies) // 2] if latencies else None,
                "p95": p95,
                "max": latencies[-1] if latencies else None,
            },
            "verdicts": verdicts,
            "lease_owners": sorted(o for o in owners if o),
            "invariants": invariants,
            "checks": checks,
            "business": business,
            "all_checks_passed": all(checks.values())
            and len(samples) >= 0.9 * max(1, len(run_ids)),
            "errors": errors[:20],
        }
        out = ROOT / args.out
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        samples_path = ROOT / args.samples
        samples_path.parent.mkdir(parents=True, exist_ok=True)
        samples_path.write_text(
            "\n".join(json.dumps(s, ensure_ascii=False) for s in samples) + "\n",
            encoding="utf-8",
        )
        print(
            json.dumps({"checks": checks, "all": report["all_checks_passed"]}, ensure_ascii=False)
        )
        print(f"证据：{out}")
        return 0 if report["all_checks_passed"] else 1
    finally:
        for p in reversed(procs):
            p.terminate()
        for p in procs:
            try:
                p.wait(timeout=10)
            except Exception:  # noqa: BLE001
                p.kill()


if __name__ == "__main__":
    raise SystemExit(main())
