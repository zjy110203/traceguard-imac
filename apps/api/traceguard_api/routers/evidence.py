"""事件 / 证据 / 产物 API（原始证据可下载，完整性可校验）。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Response
from traceguard_core.errors import AppError

from ..services import get_services

router = APIRouter(prefix="/api/v1", tags=["evidence"])


@router.get("/events")
async def list_events(
    workspace_id: str | None = None,
    run_id: str | None = None,
    event_type: str | None = None,
    limit: int = Query(200, le=1000),
    offset: int = 0,
) -> list[dict[str, Any]]:
    events = get_services().rt.events.list_events(
        workspace_id, run_id=run_id, event_type=event_type, limit=limit, offset=offset
    )
    return [e.to_dict() for e in events]


@router.get("/events/stats")
async def event_stats(workspace_id: str | None = None) -> dict[str, Any]:
    return get_services().rt.events.stats(workspace_id)


@router.get("/events/conflicts")
async def event_conflicts() -> list[dict[str, Any]]:
    return get_services().rt.events.conflicts()


@router.get("/events/{event_id}")
async def get_event(event_id: str) -> dict[str, Any]:
    ev = get_services().rt.events.get(event_id)
    if not ev:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "event not found"})
    return ev.to_dict()


@router.get("/evidence")
async def list_evidence(
    workspace_id: str | None = None,
    run_id: str | None = None,
    kind: str | None = None,
    source_type: str | None = None,
    limit: int = Query(200, le=500),
    offset: int = 0,
) -> list[dict[str, Any]]:
    evds = get_services().rt.evidence.list(
        workspace_id=workspace_id,
        run_id=run_id,
        kind=kind,
        source_type=source_type,
        limit=limit,
        offset=offset,
    )
    return [e.to_dict() for e in evds]


@router.get("/evidence/{evidence_id}")
async def get_evidence(evidence_id: str) -> dict[str, Any]:
    rec = get_services().rt.evidence.get(evidence_id)
    if not rec:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "evidence not found"})
    return rec.to_dict()


@router.get("/evidence/{evidence_id}/download")
async def download_evidence(evidence_id: str) -> Response:
    rt = get_services().rt
    rec = rt.evidence.get(evidence_id)
    if not rec or not rec.attachment_ref:
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
async def list_artifacts(workspace_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    return get_services().rt.artifacts.list(workspace_id, limit)


@router.get("/artifacts/{artifact_id}/download")
async def download_artifact(artifact_id: str) -> Response:
    rt = get_services().rt
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
async def verify_evidence(evidence_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    rt = get_services().rt
    rec = rt.evidence.get(evidence_id)
    if not rec:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "evidence not found"})
    ok = rt.evidence.verify_integrity(evidence_id, payload.get("content") or {})
    return {"evidence_id": evidence_id, "integrity_check": "PASS" if ok else "FAIL"}
