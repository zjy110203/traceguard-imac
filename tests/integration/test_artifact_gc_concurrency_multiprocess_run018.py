"""RUN-018 WP2 §2.5：Artifact GC 真实并发测试（多 OS 进程 + 独立数据库连接）。

与旧版 test_artifact_gc_concurrency_run018.py（线程 + put_done.wait 串行化）不同，
本文件使用**独立 OS 进程**（spawn 子进程执行器 scripts/artifact_gc_concurrency_worker.py），
文件轮询同步，真正交错执行。覆盖指令要求的 8 类场景：

1. GC 与 put 并发（场景 A：文件与登记）
2. GC 与 Evidence 引用并发（场景 B）
3. GC 与 Run 报告引用并发（场景 B）
4. 两个 GC 进程同时处理同一 Artifact
5. 进程在 GC 中途被 SIGKILL（journal 意图留在中途）
6. 恢复（recover_journal）后再次执行 GC
7. 同 SHA 并发写入
8. 引用创建和删除竞争（多轮压力）

PG16 变体：`TG_TEST_POSTGRES_URL` 或本机 127.0.0.1:55432 可达时执行场景
1/2/4/7/8 的 PostgreSQL 版本。**不能把 PG 专项 SKIP 当作 PG16 验收成功**
（状态口径见 RUN-018-WP4-ARTIFACT-GC.md）。

验收不变量（每场景后断言）：
- 零 dangling reference（Evidence/Run 引用的产物必有登记）；
- 零 ArtifactRow 指向缺失文件（verify_consistency）；
- 被引用/登记的产物不被 GC 误删；
- GC 动作幂等可恢复（journal 可解释所有中间态）。
"""

from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "artifact_gc_concurrency_worker.py"

PG_ADMIN = os.environ.get("TG_RUN015_PG_ADMIN", "postgresql://traceguard@127.0.0.1:55432/postgres")
PG_NAME = f"tg_gc_mp_{int(time.time()) % 1000000}_{os.getpid()}"
PG_URL = os.environ.get("TG_TEST_POSTGRES_URL") or (
    f"postgresql+psycopg://traceguard@127.0.0.1:55432/{PG_NAME}"
)


def _can_connect_pg() -> bool:
    try:
        import psycopg

        conn = psycopg.connect(PG_ADMIN, connect_timeout=3)
        conn.close()
        return True
    except Exception:  # noqa: BLE001
        return False


PG_AVAILABLE = pytest.mark.skipif(
    not _can_connect_pg(), reason="PostgreSQL 16 不可达（需 TG_TEST_POSTGRES_URL 或本机 55432）"
)


