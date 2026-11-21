"""API 集成测试：直接调用应用内的路由函数，避免依赖 httpx/testclient（本机网络慢）。

真实端到端 HTTP 验证由 docs/quickstart 的 curl 命令与 test-results.md 记录。
"""

from __future__ import annotations

import asyncio

from traceguard_api.main import create_app


def test_create_app_and_health_routes() -> None:
    app = create_app()
    paths = {getattr(r, "path", None) for r in app.routes}
    assert "/health" in paths
    assert "/health/ready" in paths
    assert "/openapi.json" in paths


def test_health_handler_returns_ok() -> None:
    app = create_app()
    health_fn = next(r.endpoint for r in app.routes if getattr(r, "path", None) == "/health")
    result = asyncio.run(health_fn())
    assert result["status"] == "ok"
    assert result["app"] == "TraceGuard"


def test_ready_handler_config_redacted() -> None:
    app = create_app()
    ready_fn = next(r.endpoint for r in app.routes if getattr(r, "path", None) == "/health/ready")
    result = asyncio.run(ready_fn())
    assert result["ready"] is True
    assert "supersecret" not in str(result)
