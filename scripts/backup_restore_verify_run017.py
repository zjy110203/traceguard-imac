"""RUN-017 WP5.1 / WP5.2：备份与恢复的**真实**验收（SQLite + PostgreSQL 16）。

不是"文档里写了备份命令"，而是：

1. 在独立临时库里创建真实业务数据（TestCase / Run / Event / Evidence / Artifact）；
2. 执行备份（SQLite：复制数据库 + 产物目录；PG：`pg_dump -Fc`）；
3. 恢复到**全新目录 / 全新数据库**；
4. 启动新 Runtime，逐项回读并校验：
   - 记录数量、Event 序号、Evidence 完整性（重算摘要 + 附件 SHA-256）、
     Artifact SHA-256、Run 报告、TestCase 版本、数据库迁移版本；
5. 在恢复后的库里继续执行一次正常业务动作（证明不是只读快照）。

安全边界：只使用本轮创建的临时目录与临时数据库，不触碰任何用户数据。

用法：
    .venv/bin/python scripts/backup_restore_verify_run017.py \
      --out docs/assets/run017/wp5/backup-restore
    # PostgreSQL 16（需要 pg_dump / pg_restore）：
    .venv/bin/python scripts/backup_restore_verify_run017.py --pg-admin-url \
      postgresql://traceguard@127.0.0.1:55432/postgres
退出码：0 全部通过；1 有失败；3 环境不可用（BLOCKED）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for _p in (
    "packages/core",
    "packages/contracts",
    "packages/execution",
    "packages/agents",
    "packages/evidence",
    "packages/persistence",
    "apps/api",
):
    sys.path.insert(0, str(ROOT / _p))

#: 常见安装位置（本机 Postgres.app / Homebrew / Linux 包管理器）
PG_BIN_CANDIDATES = (
    "/Volumes/Postgres-2.9.7-16/Postgres.app/Contents/Versions/16/bin",
    "/Applications/Postgres.app/Contents/Versions/16/bin",
    "/opt/homebrew/opt/postgresql@16/bin",
    "/usr/local/opt/postgresql@16/bin",
    "/usr/lib/postgresql/16/bin",
)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def find_pg_tool(name: str) -> str | None:
    """在 PATH 与常见目录中查找 pg_dump/pg_restore/psql。"""
    found = shutil.which(name)
    if found:
        return found
    for base in PG_BIN_CANDIDATES:
        candidate = Path(base) / name
        if candidate.is_file():
            return str(candidate)
    return None


# --------------------------------------------------------------------- 数据构造
def build_business_data(db_url: str, artifact_dir: Path) -> dict[str, Any]:
    """在给定库中创建一组真实业务数据，返回可校验的标识。"""
    from traceguard_api.runtime import Runtime
    from traceguard_persistence import Database

    db = Database(db_url)
    db.create_schema()
    try:
        rt = Runtime(db)
        tc_id = f"tcase_bak_{uuid.uuid4().hex[:8]}"
        rt.catalog.create_testcase(
            {
                "testcase_id": tc_id,
                "version": 1,
                "workspace_id": "default",
                "created_at": "2026-01-01T00:00:00Z",
                "steps": [{"step_id": "s1", "action": "navigate", "value": "/projects"}],
                "assertions": [
                    {
                        "assertion_id": "a1",
                        "description": "标题存在",
                        "kind": "ui",
                        "required": True,
                        "expected": {"test_id": "page-title"},
                    }
                ],
                "compile_source": "human",
            },
            title="backup-restore",
            review_status="APPROVED",
        )
        task = rt.tasks.create_task(
            tc_id,
            f"bak-{uuid.uuid4().hex[:8]}",
            testcase_version_id=f"{tc_id}@1",
            workspace_id="default",
        )
        run = rt.tasks.create_run(task["task_id"], f"{tc_id}@1")
        run_id = run["run_id"]
        lease = rt.tasks.acquire_lease(run_id, "worker-backup", ttl_seconds=60)
        token = int(lease["fencing_token"])
        rt.tasks.mark_running(run_id, "worker-backup", token, "playwright")

        artifact = rt.artifacts.put(
            b"backup-restore-artifact", workspace_id="default", original_name="proof.txt"
        )
        rt.evidence.add_raw(
            f"evd_bak_{uuid.uuid4().hex[:8]}",
            source_type="pipeline",
            source_ref=f"run:{run_id}",
            workspace_id="default",
            content={"note": "backup-restore", "value": 42},
            source_trust="TRUSTED",
            run_id=run_id,
            attachment_ref=artifact["artifact_id"],
            attachment_sha256=artifact["sha256"],
        )
        rt.tasks.submit_result(
            run_id,
            "worker-backup",
            token,
            execution_status="COMPLETED",
            test_verdict="PASSED",
            report={
                "run_id": run_id,
                "evidence_ids": [e.evidence_id for e in rt.evidence.list(run_id=run_id)],
                "trace_artifact": artifact["artifact_id"],
            },
            observer_mode="playwright",
            error=None,
        )
        events = rt.events.list_events(run_id=run_id)
        return {
            "testcase_id": tc_id,
            "testcase_version_ref": f"{tc_id}@1",
            "task_id": task["task_id"],
            "run_id": run_id,
            "artifact_id": artifact["artifact_id"],
            "artifact_sha256": artifact["sha256"],
            "artifact_path": artifact["path"],
            "evidence_ids": [e.evidence_id for e in rt.evidence.list(run_id=run_id)],
            "event_count": len(events),
            "event_seqs": [int(e.seq) for e in events][:50],
        }
    finally:
        db.dispose()


def verify_restored(db_url: str, artifact_dir: Path, expected: dict[str, Any]) -> dict[str, Any]:
    """在恢复后的库上逐项回读并校验；任何一项失败都如实记录。"""
    from traceguard_api.runtime import Runtime
    from traceguard_persistence import Database
    from traceguard_persistence.db import MIGRATIONS

    checks: dict[str, Any] = {}
    db = Database(db_url)
    try:
        rt = Runtime(db)
        version = rt.catalog.get_version(expected["testcase_id"], 1)
        checks["testcase_version"] = bool(version and version.get("review_status") == "APPROVED")

        run = rt.tasks.get_run(expected["run_id"])
        checks["run_exists"] = bool(run)
        report = (run or {}).get("report") or {}
        checks["run_verdict"] = (run or {}).get("test_verdict") == "PASSED"
        checks["run_report_preserved"] = report.get("run_id") == expected["run_id"]

        evidence = rt.evidence.list(run_id=expected["run_id"])
        checks["evidence_count"] = len(evidence) == len(expected["evidence_ids"])
        integrity = [
            rt.evidence.verify_storage_integrity(item.evidence_id, artifacts=rt.artifacts)
            for item in evidence
        ]
        checks["evidence_integrity"] = all(i.get("status") == "PASS" for i in integrity)
        checks["evidence_integrity_detail"] = [i.get("status") for i in integrity]

        artifact_path = Path(expected["artifact_path"])
        if not artifact_path.is_file():
            # 恢复目录不同：按 sha256 在恢复后的产物根目录里找
            candidate = (
                Path(artifact_dir) / expected["artifact_sha256"][:2] / expected["artifact_sha256"]
            )
            artifact_path = candidate
        checks["artifact_file_exists"] = artifact_path.is_file()
        if artifact_path.is_file():
            digest = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
            checks["artifact_sha256"] = digest == expected["artifact_sha256"]
        else:
            checks["artifact_sha256"] = False

        events = rt.events.list_events(run_id=expected["run_id"])
        checks["event_seqs_preserved"] = [int(e.seq) for e in events][:50] == expected["event_seqs"]

        with db.session() as s:
            from sqlalchemy import text

            rows = s.execute(text("SELECT version FROM tg_schema_migration")).all()
        versions = sorted(int(r[0]) for r in rows)
        checks["migration_versions"] = versions == sorted(v for v, _n, _m in MIGRATIONS)

        # 恢复后继续执行：证明不是只读快照
        task = rt.tasks.create_task(
            expected["testcase_id"],
            f"post-restore-{uuid.uuid4().hex[:8]}",
            testcase_version_id=expected["testcase_version_ref"],
            workspace_id="default",
        )
        new_run = rt.tasks.create_run(task["task_id"], expected["testcase_version_ref"])
        checks["post_restore_write"] = bool(new_run.get("run_id"))
        return {
            "checks": checks,
            "all_passed": all(v is True for k, v in checks.items() if isinstance(v, bool)),
        }
    finally:
        db.dispose()


# ------------------------------------------------------------------ SQLite 用例
def sqlite_case(work: Path) -> dict[str, Any]:
    src = work / "source"
    backup = work / "backup"
    restored = work / "restored"
    for path in (src, backup, restored):
        path.mkdir(parents=True, exist_ok=True)

    src_db = src / "traceguard.db"
    src_artifacts = src / "artifacts"
    env_backup = os.environ.get("TG_ARTIFACT_DIR")
    os.environ["TG_ARTIFACT_DIR"] = str(src_artifacts)
    try:
        from traceguard_core.config import reset_settings_cache

        reset_settings_cache()
        expected = build_business_data(f"sqlite:///{src_db}", src_artifacts)

        # 备份：数据库文件 + 产物目录（SQLite 需先确保 WAL 已落盘；本脚本用默认 journal）
        shutil.copy2(src_db, backup / "traceguard.db")
        shutil.copytree(src_artifacts, backup / "artifacts", dirs_exist_ok=True)

        # 恢复：全新目录
        shutil.copy2(backup / "traceguard.db", restored / "traceguard.db")
        shutil.copytree(backup / "artifacts", restored / "artifacts", dirs_exist_ok=True)

        os.environ["TG_ARTIFACT_DIR"] = str(restored / "artifacts")
        reset_settings_cache()
        result = verify_restored(
            f"sqlite:///{restored / 'traceguard.db'}", restored / "artifacts", expected
        )
        return {
            "kind": "sqlite",
            "backup_method": "file copy (db + artifacts)",
            "restore_target": str(restored),
            "expected": {
                k: expected[k] for k in ("testcase_id", "run_id", "artifact_sha256", "evidence_ids")
            },
            **result,
        }
    finally:
        if env_backup is None:
            os.environ.pop("TG_ARTIFACT_DIR", None)
        else:
            os.environ["TG_ARTIFACT_DIR"] = env_backup
        from traceguard_core.config import reset_settings_cache

        reset_settings_cache()


# ---------------------------------------------------------------------- PG 用例
def pg_case(work: Path, admin_url: str) -> dict[str, Any]:
    pg_dump = find_pg_tool("pg_dump")
    pg_restore = find_pg_tool("pg_restore")
    psql = find_pg_tool("psql")
    if not (pg_dump and pg_restore and psql):
        return {
            "kind": "postgres16",
            "blocked": True,
            "reason": "pg_dump / pg_restore / psql 不可用（安装 postgresql-client-16）",
            "all_passed": False,
        }

    db_name = f"traceguard_run017_bak_{uuid.uuid4().hex[:6]}"
    restore_name = f"{db_name}_restored"
    artifacts = work / "pg-artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    env_backup = os.environ.get("TG_ARTIFACT_DIR")
    os.environ["TG_ARTIFACT_DIR"] = str(artifacts)
    try:
        from traceguard_core.config import reset_settings_cache

        reset_settings_cache()
        server_url = admin_url.rstrip("/").rsplit("/", 1)[0]
        admin_dsn = server_url + "/postgres"
        with __import__("psycopg").connect(admin_dsn, autocommit=True) as conn:
            conn.execute(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)')
            conn.execute(f'CREATE DATABASE "{db_name}"')
            conn.execute(f'DROP DATABASE IF EXISTS "{restore_name}" WITH (FORCE)')

        src_url = f"{server_url}/{db_name}"
        expected = build_business_data(
            src_url.replace("postgresql://", "postgresql+psycopg://"), artifacts
        )

        dump_file = work / "pg.dump"
        subprocess.run(  # noqa: S603 - 受控本地命令
            [pg_dump, "--format=custom", "--no-owner", "--file", str(dump_file), src_url],
            check=True,
            capture_output=True,
            text=True,
        )
        with __import__("psycopg").connect(admin_dsn, autocommit=True) as conn:
            conn.execute(f'CREATE DATABASE "{restore_name}"')
        restore_url = f"{server_url}/{restore_name}"
        subprocess.run(  # noqa: S603
            [pg_restore, "--no-owner", "--dbname", restore_url, str(dump_file)],
            check=True,
            capture_output=True,
            text=True,
        )

        result = verify_restored(
            restore_url.replace("postgresql://", "postgresql+psycopg://"), artifacts, expected
        )
        result.update(
            {
                "kind": "postgres16",
                "backup_method": "pg_dump --format=custom",
                "restore_method": "pg_restore --no-owner",
                "source_db": db_name,
                "restore_db": restore_name,
                "dump_bytes": dump_file.stat().st_size,
                "expected_ids": {
                    "testcase_id": expected["testcase_id"],
                    "run_id": expected["run_id"],
                    "artifact_sha256": expected["artifact_sha256"],
                },
            }
        )
        return result
    finally:
        try:
            with __import__("psycopg").connect(admin_dsn, autocommit=True) as conn:
                conn.execute(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)')
                conn.execute(f'DROP DATABASE IF EXISTS "{restore_name}" WITH (FORCE)')
        except Exception:  # noqa: BLE001 - 清理失败不掩盖主结论
            pass
        if env_backup is None:
            os.environ.pop("TG_ARTIFACT_DIR", None)
        else:
            os.environ["TG_ARTIFACT_DIR"] = env_backup
        from traceguard_core.config import reset_settings_cache

        reset_settings_cache()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="docs/assets/run017/wp5/backup-restore")
    parser.add_argument("--pg-admin-url", default="")
    parser.add_argument("--workdir", default="")
    args = parser.parse_args()

    out_dir = ROOT / args.out
    out_dir.mkdir(parents=True, exist_ok=True)
    work = Path(args.workdir) if args.workdir else Path(tempfile.mkdtemp(prefix="tg-bak-"))
    work.mkdir(parents=True, exist_ok=True)

    started = _now()
    cases: list[dict[str, Any]] = []
    sqlite_result = sqlite_case(work / "sqlite")
    cases.append(sqlite_result)
    print(json.dumps({"sqlite": sqlite_result}, ensure_ascii=False)[:600], flush=True)

    if args.pg_admin_url:
        pg_result = pg_case(work / "pg", args.pg_admin_url)
        cases.append(pg_result)
        print(json.dumps({"postgres16": pg_result}, ensure_ascii=False)[:600], flush=True)

    blocked = [c for c in cases if c.get("blocked")]
    report = {
        "run": "RUN-017 WP5",
        "started_at": started,
        "finished_at": _now(),
        "workdir": str(work),
        "cases": cases,
        "all_passed": all(c.get("all_passed") for c in cases) and not blocked,
        "blocked": blocked,
        "note": "所有用例只使用临时目录与临时数据库；恢复后执行一次真实写入以证明库可用",
    }
    (out_dir / "backup-restore.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if blocked:
        return 3
    return 0 if report["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
