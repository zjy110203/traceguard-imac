"""系统 API：健康、浏览器自检、种子数据、运行统计。"""

from __future__ import annotations

import threading
from typing import Any

from fastapi import APIRouter, Query
from starlette.concurrency import run_in_threadpool

from ..seed import seed_teamflow_catalog
from ..services import get_services

router = APIRouter(prefix="/api/v1/system", tags=["system"])

_browser_cache: dict[str, Any] | None = None
_browser_lock = threading.Lock()


@router.get("/health")
async def health() -> dict[str, Any]:
    rt = get_services().rt
    return {
        "status": "ok",
        "app": rt.settings.app_name,
        "environment": rt.settings.environment,
        **rt.health(),
    }


@router.get("/browser")
async def browser_health(
    probe: bool = Query(False, description="是否真实启动一次浏览器"),
) -> dict[str, Any]:
    global _browser_cache
    if not probe and _browser_cache:
        return _browser_cache
    from traceguard_execution.browser.manager import BrowserUnavailableError, probe_browser

    with _browser_lock:
        try:
            # Playwright 同步 API 不能运行在事件循环线程，必须放线程池
            info = await run_in_threadpool(probe_browser)
            _browser_cache = {"available": True, **info}
        except BrowserUnavailableError as exc:
            _browser_cache = {"available": False, "error": str(exc)}
        except Exception as exc:  # noqa: BLE001 - 自检失败不应 500
            _browser_cache = {"available": False, "error": f"{type(exc).__name__}: {exc}"}
        return _browser_cache


@router.post("/seed")
async def seed() -> dict[str, Any]:
    rt = get_services().rt
    return seed_teamflow_catalog(rt, base_url=rt.settings.teamflow_base_url)


@router.get("/metrics")
async def metrics() -> dict[str, Any]:
    rt = get_services().rt
    runs = rt.tasks.list_runs(limit=500)
    verdicts: dict[str, int] = {}
    for r in runs:
        verdicts[r["test_verdict"]] = verdicts.get(r["test_verdict"], 0) + 1
    return {
        "runs": len(runs),
        "verdicts": verdicts,
        "evidence": rt.evidence.count_all(),
        "events": rt.events.count_all(),
        "diagnoses": len(rt.docs.list(kind="diagnosis", limit=500)),
        "repairs": len(rt.docs.list(kind="repair", limit=500)),
        "testcases": len(rt.catalog.list_testcases()),
        "intents": len(rt.catalog.list_intents()),
    }


@router.get("/config")
async def config() -> dict[str, Any]:
    rt = get_services().rt
    s = rt.settings
    return {
        "database": s.database_url.split(":")[0],
        "teamflow_base_url": s.teamflow_base_url,
        "browser": {
            "headless": s.browser_headless,
            "channel": s.browser_channel or "bundled-chromium",
            "timeout_ms": s.browser_timeout_ms,
        },
        "worker": {"id": rt.worker_id, "inline": s.inline_worker},
        "artifact_dir": s.artifact_dir,
        "environment": s.environment,
    }
