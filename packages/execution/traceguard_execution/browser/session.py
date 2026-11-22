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

from traceguard_core.logging import get_logger

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
    ) -> None:
        self.manager = manager
        self.base_url = base_url.rstrip("/")
        self.run_id = run_id
        self.context = manager.new_context()
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
            except OSError:
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
