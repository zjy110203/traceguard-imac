"""RUN-009 WP2：认证、会话、用户与审计 API。"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from traceguard_core.config import get_settings
from traceguard_persistence import auth as auth_store

from ..auth import AuthManager, Principal, current_principal, get_auth_manager
from ..runtime import get_runtime

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


class LoginReq(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


class PasswordReq(BaseModel):
    old_password: str = Field(min_length=1)
    new_password: str = Field(min_length=8)


class UserCreateReq(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=8)
    roles: list[str] = Field(default_factory=list)
    workspaces: list[str] = Field(default_factory=lambda: ["*"])


class WorkspacesReq(BaseModel):
    workspaces: list[str]


def _require_platform_admin(principal: Principal) -> None:
    if "platform-admin" not in principal.roles:
        raise HTTPException(
            403, detail={"code": "PERMISSION_DENIED", "message": "platform-admin required"}
        )


@router.get("/status")
async def auth_status() -> dict[str, Any]:
    """公开端点：告知前端认证是否开启（不泄露任何用户/会话信息）。"""
    from ..auth import _effective_auth_enabled

    return {
        "auth_enabled": _effective_auth_enabled(),
        "csrf_required": get_settings().auth_csrf_required,
    }


@router.post("/login")
async def login(req: LoginReq, request: Request, response: Response) -> dict[str, Any]:
    settings = get_settings()
    manager: AuthManager = get_auth_manager(request)
    client = request.client.host if request.client else ""
    result = manager.login(req.username, req.password, client)
    response.set_cookie(
        key=settings.auth_cookie_name,
        value=result["token"],
        max_age=result["expires_in_s"],
        httponly=True,
        samesite="lax",
        secure=settings.auth_cookie_secure,
    )
    return {k: v for k, v in result.items() if k != "token"}


@router.post("/logout")
async def logout(
    request: Request,
    response: Response,
    principal: Annotated[Principal, Depends(current_principal)],
) -> dict[str, str]:
    get_auth_manager(request).logout(principal)
    response.delete_cookie(get_settings().auth_cookie_name)
    return {"status": "ok"}


@router.get("/me")
async def me(
    principal: Annotated[Principal, Depends(current_principal)],
) -> dict[str, Any]:
    return {
        "user_id": principal.user_id,
        "username": principal.username,
        "roles": list(principal.roles),
        "actor_id": principal.actor_id,
    }


@router.post("/password")
async def change_password(
    req: PasswordReq,
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
) -> dict[str, str]:
    db = get_runtime().db
    ok = auth_store.change_password(db, principal.user_id, req.old_password, req.new_password)
    if not ok:
        raise HTTPException(
            400, detail={"code": "INVALID_PASSWORD", "message": "old password mismatch"}
        )
    auth_store.record_audit(
        db,
        actor_id=principal.actor_id,
        role=principal.primary_role,
        action="auth.password_changed",
        resource_type="user",
        resource_id=principal.user_id,
        result="SUCCESS",
    )
    return {"status": "ok"}


@router.get("/users")
async def list_users(
    principal: Annotated[Principal, Depends(current_principal)],
) -> list[dict[str, Any]]:
    _require_platform_admin(principal)
    users = []
    for user in auth_store.list_users(get_runtime().db):
        users.append(
            {
                "user_id": user["user_id"],
                "username": user["username"],
                "roles": user["roles"],
                "active": bool(user["active"]),
                "workspaces": auth_store.parse_workspaces(user.get("workspaces")),
            }
        )
    return users


@router.post("/users", status_code=201)
async def create_user(
    req: UserCreateReq,
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
) -> dict[str, Any]:
    _require_platform_admin(principal)
    try:
        user = auth_store.create_user(
            get_runtime().db, username=req.username, password=req.password, roles=req.roles
        )
        auth_store.set_user_workspaces(get_runtime().db, user["user_id"], req.workspaces)
    except ValueError as exc:
        raise HTTPException(400, detail={"code": str(exc), "message": str(exc)}) from exc
    auth_store.record_audit(
        get_runtime().db,
        actor_id=principal.actor_id,
        role=principal.primary_role,
        action="auth.user_created",
        resource_type="user",
        resource_id=user["user_id"],
        result="SUCCESS",
        after={"roles": req.roles, "workspaces": req.workspaces},
    )
    return {**user, "workspaces": auth_store.parse_workspaces(req.workspaces)}


@router.post("/users/{user_id}/active")
async def set_user_active(
    user_id: str,
    active: bool,
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
) -> dict[str, Any]:
    _require_platform_admin(principal)
    ok = auth_store.set_user_active(get_runtime().db, user_id, active)
    if not ok:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "user not found"})
    if not active:
        auth_store.revoke_all_sessions(get_runtime().db, user_id)
    auth_store.record_audit(
        get_runtime().db,
        actor_id=principal.actor_id,
        role=principal.primary_role,
        action="auth.user_active_changed",
        resource_type="user",
        resource_id=user_id,
        result="SUCCESS",
        after={"active": active},
    )
    return {"user_id": user_id, "active": active}


@router.post("/users/{user_id}/workspaces")
async def set_user_workspaces(
    user_id: str,
    req: WorkspacesReq,
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
) -> dict[str, Any]:
    """更新用户 workspace 数据访问范围（对象级授权）。停用用户的会话全部撤销。"""
    _require_platform_admin(principal)
    ok = auth_store.set_user_workspaces(get_runtime().db, user_id, req.workspaces)
    if not ok:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "user not found"})
    auth_store.record_audit(
        get_runtime().db,
        actor_id=principal.actor_id,
        role=principal.primary_role,
        action="auth.user_workspaces_changed",
        resource_type="user",
        resource_id=user_id,
        result="SUCCESS",
        after={"workspaces": auth_store.parse_workspaces(req.workspaces)},
    )
    return {"user_id": user_id, "workspaces": auth_store.parse_workspaces(req.workspaces)}


@router.get("/permissions")
async def permission_matrix(
    principal: Annotated[Principal, Depends(current_principal)],
) -> dict[str, Any]:
    """角色 → 动作映射（展示用途）。服务端始终是最终权限判定者。"""
    return {"matrix": auth_store.PERMISSION_MATRIX}


@router.get("/audit/verify")
async def verify_audit(
    principal: Annotated[Principal, Depends(current_principal)],
) -> dict[str, Any]:
    _require_platform_admin(principal)
    return auth_store.verify_audit_chain(get_runtime().db)
