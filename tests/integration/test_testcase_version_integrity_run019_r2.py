"""RUN-019-R2：TestCaseVersion 历史完整性（作者内容不可覆盖 + 审核元数据可追加）。

`immutable=True` 之前只是流程标记。本文件锁定 R2 之后的真实约束：

1. 创建版本时写入 `authoring_hash`（步骤/断言/配置的作者内容摘要）；
2. 审核 / Oracle 绑定写库前复核摘要，历史业务内容被改写 → 409；
3. 审核元数据（review_history / review_revision / oracle_bindings）可追加；
4. 新版本创建不影响旧版本文档与已固定 Oracle 版本；
5. 旧 Run 的版本引用保持不变。
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from traceguard_persistence import Database, set_database


@pytest.fixture
def client(tmp_path):  # type: ignore[no-untyped-def]
    from traceguard_api.main import create_app
    from traceguard_api.runtime import reset_runtime
    from traceguard_api.services import reset_services

    db = Database(f"sqlite:///{tmp_path / 'version-integrity.db'}")
    set_database(db)
    reset_runtime()
    reset_services()
    app = create_app()
    with TestClient(app) as c:
        yield c, db
    reset_runtime()
    reset_services()


def _payload(tc_id: str, value: str = "http://127.0.0.1:8011/") -> dict:
    return {
        "testcase_id": tc_id,
        "title": "版本完整性用例",
        "steps": [{"step_id": "s1", "action": "navigate", "value": value}],
        "assertions": [
            {
                "assertion_id": "a1",
                "description": "业务成功",
                "kind": "business_state",
                "required": True,
                "expected": {"status": "ok"},
            }
        ],
    }


def _tamper_steps(db: Database, tc_id: str, version: int, value: str) -> None:
    """模拟绕过服务层直接改库（SQLite JSON 列不是数据库级不可变）。"""
    from traceguard_persistence.models import TestCaseVersionRow

    with db.session() as s:
        row = s.get(TestCaseVersionRow, f"{tc_id}@{version}")
        doc = dict(row.doc or {})
        doc["steps"] = [{"step_id": "s1", "action": "navigate", "value": value}]
        row.doc = doc
        s.flush()


def test_version_records_authoring_hash(client):  # type: ignore[no-untyped-def]
    c, _ = client
    tc_id = f"tcase_intg_{uuid.uuid4().hex[:8]}"
    created = c.post("/api/v1/testcases", json=_payload(tc_id))
    assert created.status_code == 201, created.text
    version = c.get(f"/api/v1/testcases/{tc_id}/versions/1").json()
    assert len(str(version.get("authoring_hash") or "")) == 64
    # 作者内容变更（新建版本）必须产生不同的摘要
    v2 = c.post(
        f"/api/v1/testcases/{tc_id}/versions",
        json={
            "steps": [{"step_id": "s1", "action": "navigate", "value": "http://127.0.0.1:8011/b"}]
        },
    )
    assert v2.status_code == 201, v2.text
    v2_doc = c.get(f"/api/v1/testcases/{tc_id}/versions/2").json()
    assert v2_doc["authoring_hash"] != version["authoring_hash"]


def test_tampered_business_content_blocks_review(client):  # type: ignore[no-untyped-def]
    c, db = client
    tc_id = f"tcase_intg_{uuid.uuid4().hex[:8]}"
    assert c.post("/api/v1/testcases", json=_payload(tc_id)).status_code == 201
    _tamper_steps(db, tc_id, 1, "http://tampered.local/")

    review = c.post(
        f"/api/v1/testcases/{tc_id}/versions/1/review",
        json={"decision": "REJECTED", "reviewer": "human:qa-lead"},
    )
    assert review.status_code == 409, review.text
    assert "TESTCASE_VERSION_CONTENT_MUTATED" in review.json()["detail"]["message"]
    # 被拒绝的流转不能留下任何状态变化
    assert c.get(f"/api/v1/testcases/{tc_id}/versions/1").json()["review_status"] == "PENDING"


def test_tampered_business_content_blocks_oracle_binding(client):  # type: ignore[no-untyped-def]
    c, db = client
    tc_id = f"tcase_intg_{uuid.uuid4().hex[:8]}"
    assert c.post("/api/v1/testcases", json=_payload(tc_id)).status_code == 201
    oracle = c.post(
        "/api/v1/oracles",
        json={
            "oracle_id": f"oracle_intg_{uuid.uuid4().hex[:6]}",
            "assertion_id": "a1",
            "expected_behavior": "业务成功",
            "requirement_ref": "REQ-INTG",
            "approved": True,
            "approved_by": "human:qa-lead",
        },
    ).json()
    _tamper_steps(db, tc_id, 1, "http://tampered.local/")
    bound = c.post(
        f"/api/v1/testcases/{tc_id}/versions/1/oracle-bindings",
        json={"bindings": {"a1": {"oracle_id": oracle["oracle_id"]}}, "actor": "human:qa-lead"},
    )
    assert bound.status_code == 409, bound.text
    assert "TESTCASE_VERSION_CONTENT_MUTATED" in bound.json()["detail"]["message"]


def test_review_appends_metadata_without_changing_authoring_hash(client):  # type: ignore[no-untyped-def]
    c, _ = client
    tc_id = f"tcase_intg_{uuid.uuid4().hex[:8]}"
    assert c.post("/api/v1/testcases", json=_payload(tc_id)).status_code == 201
    before = c.get(f"/api/v1/testcases/{tc_id}/versions/1").json()

    oracle = c.post(
        "/api/v1/oracles",
        json={
            "oracle_id": f"oracle_intg_{uuid.uuid4().hex[:6]}",
            "assertion_id": "a1",
            "expected_behavior": "业务成功",
            "requirement_ref": "REQ-INTG",
            "approved": True,
            "approved_by": "human:qa-lead",
        },
    ).json()
    assert (
        c.post(
            f"/api/v1/testcases/{tc_id}/versions/1/oracle-bindings",
            json={"bindings": {"a1": {"oracle_id": oracle["oracle_id"]}}, "actor": "human:qa-lead"},
        ).status_code
        == 200
    )
    review = c.post(
        f"/api/v1/testcases/{tc_id}/versions/1/review",
        json={"decision": "APPROVED", "reviewer": "human:qa-lead"},
    )
    assert review.status_code == 200, review.text

    after = c.get(f"/api/v1/testcases/{tc_id}/versions/1").json()
    assert after["authoring_hash"] == before["authoring_hash"]  # 业务内容没变
    assert after["authoring_hash"]  # 摘要仍在
    assert after["review_status"] == "APPROVED"
    assert len(after["review_history"]) == 1
    # Oracle 版本被固定到断言（审批后不可再变）
    assert after["assertions"][0]["oracle_version"] == 1
    assert after["oracle_binding_frozen"] is True
    # 已审批版本不允许改绑定
    again = c.post(
        f"/api/v1/testcases/{tc_id}/versions/1/oracle-bindings",
        json={"bindings": {"a1": {"oracle_id": oracle["oracle_id"]}}, "actor": "human:qa-lead"},
    )
    assert again.status_code == 412, again.text


def test_old_run_reference_survives_new_version(client):  # type: ignore[no-untyped-def]
    c, _ = client
    from traceguard_api.services import get_services

    tc_id = f"tcase_intg_{uuid.uuid4().hex[:8]}"
    assert c.post("/api/v1/testcases", json=_payload(tc_id)).status_code == 201
    rt = get_services().rt
    task = rt.tasks.create_task(
        tc_id,
        f"intg-{uuid.uuid4().hex[:8]}",
        testcase_version_id=f"{tc_id}@1",
        workspace_id="default",
    )
    run = rt.tasks.create_run(task["task_id"], f"{tc_id}@1", auto_queue=False)
    v1_before = c.get(f"/api/v1/testcases/{tc_id}/versions/1").json()

    v2 = c.post(
        f"/api/v1/testcases/{tc_id}/versions",
        json={
            "steps": [{"step_id": "s1", "action": "navigate", "value": "http://127.0.0.1:8011/c"}]
        },
    )
    assert v2.status_code == 201, v2.text
    assert c.get(f"/api/v1/testcases/{tc_id}/versions/1").json() == v1_before
    assert rt.tasks.get_run(run["run_id"])["testcase_version_id"] == f"{tc_id}@1"
