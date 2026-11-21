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
    "TF-API-001": "创建接口返回 500（API 异常）",
    "TF-NET-001": "请求延迟/超时（网络超时）",
    "TF-DB-001": "UI 显示成功但持久化被回滚（数据持久化失败）",
    "TF-UI-001": "成功提示缺失或错误显示（UI 表现异常）",
    "TF-TIME-001": "列表刷新延迟/竞态（异步时序异常）",
}


async def apply_network_delay() -> None:
    """TF-NET-001：在 API 处理前注入延迟。"""
    if FAULT_STATE.is_active("TF-NET-001"):
        delay_ms = FAULT_STATE.param("TF-NET-001", "delay_ms", 3000)
        await asyncio.sleep(delay_ms / 1000.0)
