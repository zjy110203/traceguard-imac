"""RUN-009 WP2：FastAPI 服务端身份、会话、CSRF 与审计接入。

核心原则：认证开启后，请求体中的 reviewer / approved_by / operator 等身份声明一律忽略，
审批主体只来自服务端验证过的 Principal；未认证请求 fail-closed，不回退匿名管理员。
"""

from __future__ import annotations

import hmac
import json
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from traceguard_core.config import Settings, get_settings
from traceguard_persistence import auth as auth_store
from traceguard_persistence.db import Database

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
PUBLIC_PATHS = {"/api/v1/auth/login", "/api/v1/auth/csrf"}


def action_for_request(method: str, path: str) -> str | None:
    """把 HTTP 请求映射到 RBAC 动作；未知写操作 fail-closed 交给 platform-admin。"""
    if path.startswith("/api/v1/auth/"):
        return None
    if method in SAFE_METHODS:
        return "read"
    if path.endswith("/review") and "/testcases/" in path:
        return "testcase.review"
    if path.endswith("/oracle-bindings"):
        return "oracle.bind"
    if "/oracles" in path:
        return "oracle.create"
    if path.endswith("/execute") or "/tasks" in path:
        return "run.execute"
    if "/repairs/" in path:
        if path.endswith("/promote"):
            return "repair.promote"
        if path.endswith("/rollback"):
            return "repair.rollback"
        if path.endswith("/approve") or path.endswith("/reject"):
            return "repair.approve"
        return "repair.submit"
    if "/probes" in path or "/probe-plan" in path or "/probe-suggestions" in path:
        return "probe.execute"
    if "/diagnos" in path or "/dispositions" in path or "/graph" in path:
        return "diagnosis.operate"
    if "/testcases" in path or "/intents" in path:
        return "testcase.create"
    if "/system" in path or "/benchmark" in path:
        return "system.configure"
    return "system.configure"


_failures: dict[str, list[float]] = {}
_failure_lock = threading.Lock()


def request_id(request: Request) -> str:
    value = request.headers.get("x-request-id") or getattr(request.state, "request_id", "")
    return str(value)[:120]


@dataclass(frozen=True)
class Principal:
    user_id: str
    username: str
    roles: tuple[str, ...]
    actor_id: str
    csrf_token: str
    session_token: str

    @property
    def primary_role(self) -> str:
        return self.roles[0] if self.roles else ""


class AuthManager:
    def __init__(self, db: Database, settings: Settings | None = None) -> None:
        self.db = db
        self.settings = settings or get_settings()

    def login(self, username: str, password: str, client: str = "") -> dict[str, Any]:
        key = f"{(username or '').lower()}|{client}"
        now = time.time()
        window = self.settings.auth_login_window_s
        with _failure_lock:
            recent = [t for t in _failures.get(key, []) if now - t <= window]
            _failures[key] = recent
            if len(recent) >= self.settings.auth_max_failed_logins:
                raise HTTPException(
                    429, detail={"code": "LOGIN_RATE_LIMITED", "message": "too many attempts"}
                )

        user = auth_store.get_user_by_username(self.db, username)
        valid = bool(
            user and user["active"] and auth_store.verify_password(password, user["password_hash"])
        )
        if not valid:
            with _failure_lock:
                _failures.setdefault(key, []).append(now)
            raise HTTPException(
                401, detail={"code": "INVALID_CREDENTIALS", "message": "invalid credentials"}
            )

        with _failure_lock:
            _failures.pop(key, None)
        try:
            roles = json.loads(user.get("roles") or "[]")
        except (TypeError, ValueError):
            roles = []
        session = auth_store.create_session(
            self.db, user["user_id"], self.settings.auth_session_ttl_s
        )
        auth_store.record_audit(
            self.db,
            actor_id=f"user:{user['username']}",
            role=",".join(roles),
            action="auth.login",
            resource_type="session",
            result="SUCCESS",
        )
        return {
            "username": user["username"],
            "roles": roles,
            "csrf_token": session["csrf_token"],
            "expires_in_s": session["expires_in_s"],
            "token": session["token"],
        }

    def resolve(self, token: str) -> Principal | None:
        record = auth_store.get_session(self.db, token)
        if not record:
            return None
        roles = tuple(str(r) for r in record.get("role_list", []))
        return Principal(
            user_id=str(record["user_id"]),
            username=str(record["username"]),
            roles=roles,
            actor_id=f"user:{record['username']}",
            csrf_token=str(record["csrf_token"]),
            session_token=token,
        )

    def logout(self, principal: Principal) -> None:
        auth_store.revoke_session(self.db, principal.session_token)
        auth_store.record_audit(
            self.db,
            actor_id=principal.actor_id,
            role=",".join(principal.roles),
            action="auth.logout",
            resource_type="session",
            result="SUCCESS",
        )


