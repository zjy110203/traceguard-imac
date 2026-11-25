"""RUN-012 WP3：全局统计 `/system/metrics` 的 Workspace 过滤与真实聚合计数。

覆盖指令 §2.3：
- 显式 workspace_id 非授权 → 403 WORKSPACE_FORBIDDEN；授权 → 200 且只含该空间计数；
- 不传 workspace_id：普通用户只统计授权空间并集，platform-admin 才是全局汇总；
- 计数为 SQL COUNT(*) / GROUP BY 全量聚合，不得用 limit=500 的列表长度推断；
- response 声明 scope / counting 口径，杜绝"截断后计数"再次出现。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from traceguard_core.config import reset_settings_cache
from traceguard_persistence import Database, set_database
from traceguard_persistence import auth as auth_store

WS_A = "ws-metrics-a"
WS_B = "ws-metrics-b"


@pytest.fixture
def metrics_client(tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("TG_AUTH_ENABLED", "true")
    monkeypatch.setenv("TG_AUTH_CSRF_REQUIRED", "true")
    monkeypatch.setenv("TG_LLM_PROVIDER", "")
    reset_settings_cache()
    from traceguard_api.auth import reset_auth_policy_cache
    from traceguard_api.main import create_app
    from traceguard_api.runtime import reset_runtime
    from traceguard_api.services import reset_services

    reset_auth_policy_cache()
    db = Database(f"sqlite:///{tmp_path / 'metrics-r12.db'}")
    set_database(db)
    reset_runtime()
    reset_services()
    app = create_app()
    with TestClient(app) as client:
        users = [
            ("m-admin", ["platform-admin"], ["*"]),
            ("m-a", ["qa-engineer"], [WS_A]),
            ("m-b", ["qa-engineer"], [WS_B]),
            ("m-both", ["qa-lead"], [WS_A, WS_B]),
        ]
        for name, roles, workspaces in users:
            created = auth_store.create_user(
                db, username=name, password="password-123", roles=roles
            )
            auth_store.set_user_workspaces(db, created["user_id"], workspaces)

        admin_headers = _login(client, "m-admin")
        seed(client, admin_headers, WS_A, intents=2, testcases=3)
        seed(client, admin_headers, WS_B, intents=5, testcases=1)
        # 清除应用启动种子（default 空间），避免污染全局计数断言
        with db.session() as s:
            s.execute(text("DELETE FROM tg_intent WHERE workspace_id = 'default'"))
            s.execute(text("DELETE FROM tg_testcase WHERE workspace_id = 'default'"))
            # 造 tg_run 行：WS_A 3 条（2 QUEUED + 1 RUNNING）、WS_B 2 条 COMPLETED
            seed_runs(s, WS_A, [("QUEUED", 2), ("RUNNING", 1)])
            seed_runs(s, WS_B, [("COMPLETED", 2)])
        yield client, db
    reset_runtime()
    reset_services()
    reset_settings_cache()
    reset_auth_policy_cache()


def _login(client: TestClient, username: str) -> dict[str, str]:
    r = client.post("/api/v1/auth/login", json={"username": username, "password": "password-123"})
    assert r.status_code == 200, r.text
    return {"X-CSRF-Token": r.json()["csrf_token"]}


def seed_runs(session, workspace_id: str, plan: list[tuple[str, int]]) -> None:
    """直接写 tg_run 行（队列统计只读聚合，无需完整 Task 血缘）。"""
    from traceguard_persistence.models import RunRow

    n = 0
    for status, count in plan:
        for _ in range(count):
            n += 1
            session.add(
                RunRow(
                    run_id=f"run_{workspace_id}_{n}",
                    task_id=f"task_{workspace_id}_{n}",
                    workspace_id=workspace_id,
                    attempt=1,
                    testcase_version_id=f"tcase_{workspace_id}@1",
                    execution_status=status,
                    test_verdict="NOT_EVALUATED",
                    fencing_token=0,
                )
            )


def seed(
    client: TestClient, headers: dict[str, str], workspace_id: str, *, intents: int, testcases: int
) -> None:
    """在指定 Workspace 造真实 Intent / TestCase 行（走 HTTP，保证全链路一致）。"""
    for i in range(intents):
        r = client.post(
            "/api/v1/intents",
            json={
                "title": f"{workspace_id} 需求 {i}",
                "description": f"{workspace_id} 需求描述 {i}",
                "workspace_id": workspace_id,
            },
            headers=headers,
        )
        assert r.status_code == 201, r.text
    for i in range(testcases):
        r = client.post(
            "/api/v1/testcases",
            json={
                "title": f"{workspace_id} 用例 {i}",
                "workspace_id": workspace_id,
                "steps": [
                    {
                        "step_id": "s1",
                        "action": "navigate",
                        "target": {"strategy": "css", "value": "body"},
                    }
                ],
                "assertions": [
                    {
                        "assertion_id": "a1",
                        "description": "页面可见",
                        "kind": "ui",
                        "required": True,
                    }
                ],
            },
            headers=headers,
        )
        assert r.status_code in (200, 201), r.text


def test_metrics_requires_auth(metrics_client) -> None:  # type: ignore[no-untyped-def]
    client, _ = metrics_client
    # 显式清空既有会话 Cookie：模拟真正未登录的匿名请求
    client.cookies.clear()
    assert client.get("/api/v1/system/metrics").status_code == 401


def test_metrics_forbidden_for_unauthorized_explicit_workspace(metrics_client) -> None:  # type: ignore[no-untyped-def]
    client, _ = metrics_client
    headers = _login(client, "m-a")
    r = client.get(f"/api/v1/system/metrics?workspace_id={WS_B}", headers=headers)
    assert r.status_code == 403, r.text
    assert r.json()["detail"]["code"] == "WORKSPACE_FORBIDDEN"


def test_metrics_explicit_workspace_counts_only_that_workspace(metrics_client) -> None:  # type: ignore[no-untyped-def]
    client, _ = metrics_client
    headers = _login(client, "m-a")
    r = client.get(f"/api/v1/system/metrics?workspace_id={WS_A}", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["intents"] == 2
    assert body["testcases"] == 3
    assert body["scope"]["mode"] == "workspace"
    assert body["scope"]["workspace_id"] == WS_A
    assert body["scope"]["global"] is False


def test_metrics_authorized_union_for_normal_user(metrics_client) -> None:  # type: ignore[no-untyped-def]
    client, _ = metrics_client
    headers = _login(client, "m-both")
    r = client.get("/api/v1/system/metrics", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    # 只统计授权的两个空间并集（2+5=7 intents，3+1=4 testcases）
    assert body["intents"] == 7
    assert body["testcases"] == 4
    assert body["scope"]["mode"] == "authorized_workspaces"
    assert body["scope"]["global"] is False
    assert sorted(body["scope"]["authorized_workspaces"]) == sorted([WS_A, WS_B])


def test_metrics_single_scope_user_sees_only_own_workspace(metrics_client) -> None:  # type: ignore[no-untyped-def]
    client, _ = metrics_client
    headers = _login(client, "m-a")
    r = client.get("/api/v1/system/metrics", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["intents"] == 2
    assert body["testcases"] == 3
    assert body["scope"]["authorized_workspaces"] == [WS_A]


def test_metrics_global_only_for_platform_admin(metrics_client) -> None:  # type: ignore[no-untyped-def]
    client, _ = metrics_client
    headers = _login(client, "m-admin")
    r = client.get("/api/v1/system/metrics", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    # 全局 = 所有空间合计（2+5=7 intents，3+1=4 testcases）
    assert body["intents"] == 7
    assert body["testcases"] == 4
    assert body["scope"]["mode"] == "global"
    assert body["scope"]["global"] is True
    assert body["scope"]["authorized_workspaces"] is None


def test_metrics_counting_metadata_declares_sql_aggregation(metrics_client) -> None:  # type: ignore[no-untyped-def]
    client, _ = metrics_client
    headers = _login(client, "m-admin")
    body = client.get("/api/v1/system/metrics", headers=headers).json()
    counting = body["counting"]
    assert counting["list_limit"] is None
    assert "COUNT" in counting["source"].upper()
    assert counting["time_range"] == "ALL_TIME"
    # verdicts 必须是分组计数字典（可为空），不是截断列表长度
    assert isinstance(body["verdicts"], dict)


def test_metrics_exceeds_legacy_500_truncation_threshold(metrics_client) -> None:  # type: ignore[no-untyped-def]
    """造 520 条 Intent，验证计数不受历史 limit=500 截断影响。"""
    client, _ = metrics_client
    headers = _login(client, "m-admin")
    for i in range(520):
        r = client.post(
            "/api/v1/intents",
            json={
                "title": f"bulk 需求 {i}",
                "description": "bulk",
                "workspace_id": WS_A,
            },
            headers=headers,
        )
        assert r.status_code == 201, r.text
    body = client.get(f"/api/v1/system/metrics?workspace_id={WS_A}", headers=headers).json()
    assert body["intents"] == 522  # 原 2 条 + 新 520 条，>500 证明非截断计数


def test_queue_stats_workspace_scoped_aggregation(metrics_client) -> None:  # type: ignore[no-untyped-def]
    """`/queue/stats` 必须按调用方授权空间做 SQL 分组聚合，而非截断列表长度。"""
    client, _ = metrics_client
    headers = _login(client, "m-a")
    body = client.get("/api/v1/queue/stats", headers=headers).json()
    # WS_A：2 QUEUED + 1 RUNNING = 3
    assert body["total"] == 3
    assert body["by_status"] == {"QUEUED": 2, "RUNNING": 1}
    assert body["counting"]["list_limit"] is None


def test_queue_stats_global_for_admin(metrics_client) -> None:  # type: ignore[no-untyped-def]
    client, _ = metrics_client
    headers = _login(client, "m-admin")
    body = client.get("/api/v1/queue/stats", headers=headers).json()
    # 全局：WS_A 3 条 + WS_B 2 条 = 5
    assert body["total"] == 5
    assert body["by_status"] == {"QUEUED": 2, "RUNNING": 1, "COMPLETED": 2}


def test_queue_stats_multi_workspace_union(metrics_client) -> None:  # type: ignore[no-untyped-def]
    client, _ = metrics_client
    headers = _login(client, "m-both")
    body = client.get("/api/v1/queue/stats", headers=headers).json()
    assert body["total"] == 5
