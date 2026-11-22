"""Task / Run / Step 服务：持久化 + 状态机 + 租约 fencing + 队列领取。

不变量（来自资料/06 与设计基线）：
- Task 是逻辑任务，Run 是执行尝试；重试产生新 Run，不覆盖历史；
- `execution_status` 与 `test_verdict` 独立；
- Run 有租约（lease_owner / lease_expires_at）与执行代次（fencing_token），旧代次提交被拒绝；
- 租约过期后 Run 可被重新领取，fencing_token 递增，旧 Worker 的心跳与提交都会失败。
"""

from __future__ import annotations

import time
from typing import Any

from sqlalchemy import select
from traceguard_core.errors import AppError, ErrorCode
from traceguard_core.ids import new_id
from traceguard_persistence.db import Database, get_database
from traceguard_persistence.models import RunRow, StepRow, TaskRow

TASK_TRANSITIONS: dict[str, set[str]] = {
    "CREATED": {"QUEUED", "CANCELLED"},
    "QUEUED": {"RUNNING", "CANCELLED"},
    "RUNNING": {"WAITING", "COMPLETED", "FAILED", "CANCELLED"},
    "WAITING": {"RUNNING", "CANCELLED"},
    "COMPLETED": set(),
    "FAILED": set(),
    "CANCELLED": set(),
}
RUN_TRANSITIONS: dict[str, set[str]] = {
    "QUEUED": {"PREPARING", "CANCELLED"},
    "PREPARING": {"RUNNING", "FAILED", "TIMED_OUT", "CANCELLED"},
    "RUNNING": {"FINALIZING", "FAILED", "TIMED_OUT", "CANCELLED"},
    "FINALIZING": {"COMPLETED", "FAILED"},
    "COMPLETED": set(),
    "FAILED": set(),
    "TIMED_OUT": set(),
    "CANCELLED": set(),
}
FINAL_RUN_STATES = {"COMPLETED", "FAILED", "TIMED_OUT", "CANCELLED"}


