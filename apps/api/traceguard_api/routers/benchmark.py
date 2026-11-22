"""Benchmark API：运行真实对照实验并读取指标（GT 不出网）。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool
from traceguard_benchmark.runner import BenchmarkRunner

from ..services import get_services

router = APIRouter(prefix="/api/v1/benchmark", tags=["benchmark"])


class RunExperimentReq(BaseModel):
    kind: str = "B"


@router.get("/experiments")
async def list_experiments(limit: int = 50) -> list[dict[str, Any]]:
    return BenchmarkRunner(get_services().rt).list(limit)


@router.get("/experiments/{experiment_id}")
async def get_experiment(experiment_id: str) -> dict[str, Any]:
    doc = BenchmarkRunner(get_services().rt).get(experiment_id)
    if not doc:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "experiment not found"})
    return doc


@router.post("/experiments", status_code=201)
async def run_experiment(req: RunExperimentReq) -> dict[str, Any]:
    runner = BenchmarkRunner(get_services().rt)
    try:
        return await run_in_threadpool(runner.run, req.kind)
    except ValueError as exc:
        raise HTTPException(422, detail={"code": "VALIDATION_ERROR", "message": str(exc)}) from exc


@router.get("/dataset")
async def dataset_info() -> dict[str, Any]:
    """只返回数据集元信息与场景 ID，不返回 Ground Truth 标签。"""
    from traceguard_benchmark.scenarios import DATASET_ID, SCENARIOS

    return {
        "dataset_id": DATASET_ID,
        "scenarios": [
            {
                "scenario_id": s.scenario_id,
                "testcase_id": s.testcase_id,
                "fault_mechanism": s.fault_mechanism,
                "description": s.description,
            }
            for s in SCENARIOS
        ],
        "ground_truth_exposed": False,
    }
