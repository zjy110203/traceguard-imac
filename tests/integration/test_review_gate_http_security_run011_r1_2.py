"""RUN-011-R1.2 WP1：公共 HTTP 审核接口不得绕过稳定性回放门禁。

覆盖指令 §2.3：
- require_stability=false → 422，用例仍 PENDING；
- Compiler 候选无合法回放 → 412；合法回放 → 200；
- 人工编写用例的 EXEMPT_HUMAN_AUTHORED 语义保留；
- HTTP 审核记录中不出现 SKIPPED_BY_CALLER；
- 服务层 require_stability=False 的受信任内部路径保持可用（历史迁移夹具）。
"""

from __future__ import annotations

import json
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from traceguard_core.config import reset_settings_cache
from traceguard_persistence import Database, set_database
from traceguard_persistence import auth as auth_store


@pytest.fixture
def lead_client(tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
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
    db = Database(f"sqlite:///{tmp_path / 'review-gate-r12.db'}")
    set_database(db)
    reset_runtime()
    reset_services()
    app = create_app()
    with TestClient(app) as client:
        created = auth_store.create_user(
            db, username="lead", password="password-123", roles=["qa-lead"]
        )
        auth_store.set_user_workspaces(db, created["user_id"], ["default"])
        login = client.post(
            "/api/v1/auth/login", json={"username": "lead", "password": "password-123"}
        )
        assert login.status_code == 200, login.text
        yield client, db, {"X-CSRF-Token": login.json()["csrf_token"]}
    reset_runtime()
    reset_services()
    reset_settings_cache()
    reset_auth_policy_cache()


def _oracle(client: TestClient, headers: dict[str, str], assertion_id: str = "a1") -> str:
    oracle_id = f"oracle_gate_{uuid.uuid4().hex[:10]}"
    r = client.post(
        "/api/v1/oracles",
        json={
            "oracle_id": oracle_id,
            "assertion_id": assertion_id,
            "expected_behavior": "成功提示必须出现",
            "requirement_ref": "REQ-GATE-R12",
            "version": 1,
            "approved": True,
            "approved_by": "role:qa-lead",
        },
        headers=headers,
    )
    assert r.status_code == 201, r.text
    return oracle_id


def _testcase(
    client: TestClient,
    headers: dict[str, str],
    *,
    compile_source: str = "compiler",
) -> str:
    oracle_id = _oracle(client, headers)
    tc_id = f"tcase_gate_{uuid.uuid4().hex[:10]}"
    r = client.post(
        "/api/v1/testcases",
        json={
            "testcase_id": tc_id,
            "title": "R1.2 门禁用例",
            "compile_source": compile_source,
            "steps": [{"step_id": "s1", "action": "navigate", "value": "http://127.0.0.1:8011/"}],
            "assertions": [
                {
                    "assertion_id": "a1",
                    "description": "成功提示",
                    "kind": "ui",
                    "required": True,
                    "expected": {"test_id": "banner-success"},
                    "oracle_id": oracle_id,
                    "oracle_version": 1,
                }
            ],
        },
        headers=headers,
    )
    assert r.status_code == 201, r.text
    return tc_id


def _review(client: TestClient, headers: dict[str, str], tc_id: str, payload: dict) -> object:
    return client.post(
        f"/api/v1/testcases/{tc_id}/versions/1/review", json=payload, headers=headers
    )


def _stored_review_history(client: TestClient, tc_id: str) -> list[dict]:
    version = client.get(f"/api/v1/testcases/{tc_id}/versions/1").json()
    return list(version.get("review_history") or [])


def test_http_review_rejects_require_stability_false(lead_client):  # type: ignore[no-untyped-def]
    client, _, headers = lead_client
    tc_id = _testcase(client, headers)
    r = _review(
        client,
        headers,
        tc_id,
        {
            "decision": "APPROVED",
            "reviewer": "role:qa-lead",
            "require_stability": False,
        },
    )
    assert r.status_code == 422, r.text
    assert client.get(f"/api/v1/testcases/{tc_id}/versions/1").json()["review_status"] == "PENDING"
    assert _stored_review_history(client, tc_id) == []


def test_compiler_candidate_without_replay_is_412_over_http(lead_client):  # type: ignore[no-untyped-def]
    client, _, headers = lead_client
    tc_id = _testcase(client, headers, compile_source="compiler")
    r = _review(client, headers, tc_id, {"decision": "APPROVED", "reviewer": "role:qa-lead"})
    assert r.status_code == 412, r.text
    assert "STABILITY_REPLAY_REQUIRED" in r.json()["detail"]["message"]
    assert client.get(f"/api/v1/testcases/{tc_id}/versions/1").json()["review_status"] == "PENDING"


def test_compiler_candidate_with_valid_replay_is_approved(lead_client):  # type: ignore[no-untyped-def]
    client, _, headers = lead_client
    tc_id = _testcase(client, headers, compile_source="compiler")
    from traceguard_api.services import get_services
    from traceguard_persistence.models import RunRow

    svc = get_services().stability
    replay = svc.start(tc_id, 1, repeats=2)
    for record, verdict in zip(replay["records"], ["PASSED", "PASSED"], strict=False):
        with get_services().rt.db.session() as s:
            row = s.get(RunRow, record["run_id"])
            row.test_verdict = verdict
            row.execution_status = "COMPLETED"
            row.report_json = {"steps": [], "assertion_outcomes": []}
    refreshed = svc.refresh(replay["replay_id"])
    assert refreshed["promotable"] is True

    r = _review(client, headers, tc_id, {"decision": "APPROVED", "reviewer": "role:qa-lead"})
    assert r.status_code == 200, r.text
    history = _stored_review_history(client, tc_id)
    assert history[-1]["stability_policy"] == "ENFORCED"
    assert history[-1]["stability_evidence"]["replay_id"] == replay["replay_id"]


def test_human_authored_exemption_unchanged_over_http(lead_client):  # type: ignore[no-untyped-def]
    client, _, headers = lead_client
    tc_id = _testcase(client, headers, compile_source="human")
    r = _review(client, headers, tc_id, {"decision": "APPROVED", "reviewer": "role:qa-lead"})
    assert r.status_code == 200, r.text
    history = _stored_review_history(client, tc_id)
    assert history[-1]["stability_policy"] == "EXEMPT_HUMAN_AUTHORED"


def test_no_skipped_by_caller_review_history_or_audit(lead_client):  # type: ignore[no-untyped-def]
    client, db, headers = lead_client
    tc_id = _testcase(client, headers, compile_source="human")
    bad = _review(
        client,
        headers,
        tc_id,
        {
            "decision": "APPROVED",
            "reviewer": "role:qa-lead",
            "require_stability": False,
        },
    )
    assert bad.status_code == 422
    ok = _review(client, headers, tc_id, {"decision": "APPROVED", "reviewer": "role:qa-lead"})
    assert ok.status_code == 200
    assert all(
        h.get("stability_policy") != "SKIPPED_BY_CALLER"
        for h in _stored_review_history(client, tc_id)
    )
    with db.session() as s:
        rows = s.execute(
            text(
                "SELECT before_json, after_json FROM tg_audit_log WHERE action = 'testcase.review'"
            )
        ).all()
    assert not any(
        "SKIPPED_BY_CALLER" in json.dumps(tuple(row), ensure_ascii=False) for row in rows
    )


def test_trusted_internal_migration_exemption_still_works(lead_client):  # type: ignore[no-untyped-def]
    """历史迁移夹具仍可通过服务端内部路径豁免，HTTP 入口无法使用。"""
    client, _, headers = lead_client
    tc_id = _testcase(client, headers, compile_source="compiler")
    from traceguard_api.services import get_services

    result = get_services().rt.catalog.review_testcase(
        tc_id, 1, "APPROVED", reviewer="human:migration", require_stability=False
    )
    assert result["review_status"] == "APPROVED"
    history = _stored_review_history(client, tc_id)
    assert history[-1]["stability_policy"] == "SKIPPED_BY_CALLER"
    assert all(h.get("reviewer") != "role:qa-lead" for h in history)
