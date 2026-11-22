"""API 集成测试：资产 / 审核门禁 / 证据下载 / 修复规则 / 诊断前置条件。

不启动真实浏览器（浏览器链路由 tests/e2e/test_playwright_real.py 覆盖）。
"""

from __future__ import annotations

import base64
import time

import pytest
from traceguard_core.errors import AppError
from traceguard_persistence import Database, set_database


@pytest.fixture(scope="module")
def client(work_tmp):  # type: ignore[no-untyped-def]
    from fastapi.testclient import TestClient
    from traceguard_api.main import create_app
    from traceguard_api.runtime import reset_runtime
    from traceguard_api.services import reset_services

    db = Database(f"sqlite:///{work_tmp / 'api.db'}")
    set_database(db)
    reset_runtime()
    reset_services()
    app = create_app()
    with TestClient(app) as c:
        yield c
    reset_runtime()
    reset_services()


def _intent(c) -> dict:  # type: ignore[no-untyped-def]
    r = c.post(
        "/api/v1/intents",
        json={"title": "集成测试需求", "description": "通过 API 创建用例并校验门禁"},
    )
    assert r.status_code == 201, r.text
    return r.json()


def _testcase(c, intent_id: str) -> dict:  # type: ignore[no-untyped-def]
    payload = {
        "title": "API 创建用例",
        "intent_ref": intent_id,
        "steps": [
            {"step_id": "s1", "action": "navigate", "value": "/projects"},
            {
                "step_id": "s2",
                "action": "assert",
                "target": {"strategy": "test_id", "value": "page-title"},
                "value": "page-title",
            },
        ],
        "assertions": [
            {
                "assertion_id": "a1",
                "description": "标题存在",
                "kind": "ui",
                "expected": {"test_id": "page-title"},
            }
        ],
        "run_config": {"base_url": "http://127.0.0.1:8001"},
    }
    r = c.post("/api/v1/testcases", json=payload)
    assert r.status_code == 201, r.text
    return r.json()


def test_health_and_catalog_flow(client) -> None:  # type: ignore[no-untyped-def]
    h = client.get("/api/v1/system/health")
    assert h.status_code == 200
    assert h.json()["database"]["ok"] is True

    it = _intent(client)
    assert any(x["intent_id"] == it["intent_id"] for x in client.get("/api/v1/intents").json())
    assert (
        client.post(
            f"/api/v1/intents/{it['intent_id']}/status", json={"status": "APPROVED"}
        ).status_code
        == 200
    )

    tc = _testcase(client, it["intent_id"])
    assert tc["review_status"] == "PENDING"
    # 未审核版本禁止正式执行
    blocked = client.post(f"/api/v1/testcases/{tc['testcase_id']}/execute", json={"sync": False})
    assert blocked.status_code == 412
    assert blocked.json()["detail"]["code"] == "TESTCASE_NOT_APPROVED"

    # Agent 不能审核
    bad = client.post(
        f"/api/v1/testcases/{tc['testcase_id']}/versions/1/review",
        json={"decision": "APPROVED", "reviewer": "agent"},
    )
    assert bad.status_code in (403, 412)

    ok = client.post(
        f"/api/v1/testcases/{tc['testcase_id']}/versions/1/review",
        json={"decision": "APPROVED", "reviewer": "role:qa-lead"},
    )
    assert ok.status_code == 200 and ok.json()["review_status"] == "APPROVED"

    queued = client.post(f"/api/v1/testcases/{tc['testcase_id']}/execute", json={"sync": False})
    assert queued.status_code == 201
    assert queued.json()["status"] == "QUEUED"
    assert (
        client.get(f"/api/v1/runs/{queued.json()['run_id']}").json()["test_verdict"]
        == "NOT_EVALUATED"
    )


def test_oracle_agent_cannot_approve(client) -> None:  # type: ignore[no-untyped-def]
    payload = {
        "oracle_id": "oracle_api_test",
        "assertion_id": "a1",
        "expected_behavior": "页面标题存在",
        "requirement_ref": "REQ-API-1",
        "approved": True,
        "approved_by": "agent",
    }
    r = client.post("/api/v1/oracles", json=payload)
    assert r.status_code == 412
    payload["approved_by"] = "role:qa-lead"
    assert client.post("/api/v1/oracles", json=payload).status_code == 201


def test_evidence_and_artifact_download(client) -> None:  # type: ignore[no-untyped-def]
    from traceguard_api.services import get_services

    rt = get_services().rt
    data = b"\x89PNG\r\n\x1a\nfake-png-bytes"
    art = rt.artifacts.put(
        data, workspace_id="default", media_type="image/png", original_name="x.png"
    )
    evd_id = f"evd_apitest_{int(time.time() * 1000)}"
    rt.evidence.add_raw(
        evd_id,
        "browser",
        "api:test",
        "default",
        {"step_id": "s1", "artifact": art, "password": "secret"},
        run_id="run_api_test",
        attachment_ref=art["artifact_id"],
    )
    got = client.get(f"/api/v1/evidence/{evd_id}")
    assert got.status_code == 200
    assert got.json()["content"]["password"] == "***REDACTED***"
    dl = client.get(f"/api/v1/evidence/{evd_id}/download")
    assert dl.status_code == 200 and dl.content == data
    assert dl.headers["x-traceguard-sha256"] == art["sha256"]
    listed = client.get("/api/v1/evidence?run_id=run_api_test").json()
    assert any(e["evidence_id"] == evd_id for e in listed)


