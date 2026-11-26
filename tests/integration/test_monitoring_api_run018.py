"""RUN-018 WP8：运行监控与告警 API 集成测试。

覆盖：
- GET /api/v1/monitoring/metrics 六类指标组齐全 + 阈值声明 provisional；
- 请求指标中间件真实计数（requests_total / 响应时间分位）；
- 告警列表 + 生命周期（OPEN → ACKNOWLEDGED → RESOLVED，原因必填，非法迁移 409）；
- Workspace 作用域（普通用户只看授权范围，响应声明 scope）；
- RBAC：告警处理（POST）仅 platform-admin（fail-closed 映射 system.configure）。
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient
from traceguard_core.config import reset_settings_cache
from traceguard_persistence import Database, set_database
from traceguard_persistence import auth as auth_store
from traceguard_persistence.models import WorkerHeartbeatRow
from traceguard_persistence.monitoring import reset_process_metrics, upsert_worker_heartbeat

WS_A = "ws-mon-a"


@pytest.fixture
def monitoring_client(tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("TG_AUTH_ENABLED", "false")
    monkeypatch.setenv("TG_LLM_PROVIDER", "")
    reset_settings_cache()
    reset_process_metrics()
    from traceguard_api.auth import reset_auth_policy_cache
    from traceguard_api.main import create_app
    from traceguard_api.runtime import reset_runtime
    from traceguard_api.services import reset_services

    reset_auth_policy_cache()
    db = Database(f"sqlite:///{tmp_path / 'monitoring-wp8.db'}")
    set_database(db)
    reset_runtime()
    reset_services()
    app = create_app()
    with TestClient(app) as client:
        yield client, db
    reset_runtime()
    reset_services()
    reset_settings_cache()
    reset_auth_policy_cache()
    reset_process_metrics()


@pytest.fixture
def monitoring_auth_client(tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("TG_AUTH_ENABLED", "true")
    monkeypatch.setenv("TG_AUTH_CSRF_REQUIRED", "true")
    monkeypatch.setenv("TG_LLM_PROVIDER", "")
    reset_settings_cache()
    reset_process_metrics()
    from traceguard_api.auth import reset_auth_policy_cache
    from traceguard_api.main import create_app
    from traceguard_api.runtime import reset_runtime
    from traceguard_api.services import reset_services

    reset_auth_policy_cache()
    db = Database(f"sqlite:///{tmp_path / 'monitoring-auth.db'}")
    set_database(db)
    reset_runtime()
    reset_services()
    app = create_app()
    with TestClient(app) as client:
        created = auth_store.create_user(
            db, username="mon-admin", password="password-123", roles=["platform-admin"]
        )
        auth_store.set_user_workspaces(db, created["user_id"], ["*"])
        created2 = auth_store.create_user(
            db, username="mon-qa", password="password-123", roles=["qa-engineer"]
        )
        auth_store.set_user_workspaces(db, created2["user_id"], [WS_A])
        yield client, db
    reset_runtime()
    reset_services()
    reset_settings_cache()
    reset_auth_policy_cache()
    reset_process_metrics()


def _login(client: TestClient, username: str) -> dict[str, str]:
    r = client.post("/api/v1/auth/login", json={"username": username, "password": "password-123"})
    assert r.status_code == 200, r.text
    return {"X-CSRF-Token": r.json()["csrf_token"]}


def _stale_worker(db: Database, worker_id: str = "w-stale", age_s: float = 3600.0) -> None:
    upsert_worker_heartbeat(db, worker_id=worker_id, mode="standalone", status="IDLE")
    with db.session() as s:
        row = s.get(WorkerHeartbeatRow, worker_id)
        row.last_heartbeat_at = time.time() - age_s


# ------------------------------------------------------------------- metrics
def test_metrics_has_all_six_groups_and_provisional_thresholds(monitoring_client) -> None:  # type: ignore[no-untyped-def]
    client, _db = monitoring_client
    # 第一次调用建立请求样本（中间件在响应后落计数），第二次读取快照
    client.get("/api/v1/monitoring/metrics")
    r = client.get("/api/v1/monitoring/metrics")
    assert r.status_code == 200, r.text
    body = r.json()
    for group in ("api", "worker", "queue", "run", "artifact", "database"):
        assert group in body, f"缺少指标组 {group}"
    assert body["thresholds_status"]["status"] == "provisional"
    assert "SLA" in body["thresholds_status"]["disclaimer"]
    # API 指标：真实请求已被中间件计数
    assert body["api"]["requests_total"] >= 1
    assert body["api"]["response_time_ms"]["count"] >= 1
    assert body["api"]["response_time_ms"]["p50"] is not None
    # Queue / Run 指标带 scope 与 counting 口径声明
    assert body["queue"]["counting"]["source"]
    assert body["run"]["counting"]["source"]
    assert body["scope"]["mode"] == "global"
    # 数据库组：迁移版本可观测
    assert "migration" in body["database"]
    assert "pool" in body["database"]
    assert "failures" in body["database"]


def test_metrics_worker_section_reflects_heartbeat(monitoring_client) -> None:  # type: ignore[no-untyped-def]
    client, db = monitoring_client
    _stale_worker(db, "w-offline")
    upsert_worker_heartbeat(db, worker_id="w-online", mode="inline", status="RUNNING")
    body = client.get("/api/v1/monitoring/metrics").json()
    workers = {w["worker_id"]: w for w in body["worker"]["workers"]}
    assert workers["w-offline"]["online"] is False
    assert workers["w-online"]["online"] is True
    # 计数包含 API 内嵌 Worker 的心跳（inline_worker 默认开启），只做相对断言
    assert body["worker"]["online"] >= 1
    assert body["worker"]["offline"] >= 1
    assert body["worker"]["total"] >= 2
    assert body["worker"]["thresholds"]["status"] == "provisional"


def test_metrics_request_error_rate_recorded(monitoring_client) -> None:  # type: ignore[no-untyped-def]
    client, _db = monitoring_client
    client.get("/api/v1/monitoring/does-not-exist")  # 404 计入错误
    body = client.get("/api/v1/monitoring/metrics").json()
    assert body["api"]["error_count"] >= 1
    assert body["api"]["recent_errors"], "最近错误列表应记录 404"
    assert any(e["status"] == 404 for e in body["api"]["recent_errors"])


# -------------------------------------------------------------------- alerts
def test_alerts_lifecycle_open_ack_resolve(monitoring_client) -> None:  # type: ignore[no-untyped-def]
    client, db = monitoring_client
    _stale_worker(db)
    listing = client.get("/api/v1/monitoring/alerts").json()
    assert listing["total"] >= 1
    alert = next(a for a in listing["alerts"] if a["alert_type"] == "WORKER_OFFLINE")
    assert alert["status"] == "OPEN"
    assert alert["detail"]["threshold_status"] == "provisional"

    ack = client.post(f"/api/v1/monitoring/alerts/{alert['alert_id']}/acknowledge", json={})
    assert ack.status_code == 200, ack.text
    assert ack.json()["status"] == "ACKNOWLEDGED"
    assert ack.json()["acknowledged_by"]  # 处理主体由服务端记录
    assert ack.json()["acknowledged_at"] is not None

    # 重复 ACK → 非法迁移
    again = client.post(f"/api/v1/monitoring/alerts/{alert['alert_id']}/acknowledge", json={})
    assert again.status_code == 409

    # 解决原因必填
    no_reason = client.post(
        f"/api/v1/monitoring/alerts/{alert['alert_id']}/resolve", json={"reason": " "}
    )
    assert no_reason.status_code == 422

    done = client.post(
        f"/api/v1/monitoring/alerts/{alert['alert_id']}/resolve",
        json={"reason": "Worker 已恢复心跳"},
    )
    assert done.status_code == 200, done.text
    doc = done.json()
    assert doc["status"] == "RESOLVED"
    assert doc["resolution_reason"] == "Worker 已恢复心跳"
    assert doc["resolved_by"]
    assert doc["resolved_at"] is not None

    # 已解决告警不得再次处理
    assert (
        client.post(f"/api/v1/monitoring/alerts/{alert['alert_id']}/resolve", json={"reason": "x"}).status_code
        == 409
    )

    history = client.get("/api/v1/monitoring/alerts?status=RESOLVED").json()
    assert any(a["alert_id"] == alert["alert_id"] for a in history["alerts"])


def test_alerts_not_found(monitoring_client) -> None:  # type: ignore[no-untyped-def]
    client, _db = monitoring_client
    assert client.get("/api/v1/monitoring/alerts/alrt_missing").status_code == 404
    assert (
        client.post("/api/v1/monitoring/alerts/alrt_missing/acknowledge", json={}).status_code == 404
    )


def test_alerts_auto_evaluated_on_metrics(monitoring_client) -> None:  # type: ignore[no-untyped-def]
    client, db = monitoring_client
    _stale_worker(db)
    body = client.get("/api/v1/monitoring/metrics").json()
    assert "WORKER_OFFLINE:w-stale" in body["alerts"]["evaluation"]["findings"]
    assert body["alerts"]["summary"]["open"] >= 1


# --------------------------------------------------------------- scope / RBAC
def test_metrics_scope_declared_for_workspace_user(monitoring_auth_client) -> None:  # type: ignore[no-untyped-def]
    client, _db = monitoring_auth_client
    headers = _login(client, "mon-qa")
    body = client.get("/api/v1/monitoring/metrics", headers=headers).json()
    assert body["scope"]["mode"] == "authorized_workspaces"
    assert body["scope"]["global"] is False
    assert body["scope"]["authorized_workspaces"] == [WS_A]

    admin_headers = _login(client, "mon-admin")
    admin_body = client.get("/api/v1/monitoring/metrics", headers=admin_headers).json()
    assert admin_body["scope"]["global"] is True


def test_alert_mutations_require_platform_admin(monitoring_auth_client) -> None:  # type: ignore[no-untyped-def]
    client, db = monitoring_auth_client
    _stale_worker(db)
    # TestClient 共享 Cookie：登录与请求必须成对（会话与 CSRF 同主体）
    qa_headers = _login(client, "mon-qa")
    listing = client.get("/api/v1/monitoring/alerts", headers=qa_headers).json()
    alert = next(a for a in listing["alerts"] if a["alert_type"] == "WORKER_OFFLINE")

    denied = client.post(
        f"/api/v1/monitoring/alerts/{alert['alert_id']}/acknowledge", json={}, headers=qa_headers
    )
    assert denied.status_code == 403
    assert denied.json()["detail"]["code"] == "PERMISSION_DENIED"

    admin_headers = _login(client, "mon-admin")
    ok = client.post(
        f"/api/v1/monitoring/alerts/{alert['alert_id']}/acknowledge", json={}, headers=admin_headers
    )
    assert ok.status_code == 200


def test_alert_detail_workspace_forbidden(monitoring_auth_client) -> None:  # type: ignore[no-untyped-def]
    client, db = monitoring_auth_client
    # 直接构造一条归属其它 Workspace 的告警
    with db.session() as s:
        from traceguard_core.ids import new_id
        from traceguard_persistence.models import AlertRow

        row = AlertRow(
            alert_id=new_id("alrt"),
            fingerprint="QUEUE_BACKLOG:other",
            alert_type="QUEUE_BACKLOG",
            severity="WARNING",
            status="OPEN",
            title="其它空间积压",
            message="x",
            scope_workspace="ws-other",
            first_seen=time.time(),
            last_seen=time.time(),
        )
        s.add(row)
        s.flush()
        alert_id = row.alert_id

    qa_headers = _login(client, "mon-qa")
    r = client.get(f"/api/v1/monitoring/alerts/{alert_id}", headers=qa_headers)
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "WORKSPACE_FORBIDDEN"

    admin_headers = _login(client, "mon-admin")
    assert client.get(f"/api/v1/monitoring/alerts/{alert_id}", headers=admin_headers).status_code == 200
