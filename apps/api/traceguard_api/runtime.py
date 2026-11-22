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
        self.artifacts = ArtifactStore(self.db, root=settings.artifact_dir)
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

    # -------------------------------------------------------------- inline worker
    def start_inline_worker(self) -> None:
        """单机模式：后台线程消费队列（与独立 Worker 相同语义，便于本地演示）。"""
        if self._inline_thread and self._inline_thread.is_alive():
            return
        self._inline_stop.clear()

        def loop() -> None:
            pid = f"{self.worker_id}-inline"
            while not self._inline_stop.is_set():
                try:
                    self.tasks.reap_expired_runs()
                    claimed = self.tasks.claim_next_run(pid, ttl_seconds=600)
                    if not claimed:
                        self._inline_stop.wait(self.settings.worker_poll_interval_s)
                        continue
                    logger.info("worker_claim", run_id=claimed["run_id"], worker=pid)
                    self.pipeline(pid).execute(claimed["run_id"], lease=claimed)
                except Exception:  # noqa: BLE001 - worker 不得因单次任务失败退出
                    logger.exception("worker_loop_error")
                    self._inline_stop.wait(2.0)

        self._inline_thread = threading.Thread(target=loop, name="tg-inline-worker", daemon=True)
        self._inline_thread.start()
        logger.info("inline_worker_started", worker=self.worker_id)

    def stop_inline_worker(self) -> None:
        self._inline_stop.set()
        if self._inline_thread:
            self._inline_thread.join(timeout=10)
            self._inline_thread = None

    # ------------------------------------------------------------------ health
    def health(self) -> dict[str, Any]:
        from sqlalchemy import text

        db_ok = True
        db_error = ""
        try:
            with self.db.session() as s:
                s.execute(text("SELECT 1"))
        except Exception as exc:  # noqa: BLE001
            db_ok, db_error = False, str(exc)
        return {
            "database": {"ok": db_ok, "dialect": self.db.url.split(":")[0], "error": db_error},
            "artifact_dir": str(self.artifacts.root),
            "worker": {
                "id": self.worker_id,
                "inline_running": bool(self._inline_thread and self._inline_thread.is_alive()),
            },
            "counts": {
                "intents": len(self.catalog.list_intents()),
                "testcases": len(self.catalog.list_testcases()),
                "tasks": len(self.tasks.list_tasks(limit=1000)),
                "runs": len(self.tasks.list_runs(limit=1000)),
            },
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
