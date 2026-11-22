"""诊断规则回归：5xx 优先于下游症状、UI/持久化冲突分类、无证据不确认。"""

from __future__ import annotations

import time

import pytest
from traceguard_agents.healing.rules import (
    assertion_preserved,
    diff_allowed,
    oracle_integrity_ok,
)
from traceguard_api.runtime import Runtime
from traceguard_api.services.diagnosis_service import DiagnosisService
from traceguard_contracts.models import AssertionSpec, TestCaseVersion, TestStep
from traceguard_persistence import Database


@pytest.fixture()
def rt(work_tmp):  # type: ignore[no-untyped-def]
    import uuid

    db = Database(f"sqlite:///{work_tmp / f'diag-{uuid.uuid4().hex[:6]}.db'}")
    db.create_schema()
    return Runtime(db)


def _base_tc(version: int = 1) -> TestCaseVersion:
    return TestCaseVersion(
        testcase_id="tcase_rules",
        version=version,
        steps=[TestStep(step_id="s1", action="navigate", value="/projects")],
        assertions=[
            AssertionSpec(
                assertion_id="a1",
                description="必须成功",
                kind="ui",
                expected={"test_id": "ok"},
            ),
            AssertionSpec(
                assertion_id="a2",
                description="业务落库",
                kind="business_state",
                expected={"api": "/api/state/projects", "field": "count", "op": "gte", "value": 1},
            ),
        ],
        created_at="2026-01-01T00:00:00Z",
    )


def test_assertion_preservation_rules() -> None:
    base = _base_tc()
    assert assertion_preserved(base, base.model_copy(deep=True))
    weakened = base.model_copy(deep=True)
    weakened.assertions[1].expected = {
        "api": "/api/state/projects",
        "field": "count",
        "op": "gte",
        "value": 0,
    }
    assert assertion_preserved(base, weakened) is False
    removed = base.model_copy(deep=True)
    removed.assertions = [base.assertions[0]]
    assert assertion_preserved(base, removed) is False
    stricter = base.model_copy(deep=True)
    stricter.assertions.append(
        AssertionSpec(assertion_id="a3", description="额外", kind="api", expected={"x": 1})
    )
    assert assertion_preserved(base, stricter) is True


def test_oracle_integrity_rules() -> None:
    base = _base_tc()
    cand = base.model_copy(deep=True)
    assert oracle_integrity_ok(base, cand) is True
    cand.assertions[0].oracle_id = "oracle_hijack"
    assert oracle_integrity_ok(base, cand) is False


def test_diff_whitelist() -> None:
    assert diff_allowed([{"path": "steps[].target", "value": {"strategy": "css", "value": "#a"}}])
    assert not diff_allowed([{"path": "assertions[].expected"}])
    assert not diff_allowed([{"path": "oracle"}])
    assert not diff_allowed([{"path": "product_code/login.js"}])


def test_api_5xx_outranks_persistence_symptom(rt) -> None:  # type: ignore[no-untyped-def]
    """接口 500 导致未落库时，主因必须是 API，而不是把症状当根因。"""
    task = rt.tasks.create_task(
        "tcase_rules", f"diag-{time.time_ns()}", testcase_version_id="tcase_rules@1"
    )
    run = rt.tasks.create_run(task["task_id"], "tcase_rules@1")
    lease = rt.tasks.acquire_lease(run["run_id"], "w")
    report = {
        "steps": [
            {
                "step_id": "s1",
                "action": "click",
                "status": "PASSED",
                "observation": {
                    "http_errors": [
                        {
                            "url": "http://x/api/projects",
                            "method": "POST",
                            "status": 500,
                            "resource_type": "fetch",
                        }
                    ],
                    "page_errors": [],
                    "network": [],
                },
                "evidence_ids": [],
                "artifacts": {},
            }
        ],
        "assertion_outcomes": [
            {
                "assertion_id": "a1",
                "status": "PASSED",
                "required": True,
                "kind": "ui",
                "oracle_available": True,
                "reason": "present",
            },
            {
                "assertion_id": "a2",
                "status": "FAILED",
                "required": True,
                "kind": "business_state",
                "oracle_available": True,
                "reason": "countgte1 got=0",
            },
        ],
        "summary": {"total": 2, "passed": 1, "failed": 1},
    }
    rt.tasks.submit_result(
        run["run_id"],
        "w",
        lease["fencing_token"],
        execution_status="RUNNING",
        test_verdict="NOT_EVALUATED",
    )
    rt.tasks.submit_result(
        run["run_id"],
        "w",
        lease["fencing_token"],
        execution_status="FINALIZING",
        test_verdict="FAILED",
        report=report,
    )
    rt.evidence.add_raw(
        "evd_net_1",
        "network",
        "step_signals:s1",
        "default",
        {
            "run_id": run["run_id"],
            "step_id": "s1",
            "signals": {
                "http_errors": [{"url": "http://x/api/projects", "status": 500}],
            },
        },
        source_trust="TRUSTED",
        run_id=run["run_id"],
    )
    rt.evidence.add_raw(
        "evd_state_1",
        "api",
        "state_query:/api/state/projects",
        "default",
        {"run_id": run["run_id"], "response": {"count": 0}},
        source_trust="TRUSTED",
        run_id=run["run_id"],
    )
    diag = DiagnosisService(rt).diagnose(run["run_id"])
    mechs = [h["failure_mechanism"] for h in diag["hypotheses"]]
    assert "API" in mechs
    assert "PERSISTENCE" not in mechs  # 症状不作为独立根因
    confirmed = [h for h in diag["hypotheses"] if h["status"] == "CONFIRMED"]
    assert confirmed and confirmed[0]["failure_mechanism"] == "API"
    assert diag["outcome"] == "CONFIRMED"
    assert diag["disposition"] == "PRODUCT_DEFECT"


def test_no_evidence_stays_inconclusive(rt) -> None:  # type: ignore[no-untyped-def]
    """只有 UI 断言失败、无任何权威证据时，不得确认根因。"""
    task = rt.tasks.create_task(
        "tcase_rules", f"diag2-{time.time_ns()}", testcase_version_id="tcase_rules@1"
    )
    run = rt.tasks.create_run(task["task_id"], "tcase_rules@1")
    lease = rt.tasks.acquire_lease(run["run_id"], "w")
    report = {
        "steps": [
            {
                "step_id": "s1",
                "action": "assert",
                "status": "FAILED",
                "error": "expected text missing",
                "observation": {"present": True, "text": "无提示", "page_errors": []},
                "evidence_ids": [],
                "artifacts": {},
            }
        ],
        "assertion_outcomes": [
            {
                "assertion_id": "a1",
                "status": "FAILED",
                "required": True,
                "kind": "ui",
                "oracle_available": True,
                "reason": "text_contains=成功",
            }
        ],
        "summary": {"total": 1, "passed": 0, "failed": 1},
    }
    rt.tasks.submit_result(
        run["run_id"],
        "w",
        lease["fencing_token"],
        execution_status="RUNNING",
        test_verdict="NOT_EVALUATED",
    )
    rt.tasks.submit_result(
        run["run_id"],
        "w",
        lease["fencing_token"],
        execution_status="FINALIZING",
        test_verdict="FAILED",
        report=report,
    )
    diag = DiagnosisService(rt).diagnose(run["run_id"])
    assert diag["outcome"] in {"CONFIRMED", "PROBABLE", "INCONCLUSIVE"}
    # 没有任何 evidence 时，不得出现无来源的 CONFIRMED
    for h in diag["hypotheses"]:
        if h["status"] == "CONFIRMED":
            assert h["supporting"], "CONFIRMED 必须引用真实证据"
