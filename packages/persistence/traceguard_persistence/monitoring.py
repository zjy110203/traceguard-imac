"""RUN-018 WP8：产品运行监控指标（API / Worker / Queue / Run / Artifact / Database）。

设计约束（来自 WP8 指令）：
- 尽量扩展现有能力，不引入 Redis / Celery / S3 / 新消息队列 / 外部监控栈；
- 指标全部来自现有持久化表（tg_run / tg_task / tg_artifact / tg_worker_heartbeat /
  tg_schema_migration）与进程内轻量计数器；
- 所有阈值为 **provisional**（临时值，未经实测校准），不得宣传为正式 SLA；
- 统计口径随响应显式声明（与 /system/metrics 的 `counting` 口径声明一致）。

进程内计数器（请求指标 / 队列趋势 / 数据库失败计数）随进程重启清零，
响应中以 `since` / `note` 显式声明，绝不冒充全历史数据。
"""

from __future__ import annotations

import hashlib
import threading
import time
from collections import deque
from typing import Any

from sqlalchemy import func, select
from traceguard_core.ids import utc_now_iso

from .db import Database
from .models import ArtifactRow, RunRow, WorkerHeartbeatRow

# ------------------------------------------------------------------ thresholds
#: 全部为 **provisional** 阈值：未经过真实负载实测校准，仅用于告警初版触发。
#: 任何文档/界面/响应都必须标注 provisional，不得当作 SLA 承诺。
PROVISIONAL_THRESHOLDS: dict[str, Any] = {
    "status": "provisional",
    "disclaimer": "所有阈值均为 provisional（临时配置，未实测校准），不是 SLA。",
    "worker_offline_seconds": 90.0,
    "queue_backlog_count": 20,
    "queue_oldest_wait_seconds": 900.0,
    "run_failure_rate": 0.5,
    "run_failure_rate_min_samples": 5,
    "run_overrun_seconds": 900.0,
    "artifact_missing_max": 0,
    "artifact_hash_mismatch_max": 0,
    "gc_error_max": 0,
    "api_error_rate": 0.2,
    "api_error_rate_min_requests": 20,
    "artifact_hash_check_limit": 50,
}

#: 告警规则引用的阈值键 → 环境变量覆盖（可选；未设置即用上表默认值）
THRESHOLD_ENV_KEYS: dict[str, str] = {
    "worker_offline_seconds": "TG_ALERT_WORKER_OFFLINE_S",
    "queue_backlog_count": "TG_ALERT_QUEUE_BACKLOG_N",
    "queue_oldest_wait_seconds": "TG_ALERT_QUEUE_WAIT_S",
    "run_failure_rate": "TG_ALERT_RUN_FAILURE_RATE",
    "run_failure_rate_min_samples": "TG_ALERT_RUN_FAILURE_MIN_SAMPLES",
    "run_overrun_seconds": "TG_ALERT_RUN_OVERRUN_S",
    "api_error_rate": "TG_ALERT_API_ERROR_RATE",
}


def effective_thresholds() -> dict[str, Any]:
    """合并环境变量覆盖（仍是 provisional，覆盖不改变其非 SLA 性质）。"""
    import os

    out = dict(PROVISIONAL_THRESHOLDS)
    for key, env_name in THRESHOLD_ENV_KEYS.items():
        raw = os.environ.get(env_name, "").strip()
        if not raw:
            continue
        try:
            value = float(raw)
        except ValueError:
            continue
        out[key] = int(value) if key.endswith("_count") or key.endswith("_samples") else value
    return out


