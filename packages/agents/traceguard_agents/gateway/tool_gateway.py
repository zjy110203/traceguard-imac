"""Tool Gateway（阶段5）：每次工具调用的强制检查点。

安全检查顺序（不可颠倒）：
1. 权限检查（工具是否在 Agent 白名单）
2. 参数检查（参数类型/取值）
3. 域名/范围检查（SSRF/域名白名单/Workspace 约束）
4. 风险检查
5. 预算检查（步骤/时间/调用预算）
6. 执行
7. 审计

关键安全原则：
- 页面内容是不可信数据，永远不能授予或提升工具权限（Prompt Injection 防护）；
- 不允许任意 Shell、任意文件访问、任意网络；
- 凭证不进入普通 Prompt/日志。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from traceguard_core.errors import AppError, ErrorCode
from traceguard_core.security.target_policy import TargetPolicy


@dataclass
class Budget:
    max_steps: int = 30
    max_tool_calls: int = 50
    max_model_calls: int = 20
    max_duration_seconds: float = 120.0
    max_wallclock: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        self.max_wallclock = time.time() + self.max_duration_seconds


@dataclass
class ToolCall:
    tool: str
    params: dict[str, Any]
    source: str = "agent"  # agent / page_content（页面内容触发的工具调用必须拒绝）


@dataclass
class GatewayDecision:
    allowed: bool
    reason: str
    checks: dict[str, bool] = field(default_factory=dict)
    detail: str = ""


class ToolGateway:
    """工具调用权限网关。"""

    # 探索阶段允许的最小工具集（无 shell / 无文件 / 无任意网络）
    ALLOWED_TOOLS = {"navigate", "click", "fill", "read_page", "read_dom", "screenshot"}
    # 只读工具：合法调用可以不带参数（无写风险，scope/risk 检查不变）
    READ_ONLY_TOOLS = {"read_page", "read_dom", "screenshot"}
    # 批准的域名白名单（TeamFlow 本地）
    ALLOWED_HOSTS = {"127.0.0.1", "localhost"}
    ALLOWED_PORTS = {8011}  # TeamFlow 端口

    def __init__(
        self,
        allowed_tools: set[str] | None = None,
        allowed_hosts: set[str] | None = None,
        allowed_ports: set[int] | None = None,
        budget: Budget | None = None,
        target_policy: TargetPolicy | None = None,
        base_url: str = "",
    ) -> None:
        self.allowed_tools = allowed_tools or set(self.ALLOWED_TOOLS)
        self.allowed_hosts = allowed_hosts or set(self.ALLOWED_HOSTS)
        self.allowed_ports = allowed_ports or set(self.ALLOWED_PORTS)
        self.budget = budget or Budget()
        self._calls = 0
        self._steps = 0
        self._audit: list[dict[str, Any]] = []
        self._lock = threading.RLock()
        self.executors: dict[str, Callable[[dict[str, Any]], Any]] = {}
        # 明确禁止的工具（即使调用方尝试加入白名单）
        self.FORBIDDEN = {"shell", "exec", "file_read", "file_write", "arbitrary_http"}
        # 与正式 Playwright Runner 共用同一份目标策略：不能一边严一边松
        self.base_url = base_url.rstrip("/")
        if target_policy is not None:
            self.policy = target_policy
        else:
            origins = [f"http://{h}:{p}" for h in self.allowed_hosts for p in self.allowed_ports]
            self.policy = TargetPolicy.from_origins(origins)

    def register_executor(self, tool: str, fn: Callable[[dict[str, Any]], Any]) -> None:
        self.executors[tool] = fn

    def _check_host(self, url: str) -> bool:
        """兼容入口：统一委托给 TargetPolicy（不得与正式 Runner 出现两套规则）。"""
        return self.policy.check(url).allowed

    def check_landed_url(self, url: str) -> dict[str, Any]:
        """跳转/重定向后的落地地址复核（Explorer 侧）。"""
        decision = self.policy.check_landed_url(url)
        if not decision.allowed:
            with self._lock:
                self._audit.append(
                    {
                        "tool": "landed_url",
                        "url": url,
                        "allowed": False,
                        "reason": decision.reason,
                        "at": time.time(),
                    }
                )
        return {"allowed": decision.allowed, "reason": decision.reason, "url": url}

    def check(self, call: ToolCall) -> GatewayDecision:
        checks: dict[str, bool] = {}

        # Prompt Injection 防护：页面内容触发的工具调用一律拒绝（不可提升权限）
        if call.source == "page_content":
            return GatewayDecision(
                False, "PAGE_CONTENT_CANNOT_GRANT_TOOL_PERMISSION", {"permission": False}
            )

        # 1. 权限检查
        checks["permission"] = call.tool in self.allowed_tools and call.tool not in self.FORBIDDEN
        if not checks["permission"]:
            return GatewayDecision(False, f"TOOL_NOT_ALLOWED: {call.tool}", checks)

        # 2. 参数检查（只读工具允许空参数；写工具必须携带参数）
        checks["params"] = isinstance(call.params, dict) and (
            bool(call.params) or call.tool in self.READ_ONLY_TOOLS
        )
        if not checks["params"]:
            return GatewayDecision(False, "INVALID_PARAMS", checks)

        # 3. 域名/范围检查（navigate 类工具；与正式 Playwright Runner 同一策略）
        checks["scope"] = True
        if call.tool == "navigate":
            raw = str(call.params.get("url", ""))
            decision = self.policy.resolve(raw, self.base_url or self.policy.base_url)
            checks["scope"] = decision.allowed
            if not checks["scope"]:
                # 相对路径按 base 解析，协议相对与绝对 URL 都必须重新过白名单
                return GatewayDecision(False, "UNAUTHORIZED_TARGET", checks, detail=decision.reason)

        # 4. 风险检查（写操作限定在可清理测试数据）
        checks["risk"] = True
        if call.tool in ("click", "fill"):
            checks["risk"] = bool(call.params.get("workspace") in (None, "default", "test"))
            if not checks["risk"]:
                return GatewayDecision(False, "WRITE_OUTSIDE_TEST_SCOPE", checks)

        # 5. 预算检查
        with self._lock:
            calls_ok = self._calls < self.budget.max_tool_calls
            time_ok = time.time() < self.budget.max_wallclock
        checks["budget"] = calls_ok and time_ok
        if not calls_ok:
            return GatewayDecision(False, "TOOL_CALL_BUDGET_EXHAUSTED", checks)
        if not time_ok:
            return GatewayDecision(False, "TIME_BUDGET_EXHAUSTED", checks)

        return GatewayDecision(True, "ALLOWED", checks)

    def execute(self, call: ToolCall) -> Any:
        decision = self.check(call)
        with self._lock:
            self._audit.append(
                {
                    "tool": call.tool,
                    "params_keys": sorted(call.params.keys()),
                    "source": call.source,
                    "allowed": decision.allowed,
                    "reason": decision.reason,
                    "at": time.time(),
                }
            )
        if not decision.allowed:
            raise AppError(ErrorCode.AUTHORIZATION_ERROR, decision.reason)
        with self._lock:
            self._calls += 1
        fn = self.executors.get(call.tool)
        if not fn:
            raise AppError(ErrorCode.RESOURCE_UNAVAILABLE, f"no executor for {call.tool}")
        return fn(call.params)

    def audit_log(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._audit)
