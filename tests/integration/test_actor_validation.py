"""RUN-007 WP1-03：审批主体校验统一（参数化非法主体）。

原则：主体类型校验 ≠ 身份认证。本测试只验证"非人类主体不得充当审批人"，
不声称系统已经具备生产级认证/RBAC（D-01 / P08 仍未批准）。
"""

from __future__ import annotations

import pytest
from traceguard_persistence import Database, set_database

NON_HUMAN_ACTORS = [
    "agent",
    "agent:gpt",
    "Agent:auto-fix",
    "agents",
    "ai",
    "llm",
    "llm:deepseek",
    "bot:repairer",
    "system:promotion",
    "service:worker",
    "worker-1",
    "model:x",
    "robot",
    "",
    "   ",
]

HUMAN_ACTORS = ["role:qa-lead", "human:ann", "qa-team", "org:agents-team"]


@pytest.fixture(scope="module")
def client(work_tmp):  # type: ignore[no-untyped-def]
    from fastapi.testclient import TestClient
    from traceguard_api.main import create_app
    from traceguard_api.runtime import reset_runtime
    from traceguard_api.services import reset_services

    db = Database(f"sqlite:///{work_tmp / 'actor.db'}")
    set_database(db)
    reset_runtime()
    reset_services()
    app = create_app()
    with TestClient(app) as c:
        yield c
    reset_runtime()
    reset_services()


def _new_testcase(client, name: str) -> dict:  # type: ignore[no-untyped-def]
    r = client.post(
        "/api/v1/testcases",
        json={
            "title": f"主体校验-{name}",
            "steps": [{"step_id": "s1", "action": "navigate", "value": "/projects"}],
            "assertions": [
                {
                    "assertion_id": "a1",
                    "description": "标题存在",
                    "kind": "ui",
                    "required": True,
                    "expected": {"test_id": "page-title"},
                }
            ],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


# --------------------------------------------------------------- 用例审核
@pytest.mark.parametrize("actor", NON_HUMAN_ACTORS)
def test_review_rejects_non_human(client, actor: str) -> None:  # type: ignore[no-untyped-def]
    tc = _new_testcase(client, f"review-{abs(hash(actor))}")
    r = client.post(
        f"/api/v1/testcases/{tc['testcase_id']}/versions/1/review",
        json={"decision": "APPROVED", "reviewer": actor},
    )
    assert r.status_code in (403, 412), r.text


# ------------------------------------------------------------- Oracle 审批
@pytest.mark.parametrize("actor", NON_HUMAN_ACTORS)
def test_oracle_approval_rejects_non_human(client, actor: str) -> None:  # type: ignore[no-untyped-def]
    r = client.post(
        "/api/v1/oracles",
        json={
            "oracle_id": f"oracle_actor_{abs(hash(actor))}",
            "assertion_id": "a1",
            "expected_behavior": "标题必须存在",
            "requirement_ref": "REQ-ACTOR-1",
            "approved": True,
            "approved_by": actor,
        },
    )
    assert r.status_code in (403, 412), r.text


# ------------------------------------------------------------- Oracle 绑定
@pytest.mark.parametrize("actor", NON_HUMAN_ACTORS)
def test_oracle_binding_rejects_non_human(client, actor: str) -> None:  # type: ignore[no-untyped-def]
    tc = _new_testcase(client, f"bind-{abs(hash(actor))}")
    oracle_id = f"oracle_bind_{abs(hash(actor))}"
    client.post(
        "/api/v1/oracles",
        json={
            "oracle_id": oracle_id,
            "assertion_id": "a1",
            "expected_behavior": "标题必须存在",
            "requirement_ref": "REQ-ACTOR-2",
            "approved": True,
            "approved_by": "role:qa-lead",
        },
    )
    r = client.post(
        f"/api/v1/testcases/{tc['testcase_id']}/versions/1/oracle-bindings",
        json={"bindings": {"a1": {"oracle_id": oracle_id}}, "actor": actor},
    )
    assert r.status_code in (403, 412), r.text


# --------------------------------------------------------------- 修复审批
_REPAIR_TC: dict[str, str] = {}


def _repair_testcase(client) -> str:  # type: ignore[no-untyped-def]
    if "id" not in _REPAIR_TC:
        tc = _new_testcase(client, "repair-base")
        _REPAIR_TC["id"] = tc["testcase_id"]
    return _REPAIR_TC["id"]


def _proposal(client) -> str:  # type: ignore[no-untyped-def]
    tc_id = _repair_testcase(client)
    r = client.post(
        "/api/v1/repairs",
        json={
            "testcase_id": tc_id,
            "base_version": 1,
            "changes": [{"path": "steps[].value", "step_index": 0, "value": "/projects?healed=1"}],
            "reason": "主体校验测试",
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["proposal_id"]


@pytest.mark.parametrize("actor", NON_HUMAN_ACTORS)
def test_repair_approve_rejects_non_human(client, actor: str) -> None:  # type: ignore[no-untyped-def]
    pid = _proposal(client)
    r = client.post(f"/api/v1/repairs/{pid}/approve", json={"reviewer": actor, "note": "x"})
    assert r.status_code in (403, 409, 412), r.text
    assert r.status_code != 200


@pytest.mark.parametrize("actor", NON_HUMAN_ACTORS)
def test_repair_reject_rejects_non_human(client, actor: str) -> None:  # type: ignore[no-untyped-def]
    pid = _proposal(client)
    r = client.post(f"/api/v1/repairs/{pid}/reject", json={"reviewer": actor, "note": "x"})
    assert r.status_code in (403, 409, 412), r.text
    assert r.status_code != 200


@pytest.mark.parametrize("actor", NON_HUMAN_ACTORS)
def test_repair_rollback_rejects_non_human(client, actor: str) -> None:  # type: ignore[no-untyped-def]
    pid = _proposal(client)
    r = client.post(f"/api/v1/repairs/{pid}/rollback", json={"operator": actor, "reason": "x"})
    assert r.status_code in (403, 409, 412), r.text
    assert r.status_code != 200


# --------------------------------------------------------- Disposition 复核
@pytest.mark.parametrize("actor", NON_HUMAN_ACTORS)
def test_disposition_review_rejects_non_human(client, actor: str) -> None:  # type: ignore[no-untyped-def]
    from traceguard_api.services import get_services

    rt = get_services().rt
    did = f"disp_actor_{abs(hash(actor))}"
    rt.docs.put(
        doc_id=did,
        kind="disposition",
        doc={"workspace_id": "default", "diagnosis_id": "diag_x", "review_status": "PENDING"},
        workspace_id="default",
        status="PENDING",
    )
    r = client.post(
        f"/api/v1/dispositions/{did}/review", json={"review_status": "APPROVED", "reviewer": actor}
    )
    assert r.status_code in (403, 412), r.text


# ------------------------------------------------------------ 合法人类主体
@pytest.mark.parametrize("actor", HUMAN_ACTORS)
def test_disposition_review_accepts_named_human(client, actor: str) -> None:  # type: ignore[no-untyped-def]
    from traceguard_api.services import get_services

    rt = get_services().rt
    did = f"disp_ok_{abs(hash(actor))}"
    rt.docs.put(
        doc_id=did,
        kind="disposition",
        doc={"workspace_id": "default", "diagnosis_id": "diag_y", "review_status": "PENDING"},
        workspace_id="default",
        status="PENDING",
    )
    r = client.post(
        f"/api/v1/dispositions/{did}/review", json={"review_status": "APPROVED", "reviewer": actor}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["review_status"] == "APPROVED"
    history = body["review_history"][-1]
    assert history["reviewer"] == actor
    assert history["at"]
