"""真实浏览器会话管理（Playwright 同步 API）。

关键工程约束（踩过的坑，写入代码注释避免复发）：
- Playwright 同步对象是**线程亲和**的：必须在同一线程内 start/使用/stop。
  因此 Manager 采用 thread-local，一个线程一套 Playwright + Browser。
- 每个 Run 一个独立 BrowserContext（cookie/storage 隔离），Run 结束必关。
- 浏览器启动失败必须抛明确错误，绝不静默降级为 HTTP 观测。
- 进程退出/异常时通过 close_all() 释放，避免僵尸 chromium 进程。

RUN-010 WP2（受控出站代理接入正式执行内核）：
- 默认强制 egress：浏览器启动前先拉起 EgressProxyController，chromium 以
  `--proxy-server` 方式启动，所有网络请求（HTTP/HTTPS/WS/WSS）必须穿过
  代理逐请求校验；不存在「代理失败退回直连」的路径（fail-closed）。
- 代理审计与 Run/Worker 关联（见 egress.EgressProxyController）。
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field
from typing import Any

from traceguard_core.logging import get_logger

from .egress import EgressProxyController, EgressProxyDeadError, scheme_dual_policy

logger = get_logger("traceguard.browser")

#: 浏览器渠道优先级：显式配置 > 内置 chromium > 系统 Edge/Chrome
DEFAULT_CHANNELS = ("", "msedge", "chrome")


def egress_enforced_from_env() -> bool:
    """是否强制受控出站代理。默认开启；显式关闭必须写明仅限开发调试。"""
    raw = os.getenv("TG_EGRESS_ENFORCED")
    if raw is None:
        return True
    enforced = raw.strip().lower() in {"1", "true", "yes", "on"}
    if not enforced:
        logger.warning("egress_enforcement_disabled_explicitly")
    return enforced


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
    #: RUN-010 WP2：强制受控出站代理（默认 True；显式关闭仅限开发调试并打警告）
    egress_enforced: bool = True

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
            "egress_enforced": self.egress_enforced,
        }


class BrowserManager:
    """一个线程内复用浏览器实例；上下文按 Run 新建。

    egress_enforced=True（默认）时，本实例 1:1 绑定一个 EgressProxyController：
    浏览器启动前代理必须已在运行；代理死亡后任何新的上下文创建/网络执行都会
    失败关闭。
    """

    def __init__(
        self,
        config: BrowserConfig | None = None,
        *,
        worker_id: str = "",
        target_policy: Any | None = None,
    ) -> None:
        self.config = config or BrowserConfig()
        self._pw = None
        self._browser = None
        self._lock = threading.RLock()
        self._launches = 0
        self.launch_channel = ""
        # RUN-010 WP2：受控出站代理（与浏览器实例 1:1 绑定）
        self._egress: EgressProxyController | None = None
        self._worker_id = worker_id or os.getenv("TG_WORKER_ID", "")
        self._egress_policy: Any | None = target_policy
        self._pending_run_context: dict[str, str] = {}

    def egress_enforced(self) -> bool:
        return bool(self.config.egress_enforced)

    def proxy_controller(self) -> EgressProxyController | None:
        return self._egress

    def require_egress_alive(self) -> None:
        """执行前断言代理存活（fail-closed）。未启用 egress 时为空操作。"""
        if self.config.egress_enforced:
            if self._egress is None:
                raise EgressProxyDeadError("EGRESS_PROXY_NOT_STARTED: 代理未随浏览器启动")
            self._egress.require_alive()

    def set_run_context(self, *, run_id: str, worker_id: str = "") -> None:
        """Run 开始时调用：代理审计记录将携带 run_id/worker_id。"""
        if self._egress is not None:
            self._egress.set_run_context(run_id=run_id, worker_id=worker_id or self._worker_id)
        else:
            # 代理未启动时缓存，_start_egress 后由 set_egress_policy 一并补设
            self._pending_run_context = {
                "run_id": run_id,
                "worker_id": worker_id or self._worker_id,
            }

    def clear_run_context(self) -> None:
        if self._egress is not None:
            self._egress.clear_run_context()

    def set_egress_policy(self, policy: Any) -> None:
        """BrowserSession 在构造时同步其 TargetPolicy（单一授权事实源）。

        保证 Explorer 与正式 Runner 的代理授权规则一致：代理的评估结果与
        会话层 route guard 使用同一个策略对象。代理已在运行时更新其策略引用
        （代理侧使用 scheme 对偶变体，见 egress.scheme_dual_policy）。
        """
        self._egress_policy = policy
        if self._egress is not None:
            from .egress import scheme_dual_policy

            self._egress.policy = scheme_dual_policy(policy)

    def egress_audit(self) -> dict[str, Any]:
        """当前代理审计摘要（含 run/worker 关联与被拒目标清单）。"""
        if self._egress is None:
            return {"enforced": False, "total": 0, "allowed": 0, "blocked": 0}
        summary = self._egress.audit_summary()
        summary["enforced"] = True
        return summary

    # ---------------------------------------------------------------- lifecycle
    def _start_egress(self, base_url: str = "") -> None:
        """启动受控出站代理。目标策略优先显式传入，否则从 base_url 推导。"""
        from traceguard_core.security.target_policy import TargetPolicy

        policy = self._egress_policy
        if policy is None:
            env_origins = [
                o.strip() for o in os.getenv("TG_TARGET_EXTRA_ORIGINS", "").split(",") if o.strip()
            ]
            if base_url:
                policy = TargetPolicy.from_origins(
                    [base_url, *env_origins] if env_origins else [base_url]
                )
            else:
                policy = TargetPolicy.from_origins(env_origins or ["http://127.0.0.1:8011"])
        controller = EgressProxyController(scheme_dual_policy(policy), worker_id=self._worker_id)
        controller.start()
        self._egress = controller
        if self._pending_run_context:
            controller.set_run_context(**self._pending_run_context)
            self._pending_run_context = {}

    def start(self, base_url: str = "") -> None:
        with self._lock:
            if self._browser is not None:
                return
            # 1) 代理先行：代理起不来就不允许浏览器启动（fail-closed）
            if self.config.egress_enforced and self._egress is None:
                try:
                    self._start_egress(base_url)
                except Exception as exc:  # noqa: BLE001 - 转成明确的不可用错误
                    raise BrowserUnavailableError(
                        f"EGRESS_PROXY_UNAVAILABLE: 受控出站代理启动失败（{exc}）；"
                        "不允许无代理直连启动浏览器"
                    ) from exc
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
                    # 2) 所有浏览器流量强制经代理（HTTP 绝对形式 + CONNECT 隧道）
                    # RUN-010 实测：不要加 --host-resolver-rules=MAP * ~NOTFOUND。
                    # 该规则会让 Chromium 对 http:// 目标先发 CONNECT（https 升级
                    # 路径），对明文目标 TLS 握手失败后甚至可能直连回退；不加时
                    # Chromium 对 http:// 走标准绝对形式请求、对 https:// 走
                    # CONNECT（hostname 交给代理解析），均受逐请求校验约束。
                    if self.config.egress_enforced and self._egress is not None:
                        kwargs["proxy"] = {"server": self._egress.url}
                    self._browser = self._pw.chromium.launch(**kwargs)
                    self.launch_channel = channel or "bundled-chromium"
                    logger.info(
                        "browser_launched",
                        channel=self.launch_channel,
                        egress=self.config.egress_enforced,
                        proxy=self._egress.url if self._egress else "",
                    )
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
        # RUN-010 WP2：上下文创建前断言代理存活；代理死亡 → fail-closed
        self.require_egress_alive()
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
            # RUN-010 WP2：浏览器完全关闭后才能停代理（生命周期覆盖整个执行周期）
            if self._egress is not None:
                self._egress.stop()
                self._egress = None

    def health(self) -> dict[str, Any]:
        running = self._browser is not None and self._browser.is_connected()
        info: dict[str, Any] = {
            "running": running,
            "channel": self.launch_channel or self.config.channel or "bundled-chromium",
            "config": self.config.to_dict(),
            "egress": self.egress_audit(),
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
        egress_enforced=egress_enforced_from_env(),
    )


def probe_browser() -> dict[str, Any]:
    """环境自检：真实启动一次浏览器（含受控出站代理），返回版本与渠道。"""
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
