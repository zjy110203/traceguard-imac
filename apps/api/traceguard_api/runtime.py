"""组合根（Composition Root）：所有服务在此构造，路由层只依赖该容器。

单机部署（默认）与分离 Worker 部署共用同一套服务；差异仅在于谁来执行 Run。
"""

from __future__ import annotations

import os
import socket
import threading
from typing import Any

from traceguard_core.config import get_settings
from traceguard_core.logging import get_logger
from traceguard_execution.runtime.pipeline import ExecutionOptions, RunPipeline
from traceguard_execution.tasks.service import TaskService
from traceguard_persistence.catalog import CatalogService
from traceguard_persistence.db import Database, get_database
from traceguard_persistence.stores import (
    ArtifactStore,
    DocStore,
    PersistentEventStore,
    PersistentEvidenceManager,
)

logger = get_logger("traceguard.runtime")

#: Run 的终态（非终态意味着仍在排队/执行）
TERMINAL_RUN_STATUSES = frozenset({"COMPLETED", "FAILED", "TIMED_OUT", "CANCELLED"})


def default_worker_id() -> str:
    return f"{socket.gethostname()}-{os.getpid()}"


class Runtime:
    def __init__(self, db: Database | None = None) -> None:
        settings = get_settings()
        self.settings = settings
        self.db = db or get_database()
        self.db.create_schema()
        self.catalog = CatalogService(self.db)
        self.tasks = TaskService(self.db)
        self.events = PersistentEventStore(self.db)
        self.evidence = PersistentEvidenceManager(self.db)
        self.artifacts = ArtifactStore(
            self.db, root=settings.artifact_dir, backend=settings.artifact_backend
        )
        self.docs = DocStore(self.db)
        self.worker_id = settings.worker_id or default_worker_id()
        self._pipeline_lock = threading.Lock()
        self._inline_thread: threading.Thread | None = None
        self._inline_stop = threading.Event()

    # ------------------------------------------------------------------ pipeline
    def execution_options(self) -> ExecutionOptions:
        s = self.settings
        return ExecutionOptions(
            headless=s.browser_headless,
            channel=s.browser_channel,
            max_duration_s=s.run_max_duration_s,
        )

    def pipeline(self, worker_id: str | None = None) -> RunPipeline:
        return RunPipeline(
            self.db,
            self.tasks,
            self.catalog,
            self.events,
            self.evidence,
            self.artifacts,
            worker_id=worker_id or self.worker_id,
            options=self.execution_options(),
        )

    def execute_run(self, run_id: str, worker_id: str | None = None) -> dict[str, Any]:
        outcome = self.pipeline(worker_id).execute(run_id)
        return {
            "run_id": outcome.run_id,
            "execution_status": outcome.execution_status,
            "test_verdict": outcome.test_verdict,
            "observer_mode": outcome.observer_mode,
            "report": outcome.report,
        }

    # -------------------------------------------------------------- run waiting
    def wait_for_run(self, run_id: str, timeout_s: float = 300.0, poll_s: float = 0.25) -> dict:
        """等待 Run 到达终态（由**真实 Worker 消费队列**，不由 API 线程执行）。

        RUN-007-RESUME：沙箱/回归等正式执行必须走队列；这里只做等待与状态读取，
        绝不调用 execute_run，避免"API 线程池冒充 Worker"。
        """
        import time as _time

        deadline = _time.time() + max(1.0, timeout_s)
        doc: dict[str, Any] | None = None
        while _time.time() < deadline:
            doc = self.tasks.get_run(run_id)
            if doc and doc.get("execution_status") in TERMINAL_RUN_STATUSES:
                return doc
            _time.sleep(poll_s)
        return doc or {
            "run_id": run_id,
            "execution_status": "UNKNOWN",
            "test_verdict": "NOT_EVALUATED",
        }

    # -------------------------------------------------------------- inline worker
    def start_inline_worker(self) -> None:
        """单机模式：后台线程消费队列（与独立 Worker 相同语义，便于本地演示）。"""
        if self._inline_thread and self._inline_thread.is_alive():
            return
        self._inline_stop.clear()

        def loop() -> None:
            # RUN-018 WP8：心跳 + 告警评估（共享同一持久化层；观测失败绝不影响执行）
            from traceguard_persistence.alerts import AlertEngine
            from traceguard_persistence.monitoring import (
                MetricsCollector,
                upsert_worker_heartbeat,
            )

            pid = f"{self.worker_id}-inline"
            counters = {"claims": 0, "takeovers": 0, "failures": 0}
            heartbeat = upsert_worker_heartbeat
            engine = AlertEngine(self.db, min_evaluate_interval_s=30.0)
            collector = MetricsCollector(self.db, self.artifacts, worker_id=pid)

            def beat(
                status: str,
                run_id: str | None = None,
                task_id: str | None = None,
                lease_expires_at: float = 0.0,
            ) -> None:
                try:
                    heartbeat(
                        self.db,
                        worker_id=pid,
                        mode="inline",
                        status=status,
                        hostname=__import__("socket").gethostname(),
                        pid=__import__("os").getpid(),
                        current_run_id=run_id,
                        current_task_id=task_id,
                        lease_expires_at=lease_expires_at,
                        claims_delta=counters["claims"],
                        takeovers_delta=counters["takeovers"],
                        failures_delta=counters["failures"],
                    )
                    counters["claims"] = counters["takeovers"] = counters["failures"] = 0
                except Exception:  # noqa: BLE001 - 心跳失败不得影响 Worker 主循环
                    pass

            while not self._inline_stop.is_set():
                try:
                    self.tasks.reap_expired_runs()
                    claimed = self.tasks.claim_next_run(pid, ttl_seconds=600)
                    if not claimed:
                        beat("IDLE")
                        try:
                            engine.evaluate(collector.collect())
                        except Exception:  # noqa: BLE001
                            pass
                        self._inline_stop.wait(self.settings.worker_poll_interval_s)
                        continue
                    logger.info("worker_claim", run_id=claimed["run_id"], worker=pid)
                    counters["claims"] += 1
                    if str(claimed.get("previous_status") or "QUEUED") != "QUEUED":
                        counters["takeovers"] += 1
                    beat(
                        "RUNNING",
                        run_id=claimed["run_id"],
                        task_id=claimed.get("task_id"),
                        lease_expires_at=float(claimed.get("lease_expires_at") or 0.0),
                    )
                    outcome = self.pipeline(pid).execute(claimed["run_id"], lease=claimed)
                    if outcome.execution_status in ("FAILED", "TIMED_OUT"):
                        counters["failures"] += 1
                    beat("IDLE")
                except Exception:  # noqa: BLE001 - worker 不得因单次任务失败退出
                    logger.exception("worker_loop_error")
                    counters["failures"] += 1
                    beat("IDLE")
                    self._inline_stop.wait(2.0)
            beat("STOPPED")

        self._inline_thread = threading.Thread(target=loop, name="tg-inline-worker", daemon=True)
        self._inline_thread.start()
        logger.info("inline_worker_started", worker=self.worker_id)

    def stop_inline_worker(self) -> None:
        self._inline_stop.set()
        if self._inline_thread:
            self._inline_thread.join(timeout=10)
            self._inline_thread = None

    # ------------------------------------------------------------------ health
    def health(self, *, workspace_ids: list[str] | None = None) -> dict[str, Any]:
        import os
        import resource
        import sys

        from sqlalchemy import func, select, text
        from traceguard_persistence.models import IntentRow, RunRow, TaskRow, TestCaseRow

        db_ok = True
        db_error = ""
        try:
            with self.db.session() as s:
                s.execute(text("SELECT 1"))
        except Exception as exc:  # noqa: BLE001
            db_ok, db_error = False, str(exc)
        usage = resource.getrusage(resource.RUSAGE_SELF)
        rss = int(usage.ru_maxrss)
        if sys.platform != "darwin":
            rss *= 1024  # Linux: KiB → bytes

        # RUN-013 WP6：健康计数与 /system/metrics 同口径——普通用户只统计授权
        # Workspace 并集，且使用 SQL COUNT 避免 list(limit=1000) 的截断失真。
        counts: dict[str, int] = {"intents": 0, "testcases": 0, "tasks": 0, "runs": 0}
        if db_ok:
            with self.db.session() as s:
                for label, model in (
                    ("intents", IntentRow),
                    ("testcases", TestCaseRow),
                    ("tasks", TaskRow),
                    ("runs", RunRow),
                ):
                    q = select(func.count()).select_from(model)
                    if workspace_ids is not None:
                        q = q.where(model.workspace_id.in_(list(workspace_ids)))
                    counts[label] = int(s.scalar(q) or 0)

        return {
            "database": {"ok": db_ok, "dialect": self.db.url.split(":")[0], "error": db_error},
            "artifact_dir": str(self.artifacts.root),
            "process": {
                "pid": os.getpid(),
                "rss_bytes": rss,
                "cpu_user_s": round(float(usage.ru_utime), 3),
                "cpu_sys_s": round(float(usage.ru_stime), 3),
            },
            "worker": {
                "id": self.worker_id,
                "inline_running": bool(self._inline_thread and self._inline_thread.is_alive()),
            },
            "counts": counts,
        }


_runtime: Runtime | None = None
_lock = threading.Lock()


def get_runtime() -> Runtime:
    global _runtime
    with _lock:
        if _runtime is None:
            _runtime = Runtime()
        return _runtime


def reset_runtime() -> None:
    global _runtime
    with _lock:
        if _runtime is not None:
            _runtime.stop_inline_worker()
        _runtime = None
