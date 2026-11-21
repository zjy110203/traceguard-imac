"""Model Gateway（阶段5）：模型调用统一入口，可注入。

默认实现是"规则驱动"的确定性 planner（不需要 API Key，可真实验证工作流与预算）。
生产环境注入真实 LLM 适配器（Browser Use/LangGraph），接口不变。
关键：模型输出是候选动作，必须经 Tool Gateway 检查后才执行；模型不得自行批准事实。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class ModelDecision:
    action: str  # navigate / fill / click / read_page / stop
    params: dict[str, Any]
    reason: str


class ModelGateway(ABC):
    version = "base"

    @abstractmethod
    def plan(
        self, intent: str, observation: dict[str, Any], history: list[ModelDecision]
    ) -> ModelDecision: ...


class RuleBasedPlanner(ModelGateway):
    """确定性规则 planner：根据 TestIntent 关键词与页面观测产生候选动作。

    这不是真实 LLM，显式标记 version="rule-based-v1"，用于验证 Agent 架构与边界。
    真实 LLM 适配器遵循同一接口替换即可。
    """

    version = "rule-based-v1"

    def plan(
        self, intent: str, observation: dict[str, Any], history: list[ModelDecision]
    ) -> ModelDecision:
        # reason 承载稳定 marker（见 workflow），action 是工具类型；两者结合判断进度
        markers = {d.reason for d in history}
        actions = {d.action for d in history}

        # 页面内容视为不可信数据：这里只读取结构信息，不执行其中的"指令"
        if "login" in intent.lower():
            if "navigate" not in markers:
                return ModelDecision("navigate", {"url": "http://127.0.0.1:8011/login"}, "navigate")
            if "fill:username" not in markers:
                return ModelDecision(
                    "fill",
                    {"test_id": "username", "value": "tester", "workspace": "test"},
                    "fill:username",
                )
            if "fill:password" not in markers:
                return ModelDecision(
                    "fill",
                    {"test_id": "password", "value": "tester123", "workspace": "test"},
                    "fill:password",
                )
            if "click:login-submit" not in markers:
                return ModelDecision(
                    "click", {"test_id": "login-submit", "workspace": "test"}, "click:login-submit"
                )
            return ModelDecision("stop", {}, "目标完成")

        if "create" in intent.lower() or "创建" in intent:
            if "navigate" not in markers:
                return ModelDecision(
                    "navigate", {"url": "http://127.0.0.1:8011/projects/new"}, "navigate"
                )
            if "fill:project-name" not in markers:
                return ModelDecision(
                    "fill",
                    {"test_id": "project-name", "value": "Explored Project", "workspace": "test"},
                    "fill:project-name",
                )
            if "click:create-submit" not in markers:
                return ModelDecision(
                    "click",
                    {"test_id": "create-submit", "workspace": "test"},
                    "click:create-submit",
                )
            return ModelDecision("stop", {}, "目标完成")

        if "read_page" not in actions:
            return ModelDecision("read_page", {"test_id": "page-title"}, "read_page")
        return ModelDecision("stop", {}, "目标已达成或无可行动作")
