"""RUN-019 WP1：TestIntent 编辑、状态机与审批权限映射。"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from traceguard_core.config import reset_settings_cache
from traceguard_persistence import Database, set_database


@pytest.fixture
def intent_client(tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("TG_AUTH_ENABLED", "true")
    monkeypatch.setenv("TG_AUTH_CSRF_REQUIRED", "true")
    monkeypatch.setenv("TG_LLM_PROVIDER", "")
    reset_settings_cache()
    from traceguard_api.auth import reset_auth_policy_cache
    from traceguard_api.main import create_app
    from traceguard_api.runtime import reset_runtime
    from traceguard_api.services import reset_services
    from traceguard_persistence import auth as auth_store

    reset_auth_policy_cache()
    db = Database(f"sqlite:///{tmp_path / 'run019_intent.db'}")
    set_database(db)
    reset_runtime()
    reset_services()
    app = create_app()
    for name, roles in (
        ("admin", ["platform-admin", "qa-lead", "qa-engineer", "viewer"]),
        ("engineer", ["qa-engineer", "viewer"]),
        ("lead", ["qa-lead", "qa-engineer", "viewer"]),
    ):
        auth_store.create_user(db, username=name, password="password-123", roles=roles)
    with TestClient(app) as client:
        yield client, app
    reset_runtime()
    reset_services()
    reset_settings_cache()
    reset_auth_policy_cache()


def _login(client: TestClient, name: str) -> str:
    r = client.post("/api/v1/auth/login", json={"username": name, "password": "password-123"})
    assert r.status_code == 200, r.text
    return r.json()["csrf_token"]


def _headers(csrf: str) -> dict[str, str]:
    return {"X-CSRF-Token": csrf}


def _create(client: TestClient, csrf: str) -> dict[str, Any]:
    r = client.post(
        "/api/v1/intents",
        json={
            "title": "需求标题",
            "description": "原始描述",
            "requirement_refs": ["REQ-1"],
        },
        headers=_headers(csrf),
    )
    assert r.status_code == 201, r.text
    return r.json()


def test_submit_and_approve_are_separate_permissions(
    intent_client,  # type: ignore[no-untyped-def]
) -> None:
    client, app = intent_client
    engineer = TestClient(app)
    eng_csrf = _login(engineer, "engineer")
    intent = _create(engineer, eng_csrf)

    submitted = engineer.post(
        f"/api/v1/intents/{intent['intent_id']}/submit", headers=_headers(eng_csrf)
    )
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["status"] == "REVIEW"

    denied = engineer.post(
        f"/api/v1/intents/{intent['intent_id']}/approve", headers=_headers(eng_csrf)
    )
    assert denied.status_code == 403, denied.text
    denied_legacy = engineer.post(
        f"/api/v1/intents/{intent['intent_id']}/status",
        json={"status": "APPROVED"},
        headers=_headers(eng_csrf),
    )
    assert denied_legacy.status_code == 403, denied_legacy.text

    lead = TestClient(app)
    lead_csrf = _login(lead, "lead")
    approved = lead.post(
        f"/api/v1/intents/{intent['intent_id']}/approve", headers=_headers(lead_csrf)
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "APPROVED"


def test_illegal_transition_rejected(intent_client):  # type: ignore[no-untyped-def]
    client, _ = intent_client
    admin_csrf = _login(client, "admin")
    intent = _create(client, admin_csrf)
    r = client.post(
        f"/api/v1/intents/{intent['intent_id']}/status",
        json={"status": "APPROVED"},
        headers=_headers(admin_csrf),
    )
    assert r.status_code == 412, r.text
    assert "INVALID_INTENT_TRANSITION" in r.text


def test_edit_preserves_source_and_resets_review(
    intent_client,  # type: ignore[no-untyped-def]
) -> None:
    client, _ = intent_client
    admin_csrf = _login(client, "admin")
    intent = _create(client, admin_csrf)
    client.post(f"/api/v1/intents/{intent['intent_id']}/submit", headers=_headers(admin_csrf))
    patched = client.patch(
        f"/api/v1/intents/{intent['intent_id']}",
        json={"title": "新标题", "requirement_refs": ["REQ-2"]},
        headers=_headers(admin_csrf),
    )
    assert patched.status_code == 200, patched.text
    body = patched.json()
    assert body["version"] == 2
    assert body["status"] == "DRAFT"
    assert body["original_requirement_refs"] == ["REQ-1"]
    assert body["original_description"] == "原始描述"
    assert body["requirement_refs"] == ["REQ-2"]


def test_edit_rejected_when_linked_testcases_exist(
    intent_client,  # type: ignore[no-untyped-def]
) -> None:
    client, _ = intent_client
    admin_csrf = _login(client, "admin")
    intent = _create(client, admin_csrf)
    client.post(f"/api/v1/intents/{intent['intent_id']}/submit", headers=_headers(admin_csrf))
    client.post(f"/api/v1/intents/{intent['intent_id']}/approve", headers=_headers(admin_csrf))
    tc = client.post(
        "/api/v1/testcases",
        json={
            "title": "用例",
            "intent_ref": intent["intent_id"],
            "steps": [{"step_id": "s1", "action": "navigate", "value": "http://x"}],
            "assertions": [{"assertion_id": "a1", "description": "ok", "kind": "ui"}],
        },
        headers=_headers(admin_csrf),
    )
    assert tc.status_code == 201, tc.text
    patched = client.patch(
        f"/api/v1/intents/{intent['intent_id']}",
        json={"title": "改标题"},
        headers=_headers(admin_csrf),
    )
    assert patched.status_code == 412, patched.text
    assert "INTENT_HAS_LINKED_TESTCASES" in patched.text
