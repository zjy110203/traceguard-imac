"""FastAPI 应用工厂：统一配置、日志、错误处理、健康检查与 SSE 预留。

阶段0 只交付工程骨架与基础设施；业务路由在阶段1+ 挂载。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from fastapi import FastAPI, Request
from traceguard_core.config import get_settings
from traceguard_core.errors import AppError, ErrorCode, app_error_handler, error_response
from traceguard_core.logging import configure_logging, get_logger

logger = get_logger("traceguard.api")


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)

    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        docs_url="/docs",
        openapi_url="/openapi.json",
    )
    app.add_exception_handler(AppError, app_error_handler)

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):  # type: ignore[no-untyped-def]
        logger.exception("unhandled_error", path=request.url.path)
        return error_response(ErrorCode.INTERNAL_ERROR, "internal error")

    @app.get("/health", tags=["infra"])
    async def health() -> dict:
        return {
            "status": "ok",
            "app": settings.app_name,
            "environment": settings.environment,
            "db_dialect": settings.database_url.split(":")[0],
        }

    @app.get("/health/ready", tags=["infra"])
    async def ready() -> dict:
        # 阶段0：仅确认配置可加载；阶段1 起加入真实 DB ping。
        return {"ready": True, "config": settings.public_dict()}

    @app.get("/events/stream", tags=["infra"])
    async def events_stream(request: Request) -> AsyncIterator[bytes]:
        """SSE 展示通道占位：阶段3 接入 Redis Pub/Sub。持久化事件才是权威来源。"""
        try:
            while True:
                if await request.is_disconnected():
                    break
                yield b": keepalive\n\n"
                await asyncio.sleep(15)
        except asyncio.CancelledError:
            return

    # 业务路由在阶段1+ 通过 include_router 挂载，阶段0 不挂载占位空壳。
    return app


app = create_app()
