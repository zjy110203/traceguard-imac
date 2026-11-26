"""RUN-018 WP8：告警引擎单元测试（规则评估 + 状态机 + 持久化）。

覆盖指令 §8.2：
- 七类告警规则（Worker 离线 / 队列积压 / Run 失败率 / 数据库不可达 /
  Artifact 缺失或损坏 / GC 异常 / 长时间任务超时）；
- 状态机 OPEN → ACKNOWLEDGED → RESOLVED（含处理主体/处理时间/解决原因）；
- 同一持续条件去重（occurrences 累加），条件消失自动 RESOLVED；
- 阈值声明为 provisional，不得冒充 SLA。
"""

from __future__ import annotations

import time

import pytest

from traceguard_persistence.alerts import (
    ALERT_TRANSITIONS,
    ALERT_TYPES,
    AlertEngine,
    AlertStateError,
)
from traceguard_persistence.monitoring import (
    PROVISIONAL_THRESHOLDS,
    MetricsCollector,
    effective_thresholds,
    reset_process_metrics,
    upsert_worker_heartbeat,
)
from traceguard_persistence.models import RunRow, WorkerHeartbeatRow
from traceguard_persistence import Database


@pytest.fixture
def db():
    reset_process_metrics()
    database = Database("sqlite:///:memory:")
    database.create_schema()
    yield database
    reset_process_metrics()


def _seed_stale_worker(db: Database, worker_id: str = "w-stale", age_s: float = 3600.0) -> None:
    upsert_worker_heartbeat(db, worker_id=worker_id, mode="standalone", status="IDLE")
    with db.session() as s:
        row = s.get(WorkerHeartbeatRow, worker_id)
        row.last_heartbeat_at = time.time() - age_s


def _seed_queued_runs(db: Database, n: int, age_s: float = 2000.0) -> None:
    now = time.time()
    with db.session() as s:
        for i in range(n):
            s.add(
                RunRow(
                    run_id=f"run-q{i}",
                    task_id=f"task-q{i}",
                    workspace_id="default",
                    attempt=1,
                    testcase_version_id="tcase@1",
                    execution_status="QUEUED",
                    test_verdict="NOT_EVALUATED",
                    fencing_token=0,
                    created_at=now - age_s,
                )
            )


def _snapshot(db: Database) -> dict:
    return MetricsCollector(db, None).collect()


# ------------------------------------------------------------------ 状态机
def test_alert_state_machine_transitions() -> None:
    assert "ACKNOWLEDGED" in ALERT_TRANSITIONS["OPEN"]
    assert "RESOLVED" in ALERT_TRANSITIONS["OPEN"]
    assert "RESOLVED" in ALERT_TRANSITIONS["ACKNOWLEDGED"]
    assert ALERT_TRANSITIONS["RESOLVED"] == set()


def test_acknowledge_records_actor_and_time(db) -> None:  # type: ignore[no-untyped-def]
    engine = AlertEngine(db)
    _seed_stale_worker(db)
    engine.evaluate(_snapshot(db))
    alert = engine.list_alerts(status="OPEN")[0]
    out = engine.acknowledge(alert["alert_id"], "user:ops1", now=123.0)
    assert out["status"] == "ACKNOWLEDGED"
    assert out["acknowledged_by"] == "user:ops1"
    assert out["acknowledged_at"] == 123.0


def test_resolve_requires_reason_and_records_it(db) -> None:  # type: ignore[no-untyped-def]
    engine = AlertEngine(db)
    _seed_stale_worker(db)
    engine.evaluate(_snapshot(db))
    alert = engine.list_alerts(status="OPEN")[0]
    with pytest.raises(ValueError, match="RESOLUTION_REASON_REQUIRED"):
        engine.resolve(alert["alert_id"], "user:ops1", "   ")
    out = engine.resolve(alert["alert_id"], "user:ops1", "已重启 Worker", now=456.0)
    assert out["status"] == "RESOLVED"
    assert out["resolution_reason"] == "已重启 Worker"
    assert out["resolved_by"] == "user:ops1"
    assert out["resolved_at"] == 456.0


def test_illegal_transitions_rejected(db) -> None:  # type: ignore[no-untyped-def]
    engine = AlertEngine(db)
    _seed_stale_worker(db)
    engine.evaluate(_snapshot(db))
    alert = engine.list_alerts(status="OPEN")[0]
    engine.resolve(alert["alert_id"], "user:ops1", "done")
    with pytest.raises(AlertStateError):
        engine.acknowledge(alert["alert_id"], "user:ops1")
    with pytest.raises(AlertStateError):
        engine.resolve(alert["alert_id"], "user:ops1", "again")