# ------------------------------------------------------- in-process registries
class _RequestMetrics:
    """API 请求指标（进程内，有界窗口）。

    只做聚合（计数 / 错误率 / 响应时间分位）与最近错误列表，
    不做按路径的高基数分组，避免内存无界增长。
    """

    def __init__(self, maxlen: int = 2000, error_keep: int = 50) -> None:
        self.started_at = time.time()
        self.total = 0
        self.by_status_class: dict[str, int] = {"2xx": 0, "3xx": 0, "4xx": 0, "5xx": 0}
        self._lock = threading.Lock()
        self._durations_ms: deque[float] = deque(maxlen=maxlen)
        self._errors: deque[dict[str, Any]] = deque(maxlen=error_keep)
        self.window_samples = maxlen

    def record(self, method: str, path: str, status: int, duration_ms: float) -> None:
        with self._lock:
            self.total += 1
            cls = f"{int(status) // 100}xx"
            if cls in self.by_status_class:
                self.by_status_class[cls] += 1
            self._durations_ms.append(float(duration_ms))
            if status >= 400:
                self._errors.append(
                    {
                        "method": method,
                        "path": path,
                        "status": int(status),
                        "duration_ms": round(float(duration_ms), 2),
                        "at": time.time(),
                    }
                )

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            durations = sorted(self._durations_ms)
            total = self.total
            classes = dict(self.by_status_class)
            errors = list(self._errors)
        err4 = classes.get("4xx", 0)
        err5 = classes.get("5xx", 0)
        error_count = err4 + err5
        return {
            "requests_total": total,
            "by_status_class": classes,
            "error_count": error_count,
            "error_rate": round(error_count / total, 4) if total else 0.0,
            "error_rate_5xx": round(err5 / total, 4) if total else 0.0,
            "response_time_ms": _duration_stats(durations),
            "recent_errors": errors[-20:][::-1],
            "since": self.started_at,
            "since_iso": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(self.started_at)),
            "window": {
                "samples": len(durations),
                "max_samples": self.window_samples,
                "note": "进程内有界窗口（随进程重启清零）；分位数只覆盖窗口内样本",
            },
        }


def _percentile(sorted_values: list[float], pct: float) -> float | None:
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return round(sorted_values[0], 2)
    rank = (pct / 100.0) * (len(sorted_values) - 1)
    low = int(rank)
    high = min(low + 1, len(sorted_values) - 1)
    frac = rank - low
    return round(sorted_values[low] * (1 - frac) + sorted_values[high] * frac, 2)


def _duration_stats(durations: list[float]) -> dict[str, Any]:
    if not durations:
        return {"count": 0, "p50": None, "p95": None, "avg": None, "max": None}
    return {
        "count": len(durations),
        "p50": _percentile(durations, 50),
        "p95": _percentile(durations, 95),
        "avg": round(sum(durations) / len(durations), 2),
        "max": round(max(durations), 2),
    }


REQUEST_METRICS = _RequestMetrics()

_queue_trend: deque[dict[str, Any]] = deque(maxlen=240)
_queue_trend_lock = threading.Lock()
_queue_trend_last = 0.0

_db_failures: dict[str, Any] = {"read": 0, "write": 0, "last_error": "", "last_error_at": 0.0}
_db_failures_lock = threading.Lock()


def note_db_failure(kind: str, error: str) -> None:
    """记录监控视角观察到的关键读/写失败（进程内计数，随重启清零）。"""
    with _db_failures_lock:
        if kind in ("read", "write"):
            _db_failures[kind] = int(_db_failures[kind]) + 1
        _db_failures["last_error"] = str(error)[:300]
        _db_failures["last_error_at"] = time.time()


def reset_process_metrics() -> None:
    """测试隔离：清空进程内监控状态。"""
    global _queue_trend_last
    with REQUEST_METRICS._lock:  # noqa: SLF001 - 同模块内部复位
        REQUEST_METRICS.total = 0
        REQUEST_METRICS.by_status_class = {"2xx": 0, "3xx": 0, "4xx": 0, "5xx": 0}
        REQUEST_METRICS._durations_ms.clear()
        REQUEST_METRICS._errors.clear()
        REQUEST_METRICS.started_at = time.time()
    with _queue_trend_lock:
        _queue_trend.clear()
        _queue_trend_last = 0.0
    with _db_failures_lock:
        _db_failures.update({"read": 0, "write": 0, "last_error": "", "last_error_at": 0.0})


