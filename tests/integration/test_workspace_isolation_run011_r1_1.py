"""RUN-011-R1.1 WP2：真实双 Workspace 隔离与对象级授权验收。

与 RUN-010 的认证测试不同：这里每个 Workspace 都有**专属** viewer / qa-engineer /
qa-lead，所有账号都不使用 `workspaces=["*"]`，并且负向用例会同时校验：

- HTTP 403 / 404（不泄露对象是否存在之外的信息）；
- 数据库没有新增非法对象；
- 审计记录中存在 DENIED；
- 不存在跨 Workspace 的 Evidence / Task / Run 引用。
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from traceguard_core.config import reset_settings_cache
from traceguard_persistence import Database, set_database
from traceguard_persistence import auth as auth_store
from traceguard_persistence.models import WorkspaceRow

WS_A = "ws-iso-a"
WS_B = "ws-iso-b"


@pytest.fixture
def iso_env(tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("TG_AUTH_ENABLED", "true")
    monkeypatch.setenv("TG_AUTH_CSRF_REQUIRED", "true")
    monkeypatch.setenv("TG_LLM_PROVIDER", "")
    reset_settings_cache()
    from traceguard_api.auth import reset_auth_policy_cache
    from traceguard_api.main import create_app
    from traceguard_api.runtime import reset_runtime
    from traceguard_api.services import get_services, reset_services

    reset_auth_policy_cache()
    db = Database(f"sqlite:///{tmp_path / 'workspace-isolation.db'}")
    set_database(db)
    reset_runtime()
    reset_services()
    with db.session() as s:
        s.add(WorkspaceRow(workspace_id=WS_A, name="Workspace A", created_by="human:setup"))
        s.add(WorkspaceRow(workspace_id=WS_B, name="Workspace B", created_by="human:setup"))

    users: dict[str, str] = {}
    for ws, slug in ((WS_A, "a"), (WS_B, "b")):
        for role_name, roles in (
            ("viewer", ["viewer"]),
            ("engineer", ["qa-engineer", "viewer"]),
            ("lead", ["qa-lead", "qa-engineer", "viewer"]),
        ):
            username = f"{slug}_{role_name}"
            created = auth_store.create_user(
                db, username=username, password="password-123", roles=roles
            )
            auth_store.set_user_workspaces(db, created["user_id"], [ws])
            users[username] = ws
    admin = auth_store.create_user(
        db,
        username="platform_admin",
        password="password-123",
        roles=["platform-admin", "qa-lead", "qa-engineer", "viewer"],
    )
    auth_store.set_user_workspaces(db, admin["user_id"], ["*"])
    users["platform_admin"] = "*"

    app = create_app()
    with TestClient(app) as client:
        rt = get_services().rt
        assets = {
            "a": _seed_assets(rt, WS_A, "a"),
            "b": _seed_assets(rt, WS_B, "b"),
        }
        yield client, db, rt, assets
    reset_runtime()
    reset_services()
    reset_settings_cache()
    reset_auth_policy_cache()


def _seed_assets(rt: Any, workspace_id: str, slug: str) -> dict[str, Any]:
    """为Workspace建立真实主链：TestCase→Run(失败)→Evidence→Diagnosis→Probe Plan→Repair。"""
    from traceguard_api.services.diagnosis_service import DiagnosisService
    from traceguard_api.services.repair_service import RepairService

    tc_id = f"tcase_iso_{slug}"
    oracle_id = f"oracle_iso_{slug}"
    rt.catalog.create_oracle(
        {
            "oracle_id": oracle_id,
            "assertion_id": "a1",
            "expected_behavior": "页面可见",
            "requirement_ref": f"REQ-ISO-{slug}",
            "version": 1,
            "workspace_id": workspace_id,
        },
        approved=True,
        approved_by="human:setup",
    )
    rt.catalog.create_testcase(
        {
            "testcase_id": tc_id,
            "version": 1,
            "workspace_id": workspace_id,
            "created_at": "2026-01-01T00:00:00Z",
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
                    "expected": {},
                    "oracle_id": oracle_id,
                }
            ],
        },
        title=tc_id,
        workspace_id=workspace_id,
        review_status="DRAFT",
    )
    rt.catalog.review_testcase(
        tc_id, 1, "APPROVED", "human:setup", require_stability=False
    )
    intent = rt.catalog.create_intent(
        {
            "intent_id": f"intent_iso_{slug}",
            "title": f"隔离验收需求 {slug}",
            "description": "Workspace 隔离验收",
            "owner": "qa-team",
            "workspace_id": workspace_id,
        }
    )
    task = rt.tasks.create_task(
        tc_id,
        f"iso-task-{slug}",
        testcase_version_id=f"{tc_id}@1",
        workspace_id=workspace_id,
    )
    run = rt.tasks.create_run(task["task_id"], f"{tc_id}@1")
    lease = rt.tasks.acquire_lease(run["run_id"], "setup-worker")
    report = {
        "steps": [
            {
                "step_id": "s1",
                "status": "FAILED",
                "error": "locator timeout",
                "observation": {},
                "artifacts": {},
            }
        ],
        "assertion_outcomes": [
            {
                "assertion_id": "a1",
                "status": "FAILED",
                "required": False,
                "kind": "ui",
                "oracle_available": False,
                "reason": "missing",
            }
        ],
        "summary": {"total": 1, "passed": 0, "failed": 1},
    }
    rt.tasks.submit_result(
        run["run_id"],
        "setup-worker",
        lease["fencing_token"],
        execution_status="RUNNING",
        test_verdict="NOT_EVALUATED",
    )
    rt.tasks.submit_result(
        run["run_id"],
        "setup-worker",
        lease["fencing_token"],
        execution_status="COMPLETED",
        test_verdict="FAILED",
        report=report,
    )
    evidence_id = f"evd_iso_{slug}"
    rt.evidence.add_raw(
        evidence_id,
        "step_signals",
        f"s1:{slug}",
        workspace_id,
        {"run_id": run["run_id"], "workspace_id": workspace_id, "signals": {}},
        source_trust="TRUSTED",
        run_id=run["run_id"],
    )
    diagnosis = DiagnosisService(rt).diagnose(run["run_id"])
    plan_id = f"plan_iso_{slug}"
    rt.docs.put(
        doc_id=plan_id,
        kind="probe_plan",
        doc={
            "plan_id": plan_id,
            "run_id": run["run_id"],
            "diagnosis_id": diagnosis["diagnosis_id"],
            "workspace_id": workspace_id,
        },
        workspace_id=workspace_id,
        ref_id=run["run_id"],
    )
    proposal = RepairService(rt).propose(
        testcase_id=tc_id,
        base_version=1,
        changes=[{"path": "steps[].target", "step_index": 0, "value": {"strategy": "css", "value": "#ok"}}],
        reason="隔离验收候选",
        created_by="human:setup",
        actor="human:setup",
        diagnosis_id=diagnosis["diagnosis_id"],
        source_run_id=run["run_id"],
    )
    return {
        "workspace_id": workspace_id,
        "intent": intent,
        "testcase_id": tc_id,
        "task_id": task["task_id"],
        "run_id": run["run_id"],
        "evidence_id": evidence_id,
        "diagnosis_id": diagnosis["diagnosis_id"],
        "plan_id": plan_id,
        "proposal_id": proposal["proposal_id"],
    }


def _login(client: TestClient, username: str) -> str:
    r = client.post(
        "/api/v1/auth/login", json={"username": username, "password": "password-123"}
    )
    assert r.status_code == 200, r.text
    return r.json()["csrf_token"]


def _h(csrf: str) -> dict[str, str]:
    return {"X-CSRF-Token": csrf}


def _count(db: Database, table: str) -> int:
    with db.session() as s:
        return int(s.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one())


def _denied_audits(db: Database) -> list[dict[str, Any]]:
    with db.session() as s:
        rows = s.execute(text(
            "SELECT actor_id, action, resource_id, result FROM tg_audit_log "
            "WHERE result = 'DENIED'"
        )).mappings().all()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------- 正向验收


def test_positive_scope_read_and_cross_workspace_admin(iso_env) -> None:  # type: ignore[no-untyped-def]
    client, _db, _rt, assets = iso_env
    a = assets["a"]
    b = assets["b"]
    # 同一 TestClient 只有一个 Cookie 会话：按用户分段登录，避免会话串用。
    a_csrf = _login(client, "a_viewer")
    assert client.get(f"/api/v1/tasks/{a['task_id']}", headers=_h(a_csrf)).status_code == 200
    assert client.get(f"/api/v1/runs/{b['run_id']}", headers=_h(a_csrf)).status_code == 403

    b_csrf = _login(client, "b_viewer")
    assert client.get(f"/api/v1/runs/{a['run_id']}", headers=_h(b_csrf)).status_code == 403
    assert client.get(f"/api/v1/runs/{b['run_id']}", headers=_h(b_csrf)).status_code == 200

    admin_csrf = _login(client, "platform_admin")
    assert client.get(f"/api/v1/runs/{a['run_id']}", headers=_h(admin_csrf)).status_code == 200
    assert client.get(f"/api/v1/runs/{b['run_id']}", headers=_h(admin_csrf)).status_code == 200


def test_list_default_scope_and_explicit_workspace(iso_env) -> None:  # type: ignore[no-untyped-def]
    client, _db, _rt, assets = iso_env
    a, b = assets["a"], assets["b"]
    a_csrf = _login(client, "a_engineer")

    tasks = client.get("/api/v1/tasks", headers=_h(a_csrf)).json()
    assert any(t["task_id"] == a["task_id"] for t in tasks)
    assert all(t["task_id"] != b["task_id"] for t in tasks)
    runs = client.get("/api/v1/runs", headers=_h(a_csrf)).json()
    assert any(r["run_id"] == a["run_id"] for r in runs)
    assert all(r["run_id"] != b["run_id"] for r in runs)
    evidence = client.get("/api/v1/evidence", headers=_h(a_csrf)).json()
    assert all(str(e.get("workspace_id")) == WS_A for e in evidence)

    allowed = client.get(f"/api/v1/runs?workspace_id={WS_A}", headers=_h(a_csrf))
    assert allowed.status_code == 200
    forbidden = client.get(f"/api/v1/runs?workspace_id={WS_B}", headers=_h(a_csrf))
    assert forbidden.status_code == 403


# ---------------------------------------------------------------- 负向验收


def test_cross_workspace_detail_and_derived_reads_denied(iso_env) -> None:  # type: ignore[no-untyped-def]
    client, db, _rt, assets = iso_env
    b = assets["b"]
    a_csrf = _login(client, "a_viewer")
    before = {t: _count(db, t) for t in ("tg_task", "tg_run", "tg_evidence")}

    paths = [
        f"/api/v1/tasks/{b['task_id']}",
        f"/api/v1/tasks/{b['task_id']}/runs",
        f"/api/v1/tasks/{b['task_id']}/summary",
        f"/api/v1/runs/{b['run_id']}",
        f"/api/v1/runs/{b['run_id']}/steps",
        f"/api/v1/runs/{b['run_id']}/report",
        f"/api/v1/runs/{b['run_id']}/evidence",
        f"/api/v1/runs/{b['run_id']}/events",
        f"/api/v1/runs/{b['run_id']}/timeline",
        f"/api/v1/evidence/{b['evidence_id']}",
        f"/api/v1/evidence/{b['evidence_id']}/download",
        f"/api/v1/diagnoses/{b['diagnosis_id']}",
        f"/api/v1/probe-plans/{b['plan_id']}",
        f"/api/v1/repairs/{b['proposal_id']}",
    ]
    for path in paths:
        r = client.get(path, headers=_h(a_csrf))
        assert r.status_code == 403, (path, r.status_code, r.text)

    assert {t: _count(db, t) for t in before} == before
    denied = _denied_audits(db)
    assert denied and all(entry["result"] == "DENIED" for entry in denied)
    assert {entry["actor_id"] for entry in denied} >= {"user:a_viewer"}


def test_b_side_cross_workspace_reads_denied(iso_env) -> None:  # type: ignore[no-untyped-def]
    client, _db, _rt, assets = iso_env
    a, b = assets["a"], assets["b"]
    b_csrf = _login(client, "b_viewer")
    for path in (
        f"/api/v1/tasks/{a['task_id']}",
        f"/api/v1/runs/{a['run_id']}/report",
        f"/api/v1/evidence/{a['evidence_id']}",
        f"/api/v1/diagnoses/{a['diagnosis_id']}",
        f"/api/v1/repairs/{a['proposal_id']}",
    ):
        assert client.get(path, headers=_h(b_csrf)).status_code == 403, path
    # 正向控制：B 自己的对象可读
    assert client.get(f"/api/v1/runs/{b['run_id']}", headers=_h(b_csrf)).status_code == 200


def test_forged_body_workspace_cannot_expand_scope(iso_env) -> None:  # type: ignore[no-untyped-def]
    client, db, _rt, _assets = iso_env
    a_csrf = _login(client, "a_engineer")
    before = _count(db, "tg_intent")

    forged = client.post(
        "/api/v1/intents",
        json={
            "title": "伪造工作区",
            "description": "请求体声明 B",
            "workspace_id": WS_B,
        },
        headers=_h(a_csrf),
    )
    assert forged.status_code == 403, forged.text
    assert _count(db, "tg_intent") == before

    forged_testcase = client.post(
        "/api/v1/testcases",
        json={
            "title": "伪造用例工作区",
            "workspace_id": WS_B,
            "steps": [{"step_id": "s1", "action": "navigate", "value": "/"}],
            "assertions": [
                {
                    "assertion_id": "a1",
                    "description": "可见",
                    "kind": "ui",
                    "expected": {},
                }
            ],
        },
        headers=_h(a_csrf),
    )
    assert forged_testcase.status_code == 403, forged_testcase.text


def test_cross_workspace_task_and_run_creation_denied(iso_env) -> None:  # type: ignore[no-untyped-def]
    client, db, _rt, assets = iso_env
    b = assets["b"]
    a_csrf = _login(client, "a_engineer")
    before_tasks, before_runs = _count(db, "tg_task"), _count(db, "tg_run")

    # A 用户拿 B 的 TestCase（即便 body 声明 A）→ 服务端解析出 B 并拒绝
    r = client.post(
        "/api/v1/tasks",
        json={
            "testcase_ref": b["testcase_id"],
            "workspace_id": WS_A,
            "version": 1,
        },
        headers=_h(a_csrf),
    )
    assert r.status_code == 403, r.text

    # A 用户拿 B 的 Task 创建 Run
    r2 = client.post(f"/api/v1/tasks/{b['task_id']}/runs", headers=_h(a_csrf))
    assert r2.status_code == 403, r2.text

    assert _count(db, "tg_task") == before_tasks
    assert _count(db, "tg_run") == before_runs


def test_cross_workspace_probe_and_repair_denied(iso_env) -> None:  # type: ignore[no-untyped-def]
    client, db, _rt, assets = iso_env
    a, b = assets["a"], assets["b"]
    a_csrf = _login(client, "a_engineer")
    before_docs = _count(db, "tg_doc")

    r_probe = client.post(
        f"/api/v1/runs/{b['run_id']}/probe-plan",
        headers=_h(a_csrf),
        params={"diagnosis_id": b["diagnosis_id"]},
    )
    assert r_probe.status_code == 403, r_probe.text

    r_repair = client.post(
        "/api/v1/repairs",
        json={
            "testcase_id": a["testcase_id"],
            "base_version": 1,
            "diagnosis_id": b["diagnosis_id"],
            "source_run_id": b["run_id"],
            "changes": [
                {
                    "path": "steps[].target",
                    "step_index": 0,
                    "value": {"strategy": "css", "value": "#x"},
                }
            ],
        },
        headers=_h(a_csrf),
    )
    assert r_repair.status_code == 403, r_repair.text

    r_exec = client.post(
        "/api/v1/probes/execute",
        json={
            "probe_id": "network_trace",
            "run_id": b["run_id"],
            "workspace_id": WS_A,
        },
        headers=_h(a_csrf),
    )
    assert r_exec.status_code == 403, r_exec.text
    assert _count(db, "tg_doc") == before_docs


def test_cross_workspace_repair_operations_denied(iso_env) -> None:  # type: ignore[no-untyped-def]
    client, db, _rt, assets = iso_env
    b_prop = assets["b"]["proposal_id"]
    before = _count(db, "tg_doc")

    # A 的 qa-lead 有 repair.approve 权限，但不得操作 B 的对象
    a_lead = _login(client, "a_lead")
    for suffix, payload in (
        ("validate", None),
        ("sandbox-run", None),
        ("submit", None),
        ("approve", {"reviewer": "human:lead"}),
        ("reject", {"reviewer": "human:lead"}),
        ("reconcile", None),
    ):
        r = client.post(
            f"/api/v1/repairs/{b_prop}/{suffix}",
            json=payload,
            headers=_h(a_lead),
        )
        assert r.status_code == 403, (suffix, r.status_code, r.text)

    # platform-admin 按既定策略可跨 Workspace，因此 promote 能进入业务层，
    # 只被状态机拒绝（409），这恰好证明对象级授权没有对管理员误伤。
    admin = _login(client, "platform_admin")
    assert client.get(f"/api/v1/repairs/{b_prop}", headers=_h(admin)).status_code == 200
    r = client.post(
        f"/api/v1/repairs/{b_prop}/promote",
        json={"idempotency_key": "iso-cross-promote"},
        headers=_h(admin),
    )
    assert r.status_code == 409, r.text
    assert _count(db, "tg_doc") == before


def test_no_cross_workspace_evidence_or_object_enumeration(iso_env) -> None:  # type: ignore[no-untyped-def]
    client, db, _rt, assets = iso_env
    a, b = assets["a"], assets["b"]
    a_csrf = _login(client, "a_engineer")

    with db.session() as s:
        rows = s.execute(text(
            "SELECT evidence_id, workspace_id, run_id FROM tg_evidence"
        )).mappings().all()
    by_id = {str(r["evidence_id"]): dict(r) for r in rows}
    assert by_id[a["evidence_id"]]["workspace_id"] == WS_A
    assert by_id[b["evidence_id"]]["workspace_id"] == WS_B
    assert by_id[a["evidence_id"]]["run_id"] == a["run_id"]
    assert by_id[b["evidence_id"]]["run_id"] == b["run_id"]
    assert not any(r["workspace_id"] == WS_B and r["run_id"] == a["run_id"] for r in rows)
    assert not any(r["workspace_id"] == WS_A and r["run_id"] == b["run_id"] for r in rows)

    # 列表接口不泄露 B 的对象 ID（枚举防护）
    text_blob = str(
        client.get("/api/v1/tasks", headers=_h(a_csrf)).json()
        + client.get("/api/v1/runs", headers=_h(a_csrf)).json()
        + client.get("/api/v1/repairs", headers=_h(a_csrf)).json()
    )
    assert b["task_id"] not in text_blob
    assert b["run_id"] not in text_blob
    assert b["proposal_id"] not in text_blob
