"""单个 Run 的浏览器会话：隔离上下文 + 观测采集 + 证据产出（bytes）。

采集内容：
- console（含类型/文本/位置）与 pageerror（未捕获异常）；
- 网络：请求/响应（状态、耗时、资源类型、失败原因）、4xx/5xx 列表；
- DOM 快照（上限截断，避免超大证据）、ARIA 快照（可访问性树，供断言/定位调试）；
- 截图（全页可选）、Playwright trace（zip，可在 trace viewer 回放）。
所有采集都是"旁路"，不改变被测应用行为；采集异常不阻断步骤执行。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin

from traceguard_core.logging import get_logger
from traceguard_core.security.target_policy import (
    TargetAccessDenied,
    TargetCheck,
    TargetPolicy,
)

from .manager import BrowserManager

logger = get_logger("traceguard.browser.session")

MAX_DOM_CHARS = 400_000
MAX_CONSOLE_ENTRIES = 500
MAX_NETWORK_ENTRIES = 1000


@dataclass
class NetworkEntry:
    url: str
    method: str
    resource_type: str
    status: int | None = None
    ok: bool | None = None
    failure: str | None = None
    started_ms: float = field(default_factory=lambda: time.time() * 1000)
    duration_ms: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "method": self.method,
            "resource_type": self.resource_type,
            "status": self.status,
            "ok": self.ok,
            "failure": self.failure,
            "duration_ms": self.duration_ms,
        }


class BrowserSession:
    def __init__(
        self,
        manager: BrowserManager,
        *,
        base_url: str,
        run_id: str = "",
        trace_enabled: bool = True,
        target_policy: TargetPolicy | None = None,
    ) -> None:
        self.manager = manager
        self.base_url = base_url.rstrip("/")
        self.run_id = run_id
        self.policy = target_policy or TargetPolicy.from_base_url(self.base_url)
        # RUN-007 WP1-05：钉住授权主机的 DNS 解析结果，后续解析漂移即视为 rebinding
        try:
            self.policy.pin_dns()
        except Exception:  # noqa: BLE001 - 钉不住不影响 URL 层白名单
            pass
        self.blocked_requests: list[dict[str, Any]] = []
        self.redirect_checks: list[dict[str, Any]] = []
        self.context = manager.new_context()
        # 请求级网络管控：未授权 host/port 的请求在发出前被 abort（不依赖提示词/人工检查）
        self.context.route("**/*", self._guard_route)
        self._guard_websockets()
        self.page = self.context.new_page()
        self.console: list[dict[str, Any]] = []
        self.page_errors: list[dict[str, Any]] = []
        self.network: list[NetworkEntry] = []
        self.dialogs: list[dict[str, Any]] = []
        self._req_index: dict[Any, NetworkEntry] = {}
        self._trace_enabled = trace_enabled
        self._tracing = False
        self._closed = False
        self._bind()
        if trace_enabled:
            try:
                self.context.tracing.start(screenshots=True, snapshots=True, sources=False)
                self._tracing = True
            except Exception:  # noqa: BLE001
                self._tracing = False

    # ------------------------------------------------------------------ events
    def _guard_route(self, route: Any) -> None:
        """请求级目标策略：未授权地址直接 abort，并记录审计事实。

        说明：`about:` / `data:` / `blob:` 属于本地内联资源，策略放行。

        RUN-007 WP1-05 关键修复：**顶层文档导航的 3xx 不再交给浏览器自动跟随**。
        Chromium 会自动 follow 服务端 302，此时第二个请求由网络栈直接发出，
        route 回调不再被调用 —— 这正是"顶层 302 仍能打到未授权目标"的原因。
        现在对文档请求先 `route.fetch(max_redirects=0)` 自己取响应，
        检查 `Location` 是否在授权范围：
        - 不在 → 直接返回合成 403，未授权目标收到 0 个请求；
        - 在 → 原样返回 302，由浏览器继续（目标本身已授权）。
        """
        try:
            request = route.request
            url = request.url
        except Exception:  # noqa: BLE001 - 路由对象异常时保守放行会破坏安全边界
            try:
                route.abort(error_code="blockedbyclient")
            except Exception:  # noqa: BLE001
                pass
            return
        decision = self.policy.check(url, resolve_dns=True)
        if not decision.allowed:
            self._record_blocked(request, url, decision.reason)
            try:
                route.abort(error_code="blockedbyclient")
            except Exception:  # noqa: BLE001
                pass
            return

        if self._is_document_navigation(request):
            if self._guard_document_navigation(route, url):
                return
            # 自己接管失败（例如非 GET 导航/取响应异常）：退回普通放行，
            # 但落地地址仍会被 check_target 复核
        try:
            route.continue_()
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def _is_document_navigation(request: Any) -> bool:
        try:
            return (
                getattr(request, "resource_type", "") == "document"
                and getattr(request, "method", "GET").upper() == "GET"
                and request.is_navigation_request()
            )
        except Exception:  # noqa: BLE001
            return False

    def _guard_document_navigation(self, route: Any, url: str) -> bool:
        """取回 3xx 并检查 Location。返回 True 表示已经处理（fulfill/abort）。"""
        try:
            response = route.fetch(max_redirects=0)
        except Exception:  # noqa: BLE001 - 取不到就让调用方 continue_
            return False
        try:
            status = int(getattr(response, "status", 0) or 0)
        except Exception:  # noqa: BLE001
            status = 0
        if not 300 <= status < 400:
            try:
                route.fulfill(response=response)
            except Exception:  # noqa: BLE001
                return False
            return True

        headers = {}
        try:
            headers = {k.lower(): v for k, v in (response.headers or {}).items()}
        except Exception:  # noqa: BLE001
            headers = {}
        location = headers.get("location", "")
        target = urljoin(url, location) if location else ""
        check = self.policy.check(target, resolve_dns=True) if target else None
        blocked = bool(check and not check.allowed)
        self.redirect_checks.append(
            {
                "from": url,
                "status": status,
                "location": location,
                "resolved": target,
                "decision": "BLOCKED" if blocked else "ALLOWED",
                "reason": check.reason if check else "NO_LOCATION_HEADER",
            }
        )
        if blocked:
            self._record_blocked(
                getattr(route, "request", None),
                target or url,
                f"REDIRECT_LOCATION_NOT_ALLOWED:{check.reason if check else 'UNKNOWN'}",
            )
            try:
                route.fulfill(
                    status=403,
                    content_type="text/html; charset=utf-8",
                    body=(
                        "<html><body><h1>403 Target Not Allowed</h1>"
                        "<p>TraceGuard 目标访问策略阻止了跳转到未授权地址。</p>"
                        "</body></html>"
                    ).encode(),
                )
            except Exception:  # noqa: BLE001
                try:
                    route.abort(error_code="blockedbyclient")
                except Exception:  # noqa: BLE001
                    pass
            return True
        try:
            route.fulfill(response=response)
        except Exception:  # noqa: BLE001
            return False
        return True

    def _record_blocked(self, request: Any, url: str, reason: str) -> None:
        self.blocked_requests.append(
            {
                "url": url,
                "reason": reason,
                "method": getattr(request, "method", "") if request is not None else "",
                "resource_type": getattr(request, "resource_type", "") if request else "",
                "at_ms": int(time.time() * 1000),
            }
        )

    def _guard_websockets(self) -> None:
        """WebSocket 也纳入同一策略（route 不覆盖 ws/wss，需要单独拦截）。"""
        router = getattr(self.context, "route_web_socket", None)
        if router is None:  # 旧版 Playwright：退化为不拦截，并在证据里标记
            self.websocket_guard = "UNAVAILABLE"
            return
        self.websocket_guard = "ACTIVE"

        def _handler(ws: Any) -> None:
            try:
                url = ws.url
            except Exception:  # noqa: BLE001
                try:
                    ws.close()
                except Exception:  # noqa: BLE001
                    pass
                return
            decision = self.policy.check(url, resolve_dns=True)
            if decision.allowed:
                # 显式双向转发：只做策略判断，不改写 WebSocket 语义
                try:
                    server_ws = ws.connect_to_server()
                    try:
                        ws.on_message(lambda message: server_ws.send(message))
                        server_ws.on_message(lambda message: ws.send(message))
                    except Exception:  # noqa: BLE001
                        pass
                except Exception:  # noqa: BLE001
                    pass
                return
            self._record_blocked(None, url, f"WEBSOCKET_NOT_ALLOWED:{decision.reason}")
            try:
                ws.close()
            except Exception:  # noqa: BLE001
                pass

        try:
            router("**/*", _handler)
        except Exception:  # noqa: BLE001 - 注册失败不能让会话不可用，但要如实标记
            self.websocket_guard = "REGISTER_FAILED"

    def check_target(self, url: str, *, landed: bool = False) -> TargetCheck:
        """导航/跳转前后复核目标地址；不通过则抛 TargetAccessDenied。"""
        decision = self.policy.check_landed_url(url) if landed else self.policy.check(url)
        if not decision.allowed:
            raise TargetAccessDenied(decision.reason, url)
        return decision

    def _bind(self) -> None:
        self.page.on("console", self._on_console)
        self.page.on("pageerror", self._on_page_error)
        self.page.on("request", self._on_request)
        self.page.on("response", self._on_response)
        self.page.on("requestfailed", self._on_request_failed)
        self.page.on("dialog", self._on_dialog)
        self.context.on("page", self._on_new_page)
        self.context.on("weberror", self._on_weberror)

    def _on_new_page(self, page: Any) -> None:
        # 弹窗页面也采集 console/error，避免证据缺失
        try:
            page.on("console", self._on_console)
            page.on("pageerror", self._on_page_error)
        except Exception:  # noqa: BLE001
            pass

    def _on_console(self, msg: Any) -> None:
        if len(self.console) >= MAX_CONSOLE_ENTRIES:
            return
        try:
            loc = msg.location or {}
            self.console.append(
                {
                    "type": msg.type,
                    "text": msg.text[:2000],
                    "url": loc.get("url", ""),
                    "line": loc.get("lineNumber"),
                    "at_ms": int(time.time() * 1000),
                }
            )
        except Exception:  # noqa: BLE001
            pass

    def _on_page_error(self, err: Any) -> None:
        if len(self.page_errors) >= MAX_CONSOLE_ENTRIES:
            return
        self.page_errors.append(
            {"message": str(err)[:2000], "stack": getattr(err, "stack", "")[:4000]}
        )

    def _on_weberror(self, web_error: Any) -> None:
        try:
            self.page_errors.append({"message": str(web_error.error)[:2000], "source": "context"})
        except Exception:  # noqa: BLE001
            pass

    def _on_request(self, request: Any) -> None:
        if len(self.network) >= MAX_NETWORK_ENTRIES:
            return
        entry = NetworkEntry(
            url=request.url,
            method=request.method,
            resource_type=request.resource_type,
            started_ms=time.time() * 1000,
        )
        self.network.append(entry)
        self._req_index[request] = entry

    def _on_response(self, response: Any) -> None:
        entry = self._req_index.get(response.request)
        if entry is None:
            entry = NetworkEntry(
                url=response.url,
                method=response.request.method,
                resource_type=response.request.resource_type,
            )
            self.network.append(entry)
            self._req_index[response.request] = entry
        entry.status = response.status
        entry.ok = response.ok
        entry.duration_ms = int(time.time() * 1000 - entry.started_ms)

    def _on_request_failed(self, request: Any) -> None:
        entry = self._req_index.get(request)
        if entry is None:
            entry = NetworkEntry(
                url=request.url,
                method=request.method,
                resource_type=request.resource_type,
            )
            self.network.append(entry)
            self._req_index[request] = entry
        try:
            entry.failure = str(request.failure)
        except Exception:  # noqa: BLE001
            entry.failure = "request failed"
        entry.duration_ms = int(time.time() * 1000 - entry.started_ms)

    def _on_dialog(self, dialog: Any) -> None:
        self.dialogs.append({"type": dialog.type, "message": dialog.message[:1000]})
        try:
            dialog.accept()
        except Exception:  # noqa: BLE001
            pass

    # --------------------------------------------------------------- capturing
    def screenshot(self, *, full_page: bool = True) -> bytes:
        return self.page.screenshot(full_page=full_page, animations="disabled")

    def dom(self) -> str:
        html = self.page.content()
        return html[:MAX_DOM_CHARS]

    def aria(self) -> str:
        try:
            return self.page.locator("body").aria_snapshot(timeout=3000)[:20000]
        except Exception:  # noqa: BLE001
            return ""

    def title(self) -> str:
        try:
            return self.page.title()
        except Exception:  # noqa: BLE001
            return ""

    def current_url(self) -> str:
        try:
            return self.page.url
        except Exception:  # noqa: BLE001
            return ""

    def wait_network_settle(self, timeout_ms: int = 3000, quiet_ms: int = 250) -> None:
        """等待在途请求收敛。

        Playwright 的事件是在 API 调用期间派发的：仅 await load_state 可能拿不到
        fetch 的响应事件。这里轮询 page.wait_for_timeout 驱动事件循环，直到
        没有 status=None 的请求，或超时（超时也返回，由调用方按"在途"记录）。
        """
        deadline = time.time() + timeout_ms / 1000
        quiet_deadline = time.time() + quiet_ms / 1000
        while time.time() < deadline:
            pending = [e for e in self.network if e.status is None and not e.failure]
            if not pending:
                if time.time() >= quiet_deadline:
                    return
            else:
                quiet_deadline = time.time() + quiet_ms / 1000
            try:
                self.page.wait_for_timeout(100)
            except Exception:  # noqa: BLE001
                return

    def console_since(self, offset: int) -> list[dict[str, Any]]:
        return self.console[offset:]

    def network_since(self, offset: int) -> list[dict[str, Any]]:
        return [e.to_dict() for e in self.network[offset:]]

    def error_signals(self) -> dict[str, Any]:
        failed = [e.to_dict() for e in self.network if e.status and e.status >= 400]
        failed += [e.to_dict() for e in self.network if e.failure]
        console_errors = [c for c in self.console if c["type"] == "error"]
        return {
            "page_errors": list(self.page_errors),
            "console_errors": console_errors,
            "http_errors": failed,
            "dialogs": list(self.dialogs),
            "blocked_requests": list(self.blocked_requests),
            "redirect_checks": list(self.redirect_checks),
            "websocket_guard": getattr(self, "websocket_guard", "UNKNOWN"),
            "dns_pinned_hosts": sorted(self.policy.pinned_dns),
        }

    def stop_trace(self) -> bytes | None:
        if not self._tracing:
            return None
        try:
            path = f".traceguard/tmp/trace-{self.run_id or 'run'}-{int(time.time() * 1000)}.zip"
            import os

            os.makedirs(os.path.dirname(path), exist_ok=True)
            self.context.tracing.stop(path=path)
            self._tracing = False
            with open(path, "rb") as fh:
                data = fh.read()
            try:
                os.remove(path)
            except (OSError, SystemExit):
                # SystemExit：沙箱环境的 safe-delete 守卫可能拦截删除；
                # 删除失败只留下临时 trace 文件，不影响 Run 结果
                pass
            return data
        except Exception:  # noqa: BLE001
            logger.warning("trace_stop_failed", run_id=self.run_id)
            return None

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            if self._tracing:
                self.context.tracing.stop()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.context.close()
        except Exception:  # noqa: BLE001
            logger.warning("context_close_failed", run_id=self.run_id)
