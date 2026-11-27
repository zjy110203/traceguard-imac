"""RUN-018 WP8/R1：Monitoring 与 Alert 的真实运行验收（真实 uvicorn API 进程）。

与 test_monitoring_api_run018.py（TestClient 进程内）不同，本文件启动**真实
uvicorn API 子进程** + 共享 SQLite 文件库，覆盖指令要求的真实运行场景：

1. API 重启：进程内请求计数器随重启清零（口径 `since` 更新），
   告警记录跨重启持久化（数据库为准，不随进程消失）；
2. Worker 停止：心跳停止 → WORKER_OFFLINE 告警 OPEN；心跳恢复 →
   引擎自动 RESOLVED（condition_cleared，告警恢复）；
3. Queue 积压：真实 QUEUED Run 行超过 provisional 阈值 → QUEUE_BACKLOG；
4. 数据源不可用不能当作恢复：worker 采集报错时，已有告警保持活跃；
   快照 TOCTOU：generated_at 之后创建的告警不被旧快照自动解决；
5. Artifact 缺失：登记在册但文件被删 → ARTIFACT_INTEGRITY OPEN；
6. GC 异常：GC 审计出现 error 记录 → GC_ANOMALY OPEN；
7. 告警详情带 ref_run_id / ref_worker_id（可跳转关联 Run / Worker）。

所有阈值保持 provisional 口径，不做 SLA 宣称。
"""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _get(url: str) -> tuple[int, dict]:
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:  # noqa: S310 - 本地受控
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        return exc.code, {}


