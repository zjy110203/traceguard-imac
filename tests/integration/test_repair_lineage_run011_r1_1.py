"""RUN-011-R1.1 WP3：Repair 服务端血缘与来源真实性负向验收。

覆盖指令 §3.4 的全部反例：不存在的 Diagnosis、Diagnosis/Run/TestCase/Workspace
不匹配、伪造 llm 来源、伪造 created_by、PASSED Run 作为失败来源、
同一 Proposal 使用其它用例/其它版本。断言 ErrCode 与"没有写入提案"。
"""

from __future__ import annotations

from typing import Any

import pytest

# 复用 WP2 的双 Workspace 认证环境（同一测试目录已加入 sys.path）
from test_workspace_isolation_run011_r1_1 import iso_env  # noqa: F401,E402,F811
from traceguard_core.errors import AppError
from traceguard_persistence import Database, set_database

WS_A = "ws-iso-a"
WS_B = "ws-iso-b"


@pytest.fixture
def repair_env(work_tmp, monkeypatch):  # type: ignore[no-untyped-def]
    import uuid

    from traceguard_api.runtime import Runtime
    from traceguard_api.services.repair_service import RepairService
    from traceguard_core.config import reset_settings_cache

    monkeypatch.setenv("TG_LLM_PROVIDER", "")
    reset_settings_cache()
    db = Database(f"sqlite:///{work_tmp / f'repair-lineage-{uuid.uuid4().hex[:6]}.db'}")
    set_database(db)
    rt = Runtime(db)
    assets = {
        "a": _seed_case(rt, "tcase_lineage_a", WS_A, versions=2),
        "b": _seed_case(rt, "tcase_lineage_b", WS_B, versions=1),
    }
    return rt, RepairService(rt), assets["a"], assets["b"]


def _seed_case(rt: Any, tc_id: str, ws: str, *, versions: int) -> dict[str, Any]:
    from traceguard_core.ids import utc_now_iso

    out: dict[str, Any] = {"testcase_id": tc_id, "workspace_id": ws, "runs": []}
    for _v in range(1, versions + 1):
        rt.catalog.create_testcase(
            {
                "testcase_id": tc_id,
                "version": 1,
                "workspace_id": ws,
                "created_at": utc_now_iso(),
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
                        "description": "可见",
                        "kind": "ui",
                        "required": True,
                        "expected": {},
                    }
                ],
            },
            title=tc_id,
            workspace_id=ws,
            review_status="DRAFT",
        )
    out["failed_run"] = _make_run(rt, tc_id, 1, ws, verdict="FAILED")
    out["passed_run"] = _make_run(rt, tc_id, 1, ws, verdict="PASSED")
    if versions > 1:
        out["v2_run"] = _make_run(rt, tc_id, 2, ws, verdict="FAILED")
    return out


def _make_run(rt: Any, tc_id: str, version: int, ws: str, *, verdict: str) -> str:
    import time

    ref = f"{tc_id}@{version}"
    task = rt.tasks.create_task(
        tc_id,
        f"lineage-{tc_id}-{version}-{verdict}-{time.time_ns()}",
        testcase_version_id=ref,
        workspace_id=ws,
    )
    run = rt.tasks.create_run(task["task_id"], ref)
    lease = rt.tasks.acquire_lease(run["run_id"], "setup-worker")
    report = {
        "steps": [
            {
                "step_id": "s1",
                "status": "FAILED" if verdict == "FAILED" else "PASSED",
                "error": "locator timeout" if verdict == "FAILED" else "",
                "observation": {},
            }
        ],
        "assertion_outcomes": [],
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
        test_verdict=verdict,
        report=report,
    )
    return run["run_id"]


def _put_diagnosis(
    rt: Any,
    diagnosis_id: str,
    *,
    run_id: str,
    testcase_id: str,
    workspace_id: str,
) -> str:
    rt.docs.put(
        doc_id=diagnosis_id,
        kind="diagnosis",
        doc={
            "diagnosis_id": diagnosis_id,
            "run_id": run_id,
            "testcase_id": testcase_id,
            "workspace_id": workspace_id,
            "outcome": "CONFIRMED",
        },
        workspace_id=workspace_id,
        ref_id=run_id,
        status="CONFIRMED",
    )
    return diagnosis_id


