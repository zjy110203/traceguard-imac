"""RUN-013 WP2：多 Workspace 并发一致性与对象级授权（API 级真实验证）。

覆盖指令 §4：
- `/events/stream` 必须认证 + 按服务端授权过滤（修复前该路径不在 `/api/v1` 下，
  认证中间件不覆盖，匿名/越权用户可订阅任意 Workspace 事件流）；
- SSE 续传游标不能绕过 Workspace 授权；
- 并发多用户读取不出现跨空间数据回填；
- 用户 Workspace 授权被撤销后，已登录会话的后续请求立即失效；
- 事件详情 / 列表 / 下载的对象级授权。

不启动真实 Worker：本文件只验证 API 授权与并发一致性；真实浏览器并发验收见
`scripts/workspace_concurrency_acceptance_run013.py`。
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient
from traceguard_api import sse as sse_util
from traceguard_core.config import reset_settings_cache
from traceguard_persistence import Database, set_database
from traceguard_persistence import auth as auth_store
from traceguard_persistence.stores import PersistentEventStore


@pytest.fixture()
def ws_client(tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("TG_AUTH_ENABLED", "true")
    monkeypatch.setenv("TG_AUTH_CSRF_REQUIRED", "true")
    monkeypatch.setenv("TG_INLINE_WORKER", "false")
    monkeypatch.setenv("TG_LLM_PROVIDER", "")
    reset_settings_cache()
    from traceguard_api.auth import reset_auth_policy_cache
    from traceguard_api.main import create_app
    from traceguard_api.runtime import reset_runtime
    from traceguard_api.services import reset_services

    reset_auth_policy_cache()
    db = Database(f"sqlite:///{tmp_path / 'workspace-r13.db'}")
    set_database(db)
    reset_runtime()
    reset_services()
    app = create_app()
    with TestClient(app) as client:
        users = {}
        for name, roles, workspaces in (
            ("alice", ["qa-engineer"], ["ws-a"]),
            ("bob", ["qa-engineer"], ["ws-b"]),
            ("admin", ["platform-admin"], ["*"]),
            ("multi", ["qa-lead"], ["ws-a", "ws-b"]),
        ):
            created = auth_store.create_user(
                db, username=name, password="password-123", roles=roles
            )
            auth_store.set_user_workspaces(db, created["user_id"], workspaces)
            users[name] = created["user_id"]

        store = PersistentEventStore(db)
        events: dict[str, dict[str, Any]] = {}
        # 交错写入三个 Workspace 的事件：若服务端未过滤，前 N 条就会混入越权空间。
        for idx in range(3):
            for ws in ("ws-a", "ws-b", "ws-c"):
                stored = store.append(
                    event_type="run.completed",
                    server_workspace_id=ws,
                    producer_id="run013-test",
                    producer_instance_id=f"pi-{ws}",
                    idempotency_key=f"r13-{ws}-{idx}",
                    payload={"verdict": "PASSED", "workspace": ws},
                    context_refs={"run": f"run_{ws}_{idx}"},
                ).to_dict()
                events.setdefault(ws, stored)
        yield client, db, users, events, store
    reset_runtime()
    reset_services()
    reset_settings_cache()
    reset_auth_policy_cache()


def _login(client: TestClient, username: str) -> dict[str, str]:
    r = client.post("/api/v1/auth/login", json={"username": username, "password": "password-123"})
    assert r.status_code == 200, r.text
    return {"X-CSRF-Token": r.json()["csrf_token"]}


def _session_cookie(client: TestClient) -> str:
    for cookie in client.cookies.jar:
        if cookie.name == "tg_session":
            return str(cookie.value)
    raise AssertionError("login did not set tg_session cookie")


def _sse_events(response: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for line in response.text.splitlines():
        if line.startswith("data: "):
            out.append(json.loads(line[6:]))
    return out


def test_sse_requires_authentication(ws_client) -> None:  # type: ignore[no-untyped-def]
    client, _, _, _, _ = ws_client
    # 修复前 `/events/stream` 不在认证中间件覆盖范围，匿名也能建立事件流。
    r = client.get("/events/stream?workspace_id=ws-a&limit=3")
    assert r.status_code == 401, r.text
    assert r.json()["detail"]["code"] == "UNAUTHENTICATED"


def test_sse_rejects_foreign_workspace_and_filters_by_server_scope(
    ws_client,
) -> None:  # type: ignore[no-untyped-def]
    client, _, _, _, _ = ws_client
    _login(client, "alice")

    forbidden = client.get("/events/stream?workspace_id=ws-b&limit=1")
    assert forbidden.status_code == 403, forbidden.text
    assert forbidden.json()["detail"]["code"] == "WORKSPACE_FORBIDDEN"

    own = client.get("/events/stream?workspace_id=ws-a&limit=3")
    assert own.status_code == 200, own.text
    rows = _sse_events(own)
    assert len(rows) == 3 and {e["workspace_id"] for e in rows} == {"ws-a"}

    # 不传 workspace_id：只返回服务端授权范围（ws-a），不得出现 ws-b / ws-c。
    unscoped = client.get("/events/stream?limit=3")
    assert unscoped.status_code == 200, unscoped.text
    rows = _sse_events(unscoped)
    assert rows
    assert {e["workspace_id"] for e in rows} == {"ws-a"}


def test_sse_cursor_cannot_bypass_workspace_scope(ws_client) -> None:  # type: ignore[no-untyped-def]
    client, _, _, events, store = ws_client
    _login(client, "alice")

    # alice 拿到 ws-b 事件的游标（例如历史泄露/伪造），也不得借续传读取 ws-b。
    class _Cursor:
        producer_instance_id = events["ws-b"]["producer_instance_id"]
        sequence = events["ws-b"]["sequence"]
        recorded_at = events["ws-b"]["recorded_at"]
        event_id = events["ws-b"]["event_id"]

    cursor = sse_util.encode_cursor(_Cursor())
    # 游标之后再写入一条 ws-a 事件：越权过滤正确时应恰好收到它，而不是 ws-b 的后续事件。
    store.append(
        event_type="run.completed",
        server_workspace_id="ws-a",
        producer_id="run013-test",
        producer_instance_id="pi-ws-a",
        idempotency_key="r13-ws-a-tail",
        payload={"verdict": "PASSED", "workspace": "ws-a"},
        context_refs={"run": "run_ws-a_tail"},
    )
    r = client.get(f"/events/stream?last_event_id={quote(cursor)}&limit=1")
    assert r.status_code == 200, r.text
    rows = _sse_events(r)
    assert all(e["workspace_id"] != "ws-b" for e in rows), rows
    assert all(e["workspace_id"] != "ws-c" for e in rows), rows


def test_multi_workspace_user_sees_union_only(ws_client) -> None:  # type: ignore[no-untyped-def]
    client, _, _, _, _ = ws_client
    _login(client, "multi")
    r = client.get("/events/stream?limit=6")
    assert r.status_code == 200, r.text
    assert {e["workspace_id"] for e in _sse_events(r)} == {"ws-a", "ws-b"}

    _login(client, "admin")
    r = client.get("/events/stream?limit=9")
    assert r.status_code == 200, r.text
    assert {e["workspace_id"] for e in _sse_events(r)} == {"ws-a", "ws-b", "ws-c"}


def test_concurrent_reads_do_not_cross_workspaces(ws_client) -> None:  # type: ignore[no-untyped-def]
    client, _, _, _, _ = ws_client
    _login(client, "alice")
    alice_cookie = _session_cookie(client)
    _login(client, "bob")
    bob_cookie = _session_cookie(client)
    # 清空共享 CookieJar：并发请求各自显式携带自己的会话，避免互相覆盖。
    client.cookies.clear()

    def read(ws: str, cookie: str) -> set[str]:
        r = client.get(
            f"/api/v1/events?workspace_id={ws}",
            headers={"Cookie": f"tg_session={cookie}"},
        )
        assert r.status_code == 200, r.text
        return {str(row["workspace_id"]) for row in r.json()}

    jobs = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        for _ in range(12):
            jobs.append(pool.submit(read, "ws-a", alice_cookie))
            jobs.append(pool.submit(read, "ws-b", bob_cookie))
        results = [job.result() for job in jobs]
    assert results
    assert all(ws == {"ws-a"} for ws in results[0::2]), results[0::2]
    assert all(ws == {"ws-b"} for ws in results[1::2]), results[1::2]


def test_revoked_workspace_access_is_denied_immediately(ws_client) -> None:  # type: ignore[no-untyped-def]
    client, db, users, events, _ = ws_client
    _login(client, "alice")
    assert client.get("/api/v1/events?workspace_id=ws-a").status_code == 200

    # 撤销 ws-a，仅保留 ws-b：已登录会话的下一次请求就必须被拒绝。
    auth_store.set_user_workspaces(db, users["alice"], ["ws-b"])
    denied = client.get("/api/v1/events?workspace_id=ws-a")
    assert denied.status_code == 403, denied.text
    assert denied.json()["detail"]["code"] == "WORKSPACE_FORBIDDEN"

    sse_denied = client.get("/events/stream?workspace_id=ws-a&limit=1")
    assert sse_denied.status_code == 403, sse_denied.text

    # 事件详情也不能继续读取。
    detail = client.get(f"/api/v1/events/{events['ws-a']['event_id']}")
    assert detail.status_code == 403, detail.text


def test_foreign_event_detail_denied(ws_client) -> None:  # type: ignore[no-untyped-def]
    client, _, _, events, _ = ws_client
    _login(client, "alice")
    r = client.get(f"/api/v1/events/{events['ws-b']['event_id']}")
    assert r.status_code == 403, r.text
    own = client.get(f"/api/v1/events/{events['ws-a']['event_id']}")
    assert own.status_code == 200, own.text
    assert own.json()["workspace_id"] == "ws-a"
