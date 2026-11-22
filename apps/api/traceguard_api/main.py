"""TraceGuard API 应用工厂。

职责：
- 统一配置/日志/错误处理/CORS；
- 挂载全部业务路由（catalog/execution/evidence/intelligence/benchmark/system）；
- 启动时建表 + 幂等种子数据；按配置启动内嵌 Worker（单机模式）；
- 若存在前端构建产物（apps/web/dist），直接托管 SPA（单端口部署）。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from traceguard_core.config import get_settings
from traceguard_core.errors import AppError, ErrorCode, app_error_handler, error_response
from traceguard_core.logging import configure_logging, get_logger

logger = get_logger("traceguard.api")

DIST_DIR = Path(__file__).resolve().parents[2] / "web" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    from .runtime import get_runtime
    from .seed import seed_teamflow_catalog

    settings = get_settings()
    rt = get_runtime()
    rt.db.create_schema()
    result = seed_teamflow_catalog(rt, base_url=settings.teamflow_base_url)
    logger.info("startup", seeded=result, db=rt.db.url.split(":")[0])
    if settings.inline_worker:
        rt.start_inline_worker()
    yield
    rt.stop_inline_worker()
    try:
        from traceguard_execution.browser.manager import close_current_thread_browser

        close_current_thread_browser()
    except Exception:  # noqa: BLE001
        pass


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)

    app = FastAPI(
        title=settings.app_name,
        version="0.2.0",
        description="Agentic Web UI Testing & Quality Intelligence Platform",
        docs_url="/docs",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )
    app.add_exception_handler(AppError, app_error_handler)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

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
        from .runtime import get_runtime

        rt = get_runtime()
        h = rt.health()
        return {
            "ready": h["database"]["ok"],
            "config": settings.public_dict(),
            "database": h["database"],
            "artifact_dir": h["artifact_dir"],
            "worker": h["worker"],
        }

    @app.get("/events/stream", tags=["infra"])
    async def events_stream(request: Request) -> AsyncIterator[bytes]:
        """SSE 保活通道；持久化事件才是权威来源（前端用轮询/事件 API 查询）。"""
        try:
            while True:
                if await request.is_disconnected():
                    break
                yield b": keepalive\n\n"
                await asyncio.sleep(15)
        except asyncio.CancelledError:
            return

    from .routers import catalog, evidence, execution, intelligence, system

    app.include_router(system.router)
    app.include_router(catalog.router)
    app.include_router(execution.router)
    app.include_router(evidence.router)
    app.include_router(intelligence.router)

    try:
        from .routers import benchmark as benchmark_router

        app.include_router(benchmark_router.router)
    except ImportError:  # pragma: no cover - 基准模块未就绪时不影响主体
        logger.warning("benchmark_router_unavailable")

    # 单端口部署：托管前端构建产物
    if DIST_DIR.exists():
        app.mount("/assets", StaticFiles(directory=DIST_DIR / "assets"), name="assets")

        @app.get("/", include_in_schema=False)
        async def index() -> FileResponse:
            return FileResponse(DIST_DIR / "index.html")

        @app.get("/{full_path:path}", include_in_schema=False)
        async def spa(full_path: str):
            if full_path.startswith(("api/", "docs", "openapi", "health", "events")):
                raise AppError(ErrorCode.NOT_FOUND, "not found")
            candidate = DIST_DIR / full_path
            if candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(DIST_DIR / "index.html")

    return app


app = create_app()
