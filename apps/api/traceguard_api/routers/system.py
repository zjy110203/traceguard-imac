"""系统 API：健康、浏览器自检、种子数据、运行统计、模型运行摘要。"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query, Request
from sqlalchemy import bindparam, text
from starlette.concurrency import run_in_threadpool
from traceguard_agents.gateway.llm_gateway import TokenUsageTracker

from ..seed import seed_teamflow_catalog
from ..services import get_services
from ..workspace import principal_of, scope_filter, visible_workspaces

router = APIRouter(prefix="/api/v1/system", tags=["system"])

_browser_cache: dict[str, Any] | None = None
_browser_lock = threading.Lock()


@router.get("/health")
async def health(request: Request) -> dict[str, Any]:
    rt = get_services().rt
    # RUN-013 WP6：计数按调用方授权 Workspace 过滤（与 /system/metrics 同口径）。
    allowed = scope_filter(
        principal_of(request), None, resource_type="system_health", request=request
    )
    return {
        "status": "ok",
        "app": rt.settings.app_name,
        "environment": rt.settings.environment,
        **rt.health(workspace_ids=allowed),
    }


@router.get("/browser")
async def browser_health(
    probe: bool = Query(False, description="是否真实启动一次浏览器"),
) -> dict[str, Any]:
    global _browser_cache
    if not probe and _browser_cache:
        return _browser_cache
    with _browser_lock:
        # RUN-007 修复：probe 必须在"全新线程"里跑。
        # 之前放在 anyio 线程池里执行：一旦同进程任何 worker 线程已经创建过
        # Playwright 同步实例（执行 Run / 探索都会创建），anyio 线程复用会让
        # `sync_playwright().start()` 误判"在 asyncio loop 里"而失败，
        # 导致 /system/browser 永久返回 available=false。专用线程没有这个污染。
        info = await run_in_threadpool(_probe_in_fresh_thread)
        _browser_cache = info
        return _browser_cache


def _probe_in_fresh_thread() -> dict[str, Any]:
    import queue as _queue
    import threading

    from traceguard_execution.browser.manager import probe_browser

    box: _queue.Queue[dict[str, Any]] = _queue.Queue(maxsize=1)

    def _run() -> None:
        try:
            box.put({"available": True, **probe_browser()})
        except Exception as exc:  # noqa: BLE001 - 自检失败不应 500
            box.put({"available": False, "error": f"{type(exc).__name__}: {exc}"})

    thread = threading.Thread(target=_run, name="tg-browser-probe", daemon=True)
    thread.start()
    thread.join(timeout=90)
    if thread.is_alive():
        return {"available": False, "error": "TimeoutError: browser probe 超过 90s 未完成"}
    return box.get_nowait()


@router.post("/seed")
async def seed() -> dict[str, Any]:
    rt = get_services().rt
    return seed_teamflow_catalog(rt, base_url=rt.settings.teamflow_base_url)


@router.get("/metrics")
async def metrics(
    request: Request, workspace_id: str | None = Query(default=None)
) -> dict[str, Any]:
    """真实数据库聚合统计（RUN-012 WP3）。

    权限口径：
    - 显式 workspace_id：走统一 `scope_filter()`，非授权空间 403；
    - 不传 workspace_id：普通用户只统计授权空间并集，platform-admin 才得到全局汇总；
    - 前端隐藏数据不作为授权依据。

    计数口径：全部使用 SQL COUNT(*) / GROUP BY，不再用 limit=500 的列表长度推断；
    时间范围为全量历史，response 中显式声明。
    """
    rt = get_services().rt
    principal = principal_of(request)
    if workspace_id is not None:
        scope_filter(principal, workspace_id, resource_type="system.metrics", request=request)
        scopes: list[str] | None = [workspace_id]
        mode = "workspace"
    else:
        allowed = visible_workspaces(principal)
        scopes = None if allowed is None else sorted(allowed)
        mode = "global" if scopes is None else "authorized_workspaces"

    where = " WHERE workspace_id IN :scope_ids" if scopes is not None else ""
    doc_where = " WHERE kind = :kind" + (
        " AND workspace_id IN :scope_ids" if scopes is not None else ""
    )
    params: dict[str, Any] = {"scope_ids": scopes} if scopes is not None else {}
    verdict_params = dict(params)

    with rt.db.session() as session:
        stmt_verdicts = text(
            f"SELECT test_verdict, COUNT(*) AS n FROM tg_run{where} GROUP BY test_verdict"
        )
        if scopes is not None:
            stmt_verdicts = stmt_verdicts.bindparams(bindparam("scope_ids", expanding=True))
        verdicts = {
            str(row[0] or "UNKNOWN"): int(row[1])
            for row in session.execute(stmt_verdicts, verdict_params).all()
        }

        def doc_count(kind: str) -> int:
            stmt = text(f"SELECT COUNT(*) FROM tg_doc{doc_where}")
            if scopes is not None:
                stmt = stmt.bindparams(bindparam("scope_ids", expanding=True))
            return int(session.scalar(stmt, {**params, "kind": kind}) or 0)

        out = {
            "runs": int(
                session.scalar(_bind(text(f"SELECT COUNT(*) FROM tg_run{where}"), scopes), params)
                or 0
            ),
            "verdicts": verdicts,
            "evidence": int(
                session.scalar(
                    _bind(text(f"SELECT COUNT(*) FROM tg_evidence{where}"), scopes), params
                )
                or 0
            ),
            "events": int(
                session.scalar(_bind(text(f"SELECT COUNT(*) FROM tg_event{where}"), scopes), params)
                or 0
            ),
            "diagnoses": doc_count("diagnosis"),
            "repairs": doc_count("repair"),
            "testcases": int(
                session.scalar(
                    _bind(text(f"SELECT COUNT(*) FROM tg_testcase{where}"), scopes), params
                )
                or 0
            ),
            "intents": int(
                session.scalar(
                    _bind(text(f"SELECT COUNT(*) FROM tg_intent{where}"), scopes), params
                )
                or 0
            ),
        }
    return {
        **out,
        "scope": {
            "mode": mode,
            "workspace_id": workspace_id,
            "authorized_workspaces": scopes,
            "global": scopes is None,
        },
        "counting": {
            "source": "SQL COUNT(*) / GROUP BY 直接查询持久化表",
            "list_limit": None,
            "time_range": "ALL_TIME",
            "verdict_definition": "tg_run.test_verdict 分组计数；UNKNOWN 表示空值",
        },
    }


def _bind(stmt: Any, scopes: list[str] | None) -> Any:
    return stmt.bindparams(bindparam("scope_ids", expanding=True)) if scopes is not None else stmt


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
    tracker = TokenUsageTracker(
        path=str(usage_path), daily_budget_usd=float(s.llm_daily_budget_usd)
    )
    summary = tracker.summary()

    # 规则兜底来自真实探索/诊断文档，不从用量文件臆测。
    # RUN-014 WP3：旧实现用 `docs.list(limit=1000/500)` 遍历——文档数超过 limit 时
    # 统计会被静默截断却仍标称全量。现改为按键集分页完整读取（`iter_all`），
    # 并在响应中声明实际扫描量与是否有截断。
    fallback_calls = 0
    llm_calls = 0
    rule_decisions = 0
    advisor_called = 0
    advisor_skipped = 0
    compiler_llm = compiler_fallback = 0
    scanned = {"exploration": 0, "diagnosis": 0, "probe_plan": 0, "repair": 0}
    for rec in rt.docs.iter_all(kind="exploration"):
        doc = rec.doc or {}
        scanned["exploration"] += 1
        engine = str(doc.get("engine") or "")
        stats = doc.get("llm_stats") or {}
        if isinstance(stats, dict):
            fallback_calls += int(stats.get("fallback_calls") or 0)
            llm_calls += int(stats.get("llm_calls") or 0)
        if engine and not engine.startswith("llm:"):
            # engine 只表示规划器配置，不把 engine=llm:* 的动作都算作 LLM 成功。
            rule_decisions += 1
        meta = ((doc.get("compiled") or {}).get("llm_meta")) or {}
        if meta.get("called"):
            compiler_llm += 1
        if meta.get("fallback"):
            compiler_fallback += 1
    for rec in rt.docs.iter_all(kind="diagnosis"):
        doc = rec.doc or {}
        scanned["diagnosis"] += 1
        advisor = doc.get("llm_advisor") or {}
        if isinstance(advisor, dict):
            if advisor.get("called"):
                advisor_called += 1
            if advisor.get("skipped"):
                advisor_skipped += 1

    # RUN-005 WP2：三类扩展顾问的真实调用/兜底统计（来自文档，不臆测）
    probe_llm = probe_fallback = 0
    for rec in rt.docs.iter_all(kind="probe_plan"):
        notes = (rec.doc or {}).get("llm_planner") or {}
        scanned["probe_plan"] += 1
        if notes.get("called"):
            probe_llm += 1
        if notes.get("fallback"):
            probe_fallback += 1
    repair_llm = 0
    for rec in rt.docs.iter_all(kind="repair"):
        scanned["repair"] += 1
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
            # RUN-014 WP3：统计口径可核验——全量按键集分页读取，不静默截断
            "coverage": {
                "method": "keyset_pagination (DocStore.iter_all)",
                "documents_scanned": scanned,
                "truncated": False,
                "limits_used": None,
                "previous_behavior": "旧实现 limit=1000/500，超出即静默截断",
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
