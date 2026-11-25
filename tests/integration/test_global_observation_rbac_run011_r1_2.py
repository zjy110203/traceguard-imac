"""RUN-011-R1.2 WP1：三个全局观测接口的 platform-admin 专属权限收口。

覆盖指令 §2.3：
- 未登录 401 / viewer、qa-engineer、qa-lead 403 / platform-admin 200；
- 拒绝请求写入 DENIED 审计记录；
- 普通 Workspace 内 read 不受影响；
- RBAC 展示矩阵包含 system.observe 且仅 admin 可用。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from traceguard_core.config import reset_settings_cache
from traceguard_persistence import Database, set_database
from traceguard_persistence import auth as auth_store

OBSERVATION_PATHS = (
    "/api/v1/events/conflicts",
    "/api/v1/probes/audit",
    "/api/v1/inbox/stats",
)


@pytest.fixture
def rbac_client(tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("TG_AUTH_ENABLED", "true")
    monkeypatch.setenv("TG_AUTH_CSRF_REQUIRED", "true")
    monkeypatch.setenv("TG_LLM_PROVIDER", "")
    reset_settings_cache()
    from traceguard_api.auth import reset_auth_policy_cache
    from traceguard_api.main import create_app
    from traceguard_api.runtime import reset_runtime
    from traceguard_api.services import reset_services

    reset_auth_policy_cache()
    db = Database(f"sqlite:///{tmp_path / 'observe-r12.db'}")
    set_database(db)
    reset_runtime()
    reset_services()
    app = create_app()
    with TestClient(app) as client:
        users = [
            ("admin", ["platform-admin"], ["*"]),
            ("lead", ["qa-lead"], ["default"]),
            ("engineer", ["qa-engineer"], ["default"]),
            ("viewer", ["viewer"], ["default"]),
        ]
        for name, roles, workspaces in users:
            created = auth_store.create_user(
                db, username=name, password="password-123", roles=roles
            )
            auth_store.set_user_workspaces(db, created["user_id"], workspaces)
        yield client, db
    reset_runtime()
    reset_services()
    reset_settings_cache()
    reset_auth_policy_cache()


def _login(client: TestClient, username: str) -> dict[str, str]:
    r = client.post(
        "/api/v1/auth/login", json={"username": username, "password": "password-123"}
    )
    assert r.status_code == 200, r.text
    return {"X-CSRF-Token": r.json()["csrf_token"]}


@pytest.mark.parametrize("path", OBSERVATION_PATHS)
def test_global_observation_access_matrix(rbac_client, path: str):  # type: ignore[no-untyped-def]
    client, _ = rbac_client
    assert client.get(path).status_code == 401, path
    for username in ("viewer", "engineer", "lead"):
        headers = _login(client, username)
        r = client.get(path, headers=headers)
        assert r.status_code == 403, (username, path, r.text)
        assert r.json()["detail"]["code"] == "PERMISSION_DENIED"
        assert "system.observe" in r.json()["detail"]["message"]
    headers = _login(client, "admin")
    assert client.get(path, headers=headers).status_code == 200, path


@pytest.mark.parametrize("path", OBSERVATION_PATHS)
def test_global_observation_denials_are_audited(rbac_client, path: str):  # type: ignore[no-untyped-def]
    client, db = rbac_client
    headers = _login(client, "viewer")
    assert client.get(path, headers=headers).status_code == 403
    with db.session() as s:
        rows = s.execute(
            text(
                "SELECT action, result, resource_id FROM tg_audit_log "
                "WHERE action = 'system.observe' AND result = 'DENIED'"
            )
        ).all()
    assert rows and any(path == str(row[2]) for row in rows)


def test_action_mapping_and_permission_matrix(rbac_client):  # type: ignore[no-untyped-def]
    client, _ = rbac_client
    from traceguard_api.auth import action_for_request
    from traceguard_persistence.auth import PERMISSION_MATRIX, has_permission

    for path in OBSERVATION_PATHS:
        assert action_for_request("GET", path) == "system.observe"
        assert action_for_request("GET", path + "/") == "system.observe"
    # 普通业务 GET 仍映射到 read；不能因新增全局观测权限而破坏 Workspace 内读取。
    assert action_for_request("GET", "/api/v1/intents?workspace_id=default") == "read"
    assert action_for_request("GET", "/api/v1/runs") == "read"
    assert has_permission(["platform-admin"], "system.observe") is True
    for role in ("viewer", "qa-engineer", "qa-lead"):
        assert has_permission([role], "system.observe") is False

    admin_headers = _login(client, "admin")
    matrix = client.get("/api/v1/auth/permissions", headers=admin_headers).json()["matrix"]
    assert matrix["platform-admin"] == sorted(matrix["platform-admin"])
    assert "system.observe" in matrix["platform-admin"]
    for role in ("viewer", "qa-engineer", "qa-lead"):
        assert "system.observe" not in matrix[role]
    assert PERMISSION_MATRIX["platform-admin"] == matrix["platform-admin"]


def test_workspace_read_permissions_unchanged(rbac_client):  # type: ignore[no-untyped-def]
    client, _ = rbac_client
    for username in ("viewer", "engineer", "lead"):
        headers = _login(client, username)
        r = client.get("/api/v1/intents?workspace_id=default", headers=headers)
        assert r.status_code == 200, (username, r.text)
        forbidden = client.get("/api/v1/intents?workspace_id=other", headers=headers)
        assert forbidden.status_code == 403
        assert forbidden.json()["detail"]["code"] == "WORKSPACE_FORBIDDEN"