def _changes() -> list[dict[str, Any]]:
    return [
        {
            "path": "steps[].target",
            "step_index": 0,
            "value": {"strategy": "css", "value": "#fixed"},
        }
    ]


def _assert_rejected(svc: Any, expected_code: str, **kwargs: Any) -> None:
    before = len(svc.list())
    with pytest.raises(AppError) as exc:
        svc.propose(
            testcase_id=kwargs.get("testcase_id", "tcase_lineage_a"),
            base_version=kwargs.get("base_version", 1),
            changes=kwargs.get("changes", _changes()),
            created_by="human:setup",
            actor="human:setup",
            diagnosis_id=kwargs.get("diagnosis_id"),
            source_run_id=kwargs.get("source_run_id"),
            repair_source=kwargs.get("repair_source", ""),
            trusted_source=kwargs.get("trusted_source", False),
        )
    assert exc.value.code.value == expected_code, (expected_code, exc.value.message)
    assert len(svc.list()) == before, "拒绝时不得产生提案"


# --------------------------------------------------------------- 血缘反例


def test_nonexistent_diagnosis_rejected(repair_env) -> None:  # type: ignore[no-untyped-def]
    _rt, svc, a, _b = repair_env
    _assert_rejected(
        svc,
        "NOT_FOUND",
        diagnosis_id="diag_does_not_exist",
        source_run_id=a["failed_run"],
    )


def test_diagnosis_run_mismatch_rejected(repair_env) -> None:  # type: ignore[no-untyped-def]
    rt, svc, a, _b = repair_env
    diag = _put_diagnosis(
        rt,
        "diag_wrong_run",
        run_id=a["v2_run"],
        testcase_id=a["testcase_id"],
        workspace_id=WS_A,
    )
    _assert_rejected(
        svc, "CONFLICT", diagnosis_id=diag, source_run_id=a["failed_run"]
    )


def test_diagnosis_testcase_mismatch_rejected(repair_env) -> None:  # type: ignore[no-untyped-def]
    rt, svc, a, b = repair_env
    diag = _put_diagnosis(
        rt,
        "diag_wrong_tc",
        run_id=a["failed_run"],
        testcase_id=b["testcase_id"],
        workspace_id=WS_A,
    )
    _assert_rejected(svc, "CONFLICT", diagnosis_id=diag, source_run_id=a["failed_run"])


def test_run_testcase_mismatch_rejected(repair_env) -> None:  # type: ignore[no-untyped-def]
    _rt, svc, a, b = repair_env
    # Workspace 校验优先于用例归属校验（fail-closed）
    _assert_rejected(
        svc,
        "AUTHORIZATION_ERROR",
        testcase_id=a["testcase_id"],
        source_run_id=b["failed_run"],
    )


def test_run_testcase_mismatch_same_workspace_rejected(repair_env) -> None:  # type: ignore[no-untyped-def]
    """同一 Workspace 内也不能拿别的用例的失败 Run 作为来源。"""
    rt, svc, a, _b = repair_env
    other = _seed_case(rt, "tcase_lineage_other", WS_A, versions=1)
    _assert_rejected(
        svc,
        "CONFLICT",
        testcase_id=a["testcase_id"],
        source_run_id=other["failed_run"],
    )


def test_workspace_mismatch_rejected(repair_env) -> None:  # type: ignore[no-untyped-def]
    rt, svc, a, _b = repair_env
    diag = _put_diagnosis(
        rt,
        "diag_wrong_ws",
        run_id=a["failed_run"],
        testcase_id=a["testcase_id"],
        workspace_id=WS_B,
    )
    _assert_rejected(
        svc, "AUTHORIZATION_ERROR", diagnosis_id=diag, source_run_id=a["failed_run"]
    )


