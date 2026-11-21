"""Ground Truth 隔离域（阶段1，关键安全边界）。

规则（架构原则15 / 用户指令阶段1）：
- Ground Truth 独立于被测页面与 Agent 运行上下文；
- Agent 不得直接读取 Ground Truth；
- 仅"评测侧"通过专用令牌读取，令牌来自环境变量 TG_GROUND_TRUTH_TOKEN，默认随机生成且仅在进程内；
- Ground Truth 记录每个 Benchmark 场景的真实预期与故障机制，供 Benchmark 计分，绝不暴露给 Explorer/Diagnosis Agent 的工具集。

实现：独立内存字典 + 独立令牌校验。不写入任何 Agent 可访问的 API 路由。
"""

from __future__ import annotations

import secrets
import threading
from dataclasses import dataclass, field
from typing import Any


@dataclass
class GroundTruthRecord:
    scenario_id: str
    fault_mechanism_id: str
    # 真实预期：例如"项目未持久化" / "API 返回 500"
    expected_business_state: dict[str, Any] = field(default_factory=dict)
    expected_failure_class: str = ""
    notes: str = ""


class GroundTruthStore:
    """仅评测侧可访问。令牌不入库、不打印、不通过业务 API 暴露。"""

    def __init__(self) -> None:
        self._token = secrets.token_urlsafe(24)
        self._records: dict[str, GroundTruthRecord] = {}
        self._lock = threading.Lock()

    @property
    def evaluation_token(self) -> str:
        """仅供评测 Harness 在进程内获取；禁止写入日志/API 响应/Agent 上下文。"""
        return self._token

    def check_token(self, token: str | None) -> bool:
        return bool(token) and secrets.compare_digest(str(token), self._token)

    def put(self, rec: GroundTruthRecord) -> None:
        with self._lock:
            self._records[rec.scenario_id] = rec

    def get(self, scenario_id: str) -> GroundTruthRecord | None:
        with self._lock:
            return self._records.get(scenario_id)

    def all_public_refs(self) -> list[str]:
        """仅返回 scenario_id 引用（不含预期内容），供 Agent 侧知道有哪些场景但不泄露答案。"""
        with self._lock:
            return sorted(self._records.keys())


GROUND_TRUTH = GroundTruthStore()
