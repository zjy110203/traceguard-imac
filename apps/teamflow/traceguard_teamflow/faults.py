"""可重复故障注入（阶段1）。

设计要点：
- 故障状态由专用控制 API 设置，可重复、可清理；
- 每个故障有明确 mechanism_id，便于诊断分类；
- 故障效果作用于业务 API / UI 表现 / 时序，且不影响"独立业务状态查询"通道（除非 DB 故障刻意测试持久化）。
- 不修改真实用户数据，仅作用于 TeamFlow 测试数据。
"""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass, field
from typing import Any


@dataclass
class FaultState:
    active: set[str] = field(default_factory=set)
    params: dict[str, Any] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def activate(self, fault_id: str, params: dict[str, Any] | None = None) -> None:
        with self._lock:
            self.active.add(fault_id)
            if params:
                self.params[fault_id] = params

    def clear(self, fault_id: str | None = None) -> None:
        with self._lock:
            if fault_id is None:
                self.active.clear()
                self.params.clear()
            else:
                self.active.discard(fault_id)
                self.params.pop(fault_id, None)

    def is_active(self, fault_id: str) -> bool:
        with self._lock:
            return fault_id in self.active

    def param(self, fault_id: str, key: str, default: Any = None) -> Any:
        with self._lock:
            return self.params.get(fault_id, {}).get(key, default)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {"active": sorted(self.active), "params": dict(self.params)}


FAULT_STATE = FaultState()

FAULT_IDS = {
    # 原有（RUN-001/002）
    "TF-API-001": "创建接口返回 500（API 异常）",
    "TF-NET-001": "请求延迟/超时（网络超时）",
    "TF-DB-001": "UI 显示成功但持久化被回滚（数据持久化失败）",
    "TF-UI-001": "成功提示缺失或错误显示（UI 表现异常）",
    "TF-TIME-001": "列表刷新延迟/竞态（异步时序异常）",
    # RUN-003-D 扩充
    "TF-API-002": "创建接口返回 503（服务不可用变体）",
    "TF-API-003": "更新接口返回 500（编辑链路 API 异常）",
    "TF-NET-002": "请求长延迟 9000ms（必然超时）",
    "TF-DB-002": "重名项目持久化静默失败（假通过变体）",
    "TF-DB-003": "独立状态查询滞后 2000ms（最终一致性，Flaky 来源）",
    "TF-DB-004": "独立状态查询滞后 9000ms（强滞后）",
    "TF-UI-002": "表单提交被静默忽略（无任何反馈）",
    "TF-UI-003": "错误提示误导（显示成功文案）",
    "TF-LOC-001": "提交按钮 test_id 变更（定位器失效）",
    "TF-LOC-002": "名称输入框 test_id 变更（定位器失效变体）",
    "TF-TIME-002": "列表刷新延迟 5000ms（强时序）",
    "TF-TIME-003": "双提交竞态（300ms 内第二次提交被丢弃）",
    "TF-SEC-001": "创建返回 403（权限异常）",
    "TF-SEC-002": "列表读取返回 403（读取权限异常）",
    "TF-COMP-001": "复合：API 500 + 错误提示缺失",
    "TF-COMP-002": "复合：持久化回滚 + 列表延迟",
    "TF-COMP-003": "复合：定位器失效 + 静默忽略",
    "TF-FLAKY-001": "Flaky：持久化回滚按 50% 概率触发",
    "TF-FLAKY-002": "Flaky：API 500 按 50% 概率触发",
    "TF-DB-005": "持久化写入慢 4000ms（最终落库）",
    "TF-API-004": "更新接口返回 503（编辑链路服务不可用）",
    "TF-UI-004": "成功提示延迟渲染 3000ms（提示晚到）",
}


async def apply_network_delay() -> None:
    """TF-NET-001：在 API 处理前注入延迟。TF-NET-002：9000ms 强延迟（必然超时）。"""
    if FAULT_STATE.is_active("TF-NET-001"):
        delay_ms = FAULT_STATE.param("TF-NET-001", "delay_ms", 3000)
        await asyncio.sleep(delay_ms / 1000.0)
    if FAULT_STATE.is_active("TF-NET-002"):
        await asyncio.sleep(9.0)