class TaskService:
    def __init__(self, db: Database | None = None) -> None:
        self.db = db or get_database()
        self.db.create_schema()

    # ------------------------------------------------------------------ Task
    def create_task(
        self,
        testcase_ref: str,
        idempotency_key: str,
        application: str = "teamflow",
        *,
        testcase_version_id: str = "",
        mode: str = "playwright",
        workspace_id: str = "default",
    ) -> dict[str, Any]:
        with self.db.session() as s:
            existing = s.scalar(select(TaskRow).where(TaskRow.idempotency_key == idempotency_key))
            if existing:
                return {"task_id": existing.task_id, "idempotent_hit": True}
            task_id = new_id("task")
            s.add(
                TaskRow(
                    task_id=task_id,
                    workspace_id=workspace_id,
                    testcase_ref=testcase_ref,
                    testcase_version_id=testcase_version_id,
                    application=application,
                    status="CREATED",
                    idempotency_key=idempotency_key,
                    mode=mode,
                )
            )
            return {"task_id": task_id, "idempotent_hit": False}

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        with self.db.session() as s:
            r = s.get(TaskRow, task_id)
            return self._task_dict(r) if r else None

    def transition_task(self, task_id: str, target: str) -> None:
        with self.db.session() as s:
            r = s.get(TaskRow, task_id)
            if not r:
                raise AppError(ErrorCode.NOT_FOUND, f"task {task_id} not found")
            if target != r.status and target not in TASK_TRANSITIONS.get(r.status, set()):
                raise AppError(ErrorCode.CONFLICT, f"illegal task transition {r.status}->{target}")
            r.status = target
            r.updated_at = time.time()

    def cancel_task(self, task_id: str) -> dict[str, Any]:
        with self.db.session() as s:
            r = s.get(TaskRow, task_id)
            if not r:
                raise AppError(ErrorCode.NOT_FOUND, f"task {task_id} not found")
            if r.status not in {"CREATED", "QUEUED", "RUNNING", "WAITING"}:
                raise AppError(ErrorCode.CONFLICT, f"task in final state {r.status}")
            r.status = "CANCELLED"
            r.updated_at = time.time()
            for run in s.scalars(
                select(RunRow).where(
                    RunRow.task_id == task_id, RunRow.execution_status.notin_(FINAL_RUN_STATES)
                )
            ).all():
                run.execution_status = "CANCELLED"
            return {"task_id": task_id, "status": "CANCELLED"}

    def list_tasks(
        self,
        workspace_id: str | None = None,
        status: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        with self.db.session() as s:
            q = select(TaskRow)
            if workspace_id:
                q = q.where(TaskRow.workspace_id == workspace_id)
            if status:
                q = q.where(TaskRow.status == status)
            rows = s.scalars(q.order_by(TaskRow.created_at.desc()).limit(limit)).all()
            return [self._task_dict(r) for r in rows]

    @staticmethod
    def _task_dict(r: TaskRow) -> dict[str, Any]:
        return {
            "task_id": r.task_id,
            "workspace_id": r.workspace_id,
            "testcase_ref": r.testcase_ref,
            "testcase_version_id": r.testcase_version_id,
            "application": r.application,
            "status": r.status,
            "mode": r.mode,
            "idempotency_key": r.idempotency_key,
            "created_at": r.created_at,
        }

    # ------------------------------------------------------------------- Run
    def create_run(
        self,
        task_id: str,
        testcase_version_id: str,
        *,
        attempt: int | None = None,
        auto_queue: bool = True,
    ) -> dict[str, Any]:
        """每次调用产生新 Run（重试语义），不覆盖历史。"""
        with self.db.session() as s:
            task = s.get(TaskRow, task_id)
            if not task:
                raise AppError(ErrorCode.NOT_FOUND, f"task {task_id} not found")
            prev = s.scalar(
                select(RunRow)
                .where(RunRow.task_id == task_id)
                .order_by(RunRow.attempt.desc())
                .limit(1)
            )
            next_attempt = attempt if attempt is not None else (prev.attempt + 1 if prev else 1)
            run_id = new_id("run")
            s.add(
                RunRow(
                    run_id=run_id,
                    task_id=task_id,
                    workspace_id=task.workspace_id,
                    attempt=next_attempt,
                    testcase_version_id=testcase_version_id or task.testcase_version_id,
                    execution_status="QUEUED",
                    test_verdict="NOT_EVALUATED",
                    fencing_token=0,
                )
            )
            if auto_queue and task.status == "CREATED":
                task.status = "QUEUED"
                task.updated_at = time.time()
            return {
                "run_id": run_id,
                "attempt": next_attempt,
                "execution_status": "QUEUED",
                "observer_mode": task.mode,
            }

    def acquire_lease(
        self, run_id: str, worker_id: str, ttl_seconds: float = 60.0
    ) -> dict[str, Any]:
        """获取执行租约：fencing_token 单调递增；仅在非终态且租约不活跃时可获取。"""
        with self.db.session() as s:
            r = s.get(RunRow, run_id)
            if not r:
                raise AppError(ErrorCode.NOT_FOUND, f"run {run_id} not found")
            now = time.time()
            lease_active = bool(r.lease_owner) and r.lease_expires_at > now
            if r.execution_status in FINAL_RUN_STATES or r.execution_status == "FINALIZING":
                raise AppError(ErrorCode.CONFLICT, "run not acquirable (final)")
            if lease_active:
                raise AppError(ErrorCode.CONFLICT, "run not acquirable (lease active)")
            r.fencing_token += 1
            r.lease_owner = worker_id
            r.lease_expires_at = now + ttl_seconds
            r.execution_status = "PREPARING"
            r.started_at = r.started_at or now
            task = s.get(TaskRow, r.task_id)
            if task and task.status in {"CREATED", "QUEUED"}:
                task.status = "RUNNING"
                task.updated_at = now
            return {
                "fencing_token": r.fencing_token,
                "lease_expires_at": r.lease_expires_at,
                "attempt": r.attempt,
                "workspace_id": r.workspace_id,
                "task_id": r.task_id,
            }

    def renew_lease(
        self, run_id: str, worker_id: str, fencing_token: int, ttl_seconds: float = 60.0
    ) -> bool:
        with self.db.session() as s:
            r = s.get(RunRow, run_id)
            if not r or r.fencing_token != fencing_token or r.lease_owner != worker_id:
                return False
            r.lease_expires_at = time.time() + ttl_seconds
            return True

    def claim_next_run(self, worker_id: str, ttl_seconds: float = 120.0) -> dict[str, Any] | None:
        """Worker 领取：取最早排队的 Run；租约过期未提交的 Run 也会被回收领取。"""
        now = time.time()
        with self.db.session() as s:
            candidates = s.scalars(
                select(RunRow)
                .where(
                    RunRow.execution_status.in_(["QUEUED", "PREPARING", "RUNNING"]),
                    (RunRow.lease_expires_at < now) | (RunRow.lease_owner.is_(None)),
                )
                .order_by(RunRow.created_at)
                .limit(5)
            ).all()
            target = None
            for r in candidates:
                if r.execution_status == "QUEUED" or not r.lease_owner:
                    target = r
                    break
                if r.lease_expires_at < now:
                    target = r
                    break
            if target is None:
                return None
            target.fencing_token += 1
            target.lease_owner = worker_id
            target.lease_expires_at = now + ttl_seconds
            previous = target.execution_status
            target.execution_status = "PREPARING"
            target.started_at = target.started_at or now
            task = s.get(TaskRow, target.task_id)
            if task and task.status in {"CREATED", "QUEUED"}:
                task.status = "RUNNING"
                task.updated_at = now
            return {
                "run_id": target.run_id,
                "task_id": target.task_id,
                "workspace_id": target.workspace_id,
                "attempt": target.attempt,
                "fencing_token": target.fencing_token,
                "previous_status": previous,
                "lease_expires_at": target.lease_expires_at,
            }

    def reap_expired_runs(self) -> list[dict[str, Any]]:
        """把租约过期的未完成 Run 标记为可重新领取（不改变执行历史）。"""
        now = time.time()
        reaped: list[dict[str, Any]] = []
        with self.db.session() as s:
            rows = s.scalars(
                select(RunRow).where(
                    RunRow.execution_status.in_(["PREPARING", "RUNNING"]),
                    RunRow.lease_expires_at < now,
                )
            ).all()
            for r in rows:
                reaped.append(
                    {
                        "run_id": r.run_id,
                        "fencing_token": r.fencing_token,
                        "lease_owner": r.lease_owner,
                        "status": r.execution_status,
                    }
                )
                r.lease_owner = None
                r.lease_expires_at = 0.0
                r.execution_status = "QUEUED"
        return reaped

    def submit_result(
        self,
        run_id: str,
        worker_id: str,
        fencing_token: int,
        *,
        execution_status: str,
        test_verdict: str,
        report: dict[str, Any] | None = None,
        observer_mode: str | None = None,
        error: str | None = None,
    ) -> None:
        """提交结果必须校验执行代次；旧代次被拒绝。execution_status 与 verdict 独立写入。"""
        with self.db.session() as s:
            r = s.get(RunRow, run_id)
            if not r:
                raise AppError(ErrorCode.NOT_FOUND, f"run {run_id} not found")
            if r.fencing_token != fencing_token or r.lease_owner != worker_id:
                raise AppError(
                    ErrorCode.CONFLICT, "STALE_FENCING_TOKEN: old worker submit rejected"
                )
            if (
                execution_status != r.execution_status
                and execution_status not in RUN_TRANSITIONS.get(r.execution_status, set())
                and not (execution_status == "COMPLETED" and r.execution_status == "RUNNING")
            ):
                raise AppError(
                    ErrorCode.CONFLICT,
                    f"illegal run transition {r.execution_status}->{execution_status}",
                )
            r.execution_status = execution_status
            r.test_verdict = test_verdict
            r.report_json = report
            r.observer_mode = observer_mode or r.observer_mode
            r.error = error
            r.ended_at = time.time()
            r.lease_expires_at = 0.0
            task = s.get(TaskRow, r.task_id)
            if task and task.status in {"RUNNING", "WAITING"}:
                task.status = "COMPLETED" if execution_status == "COMPLETED" else "FAILED"
                task.updated_at = time.time()

    def mark_running(
        self, run_id: str, worker_id: str, fencing_token: int, observer_mode: str
    ) -> None:
        with self.db.session() as s:
            r = s.get(RunRow, run_id)
            if not r or r.fencing_token != fencing_token or r.lease_owner != worker_id:
                raise AppError(ErrorCode.CONFLICT, "STALE_FENCING_TOKEN")
            if "RUNNING" in RUN_TRANSITIONS.get(r.execution_status, set()):
                r.execution_status = "RUNNING"
            r.observer_mode = observer_mode

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self.db.session() as s:
            r = s.get(RunRow, run_id)
            return self._run_dict(r) if r else None

    def list_runs(
        self,
        task_id: str | None = None,
        *,
        workspace_id: str | None = None,
        status: str | None = None,
        verdict: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        with self.db.session() as s:
            q = select(RunRow)
            if task_id:
                q = q.where(RunRow.task_id == task_id)
            if workspace_id:
                q = q.where(RunRow.workspace_id == workspace_id)
            if status:
                q = q.where(RunRow.execution_status == status)
            if verdict:
                q = q.where(RunRow.test_verdict == verdict)
            rows = s.scalars(q.order_by(RunRow.created_at.desc()).limit(limit)).all()
            return [self._run_dict(r) for r in rows]

    @staticmethod
    def _run_dict(r: RunRow) -> dict[str, Any]:
        return {
            "run_id": r.run_id,
            "task_id": r.task_id,
            "workspace_id": r.workspace_id,
            "attempt": r.attempt,
            "testcase_version_id": r.testcase_version_id,
            "execution_status": r.execution_status,
            "test_verdict": r.test_verdict,
            "observer_mode": r.observer_mode,
            "fencing_token": r.fencing_token,
            "lease_owner": r.lease_owner,
            "lease_expires_at": r.lease_expires_at,
            "report": r.report_json,
            "error": r.error,
            "created_at": r.created_at,
            "started_at": r.started_at,
            "ended_at": r.ended_at,
        }

    # ------------------------------------------------------------------ Step
    def record_step(
        self,
        run_id: str,
        step_id: str,
        index: int,
        status: str = "PENDING",
        error: str | None = None,
        *,
        action: str = "",
        duration_ms: int = 0,
        observation: dict[str, Any] | None = None,
    ) -> None:
        with self.db.session() as s:
            existing = s.get(StepRow, step_id)
            if existing:
                existing.status = status
                existing.error = error
                existing.action = action or existing.action
                existing.duration_ms = duration_ms or existing.duration_ms
                if observation is not None:
                    existing.observation = observation
            else:
                s.add(
                    StepRow(
                        step_id=step_id,
                        run_id=run_id,
                        index=index,
                        status=status,
                        error=error,
                        action=action,
                        duration_ms=duration_ms,
                        observation=observation or {},
                    )
                )

    def list_steps(self, run_id: str) -> list[dict[str, Any]]:
        with self.db.session() as s:
            rows = s.scalars(
                select(StepRow).where(StepRow.run_id == run_id).order_by(StepRow.index)
            ).all()
            return [
                {
                    "step_id": r.step_id,
                    "run_id": r.run_id,
                    "index": r.index,
                    "action": r.action,
                    "status": r.status,
                    "error": r.error,
                    "duration_ms": r.duration_ms,
                    "observation": dict(r.observation or {}),
                }
                for r in rows
            ]

    # --------------------------------------------------------------- Summary
    def task_summary(self, task_id: str) -> dict[str, Any]:
        """Task 级展示汇总（不得冒充单次 Run 的 Test Verdict）。"""
        runs = self.list_runs(task_id)
        verdicts: dict[str, int] = {}
        for r in runs:
            verdicts[r["test_verdict"]] = verdicts.get(r["test_verdict"], 0) + 1
        latest = runs[0] if runs else None
        return {
            "task_id": task_id,
            "run_count": len(runs),
            "verdicts": verdicts,
            "latest_run": latest,
            "note": "展示汇总，非单次 Run 的 Test Verdict",
        }

    def queue_stats(self) -> dict[str, Any]:
        with self.db.session() as s:
            queued = s.scalar(select(RunRow).where(RunRow.execution_status == "QUEUED").limit(1))
            running = s.scalar(select(RunRow).where(RunRow.execution_status == "RUNNING").limit(1))
            total = len(s.scalars(select(RunRow.run_id)).all())
        return {
            "queued_present": queued is not None,
            "running_present": running is not None,
            "total_runs": total,
        }
