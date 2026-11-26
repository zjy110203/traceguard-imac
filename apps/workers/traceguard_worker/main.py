"""TraceGuard 独立 Worker：消费 QUEUED Run 并执行真实浏览器测试。

与 API 内嵌 Worker 语义一致（同一 TaskService + RunPipeline）：
- 租约领取 + fencing token，旧 Worker 提交被拒绝；
- 执行前回收过期租约；
- 崩溃/重启后任务可被其它 Worker 重新领取；
- SIGTERM 优雅退出（当前任务完成后停止领取）。

用法：
    python -m traceguard_worker.main --once        # 执行一个任务后退出（CI/演示）
    python -m traceguard_worker.main               # 常驻
    python -m traceguard_worker.main --poll 0.5    # 自定义轮询间隔
"""

from __future__ import annotations

import argparse
import json
import os
import resource
import signal
import socket
import sys
import time
from pathlib import Path
from types import FrameType

from traceguard_core.config import get_settings
from traceguard_core.logging import configure_logging, get_logger
from traceguard_execution.browser.manager import close_current_thread_browser
from traceguard_execution.runtime.pipeline import RunPipeline
from traceguard_execution.tasks.service import TaskService
from traceguard_persistence import ArtifactStore, CatalogService, Database, get_database
from traceguard_persistence.stores import PersistentEventStore, PersistentEvidenceManager

logger = get_logger("traceguard.worker")

_stop = False


def _handle_signal(signum: int, _frame: FrameType | None) -> None:
    global _stop
    _stop = True
    logger.info("worker_shutdown_requested", signal=signum)


def _write_metrics(worker_id: str, metrics_dir: str) -> None:
    """RUN-013 WP4：把 Worker 自身 RSS/CPU 写成快照文件，供长稳脚本采集。

    宿主机限制 `ps` 时仍可观测；失败静默（观测不得影响执行）。
    """
    if not metrics_dir:
        return
    try:
        usage = resource.getrusage(resource.RUSAGE_SELF)
        rss = int(usage.ru_maxrss)
        if sys.platform != "darwin":
            rss *= 1024  # Linux: KiB → bytes
        safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in worker_id)
        target = Path(metrics_dir) / f"worker-{safe}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(
                {
                    "worker_id": worker_id,
                    "pid": os.getpid(),
                    "rss_bytes": rss,
                    "cpu_user_s": round(float(usage.ru_utime), 3),
                    "cpu_sys_s": round(float(usage.ru_stime), 3),
                    "at": time.time(),
                }
            ),
            encoding="utf-8",
        )
        tmp.replace(target)
    except Exception:  # noqa: BLE001 - 资源观测不得影响 Worker 主循环
        return


def build_worker(worker_id: str) -> tuple[TaskService, RunPipeline]:
    settings = get_settings()
    db: Database = get_database()
    db.create_schema()
    tasks = TaskService(db)
    pipeline = RunPipeline(
        db,
        tasks,
        CatalogService(db),
        PersistentEventStore(db),
        PersistentEvidenceManager(db),
        ArtifactStore(db, root=settings.artifact_dir, backend=settings.artifact_backend),
        worker_id=worker_id,
    )
    return tasks, pipeline


def run(once: bool = False, poll_interval: float | None = None) -> int:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)
    worker_id = settings.worker_id or f"{socket.gethostname()}-{__import__('os').getpid()}"
    interval = poll_interval if poll_interval is not None else settings.worker_poll_interval_s
    tasks, pipeline = build_worker(worker_id)
    logger.info("worker_started", worker=worker_id, once=once, poll=interval)

    # RUN-018 WP8：心跳 + 告警评估（观测失败绝不影响执行主循环）
    from traceguard_persistence.alerts import AlertEngine
    from traceguard_persistence.monitoring import (
        MetricsCollector,
        upsert_worker_heartbeat,
    )
    from traceguard_persistence.db import get_database

    db = get_database()
    engine = AlertEngine(db, min_evaluate_interval_s=30.0)
    collector = MetricsCollector(db, worker_id=worker_id)
    counters = {"claims": 0, "takeovers": 0, "failures": 0}

    def beat(
        status: str,
        run_id: str | None = None,
        task_id: str | None = None,
        lease_expires_at: float = 0.0,
    ) -> None:
        try:
            upsert_worker_heartbeat(
                db,
                worker_id=worker_id,
                mode="standalone",
                status=status,
                hostname=socket.gethostname(),
                pid=os.getpid(),
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

    executed = 0
    try:
        while not _stop:
            _write_metrics(worker_id, settings.worker_metrics_dir)
            try:
                reaped = tasks.reap_expired_runs()
                if reaped:
                    logger.warning("expired_leases_reaped", count=len(reaped))
                claimed = tasks.claim_next_run(worker_id, ttl_seconds=settings.worker_lease_ttl_s)
                if not claimed:
                    beat("IDLE")
                    try:
                        engine.evaluate(collector.collect())
                    except Exception:  # noqa: BLE001
                        pass
                    if once:
                        logger.info("worker_no_task")
                        return 0
                    time.sleep(interval)
                    continue
                logger.info("run_claimed", run_id=claimed["run_id"], attempt=claimed["attempt"])
                counters["claims"] += 1
                if str(claimed.get("previous_status") or "QUEUED") != "QUEUED":
                    counters["takeovers"] += 1
                beat(
                    "RUNNING",
                    run_id=claimed["run_id"],
                    task_id=claimed.get("task_id"),
                    lease_expires_at=float(claimed.get("lease_expires_at") or 0.0),
                )
                outcome = pipeline.execute(claimed["run_id"], lease=claimed)
                executed += 1
                if outcome.execution_status in ("FAILED", "TIMED_OUT"):
                    counters["failures"] += 1
                beat("IDLE")
                _write_metrics(worker_id, settings.worker_metrics_dir)
                logger.info(
                    "run_finished",
                    run_id=outcome.run_id,
                    verdict=outcome.test_verdict,
                    status=outcome.execution_status,
                )
                if once:
                    return 0
            except KeyboardInterrupt:  # pragma: no cover
                break
            except Exception:  # noqa: BLE001 - Worker 不得因单任务失败退出
                logger.exception("worker_iteration_failed")
                counters["failures"] += 1
                beat("IDLE")
                if once:
                    return 1
                time.sleep(max(1.0, interval))
    finally:
        beat("STOPPED")
        close_current_thread_browser()
        logger.info("worker_stopped", executed=executed)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="TraceGuard Worker")
    parser.add_argument("--once", action="store_true", help="执行一个任务后退出")
    parser.add_argument("--poll", type=float, default=None, help="空闲轮询间隔（秒）")
    args = parser.parse_args(argv)
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)
    return run(once=args.once, poll_interval=args.poll)


if __name__ == "__main__":
    sys.exit(main())
