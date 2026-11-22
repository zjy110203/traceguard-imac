"""执行 API：Task / Run / Step / 执行触发 / 队列状态。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool
from traceguard_core.errors import AppError

from ..services import get_services

router = APIRouter(prefix="/api/v1", tags=["execution"])


def _http(exc: AppError) -> HTTPException:
    mapping = {
        "NOT_FOUND": 404,
        "CONFLICT": 409,
        "PRECONDITION_FAILED": 412,
        "VALIDATION_ERROR": 422,
        "AUTHORIZATION_ERROR": 403,
    }
    return HTTPException(
        status_code=mapping.get(exc.code.value, 400),
        detail={"code": exc.code.value, "message": exc.message},
    )


class CreateTaskReq(BaseModel):
    testcase_ref: str
    idempotency_key: str | None = None
    application: str = "teamflow"
    version: int | None = None
    mode: str = "playwright"


class ExecuteReq(BaseModel):
    mode: str | None = None
    sync: bool = False


def _resolve_version(
    testcase_id: str, version: int | None, *, require_approved: bool = True
) -> int:
    rt = get_services().rt
    tc = rt.catalog.get_testcase(testcase_id)
    if not tc:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "testcase not found"})
    if version is None:
        approved = rt.catalog.latest_approved_version(testcase_id)
        if not approved:
            raise HTTPException(
                412,
                detail={
                    "code": "TESTCASE_NOT_APPROVED",
                    "message": "用例尚无已审核版本：请先在用例详情中审核通过",
                },
            )
        return int(approved["version"])
    doc = rt.catalog.get_version(testcase_id, version)
    if not doc:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "version not found"})
    if require_approved and doc.get("review_status") != "APPROVED":
        raise HTTPException(
            412,
            detail={
                "code": "TESTCASE_NOT_APPROVED",
                "message": f"版本 v{version} 未审核通过，禁止正式执行",
            },
        )
    return version


@router.post("/tasks", status_code=201)
async def create_task(req: CreateTaskReq) -> dict[str, Any]:
    rt = get_services().rt
    version = _resolve_version(req.testcase_ref, req.version)
    key = req.idempotency_key or f"api-{req.testcase_ref}-{__import__('time').time_ns()}"
    try:
        out = rt.tasks.create_task(
            req.testcase_ref,
            key,
            req.application,
            testcase_version_id=f"{req.testcase_ref}@{version}",
            mode=req.mode,
        )
    except AppError as exc:
        raise _http(exc) from exc
    out["testcase_version"] = version
    return out


@router.get("/tasks")
async def list_tasks(
    workspace_id: str | None = None, status: str | None = None, limit: int = 200
) -> list[dict[str, Any]]:
    return get_services().rt.tasks.list_tasks(workspace_id, status, limit)


@router.get("/tasks/{task_id}")
async def get_task(task_id: str) -> dict[str, Any]:
    doc = get_services().rt.tasks.get_task(task_id)
    if not doc:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "task not found"})
    return doc


@router.post("/tasks/{task_id}/runs", status_code=201)
async def create_run(task_id: str, version: int | None = None) -> dict[str, Any]:
    rt = get_services().rt
    task = rt.tasks.get_task(task_id)
    if not task:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "task not found"})
    tc_id = task["testcase_ref"]
    v = _resolve_version(
        tc_id, version or task.get("testcase_version_id", "").split("@")[-1] or None
    )
    try:
        return rt.tasks.create_run(task_id, f"{tc_id}@{v}")
    except AppError as exc:
        raise _http(exc) from exc


@router.post("/tasks/{task_id}/cancel")
async def cancel_task(task_id: str) -> dict[str, Any]:
    try:
        return get_services().rt.tasks.cancel_task(task_id)
    except AppError as exc:
        raise _http(exc) from exc


@router.get("/tasks/{task_id}/runs")
async def list_task_runs(task_id: str) -> list[dict[str, Any]]:
    return get_services().rt.tasks.list_runs(task_id)


@router.get("/tasks/{task_id}/summary")
async def task_summary(task_id: str) -> dict[str, Any]:
    return get_services().rt.tasks.task_summary(task_id)


@router.post("/testcases/{testcase_id}/execute", status_code=201)
async def execute_testcase(
    testcase_id: str, req: ExecuteReq | None = None, version: int | None = None
) -> dict[str, Any]:
    """一键执行：创建 Task + Run（默认入队，由 Worker 消费）。"""
    rt = get_services().rt
    req = req or ExecuteReq()
    v = _resolve_version(testcase_id, version)
    task = rt.tasks.create_task(
        testcase_id,
        f"exec-{testcase_id}-{__import__('time').time_ns()}",
        testcase_version_id=f"{testcase_id}@{v}",
        mode=req.mode or "playwright",
    )
    run = rt.tasks.create_run(task["task_id"], f"{testcase_id}@{v}")
    result: dict[str, Any] = {
        "task_id": task["task_id"],
        "run_id": run["run_id"],
        "testcase_id": testcase_id,
        "version": v,
        "status": "QUEUED",
    }
    if req.sync:
        # Playwright sync API 不能运行在事件循环里：执行必须放到线程池
        result["execution"] = await run_in_threadpool(rt.execute_run, run["run_id"])
        result["status"] = result["execution"]["execution_status"]
    return result


@router.post("/runs/{run_id}/execute")
async def execute_run(run_id: str) -> dict[str, Any]:
    """同步执行（本地调试/演示）；生产建议启动独立 Worker。"""
    try:
        return await run_in_threadpool(get_services().rt.execute_run, run_id)
    except AppError as exc:
        raise _http(exc) from exc


@router.get("/runs")
async def list_runs(
    workspace_id: str | None = None,
    status: str | None = None,
    verdict: str | None = None,
    limit: int = Query(100, le=500),
) -> list[dict[str, Any]]:
    return get_services().rt.tasks.list_runs(
        workspace_id=workspace_id, status=status, verdict=verdict, limit=limit
    )


@router.get("/runs/{run_id}")
async def get_run(run_id: str) -> dict[str, Any]:
    doc = get_services().rt.tasks.get_run(run_id)
    if not doc:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "run not found"})
    return doc


@router.get("/runs/{run_id}/steps")
async def list_steps(run_id: str) -> list[dict[str, Any]]:
    return get_services().rt.tasks.list_steps(run_id)


@router.get("/runs/{run_id}/report")
async def get_report(run_id: str) -> dict[str, Any]:
    doc = get_services().rt.tasks.get_run(run_id)
    if not doc:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "run not found"})
    return doc.get("report") or {"message": "run 尚未产生报告"}


@router.get("/runs/{run_id}/evidence")
async def run_evidence(run_id: str) -> list[dict[str, Any]]:
    evds = get_services().rt.evidence.list(run_id=run_id, limit=500)
    return [e.to_dict() for e in evds]


@router.get("/runs/{run_id}/events")
async def run_events(run_id: str, limit: int = 500) -> list[dict[str, Any]]:
    events = get_services().rt.events.list_events(run_id=run_id, limit=limit)
    return [e.to_dict() for e in events]


@router.get("/queue/stats")
async def queue_stats() -> dict[str, Any]:
    rt = get_services().rt
    runs = rt.tasks.list_runs(limit=500)
    counts: dict[str, int] = {}
    for r in runs:
        counts[r["execution_status"]] = counts.get(r["execution_status"], 0) + 1
    return {
        "by_status": counts,
        "total": len(runs),
        "worker": {
            "id": rt.worker_id,
            "inline_running": bool(rt._inline_thread and rt._inline_thread.is_alive()),
        },
    }


@router.post("/queue/reap")
async def reap_expired() -> dict[str, Any]:
    reaped = get_services().rt.tasks.reap_expired_runs()
    return {"reaped": reaped, "count": len(reaped)}
