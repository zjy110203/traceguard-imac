"""质量智能 API：Diagnosis / Disposition / Evidence Graph / Probe / Repair。

RUN-011-R1.1 WP1：对象级授权 + Workspace 作用域统一走 `workspace.py`；
WP3：Repair 血缘与来源由服务端验证，不再信任请求体声明。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool
from traceguard_core.errors import AppError
from traceguard_core.ids import utc_now_iso
from traceguard_persistence.catalog import require_human_actor

from ..auth import trusted_actor
from ..services import get_services
from ..workspace import principal_of, require_entity_workspace, scope_filter

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


def _doc_or_404(doc_id: str, request: Request, *, kind: str | None = None):  # type: ignore[no-untyped-def]
    """取聚合文档并做对象级授权（Graph/Diagnosis/Probe/Repair 统一入口）。"""
    rec = get_services().rt.docs.get(doc_id)
    if rec is None or (kind is not None and rec.kind != kind):
        raise HTTPException(
            404, detail={"code": "NOT_FOUND", "message": f"{kind or 'doc'} not found"}
        )
    require_entity_workspace(
        principal_of(request),
        rec.workspace_id,
        resource_type=rec.kind,
        resource_id=doc_id,
        request=request,
    )
    return rec


def _run_workspace_of(run_id: str, request: Request) -> dict[str, Any]:
    run = get_services().rt.tasks.get_run(run_id)
    if not run:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "run not found"})
    require_entity_workspace(
        principal_of(request),
        run.get("workspace_id"),
        resource_type="run",
        resource_id=run_id,
        request=request,
    )
    return run


# ----------------------------------------------------------------- exploration
@router.post("/intents/{intent_id}/explore", status_code=201)
async def explore_intent(intent_id: str, request: Request, max_steps: int = 8) -> dict[str, Any]:
    """Agent 真实探索（只读工具 + 预算 + 域名白名单），产出可复现轨迹。"""
    from ..routers.catalog import _intent_or_404

    _intent_or_404(intent_id, request)
    try:
        return await run_in_threadpool(get_services().exploration.explore, intent_id, max_steps)
    except AppError as exc:
        raise _http(exc) from exc


@router.get("/explorations")
async def list_explorations(
    request: Request, workspace_id: str | None = None, limit: int = 50
) -> list[dict[str, Any]]:
    allowed = scope_filter(
        principal_of(request), workspace_id, resource_type="exploration", request=request
    )
    return get_services().exploration.list(workspace_id, limit, workspace_ids=allowed)


@router.get("/explorations/{exploration_id}")
async def get_exploration(exploration_id: str, request: Request) -> dict[str, Any]:
    doc = get_services().exploration.get(exploration_id)
    if not doc:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "exploration not found"})
    require_entity_workspace(
        principal_of(request),
        doc.get("workspace_id"),
        resource_type="exploration",
        resource_id=exploration_id,
        request=request,
    )
    return doc


@router.post("/explorations/{exploration_id}/compile", status_code=201)
async def compile_exploration(
    exploration_id: str, request: Request, mode: str | None = None
) -> dict[str, Any]:
    """轨迹 → 候选用例（PENDING，必须人工审核后才能执行）。

    mode: rule_only / llm_only / llm_plus_rule_validation（未指定则读配置开关）。
    LLM 路径失败时回退规则编译，并在 compiled.llm_meta 中如实标记 fallback。
    """
    doc = get_services().exploration.get(exploration_id)
    if not doc:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "exploration not found"})
    require_entity_workspace(
        principal_of(request),
        doc.get("workspace_id"),
        resource_type="exploration",
        resource_id=exploration_id,
        request=request,
    )
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
    request: Request,
    repeats: int | None = None,
    risk_profile: str = "default",
    sync: bool = False,
) -> dict[str, Any]:
    """候选用例稳定性回放：每次独立 Run，走沙箱入口，不覆盖历史。

    RUN-007：sync=true 不再在请求线程里直接 execute_run（会与真实 Worker 抢租约，
    产生 "run not acquirable" 冲突），而是创建 Run 后等待**真实 Worker 消费**完成，
    超时仍返回 RUNNING 快照（不伪造结论）。
    """
    from ..routers.catalog import _testcase_or_404

    _testcase_or_404(testcase_id, request)
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
    request: Request, testcase_id: str | None = None, limit: int = 50
) -> list[dict[str, Any]]:
    allowed = scope_filter(
        principal_of(request), None, resource_type="stability_replay", request=request
    )
    return get_services().stability.list(testcase_id, limit, workspace_ids=allowed)


@router.get("/stability-replays/{replay_id}")
async def get_stability_replay(
    replay_id: str, request: Request, refresh: bool = False
) -> dict[str, Any]:
    _doc_or_404(replay_id, request)
    try:
        svc = get_services().stability
        return svc.refresh(replay_id) if refresh else svc.get(replay_id)
    except AppError as exc:
        raise _http(exc) from exc


@router.get("/testcases/{testcase_id}/versions/{version}/stability-gate")
async def preview_stability_gate(
    testcase_id: str, version: int, request: Request
) -> dict[str, Any]:
    """RUN-013 WP3：审核条件只读预检（与审核事务同一套规则）。

    返回 `satisfied` + `problems`，供用例详情页展示"当前是否满足审核条件"，
    不产生放行效力；真正放行仍以审核事务内的门禁为准。
    """
    from ..routers.catalog import _testcase_or_404

    doc = _testcase_or_404(testcase_id, request)
    ws = str(doc.get("workspace_id") or "default")
    try:
        problems, replay = get_services().rt.catalog.stability_gate_preview(
            testcase_id, version, workspace_id=ws
        )
    except AppError as exc:
        raise _http(exc) from exc
    return {
        "testcase_id": testcase_id,
        "version": version,
        "satisfied": not problems,
        "problems": problems,
        "evidence": (
            {
                "replay_id": (replay or {}).get("replay_id"),
                "testcase_version_ref": (replay or {}).get("testcase_version_ref"),
                "repeats": (replay or {}).get("repeats"),
                "classification": ((replay or {}).get("classification") or {}).get(
                    "classification"
                ),
                "oracle_versions": (replay or {}).get("oracle_versions"),
                "promotable": (replay or {}).get("promotable"),
                "completed_at": (replay or {}).get("completed_at"),
            }
            if replay
            else None
        ),
    }


# ------------------------------------------------------------------ diagnosis
@router.post("/runs/{run_id}/diagnose", status_code=201)
async def diagnose(run_id: str, request: Request, use_llm: bool | None = None) -> dict[str, Any]:
    """诊断。`use_llm` 显式指定是否使用 LLM 顾问（None=按全局配置）。

    Benchmark 分组必须显式传 `use_llm=false` 才能保证"规则组"口径纯净。
    """
    _run_workspace_of(run_id, request)
    try:
        return get_services().diagnosis.diagnose(run_id, use_llm_advisor=use_llm)
    except AppError as exc:
        raise _http(exc) from exc


@router.get("/diagnoses")
async def list_diagnoses(
    request: Request,
    workspace_id: str | None = None,
    run_id: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    allowed = scope_filter(
        principal_of(request), workspace_id, resource_type="diagnosis", request=request
    )
    return get_services().diagnosis.list_diagnoses(
        workspace_id, run_id, limit, workspace_ids=allowed
    )


@router.get("/diagnoses/{diagnosis_id}")
async def get_diagnosis(diagnosis_id: str, request: Request) -> dict[str, Any]:
    doc = get_services().diagnosis.get(diagnosis_id)
    if not doc:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "diagnosis not found"})
    require_entity_workspace(
        principal_of(request),
        doc.get("workspace_id"),
        resource_type="diagnosis",
        resource_id=diagnosis_id,
        request=request,
    )
    return doc


@router.get("/diagnoses/{diagnosis_id}/revisions")
async def list_diagnosis_revisions(diagnosis_id: str, request: Request) -> list[dict[str, Any]]:
    """诊断版本链（RUN-018 WP5）：v1 = 原始诊断，每次重诊断追加一个不可变版本。

    每个版本记录：原 Diagnosis（before）、新 Diagnosis（after）、新增证据、
    假设变化、置信度变化、最终裁决原因（为什么确认或为什么弃权）。
    """
    doc = get_services().diagnosis.get(diagnosis_id)
    if not doc:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "diagnosis not found"})
    require_entity_workspace(
        principal_of(request),
        doc.get("workspace_id"),
        resource_type="diagnosis",
        resource_id=diagnosis_id,
        request=request,
    )
    return get_services().diagnosis.list_revisions(diagnosis_id)


@router.get("/dispositions")
async def list_dispositions(
    request: Request,
    workspace_id: str | None = None,
    status: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    allowed = scope_filter(
        principal_of(request), workspace_id, resource_type="disposition", request=request
    )
    recs = get_services().rt.docs.list(
        kind="disposition",
        workspace_id=workspace_id,
        status=status,
        limit=limit,
        workspace_ids=allowed,
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
    rec = _doc_or_404(disposition_id, request, kind="disposition")
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
async def build_graph(run_id: str, request: Request) -> dict[str, Any]:
    _run_workspace_of(run_id, request)
    return get_services().graph.build_for_run(run_id)


@router.get("/runs/{run_id}/graph")
async def get_graph(run_id: str, request: Request) -> dict[str, Any]:
    _run_workspace_of(run_id, request)
    try:
        return get_services().graph.get(run_id)
    except AppError as exc:
        raise _http(exc) from exc


@router.get("/graphs")
async def list_graphs(
    request: Request, workspace_id: str | None = None, limit: int = 50
) -> list[dict[str, Any]]:
    allowed = scope_filter(
        principal_of(request), workspace_id, resource_type="graph", request=request
    )
    return get_services().graph.list_graphs(workspace_id, limit, workspace_ids=allowed)


# --------------------------------------------------------------------- probes
@router.get("/probes/definitions")
async def probe_definitions() -> list[dict[str, Any]]:
    return get_services().probe.definitions()


class ProbeExecReq(BaseModel):
    probe_id: str
    params: dict[str, Any] = {}
    #: 仅作兼容保留：Workspace 由 Run / Diagnosis 的真实归属决定（服务端解析）
    workspace_id: str | None = None
    run_id: str | None = None
    diagnosis_id: str | None = None


@router.post("/probes/execute", status_code=201)
async def execute_probe(req: ProbeExecReq, request: Request) -> dict[str, Any]:
    if req.run_id:
        _run_workspace_of(req.run_id, request)
    if req.diagnosis_id:
        _doc_or_404(req.diagnosis_id, request, kind="diagnosis")
    if not req.run_id and not req.diagnosis_id:
        # 无关联实体时只能在自己有权的 Workspace 手动执行
        require_entity_workspace(
            principal_of(request),
            req.workspace_id or "default",
            resource_type="probe",
            resource_id=req.probe_id,
            request=request,
        )
    try:
        return get_services().probe.execute(
            req.probe_id,
            req.params,
            req.workspace_id or "default",
            run_id=req.run_id,
            diagnosis_id=req.diagnosis_id,
        )
    except AppError as exc:
        raise _http(exc) from exc


@router.get("/probes/budgets")
async def probe_budgets(request: Request, workspace_id: str | None = None) -> list[dict[str, Any]]:
    """探针预算快照（按 Run / Diagnosis 隔离，provisional 数值明确标注）。"""
    allowed = scope_filter(
        principal_of(request), workspace_id, resource_type="probe_budget", request=request
    )
    return get_services().probe.budgets_snapshot(workspace_id, workspace_ids=allowed)


@router.post("/probes/budgets/close")
async def close_probe_budget(
    request: Request, run_id: str | None = None, diagnosis_id: str | None = None
) -> dict[str, Any]:
    if run_id:
        _run_workspace_of(run_id, request)
    if diagnosis_id:
        _doc_or_404(diagnosis_id, request, kind="diagnosis")
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
    request: Request,
    diagnosis_id: str | None = None,
    max_iterations: int = 6,
) -> dict[str, Any]:
    """真实执行 Active Diagnosis 循环（探针选择 → 执行 → 新证据 → 再评估）。"""
    _run_workspace_of(run_id, request)
    if diagnosis_id:
        _doc_or_404(diagnosis_id, request, kind="diagnosis")
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
async def list_probe_plans(
    request: Request, run_id: str | None = None, limit: int = 50
) -> list[dict[str, Any]]:
    allowed = scope_filter(principal_of(request), None, resource_type="probe_plan", request=request)
    return get_services().probe.list_plans(run_id, limit, workspace_ids=allowed)


@router.get("/probe-plans/{plan_id}")
async def get_probe_plan(plan_id: str, request: Request) -> dict[str, Any]:
    rec = _doc_or_404(plan_id, request, kind="probe_plan")
    return rec.to_dict()


@router.get("/probes/executions")
async def probe_executions(
    request: Request,
    workspace_id: str | None = None,
    run_id: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    allowed = scope_filter(
        principal_of(request), workspace_id, resource_type="probe_execution", request=request
    )
    return get_services().probe.list_executions(workspace_id, run_id, limit, workspace_ids=allowed)


@router.get("/probes/audit")
async def probe_audit(response: Response) -> list[dict[str, Any]]:
    """探针引擎的**进程内**调用/拒绝轨迹（运维观测）。

    RUN-014 WP3：这不是正式审计记录。正式的探针执行事实在持久化的
    `probe_execution` 文档里（含 workspace / run / 证据 / 预算），重启后仍可回读；
    本接口只反映当前进程内的 `ProbeEngine._audit` 列表，进程重启即清空。
    通过响应头显式声明边界，避免调用方误当作不可篡改审计。
    """
    response.headers["X-TraceGuard-Audit-Persistent"] = "false"
    response.headers["X-TraceGuard-Audit-Scope"] = "in-memory-process"
    response.headers["X-TraceGuard-Audit-Formal-Source"] = "probe_execution"
    return get_services().probe.audit_log()


@router.get("/runs/{run_id}/probe-suggestions")
async def probe_suggestions(
    run_id: str, request: Request, diagnosis_id: str | None = None
) -> list[dict[str, Any]]:
    _run_workspace_of(run_id, request)
    if diagnosis_id:
        _doc_or_404(diagnosis_id, request, kind="diagnosis")
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
    #: 修复方案来源：human:* / rule:* / llm:*（三类不得混淆，服务端验证后落库）
    repair_source: str = ""


class RepairLLMReq(BaseModel):
    testcase_id: str
    base_version: int
    run_id: str | None = None
    diagnosis_id: str | None = None
    propose: bool = False


@router.post("/repairs/llm-suggest", status_code=201)
async def llm_repair_suggest(req: RepairLLMReq, request: Request) -> dict[str, Any]:
    """LLM 生成受限修复候选（禁止自动审批/晋升；不具备凭据时明确返回未配置）。"""
    from ..routers.catalog import _testcase_or_404

    _testcase_or_404(req.testcase_id, request)
    if req.run_id:
        _run_workspace_of(req.run_id, request)
    if req.diagnosis_id:
        _doc_or_404(req.diagnosis_id, request, kind="diagnosis")
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
async def propose_repair(req: RepairProposeReq, request: Request) -> dict[str, Any]:
    from ..routers.catalog import _testcase_or_404

    testcase = _testcase_or_404(req.testcase_id, request)
    actor = trusted_actor(
        request,
        req.created_by,
        "repair.submit",
        resource_type="repair",
        resource_id=req.testcase_id,
    )
    if req.diagnosis_id:
        _doc_or_404(req.diagnosis_id, request, kind="diagnosis")
    if req.source_run_id:
        _run_workspace_of(req.source_run_id, request)
    try:
        return get_services().repair.propose(
            testcase_id=req.testcase_id,
            base_version=req.base_version,
            changes=[c.model_dump() for c in req.changes],
            reason=req.reason,
            created_by=actor,
            workspace_id=testcase.get("workspace_id"),
            diagnosis_id=req.diagnosis_id,
            source_run_id=req.source_run_id,
            repair_source=req.repair_source,
            actor=actor,
            trusted_source=False,
        )
    except AppError as exc:
        raise _http(exc) from exc


@router.get("/repairs")
async def list_repairs(
    request: Request, workspace_id: str | None = None, status: str | None = None
) -> list[dict[str, Any]]:
    allowed = scope_filter(
        principal_of(request), workspace_id, resource_type="repair", request=request
    )
    return get_services().repair.list(workspace_id, status, workspace_ids=allowed)


@router.get("/repairs/{proposal_id}")
async def get_repair(proposal_id: str, request: Request) -> dict[str, Any]:
    rec = _doc_or_404(proposal_id, request, kind="repair")
    return rec.to_dict()


@router.post("/repairs/{proposal_id}/validate")
async def validate_repair(proposal_id: str, request: Request) -> dict[str, Any]:
    _doc_or_404(proposal_id, request, kind="repair")
    try:
        return get_services().repair.validate(proposal_id)
    except AppError as exc:
        raise _http(exc) from exc


@router.post("/repairs/{proposal_id}/sandbox-run")
async def sandbox_repair(proposal_id: str, request: Request) -> dict[str, Any]:
    _doc_or_404(proposal_id, request, kind="repair")
    try:
        # 沙箱验证会真实启动浏览器，必须在线程池执行
        return await run_in_threadpool(get_services().repair.sandbox_run, proposal_id)
    except AppError as exc:
        raise _http(exc) from exc


class ApproveReq(BaseModel):
    reviewer: str
    note: str = ""


@router.post("/repairs/{proposal_id}/submit")
async def submit_repair(proposal_id: str, request: Request) -> dict[str, Any]:
    _doc_or_404(proposal_id, request, kind="repair")
    try:
        return get_services().repair.submit_for_approval(proposal_id)
    except AppError as exc:
        raise _http(exc) from exc


@router.post("/repairs/{proposal_id}/approve")
async def approve_repair(proposal_id: str, req: ApproveReq, request: Request) -> dict[str, Any]:
    _doc_or_404(proposal_id, request, kind="repair")
    reviewer = trusted_actor(
        request, req.reviewer, "repair.approve", resource_type="repair", resource_id=proposal_id
    )
    try:
        return get_services().repair.approve(proposal_id, reviewer, req.note)
    except AppError as exc:
        raise _http(exc) from exc


@router.post("/repairs/{proposal_id}/reject")
async def reject_repair(proposal_id: str, req: ApproveReq, request: Request) -> dict[str, Any]:
    _doc_or_404(proposal_id, request, kind="repair")
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
    _doc_or_404(proposal_id, request, kind="repair")
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


class PromotionRecoverReq(BaseModel):
    idempotency_key: str
    #: 人工确认（必须显式为 true，否则拒绝）
    confirm: bool = False
    operator: str = ""


@router.get("/repairs/{proposal_id}/promotion-recovery")
async def promotion_recovery_plan(proposal_id: str, request: Request) -> dict[str, Any]:
    """只读：晋升失败后的恢复评估（能否重试 / 是否已实际晋升 / 为什么被阻塞）。"""
    _doc_or_404(proposal_id, request, kind="repair")
    try:
        return get_services().repair.promotion_recovery_plan(proposal_id)
    except AppError as exc:
        raise _http(exc) from exc


@router.post("/repairs/{proposal_id}/promote-recover")
async def promotion_recover(
    proposal_id: str, req: PromotionRecoverReq, request: Request
) -> dict[str, Any]:
    """人工确认后的安全恢复：不绕过 Oracle / 审批，不重复创建版本。"""
    _doc_or_404(proposal_id, request, kind="repair")
    operator = trusted_actor(
        request,
        req.operator or "user:dev-operator",
        "repair.promote",
        resource_type="repair",
        resource_id=proposal_id,
    )
    try:
        return get_services().repair.promote_recover(
            proposal_id,
            idempotency_key=req.idempotency_key,
            confirm=req.confirm,
            actor=operator,
        )
    except AppError as exc:
        raise _http(exc) from exc


class RollbackReq(BaseModel):
    operator: str
    reason: str
    #: 可选：不传时按 proposal + promoted_version 生成确定性键，重试仍幂等
    idempotency_key: str = ""


@router.post("/repairs/{proposal_id}/rollback")
async def rollback_repair(proposal_id: str, req: RollbackReq, request: Request) -> dict[str, Any]:
    _doc_or_404(proposal_id, request, kind="repair")
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
async def reconcile_repair(proposal_id: str, request: Request) -> dict[str, Any]:
    """确定性恢复：把崩溃后卡在 PROMOTING 的提案按已提交回执修复。"""
    _doc_or_404(proposal_id, request, kind="repair")
    try:
        return get_services().repair.reconcile(proposal_id)
    except AppError as exc:
        raise _http(exc) from exc


@router.post("/repairs/{proposal_id}/regression-run")
async def regression_repair(
    proposal_id: str, request: Request, wait_timeout_s: float | None = None
) -> dict[str, Any]:
    """晋升后的真实回归验证：创建独立 Regression Run，由真实 Worker 执行。

    幂等：已终态直接返回；在途只收敛同一个 Run，不重复创建。
    """
    rec = _doc_or_404(proposal_id, request, kind="repair")
    if not (rec.doc or {}).get("promoted_version"):
        raise HTTPException(
            412,
            detail={
                "code": "PRECONDITION_FAILED",
                "message": "REGRESSION_REQUIRES_PROMOTED: 提案尚未晋升，无法回归验证",
            },
        )
    try:
        return await run_in_threadpool(
            get_services().repair.regression_run,
            proposal_id,
            timeout_s=wait_timeout_s,
        )
    except AppError as exc:
        raise _http(exc) from exc


@router.get("/repairs/{proposal_id}/regression")
async def regression_status(proposal_id: str, request: Request) -> dict[str, Any]:
    """只读回归状态（含实时 Run 快照），供 UI 刷新时不依赖本地状态。"""
    _doc_or_404(proposal_id, request, kind="repair")
    try:
        return get_services().repair.regression_status(proposal_id)
    except AppError as exc:
        raise _http(exc) from exc


class RegressionRetryReq(BaseModel):
    """人工授权重试（RUN-017 WP3）。缺少 confirm 时服务端拒绝。"""

    operator: str
    confirm: bool = False
    reason_code: str = ""
    note: str = ""
    idempotency_key: str = ""


@router.post("/repairs/{proposal_id}/regression-retry")
async def regression_retry(
    proposal_id: str,
    req: RegressionRetryReq,
    request: Request,
    wait_timeout_s: float | None = None,
) -> dict[str, Any]:
    """为 INCONCLUSIVE 回归创建**新的 Attempt**（不覆盖旧 Run/结论/证据）。"""
    _doc_or_404(proposal_id, request, kind="repair")
    operator = trusted_actor(
        request, req.operator, "repair.regression", resource_type="repair", resource_id=proposal_id
    )
    try:
        return await run_in_threadpool(
            get_services().repair.regression_retry,
            proposal_id,
            actor=operator,
            confirm=req.confirm,
            reason_code=req.reason_code,
            note=req.note,
            idempotency_key=req.idempotency_key,
            timeout_s=wait_timeout_s,
        )
    except AppError as exc:
        raise _http(exc) from exc
