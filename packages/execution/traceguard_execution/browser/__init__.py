"""真实浏览器执行子包。"""

from __future__ import annotations

from .manager import (
    BrowserConfig,
    BrowserManager,
    BrowserUnavailableError,
    close_current_thread_browser,
    config_from_env,
    get_browser_manager,
    probe_browser,
)
from .session import BrowserSession, NetworkEntry

__all__ = [
    "BrowserConfig",
    "BrowserManager",
    "BrowserSession",
    "BrowserUnavailableError",
    "NetworkEntry",
    "close_current_thread_browser",
    "config_from_env",
    "get_browser_manager",
    "probe_browser",
]