def _wait_health(url: str, timeout: float = 40.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:  # noqa: S310
                if resp.status == 200:
                    return True
        except Exception:  # noqa: BLE001
            time.sleep(0.3)
    return False


class RealApi:
    def __init__(self, db_path: Path, work: Path) -> None:
        self.db_path = db_path
        self.work = work
        self.port = _free_port()
        self.proc: subprocess.Popen[bytes] | None = None

    def start(self) -> None:
        pythonpath = ":".join(
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
            )
        )
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": os.environ.get("HOME", str(self.work)),
            "TMPDIR": os.environ.get("TMPDIR", "/tmp"),
            "PYTHONPATH": pythonpath,
            "TG_DATABASE_URL": f"sqlite:///{self.db_path}",
            "TG_AUTH_ENABLED": "false",
            "TG_LLM_PROVIDER": "",
            "TG_ARTIFACT_DIR": str(self.work / "artifacts"),
        }
        self.proc = subprocess.Popen(  # noqa: S603 - 受控本地子进程
            [
                sys.executable,
                "-m",
                "uvicorn",
                "traceguard_api.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(self.port),
            ],
            cwd=str(ROOT),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        assert _wait_health(self.base + "/health"), "真实 API 未能启动"

    def stop(self, sig: int = signal.SIGTERM) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.send_signal(sig)
            try:
                self.proc.wait(timeout=15)
            except subprocess.TimeoutExpired:  # pragma: no cover
                self.proc.kill()
                self.proc.wait(timeout=10)

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def get(self, path: str) -> tuple[int, dict]:
        return _get(self.base + path)


@pytest.fixture()
def api(tmp_path):  # type: ignore[no-untyped-def]
    work = tmp_path / "mon-real"
    work.mkdir(parents=True, exist_ok=True)
    real = RealApi(work / "mon.db", work)
    real.start()
    yield real, work
    real.stop()


# --------------------------------------------------------------------- helpers
def _db(db_path: Path):
    from traceguard_persistence import Database

    db = Database(f"sqlite:///{db_path}")
    db.create_schema()
    return db


def _insert_queued_run(db, run_id: str, age_s: float = 7200.0) -> None:  # type: ignore[no-untyped-def]
    from traceguard_persistence.models import RunRow

    with db.session() as s:
        s.add(
            RunRow(
                run_id=run_id,
                task_id=f"task_{run_id}",
                workspace_id="default",
                testcase_version_id="tcase_mon@1",
                execution_status="QUEUED",
                created_at=time.time() - age_s,
            )
        )


def _heartbeat(db, worker_id: str, *, fresh: bool = True) -> None:  # type: ignore[no-untyped-def]
    from traceguard_persistence.models import WorkerHeartbeatRow
    from traceguard_persistence.monitoring import upsert_worker_heartbeat

    upsert_worker_heartbeat(db, worker_id=worker_id, mode="standalone", status="IDLE")
    if not fresh:
        with db.session() as s:
            row = s.get(WorkerHeartbeatRow, worker_id)
            row.last_heartbeat_at = time.time() - 3600.0


def _open_alerts(api: RealApi) -> list[dict[str, Any]]:
    _, alerts = api.get("/api/v1/monitoring/alerts?status=OPEN")
    return alerts if isinstance(alerts, list) else alerts.get("alerts", [])


def _all_alerts(api: RealApi) -> list[dict[str, Any]]:
    _, alerts = api.get("/api/v1/monitoring/alerts")
    return alerts if isinstance(alerts, list) else alerts.get("alerts", [])


# ------------------------------------------------------------------- 1. API 重启
def test_api_restart_resets_inproc_counters_and_keeps_alerts(api) -> None:  # type: ignore[no-untyped-def]
    real, work = api
    db_path = work / "mon.db"
    status, before = real.get("/api/v1/monitoring/metrics")
    assert status == 200
    assert before["api"]["health"] in ("ok", "degraded")
    # 真实请求产生进程内计数
    real.get("/api/v1/nonexistent-path-for-error")
    _, after_req = real.get("/api/v1/monitoring/metrics")
    assert after_req["api"]["requests_total"] >= 2
    assert after_req["api"]["error_count"] >= 1

    # 告警落库（共享数据库，跨进程持久）
    db = _db(db_path)
    from traceguard_persistence.models import AlertRow

    # 同时写入过期心跳：让 WORKER_OFFLINE 条件在重启后仍然成立（规则持续触发）
    _heartbeat(db, "w-restart", fresh=False)
    with db.session() as s:
        s.add(
            AlertRow(
                alert_id="alrt_restart_probe",
                fingerprint="WORKER_OFFLINE:w-restart",
                alert_type="WORKER_OFFLINE",
                severity="CRITICAL",
                status="OPEN",
                title="Worker 离线：w-restart",
                message="真实运行验收预置",
                created_at=time.time() - 120,
                last_seen=time.time() - 120,
            )
        )
    db.dispose()

    # 重启（SIGTERM 优雅退出 → 重新启动，同一数据库）
    real.stop()
    real.start()
    status, after = real.get("/api/v1/monitoring/metrics")
    assert status == 200
    # 进程内计数器随重启清零（口径如实声明 since）：本次 metrics 调用自身会计数 1，
    # 旧进程的 404 错误必须不再出现（error_count 归零证明是全新窗口）
    assert after["api"]["requests_total"] <= 2, (
        f"重启后进程内计数必须清零（仅当前请求），不得冒充全历史: {after['api']['requests_total']}"
    )
    assert after["api"]["error_count"] == 0, "旧进程的错误计数不得带入新窗口"
    assert after["api"]["since"] != before["api"]["since"], "计数窗口 since 必须更新"
    assert after["api"]["counting"]["note"], "必须带计数口径声明"
    # 告警以数据库为准，跨重启仍然 OPEN（不因进程重启而丢失/复活）
    status, alerts = real.get("/api/v1/monitoring/alerts")
    fps = {
        a["fingerprint"]: a
        for a in (alerts if isinstance(alerts, list) else alerts.get("alerts", []))
    }
    assert "WORKER_OFFLINE:w-restart" in fps
    assert fps["WORKER_OFFLINE:w-restart"]["status"] == "OPEN"


# ------------------------------------------------------- 2. Worker 停止与恢复
def test_worker_offline_then_recover_alert_lifecycle(api) -> None:  # type: ignore[no-untyped-def]
    real, work = api
    db = _db(work / "mon.db")
    _heartbeat(db, "w-live", fresh=True)
    db.dispose()

    _, metrics = real.get("/api/v1/monitoring/metrics")  # 触发评估
    assert not [a for a in _open_alerts(real) if a["fingerprint"] == "WORKER_OFFLINE:w-live"]

    # Worker 停止（心跳停止更新 → 心跳年龄超过 provisional 阈值）
    db = _db(work / "mon.db")
    _heartbeat(db, "w-live", fresh=False)
    db.dispose()
    _, metrics = real.get("/api/v1/monitoring/metrics")
    offline = [a for a in _open_alerts(real) if a["fingerprint"] == "WORKER_OFFLINE:w-live"]
    assert offline, "Worker 停止必须触发 WORKER_OFFLINE"
    assert offline[0]["ref_worker_id"] == "w-live", "告警必须可跳转关联 Worker"

    # Worker 恢复（心跳重新新鲜）→ 引擎自动 RESOLVED（condition_cleared）
    db = _db(work / "mon.db")
    _heartbeat(db, "w-live", fresh=True)
    db.dispose()
    real.get("/api/v1/monitoring/metrics")
    all_alerts = _all_alerts(real)
    resolved = [a for a in all_alerts if a["fingerprint"] == "WORKER_OFFLINE:w-live"]
    assert resolved and resolved[0]["status"] == "RESOLVED"
    assert resolved[0]["resolution_reason"] == "condition_cleared"
    assert resolved[0]["resolved_by"] == "system:alert-engine"


# ------------------------------------------------------------- 3. Queue 积压
def test_queue_backlog_real_rows(api) -> None:  # type: ignore[no-untyped-def]
    real, work = api
    db = _db(work / "mon.db")
    for i in range(25):  # provisional 阈值 20
        _insert_queued_run(db, f"run_backlog_{i}")
    db.dispose()
    _, metrics = real.get("/api/v1/monitoring/metrics")
    assert int(metrics["queue"]["queued"]) >= 25, metrics["queue"]
    backlog = [a for a in _open_alerts(real) if a["fingerprint"] == "QUEUE_BACKLOG"]
    assert backlog, "真实积压必须触发 QUEUE_BACKLOG"
    assert backlog[0]["detail"]["queued"] >= 25
    assert backlog[0]["detail"]["thresholds"]["status"] == "provisional"


# ------------------------------------- 4. 数据源不可用不能当作恢复 + TOCTOU
def test_unavailable_source_does_not_resolve_alerts(api) -> None:  # type: ignore[no-untyped-def]
    real, work = api
    db_path = work / "mon.db"
    db = _db(db_path)
    _heartbeat(db, "w-gone", fresh=False)
    db.dispose()
    real.get("/api/v1/monitoring/metrics")
    assert [a for a in _open_alerts(real) if a["fingerprint"] == "WORKER_OFFLINE:w-gone"]

    # 数据源报错的快照（worker 采集失败）：引擎不得把"未知"当"已恢复"
    from traceguard_persistence.alerts import AlertEngine

    engine = AlertEngine(_db(db_path))
    bad_snapshot = {
        "generated_at": time.time(),
        "worker": {"error": "collector failed (simulated)"},
        "database": {"ok": True},
    }
    result = engine.evaluate(bad_snapshot)
    still_open = [a for a in engine.list_alerts() if a["fingerprint"] == "WORKER_OFFLINE:w-gone"]
    assert still_open and still_open[0]["status"] == "OPEN", (
        f"数据源不可用时告警必须保持活跃: {result}"
    )

    # TOCTOU：快照 generated_at 之后新建的告警，不被这份旧快照自动解决
    from traceguard_persistence.models import AlertRow

    db2 = _db(db_path)
    with db2.session() as s:
        s.add(
            AlertRow(
                alert_id="alrt_toctou",
                fingerprint="QUEUE_BACKLOG",
                alert_type="QUEUE_BACKLOG",
                severity="WARNING",
                status="OPEN",
                title="快照之后新建",
                message="TOCTOU 防护验证",
                created_at=time.time() + 5,  # 晚于快照 generated_at
                last_seen=time.time() + 5,
            )
        )
    db2.dispose()
    empty_snapshot = {"generated_at": time.time(), "database": {"ok": True}}
    engine.evaluate(empty_snapshot)
    rows = {a["fingerprint"]: a for a in engine.list_alerts()}
    assert rows["QUEUE_BACKLOG"]["status"] == "OPEN", "快照之后创建的告警不得被旧快照解决"


# ------------------------------------------------- 5/6. Artifact 缺失与 GC 异常
def test_artifact_missing_and_gc_anomaly_alerts(api) -> None:  # type: ignore[no-untyped-def]
    real, work = api
    db_path = work / "mon.db"
    art_root = work / "artifacts"
    # 真实登记一个产物然后删掉落盘文件
    from traceguard_persistence.stores import ArtifactStore

    db = _db(db_path)
    store = ArtifactStore(db, root=str(art_root))
    rec = store.put(b"monitoring-real-run-artifact", workspace_id="default")
    # put 返回的 path 形如 artifacts/<2位>/<sha>；真实落盘位置是 root/<2位>/<sha>
    target = art_root / rec["sha256"][:2] / rec["sha256"]
    assert target.is_file()
    target.unlink()
    db.dispose()

    real.get("/api/v1/monitoring/metrics")
    integrity = [a for a in _open_alerts(real) if a["alert_type"] == "ARTIFACT_INTEGRITY"]
    assert integrity, "登记在册但文件缺失必须触发 ARTIFACT_INTEGRITY"

    # GC 审计出现 error 记录 → GC_ANOMALY
    audit = art_root / "_gc-audit.jsonl"
    audit.parent.mkdir(parents=True, exist_ok=True)
    audit.write_text(
        json.dumps(
            {
                "at": "2026-11-27T00:00:00Z",
                "actor": "artifact-gc",
                "op": "quarantine_file",
                "sha256": "deadbeef",
                "result": "error",
                "error": "OSError: permission denied (simulated)",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    real.get("/api/v1/monitoring/metrics")
    gc = [a for a in _open_alerts(real) if a["alert_type"] == "GC_ANOMALY"]
    assert gc, "GC 审计 error 记录必须触发 GC_ANOMALY"
    assert gc[0]["detail"]["gc"]["errors"] >= 1
