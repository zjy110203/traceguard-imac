"""RUN-010 WP2：受控出站代理与浏览器生命周期绑定。

设计
----
Playwright（同步 API）与 EgressProxy（asyncio）分属两个世界：控制器在
**后台线程**中运行独立事件循环承载代理，浏览器启动前先拉起代理，关闭时
先关浏览器再停代理——代理服务生命周期必须覆盖整个浏览器执行周期。

fail-closed 原则：
- 代理启动失败 → 浏览器不启动（BrowserUnavailableError），绝不静默直连；
- 代理线程死亡 → `alive` 变 False；BrowserManager 每次新建上下文/每次执行前
  检查存活，死亡即抛 EgressProxyDeadError 终止执行（浏览器侧因代理失联
  本来也无法建立任何新连接，双保险）；
- 不存在「代理不可用就退回直连」的路径。

审计关联：
- controller 绑定当前 run_id / worker_id（执行体在 Run 开始时 set_run_context），
  代理每条审计记录都携带，从而能把 ALLOW/BLOCK 与具体 Run 对账。
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any

from traceguard_core.logging import get_logger
from traceguard_core.security.egress_proxy import AuditEntry, EgressProxy
from traceguard_core.security.target_policy import TargetPolicy

logger = get_logger("traceguard.browser.egress")


class EgressProxyDeadError(RuntimeError):
    """受控出站代理已停止/死亡：执行必须失败，不允许继续无保护运行。"""


def scheme_dual_policy(policy: TargetPolicy) -> TargetPolicy:
    """代理侧 scheme 对偶策略：授权 origin 的 http/https 变体同 host:port 均放行。

    原因：Chromium 117+ 的 HTTPS-Upgrades 会先对 http:// 目标发 CONNECT（视为
    https），被 scheme 严格策略拒绝后可能不再回退，导致授权目标不可达。
    放宽仅限「同一 host:port 的 scheme 对偶」——host/port/私网/rebinding 检查
    全部保持不变，不扩大可访问面；会话层 route guard 仍用原策略（scheme 严格）。
    """
    from traceguard_core.security.target_policy import Origin

    dual: set[Origin] = set()
    for origin in policy.origins:
        dual.add(origin)
        flipped = "https" if origin.scheme == "http" else "http"
        dual.add(Origin(scheme=flipped, host=origin.host, port=origin.port))
    clone = TargetPolicy(
        origins=frozenset(dual),
        max_redirects=policy.max_redirects,
        allow_authorized_private=policy.allow_authorized_private,
        resolver=policy.resolver,
    )
    clone.pinned_dns = dict(policy.pinned_dns)
    return clone


class EgressProxyController:
    """把 EgressProxy 跑在后台线程，管理与浏览器实例 1:1 的生命周期。"""

    def __init__(self, policy: TargetPolicy, *, worker_id: str = "") -> None:
        self.policy = policy
        self.worker_id = worker_id
        self._proxy: EgressProxy | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._stop_evt = threading.Event()
        self._start_error: Exception | None = None
        self._run_context: dict[str, str] = {}
        self._alive = False
        self._lock = threading.Lock()

    # ------------------------------------------------------------ 生命周期
    def start(self) -> str:
        """启动代理线程，返回 `http://127.0.0.1:<port>`。失败抛异常（fail-closed）。"""
        with self._lock:
            if self._alive:
                return self.url
            self._proxy = EgressProxy(self.policy)
            self._stop_evt.clear()
            self._ready.clear()
            self._start_error = None
            self._thread = threading.Thread(target=self._run, name="egress-proxy", daemon=True)
            self._thread.start()
            if not self._ready.wait(timeout=10.0):
                raise RuntimeError("EGRESS_PROXY_START_TIMEOUT")
            if self._start_error is not None:
                raise self._start_error
            self._alive = True
            logger.info("egress_proxy_started", url=self.url, worker_id=self.worker_id)
            return self.url

    def _run(self) -> None:  # pragma: no cover - 线程体由 start 断言覆盖
        async def main() -> None:
            assert self._proxy is not None
            try:
                await self._proxy.start()
            except Exception as exc:  # noqa: BLE001 - 启动失败要传回主线程
                self._start_error = exc
                self._ready.set()
                return
            self._ready.set()
            serve_task = asyncio.ensure_future(self._proxy.serve())
            stop_wait = asyncio.ensure_future(asyncio.to_thread(self._stop_evt.wait))
            done, _pending = await asyncio.wait(
                {serve_task, stop_wait}, return_when=asyncio.FIRST_COMPLETED
            )
            for task in done:
                if task is serve_task and not task.cancelled():
                    exc = task.exception()
                    if exc is not None:
                        logger.error("egress_proxy_serve_crashed", error=str(exc))
            if not serve_task.done():
                serve_task.cancel()
                try:
                    await serve_task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
            if not stop_wait.done():
                stop_wait.cancel()

        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(main())
        finally:
            try:
                self._loop.run_until_complete(self._loop.shutdown_asyncgens())
            except Exception:  # noqa: BLE001
                pass
            self._loop.close()
            self._loop = None
            self._alive = False

    def stop(self) -> None:
        """停止代理线程（浏览器已先关闭）。幂等。"""
        self._stop_evt.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None
        self._alive = False
        logger.info("egress_proxy_stopped", worker_id=self.worker_id)

    @property
    def alive(self) -> bool:
        return self._alive and self._thread is not None and self._thread.is_alive()

    def require_alive(self) -> None:
        """执行前断言代理存活；死亡即抛错（fail-closed，绝不回退直连）。"""
        if not self.alive:
            raise EgressProxyDeadError(
                "EGRESS_PROXY_DEAD: 受控出站代理已停止，浏览器执行必须失败终止"
                "（不存在直连回退路径）"
            )

    @property
    def url(self) -> str:
        if self._proxy is None or self._proxy._server is None:  # noqa: SLF001 - 同包协作
            raise RuntimeError("EGRESS_PROXY_NOT_STARTED")
        return self._proxy.url

    # ------------------------------------------------------------ Run 上下文
    def set_run_context(self, *, run_id: str, worker_id: str = "") -> None:
        """把当前 Run（与 Worker）绑定到代理审计记录。"""
        self._run_context = {"run_id": run_id, "worker_id": worker_id or self.worker_id}

    def clear_run_context(self) -> None:
        self._run_context = {}

    # ------------------------------------------------------------ 审计导出
    def audit_entries(self) -> list[dict[str, Any]]:
        if self._proxy is None:
            return []
        ctx = dict(self._run_context)
        out: list[dict[str, Any]] = []
        for entry in self._proxy.audit:
            item = entry.to_dict()
            item["run_id"] = ctx.get("run_id", "")
            item["worker_id"] = ctx.get("worker_id", self.worker_id)
            out.append(item)
        return out

    def audit_summary(self) -> dict[str, Any]:
        entries = self.audit_entries()
        allowed = [e for e in entries if e["decision"] == "ALLOW"]
        blocked = [e for e in entries if e["decision"] == "BLOCK"]
        return {
            "total": len(entries),
            "allowed": len(allowed),
            "blocked": len(blocked),
            "blocked_targets": sorted(
                {f"{e['scheme']}://{e['host']}:{e['port']}" for e in blocked}
            ),
            "url": self.url if self._proxy is not None else "",
            "run_id": self._run_context.get("run_id", ""),
            "worker_id": self._run_context.get("worker_id", self.worker_id),
        }


__all__ = ["AuditEntry", "EgressProxyController", "EgressProxyDeadError"]
