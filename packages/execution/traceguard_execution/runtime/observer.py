"""观测提供者（Observer）协议。

确定性执行内核与"如何获取观测"解耦：
- PlaywrightObserver：真实浏览器（阶段2 目标，需浏览器可用）；
- HttpObserver：基于 TeamFlow HTTP + HTML 解析的轻量确定性观测（本机浏览器不可用时的降级，明确标记）。
两者产出统一的 observation dict，交给 Assertion Evaluator，共享同一 Verdict 逻辑。
不允许把降级观测冒充真实浏览器执行。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from traceguard_contracts.models import TestStep


@dataclass
class ObservationRecord:
    step_id: str
    action: str
    observation: dict[str, Any] = field(default_factory=dict)
    evidence_refs: list[str] = field(default_factory=list)
    error: str | None = None


class Observer(ABC):
    mode: str = "base"

    @abstractmethod
    def observe(self, step: TestStep, context: dict[str, Any]) -> ObservationRecord: ...

    @abstractmethod
    def setup(self, run_config: dict[str, Any]) -> None: ...

    @abstractmethod
    def teardown(self) -> None: ...
