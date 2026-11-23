"""RUN-004-FE：只读模型运行摘要的离线测试（不触网、不读真实密钥）。"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

from traceguard_agents.gateway.llm_gateway import TokenUsageTracker, UsageRecord
from traceguard_api.main import create_app


def _record(*, agent: str, model: str, day: str, status: str = "ok", latency: int = 100) -> UsageRecord:
    return UsageRecord(
        ts=f"{day}T00:00:00+00:00",
        provider="deepseek",
        model=model,
        agent=agent,
        task_id="task",
        run_id="run",
        prompt_version="explorer_planner@v1",
        input_tokens=100,
        output_tokens=50,
        cached_tokens=0,
        latency_ms=latency,
        request_status=status,
        retry_count=0,
        estimated_cost_usd=0.0001,
        cost_is_estimated=True,
    )


def test_tracker_summary_aggregates_real_records_without_details(tmp_path) -> None:
    tracker = TokenUsageTracker(path=str(tmp_path / "usage.jsonl"), daily_budget_usd=1.0)
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    tracker.record(_record(agent="explorer", model="deepseek-flash", day=today))
    tracker.record(_record(agent="diagnosis", model="deepseek-v4-pro", day=today, status="error:timeout", latency=300))
    tracker.record(_record(agent="explorer", model="deepseek-flash", day="2026-01-01"))

    out = tracker.summary()
    assert out["data_available"] is True
    assert out["calls"] == 2
    assert out["errors"] == 1
    assert out["input_tokens"] == 200
    assert out["output_tokens"] == 100
    assert out["cost_is_estimated"] is True
    assert out["by_agent"]["explorer"]["calls"] == 1
    assert out["by_agent"]["diagnosis"]["errors"] == 1
    assert out["by_model"]["deepseek-v4-pro"]["calls"] == 1
    assert {x["date"] for x in out["daily_series"]} == {today, "2026-01-01"}
    assert out["latency_summary"]["sample"] == 2
    assert out["prompt_versions"] == {"explorer_planner@v1": 3}
    assert "task_id" not in json.dumps(out)
    assert "run_id" not in json.dumps(out)


def test_llm_summary_endpoint_registered_and_redacted(tmp_path, monkeypatch) -> None:
    app = create_app()
    # 本项目的 FastAPI 版本把 include_router 包装为 _IncludedRouter，需展开 original_router。
    routes = list(app.routes)
    for r in list(routes):
        if hasattr(r, "original_router"):
            routes.extend(r.original_router.routes)
    paths = {getattr(r, "path", None) for r in routes}
    assert "/api/v1/system/llm/summary" in paths
    fn = next(r.endpoint for r in routes if getattr(r, "path", None) == "/api/v1/system/llm/summary")
    monkeypatch.setenv("TG_TRACEGUARD_ROOT", str(tmp_path))
    result = asyncio.run(fn())
    for key in (
        "configured",
        "connected",
        "provider",
        "default_model",
        "reasoning_model",
        "calls",
        "errors",
        "input_tokens",
        "output_tokens",
        "estimated_cost_usd",
        "cost_is_estimated",
        "budget_usd",
        "budget_exceeded",
        "by_agent",
        "by_model",
        "latency_summary",
        "daily_series",
        "decisions",
    ):
        assert key in result, key
    assert result["configured"] is False  # conftest 清空凭据
    assert result["connected"] == "unknown"  # 只读摘要不触发外部网络
    assert result["cost_is_estimated"] is True
    assert result["data_available"] is False
    assert "sk-" not in json.dumps(result)
    assert result["prompt_versions"] == {}
    assert "messages" not in result and "api_key" not in result
