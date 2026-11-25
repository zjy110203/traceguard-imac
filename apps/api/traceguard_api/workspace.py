"""对象级授权与 Workspace 作用域（RUN-011-R1.1 WP1）。

统一出口，禁止各路由自行发明授权方式：

- **实体 Workspace 一律由服务端从数据库实体行确定**，请求体/查询参数里的
  `workspace_id` 只作为"调用方想看的范围"参与校验，不能用来改写实体归属；
- detail / 写操作在取到实体后调用 `require_entity_workspace()`；
- 列表接口调用 `scope_filter()`：省略 workspace 只返回有权范围，
  指定无权 workspace 直接 403，不允许"省略即全库"；
- 认证关闭（dev / 迁移）时 `principal_of()` 返回 `None`，保持既有行为。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from fastapi import HTTPException, Request

from .auth import Principal


def principal_of(request: Request) -> Principal | None:
    """认证开启时返回真实 Principal；认证关闭时返回 None。"""
    return getattr(request.state, "principal", None)


def visible_workspaces(principal: Principal | None) -> list[str] | None:
    """调用方可见的 Workspace 集合；`None` 表示不限制（platform-admin / `*`）。"""
    if principal is None:
        return None
    if "platform-admin" in principal.roles or "*" in principal.workspaces:
        return None
    return sorted({str(w) for w in principal.workspaces if w and w != "*"})


def workspace_forbidden(workspace_id: str, *, resource_type: str = "", resource_id: str = "") -> HTTPException:
    return HTTPException(
        403,
        detail={
            "code": "WORKSPACE_FORBIDDEN",
            "message": f"workspace '{workspace_id}' is outside your access scope",
            "resource_type": resource_type,
            "resource_id": resource_id,
        },
    )


def require_entity_workspace(
    principal: Principal | None,
    doc_workspace: str,
    *,
    resource_type: str = "",
    resource_id: str = "",
    request: Request | None = None,
) -> str:
    """拿到实体后校验其真实 Workspace（detail / 写操作）。"""
    ws = normalize_workspace(doc_workspace)
    if principal is None:
        return ws
    if not principal.workspace_allowed(ws):
        if request is not None:
            _audit_denied(request, principal, ws, resource_type, resource_id)
        raise workspace_forbidden(ws, resource_type=resource_type, resource_id=resource_id)
    return ws


def scope_filter(
    principal: Principal | None,
    requested: str | None = None,
    *,
    resource_type: str = "",
    request: Request | None = None,
) -> list[str] | None:
    """列表作用域。

    返回 `None` = 不按 Workspace 过滤（全部可见）；返回列表 = 只能出现这些 Workspace。
    请求了无权 Workspace 时 403，绝不静默扩大到全库。
    """
    if requested:
        ws = normalize_workspace(requested)
        if principal is not None and not principal.workspace_allowed(ws):
            if request is not None:
                _audit_denied(request, principal, ws, resource_type, "")
            raise workspace_forbidden(ws, resource_type=resource_type)
        return [ws]
    return visible_workspaces(principal)


def normalize_workspace(workspace_id: str | None) -> str:
    """空 Workspace 归一为 `default`（历史行为），避免 '' 与 'default' 混用。"""
    value = (workspace_id or "").strip()
    return value or "default"


def workspace_of(doc: dict[str, Any] | None, *keys: str) -> str:
    """从实体字典中提取 Workspace（按给定字段顺序，首个非空生效）。"""
    for key in keys or ("workspace_id",):
        value = (doc or {}).get(key)
        if value:
            return normalize_workspace(str(value))
    return "default"


def _audit_denied(
    request: Request,
    principal: Principal,
    workspace_id: str,
    resource_type: str,
    resource_id: str,
) -> None:
    """对象级拒绝写审计（尽力而为，不阻断拒绝本身）。"""
    try:
        from traceguard_persistence import auth as auth_store

        from .auth import get_auth_manager, request_id

        auth_store.record_audit(
            get_auth_manager(request).db,
            actor_id=principal.actor_id,
            role=principal.primary_role,
            action="workspace.read",
            resource_type=resource_type or "workspace",
            resource_id=f"{workspace_id}:{resource_id}"[:160],
            result="DENIED",
            request_id=request_id(request),
        )
    except Exception:  # noqa: BLE001 - 审计失败不得改变授权结果
        pass


def filter_by_workspaces(
    rows: Sequence[dict[str, Any]],
    allowed: list[str] | None,
    key: str = "workspace_id",
) -> list[dict[str, Any]]:
    """内存态二次过滤（用于无法下推到 SQL 的投影），防御性兜底。"""
    if allowed is None:
        return list(rows)
    allowed_set = {normalize_workspace(w) for w in allowed}
    return [r for r in rows if normalize_workspace(str(r.get(key) or "")) in allowed_set]
