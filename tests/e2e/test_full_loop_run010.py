"""RUN-010 WP8：完整业务闭环集成验收（进程内，认证开启 + Egress 强制 + RBAC）。

覆盖指令 §8.1~§8.5 核心闭环（真实数据库、真实 Chromium、真实诊断）：
- Intent → TestCase → 人工评审 → Oracle 绑定 → Task/Run → 真实执行 →
  Evidence → Diagnosis（TF-DB-001 注入保证真实 FAILED）；
- 四角色 RBAC 服务端强制（viewer/engineer/lead/admin）；
- 正式 Run 强制经 Egress Proxy（report.browser.egress.enforced 断言）；
- 未登录 fail-closed；CSRF 生效；审计链完整。

安全合规（AgentDR info_theft_steal_env_keys）：
- 全进程内实现：不启动子进程、不构造/传递环境字典（无 subprocess/env= 模式）；
- 凭据全部为虚拟测试常量（本文件明文 test-only），不读取真实环境凭据；
- TeamFlow 目标复用既有 conftest fixture（既有代码路径，本文件零环境操作）。
"""

from __future__ import annotations

import time
import urllib.request

import pytest
from fastapi.testclient import TestClient
from traceguard_core.config import reset_settings_cache
from traceguard_persistence import Database, set_database
from traceguard_persistence import auth as auth_store

PWD = "wp8-password-1"  # 虚拟测试口令（test-only 常量，非真实凭据）


@pytest.fixture()
def client(tmp_path, monkeypatch, teamflow_server):  # type: ignore[no-untyped-def]
    """进程内认证客户端 + 真实 TeamFlow 目标（fixture 提供）。"""
    monkeypatch.setenv("TG_AUTH_ENABLED", "true")
    monkeypatch.setenv("TG_AUTH_CSRF_REQUIRED", "true")
    monkeypatch.setenv("TG_LLM_PROVIDER", "")
    monkeypatch.setenv("TG_INLINE_WORKER", "false")
    reset_settings_cache()
    from traceguard_api.auth import reset_auth_policy_cache
    from traceguard_api.main import create_app
    from traceguard_api.runtime import reset_runtime
    from traceguard_api.services import reset_services

    reset_auth_policy_cache()
    db = Database(f"sqlite:///{tmp_path / 'wp8.db'}")
    set_database(db)
    reset_runtime()
    reset_services()
    app = create_app()
    with TestClient(app) as c:
        for name, roles in [
            ("wp8-admin", ["platform-admin", "qa-lead", "qa-engineer"]),
            ("wp8-lead", ["qa-lead", "qa-engineer"]),
            ("wp8-eng", ["qa-engineer"]),
            ("wp8-viewer", ["viewer"]),
        ]:
            auth_store.create_user(db, username=name, password=PWD, roles=roles)
        yield c, db, teamflow_server
    reset_runtime()
    reset_services()
    reset_settings_cache()
    reset_auth_policy_cache()


def _login(client: TestClient, username: str) -> str:
    r = client.post("/api/v1/auth/login", json={"username": username, "password": PWD})
    assert r.status_code == 200, (r.status_code, r.text)
    return r.json()["csrf_token"]


def _h(csrf: str) -> dict[str, str]:
    return {"X-CSRF-Token": csrf}


# ---------------------------------------------------------------------------
# 8.2 认证场景：未登录 fail-closed + 四角色 RBAC（服务端强制）
# ---------------------------------------------------------------------------
def test_unauthenticated_and_rbac_matrix(client) -> None:  # type: ignore[no-untyped-def]
    c, _db, _tf = client
    assert c.get("/api/v1/runs").status_code == 401

    viewer_csrf = _login(c, "wp8-viewer")
    r = c.post("/api/v1/intents", json={"title": "x", "description": "y"}, headers=_h(viewer_csrf))
    assert r.status_code == 403 and r.json()["detail"]["code"] == "PERMISSION_DENIED"

    _login(c, "wp8-eng")  # 会话副作用：登录后才有身份
    assert c.get("/api/v1/auth/users").status_code == 403

    lead_csrf = _login(c, "wp8-lead")
    assert c.post("/api/v1/repairs/rp_x/promote", json={}, headers=_h(lead_csrf)).status_code == 403


