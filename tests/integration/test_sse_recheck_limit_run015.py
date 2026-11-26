"""RUN-015 WP3：SSE 授权重核验间隔的服务端上限与异常参数处理。

客户端传入超大间隔、NaN、Infinity 都不得延长撤权生效窗口。
参数收敛用单元测试逐值验证；端到端用缩小后的服务端上限（0.4s）做快速验证，
并断言生产上限常量本身为 10s。
"""

from __future__ import annotations

import json
import math

import pytest
from fastapi.testclient import TestClient
from traceguard_core.config import reset_settings_cache
from traceguard_persistence import Database, set_database
from traceguard_persistence import auth as auth_store


def test_auth_recheck_clamp_values(monkeypatch: pytest.MonkeyPatch) -> None:
    import traceguard_api.main as api_main

    assert api_main.SSE_AUTH_RECHECK_DEFAULT_SECONDS == 2.0
    assert api_main.SSE_AUTH_RECHECK_MIN_SECONDS == 0.1
    assert api_main.SSE_AUTH_RECHECK_MAX_SECONDS == 10.0

    assert api_main.effective_auth_recheck_seconds(2.0) == 2.0  # 默认值
    assert api_main.effective_auth_recheck_seconds(0.01) == 0.1  # 最小值
    assert api_main.effective_auth_recheck_seconds(1.5) == 1.5  # 正常值
    assert api_main.effective_auth_recheck_seconds(1e9) == 10.0  # 超大值
    for bad in (float("inf"), float("-inf"), float("nan"), "abc", None):
        assert api_main.effective_auth_recheck_seconds(bad) == 2.0  # type: ignore[arg-type]

    # 服务端上限可收紧；这是端到端测试使用的机制
    monkeypatch.setattr(api_main, "SSE_AUTH_RECHECK_MAX_SECONDS", 0.4)
    assert api_main.effective_auth_recheck_seconds(1e9) == 0.4


@pytest.fixture()
def env(tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("TG_AUTH_ENABLED", "true")
    monkeypatch.setenv("TG_AUTH_CSRF_REQUIRED", "true")
    monkeypatch.setenv("TG_LLM_PROVIDER", "")
    monkeypatch.setenv("TG_ARTIFACT_DIR", str(tmp_path / "artifacts"))
    reset_settings_cache()
    import traceguard_api.main as api_main
    from traceguard_api.auth import reset_auth_policy_cache
    from traceguard_api.main import create_app
    from traceguard_api.runtime import get_runtime, reset_runtime
    from traceguard_api.services import reset_services

    # 保持测试快速：服务端强制上限临时收紧；生产常量仍由单元测试断言
    monkeypatch.setattr(api_main, "SSE_AUTH_RECHECK_MAX_SECONDS", 0.4)
    # NaN/Infinity 回落到"服务端默认值"；测试里默认值也收紧以缩短等待
    monkeypatch.setattr(api_main, "SSE_AUTH_RECHECK_DEFAULT_SECONDS", 0.4)
    reset_auth_policy_cache()
    db = Database(f"sqlite:///{tmp_path / 'sse15.db'}")
    set_database(db)
    reset_runtime()
    reset_services()
    app = create_app()
    with TestClient(app) as client:
        alice = auth_store.create_user(
            db,
            username="alice15",
            password="password-123",
            roles=["qa-engineer", "viewer"],
        )
        auth_store.set_user_workspaces(db, alice["user_id"], ["ws-a"])
        yield {"client": client, "db": db, "rt": get_runtime(), "alice": alice}
    reset_runtime()
    reset_services()
    reset_settings_cache()
    reset_auth_policy_cache()


def _login(env: dict) -> dict[str, str]:  # type: ignore[type-arg]
    client: TestClient = env["client"]
    r = client.post("/api/v1/auth/login", json={"username": "alice15", "password": "password-123"})
    assert r.status_code == 200, r.text
    return {"tg_session": client.cookies.get("tg_session") or ""}


def _read_until_revoked(client: TestClient, url: str, cookies: dict[str, str], action) -> dict:
    """建立连接后执行撤权动作，读取 auth.revoked 控制帧。"""
    with client.stream("GET", url, cookies=cookies) as resp:
        assert resp.status_code == 200, resp.text
        assert math.isclose(
            float(resp.headers.get("X-TraceGuard-Auth-Recheck-Seconds", "0")), 0.4
        ), resp.headers
        current: dict = {}
        done = False
        for line in resp.iter_lines():
            if line.startswith("event: "):
                current["event"] = line[7:]
            elif line.startswith("data: "):
                current["data"] = json.loads(line[6:])
            elif line == "":
                # 首个帧边界即视为连接已建立（前面的心跳可能只是 ": keepalive" 注释行）
                if not done:
                    action()
                    done = True
                if current.get("event") == "auth.revoked":
                    return current
                current = {}
    return {}


@pytest.mark.parametrize(
    "query,bad_value",
    [
        ("auth_recheck_seconds=1e9", "huge"),
        ("auth_recheck_seconds=nan", "nan"),
        ("auth_recheck_seconds=inf", "inf"),
    ],
)
def test_malicious_interval_still_revokes_workspace(
    env: dict,
    query: str,
    bad_value: str,  # type: ignore[type-arg]
) -> None:
    client: TestClient = env["client"]
    cookies = _login(env)
    url = f"/events/stream?workspace_id=ws-a&poll_interval=0.05&heartbeat_seconds=0.2&{query}"

    def revoke() -> None:
        auth_store.set_user_workspaces(env["db"], env["alice"]["user_id"], [])

    frame = _read_until_revoked(client, url, cookies, revoke)
    assert frame.get("event") == "auth.revoked", f"{bad_value}: 撤权未生效"
    assert frame["data"]["reason"] == "WORKSPACE_REVOKED"
    # 撤权后重新授权并重新登录可正常订阅
    auth_store.set_user_workspaces(env["db"], env["alice"]["user_id"], ["ws-a"])
    cookies2 = _login(env)
    with client.stream(
        "GET", "/events/stream?workspace_id=ws-a&limit=1&poll_interval=0.05", cookies=cookies2
    ) as resp:
        assert resp.status_code == 200


@pytest.mark.parametrize("query", ["auth_recheck_seconds=1e9", "auth_recheck_seconds=nan"])
def test_malicious_interval_still_revokes_session(env: dict, query: str) -> None:  # type: ignore[type-arg]
    client: TestClient = env["client"]
    cookies = _login(env)

    def revoke() -> None:
        auth_store.revoke_session(env["db"], cookies["tg_session"])

    frame = _read_until_revoked(
        client, f"/events/stream?workspace_id=ws-a&poll_interval=0.05&{query}", cookies, revoke
    )
    assert frame.get("data", {}).get("reason") == "SESSION_INVALID"


def test_malicious_interval_still_closes_disabled_user(env: dict) -> None:  # type: ignore[type-arg]
    client: TestClient = env["client"]
    cookies = _login(env)

    def disable() -> None:
        auth_store.set_user_active(env["db"], env["alice"]["user_id"], False)

    frame = _read_until_revoked(
        client,
        "/events/stream?workspace_id=ws-a&poll_interval=0.05&auth_recheck_seconds=inf",
        cookies,
        disable,
    )
    assert frame.get("data", {}).get("reason") == "SESSION_INVALID"
