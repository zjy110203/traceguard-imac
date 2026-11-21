"""Explorer Agent 工作流（阶段5）：有状态探索 + 预算 + 停止条件 + Trace。

流程：TestIntent → 观测 → planner(候选动作) → Tool Gateway(强制检查) → 执行 → 新状态 → Trace。
不变量：
- 每个动作都经 Gateway 检查（权限/参数/域名/预算）；
- 模型输出是候选，Gateway 拒绝则不执行；
- 页面内容不可信，不能提升工具权限；
- 探索成功 ≠ 测试通过；不晋升正式 TestCase、不批准 Oracle。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from traceguard_core.ids import new_id, utc_now_iso

from ..gateway.model_gateway import ModelDecision, ModelGateway
from ..gateway.tool_gateway import ToolCall, ToolGateway

PageObserver = Callable[[dict[str, Any]], dict[str, Any]]


@dataclass
class ExplorationTrace:
    exploration_id: str
    test_intent_ref: str
    application_version: str
    browser_version: str
    agent_version: str
    prompt_version: str
    tool_version: str
    action_sequence: list[dict[str, Any]] = field(default_factory=list)
    page_state_refs: list[str] = field(default_factory=list)
    event_refs: list[str] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)
    stop_reason: str = ""
    started_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "exploration_id": self.exploration_id,
            "test_intent_ref": self.test_intent_ref,
            "application_version": self.application_version,
            "browser_version": self.browser_version,
            "agent_version": self.agent_version,
            "prompt_version": self.prompt_version,
            "tool_version": self.tool_version,
            "action_sequence": list(self.action_sequence),
            "page_state_refs": list(self.page_state_refs),
            "event_refs": list(self.event_refs),
            "evidence_refs": list(self.evidence_refs),
            "stop_reason": self.stop_reason,
            "started_at": self.started_at,
        }


class Explorer:
    def __init__(
        self,
        gateway: ToolGateway,
        planner: ModelGateway,
        page_observer: PageObserver,
        *,
        application_version: str = "teamflow-1.0.0",
    ) -> None:
        self.gateway = gateway
        self.planner = planner
        self.page_observer = page_observer
        self.application_version = application_version

    def explore(self, intent: str, max_steps: int | None = None) -> ExplorationTrace:
        trace = ExplorationTrace(
            exploration_id=new_id("exploration"),
            test_intent_ref=intent,
            application_version=self.application_version,
            browser_version="http-observer" if self.gateway.executors else "unspecified",
            agent_version="explorer-v1",
            prompt_version=self.planner.version,
            tool_version="tool-gateway-v1",
        )
        history: list[ModelDecision] = []
        observation = self.page_observer({"url": "about:blank"})
        limit = min(max_steps or self.gateway.budget.max_steps, self.gateway.budget.max_steps)

        for _ in range(limit):
            # planner 产生候选动作（不可信，需 Gateway 检查）
            decision = self.planner.plan(intent, observation, history)
            if decision.action == "stop":
                trace.stop_reason = decision.reason
                break

            # 记录历史：fill/click 用 action:test_id 作稳定 marker，其余用 action
            tid = decision.params.get("test_id", "")
            marker = f"{decision.action}:{tid}" if tid else decision.action
            history.append(ModelDecision(decision.action, decision.params, marker))

            tool_call = ToolCall(
                tool=decision.action,
                params=decision.params,
                source="agent",
            )
            try:
                result = self.gateway.execute(tool_call)
                trace.action_sequence.append(
                    {
                        "action": decision.action,
                        "params": decision.params,
                        "result": str(result)[:300],
                        "reason": decision.reason,
                        "gateway": "allowed",
                    }
                )
                # 执行后更新观测
                observation = self.page_observer(decision.params)
                trace.page_state_refs.append(f"page_{len(trace.page_state_refs) + 1}")
            except Exception as e:  # noqa: BLE001
                # Gateway 拒绝或执行失败：记录，不推进动作（防止绕过）
                trace.action_sequence.append(
                    {
                        "action": decision.action,
                        "params": decision.params,
                        "gateway": "denied",
                        "error": str(e)[:300],
                    }
                )
                trace.stop_reason = f"BLOCKED: {str(e)[:120]}"
                break
        else:
            trace.stop_reason = "STEP_BUDGET_EXHAUSTED"

        # 探索成功不是测试通过——Trace 中不产生 Test Verdict
        return trace