# ------------------------------------------------------------------ 规则
def test_worker_offline_rule(db) -> None:  # type: ignore[no-untyped-def]
    engine = AlertEngine(db)
    _seed_stale_worker(db, "w-old")
    upsert_worker_heartbeat(db, worker_id="w-fresh", status="IDLE")
    result = engine.evaluate(_snapshot(db))
    assert "WORKER_OFFLINE:w-old" in result["findings"]
    assert "WORKER_OFFLINE:w-fresh" not in result["findings"]
    alert = [a for a in engine.list_alerts() if a["alert_type"] == "WORKER_OFFLINE"][0]
    assert alert["ref_worker_id"] == "w-old"
    assert alert["severity"] in ("WARNING", "CRITICAL")
    assert alert["detail"]["threshold_status"] == "provisional"


def test_queue_backlog_rule_count_and_wait(db) -> None:  # type: ignore[no-untyped-def]
    engine = AlertEngine(db)
    _seed_queued_runs(db, n=int(PROVISIONAL_THRESHOLDS["queue_backlog_count"]) + 5, age_s=10.0)
    result = engine.evaluate(_snapshot(db))
    assert "QUEUE_BACKLOG" in result["findings"]
    alert = [a for a in engine.list_alerts() if a["alert_type"] == "QUEUE_BACKLOG"][0]
    assert alert["detail"]["queued"] > int(PROVISIONAL_THRESHOLDS["queue_backlog_count"])
    assert alert["detail"]["thresholds"]["status"] == "provisional"


def test_queue_backlog_rule_oldest_wait(db) -> None:  # type: ignore[no-untyped-def]
    engine = AlertEngine(db)
    # 数量低于阈值，但最老等待超过 provisional 阈值
    _seed_queued_runs(db, n=1, age_s=float(PROVISIONAL_THRESHOLDS["queue_oldest_wait_seconds"]) + 60)
    result = engine.evaluate(_snapshot(db))
    assert "QUEUE_BACKLOG" in result["findings"]


def test_run_timeout_rule(db) -> None:  # type: ignore[no-untyped-def]
    engine = AlertEngine(db)
    _seed_queued_runs(db, n=1, age_s=float(PROVISIONAL_THRESHOLDS["run_overrun_seconds"]) + 60)
    result = engine.evaluate(_snapshot(db))
    assert any(f.startswith("RUN_TIMEOUT:") for f in result["findings"])
    alert = [a for a in engine.list_alerts() if a["alert_type"] == "RUN_TIMEOUT"][0]
    assert alert["ref_run_id"] == "run-q0"


def test_run_failure_rate_rule(db) -> None:  # type: ignore[no-untyped-def]
    engine = AlertEngine(db)
    now = time.time()
    min_samples = int(PROVISIONAL_THRESHOLDS["run_failure_rate_min_samples"])
    with db.session() as s:
        for i in range(min_samples + 3):
            failed = i < min_samples  # 失败率远超阈值
            s.add(
                RunRow(
                    run_id=f"run-f{i}",
                    task_id=f"task-f{i}",
                    workspace_id="default",
                    attempt=1,
                    testcase_version_id="tcase@1",
                    execution_status="FAILED" if failed else "COMPLETED",
                    test_verdict="FAILED" if failed else "PASSED",
                    fencing_token=0,
                    created_at=now - 3600,
                    started_at=now - 3500,
                    ended_at=now - 3400,
                )
            )
    result = engine.evaluate(_snapshot(db))
    assert "RUN_FAILURE_RATE" in result["findings"]
    alert = [a for a in engine.list_alerts() if a["alert_type"] == "RUN_FAILURE_RATE"][0]
    assert alert["detail"]["failure_rate"] >= float(PROVISIONAL_THRESHOLDS["run_failure_rate"])
    assert alert["detail"]["threshold_status"] == "provisional"


def test_run_failure_rate_min_samples_guard(db) -> None:  # type: ignore[no-untyped-def]
    engine = AlertEngine(db)
    now = time.time()
    # 样本不足时不触发（哪怕 100% 失败）
    with db.session() as s:
        s.add(
            RunRow(
                run_id="run-few",
                task_id="task-few",
                workspace_id="default",
                attempt=1,
                testcase_version_id="tcase@1",
                execution_status="FAILED",
                test_verdict="FAILED",
                fencing_token=0,
                created_at=now - 3600,
                started_at=now - 3500,
                ended_at=now - 3400,
            )
        )
    result = engine.evaluate(_snapshot(db))
    assert "RUN_FAILURE_RATE" not in result["findings"]


def test_db_unreachable_rule_from_snapshot(db) -> None:  # type: ignore[no-untyped-def]
    engine = AlertEngine(db)
    snap = _snapshot(db)
    snap["database"] = {"ok": False, "error": "OperationalError: db is locked"}
    result = engine.evaluate(snap)
    assert "DB_UNREACHABLE" in result["findings"]
    alert = [a for a in engine.list_alerts() if a["alert_type"] == "DB_UNREACHABLE"][0]
    assert alert["severity"] == "CRITICAL"


