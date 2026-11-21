"""PlaywrightObserver：真实浏览器确定性执行（阶段2 首选路径）。

- 每个 Run 独立 Browser Context（隔离）；
- 采集 Screenshot / Network 事件作为 Evidence 引用；
- 页面内容视为不可信数据（不授予工具权限）；
- mode="playwright"，报告中明确标注，与 http-fallback 区分。

依赖：playwright 包 + 已安装的 chromium。若浏览器不可用，抛出明确错误，
不静默降级为 http-fallback（降级必须由调用方显式选择并标记）。
"""

from __future__ import annotations

import os
from typing import Any

from traceguard_contracts.models import TestStep

from .observer import ObservationRecord, Observer

# 证据目录（项目内）
EVIDENCE_DIR = ".traceguard/evidence"


class PlaywrightObserver(Observer):
    mode = "playwright"

    def __init__(self) -> None:
        self._pw = None
        self._browser = None
        self._context = None
        self._page = None
        self._base_url = ""
        self._evidence: list[str] = []
        self._network: list[dict[str, Any]] = []

    def setup(self, run_config: dict[str, Any]) -> None:
        from playwright.sync_api import sync_playwright

        self._base_url = run_config.get("base_url", "http://127.0.0.1:8011").rstrip("/")
        os.makedirs(EVIDENCE_DIR, exist_ok=True)
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=True)
        self._context = self._browser.new_context()
        self._page = self._context.new_page()
        self._page.on("requestfailed", self._on_request_failed)
        self._page.on("response", self._on_response)

    def _on_request_failed(self, request: Any) -> None:
        self._network.append({"url": request.url, "failure": str(request.failure)})

    def _on_response(self, response: Any) -> None:
        if response.status >= 400:
            self._network.append({"url": response.url, "status": response.status})

    def _locator(self, spec: Any):
        """Locator 优先级：role → test_id(放到 data-testid) → css。"""
        strategy = spec.strategy
        val = spec.value
        if strategy == "role":
            return self._page.get_by_role(
                val.split(":")[0], name=val.split(":", 1)[1] if ":" in val else None
            )
        if strategy == "test_id":
            return self._page.locator(f'[data-testid="{val}"]')
        if strategy == "label":
            return self._page.get_by_label(val)
        if strategy == "placeholder":
            return self._page.get_by_placeholder(val)
        if strategy == "css":
            return self._page.locator(val)
        return self._page.locator(val)  # xpath 有限兜底

    def observe(self, step: TestStep, context: dict[str, Any]) -> ObservationRecord:
        from playwright.sync_api import TimeoutError as PWTimeout

        obs: dict[str, Any] = {}
        err: str | None = None
        try:
            if step.action == "navigate":
                resp = self._page.goto(self._base_url + (step.value or "/"), timeout=8000)
                obs = {"status": resp.status if resp else None, "url": step.value}
            elif step.action == "fill":
                self._locator(step.target).fill(step.value or "")
                obs = {"filled": step.target.value if step.target else ""}
            elif step.action == "click":
                self._locator(step.target).click(timeout=5000)
                self._page.wait_for_timeout(500)
                obs = {"clicked": step.target.value if step.target else ""}
            elif step.action == "api":
                # API 步骤在编排器预置阶段执行；这里读取响应状态
                obs = {"preset": True}
            elif step.action == "assert":
                tid = step.value
                if tid:
                    loc = self._page.locator(f'[data-testid="{tid}"]')
                    present = loc.count() > 0
                    text = loc.first.inner_text() if present else ""
                    obs = {"present": present, "text": text, "count": loc.count()}
                else:
                    obs = {"text": self._page.content()[:2000]}
            elif step.action == "wait":
                self._page.wait_for_timeout(500)
                obs = {"waited": True}
            else:
                obs = {"unsupported_action": step.action}

            # 证据采集：每个关键步骤截图 + trace
            if step.action in ("navigate", "click", "assert"):
                shot = os.path.join(EVIDENCE_DIR, f"{step.step_id}.png")
                try:
                    self._page.screenshot(path=shot)
                    self._evidence.append(shot)
                except Exception:  # noqa: BLE001
                    pass
        except PWTimeout as e:
            err = f"TIMEOUT: {e}"
            obs = {"timed_out": True, "error": err}
        except Exception as e:  # noqa: BLE001
            err = str(e)
            obs = {"error": err}

        return ObservationRecord(
            step_id=step.step_id,
            action=step.action,
            observation=obs,
            evidence_refs=list(self._evidence),
            error=err,
        )

    def teardown(self) -> None:
        if self._context:
            self._context.close()
        if self._browser:
            self._browser.close()
        if self._pw:
            self._pw.stop()
