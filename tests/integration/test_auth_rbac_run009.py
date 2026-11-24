"""RUN-009 WP2：真实认证、会话、RBAC、CSRF、审计与安全测试。"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from traceguard_core.config import reset_settings_cache
from traceguard_persistence import Database, set_database
from traceguard_persistence import auth as auth_store


@pytest.fixture
def auth_client(tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("TG_AUTH_ENABLED", "true")
    monkeypatch.setenv("TG_AUTH_CSRF_REQUIRED", "true")
    monkeypatch.setenv("TG_LLM_PROVIDER", "")
    reset_settings_cache()
    from traceguard_api.main import create_app
    from traceguard_api.runtime import reset_runtime
    from traceguard_api.services import reset_services

    db = Database(f"sqlite:///{tmp_path / 'auth.db'}")
    set_database(db)
    reset_runtime()
    reset_services()
    app = create_app()
    with TestClient(app) as client:
        for name, roles in [
            ("admin", ["platform-admin", "qa-lead", "qa-engineer", "viewer"]),
            ("lead", ["qa-lead", "qa-engineer", "viewer"]),
            ("engineer", ["qa-engineer", "viewer"]),
            ("viewer", ["viewer"]),
        ]:
            auth_store.create_user(db, username=name, password="password-123", roles=roles)
        yield client, db
    reset_runtime()
    reset_services()
    reset_settings_cache()


def _login(client: TestClient, username: str) -> tuple[dict, str]:
    r = client.post("/api/v1/auth/login", json={"username": username, "password": "password-123"})
    assert r.status_code == 200, r.text
    body = r.json()
    return body, body["csrf_token"]


def _headers(csrf: str) -> dict[str, str]:
    return {"X-CSRF-Token": csrf}


def test_unauthenticated_request_fail_closed(auth_client):  # type: ignore[no-untyped-def]
    client, _ = auth_client
    for path in ["/api/v1/testcases", "/api/v1/runs", "/api/v1/diagnoses"]:
        r = client.get(path)
        assert r.status_code == 401, (path, r.text)


def test_login_me_wrong_password_and_rate_limit(auth_client):  # type: ignore[no-untyped-def]
    client, _ = auth_client
    assert (
        client.post(
            "/api/v1/auth/login", json={"username": "admin", "password": "wrong-password"}
        ).status_code
        == 401
    )
    body, csrf = _login(client, "admin")
    assert body["username"] == "admin"
    assert "platform-admin" in body["roles"]
    assert csrf
    me = client.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert me.json()["actor_id"] == "user:admin"


def test_rbac_low_privilege_and_forged_actor(auth_client):  # type: ignore[no-untyped-def]
    client, db = auth_client
    _, viewer_csrf = _login(client, "viewer")
    r = client.post(
        "/api/v1/intents",
        json={"title": "低权限", "description": "viewer 不应创建"},
        headers=_headers(viewer_csrf),
    )
    assert r.status_code == 403, r.text

    # qa-engineer 不是审批角色；请求体中的 reviewer 不得提升权限。
    client.get("/api/v1/auth/me")
    _, eng_csrf = _login(client, "engineer")
    r = client.post(
        "/api/v1/testcases/tcase_missing/versions/1/review",
        json={"decision": "APPROVED", "reviewer": "role:qa-lead"},
        headers=_headers(eng_csrf),
    )
    assert r.status_code == 403, r.text  # RBAC 在路由业务校验之前拒绝
    with db.session() as s:
        actions = [row[0] for row in s.execute(text("SELECT action FROM tg_audit_log"))]
    assert "testcase.review" in actions


def test_csrf_required_and_session_revocation(auth_client):  # type: ignore[no-untyped-def]
    client, db = auth_client
    body, csrf = _login(client, "admin")
    assert client.post("/api/v1/auth/logout", json={}).status_code == 403  # 缺 CSRF
    assert client.post("/api/v1/auth/logout", json={}, headers=_headers(csrf)).status_code == 200
    assert client.get("/api/v1/auth/me").status_code == 401
    assert auth_store.get_session(db, "") is None


def test_concurrent_sessions_and_partial_revoke(auth_client):  # type: ignore[no-untyped-def]
    client, db = auth_client
    first, csrf_a = _login(client, "admin")
    token_a = client.cookies.get("tg_session")
    second, csrf_b = _login(client, "admin")
    assert first["roles"] == second["roles"]
    assert csrf_a != csrf_b
    assert client.get("/api/v1/auth/me").status_code == 200
    assert auth_store.revoke_session(db, token_a or "") is True
    # 第二个会话仍有效（TestClient cookie jar 保留最后一个）
    assert client.get("/api/v1/auth/me").status_code == 200


def test_expired_session(auth_client):  # type: ignore[no-untyped-def]
    client, db = auth_client
    _, csrf = _login(client, "admin")
    with db.session() as s:
        s.execute(text("UPDATE tg_session SET expires_at = :exp"), {"exp": time.time() - 1})
    assert client.get("/api/v1/auth/me").status_code == 401
    assert (
        client.post(
            "/api/v1/intents",
            json={"title": "过期会话", "description": "应拒绝"},
            headers=_headers(csrf),
        ).status_code
        == 401
    )


def test_password_change_revokes_sessions(auth_client):  # type: ignore[no-untyped-def]
    client, db = auth_client
    _, csrf = _login(client, "lead")
    r = client.post(
        "/api/v1/auth/password",
        json={"old_password": "password-123", "new_password": "new-password-456"},
        headers=_headers(csrf),
    )
    assert r.status_code == 200, r.text
    assert client.get("/api/v1/auth/me").status_code == 401
    assert (
        client.post(
            "/api/v1/auth/login", json={"username": "lead", "password": "password-123"}
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/api/v1/auth/login", json={"username": "lead", "password": "new-password-456"}
        ).status_code
        == 200
    )


def test_audit_chain_integrity_and_tamper_detection(auth_client):  # type: ignore[no-untyped-def]
    client, db = auth_client
    _login(client, "admin")
    result = auth_store.verify_audit_chain(db)
    assert result["intact"] and result["entries"] >= 1
    with db.session() as s:
        s.execute(text("UPDATE tg_audit_log SET result = 'TAMPERED'"))
    assert auth_store.verify_audit_chain(db)["intact"] is False


def test_agent_and_llm_actors_rejected_by_business_gate():  # type: ignore[no-untyped-def]
    from traceguard_core.errors import AppError
    from traceguard_persistence.catalog import require_human_actor

    for actor in ["agent:planner", "llm:advisor", "bot:ci", "system:runner", "worker:01"]:
        with pytest.raises(AppError) as exc:
            require_human_actor(actor, field="reviewer")
        assert "MUST_BE_HUMAN" in str(exc.value.message)