def test_artifact_integrity_rules_from_snapshot(db) -> None:  # type: ignore[no-untyped-def]
    engine = AlertEngine(db)
    snap = _snapshot(db)
    snap["artifact"] = {
        "missing_files": [{"artifact_id": "art_abc", "path": "/x/y"}],
        "hash_mismatch": [{"artifact_id": "art_def", "expected": "aa", "actual": "bb"}],
        "gc": {"errors": 2},
        "hash_check": {"checked": 2},
    }
    result = engine.evaluate(snap)
    assert "ARTIFACT_INTEGRITY:missing" in result["findings"]
    assert "ARTIFACT_INTEGRITY:hash" in result["findings"]
    assert "GC_ANOMALY" in result["findings"]
    types = {a["alert_type"] for a in engine.list_alerts()}
    assert {"ARTIFACT_INTEGRITY", "GC_ANOMALY"} <= types


def test_all_required_alert_types_coverable(db) -> None:  # type: ignore[no-untyped-def]
    """§8.2 要求的七类告警都能由引擎产生。"""
    engine = AlertEngine(db)
    _seed_stale_worker(db)
    _seed_queued_runs(db, n=int(PROVISIONAL_THRESHOLDS["queue_backlog_count"]) + 5, age_s=2000.0)
    now = time.time()
    with db.session() as s:
        for i in range(int(PROVISIONAL_THRESHOLDS["run_failure_rate_min_samples"]) + 3):
            failed = i < int(PROVISIONAL_THRESHOLDS["run_failure_rate_min_samples"])
            s.add(
                RunRow(
                    run_id=f"run-cov{i}",
                    task_id=f"task-cov{i}",
                    workspace_id="default",
                    attempt=1,
                    testcase_version_id="tcase@1",
                    execution_status="FAILED" if failed else "COMPLETED",
                    test_verdict="FAILED" if failed else "PASSED",
                    fencing_token=0,
                    created_at=now - 3600,
                    started_at=now - 3500,
                    ended_at=now - 3400,
                )
            )
    snap = _snapshot(db)
    snap["database"] = {"ok": False, "error": "down"}
    snap["artifact"] = {
        "missing_files": [{"artifact_id": "art_x", "path": "/x"}],
        "hash_mismatch": [],
        "gc": {"errors": 1},
    }
    engine.evaluate(snap)
    seen = {a["alert_type"] for a in engine.list_alerts()}
    for required in ALERT_TYPES:
        assert required in seen, f"缺少告警类型 {required}"


# ------------------------------------------------------------- 去重 / 自动解决
def test_dedupe_and_occurrences(db) -> None:  # type: ignore[no-untyped-def]
    engine = AlertEngine(db)
    _seed_stale_worker(db)
    engine.evaluate(_snapshot(db))
    engine.evaluate(_snapshot(db))
    rows = engine.list_alerts()
    assert len(rows) == 1
    assert rows[0]["occurrences"] == 2
    assert rows[0]["status"] == "OPEN"


def test_auto_resolve_when_condition_clears(db) -> None:  # type: ignore[no-untyped-def]
    engine = AlertEngine(db)
    _seed_stale_worker(db)
    engine.evaluate(_snapshot(db))
    assert engine.list_alerts(status="OPEN")
    with db.session() as s:
        row = s.get(WorkerHeartbeatRow, "w-stale")
        row.last_heartbeat_at = time.time()
    result = engine.evaluate(_snapshot(db))
    assert result["auto_resolved"] == ["WORKER_OFFLINE:w-stale"]
    resolved = engine.list_alerts(status="RESOLVED")
    assert resolved[0]["resolution_reason"] == "condition_cleared"
    assert resolved[0]["resolved_by"] == "system:alert-engine"


def test_reappear_after_resolve_creates_new_alert(db) -> None:  # type: ignore[no-untyped-def]
    engine = AlertEngine(db)
    _seed_stale_worker(db)
    engine.evaluate(_snapshot(db))
    first = engine.list_alerts(status="OPEN")[0]
    engine.resolve(first["alert_id"], "user:ops1", "临时重启")
    # 条件仍在（心跳仍旧）→ 新告警，不复活旧记录
    engine.evaluate(_snapshot(db))
    rows = engine.list_alerts()
    assert len(rows) == 2
    assert {r["status"] for r in rows} == {"OPEN", "RESOLVED"}


# ------------------------------------------------------------------ 阈值
def test_thresholds_declared_provisional() -> None:
    assert PROVISIONAL_THRESHOLDS["status"] == "provisional"
    assert "不是 SLA" in PROVISIONAL_THRESHOLDS["disclaimer"]
    merged = effective_thresholds()
    assert merged["status"] == "provisional"


def test_threshold_env_override(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("TG_ALERT_WORKER_OFFLINE_S", "42")
    merged = effective_thresholds()
    assert merged["worker_offline_seconds"] == 42.0
    assert merged["status"] == "provisional"
