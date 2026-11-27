"""RUN-019 WP1：TestCase 新版本/差异比较与 Oracle 历史/审批/反向引用。"""

from __future__ import annotations

from fastapi.testclient import TestClient
from traceguard_persistence import Database, set_database


def _client(work_tmp):  # type: ignore[no-untyped-def]
    from traceguard_api.main import create_app
    from traceguard_api.runtime import reset_runtime
    from traceguard_api.services import reset_services

    db = Database(f"sqlite:///{work_tmp / 'run019_versions.db'}")
    set_database(db)
    reset_runtime()
    reset_services()
    app = create_app()
    return TestClient(app)


def _testcase_payload(step_value: str) -> dict:
    return {
        "title": "版本用例",
        "steps": [{"step_id": "s1", "action": "navigate", "value": step_value}],
        "assertions": [{"assertion_id": "a1", "description": "业务成功", "kind": "business_state"}],
    }


def test_testcase_new_version_immutable_and_diff(work_tmp):  # type: ignore[no-untyped-def]
    client = _client(work_tmp)
    created = client.post("/api/v1/testcases", json=_testcase_payload("http://one"))
    assert created.status_code == 201, created.text
    tc_id = created.json()["testcase_id"]
    assert created.json()["version"] == 1

    v2 = client.post(
        f"/api/v1/testcases/{tc_id}/versions",
        json={"steps": [{"step_id": "s1", "action": "navigate", "value": "http://two"}]},
    )
    assert v2.status_code == 201, v2.text
    assert v2.json()["version"] == 2

    v1_doc = client.get(f"/api/v1/testcases/{tc_id}/versions/1").json()
    v2_doc = client.get(f"/api/v1/testcases/{tc_id}/versions/2").json()
    assert v1_doc["steps"][0]["value"] == "http://one"
    assert v2_doc["steps"][0]["value"] == "http://two"
    listed = client.get(f"/api/v1/testcases/{tc_id}/versions").json()
    assert len(listed) == 2 and all(v["immutable"] is True for v in listed)

    diff = client.get(f"/api/v1/testcases/{tc_id}/versions/1/diff/2")
    assert diff.status_code == 200, diff.text
    assert "steps" in diff.json()["changed_fields"]
    assert "http://two" in diff.json()["diff"]


def test_oracle_versions_approval_and_references(work_tmp):  # type: ignore[no-untyped-def]
    client = _client(work_tmp)
    created = client.post(
        "/api/v1/oracles",
        json={
            "oracle_id": "oracle_run019_1",
            "assertion_id": "a1",
            "expected_behavior": "项目创建成功",
            "requirement_ref": "REQ-1",
            "approved": True,
            "approved_by": "role:qa-lead",
        },
    )
    assert created.status_code == 201, created.text

    versions = client.get("/api/v1/oracles/oracle_run019_1/versions")
    assert versions.status_code == 200, versions.text
    assert [v["version"] for v in versions.json()] == [1]

    approved = client.post(
        "/api/v1/oracles/oracle_run019_1/versions/1/approve",
        json={"approved_by": "role:qa-lead", "note": "复核通过"},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["version"] == 2
    assert approved.json()["approved"] is True
    assert len(approved.json()["approval_history"]) == 2

    rejected = client.post(
        "/api/v1/oracles/oracle_run019_1/versions/1/approve",
        json={"approved_by": "agent:llm", "note": "机器自批"},
    )
    assert rejected.status_code == 403, rejected.text

    tc = client.post("/api/v1/testcases", json=_testcase_payload("http://one")).json()
    bound = client.post(
        f"/api/v1/testcases/{tc['testcase_id']}/versions/1/oracle-bindings",
        json={
            "bindings": {"a1": {"oracle_id": "oracle_run019_1", "oracle_version": 2}},
            "actor": "role:qa-lead",
        },
    )
    assert bound.status_code == 200, bound.text
    refs = client.get("/api/v1/oracles/oracle_run019_1/references?version=2")
    assert refs.status_code == 200, refs.text
    assert any(r["testcase_id"] == tc["testcase_id"] for r in refs.json())


def test_oracle_cross_workspace_id_reuse_rejected(work_tmp):  # type: ignore[no-untyped-def]
    client = _client(work_tmp)
    ws = client.post("/api/v1/workspaces", json={"name": "Other"})
    assert ws.status_code == 201, ws.text
    wid = ws.json()["workspace_id"]
    first = client.post(
        "/api/v1/oracles",
        json={
            "oracle_id": "oracle_run019_cross",
            "assertion_id": "a1",
            "expected_behavior": "默认空间",
            "requirement_ref": "REQ-1",
        },
    )
    assert first.status_code == 201, first.text
    second = client.post(
        "/api/v1/oracles",
        json={
            "oracle_id": "oracle_run019_cross",
            "assertion_id": "a1",
            "expected_behavior": "跨空间复用",
            "requirement_ref": "REQ-1",
            "workspace_id": wid,
        },
    )
    assert second.status_code == 403, second.text
