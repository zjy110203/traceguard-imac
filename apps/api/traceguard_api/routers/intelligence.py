"""质量智能 API：Diagnosis / Disposition / Evidence Graph / Probe / Repair。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool
from traceguard_core.errors import AppError
from traceguard_core.ids import utc_now_iso
from traceguard_persistence.catalog import require_human_actor

from ..auth import trusted_actor
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
async def compile_exploration(exploration_id: str, mode: str | None = None) -> dict[str, Any]:
    """轨迹 → 候选用例（PENDING，必须人工审核后才能执行）。

    mode: rule_only / llm_only / llm_plus_rule_validation（未指定则读配置开关）。
    LLM 路径失败时回退规则编译，并在 compiled.llm_meta 中如实标记 fallback。
    """
    try:
        return await run_in_threadpool(
            get_services().exploration.compile, exploration_id, mode=mode
        )
    except AppError as exc:
        raise _http(exc) from exc


@router.post("/testcases/{testcase_id}/versions/{version}/stability-replay", status_code=201)
async def start_stability_replay(
    testcase_id: str,
    version: int,
    repeats: int | None = None,
    risk_profile: str = "default",
    sync: bool = False,
) -> dict[str, Any]:
    """候选用例稳定性回放：每次独立 Run，走沙箱入口，不覆盖历史。

    RUN-007：sync=true 不再在请求线程里直接 execute_run（会与真实 Worker 抢租约，
    产生 "run not acquirable" 冲突），而是创建 Run 后等待**真实 Worker 消费**完成，
    超时仍返回 RUNNING 快照（不伪造结论）。
    """
    from ..services import get_services

    try:
        if not sync:
            return await run_in_threadpool(
                get_services().stability.start,
                testcase_id,
                version,
                repeats=repeats,
                risk_profile=risk_profile,
                sync=False,
            )
        svc = get_services().stability
        doc = await run_in_threadpool(
            svc.start,
            testcase_id,
            version,
            repeats=repeats,
            risk_profile=risk_profile,
            sync=False,
        )
        return await run_in_threadpool(svc.wait, doc["replay_id"], timeout_s=300.0)
    except AppError as exc:
        raise _http(exc) from exc


@router.get("/stability-replays")
async def list_stability_replays(
    testcase_id: str | None = None, limit: int = 50
) -> list[dict[str, Any]]:
    return get_services().stability.list(testcase_id, limit)


@router.get("/stability-replays/{replay_id}")
async def get_stability_replay(replay_id: str, refresh: bool = False) -> dict[str, Any]:
    try:
        svc = get_services().stability
        return svc.refresh(replay_id) if refresh else svc.get(replay_id)
    except AppError as exc:
        raise _http(exc) from exc


# ------------------------------------------------------------------ diagnosis
@router.post("/runs/{run_id}/diagnose", status_code=201)
async def diagnose(run_id: str, use_llm: bool | None = None) -> dict[str, Any]:
    """诊断。`use_llm` 显式指定是否使用 LLM 顾问（None=按全局配置）。

    Benchmark 分组必须显式传 `use_llm=false` 才能保证"规则组"口径纯净。
    """
    try:
        return get_services().diagnosis.diagnose(run_id, use_llm_advisor=use_llm)
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
async def review_disposition(
    disposition_id: str, req: DispositionReviewReq, request: Request
) -> dict[str, Any]:
    """处置结论的人工复核。

    RUN-007 WP1-03：主体校验统一走 `require_human_actor()`（按命名空间首段判断），
    不再使用"字符串里包含 agent 就拒绝"的子串匹配——那种写法既漏 `bot:`/`system:`，
    又会误伤 `org:agents-team` 这类合法具名主体。
    """
    if req.review_status not in {"APPROVED", "REJECTED", "PENDING"}:
        raise HTTPException(422, detail={"code": "VALIDATION_ERROR", "message": "invalid status"})
    try:
        actor = require_human_actor(
            trusted_actor(
                request,
                req.reviewer,
                "diagnosis.operate",
                resource_type="disposition",
                resource_id=disposition_id,
            ),
            field="reviewer",
        )
    except AppError as exc:
        raise _http(exc) from exc
    rt = get_services().rt
    rec = rt.docs.get(disposition_id)
    if not rec or rec.kind != "disposition":
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "disposition not found"})
    doc = dict(rec.doc)
    doc["review_status"] = req.review_status
    doc["review_revision"] = int(doc.get("review_revision") or 0) + 1
    doc.setdefault("review_history", []).append(
        {
            "reviewer": actor,
            "decision": req.review_status,
            "note": req.note,
            "review_revision": doc["review_revision"],
            "at": utc_now_iso(),
        }
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
    run_id: str | None = None
    diagnosis_id: str | None = None


@router.post("/probes/execute", status_code=201)
async def execute_probe(req: ProbeExecReq) -> dict[str, Any]:
    try:
        return get_services().probe.execute(
            req.probe_id,
            req.params,
            req.workspace_id,
            run_id=req.run_id,
            diagnosis_id=req.diagnosis_id,
        )
    except AppError as exc:
        raise _http(exc) from exc


@router.get("/probes/budgets")
async def probe_budgets(workspace_id: str | None = None) -> list[dict[str, Any]]:
    """探针预算快照（按 Run / Diagnosis 隔离，provisional 数值明确标注）。"""
    return get_services().probe.budgets_snapshot(workspace_id)


@router.post("/probes/budgets/close")
async def close_probe_budget(
    run_id: str | None = None, diagnosis_id: str | None = None
) -> dict[str, Any]:
    try:
        snapshot = get_services().probe.close_budget(
            run_id=run_id or "", diagnosis_id=diagnosis_id or ""
        )
    except AppError as exc:
        raise _http(exc) from exc
    if snapshot is None:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "budget not found"})
    return snapshot


@router.post("/runs/{run_id}/probe-plan", status_code=201)
async def run_probe_plan(
    run_id: str,
    diagnosis_id: str | None = None,
    max_iterations: int = 6,
) -> dict[str, Any]:
    """真实执行 Active Diagnosis 循环（探针选择 → 执行 → 新证据 → 再评估）。"""
    try:
        return await run_in_threadpool(
            get_services().probe.plan_and_probe,
            run_id,
            diagnosis_id,
            max_iterations=max_iterations,
        )
    except AppError as exc:
        raise _http(exc) from exc


@router.get("/probe-plans")
async def list_probe_plans(run_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    return get_services().probe.list_plans(run_id, limit)


@router.get("/probe-plans/{plan_id}")
async def get_probe_plan(plan_id: str) -> dict[str, Any]:
    plan = get_services().probe.get_plan(plan_id)
    if plan is None:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "plan not found"})
    return plan


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
    # RUN-011-R1 WP1 §1.3：Diagnosis→Repair 血缘（可选，向后兼容）
    diagnosis_id: str | None = None
    source_run_id: str | None = None
    #: 修复方案来源：human:* / rule:* / llm:*（三类不得混淆）
    repair_source: str = ""


class RepairLLMReq(BaseModel):
    testcase_id: str
    base_version: int
    run_id: str | None = None
    diagnosis_id: str | None = None
    propose: bool = False


@router.post("/repairs/llm-suggest", status_code=201)
async def llm_repair_suggest(req: RepairLLMReq) -> dict[str, Any]:
    """LLM 生成受限修复候选（禁止自动审批/晋升；不具备凭据时明确返回未配置）。"""
    try:
        return await run_in_threadpool(
            get_services().repair.llm_suggest,
            testcase_id=req.testcase_id,
            base_version=req.base_version,
            run_id=req.run_id,
            diagnosis_id=req.diagnosis_id,
            propose=req.propose,
        )
    except AppError as exc:
        raise _http(exc) from exc


@router.post("/repairs", status_code=201)
async def propose_repair(req: RepairProposeReq) -> dict[str, Any]:
    try:
        return get_services().repair.propose(
            testcase_id=req.testcase_id,
            base_version=req.base_version,
            changes=[c.model_dump() for c in req.changes],
            reason=req.reason,
            created_by=req.created_by,
            diagnosis_id=req.diagnosis_id,
            source_run_id=req.source_run_id,
            repair_source=req.repair_source,
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
async def approve_repair(proposal_id: str, req: ApproveReq, request: Request) -> dict[str, Any]:
    reviewer = trusted_actor(
        request, req.reviewer, "repair.approve", resource_type="repair", resource_id=proposal_id
    )
    try:
        return get_services().repair.approve(proposal_id, reviewer, req.note)
    except AppError as exc:
        raise _http(exc) from exc


@router.post("/repairs/{proposal_id}/reject")
async def reject_repair(proposal_id: str, req: ApproveReq, request: Request) -> dict[str, Any]:
    rejector = trusted_actor(
        request, req.reviewer, "repair.approve", resource_type="repair", resource_id=proposal_id
    )
    try:
        return get_services().repair.reject(proposal_id, rejector, req.note)
    except AppError as exc:
        raise _http(exc) from exc


class PromoteReq(BaseModel):
    idempotency_key: str


@router.post("/repairs/{proposal_id}/promote")
async def promote_repair(proposal_id: str, req: PromoteReq, request: Request) -> dict[str, Any]:
    trusted_actor(
        request,
        "user:dev-operator",
        "repair.promote",
        resource_type="repair",
        resource_id=proposal_id,
    )
    try:
        return get_services().repair.promote(proposal_id, idempotency_key=req.idempotency_key)
    except AppError as exc:
        raise _http(exc) from exc


class RollbackReq(BaseModel):
    operator: str
    reason: str
    #: 可选：不传时按 proposal + promoted_version 生成确定性键，重试仍幂等
    idempotency_key: str = ""


@router.post("/repairs/{proposal_id}/rollback")
async def rollback_repair(proposal_id: str, req: RollbackReq, request: Request) -> dict[str, Any]:
    operator = trusted_actor(
        request, req.operator, "repair.rollback", resource_type="repair", resource_id=proposal_id
    )
    try:
        return get_services().repair.rollback(
            proposal_id, operator, req.reason, idempotency_key=req.idempotency_key
        )
    except AppError as exc:
        raise _http(exc) from exc


@router.post("/repairs/{proposal_id}/reconcile")
async def reconcile_repair(proposal_id: str) -> dict[str, Any]:
    """确定性恢复：把崩溃后卡在 PROMOTING 的提案按已提交回执修复。"""
    try:
        return get_services().repair.reconcile(proposal_id)
    except AppError as exc:
        raise _http(exc) from exc
