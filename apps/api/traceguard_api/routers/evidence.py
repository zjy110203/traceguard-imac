"""事件 / 证据 / 产物 API（原始证据可下载，完整性可校验）。

RUN-011-R1.1 WP1：detail / 下载走对象级授权（以实体行 workspace 为准）；
列表默认只返回调用方有权范围，省略 workspace_id 不再读全库。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, Response
from traceguard_core.errors import AppError

from ..services import get_services
from ..workspace import principal_of, require_entity_workspace, scope_filter

router = APIRouter(prefix="/api/v1", tags=["evidence"])


@router.get("/events")
async def list_events(
    request: Request,
    workspace_id: str | None = None,
    run_id: str | None = None,
    event_type: str | None = None,
    limit: int = Query(200, le=1000),
    offset: int = 0,
) -> list[dict[str, Any]]:
    allowed = scope_filter(
        principal_of(request), workspace_id, resource_type="event", request=request
    )
    events = get_services().rt.events.list_events(
        workspace_id,
        run_id=run_id,
        event_type=event_type,
        limit=limit,
        offset=offset,
        workspace_ids=allowed,
    )
    return [e.to_dict() for e in events]


@router.get("/events/stats")
async def event_stats(request: Request, workspace_id: str | None = None) -> dict[str, Any]:
    allowed = scope_filter(
        principal_of(request), workspace_id, resource_type="event", request=request
    )
    if allowed is not None and len(allowed) == 1:
        return get_services().rt.events.stats(allowed[0])
    stats = get_services().rt.events.stats(workspace_id)
    if allowed is None:
        return stats
    # 多个可见 Workspace：合并统计（不泄露范围外数据由 list_events 的过滤保证）
    merged: dict[str, Any] = {"total": 0, "by_type": {}, "conflicts": 0}
    for ws in allowed:
        part = get_services().rt.events.stats(ws)
        merged["total"] += int(part.get("total") or 0)
        merged["conflicts"] = int(part.get("conflicts") or 0)
        for k, v in (part.get("by_type") or {}).items():
            merged["by_type"][k] = merged["by_type"].get(k, 0) + int(v)
    return merged


@router.get("/events/conflicts")
async def event_conflicts() -> list[dict[str, Any]]:
    return get_services().rt.events.conflicts()


@router.get("/events/{event_id}")
async def get_event(event_id: str, request: Request) -> dict[str, Any]:
    ev = get_services().rt.events.get(event_id)
    if not ev:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "event not found"})
    require_entity_workspace(
        principal_of(request),
        ev.workspace_id,
        resource_type="event",
        resource_id=event_id,
        request=request,
    )
    return ev.to_dict()


@router.get("/evidence")
async def list_evidence(
    request: Request,
    workspace_id: str | None = None,
    run_id: str | None = None,
    kind: str | None = None,
    source_type: str | None = None,
    limit: int = Query(200, le=500),
    offset: int = 0,
) -> list[dict[str, Any]]:
    allowed = scope_filter(
        principal_of(request), workspace_id, resource_type="evidence", request=request
    )
    evds = get_services().rt.evidence.list(
        workspace_id=workspace_id,
        run_id=run_id,
        kind=kind,
        source_type=source_type,
        limit=limit,
        offset=offset,
        workspace_ids=allowed,
    )
    return [e.to_dict() for e in evds]


def _evidence_or_404(evidence_id: str, request: Request):  # type: ignore[no-untyped-def]
    rec = get_services().rt.evidence.get(evidence_id)
    if not rec:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "evidence not found"})
    require_entity_workspace(
        principal_of(request),
        rec.workspace_id,
        resource_type="evidence",
        resource_id=evidence_id,
        request=request,
    )
    return rec


@router.get("/evidence/{evidence_id}")
async def get_evidence(evidence_id: str, request: Request) -> dict[str, Any]:
    return _evidence_or_404(evidence_id, request).to_dict()


@router.get("/evidence/{evidence_id}/download")
async def download_evidence(evidence_id: str, request: Request) -> Response:
    rt = get_services().rt
    rec = _evidence_or_404(evidence_id, request)
    if not rec.attachment_ref:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "no attachment"})
    try:
        data, meta = rt.artifacts.read(rec.attachment_ref)
    except AppError as exc:
        raise HTTPException(412, detail={"code": exc.code.value, "message": exc.message}) from exc
    return Response(
        content=data,
        media_type=meta["media_type"],
        headers={
            "Content-Disposition": f'inline; filename="{meta.get("original_name") or evidence_id}"',
            "X-TraceGuard-SHA256": meta["sha256"],
        },
    )


@router.get("/artifacts")
async def list_artifacts(
    request: Request, workspace_id: str | None = None, limit: int = 100
) -> list[dict[str, Any]]:
    allowed = scope_filter(
        principal_of(request), workspace_id, resource_type="artifact", request=request
    )
    return get_services().rt.artifacts.list(workspace_id, limit, workspace_ids=allowed)


@router.get("/artifacts/{artifact_id}/download")
async def download_artifact(artifact_id: str, request: Request) -> Response:
    rt = get_services().rt
    meta_probe = rt.artifacts.get_meta(artifact_id)
    if meta_probe:
        require_entity_workspace(
            principal_of(request),
            meta_probe.get("workspace_id"),
            resource_type="artifact",
            resource_id=artifact_id,
            request=request,
        )
    try:
        data, meta = rt.artifacts.read(artifact_id)
    except AppError as exc:
        raise HTTPException(404, detail={"code": exc.code.value, "message": exc.message}) from exc
    return Response(
        content=data,
        media_type=meta["media_type"],
        headers={
            "Content-Disposition": f'inline; filename="{meta.get("original_name") or artifact_id}"',
            "X-TraceGuard-SHA256": meta["sha256"],
        },
    )


@router.post("/evidence/{evidence_id}/verify")
async def verify_evidence(
    evidence_id: str,
    request: Request,
    payload: dict[str, Any] | None = None,
    workspace_id: str | None = None,
) -> dict[str, Any]:
    """服务端存储完整性验证（默认不需要调用者提交原始内容）。

    RUN-007-FINALIZATION-2（WP2/C3）：
    - 服务端读取持久化 Evidence 记录与实际存储内容，重算"持久化内容摘要"并与保存时摘要比较；
    - 附件通过 ArtifactStore.read() 读回并重算 SHA-256；
    - 派生证据校验来源引用（raw_ref 是 raw、同工作区、有 derivation_rule）；
    - `integrity_check` 只表示存储完整性（PASS/FAIL/NOT_RUN）。
    兼容旧请求：若调用者仍提交 `content`，单独放在 `legacy_source_check` 里，
    绝不与存储完整性合并成一个 PASS。
    """
    rt = get_services().rt
    rec = _evidence_or_404(evidence_id, request)
    storage = rt.evidence.verify_storage_integrity(
        evidence_id, expected_workspace=workspace_id, artifacts=rt.artifacts
    )
    out: dict[str, Any] = {
        "evidence_id": evidence_id,
        "integrity_check": storage["status"],
        "storage_integrity": storage,
        "service_side_verification": True,
        "workspace_id": rec.workspace_id,
    }
    content = (payload or {}).get("content")
    if content is not None:
        ok = rt.evidence.verify_integrity(evidence_id, content)
        out["legacy_source_check"] = {
            "provided_by_caller": True,
            "match": ok,
            "status": "PASS" if ok else "FAIL",
            "note": "仅比对调用者提供的原始来源内容；不代表持久化内容完整性",
        }
    return out
