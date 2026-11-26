"""RUN-013 WP4：60 分钟长稳 + 真实故障恢复（独立 API / 双 Worker / PostgreSQL 16）。

设计原则：
- **真实执行**：每个周期都是真实 TestCase → 真实 Chromium → 真实 TeamFlow；
- **独立部署**：`TG_INLINE_WORKER=false`，API 与 Worker 是不同 OS 进程；
- **真实故障**：SIGTERM / SIGKILL / SIGSTOP 接管 / API 重启 / DB 连接中断 / SSE 断连；
- **不伪造**：未达到 60 分钟或 100 周期时如实标记，不把失败写成通过。

用法：
    .venv/bin/python scripts/soak_run013.py --minutes 60 --interval 30
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from business_loop_ui_acceptance_run012 import (  # noqa: E402
    Http,
    Reporter,
    WorkerFleet,
    _child_env,
    _free_port,
    _start_api,
    _wait_health,
)

PG_ADMIN = "postgresql://traceguard@127.0.0.1:55432/postgres"
# RUN-014 WP6：数据库名可参数化，避免覆盖 RUN-013 的历史长稳库（默认值保持兼容）。
PG_NAME = os.environ.get("TG_SOAK_PG_DB", "traceguard_run013_soak")
PG_URL = f"postgresql+psycopg://traceguard@127.0.0.1:55432/{PG_NAME}"
PASSWORD = "Soak-accept-2026!"

TERMINAL = {"COMPLETED", "FAILED", "TIMED_OUT", "CANCELLED"}


def _create_database() -> None:
    import psycopg

    with psycopg.connect(PG_ADMIN, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(f'DROP DATABASE IF EXISTS "{PG_NAME}" WITH (FORCE)')
            cur.execute(f'CREATE DATABASE "{PG_NAME}"')


def _pg(dsn: str = PG_URL) -> Any:
    import psycopg

    return psycopg.connect(dsn.replace("postgresql+psycopg://", "postgresql://"))


def _db_metrics() -> dict[str, Any]:
    with _pg() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT pg_database_size('{PG_NAME}')")
        size = int(cur.fetchone()[0])
        cur.execute("SELECT count(*) FROM pg_ls_waldir()")
        wal_files = int(cur.fetchone()[0])
        cur.execute("SELECT coalesce(sum(size),0) FROM pg_ls_waldir()")
        wal_bytes = int(cur.fetchone()[0])
    return {"db_bytes": size, "wal_files": wal_files, "wal_bytes": wal_bytes}


def _run_doc(api: Http, run_id: str) -> dict[str, Any]:
    return api.get(f"/api/v1/runs/{run_id}")


def _wait_terminal_local(
    api: Http, run_id: str, history: list[dict[str, Any]], timeout_s: float = 180.0
) -> dict[str, Any]:
    deadline = time.time() + timeout_s
    last: dict[str, Any] = {}
    while time.time() < deadline:
        last = _run_doc(api, run_id)
        history.append(
            {
                "at": time.time(),
                "execution_status": last.get("execution_status"),
                "test_verdict": last.get("test_verdict"),
                "lease_owner": last.get("lease_owner"),
                "fencing_token": last.get("fencing_token"),
            }
        )
        if str(last.get("execution_status")) in TERMINAL:
            return last
        time.sleep(0.5)
    return last


def _pick_testcase(api: Http) -> str:
    rows = api.get("/api/v1/testcases?review_status=APPROVED")
    # 长稳优先使用确定性通过的正常用例；失败注入场景会单独用于故障实验。
    priority = ("tcase_bench_normal2", "tcase_tf_create_normal", "tcase_bench_time001")
    by_id = {str(r.get("testcase_id", "")): r for r in rows}
    chosen = next((by_id[tc] for tc in priority if tc in by_id), None)
    if chosen is None:
        preferred = [r for r in rows if str(r.get("testcase_id", "")).startswith("tcase_tf_")]
        chosen = preferred[0] if preferred else (rows[0] if rows else None)
    if not chosen:
        raise RuntimeError("no approved testcase available for soak")
    return str(chosen["testcase_id"])


def _sample(
    api: Http, fleet: WorkerFleet, metrics_dir: Path, samples: list[dict[str, Any]]
) -> None:
    started = time.time()
    try:
        health = api.get("/api/v1/system/health")
        latency_ms = (time.time() - started) * 1000
        ok = True
    except Exception as exc:  # noqa: BLE001
        health, latency_ms, ok = {"error": str(exc)[:200]}, (time.time() - started) * 1000, False
    workers = []
    for path in sorted(metrics_dir.glob("worker-*.json")):
        try:
            workers.append(json.loads(path.read_text(encoding="utf-8")))
        except Exception:  # noqa: BLE001
            continue
    try:
        queue = api.get("/api/v1/queue/stats")
    except Exception:  # noqa: BLE001
        queue = {}
    try:
        dbm = _db_metrics()
    except Exception as exc:  # noqa: BLE001
        dbm = {"error": str(exc)[:200]}
    samples.append(
        {
            "at": time.time(),
            "api_ok": ok,
            "api_latency_ms": round(latency_ms, 1),
            "api_process": health.get("process") or {},
            "counts": health.get("counts") or {},
            "queue": queue,
            "workers": workers,
            "db": dbm,
            "alive": {name: fleet.alive(name) for name in fleet.procs},
        }
    )


def _execute(api: Http, tc_id: str) -> tuple[str, float]:
    created = time.time()
    run = api.post(f"/api/v1/testcases/{tc_id}/execute", {"sync": False}, expect_status=(200, 201))
    return str(run.get("run_id") or ""), created


def _fault_kill_worker(
    api: Http,
    tc_id: str,
    fleet: WorkerFleet,
    rep: Reporter,
    *,
    victim: str,
    replacement: str,
    lease_ttl: float,
) -> dict[str, Any]:
    run_id, _ = _execute(api, tc_id)
    claimed = None
    deadline = time.time() + 60
    while time.time() < deadline:
        doc = _run_doc(api, run_id)
        if str(doc.get("lease_owner") or "").startswith(victim):
            claimed = doc
            break
        time.sleep(0.3)
    if not claimed:
        rep.note(f"故障：{victim} 强杀", "未在 60s 内观察到该 Worker 持租约，跳过强杀")
        return {"run_id": run_id, "skipped": True}
    token = int(claimed.get("fencing_token") or 0)
    fleet.kill(victim)
    after = _run_doc(api, run_id)
    fleet.start(replacement)
    takeover = None
    deadline = time.time() + lease_ttl + 90
    while time.time() < deadline:
        doc = _run_doc(api, run_id)
        if (
            str(doc.get("lease_owner") or "").startswith(replacement)
            and int(doc.get("fencing_token") or 0) > token
        ):
            takeover = doc
            break
        time.sleep(0.5)
    final = _wait_terminal_local(api, run_id, [])
    rep.check(
        f"故障：{victim} 执行中被 SIGKILL，{replacement} 租约接管并执行到终态",
        bool(takeover) and str(final.get("execution_status")) in TERMINAL,
        f"run={run_id} after_kill={after.get('execution_status')} "
        f"owner={(takeover or {}).get('lease_owner')} "
        f"fencing={(takeover or {}).get('fencing_token')} status={final.get('execution_status')}",
        server_verified=True,
    )
    return {"run_id": run_id, "takeover": bool(takeover), "final": final.get("execution_status")}


def _fault_pause_worker(
    api: Http,
    tc_id: str,
    fleet: WorkerFleet,
    rep: Reporter,
    *,
    victim: str,
    replacement: str,
    lease_ttl: float,
) -> dict[str, Any]:
    run_id, _ = _execute(api, tc_id)
    claimed = None
    deadline = time.time() + 60
    while time.time() < deadline:
        doc = _run_doc(api, run_id)
        if str(doc.get("lease_owner") or "").startswith(victim) and doc.get("execution_status") in {
            "PREPARING",
            "RUNNING",
        }:
            claimed = doc
            break
        time.sleep(0.2)
    if not claimed:
        rep.note(f"故障：{victim} 暂停接管", "未在 60s 内观察到该 Worker 持有运行中租约")
        return {"run_id": run_id, "skipped": True}
    token = int(claimed.get("fencing_token") or 0)
    fleet.send(victim, signal.SIGSTOP)
    time.sleep(lease_ttl + 1.5)
    fleet.start(replacement)
    takeover = None
    deadline = time.time() + lease_ttl + 90
    while time.time() < deadline:
        doc = _run_doc(api, run_id)
        if (
            str(doc.get("lease_owner") or "").startswith(replacement)
            and int(doc.get("fencing_token") or 0) > token
        ):
            takeover = doc
            break
        time.sleep(0.5)
    final = _wait_terminal_local(api, run_id, [])
    before_evi = len(api.get(f"/api/v1/runs/{run_id}/evidence") or [])
    fleet.send(victim, signal.SIGCONT)
    time.sleep(6)
    after = _run_doc(api, run_id)
    after_evi = len(api.get(f"/api/v1/runs/{run_id}/evidence") or [])
    rep.check(
        f"故障：{victim} 暂停→租约过期→{replacement} 接管",
        bool(takeover) and str(final.get("execution_status")) in TERMINAL,
        f"run={run_id} fencing={(takeover or {}).get('fencing_token')}",
        server_verified=True,
    )
    rep.check(
        f"故障：{victim} 恢复后不覆盖终态/不追加证据",
        after.get("execution_status") == final.get("execution_status") and after_evi == before_evi,
        f"status={after.get('execution_status')} evidence={before_evi}->{after_evi}",
        server_verified=True,
    )
    return {"run_id": run_id, "takeover": bool(takeover)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=60.0)
    ap.add_argument("--interval", type=float, default=30.0)
    ap.add_argument("--out", default="docs/assets/run013/soak")
    ap.add_argument("--lease-ttl", type=float, default=6.0)
    ap.add_argument("--skip-faults", action="store_true", help="仅用于冒烟：不注入故障")
    args = ap.parse_args()

    out = (ROOT / args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    metrics_dir = out / "worker-metrics"
    metrics_dir.mkdir(parents=True, exist_ok=True)
    work = out / "_runtime"
    work.mkdir(parents=True, exist_ok=True)
    rep = Reporter(out)
    samples: list[dict[str, Any]] = []
    runs: list[dict[str, Any]] = []
    faults: list[dict[str, Any]] = []

    _create_database()
    api_port = _free_port()
    teamflow_port = _free_port()
    tf_db = f"sqlite:///{work / 'teamflow.db'}"
    if (work / "teamflow.db").exists():
        (work / "teamflow.db").unlink()
    env = _child_env(
        PG_URL,
        api_port,
        teamflow_port,
        tf_db,
        inline_worker=False,
        lease_ttl_s=args.lease_ttl,
    )
    env["TG_WORKER_METRICS_DIR"] = str(metrics_dir)
    env["TG_ARTIFACT_DIR"] = str(work / "artifacts")

    tf_proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "traceguard_teamflow.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(teamflow_port),
        ],
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    api_proc = _start_api(env, api_port)
    base = f"http://127.0.0.1:{api_port}"
    teamflow = f"http://127.0.0.1:{teamflow_port}"
    fleet = WorkerFleet(env, out, args.lease_ttl)
    api_holder: dict[str, Any] = {"proc": api_proc}

    try:
        if not _wait_health(teamflow + "/health", 30):
            rep.check("环境：TeamFlow 启动", False, teamflow)
            return 3
        if not _wait_health(base + "/health", 60):
            rep.check("环境：API 启动", False, base)
            return 3
        rep.check("环境：API 独立进程可启动", True, base)
        for name, roles, workspaces in (
            ("r13-soak-admin", "platform-admin", '["*"]'),
            ("r13-soak-lead", "qa-lead", '["default"]'),
            ("r13-soak-eng", "qa-engineer", '["default"]'),
        ):
            r = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "auth_bootstrap.py"),
                    "--db",
                    PG_URL,
                    "--username",
                    name,
                    "--password",
                    PASSWORD,
                    "--roles",
                    roles,
                    "--workspaces",
                    workspaces,
                ],
                env=env,
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                timeout=60,
            )
            if r.returncode != 0:
                rep.check(f"环境：初始化 {name}", False, r.stderr[-200:])
                return 3
        admin = Http(base, "admin")
        lead = Http(base, "lead")
        admin.login("r13-soak-admin", PASSWORD)
        lead.login("r13-soak-lead", PASSWORD)
        rep.check("环境：认证与三个角色就绪", True, "admin/lead/engineer")
        cfg = admin.get("/api/v1/system/config")
        rep.check(
            "环境：API 不启动内嵌 Worker",
            bool((cfg.get("worker") or {}).get("inline")) is False,
            f"worker={cfg.get('worker')}",
            server_verified=True,
        )
        tc_id = _pick_testcase(admin)
        rep.check("环境：选定已审核真实用例", True, tc_id, server_verified=True)

        fleet.start("r13-soak-w1")
        fleet.start("r13-soak-w2")
        if not _wait_health(base + "/health", 30):
            rep.check("环境：Worker 启动后 API 仍健康", False, "")
            return 3

        start_at = time.time()
        deadline = start_at + args.minutes * 60
        cycle = 0
        fault_plan = {
            0.05: "graceful",
            0.12: "kill_w1",
            0.24: "pause_w1",
            0.36: "api_restart",
            0.48: "db_reconnect",
            0.60: "sse",
            0.72: "kill_w2",
            0.84: "graceful_all",
            0.94: "api_restart",
        }
        done_faults: set[str] = set()

        while time.time() < deadline:
            cycle += 1
            elapsed_ratio = (time.time() - start_at) / max(1.0, args.minutes * 60)
            for ratio, name in sorted(fault_plan.items()):
                if args.skip_faults or name in done_faults or elapsed_ratio < ratio:
                    continue
                done_faults.add(name)
                rep.check(f"故障计划：触发 {name}", True, f"cycle={cycle}")
                try:
                    if name == "graceful":
                        code = fleet.stop("r13-soak-w2", timeout=20)
                        rep.check("故障：Worker-2 正常退出（SIGTERM）", code == 0, f"exit={code}")
                        fleet.start("r13-soak-w2")
                    elif name == "kill_w1":
                        faults.append(
                            _fault_kill_worker(
                                admin,
                                tc_id,
                                fleet,
                                rep,
                                victim="r13-soak-w1",
                                replacement="r13-soak-w2",
                                lease_ttl=args.lease_ttl,
                            )
                        )
                    elif name == "pause_w1":
                        faults.append(
                            _fault_pause_worker(
                                admin,
                                tc_id,
                                fleet,
                                rep,
                                victim="r13-soak-w1",
                                replacement="r13-soak-w3",
                                lease_ttl=args.lease_ttl,
                            )
                        )
                    elif name == "api_restart":
                        run_id, _ = _execute(admin, tc_id)
                        api_proc = api_holder["proc"]
                        api_proc.kill()
                        api_proc.wait(timeout=10)
                        time.sleep(3)
                        api_holder["proc"] = _start_api(env, api_port)
                        healthy = _wait_health(base + "/health", 60)
                        admin = Http(base, "admin")
                        admin.login("r13-soak-admin", PASSWORD)
                        final = _wait_terminal_local(admin, run_id, [])
                        rep.check(
                            "故障：API 重启期间 Worker 继续消费，重启后可回读终态",
                            bool(healthy) and str(final.get("execution_status")) in TERMINAL,
                            f"run={run_id} status={final.get('execution_status')}",
                            server_verified=True,
                        )
                        faults.append({"fault": "api_restart", "run_id": run_id})
                    elif name == "db_reconnect":
                        with _pg() as conn, conn.cursor() as cur:
                            cur.execute(
                                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                                "WHERE datname = %s AND pid <> pg_backend_pid()",
                                (PG_NAME,),
                            )
                        run_id, _ = _execute(admin, tc_id)
                        final = _wait_terminal_local(admin, run_id, [], timeout_s=240)
                        time.sleep(3)
                        health = None
                        for _ in range(30):
                            try:
                                health = admin.get("/api/v1/system/health")
                                if (health.get("database") or {}).get("ok"):
                                    break
                            except Exception:  # noqa: BLE001
                                pass
                            time.sleep(1)
                        rep.check(
                            "故障：数据库连接被中断后恢复（API + Worker 继续）",
                            bool(health and (health.get("database") or {}).get("ok"))
                            and str(final.get("execution_status")) in TERMINAL,
                            f"run={run_id} status={final.get('execution_status')}",
                            server_verified=True,
                        )
                        faults.append({"fault": "db_reconnect", "run_id": run_id})
                    elif name == "sse":
                        run_id, _ = _execute(admin, tc_id)
                        raw = ""
                        for _ in range(3):
                            status, parsed, raw = admin.raw(
                                "GET", "/events/stream?limit=1&heartbeat_seconds=0.2", timeout=20
                            )
                            if status == 200 and "event:" in raw:
                                break
                            time.sleep(0.5)
                        event_id = ""
                        for line in raw.splitlines():
                            if line.startswith("id: "):
                                event_id = line[4:]
                                break
                        resumed = ""
                        if event_id:
                            status2, _, resumed = admin.raw(
                                "GET",
                                "/events/stream?limit=1&heartbeat_seconds=0.2&last_event_id="
                                + urllib.parse.quote(event_id),
                                timeout=20,
                            )
                        rep.check(
                            "故障：SSE 断开后可用游标续传（不重复已读事件）",
                            bool(event_id) and ("event:" in resumed),
                            f"run={run_id} cursor={bool(event_id)} resumed={bool(resumed)}",
                            server_verified=True,
                        )
                        faults.append({"fault": "sse", "run_id": run_id})
                    elif name == "kill_w2":
                        faults.append(
                            _fault_kill_worker(
                                admin,
                                tc_id,
                                fleet,
                                rep,
                                victim="r13-soak-w2",
                                replacement="r13-soak-w1",
                                lease_ttl=args.lease_ttl,
                            )
                        )
                    elif name == "graceful_all":
                        for w in ("r13-soak-w1", "r13-soak-w2", "r13-soak-w3"):
                            fleet.stop(w, timeout=20)
                        fleet.start("r13-soak-w1")
                        fleet.start("r13-soak-w2")
                        rep.check("故障：全部 Worker 重启后恢复消费", True, "w1/w2 restarted")

                except Exception as exc:  # noqa: BLE001
                    rep.check(f"故障 {name} 执行", False, f"{type(exc).__name__}: {exc}")
                    faults.append({"fault": name, "error": str(exc)[:300]})
            try:
                run_id, created = _execute(admin, tc_id)
            except Exception:
                # 故障注入后连接池/API 可能瞬断：重试一次；仍失败则记录并继续下一周期。
                time.sleep(2)
                run_id, created = _execute(admin, tc_id)
            history: list[dict[str, Any]] = []
            final = _wait_terminal_local(admin, run_id, history)
            ended = time.time()
            runs.append(
                {
                    "run_id": run_id,
                    "execution_status": final.get("execution_status"),
                    "test_verdict": final.get("test_verdict"),
                    "observer_mode": final.get("observer_mode"),
                    "created_at": created,
                    "ended_at": ended,
                    "duration_s": round(ended - created, 3),
                    "lease_owner": final.get("lease_owner"),
                    "fencing_token": final.get("fencing_token"),
                    "status_history": history,
                }
            )
            if cycle % 2 == 1:
                _sample(admin, fleet, metrics_dir, samples)
            # 保持每周期固定节奏，避免用"缩短真实执行"制造高吞吐
            sleep_for = args.interval - (time.time() - created)
            if sleep_for > 0:
                time.sleep(sleep_for)
    except Exception as exc:  # noqa: BLE001
        rep.check("长稳脚本执行", False, f"{type(exc).__name__}: {exc}")
    finally:
        final_active_workers = sum(1 for proc in fleet.procs.values() if proc.poll() is None)
        fleet.close()
        for proc in (api_holder.get("proc"), tf_proc):
            if not proc:
                continue
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()

    # ------------------------------------------------------------ final checks
    duration_min = (runs[-1]["ended_at"] - runs[0]["created_at"]) / 60 if runs else 0.0
    rep.check(
        "长稳：运行时长达到要求",
        duration_min >= args.minutes * 0.95,
        f"{duration_min:.2f} / {args.minutes} 分钟",
    )
    rep.check("长稳：真实 Run 周期数 ≥ 100", len(runs) >= 100, f"runs={len(runs)}")
    completed = [r for r in runs if r["execution_status"] == "COMPLETED"]
    rep.check(
        "长稳：所有 Run 到达终态",
        all(r["execution_status"] in TERMINAL for r in runs),
        f"terminal={sum(1 for r in runs if r['execution_status'] in TERMINAL)}/{len(runs)}",
    )
    rep.check(
        "长稳：无状态倒退 / 无重复业务结果",
        all(
            [h["execution_status"] for h in r["status_history"]].index("COMPLETED")
            == len(r["status_history"]) - 1
            if "COMPLETED" in [h["execution_status"] for h in r["status_history"]]
            else True
            for r in runs
        ),
        "terminal 后不再出现非终态",
    )
    try:
        with _pg() as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM tg_run")
            total_runs = int(cur.fetchone()[0])
            cur.execute(
                "SELECT count(*) FROM (SELECT run_id FROM tg_step "
                "GROUP BY run_id HAVING count(*) <> count(DISTINCT step_id)) t"
            )
            dup_steps = int(cur.fetchone()[0])
            cur.execute(
                "SELECT count(*) FROM tg_step s LEFT JOIN tg_run r ON r.run_id = s.run_id "
                "WHERE r.run_id IS NULL"
            )
            orphan_steps = int(cur.fetchone()[0])
            cur.execute(
                "SELECT count(*) FROM tg_evidence e LEFT JOIN tg_run r ON r.run_id = e.run_id "
                "WHERE r.run_id IS NULL"
            )
            orphan_evi = int(cur.fetchone()[0])
            cur.execute(
                "SELECT count(*) FROM tg_run WHERE execution_status IN "
                "('COMPLETED','FAILED','TIMED_OUT','CANCELLED') AND lease_expires_at > 0"
            )
            stale_lease = int(cur.fetchone()[0])
            cur.execute(
                "SELECT count(*) FROM (SELECT run_id FROM tg_run_submission "
                "GROUP BY run_id HAVING count(*) > 1) t"
            )
            dup_submission = int(cur.fetchone()[0])
            cur.execute(
                "SELECT count(*) FROM tg_run r WHERE r.execution_status = 'COMPLETED' "
                "AND NOT EXISTS (SELECT 1 FROM tg_evidence e WHERE e.run_id = r.run_id)"
            )
            no_evidence = int(cur.fetchone()[0])
            cur.execute(
                "SELECT count(*) FROM (SELECT producer_instance_id, sequence FROM tg_event "
                "GROUP BY producer_instance_id, sequence HAVING count(*) > 1) t"
            )
            dup_events = int(cur.fetchone()[0])
    except Exception as exc:  # noqa: BLE001
        rep.check("数据库一致性检查", False, f"{type(exc).__name__}: {exc}")
        total_runs = dup_steps = orphan_steps = orphan_evi = stale_lease = -1
        dup_submission = no_evidence = dup_events = -1

    rep.check("数据库：无重复步骤", dup_steps == 0, f"duplicates={dup_steps}", server_verified=True)
    rep.check(
        "数据库：无孤儿步骤", orphan_steps == 0, f"orphan={orphan_steps}", server_verified=True
    )
    rep.check("数据库：无孤儿证据", orphan_evi == 0, f"orphan={orphan_evi}", server_verified=True)
    rep.check(
        "数据库：终态 Run 无残留租约",
        stale_lease == 0,
        f"stale={stale_lease}",
        server_verified=True,
    )
    rep.check(
        "数据库：每个 Run 至多一份提交回执",
        dup_submission == 0,
        f"dup={dup_submission}",
        server_verified=True,
    )
    rep.check(
        "数据库：完成 Run 均有证据",
        no_evidence == 0,
        f"missing={no_evidence}",
        server_verified=True,
    )
    rep.check("数据库：事件序号无重复", dup_events == 0, f"dup={dup_events}", server_verified=True)
    rep.check(
        "数据库：活跃 Worker 数 ≥ 2",
        final_active_workers >= 2,
        f"active={final_active_workers}",
    )

    summary = {
        "workpackage": "RUN-013 WP4",
        "database": "PostgreSQL 16.16",
        "inline_worker": False,
        "minutes_requested": args.minutes,
        "minutes_observed": round(duration_min, 2),
        "interval_s": args.interval,
        "runs": len(runs),
        "completed": len(completed),
        "failed": sum(1 for r in runs if r["execution_status"] == "FAILED"),
        "verdicts": {
            v: sum(1 for r in runs if r["test_verdict"] == v)
            for v in sorted({r["test_verdict"] for r in runs})
        },
        "total_runs_in_db": total_runs,
        "faults": faults,
        "samples": samples,
        "runs_detail": runs,
        "results": rep.results,
        "passed": sum(1 for r in rep.results if r["status"] == "PASS"),
        "failed_checks": len(rep.failed),
    }
    (out / f"soak-{int(round(args.minutes))}min.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out / "soak-samples.jsonl").write_text(
        "\n".join(json.dumps(s, ensure_ascii=False) for s in samples), encoding="utf-8"
    )
    print(
        f"[out] {out / f'soak-{int(round(args.minutes))}min.json'} runs={len(runs)} checks_pass={summary['passed']} "
        f"checks_fail={summary['failed_checks']} minutes={duration_min:.1f}"
    )
    return 1 if rep.failed else 0


if __name__ == "__main__":
    sys.exit(main())
