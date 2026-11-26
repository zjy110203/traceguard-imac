"""RUN-009 WP2：FastAPI 服务端身份、会话、CSRF 与审计接入。

核心原则：认证开启后，请求体中的 reviewer / approved_by / operator 等身份声明一律忽略，
审批主体只来自服务端验证过的 Principal；未认证请求 fail-closed，不回退匿名管理员。
"""

from __future__ import annotations

import hmac
import json
import secrets
import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from traceguard_core.config import Settings, get_settings
from traceguard_persistence import auth as auth_store
from traceguard_persistence.db import Database

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
PUBLIC_PATHS = {"/api/v1/auth/login", "/api/v1/auth/csrf", "/api/v1/auth/status"}

#: RUN-011-R1.2：三个无 Workspace 过滤的全局运维观测接口。
#: RUN-014 WP3：追加 `/system/llm/summary`（全局模型运行摘要，含跨 Workspace 调用/成本）
#: 与 `/system/browser`（可触发真实浏览器自检，属系统级运维动作）。
#: 必须先于通用 SAFE_METHODS → read 映射判定，避免普通 read 角色越权读取。
GLOBAL_OBSERVATION_PATHS = {
    "/api/v1/events/conflicts",
    "/api/v1/probes/audit",
    "/api/v1/inbox/stats",
    "/api/v1/system/llm/summary",
    "/api/v1/system/browser",
}


def action_for_request(method: str, path: str) -> str | None:
    """把 HTTP 请求映射到 RBAC 动作；未知写操作 fail-closed 交给 platform-admin。"""
    if path.startswith("/api/v1/auth/"):
        return None
    normalized = path.rstrip("/") or "/"
    if method in SAFE_METHODS and normalized in GLOBAL_OBSERVATION_PATHS:
        return "system.observe"
    if method in SAFE_METHODS:
        return "read"
    # RUN-011 WP1：语义修正（不是放宽权限）——
    # 1) 探索轨迹编译产出的是**候选用例**，属于 testcase.create，而不是系统配置；
    #    旧映射落到 system.configure，导致 qa-engineer 无法把自己的探索编译成候选
    #    （真实闭环中会被 403 挡住，属 RBAC 映射错误而非安全策略）。
    # 2) 证据校验是只读完整性重算（服务端读库 + 重算摘要），属于 read；
    #    旧映射落到 system.configure，工程角色无法自证证据未被篡改。
    if "/explorations/" in path and path.endswith("/compile"):
        return "testcase.create"
    if path.endswith("/verify") and "/evidence/" in path:
        return "read"
    if path.endswith("/review") and "/testcases/" in path:
        return "testcase.review"
    if path.endswith("/oracle-bindings"):
        return "oracle.bind"
    if "/oracles" in path:
        return "oracle.create"
    if path.endswith("/execute") or "/tasks" in path:
        return "run.execute"
    if "/repairs" in path and (path == "/api/v1/repairs" or path.startswith("/api/v1/repairs/")):
        if path.endswith("/promote"):
            return "repair.promote"
        if path.endswith("/promote-recover"):
            # RUN-016 WP2.3：晋升恢复与晋升同级，不能落到 repair.submit
            return "repair.promote"
        if path.endswith("/rollback"):
            return "repair.rollback"
        if path.endswith("/regression-run") or path.endswith("/regression"):
            # RUN-014 WP1：回归验证会创建真实 Run 并改变修复的验证结论，
            # 与晋升同级的正式资产动作，不能落到 repair.submit（qa-engineer）。
            return "repair.regression"
        if path.endswith("/regression-retry"):
            # RUN-017 WP3：人工授权重试同样属于 repair.regression（qa-lead / platform-admin），
            # 且服务层还会强制 require_human_actor + confirm=true。
            return "repair.regression"
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


# 未知用户也执行一次等价 PBKDF2 验证，抹平"用户不存在"与"密码错误"的响应时差。
_dummy_password_hash = auth_store.hash_password("timing-equalizer-run010")


def _equalize_login_timing() -> None:
    """对不存在的用户名消耗一次真实 KDF 计算，防止用户名枚举计时侧信道。"""
    auth_store.verify_password("not-a-real-password", _dummy_password_hash)


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
    workspaces: tuple[str, ...] = ("*",)

    @property
    def primary_role(self) -> str:
        return self.roles[0] if self.roles else ""

    def workspace_allowed(self, workspace_id: str) -> bool:
        """对象级授权：`*` = 全部；否则显式白名单。platform-admin 永远放行。"""
        if "platform-admin" in self.roles or "*" in self.workspaces:
            return True
        return workspace_id in self.workspaces