def get_auth_manager(request: Request) -> AuthManager:
    from .runtime import get_runtime

    manager = getattr(request.app.state, "auth_manager", None)
    if manager is None:
        manager = AuthManager(get_runtime().db)
        request.app.state.auth_manager = manager
    return manager


async def auth_middleware(request: Request, call_next):  # type: ignore[no-untyped-def]
    settings = get_settings()
    path = request.url.path
    request.state.request_id = request.headers.get("x-request-id") or secrets.token_hex(8)
    if (
        not settings.auth_enabled
        or not path.startswith("/api/v1/")
        or path in PUBLIC_PATHS
        or path.startswith("/api/v1/auth/login")
    ):
        return await call_next(request)
    token = request.cookies.get(settings.auth_cookie_name, "")
    principal = get_auth_manager(request).resolve(token)
    if principal is None:
        return JSONResponse(
            status_code=401,
            content={"detail": {"code": "UNAUTHENTICATED", "message": "authentication required"}},
            headers={"X-Request-ID": request.state.request_id},
        )
    if settings.auth_csrf_required and request.method not in SAFE_METHODS:
        supplied = request.headers.get("x-csrf-token", "")
        if not supplied or not hmac.compare_digest(supplied, principal.csrf_token):
            return JSONResponse(
                status_code=403,
                content={"detail": {"code": "CSRF_FAILED", "message": "invalid CSRF token"}},
                headers={"X-Request-ID": request.state.request_id},
            )
    action = action_for_request(request.method, path)
    if action and not auth_store.has_permission(principal.roles, action):
        auth_store.record_audit(
            get_auth_manager(request).db,
            actor_id=principal.actor_id,
            role=principal.primary_role,
            action=action,
            resource_type="http",
            resource_id=path[:160],
            result="DENIED",
            request_id=request_id(request),
        )
        return JSONResponse(
            status_code=403,
            content={
                "detail": {"code": "PERMISSION_DENIED", "message": f"permission denied: {action}"}
            },
            headers={"X-Request-ID": request.state.request_id},
        )
    request.state.principal = principal
    response = await call_next(request)
    response.headers["X-Request-ID"] = request.state.request_id
    return response


def current_principal(request: Request) -> Principal:
    principal = getattr(request.state, "principal", None)
    if principal is None:
        raise HTTPException(
            401, detail={"code": "UNAUTHENTICATED", "message": "authentication required"}
        )
    return principal


def trusted_actor(
    request: Request, declared: str, action: str, *, resource_type: str = "", resource_id: str = ""
) -> str:
    """返回服务端可信主体；认证关闭时仅用于显式迁移/dev，不允许空主体。

    认证开启时：忽略 declared，校验动作权限，并写审计日志。
    """
    settings = get_settings()
    principal = getattr(request.state, "principal", None)
    if principal is None:
        if settings.auth_enabled:
            raise HTTPException(
                401, detail={"code": "UNAUTHENTICATED", "message": "authentication required"}
            )
        if not declared or not str(declared).strip():
            # 保留原语义：空/空白主体交由业务层 require_human_actor 拒绝（412），不在此提前改状态码。
            return str(declared or "")
        return str(declared).strip()

    if not auth_store.has_permission(principal.roles, action):
        auth_store.record_audit(
            get_auth_manager(request).db,
            actor_id=principal.actor_id,
            role=principal.primary_role,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            result="DENIED",
            request_id=request_id(request),
        )
        raise HTTPException(
            403, detail={"code": "PERMISSION_DENIED", "message": f"permission denied: {action}"}
        )

    auth_store.record_audit(
        get_auth_manager(request).db,
        actor_id=principal.actor_id,
        role=principal.primary_role,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        result="SUCCESS",
        request_id=request_id(request),
        after={"declared_actor_ignored": str(declared or "")[:120]},
    )
    return principal.actor_id
