"""RUN-016 WP4：`/system/config` 权限边界与敏感字段脱敏。

验收点：
1. platform-admin 得到完整视图（database / artifact_dir / worker.id）；
2. qa-engineer / qa-lead / viewer 只得到非敏感子集，敏感字段为 null（脱敏而非隐藏）；
3. 未认证一律 401（认证开启时 fail-closed）；
4. 运行期不存在配置写入口（PUT/PATCH/POST 不可用），因此不存在"普通用户改全局配置"路径；
5. 认证关闭的本地模式保持完整视图（不影响既有联调）。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from traceguard_core.config import reset_settings_cache
from traceguard_persistence import Database, set_database
from traceguard_persistence import auth as auth_store

SENSITIVE = ("database", "artifact_dir")


@pytest.fixture()
def auth_client(tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("TG_AUTH_ENABLED", "true")
    monkeypatch.setenv("TG_AUTH_CSRF_REQUIRED", "true")
    monkeypatch.setenv("TG_LLM_PROVIDER", "")
    monkeypatch.setenv("TG_ARTIFACT_DIR", str(tmp_path / "artifacts"))
    reset_settings_cache()
    from traceguard_api.auth import reset_auth_policy_cache
    from traceguard_api.main import create_app
    from traceguard_api.runtime import reset_runtime
    from traceguard_api.services import reset_services

    reset_auth_policy_cache()
    db = Database(f"sqlite:///{tmp_path / 'cfg16.db'}")
    set_database(db)
    reset_runtime()
    reset_services()
    with TestClient(create_app()) as client:
        for name, roles in (
            ("admin", ["platform-admin"]),
            ("engineer", ["qa-engineer", "viewer"]),
            ("lead", ["qa-lead", "viewer"]),
            ("viewer", ["viewer"]),
        ):
            created = auth_store.create_user(
                db, username=name, password="password-123", roles=roles
            )
            auth_store.set_user_workspaces(db, created["user_id"], ["default"])
        yield client
    reset_runtime()
    reset_services()
    reset_settings_cache()
    reset_auth_policy_cache()


def _login(client: TestClient, username: str) -> dict[str, str]:
    r = client.post("/api/v1/auth/login", json={"username": username, "password": "password-123"})
    assert r.status_code == 200, r.text
    return {"tg_session": client.cookies.get("tg_session") or ""}


def _login_with_csrf(client: TestClient, username: str) -> tuple[dict[str, str], str]:
    r = client.post("/api/v1/auth/login", json={"username": username, "password": "password-123"})
    assert r.status_code == 200, r.text
    return {"tg_session": client.cookies.get("tg_session") or ""}, r.json()["csrf_token"]


def test_anonymous_cannot_read_config(auth_client: TestClient) -> None:
    r = auth_client.get("/api/v1/system/config")
    assert r.status_code == 401, r.text


def test_admin_sees_full_config(auth_client: TestClient) -> None:
    cookies = _login(auth_client, "admin")
    body = auth_client.get("/api/v1/system/config", cookies=cookies).json()
    assert body["access_level"] == "admin"
    assert body["database"]
    assert body["artifact_dir"]
    assert body["worker"]["id"]
    assert body["worker"]["inline"] in (True, False)


@pytest.mark.parametrize("username", ["engineer", "lead", "viewer"])
def test_non_admin_config_is_redacted(auth_client: TestClient, username: str) -> None:
    cookies = _login(auth_client, username)
    body = auth_client.get("/api/v1/system/config", cookies=cookies).json()
    assert body["access_level"] == "restricted"
    for field in SENSITIVE:
        assert field in body, f"{field} 应保持键存在（脱敏而非隐藏）"
        assert body[field] is None, f"{field} 不应暴露给 {username}"
    assert body["worker"]["id"] is None
    # 非敏感字段仍需可用（前端展示/联调依赖）
    assert body["teamflow_base_url"]
    assert "browser" in body
    assert body["worker"]["inline"] in (True, False)


@pytest.mark.parametrize("method", ["put", "patch", "post", "delete"])
def test_no_runtime_config_write_endpoint(auth_client: TestClient, method: str) -> None:
    """运行期没有任何配置写入口：非管理员与管理员都不存在"改全局配置"的 API。"""
    cookies, csrf = _login_with_csrf(auth_client, "admin")
    # 只针对配置资源本身（/system/seed 是数据播种，不是配置）
    kwargs: dict = {"cookies": cookies, "headers": {"X-CSRF-Token": csrf}}
    if method != "delete":
        kwargs["json"] = {"anything": 1}
    r = getattr(auth_client, method)("/api/v1/system/config", **kwargs)
    assert r.status_code in (404, 405), r.text


def test_config_full_view_when_auth_disabled(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """认证关闭的本地模式：保持完整视图，不破坏既有联调。"""
    monkeypatch.setenv("TG_AUTH_ENABLED", "false")
    monkeypatch.setenv("TG_LLM_PROVIDER", "")
    monkeypatch.setenv("TG_ARTIFACT_DIR", str(tmp_path / "artifacts-dev"))
    reset_settings_cache()
    from traceguard_api.main import create_app
    from traceguard_api.runtime import reset_runtime
    from traceguard_api.services import reset_services

    db = Database(f"sqlite:///{tmp_path / 'cfg16-dev.db'}")
    set_database(db)
    reset_runtime()
    reset_services()
    with TestClient(create_app()) as client:
        body = client.get("/api/v1/system/config").json()
    reset_runtime()
    reset_services()
    reset_settings_cache()
    assert body["access_level"] == "admin"
    assert body["artifact_dir"]
    assert body["worker"]["id"]
