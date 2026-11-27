"""RUN-019-R2：TestCase `compile_source` 来源可信度。

背景：RUN-019 之前 `POST /testcases` 与 `POST /testcases/{id}/versions` 直接接受
客户端传入的 `compile_source`（human / compiler / healed），而
`review_testcase()` 对 human 来源有稳定性回放豁免。二者叠加形成真实漏洞：

- 任何能调用公共 HTTP 的主体都可以把 Agent 生成的候选标成 human，绕过回放门禁；
- 已存在的 compiler 候选可以通过"新建版本 + 改来源"洗成 human。

本文件锁定修复后的不变量：

1. 公共 HTTP 只能产生 human（server 记录 source_origin=manual_http + 创建者）；
2. 声明 compiler / healed 一律 403，且不落库；
3. compiler / healed 候选只能经 `create_trusted_candidate()`（服务端可信入口）写入，
   且必须携带 exploration_id / proposal_id 血缘；
4. 版本递增继承既有来源，客户端改标一律 403；
5. 人工豁免与稳定性回放门禁语义不变；
6. 旧版本的 Run 引用不因新版本创建而改变。
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from traceguard_core.config import reset_settings_cache
from traceguard_persistence import Database, set_database
from traceguard_persistence import auth as auth_store

PASSWORD = "Source-Proof-2026!"


@pytest.fixture
def auth_client(tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
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
    db = Database(f"sqlite:///{tmp_path / 'source-provenance.db'}")
    set_database(db)
    reset_runtime()
    reset_services()
    app = create_app()
    with TestClient(app) as client:
        for username, role in (("eng", "qa-engineer"), ("lead", "qa-lead")):
            created = auth_store.create_user(db, username=username, password=PASSWORD, roles=[role])
            auth_store.set_user_workspaces(db, created["user_id"], ["default"])

        def login(username: str) -> dict[str, str]:
            """切换会话身份：CSRF 令牌与会话 Cookie 必须来自同一次登录。"""
            r = client.post("/api/v1/auth/login", json={"username": username, "password": PASSWORD})
            assert r.status_code == 200, r.text
            return {"X-CSRF-Token": r.json()["csrf_token"]}

        yield client, db, login
    reset_runtime()
    reset_services()
    reset_settings_cache()
    reset_auth_policy_cache()


def _oracle(client: TestClient, headers: dict[str, str], assertion_id: str) -> str:
    oracle_id = f"oracle_src_{uuid.uuid4().hex[:8]}"
    r = client.post(
        "/api/v1/oracles",
        json={
            "oracle_id": oracle_id,
            "assertion_id": assertion_id,
            "expected_behavior": "成功提示必须出现",
            "requirement_ref": "REQ-R2-SOURCE",
            "version": 1,
            "approved": True,
            "approved_by": "role:qa-lead",
        },
        headers=headers,
    )
    assert r.status_code == 201, r.text
    return oracle_id


def _payload(tc_id: str, oracle_id: str | None = None) -> dict:
    assertion = {
        "assertion_id": "a1",
        "description": "成功提示",
        "kind": "ui",
        "required": True,
        "expected": {"test_id": "banner-success"},
    }
    if oracle_id:
        assertion |= {"oracle_id": oracle_id, "oracle_version": 1}
    return {
        "testcase_id": tc_id,
        "title": "来源可信度用例",
        "steps": [{"step_id": "s1", "action": "navigate", "value": "http://127.0.0.1:8011/"}],
        "assertions": [assertion],
    }


def test_client_declared_compiler_source_rejected(auth_client):  # type: ignore[no-untyped-def]
    client, _, login = auth_client
    tc_id = f"tcase_src_{uuid.uuid4().hex[:8]}"
    r = client.post(
        "/api/v1/testcases",
        json={**_payload(tc_id), "compile_source": "compiler"},
        headers=login("eng"),
    )
    assert r.status_code == 403, r.text
    assert r.json()["detail"]["code"] == "COMPILE_SOURCE_NOT_CLIENT_CONTROLLED"
    # 拒绝必须同时"不落库"，不能先写入再报错。
    assert client.get(f"/api/v1/testcases/{tc_id}").status_code == 404


def test_client_declared_healed_source_rejected(auth_client):  # type: ignore[no-untyped-def]
    client, _, login = auth_client
    tc_id = f"tcase_src_{uuid.uuid4().hex[:8]}"
    r = client.post(
        "/api/v1/testcases",
        json={**_payload(tc_id), "compile_source": "healed"},
        headers=login("eng"),
    )
    assert r.status_code == 403, r.text
    assert r.json()["detail"]["code"] == "COMPILE_SOURCE_NOT_CLIENT_CONTROLLED"


def test_manual_creation_records_human_provenance(auth_client):  # type: ignore[no-untyped-def]
    client, _, login = auth_client
    tc_id = f"tcase_src_{uuid.uuid4().hex[:8]}"
    created = client.post("/api/v1/testcases", json=_payload(tc_id), headers=login("eng"))
    assert created.status_code == 201, created.text
    version = client.get(f"/api/v1/testcases/{tc_id}/versions/1").json()
    assert version["compile_source"] == "human"
    # 服务端显式记录来源，而不是"客户端说了算"。
    assert version["source_origin"] == "manual_http"
    assert version["source_ref"] == "user:eng"


def test_trusted_candidate_entry_rejects_human_and_missing_lineage(auth_client):  # type: ignore[no-untyped-def]
    _, _, _ = auth_client
    from traceguard_api.services import get_services
    from traceguard_core.errors import AppError

    catalog = get_services().rt.catalog
    tc_id = f"tcase_src_{uuid.uuid4().hex[:8]}"
    with pytest.raises(AppError) as human_exc:
        catalog.create_trusted_candidate(_payload(tc_id), source="human", source_ref="someone")
    assert "TRUSTED_CANDIDATE_SOURCE_INVALID" in human_exc.value.message

    with pytest.raises(AppError) as ref_exc:
        catalog.create_trusted_candidate(_payload(tc_id), source="compiler", source_ref="")
    assert "TRUSTED_CANDIDATE_SOURCE_REF_REQUIRED" in ref_exc.value.message


def test_new_version_cannot_relabel_generated_candidate_as_human(auth_client):  # type: ignore[no-untyped-def]
    client, _, login = auth_client
    from traceguard_api.services import get_services

    tc_id = f"tcase_src_{uuid.uuid4().hex[:8]}"
    get_services().rt.catalog.create_trusted_candidate(
        _payload(tc_id), source="compiler", source_ref="expl_r2_fixture", title="候选"
    )
    r = client.post(
        f"/api/v1/testcases/{tc_id}/versions",
        json={"compile_source": "human"},
        headers=login("eng"),
    )
    assert r.status_code == 403, r.text
    assert r.json()["detail"]["code"] == "COMPILE_SOURCE_NOT_CLIENT_CONTROLLED"
    # 版本数没有增加：洗来源的请求不产生副作用。
    assert len(client.get(f"/api/v1/testcases/{tc_id}/versions").json()) == 1


def test_new_version_inherits_compiler_lineage(auth_client):  # type: ignore[no-untyped-def]
    client, _, login = auth_client
    from traceguard_api.services import get_services

    tc_id = f"tcase_src_{uuid.uuid4().hex[:8]}"
    get_services().rt.catalog.create_trusted_candidate(
        _payload(tc_id), source="compiler", source_ref="expl_r2_fixture", title="候选"
    )
    r = client.post(
        f"/api/v1/testcases/{tc_id}/versions",
        json={
            "steps": [{"step_id": "s1", "action": "navigate", "value": "http://127.0.0.1:8011/x"}]
        },
        headers=login("eng"),
    )
    assert r.status_code == 201, r.text
    assert r.json()["version"] == 2
    v2 = client.get(f"/api/v1/testcases/{tc_id}/versions/2").json()
    assert v2["compile_source"] == "compiler"  # 未被降级为 human
    assert v2["source_origin"] == "compiler_flow"
    assert v2["source_ref"] == f"{tc_id}@1"  # 血缘指向父版本


def test_compiler_candidate_without_replay_rejected_and_old_run_reference_stable(
    auth_client,
):  # type: ignore[no-untyped-def]
    client, _, login = auth_client
    from traceguard_api.services import get_services

    oracle_id = _oracle(client, login("lead"), "a1")
    tc_id = f"tcase_src_{uuid.uuid4().hex[:8]}"
    get_services().rt.catalog.create_trusted_candidate(
        _payload(tc_id, oracle_id),
        source="compiler",
        source_ref="expl_r2_fixture",
        title="候选",
    )
    # 旧版本被真实 Run 引用（不经 Worker 启动，只建立数据库引用关系）。
    rt = get_services().rt
    task = rt.tasks.create_task(
        tc_id,
        f"r2-{uuid.uuid4().hex[:8]}",
        testcase_version_id=f"{tc_id}@1",
        workspace_id="default",
    )
    run = rt.tasks.create_run(task["task_id"], f"{tc_id}@1", auto_queue=False)

    v1_before = client.get(f"/api/v1/testcases/{tc_id}/versions/1").json()
    review = client.post(
        f"/api/v1/testcases/{tc_id}/versions/1/review",
        json={"decision": "APPROVED", "reviewer": "role:qa-lead"},
        headers=login("lead"),
    )
    assert review.status_code == 412, review.text
    assert "STABILITY_REPLAY_REQUIRED" in review.json()["detail"]["message"]

    v2 = client.post(
        f"/api/v1/testcases/{tc_id}/versions",
        json={
            "steps": [{"step_id": "s1", "action": "navigate", "value": "http://127.0.0.1:8011/y"}]
        },
        headers=login("eng"),
    )
    assert v2.status_code == 201, v2.text
    v1_after = client.get(f"/api/v1/testcases/{tc_id}/versions/1").json()
    assert v1_after["steps"] == v1_before["steps"]
    assert v1_after["compile_source"] == "compiler"
    assert v1_after["review_status"] == "PENDING"

    run_doc = rt.tasks.get_run(run["run_id"])
    assert run_doc is not None and run_doc["testcase_version_id"] == f"{tc_id}@1"


def test_agent_identity_cannot_approve(auth_client):  # type: ignore[no-untyped-def]
    client, _, login = auth_client
    from traceguard_api.services import get_services
    from traceguard_core.errors import AppError

    tc_id = f"tcase_src_{uuid.uuid4().hex[:8]}"
    created = client.post("/api/v1/testcases", json=_payload(tc_id), headers=login("eng"))
    assert created.status_code == 201, created.text

    # HTTP：请求体里的 agent 主体被 Principal 覆盖，不能借道自批。
    review = client.post(
        f"/api/v1/testcases/{tc_id}/versions/1/review",
        json={"decision": "APPROVED", "reviewer": "agent:llm"},
        headers=login("lead"),
    )
    assert review.status_code in {200, 412}, review.text
    if review.status_code == 200:
        version = client.get(f"/api/v1/testcases/{tc_id}/versions/1").json()
        assert version["review_history"][-1]["reviewer"] == "user:lead"

    # 服务层：agent 主体直接被拒（403），不进入审核状态机。
    with pytest.raises(AppError) as exc:
        get_services().rt.catalog.review_testcase(
            tc_id, 1, "APPROVED", "agent:llm", note="机器自批"
        )
    assert "MUST_BE_HUMAN" in exc.value.message
