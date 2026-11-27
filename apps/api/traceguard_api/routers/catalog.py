"""测试资产 API：Workspace / TestIntent / TestCase（版本、审核）/ Oracle。

RUN-011-R1.1 WP1：实体 Workspace 一律以服务端数据库行为准；写入前校验调用方
对该 Workspace 的权限；detail 路由做对象级授权；列表默认只返回有权范围。
"""

from __future__ import annotations

import difflib
import json
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
async def create_workspace(req: WorkspaceReq, request: Request) -> dict[str, Any]:
    """创建 Workspace。创建者只取服务端可信主体；请求体中的 created_by 一律忽略。"""
    created_by = (
        trusted_actor(
            request,
            "",
            "system.configure",
            resource_type="workspace",
            resource_id="create",
        )
        or "system"
    )
    try:
        return get_services().rt.catalog.create_workspace(req.name, req.description, created_by)
    except AppError as exc:
        raise _handle(exc) from exc


@router.get("/workspaces/{workspace_id}")
async def get_workspace(workspace_id: str, request: Request) -> dict[str, Any]:
    doc = get_services().rt.catalog.get_workspace(workspace_id)
    if not doc:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "workspace not found"})
    require_entity_workspace(
        principal_of(request),
        workspace_id,
        resource_type="workspace",
        resource_id=workspace_id,
        request=request,
    )
    return doc


class WorkspaceUpdateReq(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    description: str | None = None


@router.patch("/workspaces/{workspace_id}")
async def update_workspace(
    workspace_id: str, req: WorkspaceUpdateReq, request: Request
) -> dict[str, Any]:
    require_entity_workspace(
        principal_of(request),
        workspace_id,
        resource_type="workspace",
        resource_id=workspace_id,
        request=request,
    )
    trusted_actor(
        request,
        "",
        "system.configure",
        resource_type="workspace",
        resource_id=workspace_id,
    )
    try:
        return get_services().rt.catalog.update_workspace(
            workspace_id, name=req.name, description=req.description
        )
    except AppError as exc:
        raise _handle(exc) from exc


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
    return get_services().rt.catalog.list_intents(workspace_id, status, workspace_ids=allowed)


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


class IntentUpdateReq(BaseModel):
    title: str | None = Field(default=None, min_length=1)
    description: str | None = Field(default=None, min_length=1)
    owner: str | None = None
    requirement_refs: list[str] | None = None
    preconditions: list[str] | None = None
    candidate_assertions: list[str] | None = None
    business_context: dict[str, Any] | None = None


class StatusReq(BaseModel):
    status: str


@router.patch("/intents/{intent_id}")
async def update_intent(intent_id: str, req: IntentUpdateReq, request: Request) -> dict[str, Any]:
    _intent_or_404(intent_id, request)
    trusted_actor(
        request,
        "",
        "testcase.create",
        resource_type="intent",
        resource_id=intent_id,
    )
    try:
        doc = get_services().rt.catalog.update_intent(
            intent_id, {k: v for k, v in req.model_dump().items() if v is not None}
        )
    except AppError as exc:
        raise _handle(exc) from exc
    return doc


def _advance_intent(
    intent_id: str,
    status: str,
    action: str,
    request: Request,
    resource_id: str,
) -> dict[str, Any]:
    trusted_actor(request, "", action, resource_type="intent", resource_id=resource_id or intent_id)
    try:
        return get_services().rt.catalog.update_intent_status(intent_id, status)
    except AppError as exc:
        raise _handle(exc) from exc


@router.post("/intents/{intent_id}/submit")
async def submit_intent(intent_id: str, request: Request) -> dict[str, Any]:
    """提交评审：testcase.create 权限即可，但不能直接审批。"""
    _intent_or_404(intent_id, request)
    return _advance_intent(intent_id, "REVIEW", "testcase.create", request, intent_id)


@router.post("/intents/{intent_id}/approve")
async def approve_intent(intent_id: str, request: Request) -> dict[str, Any]:
    """审核通过：必须持有 testcase.review，服务端可信主体写审计。"""
    _intent_or_404(intent_id, request)
    return _advance_intent(intent_id, "APPROVED", "testcase.review", request, intent_id)


@router.post("/intents/{intent_id}/status")
async def set_intent_status(intent_id: str, req: StatusReq, request: Request) -> dict[str, Any]:
    """兼容旧端点；审批目标额外校验 testcase.review，阻断创建权限自提权。"""
    _intent_or_404(intent_id, request)
    action = "testcase.review" if req.status == "APPROVED" else "testcase.create"
    return _advance_intent(intent_id, req.status, action, request, intent_id)


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


class TestCaseVersionReq(BaseModel):
    """基于既有用例创建新版本：不可变版本只增不改，历史 Run 引用固定。"""

    title: str | None = Field(default=None, min_length=1)
    intent_ref: str | None = None
    steps: list[StepReq] | None = None
    assertions: list[AssertionReq] | None = None
    cleanup_policy: str | None = None
    run_config: dict[str, Any] | None = None
    compile_source: Literal["human", "compiler", "healed"] | None = None
    preconditions: list[str] | None = None
    test_data_refs: list[str] | None = None


@router.post("/testcases/{testcase_id}/versions", status_code=201)
async def create_testcase_version(
    testcase_id: str, req: TestCaseVersionReq, request: Request
) -> dict[str, Any]:
    base = _testcase_or_404(testcase_id, request)
    trusted_actor(
        request,
        "",
        "testcase.create",
        resource_type="testcase",
        resource_id=testcase_id,
    )
    ws = base["workspace_id"]
    latest = base.get("latest") or {}
    payload = {
        "testcase_id": testcase_id,
        "version": 1,  # 服务端递增，忽略客户端声明
        "intent_ref": req.intent_ref if req.intent_ref is not None else base.get("intent_ref"),
        "steps": [s.model_dump() for s in req.steps]
        if req.steps is not None
        else latest.get("steps", []),
        "assertions": [a.model_dump() for a in req.assertions]
        if req.assertions is not None
        else latest.get("assertions", []),
        "test_data_refs": req.test_data_refs
        if req.test_data_refs is not None
        else latest.get("test_data_refs", []),
        "preconditions": req.preconditions
        if req.preconditions is not None
        else latest.get("preconditions", []),
        "cleanup_policy": req.cleanup_policy or latest.get("cleanup_policy", "NONE"),
        "run_config": req.run_config
        if req.run_config is not None
        else latest.get("run_config", {}),
        "compile_source": req.compile_source or base.get("compile_source", "human"),
        "created_at": utc_now_iso(),
        "workspace_id": ws,
    }
    try:
        return get_services().rt.catalog.create_testcase(
            payload,
            title=req.title or base.get("title", testcase_id),
            workspace_id=ws,
            review_status="PENDING",
        )
    except AppError as exc:
        raise _handle(exc) from exc


def _diffable_lines(doc: dict[str, Any]) -> list[str]:
    volatile = {"created_at", "updated_at", "review_history", "evaluation_history"}
    clean = {k: v for k, v in doc.items() if k not in volatile}
    return json.dumps(clean, ensure_ascii=False, sort_keys=True, indent=2).splitlines()


@router.get("/testcases/{testcase_id}/versions/{from_version}/diff/{to_version}")
async def diff_testcase_versions(
    testcase_id: str, from_version: int, to_version: int, request: Request
) -> dict[str, Any]:
    _testcase_or_404(testcase_id, request)
    left = get_services().rt.catalog.get_version(testcase_id, from_version)
    right = get_services().rt.catalog.get_version(testcase_id, to_version)
    if not left or not right:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "version not found"})
    changed = sorted(
        key
        for key in set(left) | set(right)
        if key not in {"created_at", "updated_at"} and left.get(key) != right.get(key)
    )
    diff = "\n".join(
        difflib.unified_diff(
            _diffable_lines(left),
            _diffable_lines(right),
            fromfile=f"{testcase_id}@v{from_version}",
            tofile=f"{testcase_id}@v{to_version}",
            lineterm="",
        )
    )
    return {
        "testcase_id": testcase_id,
        "from_version": from_version,
        "to_version": to_version,
        "changed_fields": changed,
        "diff": diff,
    }


