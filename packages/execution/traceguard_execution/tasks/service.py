"""Task Service / Execution Orchestrator（阶段3，同步内核 + 持久化 + 租约代次）。

实现：
- Task 创建（幂等）、查询、取消；
- Run 排队、租约获取（fencing token 单调递增）、心跳续租、状态提交（校验代次）；
- 重试创建新 Run（不覆盖历史）；
- 状态机约束（来自资料/06）。

Celery/Redis 为可选异步增强，本机环境不启用；核心语义在此确定性实现并可测。
"""

from __future__ import annotations

import json
import time
from typing import Any

from traceguard_core.errors import AppError, ErrorCode
from traceguard_core.ids import new_id

from .store import ExecSession, RunRow, StepRow, TaskRow, init_exec_db

# 合法状态转换（资料/06）
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


class TaskService:
    def __init__(self) -> None:
        init_exec_db()

    # ---------- Task ----------
    def create_task(
        self, testcase_ref: str, idempotency_key: str, application: str = "teamflow"
    ) -> dict[str, Any]:
        with ExecSession() as s:
            existing = s.query(TaskRow).filter_by(idempotency_key=idempotency_key).first()
            if existing:
                return {"task_id": existing.task_id, "idempotent_hit": True}
            task_id = new_id("task")
            s.add(
                TaskRow(
                    task_id=task_id,
                    testcase_ref=testcase_ref,
                    application=application,
                    status="CREATED",
                    idempotency_key=idempotency_key,
                )
            )
            s.commit()
            return {"task_id": task_id, "idempotent_hit": False}

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        with ExecSession() as s:
            r = s.query(TaskRow).filter_by(task_id=task_id).first()
            return self._task_dict(r) if r else None

    def transition_task(self, task_id: str, target: str) -> None:
        with ExecSession() as s:
            r = s.query(TaskRow).filter_by(task_id=task_id).first()
            if not r:
                raise AppError(ErrorCode.NOT_FOUND, f"task {task_id} not found")
            if target not in TASK_TRANSITIONS.get(r.status, set()):
                raise AppError(ErrorCode.CONFLICT, f"illegal task transition {r.status}->{target}")
            r.status = target
            s.commit()

    def list_tasks(self) -> list[dict[str, Any]]:
        with ExecSession() as s:
            return [self._task_dict(r) for r in s.query(TaskRow).order_by(TaskRow.created_at).all()]

    @staticmethod
    def _task_dict(r: TaskRow) -> dict[str, Any]:
        return {
            "task_id": r.task_id,
            "testcase_ref": r.testcase_ref,
            "application": r.application,
            "status": r.status,
            "idempotency_key": r.idempotency_key,
        }

    # ---------- Run ----------
    def create_run(
        self, task_id: str, testcase_version_id: str, *, attempt: int | None = None
    ) -> dict[str, Any]:
        """每次调用产生新 Run（重试语义），不覆盖历史。"""
        with ExecSession() as s:
            task = s.query(TaskRow).filter_by(task_id=task_id).first()
            if not task:
                raise AppError(ErrorCode.NOT_FOUND, f"task {task_id} not found")
            prev = (
                s.query(RunRow).filter_by(task_id=task_id).order_by(RunRow.attempt.desc()).first()
            )
            next_attempt = attempt if attempt is not None else (prev.attempt + 1 if prev else 1)
            run = RunRow(
                run_id=new_id("run"),
                task_id=task_id,
                attempt=next_attempt,
                testcase_version_id=testcase_version_id,
                execution_status="QUEUED",
                test_verdict="NOT_EVALUATED",
                fencing_token=0,
            )
            s.add(run)
            s.commit()
            return {"run_id": run.run_id, "attempt": next_attempt, "execution_status": "QUEUED"}

    def acquire_lease(
        self, run_id: str, worker_id: str, ttl_seconds: float = 30.0
    ) -> dict[str, Any]:
        """获取执行租约：fencing_token 单调递增；仅在 QUEUED 或租约过期时可获取。"""
        with ExecSession() as s:
            r = s.query(RunRow).filter_by(run_id=run_id).first()
            if not r:
                raise AppError(ErrorCode.NOT_FOUND, f"run {run_id} not found")
            now = time.time()
            lease_active = r.lease_owner not in (None, "") and r.lease_expires_at > now
            final = r.execution_status in ("COMPLETED", "FAILED", "TIMED_OUT", "CANCELLED")
            if final or lease_active:
                raise AppError(ErrorCode.CONFLICT, "run not acquirable (final or lease active)")
            r.fencing_token += 1
            r.lease_owner = worker_id
            r.lease_expires_at = now + ttl_seconds
            r.execution_status = "PREPARING"
            s.commit()
            return {"fencing_token": r.fencing_token, "lease_expires_at": r.lease_expires_at}

    def renew_lease(
        self, run_id: str, worker_id: str, fencing_token: int, ttl_seconds: float = 30.0
    ) -> bool:
        with ExecSession() as s:
            r = s.query(RunRow).filter_by(run_id=run_id).first()
            if not r or r.fencing_token != fencing_token or r.lease_owner != worker_id:
                return False  # 失去执行权
            r.lease_expires_at = time.time() + ttl_seconds
            s.commit()
            return True

    def submit_result(
        self,
        run_id: str,
        worker_id: str,
        fencing_token: int,
        *,
        execution_status: str,
        test_verdict: str,
        report: dict[str, Any] | None = None,
    ) -> None:
        """提交结果必须校验执行代次；旧代次被拒绝（F06）。execution_status 与 verdict 独立写入。"""
        with ExecSession() as s:
            r = s.query(RunRow).filter_by(run_id=run_id).first()
            if not r:
                raise AppError(ErrorCode.NOT_FOUND, f"run {run_id} not found")
            if r.fencing_token != fencing_token or r.lease_owner != worker_id:
                raise AppError(
                    ErrorCode.CONFLICT, "STALE_FENCING_TOKEN: old worker submit rejected"
                )
            if (
                execution_status not in RUN_TRANSITIONS.get(r.execution_status, set())
                and execution_status != r.execution_status
            ):
                raise AppError(
                    ErrorCode.CONFLICT,
                    f"illegal run transition {r.execution_status}->{execution_status}",
                )
            r.execution_status = execution_status
            r.test_verdict = test_verdict  # 独立字段，不由 status 推导
            r.report_json = json.dumps(report, ensure_ascii=False) if report else None
            s.commit()

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with ExecSession() as s:
            r = s.query(RunRow).filter_by(run_id=run_id).first()
            return self._run_dict(r) if r else None

    def list_runs(self, task_id: str | None = None) -> list[dict[str, Any]]:
        with ExecSession() as s:
            q = s.query(RunRow)
            if task_id:
                q = q.filter_by(task_id=task_id)
            return [self._run_dict(r) for r in q.order_by(RunRow.created_at).all()]

    @staticmethod
    def _run_dict(r: RunRow) -> dict[str, Any]:
        return {
            "run_id": r.run_id,
            "task_id": r.task_id,
            "attempt": r.attempt,
            "testcase_version_id": r.testcase_version_id,
            "execution_status": r.execution_status,
            "test_verdict": r.test_verdict,
            "fencing_token": r.fencing_token,
            "lease_owner": r.lease_owner,
            "report": json.loads(r.report_json) if r.report_json else None,
        }

    # ---------- Step ----------
    def record_step(
        self,
        run_id: str,
        step_id: str,
        index: int,
        status: str = "PENDING",
        error: str | None = None,
    ) -> None:
        with ExecSession() as s:
            existing = s.query(StepRow).filter_by(step_id=step_id).first()
            if existing:
                existing.status = status
                existing.error = error
            else:
                s.add(
                    StepRow(step_id=step_id, run_id=run_id, index=index, status=status, error=error)
                )
            s.commit()

    def list_steps(self, run_id: str) -> list[dict[str, Any]]:
        with ExecSession() as s:
            return [
                {
                    "step_id": r.step_id,
                    "run_id": r.run_id,
                    "index": r.index,
                    "status": r.status,
                    "error": r.error,
                }
                for r in s.query(StepRow).filter_by(run_id=run_id).order_by(StepRow.index).all()
            ]

    def task_summary(self, task_id: str) -> dict[str, Any]:
        """Task 级展示汇总（不得冒充单次 Run 的 Test Verdict）。"""
        runs = self.list_runs(task_id)
        return {
            "task_id": task_id,
            "run_count": len(runs),
            "verdicts": [r["test_verdict"] for r in runs],
            "note": "展示汇总，非单次 Run 的 Test Verdict",
        }