def test_repair_rules_and_lifecycle_gates(client) -> None:  # type: ignore[no-untyped-def]
    it = _intent(client)
    tc = _testcase(client, it["intent_id"])
    client.post(
        f"/api/v1/testcases/{tc['testcase_id']}/versions/1/review",
        json={"decision": "APPROVED", "reviewer": "role:qa-lead"},
    )

    # 触碰断言 → 直接拒绝（不进入 DRAFT）
    forbidden = client.post(
        "/api/v1/repairs",
        json={
            "testcase_id": tc["testcase_id"],
            "base_version": 1,
            "changes": [{"path": "assertions[].expected", "value": {"test_id": "hacked"}}],
        },
    )
    assert forbidden.status_code == 412

    ok = client.post(
        "/api/v1/repairs",
        json={
            "testcase_id": tc["testcase_id"],
            "base_version": 1,
            "changes": [
                {
                    "path": "steps[].target",
                    "step_index": 1,
                    "value": {"strategy": "test_id", "value": "page-title-v2"},
                }
            ],
            "reason": "定位值更新",
        },
    )
    assert ok.status_code == 201, ok.text
    doc = ok.json()
    assert doc["status"] in {"VALIDATED", "DRAFT"}
    proposal = doc["proposal_id"]

    # 未沙箱通过不得提交审批
    submit = client.post(f"/api/v1/repairs/{proposal}/submit")
    assert submit.status_code == 412

    # Agent 审批被拒绝
    if doc["status"] == "DRAFT":
        client.post(f"/api/v1/repairs/{proposal}/validate")
    approve = client.post(
        f"/api/v1/repairs/{proposal}/approve", json={"reviewer": "agent:llm", "note": ""}
    )
    assert approve.status_code in (403, 409)


def test_diagnosis_requires_failure_evidence(client) -> None:  # type: ignore[no-untyped-def]
    from traceguard_api.services import get_services

    rt = get_services().rt
    task = rt.tasks.create_task(
        "tcase_fake", f"fake-{time.time_ns()}", testcase_version_id="tcase_fake@1"
    )
    run = rt.tasks.create_run(task["task_id"], "tcase_fake@1")
    lease = rt.tasks.acquire_lease(run["run_id"], "test-worker")
    rt.tasks.submit_result(
        run["run_id"],
        "test-worker",
        lease["fencing_token"],
        execution_status="RUNNING",
        test_verdict="NOT_EVALUATED",
    )
    rt.tasks.submit_result(
        run["run_id"],
        "test-worker",
        lease["fencing_token"],
        execution_status="FINALIZING",
        test_verdict="PASSED",
        report={"steps": [], "assertion_outcomes": []},
    )
    r = client.post(f"/api/v1/runs/{run['run_id']}/diagnose")
    assert r.status_code == 412
    assert "PASSED" in r.json()["detail"]["message"]


def test_probe_permission_and_whitelist(client) -> None:  # type: ignore[no-untyped-def]
    # 非白名单模板 → BLOCKED（不执行）
    r = client.post(
        "/api/v1/probes/execute",
        json={
            "probe_id": "http_state",
            "params": {"template": "raw_sql", "query": "select * from users"},
        },
    )
    assert r.status_code == 201
    assert r.json()["status"] == "BLOCKED"
    assert r.json()["permission_decision"] == "DENY"

    unknown = client.post(
        "/api/v1/probes/execute", json={"probe_id": "nope", "params": {"template": "x"}}
    )
    assert unknown.json()["status"] == "BLOCKED"


def test_benchmark_dataset_does_not_leak_ground_truth(client) -> None:  # type: ignore[no-untyped-def]
    r = client.get("/api/v1/benchmark/dataset")
    assert r.status_code == 200
    body = r.json()
    assert body["ground_truth_exposed"] is False
    assert all("gt_mechanism" not in s for s in body["scenarios"])


def test_websocket_free_artifact_integrity_error(client) -> None:  # type: ignore[no-untyped-def]
    from traceguard_api.services import get_services

    rt = get_services().rt
    art = rt.artifacts.put(b"tamper-me", workspace_id="default", media_type="text/plain")
    path = rt.artifacts._local_path(art["sha256"])
    path.write_bytes(b"tampered")
    r = client.get(f"/api/v1/artifacts/{art['artifact_id']}/download")
    assert r.status_code == 404
    assert "integrity" in r.json()["detail"]["message"].lower()
    with pytest.raises(AppError):
        rt.artifacts.read(art["artifact_id"])
    _ = base64.b64encode(b"x")  # 保留导入示例，避免 lint 误报
