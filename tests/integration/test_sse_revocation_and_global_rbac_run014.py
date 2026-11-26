"""RUN-014 WP3：SSE 活跃连接撤权 + 全局接口 RBAC + 统计口径。

覆盖指令 §六：
1. Alice 建立 ws-a 的 SSE 连接 → 撤权 → 写入新事件 → 不再收到 → 收到 auth.revoked 且连接关闭；
   重新授权后可正常订阅；
2. 会话撤销（登出）与被禁用用户：活跃连接立即失效；
3. 全局统计接口 RBAC：llm/summary、browser 仅 platform-admin（system.observe）；
4. LLM Summary 不再因 limit 静默截断（键集分页 + coverage 声明）；
5. queue/stats 真实行为：普通用户只统计授权 Workspace（修正 RUN-013 WP6 结论）；
6. Artifact 元数据缺失时的访问控制；Probe Audit 的持久化边界。
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from traceguard_core.config import reset_settings_cache
from traceguard_persistence import Database, set_database
from traceguard_persistence import auth as auth_store


@pytest.fixture()
def env(tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("TG_AUTH_ENABLED", "true")
    monkeypatch.setenv("TG_AUTH_CSRF_REQUIRED", "true")
    monkeypatch.setenv("TG_LLM_PROVIDER", "")
    monkeypatch.setenv("TG_ARTIFACT_DIR", str(tmp_path / "artifacts"))
    reset_settings_cache()
    from traceguard_api.auth import reset_auth_policy_cache
    from traceguard_api.main import create_app
    from traceguard_api.runtime import get_runtime, reset_runtime
    from traceguard_api.services import reset_services

    reset_auth_policy_cache()
    db = Database(f"sqlite:///{tmp_path / 'wp3.db'}")
    set_database(db)
    reset_runtime()
    reset_services()
    app = create_app()
    with TestClient(app) as client:
        users = {
            "admin": (["platform-admin", "qa-lead", "qa-engineer", "viewer"], ["*"]),
            "alice": (["qa-engineer", "viewer"], ["ws-a"]),
            "bob": (["qa-engineer", "viewer"], ["ws-b"]),
            "lead": (["qa-lead", "qa-engineer", "viewer"], ["ws-a"]),
            "viewer": (["viewer"], ["ws-a"]),
        }
        created: dict[str, dict] = {}
        for name, (roles, workspaces) in users.items():
            rec = auth_store.create_user(db, username=name, password="password-123", roles=roles)
            auth_store.set_user_workspaces(db, rec["user_id"], workspaces)
            created[name] = rec
        rt = get_runtime()
        yield {
            "client": client,
            "db": db,
            "rt": rt,
            "users": created,
            "app": app,
        }
    reset_runtime()
    reset_services()
    reset_settings_cache()
    reset_auth_policy_cache()


def _login(env: dict, username: str) -> tuple[dict, dict[str, str]]:  # type: ignore[type-arg]
    client: TestClient = env["client"]
    r = client.post("/api/v1/auth/login", json={"username": username, "password": "password-123"})
    assert r.status_code == 200, r.text
    body = r.json()
    cookie = client.cookies.get("tg_session")
    assert cookie
    return body, {"tg_session": cookie}


def _append_event(env: dict, workspace: str, run_id: str, key: str) -> None:
    rt = env["rt"]
    rt.events.append(
        event_type="run.started",
        server_workspace_id=workspace,
        producer_id="wp3-producer",
        producer_instance_id="wp3-inst-1",
        idempotency_key=key,
        payload={"run_id": run_id},
        context_refs={"run": run_id},
    )


def test_sse_workspace_revocation_stops_active_stream(env: dict) -> None:
    client: TestClient = env["client"]
    _body, cookies = _login(env, "alice")
    _append_event(env, "ws-a", "run_before_revoke", "sse-before")

    frames: list[dict] = []
    revoked = False
    with client.stream(
        "GET",
        "/events/stream?workspace_id=ws-a&poll_interval=0.05&heartbeat_seconds=0.2"
        "&auth_recheck_seconds=0.1",
        cookies=cookies,
    ) as resp:
        assert resp.status_code == 200
        current: dict = {}
        for line in resp.iter_lines():
            if line.startswith("event: "):
                current["event"] = line[7:]
            elif line.startswith("data: "):
                current["data"] = json.loads(line[6:])
            elif line == "" and current:
                frames.append(current)
                if not revoked:
                    # 连接已建立：撤销 Alice 对 ws-a 的授权（模拟管理员撤权）
                    auth_store.set_user_workspaces(env["db"], env["users"]["alice"]["user_id"], [])
                    revoked = True
                if current.get("event") == "auth.revoked":
                    break
                current = {}
        current = {}

    events = [f["event"] for f in frames]
    assert "auth.revoked" in events, f"必须收到授权失效控制帧，实际 {events}"
    notice = next(f for f in frames if f["event"] == "auth.revoked")
    assert notice["data"]["reason"] == "WORKSPACE_REVOKED"
    assert notice["data"]["workspace_id"] == "ws-a"
    # 连接已结束：撤权后写入的事件不可能再投递
    _append_event(env, "ws-a", "run_after_revoke", "sse-after")
    with client.stream("GET", "/events/stream?workspace_id=ws-a", cookies=cookies) as again:
        assert again.status_code == 403
    # 重新授予 → 重新登录后可正常订阅
    auth_store.set_user_workspaces(env["db"], env["users"]["alice"]["user_id"], ["ws-a"])
    _b, cookies2 = _login(env, "alice")
    with client.stream(
        "GET",
        "/events/stream?workspace_id=ws-a&limit=1&poll_interval=0.05",
        cookies=cookies2,
    ) as resp2:
        assert resp2.status_code == 200
        got = []
        for line in resp2.iter_lines():
            if line.startswith("event: run.started"):
                got.append(line)
                break
        assert got, "重新授权后应能收到业务事件"


def test_sse_session_revocation_logout(env: dict) -> None:
    client: TestClient = env["client"]
    _body, cookies = _login(env, "alice")
    revoked = False
    saw_notice = False
    with client.stream(
        "GET",
        "/events/stream?workspace_id=ws-a&poll_interval=0.05&heartbeat_seconds=0.2"
        "&auth_recheck_seconds=0.1",
        cookies=cookies,
    ) as resp:
        assert resp.status_code == 200
        current: dict = {}
        lines = 0
        for line in resp.iter_lines():
            lines += 1
            if not revoked and lines >= 4:
                # 连接已建立（心跳帧已到达）：撤销会话
                token = client.cookies.get("tg_session") or ""
                auth_store.revoke_session(env["db"], token)
                revoked = True
            if line.startswith("event: "):
                current["event"] = line[7:]
            elif line.startswith("data: "):
                current["data"] = json.loads(line[6:])
            elif line == "" and current:
                if current.get("event") == "auth.revoked":
                    assert current["data"]["reason"] == "SESSION_INVALID"
                    saw_notice = True
                    break
                current = {}
    assert saw_notice


def test_sse_user_disabled_closes_stream(env: dict) -> None:
    client: TestClient = env["client"]
    _body, cookies = _login(env, "alice")
    disabled = False
    saw = False
    with client.stream(
        "GET",
        "/events/stream?workspace_id=ws-a&poll_interval=0.05&heartbeat_seconds=0.2"
        "&auth_recheck_seconds=0.1",
        cookies=cookies,
    ) as resp:
        assert resp.status_code == 200
        current: dict = {}
        lines = 0
        for line in resp.iter_lines():
            lines += 1
            if not disabled and lines >= 4:
                auth_store.set_user_active(env["db"], env["users"]["alice"]["user_id"], False)
                disabled = True
            if line.startswith("event: "):
                current["event"] = line[7:]
            elif line.startswith("data: "):
                current["data"] = json.loads(line[6:])
            elif line == "" and current:
                if current.get("event") == "auth.revoked":
                    assert current["data"]["reason"] == "SESSION_INVALID"
                    saw = True
                    break
                current = {}
    assert saw


@pytest.mark.parametrize("role", ["viewer", "lead"])
def test_global_model_summary_requires_system_observe(env: dict, role: str) -> None:
    client: TestClient = env["client"]
    _b, cookies = _login(env, role)
    r = client.get("/api/v1/system/llm/summary", cookies=cookies)
    assert r.status_code == 403, r.text
    assert "system.observe" in json.dumps(r.json(), ensure_ascii=False)


def test_platform_admin_can_read_global_model_summary(env: dict) -> None:
    client: TestClient = env["client"]
    _b, cookies = _login(env, "admin")
    r = client.get("/api/v1/system/llm/summary", cookies=cookies)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["coverage"]["truncated"] is False
    assert body["coverage"]["limits_used"] is None
    assert "documents_scanned" in body["coverage"]


def test_browser_probe_requires_admin(env: dict) -> None:
    client: TestClient = env["client"]
    _b, cookies = _login(env, "lead")
    r = client.get("/api/v1/system/browser", cookies=cookies)
    assert r.status_code == 403


def test_llm_summary_counts_are_complete_beyond_page(env: dict) -> None:
    """文档数超过单页（500）时统计不得截断：插入 620 条探索文档后应全量计数。"""
    rt = env["rt"]
    for i in range(620):
        rt.docs.put(
            doc_id=f"exp_{i:04d}",
            kind="exploration",
            doc={
                "engine": "rule",
                "llm_stats": {"fallback_calls": 1, "llm_calls": 0},
                "compiled": {"llm_meta": {"called": False, "fallback": True}},
            },
            workspace_id="ws-a",
        )
    client: TestClient = env["client"]
    _b, cookies = _login(env, "admin")
    body = client.get("/api/v1/system/llm/summary", cookies=cookies).json()
    assert body["coverage"]["documents_scanned"]["exploration"] >= 620
    assert body["coverage"]["truncated"] is False
    assert body["decisions"]["rule_explorations"] >= 620
    assert body["decisions"]["fallback_calls"] >= 620


def test_doc_iter_all_paginates_without_loss(env: dict) -> None:
    rt = env["rt"]
    for i in range(11):
        rt.docs.put(doc_id=to_doc_id(i), kind="probe_plan", doc={"i": i}, workspace_id="ws-a")
    seen = [d.doc["i"] for d in rt.docs.iter_all(kind="probe_plan", page_size=4)]
    assert sorted(seen) == list(range(11))
    assert rt.docs.count(kind="probe_plan") == 11


def to_doc_id(i: int) -> str:
    return f"pp_{i:03d}"


def test_queue_stats_are_workspace_scoped(env: dict) -> None:
    """RUN-013 WP6 曾记录 queue/stats 为全局队列；实际已按授权空间过滤，此处实测对照。"""
    rt = env["rt"]
    for ws in ("ws-a", "ws-b"):
        tc_id = f"tcase_q_{ws.replace('-', '_')}"
        rt.catalog.create_testcase(
            {
                "testcase_id": tc_id,
                "version": 1,
                "workspace_id": ws,
                "created_at": "2026-01-01T00:00:00Z",
                "steps": [{"step_id": "s1", "action": "navigate", "value": "/"}],
                "assertions": [
                    {
                        "assertion_id": "a1",
                        "description": "x",
                        "kind": "ui",
                        "required": True,
                        "expected": {},
                    }
                ],
            },
            title="q",
            workspace_id=ws,
            review_status="APPROVED",
        )
        task = rt.tasks.create_task(
            tc_id, f"q-{ws}", testcase_version_id=f"{tc_id}@1", workspace_id=ws
        )
        rt.tasks.create_run(task["task_id"], f"{tc_id}@1")

    client: TestClient = env["client"]
    _b, alice = _login(env, "alice")
    a_body = client.get("/api/v1/queue/stats", cookies=alice).json()
    assert a_body["total"] >= 1
    _bb, admin = _login(env, "admin")
    g_body = client.get("/api/v1/queue/stats", cookies=admin).json()
    assert g_body["total"] > a_body["total"], "管理员看到全局，普通用户只看授权空间"
    assert a_body["scope"]["global"] is False


def test_artifact_without_metadata_is_not_served(env: dict) -> None:
    """文件存在但元数据缺失：不得返回内容（404），也不得绕过 Workspace 校验。"""
    rt = env["rt"]
    artifact_root = rt.artifacts.root
    digest = "a" * 64
    path = artifact_root / digest[:2] / digest
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"orphan-artifact-bytes")
    client: TestClient = env["client"]
    _b, admin = _login(env, "admin")
    r = client.get(f"/api/v1/artifacts/art_{digest[:20]}/download", cookies=admin)
    assert r.status_code == 404


def test_artifact_cross_workspace_denied(env: dict) -> None:
    rt = env["rt"]
    meta = rt.artifacts.put(b"secret-ws-b", workspace_id="ws-b", original_name="b.txt")
    client: TestClient = env["client"]
    _b, alice = _login(env, "alice")
    r = client.get(f"/api/v1/artifacts/{meta['artifact_id']}/download", cookies=alice)
    assert r.status_code == 403
    _bb, bob = _login(env, "bob")
    ok = client.get(f"/api/v1/artifacts/{meta['artifact_id']}/download", cookies=bob)
    assert ok.status_code == 200 and ok.content == b"secret-ws-b"


def test_probe_audit_declares_in_memory_scope(env: dict) -> None:
    client: TestClient = env["client"]
    _b, admin = _login(env, "admin")
    r = client.get("/api/v1/probes/audit", cookies=admin)
    assert r.status_code == 200
    assert r.headers.get("X-TraceGuard-Audit-Persistent") == "false"
    assert r.headers.get("X-TraceGuard-Audit-Scope") == "in-memory-process"
    assert r.headers.get("X-TraceGuard-Audit-Formal-Source") == "probe_execution"
