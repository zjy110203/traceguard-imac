"""测试资产 API：Workspace / TestIntent / TestCase（版本、审核）/ Oracle。

RUN-011-R1.1 WP1：实体 Workspace 一律以服务端数据库行为准；写入前校验调用方
对该 Workspace 的权限；detail 路由做对象级授权；列表默认只返回有权范围。
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from traceguard_core.errors import AppError
from traceguard_core.ids import new_id, utc_now_iso

from ..auth import trusted_actor
from ..services import get_services
from ..workspace import (
    normalize_workspace,
    principal_of,
    require_entity_workspace,
    scope_filter,
)

router = APIRouter(prefix="/api/v1", tags=["catalog"])


def _handle(exc: AppError) -> HTTPException:
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


def _require_write_workspace(request: Request, workspace_id: str) -> str:
    """写入前校验调用方对该 Workspace 的权限（返回规范化后的值）。"""
    return require_entity_workspace(
        principal_of(request),
        workspace_id,
        resource_type="workspace",
        resource_id=workspace_id,
        request=request,
    )


# ---------------------------------------------------------------- workspaces
class WorkspaceReq(BaseModel):
    name: str = Field(min_length=1)
    description: str = ""
    created_by: str = "human:admin"


@router.get("/workspaces")
async def list_workspaces(request: Request) -> list[dict[str, Any]]:
    rows = get_services().rt.catalog.list_workspaces()
    allowed = scope_filter(principal_of(request), None, resource_type="workspace", request=request)
    if allowed is None:
        return rows
    allowed_set = set(allowed)
    return [r for r in rows if r.get("workspace_id") in allowed_set]


@router.post("/workspaces", status_code=201)
async def create_workspace(req: WorkspaceReq) -> dict[str, Any]:
    return get_services().rt.catalog.create_workspace(req.name, req.description, req.created_by)


# ------------------------------------------------------------------- intents
class IntentReq(BaseModel):
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)
    owner: str = "qa-team"
    workspace_id: str = "default"
    intent_id: str | None = None
    requirement_refs: list[str] = []
    preconditions: list[str] = []
    candidate_assertions: list[str] = []
    business_context: dict[str, Any] = {}


@router.get("/intents")
async def list_intents(
    request: Request, workspace_id: str | None = None, status: str | None = None
) -> list[dict[str, Any]]:
    allowed = scope_filter(
        principal_of(request), workspace_id, resource_type="intent", request=request
    )
    return get_services().rt.catalog.list_intents(
        workspace_id, status, workspace_ids=allowed
    )


@router.post("/intents", status_code=201)
async def create_intent(req: IntentReq, request: Request) -> dict[str, Any]:
    ws = _require_write_workspace(request, req.workspace_id)
    if req.intent_id and get_services().rt.catalog.get_intent(req.intent_id):
        raise HTTPException(
            409,
            detail={
                "code": "INTENT_ID_EXISTS",
                "message": f"intent_id {req.intent_id} 已存在，不得跨 Workspace 复用",
            },
        )
    payload = {
        "intent_id": req.intent_id or new_id("intent"),
        "title": req.title,
        "description": req.description,
        "owner": req.owner,
        "requirement_refs": req.requirement_refs,
        "preconditions": req.preconditions,
        "candidate_assertions": req.candidate_assertions,
        "business_context": req.business_context,
        "workspace_id": ws,
        "status": "DRAFT",
    }
    try:
        return get_services().rt.catalog.create_intent(payload)
    except AppError as exc:
        raise _handle(exc) from exc


def _intent_or_404(intent_id: str, request: Request) -> dict[str, Any]:
    doc = get_services().rt.catalog.get_intent(intent_id)
    if not doc:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "intent not found"})
    require_entity_workspace(
        principal_of(request),
        doc.get("workspace_id"),
        resource_type="intent",
        resource_id=intent_id,
        request=request,
    )
    return doc


@router.get("/intents/{intent_id}")
async def get_intent(intent_id: str, request: Request) -> dict[str, Any]:
    return _intent_or_404(intent_id, request)


class StatusReq(BaseModel):
    status: str


@router.post("/intents/{intent_id}/status")
async def set_intent_status(intent_id: str, req: StatusReq, request: Request) -> dict[str, Any]:
    _intent_or_404(intent_id, request)
    try:
        return get_services().rt.catalog.update_intent_status(intent_id, req.status)
    except AppError as exc:
        raise _handle(exc) from exc


# ----------------------------------------------------------------- testcases
class StepReq(BaseModel):
    step_id: str
    action: str
    target: dict[str, Any] | None = None
    value: str | None = None
    description: str = ""


class AssertionReq(BaseModel):
    assertion_id: str
    description: str
    kind: str = "ui"
    required: bool = True
    expected: dict[str, Any] = {}
    oracle_id: str | None = None
    oracle_version: int | None = None


class TestCaseReq(BaseModel):
    testcase_id: str | None = None
    title: str = Field(min_length=1)
    intent_ref: str | None = None
    workspace_id: str = "default"
    steps: list[StepReq] = Field(min_length=1)
    assertions: list[AssertionReq] = Field(min_length=1)
    cleanup_policy: str = "NONE"
    run_config: dict[str, Any] = {}
    compile_source: Literal["human", "compiler", "healed"] = "human"
    preconditions: list[str] = []
    test_data_refs: list[str] = []


@router.get("/testcases")
async def list_testcases(
    request: Request, workspace_id: str | None = None, review_status: str | None = None
) -> list[dict[str, Any]]:
    allowed = scope_filter(
        principal_of(request), workspace_id, resource_type="testcase", request=request
    )
    return get_services().rt.catalog.list_testcases(
        workspace_id, review_status, workspace_ids=allowed
    )


@router.post("/testcases", status_code=201)
async def create_testcase(req: TestCaseReq, request: Request) -> dict[str, Any]:
    ws = _require_write_workspace(request, req.workspace_id)
    # 跨 Workspace 引用一律拒绝：intent_ref 必须与用例同属一个 Workspace
    if req.intent_ref:
        intent = get_services().rt.catalog.get_intent(req.intent_ref)
        if intent and normalize_workspace(intent.get("workspace_id")) != normalize_workspace(ws):
            raise HTTPException(
                403,
                detail={
                    "code": "CROSS_WORKSPACE_REFERENCE",
                    "message": f"intent {req.intent_ref} 属于 workspace "
                    f"'{intent.get('workspace_id')}'，不能被 '{ws}' 的用例引用",
                },
            )
    tc_id = req.testcase_id or new_id("tcase")
    payload = {
        "testcase_id": tc_id,
        "version": 1,
        "intent_ref": req.intent_ref,
        "steps": [s.model_dump() for s in req.steps],
        "assertions": [a.model_dump() for a in req.assertions],
        "test_data_refs": req.test_data_refs,
        "preconditions": req.preconditions,
        "cleanup_policy": req.cleanup_policy,
        "run_config": req.run_config,
        "compile_source": req.compile_source,
        "created_at": utc_now_iso(),
        "workspace_id": ws,
    }
    try:
        doc = get_services().rt.catalog.create_testcase(
            payload, title=req.title, workspace_id=ws, review_status="PENDING"
        )
    except AppError as exc:
        raise _handle(exc) from exc
    return doc


def _testcase_or_404(testcase_id: str, request: Request) -> dict[str, Any]:
    doc = get_services().rt.catalog.get_testcase(testcase_id)
    if not doc:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "testcase not found"})
    require_entity_workspace(
        principal_of(request),
        doc.get("workspace_id"),
        resource_type="testcase",
        resource_id=testcase_id,
        request=request,
    )
    return doc


@router.get("/testcases/{testcase_id}")
async def get_testcase(testcase_id: str, request: Request) -> dict[str, Any]:
    return _testcase_or_404(testcase_id, request)


@router.get("/testcases/{testcase_id}/versions")
async def list_versions(testcase_id: str, request: Request) -> list[dict[str, Any]]:
    _testcase_or_404(testcase_id, request)
    return get_services().rt.catalog.list_versions(testcase_id)


@router.get("/testcases/{testcase_id}/versions/{version}")
async def get_version(testcase_id: str, version: int, request: Request) -> dict[str, Any]:
    _testcase_or_404(testcase_id, request)
    doc = get_services().rt.catalog.get_version(testcase_id, version)
    if not doc:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "version not found"})
    require_entity_workspace(
        principal_of(request),
        doc.get("workspace_id"),
        resource_type="testcase_version",
        resource_id=f"{testcase_id}@{version}",
        request=request,
    )
    return doc


class ReviewReq(BaseModel):
    decision: str
    reviewer: str
    note: str = ""
    #: 仅供历史数据修复/迁移使用；正常审核路径必须保持默认 True（不得用来跳过门禁）
    require_stability: bool = True


@router.post("/testcases/{testcase_id}/versions/{version}/review")
async def review(
    testcase_id: str, version: int, req: ReviewReq, request: Request
) -> dict[str, Any]:
    _testcase_or_404(testcase_id, request)
    reviewer = trusted_actor(
        request, req.reviewer, "testcase.review", resource_type="testcase", resource_id=testcase_id
    )
    try:
        return get_services().rt.catalog.review_testcase(
            testcase_id,
            version,
            req.decision,
            reviewer,
            req.note,
            require_stability=req.require_stability,
        )
    except AppError as exc:
        raise _handle(exc) from exc


class OracleBindingReq(BaseModel):
    """Oracle 绑定请求：assertion_id -> {oracle_id, oracle_version?}。"""

    bindings: dict[str, dict[str, Any]]
    actor: str


@router.post("/testcases/{testcase_id}/versions/{version}/oracle-bindings")
async def bind_oracles(
    testcase_id: str, version: int, req: OracleBindingReq, request: Request
) -> dict[str, Any]:
    _testcase_or_404(testcase_id, request)
    actor = trusted_actor(
        request, req.actor, "oracle.bind", resource_type="testcase", resource_id=testcase_id
    )
    try:
        return get_services().rt.catalog.bind_oracles(
            testcase_id, version, req.bindings, actor=actor
        )
    except AppError as exc:
        raise _handle(exc) from exc


# -------------------------------------------------------------------- oracles
class OracleReq(BaseModel):
    oracle_id: str | None = None
    assertion_id: str
    expected_behavior: str = Field(min_length=1)
    requirement_ref: str = Field(min_length=1)
    evaluation_rule: dict[str, Any] = {}
    consistency_window_ms: int = 0
    required: bool = True
    approved: bool = False
    approved_by: str = ""
    approval_note: str = ""
    workspace_id: str = "default"


@router.get("/oracles")
async def list_oracles(request: Request, workspace_id: str | None = None) -> list[dict[str, Any]]:
    allowed = scope_filter(
        principal_of(request), workspace_id, resource_type="oracle", request=request
    )
    return get_services().rt.catalog.list_oracles(workspace_id, workspace_ids=allowed)


@router.post("/oracles", status_code=201)
async def create_oracle(req: OracleReq, request: Request) -> dict[str, Any]:
    ws = _require_write_workspace(request, req.workspace_id)
    approved_by = ""
    if req.approved:
        approved_by = trusted_actor(
            request,
            req.approved_by,
            "oracle.create",
            resource_type="oracle",
            resource_id=req.assertion_id,
        )
    payload = {
        "oracle_id": req.oracle_id or new_id("oracle"),
        "assertion_id": req.assertion_id,
        "expected_behavior": req.expected_behavior,
        "requirement_ref": req.requirement_ref,
        "evaluation_rule": req.evaluation_rule,
        "consistency_window_ms": req.consistency_window_ms,
        "required": req.required,
        "version": 1,
        "workspace_id": ws,
    }
    try:
        return get_services().rt.catalog.create_oracle(
            payload,
            approved_by=approved_by,
            approval_note=req.approval_note,
            approved=req.approved,
        )
    except AppError as exc:
        raise _handle(exc) from exc


@router.get("/oracles/{oracle_id}")
async def get_oracle(
    oracle_id: str, request: Request, version: int | None = None
) -> dict[str, Any]:
    doc = get_services().rt.catalog.get_oracle(oracle_id, version)
    if not doc:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "oracle not found"})
    require_entity_workspace(
        principal_of(request),
        doc.get("workspace_id"),
        resource_type="oracle",
        resource_id=oracle_id,
        request=request,
    )
    return doc
