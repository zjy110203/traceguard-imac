"""测试资产 API：Workspace / TestIntent / TestCase（版本、审核）/ Oracle。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from traceguard_core.errors import AppError
from traceguard_core.ids import new_id, utc_now_iso

from ..auth import trusted_actor
from ..services import get_services

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


# ---------------------------------------------------------------- workspaces
class WorkspaceReq(BaseModel):
    name: str = Field(min_length=1)
    description: str = ""
    created_by: str = "human:admin"


@router.get("/workspaces")
async def list_workspaces() -> list[dict[str, Any]]:
    return get_services().rt.catalog.list_workspaces()


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
    workspace_id: str | None = None, status: str | None = None
) -> list[dict[str, Any]]:
    return get_services().rt.catalog.list_intents(workspace_id, status)


@router.post("/intents", status_code=201)
async def create_intent(req: IntentReq) -> dict[str, Any]:
    payload = {
        "intent_id": req.intent_id or new_id("intent"),
        "title": req.title,
        "description": req.description,
        "owner": req.owner,
        "requirement_refs": req.requirement_refs,
        "preconditions": req.preconditions,
        "candidate_assertions": req.candidate_assertions,
        "business_context": req.business_context,
        "workspace_id": req.workspace_id,
        "status": "DRAFT",
    }
    try:
        return get_services().rt.catalog.create_intent(payload)
    except AppError as exc:
        raise _handle(exc) from exc


@router.get("/intents/{intent_id}")
async def get_intent(intent_id: str) -> dict[str, Any]:
    doc = get_services().rt.catalog.get_intent(intent_id)
    if not doc:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "intent not found"})
    return doc


class StatusReq(BaseModel):
    status: str


@router.post("/intents/{intent_id}/status")
async def set_intent_status(intent_id: str, req: StatusReq) -> dict[str, Any]:
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
    compile_source: str = "human"
    preconditions: list[str] = []
    test_data_refs: list[str] = []


@router.get("/testcases")
async def list_testcases(
    workspace_id: str | None = None, review_status: str | None = None
) -> list[dict[str, Any]]:
    return get_services().rt.catalog.list_testcases(workspace_id, review_status)


@router.post("/testcases", status_code=201)
async def create_testcase(req: TestCaseReq) -> dict[str, Any]:
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
        "workspace_id": req.workspace_id,
    }
    try:
        doc = get_services().rt.catalog.create_testcase(
            payload, title=req.title, workspace_id=req.workspace_id, review_status="PENDING"
        )
    except AppError as exc:
        raise _handle(exc) from exc
    return doc


@router.get("/testcases/{testcase_id}")
async def get_testcase(testcase_id: str) -> dict[str, Any]:
    doc = get_services().rt.catalog.get_testcase(testcase_id)
    if not doc:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "testcase not found"})
    return doc


@router.get("/testcases/{testcase_id}/versions")
async def list_versions(testcase_id: str) -> list[dict[str, Any]]:
    return get_services().rt.catalog.list_versions(testcase_id)


@router.get("/testcases/{testcase_id}/versions/{version}")
async def get_version(testcase_id: str, version: int) -> dict[str, Any]:
    doc = get_services().rt.catalog.get_version(testcase_id, version)
    if not doc:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "version not found"})
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
    actor = trusted_actor(
        request, req.actor, "oracle.bind", resource_type="testcase", resource_id=testcase_id
    )
    try:
        return get_services().rt.catalog.bind_oracles(testcase_id, version, req.bindings, actor=actor)
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
async def list_oracles(workspace_id: str | None = None) -> list[dict[str, Any]]:
    return get_services().rt.catalog.list_oracles(workspace_id)


@router.post("/oracles", status_code=201)
async def create_oracle(req: OracleReq, request: Request) -> dict[str, Any]:
    approved_by = ""
    if req.approved:
        approved_by = trusted_actor(
            request, req.approved_by, "oracle.create", resource_type="oracle", resource_id=req.assertion_id
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
        "workspace_id": req.workspace_id,
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
async def get_oracle(oracle_id: str, version: int | None = None) -> dict[str, Any]:
    doc = get_services().rt.catalog.get_oracle(oracle_id, version)
    if not doc:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "oracle not found"})
    return doc
