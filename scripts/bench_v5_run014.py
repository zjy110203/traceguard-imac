"""RUN-014 WP5：Benchmark v5（23 场景 × 2 轮）独立环境编排。

与长稳/其它实验严格隔离：独立 SQLite 库 + 独立 TeamFlow 库 + 独立端口 + 独立 Worker 进程；
脚本结束后全部进程回收，不污染任何历史归档（输出写到新的 run014 目录）。

用法：
    .venv/bin/python scripts/bench_v5_run014.py
    .venv/bin/python scripts/bench_v5_run014.py --out docs/assets/run015/wp6/v5-2rounds
退出码：0 完成（统计仍可能含失败样本，如实记录）；3 环境不可达。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from business_loop_ui_acceptance_run012 import (  # noqa: E402
    WorkerFleet,
    _child_env,
    _free_port,
    _start_api,
    _wait_health,
)

DEFAULT_OUT = ROOT / "docs/assets/run014/wp5"
PASSWORD = "Bench-v5-2026!"


def main() -> int:
    ap = argparse.ArgumentParser()
    # RUN-015 WP6：新实验写到独立目录，绝不覆盖 RUN-014 原始归档
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    args = ap.parse_args()
    out_arg = Path(args.out)
    OUT = out_arg if out_arg.is_absolute() else ROOT / out_arg
    OUT.mkdir(parents=True, exist_ok=True)
    work = OUT / "_runtime"
    work.mkdir(parents=True, exist_ok=True)
    api_port, tf_port = _free_port(), _free_port()
    db_file = work / "bench-v5.db"
    tf_db = work / "bench-v5-teamflow.db"
    for f in (db_file, tf_db):
        if f.exists():
            f.unlink()
    env = _child_env(
        f"sqlite:///{db_file}",
        api_port,
        tf_port,
        f"sqlite:///{tf_db}",
        inline_worker=False,
        lease_ttl_s=60,
    )
    env["TG_LLM_PROVIDER"] = ""  # 规则臂：评估 LLM 的两臂不在本轮 bench 内混算
    fleet = WorkerFleet(env, OUT, 60)
    tf = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "traceguard_teamflow.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(tf_port),
        ],
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    api = _start_api(env, api_port)
    base, teamflow = f"http://127.0.0.1:{api_port}", f"http://127.0.0.1:{tf_port}"
    try:
        if not (_wait_health(teamflow + "/health") and _wait_health(base + "/health")):
            print("ENV_NOT_READY", flush=True)
            return 3
        roles = [
            ("viewer", "viewer", '["default"]'),
            ("qa-engineer", "qa-engineer", '["default"]'),
            ("qa-lead", "qa-lead", '["default"]'),
            ("platform-admin", "platform-admin", '["*"]'),
        ]
        for username, role, ws in roles:
            r = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "auth_bootstrap.py"),
                    "--db",
                    f"sqlite:///{db_file}",
                    "--username",
                    username,
                    "--password",
                    PASSWORD,
                    "--roles",
                    role,
                    "--workspaces",
                    ws,
                ],
                env=env,
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                timeout=60,
            )
            if r.returncode != 0:
                print(f"BOOTSTRAP_FAIL {username}: {r.stderr[-200:]}", flush=True)
                return 3
        workers = ["r14b-w1", "r14b-w2", "r14b-w3"]
        for w in workers:
            fleet.start(w)
        out_file = OUT / "v5-2rounds.json"
        child_env = dict(env)
        child_env["TG_RUN011_PASSWORD"] = PASSWORD
        child_env["TG_TRACEGUARD_DB"] = f"sqlite:///{db_file}"
        proc = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "bench_v5_run011_r1.py"),
                "--api",
                base,
                "--teamflow",
                teamflow,
                "--workers",
                ",".join(workers),
                "--rounds",
                "2",
                "--out",
                str(out_file),
            ],
            cwd=str(ROOT),
            env=child_env,
            capture_output=True,
            text=True,
            timeout=60 * 60,
        )
        log = OUT / "bench-v5.log"
        log.write_text((proc.stdout or "") + "\n--- stderr ---\n" + (proc.stderr or ""), "utf-8")
        print(proc.stdout[-3000:], flush=True)
        summary: dict[str, Any] = {
            "exit_code": proc.returncode,
            "out_file": str(out_file.relative_to(ROOT)) if out_file.exists() else "",
            "env": {
                "api": base,
                "teamflow": teamflow,
                "database": "sqlite (isolated)",
                "workers": workers,
                "llm": "disabled (rule arm)",
            },
        }
        (OUT / "bench-v5-summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), "utf-8"
        )
        return 0 if proc.returncode == 0 else 1
    finally:
        fleet.close()
        for p in (api, tf):
            p.terminate()
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()


if __name__ == "__main__":
    sys.exit(main())