def test_proposal_version_mismatch_rejected(repair_env) -> None:  # type: ignore[no-untyped-def]
    _rt, svc, a, _b = repair_env
    _assert_rejected(
        svc,
        "CONFLICT",
        testcase_id=a["testcase_id"],
        base_version=1,
        source_run_id=a["v2_run"],
    )


def test_passed_run_as_failure_source_rejected(repair_env) -> None:  # type: ignore[no-untyped-def]
    _rt, svc, a, _b = repair_env
    _assert_rejected(
        svc,
        "PRECONDITION_FAILED",
        source_run_id=a["passed_run"],
    )


def test_missing_run_rejected(repair_env) -> None:  # type: ignore[no-untyped-def]
    _rt, svc, _a, _b = repair_env
    _assert_rejected(svc, "NOT_FOUND", source_run_id="run_missing")


# --------------------------------------------------------------- 来源真实性


def test_forged_llm_source_rejected(repair_env) -> None:  # type: ignore[no-untyped-def]
    _rt, svc, a, _b = repair_env
    _assert_rejected(
        svc,
        "AUTHORIZATION_ERROR",
        source_run_id=a["failed_run"],
        repair_source="llm:gpt-5",
    )


def test_unknown_rule_source_rejected(repair_env) -> None:  # type: ignore[no-untyped-def]
    _rt, svc, a, _b = repair_env
    _assert_rejected(
        svc,
        "VALIDATION_ERROR",
        source_run_id=a["failed_run"],
        repair_source="rule:made-up",
    )


def test_rule_source_contract_violation_rejected(repair_env) -> None:  # type: ignore[no-untyped-def]
    _rt, svc, a, _b = repair_env
    _assert_rejected(
        svc,
        "VALIDATION_ERROR",
        source_run_id=a["failed_run"],
        repair_source="rule:locator-fixture",
        changes=[{"path": "assertions[].expected", "value": {}}],
    )


def test_human_source_uses_actor_not_declared_prefix(repair_env) -> None:  # type: ignore[no-untyped-def]
    _rt, svc, a, _b = repair_env
    prop = svc.propose(
        testcase_id=a["testcase_id"],
        base_version=1,
        changes=_changes(),
        created_by="agent:forged",
        actor="user:engineer",
        source_run_id=a["failed_run"],
        repair_source="human:anyone",
    )
    assert prop["created_by"] == "user:engineer"
    assert prop["repair_source"] == "human:user:engineer"


# --------------------------------------------------------------- API 层伪造


def test_api_forged_llm_source_and_created_by_rejected(iso_env) -> None:  # noqa: F811  # type: ignore[no-untyped-def]
    """复用 WP2 的双 Workspace 认证环境，验证客户端声明不能伪造来源与主体。"""
    from fastapi.testclient import TestClient
    from test_workspace_isolation_run011_r1_1 import _h, _login  # type: ignore[import-not-found]

    client: TestClient = iso_env[0]
    assets = iso_env[3]
    a = assets["a"]
    csrf = _login(client, "a_engineer")
    headers = _h(csrf)
    base_payload = {
        "testcase_id": a["testcase_id"],
        "base_version": 1,
        "diagnosis_id": a["diagnosis_id"],
        "source_run_id": a["run_id"],
        "changes": _changes(),
    }

    forged_llm = client.post(
        "/api/v1/repairs",
        json={**base_payload, "repair_source": "llm:gpt-5"},
        headers=headers,
    )
    assert forged_llm.status_code == 403, forged_llm.text
    assert "UNTRUSTED_REPAIR_SOURCE" in forged_llm.json()["detail"]["message"]

    forged_actor = client.post(
        "/api/v1/repairs",
        json={**base_payload, "created_by": "user:admin"},
        headers=headers,
    )
    assert forged_actor.status_code == 201, forged_actor.text
    doc = forged_actor.json()
    assert doc["created_by"] == "user:a_engineer", "created_by 必须取认证 Principal"
    assert doc["created_by_source"] == "principal"
    assert doc["repair_source"].startswith("human:user:a_engineer")
    assert doc["source_run_id"] == a["run_id"]
    assert doc["diagnosis_id"] == a["diagnosis_id"]
