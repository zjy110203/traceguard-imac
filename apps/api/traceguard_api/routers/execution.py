"""平台执行 API（阶段3）：Task/Run/Step 的 HTTP 管理入口。

挂到 FastAPI app。SSE /events/stream 已在 main.py 预留；阶段3 用轮询查询即可验证状态。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel
from traceguard_execution.tasks.service import TaskService

router = APIRouter(prefix="/api/v1", tags=["execution"])
_svc = TaskService()


class CreateTaskReq(BaseModel):
    testcase_ref: str
    idempotency_key: str
    application: str = "teamflow"


@router.post("/tasks")
async def create_task(req: CreateTaskReq) -> dict[str, Any]:
    return _svc.create_task(req.testcase_ref, req.idempotency_key, req.application)


@router.get("/tasks")
async def list_tasks() -> list[dict[str, Any]]:
    return _svc.list_tasks()


@router.get("/tasks/{task_id}")
async def get_task(task_id: str) -> dict[str, Any] | None:
    return _svc.get_task(task_id)


@router.post("/tasks/{task_id}/runs")
async def create_run(task_id: str) -> dict[str, Any]:
    return _svc.create_run(task_id, "tcase_version_ref")


@router.get("/tasks/{task_id}/runs")
async def list_runs(task_id: str) -> list[dict[str, Any]]:
    return _svc.list_runs(task_id)


@router.get("/runs/{run_id}")
async def get_run(run_id: str) -> dict[str, Any] | None:
    return _svc.get_run(run_id)


@router.get("/runs/{run_id}/steps")
async def list_steps(run_id: str) -> list[dict[str, Any]]:
    return _svc.list_steps(run_id)


@router.get("/tasks/{task_id}/summary")
async def task_summary(task_id: str) -> dict[str, Any]:
    return _svc.task_summary(task_id)
