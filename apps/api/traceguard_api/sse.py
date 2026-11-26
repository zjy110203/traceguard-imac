"""Server-Sent Events 传输层。

语义边界（必须保持诚实）：
- SSE 是**实时展示通道**，不是业务状态权威来源；前端最终状态仍以 API / 数据库为准。
- Event 的 `sequence` 是 **producer 实例内部序号**，不存在跨 producer 的全局强顺序；
  因此游标同时记录 `producer_instance_id + sequence`（来源与续传位置）和
  `recorded_at + event_id`（事件存储的追加顺序，用于翻页续传）。
- 投递语义为 **at-least-once**：断线重连可能重复收到事件，客户端必须按 event_id 去重；
  重复事件不得触发重复业务副作用（服务端消费侧由 Consumer Inbox 保证幂等）。
- 无全局强顺序，只保证同一 producer 实例内 sequence 递增。
"""

from __future__ import annotations

import json
from typing import Any

#: 事件重连基础间隔（毫秒），浏览器 EventSource 的 retry 字段
RETRY_MS = 2000


def encode_cursor(event: Any) -> str:
    """把事件编码为可续传游标。

    形如 `pi=worker-1-inst-x;seq=17;at=2026-...Z;eid=evt_...`
    其中 `pi/seq` 标识来源与实例内位置，`at/eid` 用于在存储中定位追加顺序。
    """
    return (
        f"pi={event.producer_instance_id};seq={event.sequence};"
        f"at={event.recorded_at};eid={event.event_id}"
    )


def decode_cursor(raw: str | None) -> dict[str, str] | None:
    """解析游标；非法游标返回 None（从头开始，不伪造续传位置）。"""
    if not raw:
        return None
    out: dict[str, str] = {}
    for part in raw.split(";"):
        key, _, value = part.partition("=")
        if key and value:
            out[key.strip()] = value.strip()
    if not out.get("at") or not out.get("eid"):
        return None
    return out


def format_event(event: Any, *, event_id: str | None = None) -> bytes:
    """序列化为 SSE 帧。data 使用完整事件 JSON（含 workspace / context_refs）。"""
    payload = json.dumps(event.to_dict(), ensure_ascii=False, separators=(",", ":"))
    lines = [
        f"id: {event_id or encode_cursor(event)}",
        f"event: {event.event_type}",
        f"data: {payload}",
    ]
    return ("\n".join(lines) + "\n\n").encode()


def format_heartbeat(*, cursor: str | None = None, note: str = "keepalive") -> bytes:
    """心跳帧：注释行 + 当前游标，便于客户端发现"确实无新事件"。"""
    if cursor:
        return f": {note} cursor={cursor}\n\n".encode()
    return f": {note}\n\n".encode()


def format_retry(ms: int = RETRY_MS) -> bytes:
    return f"retry: {ms}\n\n".encode()


def format_notice(name: str, data: dict[str, Any]) -> bytes:
    """服务端控制帧（授权失效等）。

    与业务事件区分：使用独立 event 名 + 稳定 id 前缀，客户端可据此
    停止重连/提示重新登录，而不是把它当成业务事件写入本地状态。
    """
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"id: notice:{name}\nevent: {name}\ndata: {payload}\n\n".encode()


__all__ = [
    "RETRY_MS",
    "decode_cursor",
    "encode_cursor",
    "format_event",
    "format_heartbeat",
    "format_notice",
    "format_retry",
]