def resolve_workspace_scope(principal: Principal, request: Request) -> str | None:
    """从请求中提取 workspace 作用域（query 优先，body 由路由层自行校验）。

    返回 None 表示请求未携带 workspace 维度（如 /users/{id} 这类无对象路径），
    交由路由层在拿到实体 workspace 后调用 require_workspace_doc 校验。
    """
    workspace = request.query_params.get("workspace_id")
    if workspace:
        return workspace
    for other in ("workspace", "ws"):
        value = request.query_params.get(other)
        if value:
            return value
    return None


def require_workspace_doc(principal: Principal, doc_workspace: str) -> None:
    """拿到实体后的对象级校验（detail 路由用）。拒绝时 403。"""
    if not principal.workspace_allowed(doc_workspace):
        raise HTTPException(
            403,
            detail={
                "code": "WORKSPACE_FORBIDDEN",
                "message": f"workspace '{doc_workspace}' is outside your access scope",
            },
        )


class AuthManager:
    def __init__(self, db: Database, settings: Settings | None = None) -> None:
        self.db = db
        self.settings = settings or get_settings()
        self._last_prune = 0.0

    def login(self, username: str, password: str, client: str = "") -> dict[str, Any]:
        """数据库级登录限速：失败计数持久化在 tg_login_attempt，多进程共享。

        不可通过重启单个进程绕过；即使多个 uvicorn Worker / 多副本，同一
        (username, client) 的失败窗口计数一致。
        """
        window = self.settings.auth_login_window_s
        failed = auth_store.count_failed_logins(
            self.db, username=username, client=client, window_s=window
        )
        if failed >= self.settings.auth_max_failed_logins:
            raise HTTPException(
                429,
                detail={
                    "code": "LOGIN_RATE_LIMITED",
                    "message": "too many failed attempts; try again later",
                },
            )

        user = auth_store.get_user_by_username(self.db, username)
        valid = bool(
            user and user["active"] and auth_store.verify_password(password, user["password_hash"])
        )
        # 用户不存在时执行等价 KDF，抹平响应时差（防用户名枚举）。
        auth_store.record_login_attempt(self.db, username=username, client=client, ok=valid)
        if not valid:
            if user is None:
                _equalize_login_timing()
            raise HTTPException(
                401, detail={"code": "INVALID_CREDENTIALS", "message": "invalid credentials"}
            )
        self._maybe_prune_attempts()
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
            "workspaces": auth_store.user_workspace_scope(user),
            "csrf_token": session["csrf_token"],
            "expires_in_s": session["expires_in_s"],
            "token": session["token"],
        }

    def _maybe_prune_attempts(self) -> None:
        """低频清理过期登录尝试记录（每 60s 至多一次，失败不影响登录）。"""
        now = time.time()
        if now - self._last_prune < 60:
            return
        self._last_prune = now
        try:
            auth_store.prune_login_attempts(
                self.db, older_than_s=max(self.settings.auth_login_window_s, 3600)
            )
        except Exception:  # noqa: BLE001 - 清理失败不应阻断登录
            pass

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
            workspaces=tuple(auth_store.parse_workspaces(record.get("workspaces"))),
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


@lru_cache(maxsize=1)
def _effective_auth_enabled() -> bool:
    """认证最终生效开关（含生产默认开启推导），与启动期 auth_policy 一致。"""
    from .auth_policy import evaluate_auth_policy

    return evaluate_auth_policy(get_settings()).auth_effective


def reset_auth_policy_cache() -> None:
    """测试用：环境变量变更后清空策略缓存。"""
    _effective_auth_enabled.cache_clear()


async def auth_middleware(request: Request, call_next):  # type: ignore[no-untyped-def]
    settings = get_settings()
    path = request.url.path
    request.state.request_id = request.headers.get("x-request-id") or secrets.token_hex(8)
    if (
        not _effective_auth_enabled()
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
    # RUN-010 WP1：对象级授权第一层——query 携带 workspace_id 时校验用户数据范围。
    # 仅按 HTTP 路径映射权限 ≠ workspace 级数据隔离；detail 路由的实体级校验
    # 由各路由在取得实体后调用 require_workspace_doc 完成。
    scope = resolve_workspace_scope(principal, request)
    if scope is not None and not principal.workspace_allowed(scope):
        return JSONResponse(
            status_code=403,
            content={
                "detail": {
                    "code": "WORKSPACE_FORBIDDEN",
                    "message": f"workspace '{scope}' is outside your access scope",
                }
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
    principal = getattr(request.state, "principal", None)
    if principal is None:
        if _effective_auth_enabled():
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