class ReviewReq(BaseModel):
    decision: str
    reviewer: str
    note: str = ""
    #: RUN-011-R1.2：公共 HTTP 审核入口不接受关闭稳定性门禁。
    #: 历史数据迁移豁免只保留在可信服务端内部路径（persistence 服务层），不通过 HTTP 暴露。
    require_stability: Literal[True] = True


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
            # 显式固定：即使客户端携带其它值，也不得影响稳定性门禁。
            require_stability=True,
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


@router.get("/oracles/{oracle_id}/versions")
async def list_oracle_versions(oracle_id: str, request: Request) -> list[dict[str, Any]]:
    versions = get_services().rt.catalog.list_oracle_versions(oracle_id)
    if not versions:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "oracle not found"})
    require_entity_workspace(
        principal_of(request),
        versions[0].get("workspace_id"),
        resource_type="oracle",
        resource_id=oracle_id,
        request=request,
    )
    return versions


@router.get("/oracles/{oracle_id}/versions/{version}")
async def get_oracle_version(oracle_id: str, version: int, request: Request) -> dict[str, Any]:
    doc = get_services().rt.catalog.get_oracle(oracle_id, version)
    if not doc:
        raise HTTPException(
            404, detail={"code": "NOT_FOUND", "message": "oracle version not found"}
        )
    require_entity_workspace(
        principal_of(request),
        doc.get("workspace_id"),
        resource_type="oracle_version",
        resource_id=f"{oracle_id}@{version}",
        request=request,
    )
    return doc


@router.get("/oracles/{oracle_id}/references")
async def get_oracle_references(
    oracle_id: str, request: Request, version: int | None = None
) -> list[dict[str, Any]]:
    current = get_services().rt.catalog.get_oracle(oracle_id, version)
    if not current:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "oracle not found"})
    require_entity_workspace(
        principal_of(request),
        current.get("workspace_id"),
        resource_type="oracle",
        resource_id=oracle_id,
        request=request,
    )
    return get_services().rt.catalog.oracle_references(oracle_id, version)


class OracleApproveReq(BaseModel):
    approved_by: str
    note: str = ""


@router.post("/oracles/{oracle_id}/versions/{version}/approve")
async def approve_oracle(
    oracle_id: str, version: int, req: OracleApproveReq, request: Request
) -> dict[str, Any]:
    """人工审批：基于既有版本新建已审批版本；Agent/LLM 主体一律被服务层拒绝。"""
    current = get_services().rt.catalog.get_oracle(oracle_id, version)
    if not current:
        raise HTTPException(
            404, detail={"code": "NOT_FOUND", "message": "oracle version not found"}
        )
    require_entity_workspace(
        principal_of(request),
        current.get("workspace_id"),
        resource_type="oracle",
        resource_id=oracle_id,
        request=request,
    )
    approver = trusted_actor(
        request,
        req.approved_by,
        "oracle.create",
        resource_type="oracle",
        resource_id=f"{oracle_id}@{version}",
    )
    try:
        return get_services().rt.catalog.approve_oracle(oracle_id, version, approver, req.note)
    except AppError as exc:
        raise _handle(exc) from exc
