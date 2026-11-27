"""RUN-019 WP1：Workspace 管理与用户角色变更的服务端可信主体、权限与审计验收。"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from traceguard_core.config import reset_settings_cache
from traceguard_persistence import Database, set_database
from traceguard_persistence import auth as auth_store


@pytest.fixture
def admin_client(tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("TG_AUTH_ENABLED", "true")
    monkeypatch.setenv("TG_AUTH_CSRF_REQUIRED", "true")
    monkeypatch.setenv("TG_LLM_PROVIDER", "")
    reset_settings_cache()
    from traceguard_api.auth import reset_auth_policy_cache
    from traceguard_api.main import create_app
    from traceguard_api.runtime import reset_runtime
    from traceguard_api.services import reset_services

    reset_auth_policy_cache()
    db = Database(f"sqlite:///{tmp_path / 'run019_admin.db'}")
    set_database(db)
    reset_runtime()
    reset_services()
    app = create_app()
    users = [
        ("admin", ["platform-admin", "qa-lead", "qa-engineer", "viewer"], ["*"]),
        ("lead", ["qa-lead", "qa-engineer", "viewer"], ["default"]),
        ("engineer", ["qa-engineer", "viewer"], ["default"]),
    ]
    for name, roles, workspaces in users:
        created = auth_store.create_user(db, username=name, password="password-123", roles=roles)
        auth_store.set_user_workspaces(db, created["user_id"], workspaces)
    with TestClient(app) as client:
        yield client, db, app
    reset_runtime()
    reset_services()
    reset_settings_cache()
    reset_auth_policy_cache()


def _login(client: TestClient, name: str) -> tuple[dict[str, Any], str]:
    r = client.post("/api/v1/auth/login", json={"username": name, "password": "password-123"})
    assert r.status_code == 200, r.text
    body = r.json()
    return body, body["csrf_token"]


def _csrf(token: str) -> dict[str, str]:
    return {"X-CSRF-Token": token}


def test_workspace_create_uses_trusted_principal_and_audits(
    admin_client,  # type: ignore[no-untyped-def]
) -> None:
    client, db, _ = admin_client
    _, admin_csrf = _login(client, "admin")
    r = client.post(
        "/api/v1/workspaces",
        json={"name": "Team A", "description": "desc", "created_by": "agent:forged"},
        headers=_csrf(admin_csrf),
    )
    assert r.status_code == 201, r.text
    wid = r.json()["workspace_id"]
    assert r.json()["created_by"] == "user:admin", r.json()
    assert client.get(f"/api/v1/workspaces/{wid}").json()["created_by"] == "user:admin"
    # 审计链由 verify 端点做权威校验（伪造主体被忽略且写审计）
    verify = client.get("/api/v1/auth/audit/verify")
    assert verify.status_code == 200, verify.text
    assert verify.json()["intact"] is True


def test_workspace_edit_protection_and_member_view(
    admin_client,  # type: ignore[no-untyped-def]
) -> None:
    client, _, _ = admin_client
    _, admin_csrf = _login(client, "admin")
    created = client.post(
        "/api/v1/workspaces", json={"name": "Team B"}, headers=_csrf(admin_csrf)
    ).json()
    wid = created["workspace_id"]
    patched = client.patch(
        f"/api/v1/workspaces/{wid}",
        json={"name": "Team B Renamed", "description": "new"},
        headers=_csrf(admin_csrf),
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["name"] == "Team B Renamed"

    protected = client.patch(
        "/api/v1/workspaces/default", json={"name": "hacked"}, headers=_csrf(admin_csrf)
    )
    assert protected.status_code == 403, protected.text
    assert "DEFAULT_WORKSPACE_PROTECTED" in protected.text

    members = client.get("/api/v1/auth/workspaces/default/members")
    assert members.status_code == 200, members.text
    names = {m["username"]: m["scope_mode"] for m in members.json()["members"]}
    assert names["admin"] == "all"
    assert names["lead"] == "explicit"
    assert "engineer" in names


def test_non_admin_cannot_manage_workspaces(
    admin_client,  # type: ignore[no-untyped-def]
) -> None:
    client, _, app = admin_client
    engineer = TestClient(app)
    _, csrf = _login(engineer, "engineer")
    r = engineer.post(
        "/api/v1/workspaces",
        json={"name": "evil", "created_by": "role:platform-admin"},
        headers=_csrf(csrf),
    )
    assert r.status_code == 403, r.text
    # 角色伪造不生效：会话仍是 engineer
    assert engineer.get("/api/v1/auth/me").json()["roles"] == ["qa-engineer", "viewer"]


def test_role_change_reflects_in_existing_session(
    admin_client,  # type: ignore[no-untyped-def]
) -> None:
    client, db, app = admin_client
    lead = TestClient(app)
    lead_info, lead_csrf = _login(lead, "lead")
    assert (
        lead.post(
            "/api/v1/intents",
            json={"title": "t", "description": "d"},
            headers=_csrf(lead_csrf),
        ).status_code
        == 201
    )

    _, admin_csrf = _login(client, "admin")
    lead_id = auth_store.get_user_by_username(db, "lead")["user_id"]
    changed = client.post(
        f"/api/v1/auth/users/{lead_id}/roles",
        json={"roles": ["viewer"]},
        headers=_csrf(admin_csrf),
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["roles"] == ["viewer"]

    # 不撤销会话：同一 token 的下一次请求应立即反映新权限
    restricted = lead.post(
        "/api/v1/intents",
        json={"title": "t2", "description": "d2"},
        headers=_csrf(lead_csrf),
    )
    assert restricted.status_code == 403, restricted.text
    # 会话仍有效（没有被不必要地撤销），但角色来自数据库实时读取
    assert lead.get("/api/v1/auth/me").json()["username"] == lead_info["username"]


def test_last_admin_cannot_be_deactivated_or_demoted(
    admin_client,  # type: ignore[no-untyped-def]
) -> None:
    client, db, _ = admin_client
    _, admin_csrf = _login(client, "admin")
    admin_id = auth_store.get_user_by_username(db, "admin")["user_id"]
    deactivate = client.post(
        f"/api/v1/auth/users/{admin_id}/active?active=false", headers=_csrf(admin_csrf)
    )
    assert deactivate.status_code == 409, deactivate.text
    demote = client.post(
        f"/api/v1/auth/users/{admin_id}/roles",
        json={"roles": ["viewer"]},
        headers=_csrf(admin_csrf),
    )
    assert demote.status_code == 409, demote.text

    # 增加第二个管理员后，降级第一个管理员应成功
    second = auth_store.create_user(
        db, username="admin2", password="password-123", roles=["platform-admin"]
    )
    demote = client.post(
        f"/api/v1/auth/users/{admin_id}/roles",
        json={"roles": ["viewer"]},
        headers=_csrf(admin_csrf),
    )
    assert demote.status_code == 200, demote.text
    assert "platform-admin" not in auth_store.get_user(db, admin_id)["roles"]
    assert "platform-admin" in auth_store.get_user(db, second["user_id"])["roles"]
