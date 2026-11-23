"""RUN-005 WP1.3：真实 SSE 业务事件流测试。

验收点：真实事件推送 / 多事件连续推送 / 客户端断开与恢复 / Last-Event-ID 补发 /
多 Workspace 隔离 / 无新事件心跳 / 轮询降级（HTTP 事件查询仍可用）。
"""

from __future__ import annotations

import json

import pytest
from traceguard_persistence import Database, set_database


@pytest.fixture(scope="module")
def client(work_tmp):  # type: ignore[no-untyped-def]
    import uuid

    from fastapi.testclient import TestClient
    from traceguard_api.main import create_app
    from traceguard_api.runtime import get_runtime, reset_runtime
    from traceguard_api.services import reset_services

    base = work_tmp / f"sse-{uuid.uuid4().hex[:8]}"
    base.mkdir(parents=True, exist_ok=True)
    db = Database(f"sqlite:///{base / 'sse.db'}")
    set_database(db)
    reset_runtime()
    reset_services()
    app = create_app()
    with TestClient(app) as c:
        rt = get_runtime()
        iid = rt.events.new_producer_instance("test-producer")
        for idx in range(3):
            rt.events.append(
                event_type="run.started",
                server_workspace_id="ws1",
                producer_id="test-producer",
                producer_instance_id=iid,
                idempotency_key=f"ws1-e{idx}",
                payload={"run_id": "run_sse_1", "index": idx},
                context_refs={"run": "run_sse_1"},
            )
        rt.events.append(
            event_type="run.started",
            server_workspace_id="ws2",
            producer_id="test-producer",
            producer_instance_id=iid,
            idempotency_key="ws2-e0",
            payload={"run_id": "run_sse_2", "index": 0},
            context_refs={"run": "run_sse_2"},
        )
        yield c
    reset_runtime()
    reset_services()


def _read_event_lines(resp, limit: int = 10) -> list[dict]:
    """从流中读出 N 个完整事件帧（id/event/data）。"""
    events: list[dict] = []
    current: dict = {}
    for line in resp.iter_lines():
        if line.startswith("id: "):
            current["id"] = line[4:]
        elif line.startswith("event: "):
            current["event"] = line[7:]
        elif line.startswith("data: "):
            current["data"] = json.loads(line[6:])
        elif line == "" and current:
            events.append(current)
            current = {}
            if len(events) >= limit:
                break
    return events


def test_stream_pushes_real_business_events(client) -> None:
    with client.stream("GET", "/events/stream?limit=2&poll_interval=0.05") as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        events = _read_event_lines(resp, limit=2)
    assert len(events) == 2
    assert all(e["event"] == "run.started" for e in events)
    assert events[0]["data"]["payload"]["run_id"] == "run_sse_1"
    # 游标同时包含 producer 实例与实例内序号（不虚构全局顺序）
    assert "pi=" in events[0]["id"] and "seq=" in events[0]["id"]
    assert events[0]["data"]["recorded_at"] <= events[1]["data"]["recorded_at"]


def test_last_event_id_resumes_without_duplicates(client) -> None:
    with client.stream("GET", "/events/stream?limit=1&poll_interval=0.05") as resp:
        first = _read_event_lines(resp, limit=1)
    assert len(first) == 1
    resume_from = first[0]["id"]

    with client.stream(
        "GET",
        "/events/stream?limit=2&poll_interval=0.05",
        headers={"Last-Event-ID": resume_from},
    ) as resp:
        rest = _read_event_lines(resp, limit=2)
    ids = [e["id"] for e in rest]
    assert resume_from not in ids
    assert len(ids) == 2
    # 续传事件确实在原事件之后
    assert rest[0]["data"]["recorded_at"] >= first[0]["data"]["recorded_at"]


def test_workspace_filter_isolates_streams(client) -> None:
    with client.stream("GET", "/events/stream?workspace_id=ws2&limit=1&poll_interval=0.05") as resp:
        events = _read_event_lines(resp, limit=1)
    assert [e["data"]["workspace_id"] for e in events] == ["ws2"]


def test_run_filter(client) -> None:
    with client.stream(
        "GET", "/events/stream?run_id=run_sse_1&limit=10&poll_interval=0.05"
    ) as resp:
        events = _read_event_lines(resp, limit=3)
    assert all(e["data"]["context_refs"]["run"] == "run_sse_1" for e in events)
    assert len(events) == 3


def test_heartbeat_when_no_new_events(client) -> None:
    # 从最新游标之后开始 → 没有新事件，只能收到心跳
    latest = client.get("/api/v1/events", params={"limit": 1}).json()[0]
    from traceguard_api.sse import encode_cursor

    class _Latest:
        producer_instance_id = latest["producer_instance_id"]
        sequence = latest["sequence"]
        recorded_at = latest["recorded_at"]
        event_id = latest["event_id"]

    seen: dict[str, str] = {}
    with client.stream(
        "GET",
        "/events/stream",
        params={
            "last_event_id": encode_cursor(_Latest),
            "heartbeat_seconds": 0.2,
            "poll_interval": 0.05,
        },
    ) as resp:
        for line in resp.iter_lines():
            if line.startswith(": ") and "keepalive" in line:
                seen["heartbeat"] = line
                break
    assert "heartbeat" in seen
    assert "cursor=" in seen["heartbeat"]


def test_polling_fallback_still_works(client) -> None:
    """SSE 不可用时前端可用 HTTP 事件查询降级（同一权威数据源）。"""
    r = client.get("/api/v1/events", params={"workspace_id": "ws1", "limit": 10})
    assert r.status_code == 200
    assert len(r.json()) >= 3


def test_invalid_cursor_falls_back_to_head(client) -> None:
    with client.stream(
        "GET", "/events/stream?last_event_id=garbage&limit=1&poll_interval=0.05"
    ) as resp:
        events = _read_event_lines(resp, limit=1)
    assert len(events) == 1  # 非法游标不伪造续传位置，从头开始