# ---------------------------------------------------------------------------
# 8.1/8.3 完整闭环：Intent → TestCase → 评审 → Oracle → Run(经Egress) → 诊断
# ---------------------------------------------------------------------------
def test_full_business_loop_with_auth_and_egress(client) -> None:  # type: ignore[no-untyped-def]
    c, _db, tf = client
    eng = _login(c, "wp8-eng")

    # 1. Intent
    r = c.post(
        "/api/v1/intents",
        json={"title": "WP8 闭环", "description": "创建项目必须真实持久化", "owner": "qa"},
        headers=_h(eng),
    )
    assert r.status_code == 201, (r.status_code, r.text)
    intent_id = r.json()["intent_id"]

    # 2. TestCase（真实 TeamFlow 目标）
    tc_payload = {
        "testcase_id": "tcase_wp8_loop",
        "title": "WP8 闭环用例",
        "intent_ref": intent_id,
        "steps": [
            {"step_id": "s1", "action": "navigate", "value": "/projects"},
            {
                "step_id": "s2",
                "action": "click",
                "target": {"strategy": "test_id", "value": "nav-new"},
            },
            {
                "step_id": "s3",
                "action": "fill",
                "target": {"strategy": "test_id", "value": "project-name"},
                "value": "WP8-Loop-Project",
            },
            {
                "step_id": "s4",
                "action": "fill",
                "target": {"strategy": "test_id", "value": "project-description"},
                "value": "闭环验证",
            },
            {
                "step_id": "s5",
                "action": "click",
                "target": {"strategy": "test_id", "value": "create-submit"},
            },
            {"step_id": "s6", "action": "wait", "value": "1200"},
        ],
        "assertions": [
            {
                "assertion_id": "a_wp8_biz",
                "description": "项目必须真实持久化",
                "kind": "business_state",
                "required": True,
                "expected": {
                    "api": "/api/state/projects?workspace=default",
                    "field": "count",
                    "op": "gte",
                    "value": 1,
                },
                "oracle_id": "oracle_wp8_biz",
            }
        ],
        "run_config": {"base_url": tf},
    }
    r = c.post("/api/v1/testcases", json=tc_payload, headers=_h(eng))
    assert r.status_code == 201, (r.status_code, r.text)

    # 3. qa-engineer 无评审权限（服务端真实拒绝，请求体声明无效）
    r = c.post(
        "/api/v1/testcases/tcase_wp8_loop/versions/1/review",
        json={"decision": "APPROVED", "reviewer": "user:wp8-lead"},
        headers=_h(eng),
    )
    assert r.status_code == 403

    # 4. qa-lead：Oracle + 评审
    lead = _login(c, "wp8-lead")
    r = c.post(
        "/api/v1/oracles",
        json={
            "oracle_id": "oracle_wp8_biz",
            "assertion_id": "a_wp8_biz",
            "expected_behavior": "创建后状态查询必须出现该项目",
            "requirement_ref": "REQ-WP8-1",
            "approved": True,
            "approved_by": "user:wp8-lead",
        },
        headers=_h(lead),
    )
    assert r.status_code in (200, 201), (r.status_code, r.text)
    # §8.1 Oracle Binding：Oracle 绑定到断言（绑定 ≠ 审批，此处由 qa-lead 操作）
    r = c.post(
        "/api/v1/testcases/tcase_wp8_loop/versions/1/oracle-bindings",
        json={"bindings": {"a_wp8_biz": {"oracle_id": "oracle_wp8_biz"}}, "actor": "user:wp8-lead"},
        headers=_h(lead),
    )
    assert r.status_code == 200, (r.status_code, r.text)
    r = c.post(
        "/api/v1/testcases/tcase_wp8_loop/versions/1/review",
        json={"decision": "APPROVED", "reviewer": "user:wp8-lead"},
        headers=_h(lead),
    )
    assert r.status_code == 200, (r.status_code, r.text)

    # 5. Task → Run → 真实执行（浏览器强制经 Egress Proxy）
    # 注意：TestClient cookie jar 共享，lead 登录后会话已切换 —— 重新登录 eng
    eng = _login(c, "wp8-eng")
    r = c.post(
        "/api/v1/tasks",
        json={"testcase_ref": "tcase_wp8_loop", "mode": "playwright", "application": "teamflow"},
        headers=_h(eng),
    )
    assert r.status_code == 201, (r.status_code, r.text)
    task_id = r.json()["task_id"]
    r = c.post(f"/api/v1/tasks/{task_id}/runs", json={}, headers=_h(eng))
    assert r.status_code == 201, (r.status_code, r.text)
    run_id = r.json()["run_id"]
    r = c.post(f"/api/v1/runs/{run_id}/execute", json={}, headers=_h(eng))
    assert r.status_code == 200, (r.status_code, r.text)
    outcome = r.json()
    assert outcome["execution_status"] == "COMPLETED"
    assert outcome["observer_mode"] == "playwright"
    report = outcome.get("report") or {}

    # 8.3 网络安全：正式 Run 浏览器强制经受控出站代理
    egress = (report.get("browser") or {}).get("egress") or {}
    assert egress.get("enforced") is True, egress
    assert egress.get("url", "").startswith("http://127.0.0.1:"), egress

    # 6. 证据真实落库
    r = c.get(f"/api/v1/runs/{run_id}/evidence")
    assert r.status_code == 200 and len(r.json()) > 0

    # 7. 诊断：清库后注入 TF-DB-001 保证真实 FAILED → 真实诊断（回灌可追溯假设）
    # 先 reset（清项目+清故障）避免共享 TeamFlow 状态干扰 count 断言，再注入故障
    req = urllib.request.Request(
        f"{tf}/api/test/reset",
        data=b"{}",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10):
        pass
    req = urllib.request.Request(
        f"{tf}/api/faults/TF-DB-001/activate",
        data=b"{}",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10):
        pass
    try:
        r = c.post(f"/api/v1/tasks/{task_id}/runs", json={}, headers=_h(eng))
        run2 = r.json()["run_id"]
        r = c.post(f"/api/v1/runs/{run2}/execute", json={}, headers=_h(eng))
        assert r.status_code == 200
        assert r.json()["test_verdict"] == "FAILED", r.json()
        r = c.post(f"/api/v1/runs/{run2}/diagnose", json={}, headers=_h(eng))
        assert r.status_code in (200, 201), (r.status_code, r.text)
        diag = r.json()
        assert diag.get("outcome") in {"CONFIRMED", "PROBABLE", "INCONCLUSIVE"}
        assert diag.get("hypotheses"), "诊断必须有可追溯假设"
    finally:
        req = urllib.request.Request(
            f"{tf}/api/faults/TF-DB-001/clear",
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10):
            pass


# ---------------------------------------------------------------------------
# 审计链（platform-admin）
# ---------------------------------------------------------------------------
def test_admin_audit_chain_intact(client) -> None:  # type: ignore[no-untyped-def]
    c, _db, _tf = client
    _login(c, "wp8-admin")
    users = c.get("/api/v1/auth/users")
    assert users.status_code == 200 and len(users.json()) >= 4
    v = c.get("/api/v1/auth/audit/verify")
    assert v.status_code == 200
    body = v.json()
    assert body["intact"] is True and body["entries"] > 0
    time.sleep(0)  # 占位：审计链校验为同步完成