# --------------------------------------------------------------------- 基础设施
class GCEnv:
    """一个隔离的共享环境：SQLite 文件库 + Artifact 根目录 + 子进程执行器。"""

    def __init__(self, tmp_path: Path, db_url: str, *, pg: bool = False) -> None:
        self.tmp = tmp_path
        self.db_url = db_url
        self.root = tmp_path / "artifacts"
        self.root.mkdir(parents=True, exist_ok=True)
        self.pg = pg

    def run_worker(self, *args: str, timeout: float = 120) -> dict[str, Any]:
        out = self.tmp / f"out-{time.time_ns()}.json"
        proc = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--db",
                self.db_url,
                "--root",
                str(self.root),
                "--out",
                str(out),
                *args,
            ],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if not out.exists():
            raise AssertionError(f"worker 崩溃（无输出）: stderr={proc.stderr[-2000:]}")
        return json.loads(out.read_text(encoding="utf-8"))

    def spawn(self, *args: str) -> subprocess.Popen[bytes]:
        out = self.tmp / f"out-{time.time_ns()}.json"
        proc = subprocess.Popen(
            [
                sys.executable,
                str(SCRIPT),
                "--db",
                self.db_url,
                "--root",
                str(self.root),
                "--out",
                str(out),
                *args,
            ],
            cwd=str(ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        proc._tg_out = out  # type: ignore[attr-defined]
        return proc

    def spawn_result(self, proc: subprocess.Popen[bytes], timeout: float = 120) -> dict[str, Any]:
        stdout, _ = proc.communicate(timeout=timeout)
        out = Path(str(proc._tg_out))
        if not out.exists():
            raise AssertionError(f"worker 崩溃（无输出）: stdout={str(stdout)[-2000:]}")
        return json.loads(out.read_text(encoding="utf-8"))

    def journal_path(self) -> Path:
        return self.root / "_gc-journal.jsonl"

    def open_intents(self) -> list[dict[str, Any]]:
        f = self.journal_path()
        if not f.is_file():
            return []
        intents: dict[str, dict[str, Any]] = {}
        for line in f.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            jid = str(rec.get("jid") or "")
            if rec.get("state") == "intent":
                intents[jid] = rec
            elif rec.get("state") in ("done", "resolved"):
                intents.pop(jid, None)
        return list(intents.values())

    # ------------------------------------------------- 直接构造孤儿产物（绕过 put）
    def make_orphan_row(self, data: bytes, *, created_at: str = "2020-01-01T00:00:00+00:00") -> str:
        digest = hashlib.sha256(data).hexdigest()
        artifact_id = f"art_{digest[:20]}"
        path = self.root / digest[:2] / digest
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        os.utime(path, (time.time() - 86400 * 30, time.time() - 86400 * 30))
        from traceguard_persistence import Database
        from traceguard_persistence.models import ArtifactRow

        db = Database(self.db_url)
        try:
            with db.session() as s:
                s.add(
                    ArtifactRow(
                        artifact_id=artifact_id,
                        workspace_id="default",
                        sha256=digest,
                        size=len(data),
                        media_type="application/octet-stream",
                        path=str(path),
                        backend="local",
                        created_at=created_at,
                    )
                )
        finally:
            db.dispose()
        return artifact_id

    def make_orphan_file(self, data: bytes) -> str:
        digest = hashlib.sha256(data).hexdigest()
        path = self.root / digest[:2] / digest
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        os.utime(path, (time.time() - 86400 * 30, time.time() - 86400 * 30))
        return digest

    # ------------------------------------------------------------ 一致性断言工具
    def assert_no_dangling_refs(self) -> None:
        from traceguard_persistence import Database
        from traceguard_persistence.models import ArtifactRow, EvidenceRow, RunRow
        from traceguard_persistence.stores import _ARTIFACT_REF_RE

        db = Database(self.db_url)
        try:
            with db.session() as s:
                rows = {
                    r[0]
                    for r in s.execute(
                        __import__("sqlalchemy").select(ArtifactRow.artifact_id)
                    ).all()
                }
                evidences = s.execute(
                    __import__("sqlalchemy").select(
                        EvidenceRow.evidence_id, EvidenceRow.attachment_ref, EvidenceRow.doc
                    )
                ).all()
                for eid, ref, doc in evidences:
                    refs = set(_ARTIFACT_REF_RE.findall(json.dumps(doc or {}, ensure_ascii=False)))
                    if ref:
                        refs.add(ref)
                    missing = refs - rows
                    assert not missing, f"dangling evidence refs {missing} (evidence={eid})"
                runs = s.execute(
                    __import__("sqlalchemy").select(RunRow.run_id, RunRow.report_json)
                ).all()
                for rid, report in runs:
                    refs = set(
                        _ARTIFACT_REF_RE.findall(json.dumps(report or {}, ensure_ascii=False))
                    )
                    missing = refs - rows
                    assert not missing, f"dangling run-report refs {missing} (run={rid})"
        finally:
            db.dispose()

    def assert_rows_files_consistent(self) -> None:
        from traceguard_persistence import Database
        from traceguard_persistence.stores import ArtifactStore

        db = Database(self.db_url)
        try:
            store = ArtifactStore(db, root=str(self.root))
            report = store.verify_consistency()
            assert report["consistent"], (
                f"登记/文件不一致: missing={report['missing'][:3]} mismatch={report['hash_mismatch'][:3]}"
            )
        finally:
            db.dispose()


def _setup_sqlite(tmp_path: Path) -> GCEnv:
    """先建 schema（父进程内完成，避免子进程建表竞争）。"""
    from traceguard_persistence import Database

    db_file = tmp_path / "gc.db"
    db = Database(f"sqlite:///{db_file}")
    db.create_schema()
    db.dispose()
    return GCEnv(tmp_path, f"sqlite:///{db_file}")


def _setup_pg(tmp_path: Path) -> GCEnv:
    import psycopg
    from traceguard_persistence import Database

    admin = psycopg.connect(PG_ADMIN, connect_timeout=5)
    admin.autocommit = True
    admin.execute(f'DROP DATABASE IF EXISTS "{PG_NAME}"')
    admin.execute(f'CREATE DATABASE "{PG_NAME}"')
    admin.close()
    db = Database(PG_URL)
    db.create_schema()
    # TG_TEST_POSTGRES_URL 指向共享库（CI 口径）时，清理上一测试残留，
    # 保证本测试的并发场景从干净状态开始（自有 PG_NAME 时本就是全新库）。
    from sqlalchemy import inspect as _inspect
    from sqlalchemy import text as _text

    existing = set(_inspect(db.engine).get_table_names())
    with db.session() as s:
        for table in (
            "tg_artifact",
            "tg_evidence",
            "tg_run",
            "tg_doc",
            "tg_event",
            "tg_worker_heartbeat",
            "tg_alert",
        ):
            if table in existing:
                s.execute(_text(f"DELETE FROM {table}"))
    db.dispose()
    return GCEnv(tmp_path, PG_URL, pg=True)


# ------------------------------------------------------------------- 场景 1/7/8
def _scenario_put_vs_gc(env: GCEnv, data_text: str) -> None:
    """场景 1：GC（隔离孤儿文件）与 put（同 SHA 写入+登记）跨进程并发。

    不变量：结束时 ArtifactRow 存在且其路径上的文件真实存在。
    两种合法顺序：GC 先（隔离后 put 重写并登记）/ put 先（GC 发现登记跳过）。
    """
    digest = hashlib.sha256(data_text.encode()).hexdigest()
    env.make_orphan_file(data_text.encode())  # 未登记孤儿文件，mtime 旧 → GC 候选
    gc = env.spawn("--mode", "gc", "--retention-days", "0")
    put = env.spawn("--mode", "put", "--data-text", data_text)
    gc_res = env.spawn_result(gc)
    put_res = env.spawn_result(put)
    assert not put_res.get("error"), put_res
    assert not gc_res.get("error"), gc_res
    env.assert_rows_files_consistent()
    env.assert_no_dangling_refs()
    # put 的登记必须存在
    assert put_res["artifact_id"] == f"art_{digest[:20]}"
    from traceguard_persistence import Database
    from traceguard_persistence.models import ArtifactRow

    db = Database(env.db_url)
    try:
        with db.session() as s:
            row = s.get(ArtifactRow, put_res["artifact_id"])
            assert row is not None, "put 的登记不应丢失"
            assert Path(row.path).is_file(), "登记路径上的文件必须真实存在"
    finally:
        db.dispose()


def _scenario_same_sha_concurrent_puts(env: GCEnv, data_text: str) -> None:
    """场景 7：同 SHA 并发写入（4 个进程同时 put 相同内容）。"""
    digest = hashlib.sha256(data_text.encode()).hexdigest()
    procs = [env.spawn("--mode", "put", "--data-text", data_text) for _ in range(4)]
    results = [env.spawn_result(p) for p in procs]
    for r in results:
        assert not r.get("error"), r
        assert r["sha256"] == digest
    from traceguard_persistence import Database
    from traceguard_persistence.models import ArtifactRow

    db = Database(env.db_url)
    try:
        with db.session() as s:
            import sqlalchemy as sa

            count = len(
                s.scalars(
                    sa.select(ArtifactRow.artifact_id).where(ArtifactRow.sha256 == digest)
                ).all()
            )
            assert count == 1, f"同 SHA 只应有一条登记，实际 {count}"
            row = s.get(ArtifactRow, f"art_{digest[:20]}")
            assert Path(row.path).is_file()
    finally:
        db.dispose()
    env.assert_rows_files_consistent()


def _scenario_ref_vs_gc(env: GCEnv, data_text: str, ref_mode: str) -> dict[str, Any]:
    """场景 2/3：引用创建（Evidence / Run 报告）与 GC 删除跨进程竞争。

    合法结果二选一：
    a) 引用先落库 → GC 跳过删除（行保留 + 引用完整）；
    b) GC 先删除 → 引用创建被拒绝（ARTIFACT_COLLECTED），无 dangling。
    """
    artifact_id = env.make_orphan_row(data_text.encode())
    ref = env.spawn("--mode", ref_mode, "--artifact-id", artifact_id)
    gc = env.spawn("--mode", "gc", "--retention-days", "0")
    ref_res = env.spawn_result(ref)
    gc_res = env.spawn_result(gc)
    from traceguard_persistence import Database
    from traceguard_persistence.models import ArtifactRow

    db = Database(env.db_url)
    try:
        with db.session() as s:
            row = s.get(ArtifactRow, artifact_id)
            if row is not None:
                # 结果 a：行保留 → 引用必须完整存在
                assert not ref_res.get("error"), ref_res
                actions = gc_res.get("actions", [])
                assert any(
                    a.get("op") == "delete_row" and a.get("result") == "skipped_referenced"
                    for a in actions
                ) or not any(a.get("op") == "delete_row" for a in actions), gc_res
                deleted = [
                    a
                    for a in actions
                    if a.get("op") == "delete_row" and a.get("artifact_id") == artifact_id
                ]
                assert all(a.get("result") != "deleted" for a in deleted), gc_res
                outcome = {"kept": True}
            else:
                # 结果 b：行已删 → 引用创建必须被拒绝（否则就是 dangling）
                assert ref_res.get("error") and "ARTIFACT_COLLECTED" in str(ref_res["error"]), (
                    f"行已删除但引用创建未被拒绝（dangling!）: {ref_res}"
                )
                outcome = {"kept": False}
    finally:
        db.dispose()
    env.assert_no_dangling_refs()
    env.assert_rows_files_consistent()
    return outcome


def _scenario_gc_vs_gc(env: GCEnv, data_text: str) -> None:
    """场景 4：两个 GC 进程同时回收同一孤儿 Artifact。"""
    artifact_id = env.make_orphan_row(data_text.encode())
    p1 = env.spawn("--mode", "gc", "--retention-days", "0")
    p2 = env.spawn("--mode", "gc", "--retention-days", "0")
    r1 = env.spawn_result(p1)
    r2 = env.spawn_result(p2)
    deleted = 0
    for r in (r1, r2):
        assert not r.get("error"), r
        for a in r.get("actions", []):
            if a.get("op") == "delete_row" and a.get("artifact_id") == artifact_id:
                if a.get("result") == "deleted":
                    deleted += 1
                else:
                    assert a.get("result") in ("gone", "skipped_referenced"), a
    assert deleted == 1, f"同一 Artifact 应恰好被删除一次，实际 {deleted}: {r1} {r2}"
    from traceguard_persistence import Database
    from traceguard_persistence.models import ArtifactRow

    db = Database(env.db_url)
    try:
        with db.session() as s:
            assert s.get(ArtifactRow, artifact_id) is None
    finally:
        db.dispose()
    env.assert_rows_files_consistent()


def _scenario_kill_and_recover(env: GCEnv, count: int = 120) -> dict[str, Any]:
    """场景 5+6：GC 中途被 SIGKILL → recover_journal → 再次 GC → 一致性验收。"""
    artifacts = [env.make_orphan_row(f"kill-recover-{i}".encode()) for i in range(count)]
    files = [env.make_orphan_file(f"kill-recover-file-{i}".encode()) for i in range(20)]
    proc = env.spawn("--mode", "gc", "--retention-days", "0")
    # 轮询 journal：出现 intent 后立刻杀（真实的"GC 中途"）
    deadline = time.monotonic() + 30
    killed = False
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            break  # 太快跑完了（扩 count 重试由调用方处理）
        if env.open_intents():
            os.kill(proc.pid, signal.SIGKILL)
            killed = True
            break
        time.sleep(0.005)
    proc.communicate(timeout=30)
    assert killed, "未能中断 GC（子进程过早完成或 journal 未产出 intent）"

    # 恢复（先快照 kill 时刻未完结的 intent，再执行恢复并核对计数）
    intents_before = env.open_intents()
    rec = env.run_worker("--mode", "recover")
    recovery = rec["recovery"]
    resolved_total = recovery["recovered"] + recovery["pending"] + recovery["abandoned"]
    assert resolved_total == len(intents_before), (
        f"journal 恢复计数不一致: resolved={resolved_total} intents_before={intents_before}"
    )

    # 再次 GC（apply）→ 必须成功且不误删
    final = env.run_worker("--mode", "gc", "--retention-days", "0")
    assert not final.get("error"), final

    env.assert_rows_files_consistent()
    env.assert_no_dangling_refs()
    # 所有未回收的孤儿（pending/abandoned）允许留给下次 GC；登记一致性必须成立
    return {
        "recovery": recovery,
        "final_scan": final.get("scan"),
        "artifacts": len(artifacts),
        "files": len(files),
    }


# ---------------------------------------------------------------------- SQLite
def test_sqlite_gc_vs_put(tmp_path: Path) -> None:
    env = _setup_sqlite(tmp_path)
    for i in range(5):  # 多轮，覆盖两种锁序
        _scenario_put_vs_gc(env, f"put-vs-gc-{int(time.time_ns())}-{i}")


def test_sqlite_same_sha_concurrent_puts(tmp_path: Path) -> None:
    env = _setup_sqlite(tmp_path)
    _scenario_same_sha_concurrent_puts(env, f"same-sha-{time.time_ns()}")


def test_sqlite_evidence_ref_vs_gc(tmp_path: Path) -> None:
    env = _setup_sqlite(tmp_path)
    _scenario_ref_vs_gc(env, f"ev-ref-vs-gc-{time.time_ns()}", "evidence-ref")


def test_sqlite_report_ref_vs_gc(tmp_path: Path) -> None:
    env = _setup_sqlite(tmp_path)
    _scenario_ref_vs_gc(env, f"report-ref-vs-gc-{time.time_ns()}", "report-ref")


def test_sqlite_gc_vs_gc_same_artifact(tmp_path: Path) -> None:
    env = _setup_sqlite(tmp_path)
    _scenario_gc_vs_gc(env, f"gc-vs-gc-{time.time_ns()}")


def test_sqlite_ref_delete_stress_8_rounds(tmp_path: Path) -> None:
    """场景 8：引用创建与删除竞争——多轮压力。"""
    env = _setup_sqlite(tmp_path)
    kept = 0
    for i in range(8):
        mode = "evidence-ref" if i % 2 == 0 else "report-ref"
        outcome = _scenario_ref_vs_gc(env, f"stress-{time.time_ns()}-{i}", mode)
        kept += int(outcome["kept"])
    assert kept >= 1, "8 轮竞争全部删除说明调度过于偏斜，无法证明交错"


def test_sqlite_kill_mid_gc_and_recover(tmp_path: Path) -> None:
    env = _setup_sqlite(tmp_path)
    _scenario_kill_and_recover(env, count=120)


# -------------------------------------------------------------------- PG16
@PG_AVAILABLE
def test_pg16_gc_vs_put(tmp_path: Path) -> None:
    env = _setup_pg(tmp_path)
    for i in range(5):
        _scenario_put_vs_gc(env, f"pg-put-vs-gc-{int(time.time_ns())}-{i}")


@PG_AVAILABLE
def test_pg16_same_sha_concurrent_puts(tmp_path: Path) -> None:
    env = _setup_pg(tmp_path)
    _scenario_same_sha_concurrent_puts(env, f"pg-same-sha-{time.time_ns()}")


@PG_AVAILABLE
def test_pg16_evidence_ref_vs_gc(tmp_path: Path) -> None:
    env = _setup_pg(tmp_path)
    _scenario_ref_vs_gc(env, f"pg-ev-ref-{time.time_ns()}", "evidence-ref")


@PG_AVAILABLE
def test_pg16_gc_vs_gc_same_artifact(tmp_path: Path) -> None:
    env = _setup_pg(tmp_path)
    _scenario_gc_vs_gc(env, f"pg-gc-vs-gc-{time.time_ns()}")


@PG_AVAILABLE
def test_pg16_ref_delete_stress(tmp_path: Path) -> None:
    env = _setup_pg(tmp_path)
    for i in range(6):
        mode = "evidence-ref" if i % 2 == 0 else "report-ref"
        _scenario_ref_vs_gc(env, f"pg-stress-{time.time_ns()}-{i}", mode)


@PG_AVAILABLE
def test_pg16_kill_mid_gc_and_recover(tmp_path: Path) -> None:
    env = _setup_pg(tmp_path)
    _scenario_kill_and_recover(env, count=120)
