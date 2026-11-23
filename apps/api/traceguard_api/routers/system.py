"""系统 API：健康、浏览器自检、种子数据、运行统计、模型运行摘要。"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query
from starlette.concurrency import run_in_threadpool
from traceguard_agents.gateway.llm_gateway import TokenUsageTracker

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


@router.get("/llm/summary")
async def llm_summary() -> dict[str, Any]:
    """只读模型运行摘要。

    安全边界：不返回密钥、Prompt 原文、页面内容或逐条用量明细；
    成本是应用侧估算；configured 是静态配置态，不等于真实连通。
    """
    rt = get_services().rt
    s = rt.settings
    configured = s.llm_provider.strip().lower() == "deepseek" and bool(s.deepseek_api_key)
    usage_path = Path(os.environ.get("TG_TRACEGUARD_ROOT", ".")) / ".traceguard" / "llm_usage.jsonl"
    tracker = TokenUsageTracker(path=str(usage_path), daily_budget_usd=float(s.llm_daily_budget_usd))
    summary = tracker.summary()

    # 规则兜底来自真实探索/诊断文档，不从用量文件臆测。
    fallback_calls = 0
    llm_calls = 0
    rule_decisions = 0
    advisor_called = 0
    advisor_skipped = 0
    for rec in rt.docs.list(kind="exploration", limit=1000):
        doc = rec.doc or {}
        engine = str(doc.get("engine") or "")
        stats = doc.get("llm_stats") or {}
        if isinstance(stats, dict):
            fallback_calls += int(stats.get("fallback_calls") or 0)
            llm_calls += int(stats.get("llm_calls") or 0)
        if engine and not engine.startswith("llm:"):
            # engine 只表示规划器配置，不把 engine=llm:* 的动作都算作 LLM 成功。
            rule_decisions += 1
    for rec in rt.docs.list(kind="diagnosis", limit=1000):
        doc = rec.doc or {}
        advisor = doc.get("llm_advisor") or {}
        if isinstance(advisor, dict):
            if advisor.get("called"):
                advisor_called += 1
            if advisor.get("skipped"):
                advisor_skipped += 1

    # RUN-005 WP2：三类扩展顾问的真实调用/兜底统计（来自文档，不臆测）
    compiler_llm = compiler_fallback = 0
    for rec in rt.docs.list(kind="exploration", limit=1000):
        meta = ((rec.doc or {}).get("compiled") or {}).get("llm_meta") or {}
        if meta.get("called"):
            compiler_llm += 1
        if meta.get("fallback"):
            compiler_fallback += 1
    probe_llm = probe_fallback = 0
    for rec in rt.docs.list(kind="probe_plan", limit=500):
        notes = (rec.doc or {}).get("llm_planner") or {}
        if notes.get("called"):
            probe_llm += 1
        if notes.get("fallback"):
            probe_fallback += 1
    repair_llm = 0
    for rec in rt.docs.list(kind="repair", limit=500):
        if str((rec.doc or {}).get("created_by", "")).startswith("agent:llm"):
            repair_llm += 1

    summary.update(
        {
            "configured": configured,
            "connected": "unknown",  # 只读摘要不主动触网；连通性由独立自检给出
            "provider": s.llm_provider or "none",
            "default_model": s.llm_default_model if configured else "—",
            "reasoning_model": s.llm_reasoning_model if configured else "—",
            "enabled": {
                "explorer": configured and bool(s.llm_explorer_enabled),
                "diagnosis": configured and bool(s.llm_diagnosis_enabled),
                "compiler": configured
                and bool(s.llm_compiler_enabled)
                and str(s.llm_compiler_mode) != "rule_only",
                "compiler_mode": str(s.llm_compiler_mode),
                "probe_planner": configured and bool(s.llm_probe_planner_enabled),
                "repair_planner": configured and bool(s.llm_repair_planner_enabled),
            },
            "decisions": {
                "llm_calls_recorded": llm_calls,
                "fallback_calls": fallback_calls,
                "rule_explorations": rule_decisions,
                "diagnosis_advisor_called": advisor_called,
                "diagnosis_advisor_skipped": advisor_skipped,
                "compiler_llm_calls": compiler_llm,
                "compiler_fallbacks": compiler_fallback,
                "probe_planner_calls": probe_llm,
                "probe_planner_fallbacks": probe_fallback,
                "repair_planner_proposals": repair_llm,
                "note": "engine 标记规划器配置，不等于每个动作都由 LLM 决策；成本为应用侧估算。",
            },
        }
    )
    return summary


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
