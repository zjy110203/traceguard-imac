"""RUN-018 WP8：运行监控与告警 API。

端点：
- `GET  /api/v1/monitoring/metrics`   六类运行指标快照（API/Worker/Queue/Run/Artifact/Database），
  返回前顺带评估一次告警规则并带出活跃告警计数；
- `GET  /api/v1/monitoring/alerts`    告警列表（状态/类型过滤，Workspace 作用域过滤）；
- `GET  /api/v1/monitoring/alerts/{id}` 单条告警；
- `POST /api/v1/monitoring/alerts/{id}/acknowledge`  OPEN → ACKNOWLEDGED；
- `POST /api/v1/monitoring/alerts/{id}/resolve`      → RESOLVED（解决原因必填）。

权限口径（沿用 auth.action_for_request 既有映射，未新增权限面）：
- GET → `read`（所有认证角色）；Run/Queue 指标按调用方授权 Workspace 过滤，
  与 `/system/metrics`、`/queue/stats` 同口径，响应内 `scope` 显式声明；
- POST（acknowledge/resolve）→ 未知写操作 fail-closed 映射到 `system.configure`
  （platform-admin），处理主体取服务端 Principal，不采信请求体声明。

阈值全部为 provisional，响应中 `thresholds_status` 显式声明，不是 SLA。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel
from traceguard_persistence.alerts import AlertEngine, AlertStateError
from traceguard_persistence.monitoring import MetricsCollector

from ..services import get_services
from ..workspace import principal_of, scope_filter, visible_workspaces

router = APIRouter(prefix="/api/v1/monitoring", tags=["monitoring"])


class AcknowledgeReq(BaseModel):
    note: str | None = None


class ResolveReq(BaseModel):
    reason: str


def _collector() -> MetricsCollector:
    rt = get_services().rt
    return MetricsCollector(rt.db, rt.artifacts, worker_id=rt.worker_id)


def _engine() -> AlertEngine:
    return AlertEngine(get_services().rt.db)


def _actor(request: Request) -> str:
    """处理主体：认证开启取服务端 Principal；关闭时标注 dev（不得留空伪造）。"""
    principal = principal_of(request)
    if principal is None:
        return "operator:dev"
    return f"user:{principal.username}"


@router.get("/metrics")
async def monitoring_metrics(
    request: Request,
    workspace_id: str | None = Query(default=None),
    artifact_hash_limit: int | None = Query(default=None, ge=0, le=5000),
) -> dict[str, Any]:
    """运行指标快照 + 本次告警评估摘要。

    Workspace 口径与 `/system/metrics` 一致：
    显式 workspace_id 走 scope_filter（非授权 403）；不传则普通用户只统计授权
    Workspace 并集，platform-admin 得到全局汇总。Worker/Artifact/Database 为
    主机级聚合（不含业务数据），随响应声明 `scope`。
    """
    principal = principal_of(request)
    if workspace_id is not None:
        scope_filter(principal, workspace_id, resource_type="monitoring.metrics", request=request)
        scopes: list[str] | None = [workspace_id]
        mode = "workspace"
    else:
        allowed = visible_workspaces(principal)
        scopes = None if allowed is None else sorted(allowed)
        mode = "global" if scopes is None else "authorized_workspaces"

    collector = _collector()
    snapshot = collector.collect(workspace_ids=scopes, artifact_deep_limit=artifact_hash_limit)
    eval_summary = _engine().evaluate(snapshot)
    snapshot["alerts"] = {
        "summary": {
            "open": eval_summary.get("open", 0),
            "acknowledged": eval_summary.get("acknowledged", 0),
            "created_this_eval": eval_summary.get("created", []),
        },
        "evaluation": eval_summary,
    }
    snapshot["scope"] = {
        "mode": mode,
        "workspace_id": workspace_id,
        "authorized_workspaces": scopes,
        "global": scopes is None,
    }
    return snapshot


@router.get("/alerts")
async def list_alerts(
    request: Request,
    status: str | None = Query(default=None, pattern="^(OPEN|ACKNOWLEDGED|RESOLVED|open|acknowledged|resolved)$"),
    alert_type: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    """告警列表（评估一次规则后返回，保证列表反映最新条件）。"""
    principal = principal_of(request)
    allowed = visible_workspaces(principal)
    snapshot = _collector().collect(workspace_ids=allowed)
    evaluation = _engine().evaluate(snapshot)
    rows = _engine().list_alerts(
        status=status,
        alert_type=alert_type,
        workspace_ids=allowed,
        limit=limit,
    )
    return {
        "alerts": rows,
        "total": len(rows),
        "evaluation": evaluation,
        "scope": {
            "mode": "global" if allowed is None else "authorized_workspaces",
            "global": allowed is None,
            "authorized_workspaces": allowed,
        },
    }


@router.get("/alerts/{alert_id}")
async def get_alert(alert_id: str, request: Request) -> dict[str, Any]:
    principal = principal_of(request)
    allowed = visible_workspaces(principal)
    doc = _engine().get_alert(alert_id)
    if doc is None:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "alert not found"})
    ws = doc.get("scope_workspace")
    if allowed is not None and ws is not None and ws not in allowed:
        raise HTTPException(
            403,
            detail={
                "code": "WORKSPACE_FORBIDDEN",
                "message": f"workspace '{ws}' is outside your access scope",
                "resource_type": "alert",
                "resource_id": alert_id,
            },
        )
    return doc


@router.post("/alerts/{alert_id}/acknowledge")
async def acknowledge_alert(
    alert_id: str, request: Request, body: AcknowledgeReq | None = None
) -> dict[str, Any]:
    """OPEN → ACKNOWLEDGED；处理主体/时间由服务端记录。"""
    actor = _actor(request)
    try:
        doc = _engine().acknowledge(alert_id, actor)
    except KeyError:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "alert not found"}) from None
    except AlertStateError as exc:
        raise HTTPException(409, detail={"code": "ILLEGAL_STATE", "message": str(exc)}) from exc
    _audit_alert(request, actor, "alert.acknowledge", alert_id)
    return doc


@router.post("/alerts/{alert_id}/resolve")
async def resolve_alert(alert_id: str, request: Request, body: ResolveReq) -> dict[str, Any]:
    """→ RESOLVED；解决原因必填（记录处理主体/处理时间/解决原因）。"""
    if not (body.reason or "").strip():
        raise HTTPException(
            422, detail={"code": "RESOLUTION_REASON_REQUIRED", "message": "解决原因必填"}
        )
    actor = _actor(request)
    try:
        doc = _engine().resolve(alert_id, actor, body.reason)
    except KeyError:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "alert not found"}) from None
    except AlertStateError as exc:
        raise HTTPException(409, detail={"code": "ILLEGAL_STATE", "message": str(exc)}) from exc
    _audit_alert(request, actor, "alert.resolve", alert_id)
    return doc


def _audit_alert(request: Request, actor: str, action: str, alert_id: str) -> None:
    """告警处理写审计（尽力而为；审计失败不得回滚处理结果）。"""
    try:
        from traceguard_persistence import auth as auth_store

        from ..auth import get_auth_manager, request_id

        principal = principal_of(request)
        auth_store.record_audit(
            get_auth_manager(request).db,
            actor_id=principal.actor_id if principal else actor,
            role=principal.primary_role if principal else "dev",
            action=action,
            resource_type="alert",
            resource_id=alert_id,
            result="OK",
            request_id=request_id(request),
        )
    except Exception:  # noqa: BLE001
        pass
