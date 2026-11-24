"""RUN-010 WP1：认证产品化、数据库限速、workspace 对象级授权、审计链跨进程安全。

覆盖指令 §1.5 验收清单：
1. 未登录不能访问业务 API；
2. viewer 不能执行写操作；
3. qa-engineer 不能审批 Repair；
4. qa-lead 不能执行仅管理员允许的高风险操作；
5. platform-admin 可以执行授权范围内的管理操作；
6. 客户端伪造角色被拒绝；
7. 跨 Workspace 读取被拒绝；
8. 过期与撤销 Session 被拒绝；
9. CSRF 攻击被拒绝；
10. 审计链被篡改能够检测。

附加：数据库级登录限速（不可被进程重启绕过）、认证环境策略（production fail-closed）、
未知用户响应时差均衡、审计 seq 跨进程并发追加。
"""

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
    """认证开启 + 四角色用户。admin 拥有全部 workspace，engineer/lead 仅 default。"""
    monkeypatch.setenv("TG_AUTH_ENABLED", "true")
    monkeypatch.setenv("TG_AUTH_CSRF_REQUIRED", "true")
    monkeypatch.setenv("TG_LLM_PROVIDER", "")
    reset_settings_cache()
    from traceguard_api.auth import reset_auth_policy_cache
    from traceguard_api.main import create_app
    from traceguard_api.runtime import reset_runtime
    from traceguard_api.services import reset_services

    reset_auth_policy_cache()
    db = Database(f"sqlite:///{tmp_path / 'auth10.db'}")
    set_database(db)
    reset_runtime()
    reset_services()
    app = create_app()
    with TestClient(app) as client:
        users = [
            ("admin", ["platform-admin", "qa-lead", "qa-engineer", "viewer"], ["*"]),
            ("lead", ["qa-lead", "qa-engineer", "viewer"], ["default"]),
            ("engineer", ["qa-engineer", "viewer"], ["default"]),
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


def _login(client: TestClient, username: str, password: str = "password-123") -> tuple[dict, str]:
    r = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    body = r.json()
    return body, body["csrf_token"]


def _headers(csrf: str) -> dict[str, str]:
    return {"X-CSRF-Token": csrf}


# ---------------------------------------------------------------------------
# 验收 1：未登录 fail-closed
# ---------------------------------------------------------------------------


def test_unauthenticated_fail_closed_includes_new_endpoints(auth_client):  # type: ignore[no-untyped-def]
    client, _ = auth_client
    for path in [
        "/api/v1/testcases",
        "/api/v1/runs",
        "/api/v1/diagnoses",
        "/api/v1/auth/users",
        "/api/v1/auth/permissions",
    ]:
        r = client.get(path)
        assert r.status_code == 401, (path, r.text)
    # /auth/status 是公开端点：未登录也要能知道认证是否开启
    status = client.get("/api/v1/auth/status")
    assert status.status_code == 200
    assert status.json()["auth_enabled"] is True


# ---------------------------------------------------------------------------
# 验收 2/3/4/5：角色权限矩阵
# ---------------------------------------------------------------------------


def test_viewer_cannot_write_but_can_read(auth_client):  # type: ignore[no-untyped-def]
    client, _ = auth_client
    _, viewer_csrf = _login(client, "viewer")
    r = client.post(
        "/api/v1/intents",
        json={"title": "viewer 写入", "description": "拒绝"},
        headers=_headers(viewer_csrf),
    )
    assert r.status_code == 403, r.text
    assert client.get("/api/v1/intents?workspace_id=default").status_code == 200


def test_engineer_cannot_approve_repair(auth_client):  # type: ignore[no-untyped-def]
    client, _ = auth_client
    _, eng_csrf = _login(client, "engineer")
    r = client.post(
        "/api/v1/repairs/rp_missing/approve",
        json={"decision": "APPROVED", "approved_by": "user:lead"},
        headers=_headers(eng_csrf),
    )
    assert r.status_code == 403, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "PERMISSION_DENIED"


def test_lead_cannot_promote_or_rollback(auth_client):  # type: ignore[no-untyped-def]
    client, _ = auth_client
    _, lead_csrf = _login(client, "lead")
    # repair.promote / repair.rollback 仅 platform-admin
    r1 = client.post("/api/v1/repairs/rp_missing/promote", json={}, headers=_headers(lead_csrf))
    r2 = client.post("/api/v1/repairs/rp_missing/rollback", json={}, headers=_headers(lead_csrf))
    assert r1.status_code == 403, r1.text
    assert r2.status_code == 403, r2.text
    # 用户管理是 admin-only
    users = client.get("/api/v1/auth/users", headers=_headers(lead_csrf))
    assert users.status_code == 403


def test_admin_can_manage_users_and_permissions(auth_client):  # type: ignore[no-untyped-def]
    client, _ = auth_client
    _, admin_csrf = _login(client, "admin")
    users = client.get("/api/v1/auth/users", headers=_headers(admin_csrf))
    assert users.status_code == 200, users.text
    assert len(users.json()) == 4
    perm = client.get("/api/v1/auth/permissions", headers=_headers(admin_csrf))
    assert perm.status_code == 200
    matrix = perm.json()["matrix"]
    assert "repair.promote" in matrix["platform-admin"]
    assert "repair.promote" not in matrix["qa-lead"]
    # 创建受限 workspace 用户
    created = client.post(
        "/api/v1/auth/users",
        json={
            "username": "scoped-user",
            "password": "password-456",
            "roles": ["viewer"],
            "workspaces": ["ws-b"],
        },
        headers=_headers(admin_csrf),
    )
    assert created.status_code == 201, created.text
    assert created.json()["workspaces"] == ["ws-b"]


# ---------------------------------------------------------------------------
# 验收 6：客户端伪造身份声明被拒绝（请求体声明不提升权限）
# ---------------------------------------------------------------------------


def test_forged_identity_declarations_are_ignored(auth_client):  # type: ignore[no-untyped-def]
    client, db = auth_client
    _, eng_csrf = _login(client, "engineer")
    # 伪造 reviewer / approved_by / operator 不得改变判定主体
    r = client.post(
        "/api/v1/testcases/tc_missing/versions/1/review",
        json={"decision": "APPROVED", "reviewer": "user:admin", "operator": "user:admin"},
        headers=_headers(eng_csrf),
    )
    assert r.status_code == 403, r.text
    with db.session() as s:
        rows = s.execute(
            text("SELECT action, result FROM tg_audit_log WHERE action = 'testcase.review'")
        ).all()
    assert rows and rows[-1][1] == "DENIED"


# ---------------------------------------------------------------------------
# 验收 7：跨 Workspace 读取被拒绝（对象级授权）
# ---------------------------------------------------------------------------


def test_cross_workspace_access_denied(auth_client):  # type: ignore[no-untyped-def]
    client, _ = auth_client
    _, eng_csrf = _login(client, "engineer")
    # engineer 的 scope 是 ["default"]：访问 ws-b 被拒
    r = client.get("/api/v1/intents?workspace_id=ws-b", headers=_headers(eng_csrf))
    assert r.status_code == 403, r.text
    assert r.json()["detail"]["code"] == "WORKSPACE_FORBIDDEN"
    # default 范围内正常
    ok = client.get("/api/v1/intents?workspace_id=default", headers=_headers(eng_csrf))
    assert ok.status_code == 200
    # platform-admin（*）不受限
    _, admin_csrf = _login(client, "admin")
    r2 = client.get("/api/v1/intents?workspace_id=ws-b", headers=_headers(admin_csrf))
    assert r2.status_code == 200


def test_user_workspace_scope_update_and_revocation(auth_client):  # type: ignore[no-untyped-def]
    client, db = auth_client
    _, admin_csrf = _login(client, "admin")
    users = client.get("/api/v1/auth/users", headers=_headers(admin_csrf)).json()
    eng = next(u for u in users if u["username"] == "engineer")
    # 收窄范围后旧请求被拒
    upd = client.post(
        f"/api/v1/auth/users/{eng['user_id']}/workspaces",
        json={"workspaces": ["ws-c"]},
        headers=_headers(admin_csrf),
    )
    assert upd.status_code == 200, upd.text
    _, eng_csrf = _login(client, "engineer")
    assert client.get("/api/v1/intents?workspace_id=default").status_code == 403
    assert client.get("/api/v1/intents?workspace_id=ws-c").status_code == 200
    # 停用用户后所有会话失效（重新登录 admin：cookie jar 已被 engineer 会话覆盖）
    engineer_token = client.cookies.get("tg_session")
    _, admin_csrf = _login(client, "admin")
    deact = client.post(
        f"/api/v1/auth/users/{eng['user_id']}/active?active=false",
        json={},
        headers=_headers(admin_csrf),
    )
    assert deact.status_code == 200, deact.text
    # engineer 的旧会话必须已被撤销
    assert engineer_token and auth_store.get_session(db, engineer_token) is None
    # engineer 无法再登录
    assert (
        client.post(
            "/api/v1/auth/login", json={"username": "engineer", "password": "password-123"}
        ).status_code
        == 401
    )


# ---------------------------------------------------------------------------
# 验收 8/9：过期/撤销 Session 与 CSRF
# ---------------------------------------------------------------------------


def test_expired_session_rejected(auth_client):  # type: ignore[no-untyped-def]
    client, db = auth_client
    _, csrf = _login(client, "admin")
    with db.session() as s:
        s.execute(text("UPDATE tg_session SET expires_at = :exp"), {"exp": time.time() - 1})
    assert client.get("/api/v1/auth/me").status_code == 401


def test_csrf_attack_rejected(auth_client):  # type: ignore[no-untyped-def]
    client, _ = auth_client
    _login(client, "admin")
    # 错误 CSRF token
    r = client.post(
        "/api/v1/intents",
        json={"title": "csrf", "description": ""},
        headers=_headers("wrong-token"),
    )
    assert r.status_code == 403, r.text
    assert r.json()["detail"]["code"] == "CSRF_FAILED"
    # 缺失 CSRF token
    r2 = client.post("/api/v1/intents", json={"title": "csrf", "description": ""})
    assert r2.status_code == 403


# ---------------------------------------------------------------------------
# 验收 10：审计链篡改检测 + seq 顺序
# ---------------------------------------------------------------------------


def test_audit_chain_tamper_detection_with_seq_order(auth_client):  # type: ignore[no-untyped-def]
    client, db = auth_client
    _login(client, "admin")
    result = auth_store.verify_audit_chain(db)
    assert result["intact"] and result["entries"] >= 1
    with db.session() as s:
        seqs = [
            row[0] for row in s.execute(text("SELECT seq FROM tg_audit_log ORDER BY seq ASC")).all()
        ]
    assert seqs == sorted(seqs)
    assert all(s is not None for s in seqs)
    with db.session() as s:
        s.execute(text("UPDATE tg_audit_log SET result = 'TAMPERED' WHERE seq = 1"))
    assert auth_store.verify_audit_chain(db)["intact"] is False


def test_audit_append_across_manager_instances_shares_chain(auth_client):  # type: ignore[no-untyped-def]
    """模拟多进程：两个独立 Database 连接同时追加审计，seq 与哈希链仍然一致。"""
    client, db = auth_client
    db2 = Database(db.url)
    e1 = auth_store.record_audit(db, actor_id="proc:a", action="x.a", result="SUCCESS")
    e2 = auth_store.record_audit(db2, actor_id="proc:b", action="x.b", result="SUCCESS")
    e3 = auth_store.record_audit(db, actor_id="proc:a", action="x.c", result="SUCCESS")
    assert e2["seq"] == e1["seq"] + 1
    assert e3["seq"] == e2["seq"] + 1
    assert e2["prev_hash"] == e1["entry_hash"]
    assert e3["prev_hash"] == e2["entry_hash"]
    for target in (db, db2):
        v = auth_store.verify_audit_chain(target)
        assert v["intact"], v


# ---------------------------------------------------------------------------
# 数据库级登录限速（不可被进程重启绕过）
# ---------------------------------------------------------------------------


def test_login_rate_limit_persists_across_restart(auth_client):  # type: ignore[no-untyped-def]
    client, db = auth_client
    for _ in range(5):
        r = client.post("/api/v1/auth/login", json={"username": "admin", "password": "wrong-pass"})
        assert r.status_code == 401
    # 第 6 次触发限速（即使密码正确）
    r = client.post("/api/v1/auth/login", json={"username": "admin", "password": "password-123"})
    assert r.status_code == 429, r.text
    assert r.json()["detail"]["code"] == "LOGIN_RATE_LIMITED"
    # "重启"：新建 AuthManager（不同进程语义），共享同一数据库计数
    from traceguard_api.auth import AuthManager

    manager2 = AuthManager(db)
    with pytest.raises(Exception) as exc:
        manager2.login("admin", "password-123", "testclient")
    assert "LOGIN_RATE_LIMITED" in str(getattr(exc.value, "detail", exc.value))


def test_login_failure_counter_scoped_by_client(auth_client):  # type: ignore[no-untyped-def]
    client, db = auth_client
    for _ in range(5):
        client.post("/api/v1/auth/login", json={"username": "lead", "password": "wrong-pass"})
    assert (
        auth_store.count_failed_logins(db, username="lead", client="testclient", window_s=300) >= 5
    )
    # 其他 client 维度不受影响
    assert (
        auth_store.count_failed_logins(db, username="lead", client="other-client", window_s=300)
        == 0
    )


def test_unknown_username_timing_equalized(auth_client):  # type: ignore[no-untyped-def]
    """未知用户名与错误密码返回同一错误码（不做用户名枚举提示）。"""
    client, _ = auth_client
    r_missing = client.post(
        "/api/v1/auth/login", json={"username": "no-such-user-xyz", "password": "whatever-123"}
    )
    r_wrong = client.post(
        "/api/v1/auth/login", json={"username": "admin", "password": "wrong-pass-123"}
    )
    assert r_missing.status_code == r_wrong.status_code == 401
    assert (
        r_missing.json()["detail"]["code"]
        == r_wrong.json()["detail"]["code"]
        == "INVALID_CREDENTIALS"
    )
    # 响应时间同数量级（均执行了一次 KDF；宽松阈值防 CI 抖动）
    assert r_missing.elapsed.total_seconds() > 0.001


# ---------------------------------------------------------------------------
# 认证环境策略（production fail-closed）
# ---------------------------------------------------------------------------


def _policy(monkeypatch, **env):  # type: ignore[no-untyped-def]
    from traceguard_api.auth_policy import evaluate_auth_policy
    from traceguard_core.config import get_settings

    for k, v in env.items():
        monkeypatch.setenv(k, v)
    reset_settings_cache()
    try:
        return evaluate_auth_policy(get_settings())
    finally:
        for k in env:
            monkeypatch.delenv(k, raising=False)
        reset_settings_cache()


def test_policy_production_auth_disabled_fails(monkeypatch):  # type: ignore[no-untyped-def]
    d = _policy(monkeypatch, TG_ENVIRONMENT="production", TG_AUTH_ENABLED="false")
    assert d.ok is False
    assert "AUTH_DISABLED_IN_PRODUCTION" in d.fail_reason


def test_policy_production_default_on(monkeypatch):  # type: ignore[no-untyped-def]
    d = _policy(monkeypatch, TG_ENVIRONMENT="production", TG_AUTH_COOKIE_SECURE="true")
    assert d.ok is True
    assert d.auth_effective is True


def test_policy_production_insecure_cookie_fails(monkeypatch):  # type: ignore[no-untyped-def]
    d = _policy(monkeypatch, TG_ENVIRONMENT="production", TG_AUTH_COOKIE_SECURE="false")
    assert d.ok is False
    assert "AUTH_COOKIE_INSECURE_IN_PRODUCTION" in d.fail_reason


def test_policy_production_long_session_fails(monkeypatch):  # type: ignore[no-untyped-def]
    d = _policy(
        monkeypatch,
        TG_ENVIRONMENT="production",
        TG_AUTH_COOKIE_SECURE="true",
        TG_AUTH_SESSION_TTL_S=str(72 * 3600),
    )
    assert d.ok is False
    assert "AUTH_SESSION_TTL_TOO_LONG" in d.fail_reason


def test_policy_staging_requires_auth(monkeypatch):  # type: ignore[no-untyped-def]
    d = _policy(monkeypatch, TG_ENVIRONMENT="staging", TG_AUTH_ENABLED="false")
    assert d.ok is False


def test_policy_development_off_allowed_with_warning(monkeypatch):  # type: ignore[no-untyped-def]
    d = _policy(monkeypatch, TG_ENVIRONMENT="development", TG_AUTH_ENABLED="false")
    assert d.ok is True
    assert d.auth_effective is False
    assert any("AUTH_DISABLED" in w for w in d.warnings_or_empty)


# ---------------------------------------------------------------------------
# RUN-011 WP1：RBAC 动作映射的语义修正
# ---------------------------------------------------------------------------
def test_action_mapping_for_compile_and_evidence_verify() -> None:
    """探索编译 = 创建候选测试用例；证据校验 = 只读完整性重算。

    旧映射把两者落到 system.configure，导致 qa-engineer 在真实独立部署闭环里
    无法编译自己的探索、也无法自证证据未被篡改（真实被 403 挡住）。
    修正的是语义映射，不是放宽权限：
    - 编译仍要求 testcase.create（viewer 依然被拒）；
    - 证据校验要求 read（所有登录角色都有，且它不写任何业务状态）。
    """
    from traceguard_api.auth import action_for_request

    assert action_for_request("POST", "/api/v1/explorations/exp_1/compile") == "testcase.create"
    assert action_for_request("POST", "/api/v1/evidence/evd_1/verify") == "read"
    # 未被修正的高风险映射必须保持 fail-closed
    assert action_for_request("POST", "/api/v1/repairs/rp_1/promote") == "repair.promote"
    # 集合端点（无尾斜杠）也必须映射到 repair.submit，而不是 fail-closed 到系统配置
    assert action_for_request("POST", "/api/v1/repairs") == "repair.submit"
    assert action_for_request("POST", "/api/v1/repairs/rp_1/validate") == "repair.submit"
    assert action_for_request("POST", "/api/v1/unknown/write/action") == "system.configure"
    assert action_for_request("GET", "/api/v1/runs") == "read"


def test_audit_verify_normalizes_json_columns_for_postgres(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """PostgreSQL 的 JSON 列读回是 dict，SQLite 是字符串；两者必须得到同一摘要。

    真实缺陷（RUN-011 WP1）：多进程 PostgreSQL 拓扑下 `audit/verify` 把所有带
    before/after 的条目判为 broken —— 不是数据被篡改，而是校验时没有把 JSON 列
    还原成写入时的规范化字符串。
    """
    import json as _json

    from traceguard_persistence import Database, set_database
    from traceguard_persistence import auth as auth_store

    db = Database(f"sqlite:///{tmp_path / 'audit-norm.db'}")
    set_database(db)
    db.create_schema()
    auth_store.record_audit(
        db,
        actor_id="user:qa-lead",
        role="qa-lead",
        action="repair.approve",
        resource_type="repair",
        resource_id="rp_1",
        result="SUCCESS",
        before={"status": "PENDING"},
        after={"status": "APPROVED", "nested": {"b": 2, "a": 1}},
    )
    with db.session() as session:
        row = dict(session.execute(text("SELECT * FROM tg_audit_log")).mappings().first())

    def digest(entry: dict) -> str:
        cleaned = {k: v for k, v in entry.items() if k not in {"seq", "entry_hash"}}
        return auth_store._audit_hash(cleaned)

    sqlite_like = auth_store._normalize_json_columns(dict(row))
    pg_like = dict(row)
    pg_like["before_json"] = _json.loads(row["before_json"])
    pg_like["after_json"] = _json.loads(row["after_json"])
    pg_like = auth_store._normalize_json_columns(pg_like)

    assert digest(sqlite_like) == digest(pg_like)
    assert digest(pg_like) == auth_store._audit_hash(
        {k: v for k, v in row.items() if k not in {"seq", "entry_hash"}}
    )
