"""真实浏览器会话管理（Playwright 同步 API）。

关键工程约束（踩过的坑，写入代码注释避免复发）：
- Playwright 同步对象是**线程亲和**的：必须在同一线程内 start/使用/stop。
  因此 Manager 采用 thread-local，一个线程一套 Playwright + Browser。
- 每个 Run 一个独立 BrowserContext（cookie/storage 隔离），Run 结束必关。
- 浏览器启动失败必须抛明确错误，绝不静默降级为 HTTP 观测。
- 进程退出/异常时通过 close_all() 释放，避免僵尸 chromium 进程。
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field
from typing import Any

from traceguard_core.logging import get_logger

logger = get_logger("traceguard.browser")

#: 浏览器渠道优先级：显式配置 > 内置 chromium > 系统 Edge/Chrome
DEFAULT_CHANNELS = ("", "msedge", "chrome")


class BrowserUnavailableError(RuntimeError):
    """真实浏览器不可用（依赖缺失或启动失败）。"""


@dataclass
class BrowserConfig:
    headless: bool = True
    channel: str = ""  # "" = playwright 自带 chromium
    viewport_width: int = 1280
    viewport_height: int = 800
    default_timeout_ms: int = 8000
    navigation_timeout_ms: int = 15000
    locale: str = "zh-CN"
    timezone: str = "Asia/Shanghai"
    slow_mo_ms: int = 0
    args: list[str] = field(default_factory=list)
    record_video: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "headless": self.headless,
            "channel": self.channel or "bundled-chromium",
            "viewport": f"{self.viewport_width}x{self.viewport_height}",
            "default_timeout_ms": self.default_timeout_ms,
            "navigation_timeout_ms": self.navigation_timeout_ms,
            "locale": self.locale,
            "timezone": self.timezone,
            "record_video": self.record_video,
        }


class BrowserManager:
    """一个线程内复用浏览器实例；上下文按 Run 新建。"""

    def __init__(self, config: BrowserConfig | None = None) -> None:
        self.config = config or BrowserConfig()
        self._pw = None
        self._browser = None
        self._lock = threading.RLock()
        self._launches = 0
        self.launch_channel = ""

    # ---------------------------------------------------------------- lifecycle
    def start(self) -> None:
        with self._lock:
            if self._browser is not None:
                return
            try:
                from playwright.sync_api import sync_playwright
            except ImportError as exc:  # pragma: no cover - 依赖缺失路径
                raise BrowserUnavailableError(
                    "playwright 未安装：pip install playwright && playwright install chromium"
                ) from exc
            self._pw = sync_playwright().start()
            errors: list[str] = []
            channels = [self.config.channel] if self.config.channel else list(DEFAULT_CHANNELS)
            for channel in channels:
                try:
                    kwargs: dict[str, Any] = {
                        "headless": self.config.headless,
                        "slow_mo": self.config.slow_mo_ms,
                        "args": self.config.args or ["--disable-dev-shm-usage", "--no-first-run"],
                    }
                    if channel:
                        kwargs["channel"] = channel
                    self._browser = self._pw.chromium.launch(**kwargs)
                    self.launch_channel = channel or "bundled-chromium"
                    logger.info("browser_launched", channel=self.launch_channel)
                    break
                except Exception as exc:  # noqa: BLE001 - 逐个渠道尝试
                    errors.append(f"{channel or 'bundled'}: {exc}")
                    continue
            if self._browser is None:
                self._pw.stop()
                self._pw = None
                raise BrowserUnavailableError("浏览器启动失败: " + " | ".join(errors))
            self._browser.on("disconnected", lambda *_: logger.warning("browser_disconnected"))

    def new_context(self, *, storage_state: dict | str | None = None) -> Any:
        self.start()
        assert self._browser is not None
        ctx = self._browser.new_context(
            viewport={"width": self.config.viewport_width, "height": self.config.viewport_height},
            locale=self.config.locale,
            timezone_id=self.config.timezone,
            storage_state=storage_state,
            ignore_https_errors=False,
        )
        ctx.set_default_timeout(self.config.default_timeout_ms)
        ctx.set_default_navigation_timeout(self.config.navigation_timeout_ms)
        return ctx

    def close(self) -> None:
        with self._lock:
            try:
                if self._browser is not None:
                    self._browser.close()
            except Exception:  # noqa: BLE001
                logger.warning("browser_close_failed")
            finally:
                self._browser = None
            try:
                if self._pw is not None:
                    self._pw.stop()
            except Exception:  # noqa: BLE001
                pass
            finally:
                self._pw = None

    def health(self) -> dict[str, Any]:
        running = self._browser is not None and self._browser.is_connected()
        info: dict[str, Any] = {
            "running": running,
            "channel": self.launch_channel or self.config.channel or "bundled-chromium",
            "config": self.config.to_dict(),
        }
        if running:
            try:
                info["version"] = self._browser.version
            except Exception:  # noqa: BLE001
                info["version"] = "unknown"
        return info


_LOCAL = threading.local()


def get_browser_manager(config: BrowserConfig | None = None) -> BrowserManager:
    """线程内单例：避免跨线程使用 Playwright 对象。"""
    mgr = getattr(_LOCAL, "manager", None)
    if mgr is None:
        mgr = BrowserManager(config or config_from_env())
        _LOCAL.manager = mgr
    elif config is not None:
        mgr.config = config
    return mgr


def config_from_env() -> BrowserConfig:
    def _bool(name: str, default: bool) -> bool:
        v = os.getenv(name)
        if v is None:
            return default
        return v.lower() in {"1", "true", "yes", "on"}

    return BrowserConfig(
        headless=_bool("TG_BROWSER_HEADLESS", True),
        channel=os.getenv("TG_BROWSER_CHANNEL", ""),
        viewport_width=int(os.getenv("TG_BROWSER_WIDTH", "1280")),
        viewport_height=int(os.getenv("TG_BROWSER_HEIGHT", "800")),
        default_timeout_ms=int(os.getenv("TG_BROWSER_TIMEOUT_MS", "8000")),
        navigation_timeout_ms=int(os.getenv("TG_BROWSER_NAV_TIMEOUT_MS", "15000")),
        record_video=_bool("TG_BROWSER_VIDEO", False),
    )


def probe_browser() -> dict[str, Any]:
    """环境自检：真实启动一次浏览器，返回版本与渠道（用于 /health 与部署校验）。"""
    mgr = BrowserManager(config_from_env())
    try:
        mgr.start()
        info = mgr.health()
        ctx = mgr.new_context()
        page = ctx.new_page()
        page.set_content("<html><body><h1>ok</h1></body></html>")
        info["smoke"] = page.inner_text("h1") == "ok"
        ctx.close()
        return info
    finally:
        mgr.close()


def close_current_thread_browser() -> None:
    mgr = getattr(_LOCAL, "manager", None)
    if mgr is not None:
        mgr.close()
        _LOCAL.manager = None
