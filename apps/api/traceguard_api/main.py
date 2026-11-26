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
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from traceguard_core.config import get_settings
from traceguard_core.errors import AppError, ErrorCode, app_error_handler, error_response
from traceguard_core.logging import configure_logging, get_logger

from .auth import auth_middleware
from .auth_policy import apply_auth_policy

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

    # RUN-010 WP1：认证环境策略启动期强制。
    # production 下认证关闭 / Cookie 不安全 / TTL 超限 → 直接启动失败（fail-closed）；
    # staging 同样强制认证；development 显式关闭时打醒目警告。
    decision = apply_auth_policy(settings)
    for warning in decision.warnings_or_empty:
        logger.warning("auth_policy_warning", warning=warning)
    if not decision.ok:
        logger.error("auth_policy_violation", reason=decision.fail_reason)
        raise RuntimeError(decision.fail_reason)

    app = FastAPI(
        title=settings.app_name,
        version="0.2.0",
        description="Agentic Web UI Testing & Quality Intelligence Platform",
        docs_url="/docs",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )
    app.add_exception_handler(AppError, app_error_handler)
    # RUN-009 WP2：认证开关显式化。未开启时必须明确记录，不得静默回退匿名管理员。
    if not decision.auth_effective:
        logger.warning("auth_disabled_explicitly", environment=settings.environment)
    else:
        logger.info("auth_enabled", session_ttl_s=settings.auth_session_ttl_s)
    app.middleware("http")(auth_middleware)
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
    async def events_stream(
        request: Request,
        workspace_id: str | None = None,
        run_id: str | None = None,
        event_type: str | None = None,
        last_event_id: str | None = None,
        heartbeat_seconds: float = 15.0,
        poll_interval: float = 1.0,
        limit: int = 0,
        auth_recheck_seconds: float = 2.0,
    ):
        """真实业务事件流（SSE）。

        - 读取持久化 Event Store（不是内存快照，也不是纯 keepalive）；
        - 支持 `Last-Event-ID` 头或 `last_event_id` 查询参数续传；
        - 支持 workspace / run / event_type 过滤、心跳、客户端断开、轮询降级；
        - `sequence` 是 producer 实例内序号，不对外宣称跨 producer 全局顺序；
        - SSE 只做实时展示，业务状态权威来源仍是 API / 数据库。

        RUN-014 WP3：**活跃连接的授权重新核验**。
        连接建立时算出的 Workspace 范围不能一直用到断开：每次轮询（默认每 2s）
        重新解析会话——用户是否仍 active、会话是否被撤销/过期、Workspace 授权是否仍成立。
        一旦失效：先发 `auth.revoked` 控制帧（说明原因），再立即关闭连接；
        不再发送任何业务事件。
        """
        from . import sse as sse_util
        from .auth import _effective_auth_enabled, get_auth_manager
        from .runtime import get_runtime
        from .workspace import principal_of, scope_filter, visible_workspaces

        # RUN-013 WP2：`/events/stream` 不在 `/api/v1` 下，认证中间件不会覆盖它。
        # 必须在此显式完成身份解析 + Workspace 作用域校验，否则匿名用户可订阅
        # 任意 Workspace 的事件流（跨空间泄露）。
        principal = principal_of(request)
        session_token = ""
        if _effective_auth_enabled():
            if principal is None:
                token = request.cookies.get(settings.auth_cookie_name, "")
                principal = get_auth_manager(request).resolve(token) if token else None
                if principal is None:
                    return JSONResponse(
                        status_code=401,
                        content={
                            "detail": {
                                "code": "UNAUTHENTICATED",
                                "message": "authentication required",
                            }
                        },
                    )
                request.state.principal = principal
            session_token = principal.session_token
        allowed = scope_filter(principal, workspace_id, resource_type="event", request=request)
        # allowed=None：管理员 / 认证关闭 → 不限制；否则按授权 Workspace 并集过滤。
        # 显式请求的 workspace 与"可见集合"分开记录：撤权判定必须针对**用户请求的那个空间**，
        # 否则非管理员传入 workspace_id 时 stream_workspace 会退化成 None（撤权永远不触发）。
        requested_ws = workspace_id.strip() if workspace_id and workspace_id.strip() else None
        stream_workspace = requested_ws
        stream_workspaces = allowed
        #: 认证关闭时没有可撤销的会话，跳过重新核验（保持既有 dev 行为）
        auth_audit = bool(session_token)

        rt = get_runtime()
        cursor = sse_util.decode_cursor(last_event_id or request.headers.get("last-event-id"))
        start_at = cursor["at"] if cursor else ""
        start_eid = cursor["eid"] if cursor else ""

        async def generate() -> AsyncIterator[bytes]:
            import time as _time

            nonlocal stream_workspaces, principal
            at, eid = start_at, start_eid
            sent = 0
            last_beat = _time.monotonic()
            last_auth_check = _time.monotonic()
            yield sse_util.format_retry()
            try:
                while True:
                    if await request.is_disconnected():
                        break
                    # ---- RUN-014 WP3：活跃连接授权重新核验 ----
                    now_check = _time.monotonic()
                    if auth_audit and now_check - last_auth_check >= max(0.1, auth_recheck_seconds):
                        last_auth_check = now_check
                        fresh = None
                        try:
                            fresh = await asyncio.to_thread(
                                get_auth_manager(request).resolve, session_token
                            )
                        except Exception as exc:  # noqa: BLE001 - DB 抖动不等于撤权
                            logger.warning("sse_auth_recheck_failed", error=str(exc)[:200])
                        if fresh is None:
                            yield sse_util.format_notice(
                                "auth.revoked", {"reason": "SESSION_INVALID"}
                            )
                            logger.info("sse_closed_session_invalid", user=principal.username)
                            return
                        if fresh.user_id != principal.user_id:
                            yield sse_util.format_notice(
                                "auth.revoked", {"reason": "SESSION_REBOUND"}
                            )
                            return
                        new_allowed = visible_workspaces(fresh)
                        if requested_ws is not None and not fresh.workspace_allowed(requested_ws):
                            yield sse_util.format_notice(
                                "auth.revoked",
                                {
                                    "reason": "WORKSPACE_REVOKED",
                                    "workspace_id": requested_ws,
                                },
                            )
                            logger.info(
                                "sse_closed_workspace_revoked",
                                user=fresh.username,
                                workspace=requested_ws,
                            )
                            return
                        # 授权变更（授予/撤销其它 Workspace）立即生效，不必重连
                        stream_workspaces = new_allowed
                        principal = fresh
                    try:
                        events = await asyncio.to_thread(
                            rt.events.list_events_after,
                            after_at=at,
                            after_event_id=eid,
                            workspace_id=stream_workspace,
                            workspace_ids=stream_workspaces,
                            run_id=run_id,
                            event_type=event_type,
                            limit=100,
                        )
                    except Exception as exc:  # noqa: BLE001 - 数据库短暂不可用不得中断长连接
                        logger.warning("sse_poll_failed", error=str(exc)[:200])
                        events = []
                        yield sse_util.format_heartbeat(note="db_temporarily_unavailable")
                    for ev in events:
                        yield sse_util.format_event(ev)
                        at, eid = ev.recorded_at, ev.event_id
                        sent += 1
                        if limit and sent >= limit:
                            yield sse_util.format_heartbeat(
                                cursor=sse_util.encode_cursor(ev), note="limit_reached"
                            )
                            return
                    now = _time.monotonic()
                    if now - last_beat >= max(0.2, heartbeat_seconds):
                        yield sse_util.format_heartbeat(cursor=f"at={at};eid={eid}" if at else None)
                        last_beat = now
                    await asyncio.sleep(max(0.05, poll_interval))
            except asyncio.CancelledError:  # 客户端断开
                return

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    from .routers import auth as auth_router
    from .routers import catalog, evidence, execution, intelligence, system

    app.include_router(system.router)
    app.include_router(auth_router.router)
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
