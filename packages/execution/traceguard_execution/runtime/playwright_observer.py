"""PlaywrightObserver：真实浏览器确定性执行。

职责边界：
- 只负责"执行步骤 + 采集观测"，不做断言判定（判定在 Assertion Evaluator）；
- 每个 Run 独立 BrowserContext；步骤前后差分采集 console/network；
- 证据（截图 / DOM / ARIA / trace）以 bytes 交给 artifact_sink 上传，返回引用写入 observation；
- 浏览器不可用必须抛错，绝不静默降级为 http-fallback。

支持动作：navigate / click / fill / select / press / assert / wait / api
定位策略：role("button:登录") / accessible_name / label / placeholder / test_id / css / xpath
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from traceguard_contracts.models import TestStep
from traceguard_core.logging import get_logger

from ..browser.manager import BrowserConfig, BrowserManager, get_browser_manager
from ..browser.session import BrowserSession
from .observer import ObservationRecord, Observer

logger = get_logger("traceguard.observer.playwright")

ArtifactSink = Callable[[bytes, str, str], dict[str, Any]]


class PlaywrightObserver(Observer):
    mode = "playwright"

    def __init__(
        self,
        manager: BrowserManager | None = None,
        *,
        artifact_sink: ArtifactSink | None = None,
        capture_screenshot: bool = True,
        capture_dom: bool = True,
        capture_aria: bool = True,
        config: BrowserConfig | None = None,
    ) -> None:
        self._manager = manager
        self._artifact_sink = artifact_sink
        self._capture_screenshot = capture_screenshot
        self._capture_dom = capture_dom
        self._capture_aria = capture_aria
        self._config = config
        self.session: BrowserSession | None = None
        self._base_url = "http://127.0.0.1:8001"
        self._trace_data: bytes | None = None
        self.browser_info: dict[str, Any] = {}

    # ------------------------------------------------------------------ setup
    def setup(self, run_config: dict[str, Any]) -> None:
        self._base_url = str(run_config.get("base_url", "http://127.0.0.1:8001")).rstrip("/")
        self._manager = self._manager or get_browser_manager(self._config)
        self.session = BrowserSession(
            self._manager,
            base_url=self._base_url,
            run_id=str(run_config.get("run_id", "")),
            trace_enabled=bool(run_config.get("trace", True)),
        )
        try:
            self.browser_info = {
                "channel": self._manager.launch_channel,
                "version": self._manager._browser.version if self._manager._browser else "",
                **self._manager.config.to_dict(),
            }
        except Exception:  # noqa: BLE001
            self.browser_info = {"channel": self._manager.launch_channel}

    # ----------------------------------------------------------------- locator
    def _locator(self, spec: Any) -> Any:
        assert self.session is not None
        page = self.session.page
        strategy = spec.strategy
        val = spec.value
        if strategy == "role":
            role, _, name = val.partition(":")
            if name:
                return page.get_by_role(role.strip(), name=name.strip())
            return page.get_by_role(val.strip())
        if strategy == "accessible_name":
            loc = page.get_by_label(val)
            if loc.count() == 0:
                loc = page.get_by_role("button", name=val)
            if loc.count() == 0:
                loc = page.get_by_text(val, exact=True)
            return loc.first
        if strategy == "test_id":
            return page.get_by_test_id(val)
        if strategy == "label":
            return page.get_by_label(val)
        if strategy == "placeholder":
            return page.get_by_placeholder(val)
        if strategy == "xpath":
            return page.locator(f"xpath={val}")
        return page.locator(val)  # css 及兜底

    # ---------------------------------------------------------------- observe
    def observe(self, step: TestStep, context: dict[str, Any]) -> ObservationRecord:
        from playwright.sync_api import TimeoutError as PWTimeout

        assert self.session is not None
        session = self.session
        console_off = len(session.console)
        net_off = len(session.network)
        started = time.time()
        obs: dict[str, Any] = {}
        err: str | None = None
        timed_out = False

        try:
            action = step.action
            if action == "navigate":
                path = step.value or "/"
                url = path if path.startswith("http") else self._base_url + path
                resp = session.page.goto(url, wait_until="domcontentloaded")
                try:
                    session.page.wait_for_load_state("networkidle", timeout=4000)
                except Exception:  # noqa: BLE001 - 长轮询页面允许非 idle
                    pass
                session.wait_network_settle()
                obs = {
                    "url": session.current_url(),
                    "title": session.title(),
                    "status": resp.status if resp else None,
                    "navigated_to": path,
                }
            elif action == "click":
                loc = self._locator(step.target) if step.target else None
                if loc is None:
                    raise ValueError("click 缺少 target")
                loc.first.wait_for(state="visible", timeout=8000)
                loc.first.click(timeout=8000)
                try:
                    session.page.wait_for_load_state("networkidle", timeout=2500)
                except Exception:  # noqa: BLE001
                    pass
                session.wait_network_settle()
                obs = {
                    "clicked": step.target.value if step.target else "",
                    "url": session.current_url(),
                    "title": session.title(),
                }
            elif action == "fill":
                loc = self._locator(step.target) if step.target else None
                if loc is None:
                    raise ValueError("fill 缺少 target")
                loc.first.fill(step.value or "", timeout=8000)
                session.wait_network_settle(timeout_ms=800)
                obs = {
                    "filled": step.target.value if step.target else "",
                    "value_len": len(step.value or ""),
                }
            elif action == "select":
                loc = self._locator(step.target) if step.target else None
                if loc is None:
                    raise ValueError("select 缺少 target")
                loc.first.select_option(step.value or "")
                obs = {"selected": step.value}
            elif action == "press":
                key = step.value or "Enter"
                if step.target:
                    self._locator(step.target).first.press(key)
                else:
                    session.page.keyboard.press(key)
                obs = {"pressed": key, "url": session.current_url()}
            elif action == "wait":
                value = (step.value or "").strip()
                if value.isdigit():
                    session.page.wait_for_timeout(int(value))
                    obs = {"waited_ms": int(value)}
                elif value:
                    target = step.target
                    if target is not None:
                        self._locator(target).first.wait_for(state="visible", timeout=10000)
                        obs = {"waited_for": target.value}
                    else:
                        session.page.locator(value).first.wait_for(timeout=10000)
                        obs = {"waited_for": value}
                else:
                    session.page.wait_for_timeout(500)
                    obs = {"waited_ms": 500}
                session.wait_network_settle(timeout_ms=1500)
            elif action == "assert":
                obs = self._assert_snapshot(step)
            elif action == "api":
                obs = {"preset": True, "url": session.current_url()}
            else:
                obs = {"unsupported_action": step.action}
                err = f"unsupported action: {step.action}"

        except PWTimeout as exc:
            timed_out = True
            err = f"TIMEOUT: {str(exc)[:500]}"
            obs = {"timed_out": True, "error": err, "url": self._safe_url()}
        except Exception as exc:  # noqa: BLE001
            err = f"{type(exc).__name__}: {str(exc)[:800]}"
            obs = {"error": err, "url": self._safe_url()}

        duration_ms = int((time.time() - started) * 1000)
        console_delta = session.console_since(console_off)
        network_delta = session.network_since(net_off)

        obs.setdefault("console", console_delta[-30:])
        obs.setdefault("network", network_delta[-50:])
        obs.setdefault("page_errors", list(session.page_errors))
        obs.setdefault("http_errors", [n for n in network_delta if (n.get("status") or 0) >= 400])
        obs["page_url"] = self._safe_url()
        obs["page_title"] = session.title()
        if timed_out:
            obs["timed_out"] = True

        artifacts: dict[str, dict[str, Any]] = {}
        if err is None or step.action in {"assert", "navigate", "click"}:
            artifacts.update(self._capture(step, obs))
        if err and "page_errors" not in obs:
            obs["page_errors"] = list(session.page_errors)

        return ObservationRecord(
            step_id=step.step_id,
            action=step.action,
            observation=obs,
            error=err,
            duration_ms=duration_ms,
            artifacts=artifacts,
        )

    # ---------------------------------------------------------------- helpers
    def _safe_url(self) -> str:
        try:
            return self.session.current_url() if self.session else ""
        except Exception:  # noqa: BLE001
            return ""

    def _assert_snapshot(self, step: TestStep) -> dict[str, Any]:
        assert self.session is not None
        page = self.session.page
        obs: dict[str, Any] = {}
        if step.target is not None:
            loc = self._locator(step.target)
            count = loc.count()
            present = count > 0
            text = loc.first.inner_text(timeout=3000) if present else ""
            visible = loc.first.is_visible() if present else False
            obs = {
                "present": present,
                "count": count,
                "text": text,
                "visible": visible,
                "test_id": step.value or "",
            }
        elif step.value:
            loc = page.locator(f'[data-testid="{step.value}"]')
            count = loc.count()
            present = count > 0
            text = loc.first.inner_text(timeout=3000) if present else ""
            obs = {
                "present": present,
                "count": count,
                "text": text,
                "visible": loc.first.is_visible() if present else False,
                "test_id": step.value,
            }
            if present:
                obs["table_rows"] = loc.locator("tr").count()
        else:
            obs = {
                "present": True,
                "count": 1,
                "text": page.locator("body").inner_text(timeout=3000)[:8000],
                "title": self.session.title(),
            }
        if step.value and step.value not in obs.get("test_id", ""):
            obs["test_id"] = step.value
        return obs

    def _capture(self, step: TestStep, obs: dict[str, Any]) -> dict[str, dict[str, Any]]:
        assert self.session is not None
        out: dict[str, dict[str, Any]] = {}
        sink = self._artifact_sink
        if sink is None:
            return out
        try:
            if self._capture_screenshot:
                png = self.session.screenshot(full_page=True)
                obs["screenshot_bytes"] = len(png)
                out["screenshot"] = sink(png, "image/png", f"{step.step_id}.png")
            if self._capture_dom:
                html = self.session.dom()
                out["dom"] = sink(html.encode(), "text/html", f"{step.step_id}.html")
            if self._capture_aria:
                aria = self.session.aria()
                if aria:
                    out["aria"] = sink(aria.encode(), "text/plain", f"{step.step_id}.aria.txt")
        except Exception as exc:  # noqa: BLE001 - 采集失败不应使步骤失败
            logger.warning("artifact_capture_failed", step=step.step_id, error=str(exc))
        return out

    # --------------------------------------------------------------- teardown
    def teardown(self) -> None:
        if self.session is not None:
            self._trace_data = self.session.stop_trace()
            self.session.close()
            self.session = None

    def drain_trace(self) -> dict[str, Any] | None:
        """Run 结束后取 trace zip，通常由 Pipeline 上传为证据。"""
        if not self._trace_data:
            return None
        data, self._trace_data = self._trace_data, None
        if self._artifact_sink is None:
            return None
        return self._artifact_sink(data, "application/zip", "playwright-trace.zip")