# ------------------------------------------------------------------ heartbeat
def upsert_worker_heartbeat(
    db: Database,
    *,
    worker_id: str,
    mode: str = "standalone",
    status: str = "IDLE",
    hostname: str = "",
    pid: int = 0,
    current_run_id: str | None = None,
    current_task_id: str | None = None,
    lease_expires_at: float = 0.0,
    claims_delta: int = 0,
    takeovers_delta: int = 0,
    failures_delta: int = 0,
    detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Worker 心跳 upsert（每个轮询周期调用；失败不得影响 Worker 主循环）。

    计数器（claims / takeovers / failures）用增量语义：调用方只报本周期变化量。
    """
    now = time.time()
    with db.session() as s:
        row = s.get(WorkerHeartbeatRow, worker_id)
        if row is None:
            row = WorkerHeartbeatRow(
                worker_id=worker_id,
                mode=mode,
                status=status,
                hostname=hostname,
                pid=pid,
                current_run_id=current_run_id,
                current_task_id=current_task_id,
                lease_expires_at=lease_expires_at,
                claims_total=max(0, claims_delta),
                takeovers_total=max(0, takeovers_delta),
                failures_total=max(0, failures_delta),
                started_at=now,
                last_heartbeat_at=now,
                detail=detail or {},
            )
            s.add(row)
        else:
            row.mode = mode
            row.status = status
            row.hostname = hostname or row.hostname
            row.pid = pid or row.pid
            row.current_run_id = current_run_id
            row.current_task_id = current_task_id
            row.lease_expires_at = lease_expires_at
            row.claims_total = int(row.claims_total or 0) + max(0, claims_delta)
            row.takeovers_total = int(row.takeovers_total or 0) + max(0, takeovers_delta)
            row.failures_total = int(row.failures_total or 0) + max(0, failures_delta)
            row.last_heartbeat_at = now
            if detail:
                row.detail = detail
        s.flush()
        return {
            "worker_id": worker_id,
            "status": status,
            "last_heartbeat_at": now,
            "claims_total": int(row.claims_total or 0),
            "takeovers_total": int(row.takeovers_total or 0),
            "failures_total": int(row.failures_total or 0),
        }


# ------------------------------------------------------------------- snapshot
class MetricsCollector:
    """聚合六类运行指标（API / Worker / Queue / Run / Artifact / Database）。"""

    def __init__(
        self,
        db: Database,
        artifacts: ArtifactStore | None = None,
        *,
        worker_id: str = "",
    ) -> None:
        self.db = db
        self.artifacts = artifacts
        self.worker_id = worker_id

    # ------------------------------------------------------------------ API
    def api_metrics(self) -> dict[str, Any]:
        snap = REQUEST_METRICS.snapshot()
        db_ok, db_error = self._probe_db()
        health = "ok" if db_ok else "degraded"
        return {
            "health": health,
            "requests_total": snap["requests_total"],
            "error_count": snap["error_count"],
            "error_rate": snap["error_rate"],
            "response_time_ms": snap["response_time_ms"],
            "recent_errors": snap["recent_errors"],
            "database_ok": db_ok,
            "database_error": db_error,
            "since": snap["since"],
            "since_iso": snap["since_iso"],
            "window": snap["window"],
            "counting": {
                "source": "进程内请求计数器（有界窗口）",
                "note": "随进程重启清零；不是全历史流量",
            },
        }

    # --------------------------------------------------------------- Worker
    def worker_metrics(self, *, now: float | None = None) -> dict[str, Any]:
        now_ts = time.time() if now is None else now
        thresholds = effective_thresholds()
        offline_after = float(thresholds["worker_offline_seconds"])
        try:
            with self.db.session() as s:
                rows = s.scalars(select(WorkerHeartbeatRow)).all()
                workers: list[dict[str, Any]] = []
                for r in rows:
                    age = max(0.0, now_ts - float(r.last_heartbeat_at or 0.0))
                    online = age <= offline_after and r.status != "STOPPED"
                    lease_remaining = 0.0
                    if r.current_run_id:
                        run = s.get(RunRow, r.current_run_id)
                        if run is not None:
                            lease_remaining = max(0.0, float(run.lease_expires_at or 0.0) - now_ts)
                    workers.append(
                        {
                            "worker_id": r.worker_id,
                            "mode": r.mode,
                            "status": r.status,
                            "online": online,
                            "hostname": r.hostname,
                            "pid": r.pid,
                            "last_heartbeat_at": r.last_heartbeat_at,
                            "heartbeat_age_s": round(age, 1),
                            "current_run_id": r.current_run_id,
                            "current_task_id": r.current_task_id,
                            "lease_remaining_s": round(lease_remaining, 1),
                            "claims_total": r.claims_total,
                            "takeovers_total": r.takeovers_total,
                            "failures_total": r.failures_total,
                            "started_at": r.started_at,
                        }
                    )
        except Exception as exc:  # noqa: BLE001 - 观测失败要显式暴露，不吞
            note_db_failure("read", str(exc))
            return {
                "error": f"{type(exc).__name__}: {exc}"[:300],
                "workers": [],
                "total": 0,
                "online": 0,
                "offline": 0,
                "thresholds": {"worker_offline_seconds": offline_after},
            }
        online_n = sum(1 for w in workers if w["online"])
        return {
            "workers": sorted(workers, key=lambda w: w["worker_id"]),
            "total": len(workers),
            "online": online_n,
            "offline": len(workers) - online_n,
            "thresholds": {
                "worker_offline_seconds": offline_after,
                "status": "provisional",
            },
            "counting": {
                "source": "tg_worker_heartbeat（Worker 每轮询周期 upsert）",
                "online_definition": f"last_heartbeat_at 距今 <= {offline_after}s 且 status != STOPPED",
            },
        }

    # ---------------------------------------------------------------- Queue
    def queue_metrics(
        self, *, workspace_ids: list[str] | None = None, now: float | None = None
    ) -> dict[str, Any]:
        now_ts = time.time() if now is None else now
        thresholds = effective_thresholds()
        overrun_s = float(thresholds["run_overrun_seconds"])
        try:
            with self.db.session() as s:
                stmt = select(RunRow.execution_status, func.count()).group_by(
                    RunRow.execution_status
                )
                if workspace_ids is not None:
                    stmt = stmt.where(RunRow.workspace_id.in_(list(workspace_ids)))
                by_status = {str(k or "UNKNOWN"): int(v) for k, v in s.execute(stmt).all()}

                q = select(func.min(RunRow.created_at)).where(RunRow.execution_status == "QUEUED")
                if workspace_ids is not None:
                    q = q.where(RunRow.workspace_id.in_(list(workspace_ids)))
                oldest_created = s.scalar(q)
                oldest_wait_s = (
                    round(max(0.0, now_ts - float(oldest_created)), 1) if oldest_created else 0.0
                )

                overdue_q = select(RunRow).where(
                    RunRow.execution_status.in_(["QUEUED", "PREPARING", "RUNNING"]),
                    RunRow.created_at < now_ts - overrun_s,
                )
                if workspace_ids is not None:
                    overdue_q = overdue_q.where(RunRow.workspace_id.in_(list(workspace_ids)))
                overdue_rows = s.scalars(overdue_q.order_by(RunRow.created_at).limit(20)).all()
                overdue = [
                    {
                        "run_id": r.run_id,
                        "task_id": r.task_id,
                        "execution_status": r.execution_status,
                        "waited_s": round(max(0.0, now_ts - float(r.created_at or 0.0)), 1),
                        "worker_id": r.lease_owner,
                    }
                    for r in overdue_rows
                ]
        except Exception as exc:  # noqa: BLE001
            note_db_failure("read", str(exc))
            return {"error": f"{type(exc).__name__}: {exc}"[:300]}

        queued = by_status.get("QUEUED", 0)
        running = by_status.get("RUNNING", 0) + by_status.get("PREPARING", 0)
        trend = self._queue_trend_sample(queued=queued, running=running, now=now_ts)
        return {
            "by_status": by_status,
            "queued": queued,
            "running": running,
            "oldest_queued_wait_s": oldest_wait_s,
            "timed_out_total": by_status.get("TIMED_OUT", 0),
            "overdue_running": overdue,
            "overdue_threshold_s": overrun_s,
            "trend": trend,
            "thresholds": {
                "queue_backlog_count": thresholds["queue_backlog_count"],
                "queue_oldest_wait_seconds": thresholds["queue_oldest_wait_seconds"],
                "run_overrun_seconds": overrun_s,
                "status": "provisional",
            },
            "scope": {
                "mode": "global" if workspace_ids is None else "authorized_workspaces",
                "global": workspace_ids is None,
                "authorized_workspaces": workspace_ids,
            },
            "counting": {
                "source": "SQL COUNT(*) / GROUP BY 直接查询 tg_run",
                "time_range": "ALL_TIME（趋势为进程内采样）",
            },
        }

    def _queue_trend_sample(self, *, queued: int, running: int, now: float) -> dict[str, Any]:
        global _queue_trend_last
        with _queue_trend_lock:
            if now - _queue_trend_last >= 3.0 or not _queue_trend:
                _queue_trend.append({"at": now, "queued": queued, "running": running})
                _queue_trend_last = now
            samples = list(_queue_trend)
        return {
            "samples": samples,
            "note": "进程内采样（每次指标请求最多 3s 一条，随进程重启清零）；"
            "如需长期趋势请在此基础上接入外部时序库（本期不引入）",
            "backlog_direction": _backlog_direction(samples),
        }

    # ------------------------------------------------------------------ Run
    def run_metrics(self, *, workspace_ids: list[str] | None = None, now: float | None = None
    ) -> dict[str, Any]:
        now_ts = time.time() if now is None else now
        window_start = now_ts - 86400.0
        try:
            with self.db.session() as s:
                verdict_stmt = select(RunRow.test_verdict, func.count()).group_by(
                    RunRow.test_verdict
                )
                status_stmt = select(RunRow.execution_status, func.count()).group_by(
                    RunRow.execution_status
                )
                if workspace_ids is not None:
                    verdict_stmt = verdict_stmt.where(
                        RunRow.workspace_id.in_(list(workspace_ids))
                    )
                    status_stmt = status_stmt.where(RunRow.workspace_id.in_(list(workspace_ids)))
                verdicts = {str(k or "UNKNOWN"): int(v) for k, v in s.execute(verdict_stmt).all()}
                by_status = {str(k or "UNKNOWN"): int(v) for k, v in s.execute(status_stmt).all()}

                w_q = select(RunRow).where(
                    RunRow.ended_at.isnot(None), RunRow.ended_at >= window_start
                )
                if workspace_ids is not None:
                    w_q = w_q.where(RunRow.workspace_id.in_(list(workspace_ids)))
                recent = s.scalars(w_q.order_by(RunRow.ended_at.desc()).limit(2000)).all()

                f_q = select(RunRow).where(
                    RunRow.execution_status.in_(["FAILED", "TIMED_OUT"]),
                )
                if workspace_ids is not None:
                    f_q = f_q.where(RunRow.workspace_id.in_(list(workspace_ids)))
                recent_failures_all = s.scalars(
                    f_q.order_by(RunRow.created_at.desc()).limit(20)
                ).all()
        except Exception as exc:  # noqa: BLE001
            note_db_failure("read", str(exc))
            return {"error": f"{type(exc).__name__}: {exc}"[:300]}

        w_verdicts: dict[str, int] = {}
        durations: list[float] = []
        failure_types: dict[str, int] = {}
        for r in recent:
            v = str(r.test_verdict or "UNKNOWN")
            w_verdicts[v] = w_verdicts.get(v, 0) + 1
            if r.started_at and r.ended_at:
                durations.append(max(0.0, float(r.ended_at) - float(r.started_at)))
            if r.execution_status in ("FAILED", "TIMED_OUT") or v == "FAILED":
                key = _failure_type(r.error, r.execution_status)
                failure_types[key] = failure_types.get(key, 0) + 1

        evaluated = sum(w_verdicts.get(k, 0) for k in ("PASSED", "FAILED", "INCONCLUSIVE"))
        passed = w_verdicts.get("PASSED", 0)
        success_rate = round(passed / evaluated, 4) if evaluated else None

        recent_failures = [
            {
                "run_id": r.run_id,
                "task_id": r.task_id,
                "workspace_id": r.workspace_id,
                "execution_status": r.execution_status,
                "test_verdict": r.test_verdict,
                "error": (r.error or "")[:300],
                "ended_at": r.ended_at,
            }
            for r in recent_failures_all
        ]
        return {
            "by_verdict": verdicts,
            "by_status": by_status,
            "last_24h": {
                "finished": len(recent),
                "by_verdict": w_verdicts,
                "evaluated": evaluated,
                "success_rate": success_rate,
                "success_rate_definition": "PASSED / (PASSED+FAILED+INCONCLUSIVE)，窗口 24h，按 ended_at 计",
                "duration_ms": _duration_stats([d * 1000.0 for d in durations]),
                "duration_definition": "ended_at - started_at（毫秒），仅统计 24h 内两者齐全的 Run",
                "failure_types": dict(
                    sorted(failure_types.items(), key=lambda kv: (-kv[1], kv[0]))[:5]
                ),
            },
            "recent_failures": recent_failures,
            "scope": {
                "mode": "global" if workspace_ids is None else "authorized_workspaces",
                "global": workspace_ids is None,
                "authorized_workspaces": workspace_ids,
            },
            "counting": {
                "source": "SQL 聚合 + 24h 窗口内行扫描（上限 2000 行/请求，超出以最近 2000 条为准）",
                "time_range": "by_verdict=ALL_TIME；last_24h=ended_at ∈ [now-24h, now]",
            },
        }

    # ------------------------------------------------------------- Artifact
    def artifact_metrics(self, *, deep_limit: int | None = None) -> dict[str, Any]:
        thresholds = effective_thresholds()
        hash_limit = int(
            deep_limit
            if deep_limit is not None
            else thresholds.get("artifact_hash_check_limit", 50)
        )
        out: dict[str, Any] = {
            "registered": 0,
            "total_bytes": 0,
            "missing_files": [],
            "hash_mismatch": [],
            "orphan_files": [],
            "orphan_rows": [],
            "gc": {},
            "hash_check": {"checked": 0, "limit": hash_limit, "truncated": False},
        }
        if self.artifacts is None:
            out["note"] = "ArtifactStore 未接入本进程，仅返回空占位"
            return out
        try:
            with self.db.session() as s:
                out["registered"] = int(s.scalar(select(func.count()).select_from(ArtifactRow)) or 0)
                out["total_bytes"] = int(
                    s.scalar(select(func.coalesce(func.sum(ArtifactRow.size), 0))) or 0
                )
                rows = s.execute(
                    select(ArtifactRow.artifact_id, ArtifactRow.sha256, ArtifactRow.path)
                    .order_by(ArtifactRow.created_at.desc())
                    .limit(max(0, hash_limit))
                ).all()
            mismatch: list[dict[str, Any]] = []
            checked = 0
            from pathlib import Path as _Path

            for artifact_id, sha256, path_str in rows:
                checked += 1
                p = _Path(path_str)
                if not p.is_file():
                    continue  # 缺失由 scan() 统一报告，避免重复
                digest = hashlib.sha256(p.read_bytes()).hexdigest()
                if digest != sha256:
                    mismatch.append({"artifact_id": artifact_id, "expected": sha256, "actual": digest})
            out["hash_mismatch"] = mismatch
            out["hash_check"] = {
                "checked": checked,
                "limit": hash_limit,
                "truncated": checked >= hash_limit and out["registered"] > checked,
                "note": "有界校验：只重算最近 N 条登记的文件哈希；完整校验请跑 scripts/artifact_gc_run016.py",
            }
            scan = self.artifacts.scan()
            out["missing_files"] = scan.get("missing_files", [])
            out["orphan_files"] = scan.get("orphan_files", [])
            out["orphan_rows"] = scan.get("orphan_rows", [])
            out["protected_fresh"] = scan.get("protected_fresh", [])
            out["gc"] = _gc_status(self.artifacts)
        except Exception as exc:  # noqa: BLE001
            note_db_failure("read", str(exc))
            out["error"] = f"{type(exc).__name__}: {exc}"[:300]
        out["thresholds"] = {
            "artifact_missing_max": thresholds["artifact_missing_max"],
            "artifact_hash_mismatch_max": thresholds["artifact_hash_mismatch_max"],
            "gc_error_max": thresholds["gc_error_max"],
            "status": "provisional",
        }
        return out

    # ------------------------------------------------------------- Database
    def database_metrics(self) -> dict[str, Any]:
        ok, error = self._probe_db()
        pool_info: dict[str, Any] = {}
        try:
            pool = self.db.engine.pool
            status = pool.status()
            pool_info = {
                "status": str(status),
                "dialect": self.db.url.split(":")[0],
            }
            for attr in ("size", "checkedin", "checkedout", "overflow"):
                if hasattr(pool, attr):
                    try:
                        pool_info[attr] = int(getattr(pool, attr))
                    except Exception:  # noqa: BLE001
                        pass
        except Exception as exc:  # noqa: BLE001
            pool_info = {"error": f"{type(exc).__name__}: {exc}"[:200]}
        migration: dict[str, Any] = {}
        try:
            from sqlalchemy import text as _text

            with self.db.session() as s:
                row = s.execute(
                    _text(
                        "SELECT COALESCE(MAX(version), 0), COUNT(*) FROM tg_schema_migration"
                    )
                ).first()
                migration = {
                    "latest_version": int(row[0] or 0) if row else 0,
                    "applied": int(row[1] or 0) if row else 0,
                }
        except Exception as exc:  # noqa: BLE001 - 旧库可能尚无迁移表
            migration = {"error": f"{type(exc).__name__}: {exc}"[:200]}
        with _db_failures_lock:
            failures = {
                "read_failures": int(_db_failures["read"]),
                "write_failures": int(_db_failures["write"]),
                "last_error": _db_failures["last_error"],
                "last_error_at": _db_failures["last_error_at"],
            }
        return {
            "ok": ok,
            "error": error,
            "dialect": self.db.url.split(":")[0],
            "migration": migration,
            "pool": pool_info,
            "failures": failures,
            "counting": {
                "source": "SELECT 1 探活 + tg_schema_migration + SQLAlchemy 连接池状态",
                "failures_note": "读/写失败计数为监控子系统自身观察到的失败（进程内，随重启清零）",
            },
        }

    def _probe_db(self) -> tuple[bool, str]:
        try:
            from sqlalchemy import text as _text

            with self.db.session() as s:
                s.execute(_text("SELECT 1"))
            return True, ""
        except Exception as exc:  # noqa: BLE001
            note_db_failure("read", str(exc))
            return False, f"{type(exc).__name__}: {exc}"[:300]

    # ------------------------------------------------------------------ all
    def collect(
        self,
        *,
        workspace_ids: list[str] | None = None,
        now: float | None = None,
        artifact_deep_limit: int | None = None,
    ) -> dict[str, Any]:
        now_ts = time.time() if now is None else now
        return {
            "generated_at": now_ts,
            "generated_at_iso": utc_now_iso(),
            "thresholds": effective_thresholds(),
            "thresholds_status": {
                "status": "provisional",
                "disclaimer": "所有阈值均为 provisional（临时配置，未实测校准），不是 SLA。",
            },
            "api": self.api_metrics(),
            "worker": self.worker_metrics(now=now_ts),
            "queue": self.queue_metrics(workspace_ids=workspace_ids, now=now_ts),
            "run": self.run_metrics(workspace_ids=workspace_ids, now=now_ts),
            "artifact": self.artifact_metrics(deep_limit=artifact_deep_limit),
            "database": self.database_metrics(),
        }


# ------------------------------------------------------------- small helpers
def _backlog_direction(samples: list[dict[str, Any]]) -> str:
    if len(samples) < 2:
        return "unknown"
    recent = [s["queued"] for s in samples[-6:]]
    if recent[-1] > recent[0]:
        return "rising"
    if recent[-1] < recent[0]:
        return "falling"
    return "steady"


def _failure_type(error: str | None, execution_status: str) -> str:
    if execution_status == "TIMED_OUT":
        return "TIMED_OUT"
    text = (error or "").strip()
    if not text:
        return "UNKNOWN"
    head = text.split(":", 1)[0].strip()
    return (head or "UNKNOWN")[:60]


def _gc_status(artifacts: Any) -> dict[str, Any]:
    """GC 状态：读取 GC 审计文件（scripts/artifact_gc_run016.py 落盘）。"""
    import json as _json
    from pathlib import Path

    audit_path = Path(artifacts.root) / "_gc-audit.jsonl"
    quarantine = Path(artifacts.root) / "_quarantine"
    status: dict[str, Any] = {
        "audit_path": str(audit_path) if audit_path.exists() else None,
        "last_run_at": None,
        "total_records": 0,
        "errors": 0,
        "quarantine_dirs": 0,
    }
    try:
        if audit_path.is_file():
            lines = [ln for ln in audit_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
            status["total_records"] = len(lines)
            for ln in lines[-50:]:
                try:
                    rec = _json.loads(ln)
                except ValueError:
                    continue
                if rec.get("result") == "error":
                    status["errors"] += 1
                status["last_run_at"] = rec.get("at") or status["last_run_at"]
            # 全文件错误计数（不过滤尾部）：GC 异常检测必须覆盖历史
            status["errors"] = sum(
                1
                for ln in lines
                if (lambda r: r.get("result") == "error")(_safe_json(ln))
            )
        if quarantine.is_dir():
            status["quarantine_dirs"] = sum(1 for p in quarantine.iterdir() if p.is_dir())
    except Exception as exc:  # noqa: BLE001
        status["error"] = f"{type(exc).__name__}: {exc}"[:200]
    return status


def _safe_json(line: str) -> dict[str, Any]:
    import json as _json

    try:
        return _json.loads(line)
    except ValueError:
        return {}


__all__ = [
    "MetricsCollector",
    "PROVISIONAL_THRESHOLDS",
    "REQUEST_METRICS",
    "effective_thresholds",
    "note_db_failure",
    "reset_process_metrics",
    "upsert_worker_heartbeat",
]
