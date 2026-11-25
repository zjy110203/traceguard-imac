"""RUN-012 WP1：PostgreSQL 16 上的 Workspace 隔离、权限过滤、审核 CAS 与审计链。

沿用 Nightly 的 `TG_TEST_POSTGRES_URL`，每次创建独立数据库；未配置时显式 skip，
不把 skip 写成通过。本文件只验证平台级不变量，不改变任何业务规则。
"""

from __future__ import annotations

import os
import uuid
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from traceguard_core.errors import AppError, ErrorCode
from traceguard_persistence import Database
from traceguard_persistence.auth import (
    create_user,
    get_user_by_username,
    record_audit,
    set_user_workspaces,
    user_workspace_scope,
    verify_audit_chain,
    workspace_in_scope,
)
from traceguard_persistence.catalog import CatalogService

PG_URL = os.environ.get("TG_TEST_POSTGRES_URL", "")
pytestmark = pytest.mark.skipif(not PG_URL, reason="TG_TEST_POSTGRES_URL 未配置（本地无 PG）")


def _admin_engine() -> Any:
    return create_engine(
        make_url(PG_URL).set(database="postgres"), isolation_level="AUTOCOMMIT", future=True
    )


@pytest.fixture()
def pg_url() -> Any:
    name = f"tg_r12ws_{uuid.uuid4().hex[:10]}"
    admin = _admin_engine()
    with admin.connect() as c:
        c.execute(text(f'CREATE DATABASE "{name}"'))
    admin.dispose()
    url = make_url(PG_URL).set(database=name).render_as_string(hide_password=False)
    yield url
    admin = _admin_engine()
    with admin.connect() as c:
        c.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    admin.dispose()


@pytest.fixture()
def db(pg_url: str) -> Any:
    database = Database(pg_url)
    database.create_schema()
    yield database
    database.dispose()


def _insert_intent(db: Database, intent_id: str, workspace: str) -> None:
    with db.session() as s:
        s.execute(
            text(
                "INSERT INTO tg_intent (intent_id, workspace_id, title, status, version, "
                "doc, created_at, updated_at) VALUES (:id, :ws, :title, 'DRAFT', 1, "
                "'{}'::json, '2026-11-25T00:00:00Z', '2026-11-25T00:00:00Z')"
            ),
            {"id": intent_id, "ws": workspace, "title": f"intent {workspace}"},
        )


def test_workspace_query_and_permission_filter_on_pg(db: Database) -> None:
    _insert_intent(db, "intent_wsa", "ws-a")
    _insert_intent(db, "intent_wsb", "ws-b")
    alice_user = create_user(
        db, username="alice", password="local-test-only", roles=["qa-engineer"]
    )
    bob_user = create_user(db, username="bob", password="local-test-only", roles=["qa-engineer"])
    set_user_workspaces(db, alice_user["user_id"], ["ws-a"])
    set_user_workspaces(db, bob_user["user_id"], ["ws-b"])
    alice = user_workspace_scope(get_user_by_username(db, "alice") or {})
    bob = user_workspace_scope(get_user_by_username(db, "bob") or {})

    def visible(scope: list[str]) -> list[str]:
        allowed: list[str] = []
        with db.session() as s:
            rows = s.execute(text("SELECT intent_id, workspace_id FROM tg_intent")).all()
        for intent_id, ws in rows:
            if workspace_in_scope(scope, str(ws)):
                allowed.append(str(intent_id))
        return sorted(allowed)

    assert alice == ["ws-a"] and bob == ["ws-b"]
    assert visible(alice) == ["intent_wsa"]
    assert visible(bob) == ["intent_wsb"]
    assert not workspace_in_scope(alice, "ws-b")
    assert not workspace_in_scope(bob, "ws-a")


def test_audit_chain_intact_on_pg(db: Database) -> None:
    for i in range(3):
        record_audit(
            db,
            actor_id="human:qa-lead",
            action="test.workspace.isolation",
            role="qa-lead",
            resource_type="workspace",
            resource_id=f"ws-{i}",
            after={"allowed": i % 2 == 0},
        )
    result = verify_audit_chain(db)
    assert result["entries"] == 3
    assert result["intact"] is True
    assert result["broken"] == []


def test_review_cas_rejects_stale_revision_on_pg(db: Database, monkeypatch: Any) -> None:
    payload = {
        "testcase_id": "tcase_pg_review_cas",
        "version": 1,
        "steps": [{"step_id": "s1", "action": "navigate", "value": "/projects"}],
        "assertions": [
            {
                "assertion_id": "a1",
                "description": "页面可见",
                "kind": "ui",
                "required": True,
                "expected": {"test_id": "project-rows"},
            }
        ],
        "compile_source": "human",
        "created_at": "2026-11-25T00:00:00Z",
    }
    catalog = CatalogService(db)
    created = catalog.create_testcase(payload, workspace_id="ws-a", review_status="PENDING")
    assert created["version"] == 1
    catalog.create_oracle(
        {
            "oracle_id": "oracle_pg_review",
            "assertion_id": "a1",
            "expected_behavior": "项目列表可见",
            "requirement_ref": "REQ-PG-REVIEW",
            "version": 1,
            "workspace_id": "ws-a",
        },
        approved=True,
        approved_by="human:qa-lead",
    )
    catalog.bind_oracles(
        "tcase_pg_review_cas",
        1,
        {"a1": {"oracle_id": "oracle_pg_review"}},
        actor="human:qa-lead",
    )

    original_gate = catalog._oracle_gate

    def racing_gate(s: Any, doc: Any, *, workspace_id: Any) -> Any:
        """模拟另一事务在校验期间推进修订号，确定性验证 PG 行级 CAS。"""
        s.execute(
            text(
                "UPDATE tg_testcase_version SET review_revision = review_revision + 1 "
                "WHERE id = 'tcase_pg_review_cas@1'"
            )
        )
        return original_gate(s, doc, workspace_id=workspace_id)

    monkeypatch.setattr(catalog, "_oracle_gate", racing_gate)
    with pytest.raises(AppError) as exc:
        catalog.review_testcase("tcase_pg_review_cas", 1, "APPROVED", reviewer="human:qa-lead")
    assert exc.value.code is ErrorCode.CONFLICT
    assert "CONCURRENT_REVIEW_CONFLICT" in exc.value.message
