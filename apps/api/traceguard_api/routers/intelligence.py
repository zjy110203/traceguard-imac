"""质量智能 API：Diagnosis / Disposition / Evidence Graph / Probe / Repair。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool
from traceguard_core.errors import AppError

from ..services import get_services

router = APIRouter(prefix="/api/v1", tags=["intelligence"])


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


# ----------------------------------------------------------------- exploration
@router.post("/intents/{intent_id}/explore", status_code=201)
async def explore_intent(intent_id: str, max_steps: int = 8) -> dict[str, Any]:
    """Agent 真实探索（只读工具 + 预算 + 域名白名单），产出可复现轨迹。"""
    try:
        return await run_in_threadpool(get_services().exploration.explore, intent_id, max_steps)
    except AppError as exc:
        raise _http(exc) from exc


@router.get("/explorations")
async def list_explorations(
    workspace_id: str | None = None, limit: int = 50
) -> list[dict[str, Any]]:
    return get_services().exploration.list(workspace_id, limit)


@router.get("/explorations/{exploration_id}")
async def get_exploration(exploration_id: str) -> dict[str, Any]:
    doc = get_services().exploration.get(exploration_id)
    if not doc:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "exploration not found"})
    return doc


@router.post("/explorations/{exploration_id}/compile", status_code=201)
async def compile_exploration(exploration_id: str) -> dict[str, Any]:
    """轨迹 → 候选用例（PENDING，必须人工审核后才能执行）。"""
    try:
        return await run_in_threadpool(get_services().exploration.compile, exploration_id)
    except AppError as exc:
        raise _http(exc) from exc


# ------------------------------------------------------------------ diagnosis
@router.post("/runs/{run_id}/diagnose", status_code=201)
async def diagnose(run_id: str) -> dict[str, Any]:
    try:
        return get_services().diagnosis.diagnose(run_id)
    except AppError as exc:
        raise _http(exc) from exc


@router.get("/diagnoses")
async def list_diagnoses(
    workspace_id: str | None = None, run_id: str | None = None, limit: int = 100
) -> list[dict[str, Any]]:
    return get_services().diagnosis.list_diagnoses(workspace_id, run_id, limit)


@router.get("/diagnoses/{diagnosis_id}")
async def get_diagnosis(diagnosis_id: str) -> dict[str, Any]:
    doc = get_services().diagnosis.get(diagnosis_id)
    if not doc:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "diagnosis not found"})
    return doc


@router.get("/dispositions")
async def list_dispositions(
    workspace_id: str | None = None, status: str | None = None, limit: int = 100
) -> list[dict[str, Any]]:
    recs = get_services().rt.docs.list(
        kind="disposition", workspace_id=workspace_id, status=status, limit=limit
    )
    return [r.to_dict() for r in recs]


class DispositionReviewReq(BaseModel):
    review_status: str
    reviewer: str
    note: str = ""


@router.post("/dispositions/{disposition_id}/review")
async def review_disposition(disposition_id: str, req: DispositionReviewReq) -> dict[str, Any]:
    if req.review_status not in {"APPROVED", "REJECTED", "PENDING"}:
        raise HTTPException(422, detail={"code": "VALIDATION_ERROR", "message": "invalid status"})
    if any(x in req.reviewer.lower() for x in ("agent", "llm")):
        raise HTTPException(
            403, detail={"code": "AUTHORIZATION_ERROR", "message": "需具名人类审核"}
        )
    rt = get_services().rt
    rec = rt.docs.get(disposition_id)
    if not rec or rec.kind != "disposition":
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "disposition not found"})
    doc = dict(rec.doc)
    doc["review_status"] = req.review_status
    doc.setdefault("review_history", []).append(
        {"reviewer": req.reviewer, "decision": req.review_status, "note": req.note}
    )
    rt.docs.put(
        doc_id=disposition_id,
        kind="disposition",
        doc=doc,
        workspace_id=doc["workspace_id"],
        ref_id=doc.get("diagnosis_id"),
        status=req.review_status,
    )
    return doc


# ---------------------------------------------------------------------- graph
@router.post("/runs/{run_id}/graph")
async def build_graph(run_id: str) -> dict[str, Any]:
    return get_services().graph.build_for_run(run_id)


@router.get("/runs/{run_id}/graph")
async def get_graph(run_id: str) -> dict[str, Any]:
    try:
        return get_services().graph.get(run_id)
    except AppError as exc:
        raise _http(exc) from exc


@router.get("/graphs")
async def list_graphs(workspace_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    return get_services().graph.list_graphs(workspace_id, limit)


# --------------------------------------------------------------------- probes
@router.get("/probes/definitions")
async def probe_definitions() -> list[dict[str, Any]]:
    return get_services().probe.definitions()


class ProbeExecReq(BaseModel):
    probe_id: str
    params: dict[str, Any] = {}
    workspace_id: str = "default"


@router.post("/probes/execute", status_code=201)
async def execute_probe(req: ProbeExecReq) -> dict[str, Any]:
    return get_services().probe.execute(req.probe_id, req.params, req.workspace_id)


@router.get("/probes/executions")
async def probe_executions(
    workspace_id: str | None = None, run_id: str | None = None, limit: int = 100
) -> list[dict[str, Any]]:
    return get_services().probe.list_executions(workspace_id, run_id, limit)


@router.get("/probes/audit")
async def probe_audit() -> list[dict[str, Any]]:
    return get_services().probe.audit_log()


@router.get("/runs/{run_id}/probe-suggestions")
async def probe_suggestions(run_id: str, diagnosis_id: str | None = None) -> list[dict[str, Any]]:
    try:
        return get_services().probe.suggest(run_id, diagnosis_id)
    except AppError as exc:
        raise _http(exc) from exc


# --------------------------------------------------------------------- repair
class ChangeReq(BaseModel):
    path: str
    step_index: int | None = None
    value: Any = None
    reason: str = ""


class RepairProposeReq(BaseModel):
    testcase_id: str
    base_version: int
    changes: list[ChangeReq]
    reason: str = ""
    created_by: str = "agent:repair-planner"


@router.post("/repairs", status_code=201)
async def propose_repair(req: RepairProposeReq) -> dict[str, Any]:
    try:
        return get_services().repair.propose(
            testcase_id=req.testcase_id,
            base_version=req.base_version,
            changes=[c.model_dump() for c in req.changes],
            reason=req.reason,
            created_by=req.created_by,
        )
    except AppError as exc:
        raise _http(exc) from exc


@router.get("/repairs")
async def list_repairs(
    workspace_id: str | None = None, status: str | None = None
) -> list[dict[str, Any]]:
    return get_services().repair.list(workspace_id, status)


@router.get("/repairs/{proposal_id}")
async def get_repair(proposal_id: str) -> dict[str, Any]:
    try:
        return get_services().repair.get(proposal_id)
    except AppError as exc:
        raise _http(exc) from exc


@router.post("/repairs/{proposal_id}/validate")
async def validate_repair(proposal_id: str) -> dict[str, Any]:
    try:
        return get_services().repair.validate(proposal_id)
    except AppError as exc:
        raise _http(exc) from exc


@router.post("/repairs/{proposal_id}/sandbox-run")
async def sandbox_repair(proposal_id: str) -> dict[str, Any]:
    try:
        # 沙箱验证会真实启动浏览器，必须在线程池执行
        return await run_in_threadpool(get_services().repair.sandbox_run, proposal_id)
    except AppError as exc:
        raise _http(exc) from exc


class ApproveReq(BaseModel):
    reviewer: str
    note: str = ""


@router.post("/repairs/{proposal_id}/submit")
async def submit_repair(proposal_id: str) -> dict[str, Any]:
    try:
        return get_services().repair.submit_for_approval(proposal_id)
    except AppError as exc:
        raise _http(exc) from exc


@router.post("/repairs/{proposal_id}/approve")
async def approve_repair(proposal_id: str, req: ApproveReq) -> dict[str, Any]:
    try:
        return get_services().repair.approve(proposal_id, req.reviewer, req.note)
    except AppError as exc:
        raise _http(exc) from exc


@router.post("/repairs/{proposal_id}/reject")
async def reject_repair(proposal_id: str, req: ApproveReq) -> dict[str, Any]:
    try:
        return get_services().repair.reject(proposal_id, req.reviewer, req.note)
    except AppError as exc:
        raise _http(exc) from exc


class PromoteReq(BaseModel):
    idempotency_key: str


@router.post("/repairs/{proposal_id}/promote")
async def promote_repair(proposal_id: str, req: PromoteReq) -> dict[str, Any]:
    try:
        return get_services().repair.promote(proposal_id, idempotency_key=req.idempotency_key)
    except AppError as exc:
        raise _http(exc) from exc


class RollbackReq(BaseModel):
    operator: str
    reason: str


@router.post("/repairs/{proposal_id}/rollback")
async def rollback_repair(proposal_id: str, req: RollbackReq) -> dict[str, Any]:
    try:
        return get_services().repair.rollback(proposal_id, req.operator, req.reason)
    except AppError as exc:
        raise _http(exc) from exc
