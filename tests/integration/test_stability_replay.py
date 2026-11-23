"""RUN-007 WP1-01：候选用例稳定性回放（F08）测试（不启真实浏览器）。

覆盖：
- 每次回放产生独立 Run，不覆盖历史；走 sandbox Task 模式；
- Run 级状态区分 PENDING / NOT_EVALUATED / INCONCLUSIVE / PASSED / FAILED；
- 未完成的 Run 不参与结论（不产生 conclusion，不允许晋升）；
- **NOT_EVALUATED 不得被归类为确定性失败**；
- Oracle 完整性检查"具体已审批版本 + 版本固定"，不只看 oracle_id 是否存在；
- 失败分类区分 Locator / Timing / Environment / 产品故障 / UI，不一律 Flaky。
"""

from __future__ import annotations

import uuid

import pytest
from traceguard_api.runtime import Runtime
from traceguard_api.services.stability_service import StabilityReplayService
from traceguard_core.errors import AppError
from traceguard_core.ids import new_id
from traceguard_persistence import Database
from traceguard_persistence.models import RunRow


@pytest.fixture()
def rt(work_tmp, monkeypatch):  # type: ignore[no-untyped-def]
    base = work_tmp / f"replay-{uuid.uuid4().hex[:8]}"
    base.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("TG_ARTIFACT_DIR", str(base / "artifacts"))
    from traceguard_core.config import reset_settings_cache

    reset_settings_cache()
    db = Database(f"sqlite:///{base / 'replay.db'}")
    runtime = Runtime(db)
    yield runtime
    db.dispose()


def _candidate(rt: Runtime, *, oracle_bound: bool = False) -> tuple[str, int]:
    testcase_id = new_id("tcase")
    oracle_id = new_id("oracle")
    if oracle_bound:
        rt.catalog.create_oracle(
            {
                "oracle_id": oracle_id,
                "assertion_id": "a1",
                "expected_behavior": "成功提示必须出现",
                "requirement_ref": "REQ-REPLAY-1",
                "version": 1,
            },
            approved=True,
            approved_by="role:qa-lead",
        )
    payload = {
        "testcase_id": testcase_id,
        "version": 1,
        "intent_ref": "intent_x",
        "compile_source": "compiler",
        "steps": [
            {"step_id": "s1", "action": "navigate", "value": "http://127.0.0.1:8011/"},
            {
                "step_id": "s2",
                "action": "click",
                "target": {"strategy": "test_id", "value": "create"},
            },
        ],
        "assertions": [
            {
                "assertion_id": "a1",
                "description": "成功提示",
                "kind": "ui",
                "required": True,
                "expected": {"test_id": "banner-success"},
                "oracle_id": oracle_id if oracle_bound else None,
                # 只有审核通过才会把版本固定下来；这里模拟"已固定"
                "oracle_version": 1 if oracle_bound else None,
            }
        ],
        "review_status": "PENDING",
        "created_at": "2026-01-01T00:00:00Z",
    }
    created = rt.catalog.create_testcase(payload, title="回放候选", review_status="PENDING")
    return testcase_id, created["version"]


def _set_verdict(
    rt: Runtime,
    run_id: str,
    verdict: str,
    report: dict | None = None,
    *,
    execution_status: str = "COMPLETED",
) -> None:
    with rt.db.session() as s:
        row = s.get(RunRow, run_id)
        row.test_verdict = verdict
        row.execution_status = execution_status
        row.report_json = report or {"steps": [], "assertion_outcomes": []}


def test_replay_creates_independent_sandbox_runs(rt: Runtime) -> None:
    testcase_id, version = _candidate(rt)
    svc = StabilityReplayService(rt)
    doc = svc.start(testcase_id, version, repeats=3)
    runs = [r["run_id"] for r in doc["records"]]
    assert len(runs) == 3 and len(set(runs)) == 3
    for record in doc["records"]:
        task = rt.tasks.get_task(record["task_id"])
        assert task["mode"] == "sandbox"  # 独立沙箱入口
        run = rt.tasks.get_run(record["run_id"])
        assert run is not None
    assert doc["status"] == "RUNNING"
    assert doc["conclusion"] is None  # 未完成不得形成结论
    assert doc["requires_human_review"] is True
    assert doc["testcase_version_ref"] == f"{testcase_id}@{version}"
    assert doc["testcase_fingerprint"]


def test_unfinished_replay_has_no_conclusion_and_is_not_promotable(rt: Runtime) -> None:
    testcase_id, version = _candidate(rt, oracle_bound=True)
    svc = StabilityReplayService(rt)
    doc = svc.start(testcase_id, version, repeats=3)
    # 只完成 2 个：仍不得下结论
    _set_verdict(rt, doc["records"][0]["run_id"], "PASSED")
    _set_verdict(rt, doc["records"][1]["run_id"], "PASSED")
    refreshed = svc.refresh(doc["replay_id"])
    assert refreshed["status"] == "RUNNING"
    assert refreshed["conclusion"] is None
    assert refreshed["promotable"] is False
    assert refreshed["pending_runs"] == [doc["records"][2]["run_id"]]


def test_not_evaluated_is_never_deterministic_failure(rt: Runtime) -> None:
    """NOT_EVALUATED（跑了但没 Oracle 可评）不是"确定性失败"，只能算证据不足。"""
    testcase_id, version = _candidate(rt, oracle_bound=False)
    svc = StabilityReplayService(rt)
    doc = svc.start(testcase_id, version, repeats=3)
    for record in doc["records"]:
        _set_verdict(rt, record["run_id"], "NOT_EVALUATED", execution_status="FAILED")
    refreshed = svc.refresh(doc["replay_id"])
    assert refreshed["classification"]["classification"] != "DETERMINISTIC_FAILURE"
    assert refreshed["classification"]["classification"] == "INSUFFICIENT_EVIDENCE"
    assert refreshed["classification"]["not_evaluated_count"] == 3
    assert refreshed["classification"]["samples_evaluated"] == 0
    assert refreshed["promotable"] is False


def test_mixed_not_evaluated_downgrades_pass(rt: Runtime) -> None:
    testcase_id, version = _candidate(rt, oracle_bound=True)
    svc = StabilityReplayService(rt)
    doc = svc.start(testcase_id, version, repeats=3)
    _set_verdict(rt, doc["records"][0]["run_id"], "PASSED")
    _set_verdict(rt, doc["records"][1]["run_id"], "PASSED")
    _set_verdict(rt, doc["records"][2]["run_id"], "NOT_EVALUATED", execution_status="FAILED")
    refreshed = svc.refresh(doc["replay_id"])
    assert refreshed["classification"]["classification"] == "EVIDENCE_INSUFFICIENT"
    assert refreshed["promotable"] is False


def test_unstable_candidate_never_promotable(rt: Runtime) -> None:
    testcase_id, version = _candidate(rt, oracle_bound=True)
    svc = StabilityReplayService(rt)
    doc = svc.start(testcase_id, version, repeats=3)
    _set_verdict(rt, doc["records"][0]["run_id"], "PASSED")
    _set_verdict(rt, doc["records"][1]["run_id"], "FAILED")
    _set_verdict(rt, doc["records"][2]["run_id"], "PASSED")
    refreshed = svc.refresh(doc["replay_id"])
    assert refreshed["classification"]["stable"] is False
    assert refreshed["classification"]["classification"] == "FLAKY_UNSTABLE"
    assert refreshed["promotable"] is False


def test_stable_but_oracle_incomplete_is_not_promotable(rt: Runtime) -> None:
    testcase_id, version = _candidate(rt, oracle_bound=False)
    svc = StabilityReplayService(rt)
    doc = svc.start(testcase_id, version, repeats=2)
    for record in doc["records"]:
        _set_verdict(rt, record["run_id"], "PASSED")
    refreshed = svc.refresh(doc["replay_id"])
    assert refreshed["classification"]["classification"] == "STABLE_PASS"
    assert refreshed["oracle_complete"] is False
    assert refreshed["promotable"] is False
    assert "不得宣称业务预期已验证" in refreshed["oracle_note"]
    assert refreshed["status"] == "COMPLETED"
    assert any("MISSING_ORACLE" in p for p in refreshed["oracle_problems"])


def test_unpinned_oracle_version_is_incomplete(rt: Runtime) -> None:
    """Oracle 存在且已审批，但断言上没有固定版本 → 不算完整。"""
    testcase_id, version = _candidate(rt, oracle_bound=True)
    with rt.db.session() as s:
        from traceguard_persistence.models import TestCaseVersionRow

        row = s.get(TestCaseVersionRow, f"{testcase_id}@{version}")
        old = dict(row.doc or {})
        # 注意：JSON 列不支持嵌套原地修改追踪，必须重建结构再赋值
        new_assertions = [{**a, "oracle_version": None} for a in old.get("assertions") or []]
        row.doc = {**old, "assertions": new_assertions}
    svc = StabilityReplayService(rt)
    doc = svc.start(testcase_id, version, repeats=2)
    for record in doc["records"]:
        _set_verdict(rt, record["run_id"], "PASSED")
    refreshed = svc.refresh(doc["replay_id"])
    assert refreshed["oracle_complete"] is False
    assert any("ORACLE_VERSION_NOT_PINNED" in p for p in refreshed["oracle_problems"])
    assert refreshed["promotable"] is False


def test_stable_with_oracle_is_promotable_but_still_needs_human(rt: Runtime) -> None:
    testcase_id, version = _candidate(rt, oracle_bound=True)
    svc = StabilityReplayService(rt)
    doc = svc.start(testcase_id, version, repeats=2)
    for record in doc["records"]:
        _set_verdict(rt, record["run_id"], "PASSED")
    refreshed = svc.refresh(doc["replay_id"])
    assert refreshed["promotable"] is True
    assert refreshed["requires_human_review"] is True  # 仍需人工审核，Agent 不能自批


def test_failure_classes_are_specific(rt: Runtime) -> None:
    testcase_id, version = _candidate(rt, oracle_bound=True)
    svc = StabilityReplayService(rt)
    doc = svc.start(testcase_id, version, repeats=3)
    _set_verdict(
        rt,
        doc["records"][0]["run_id"],
        "FAILED",
        {"steps": [{"step_id": "s2", "status": "FAILED", "error": "Locator.wait_for timeout"}]},
    )
    _set_verdict(
        rt,
        doc["records"][1]["run_id"],
        "FAILED",
        {
            "steps": [],
            "assertion_outcomes": [
                {"assertion_id": "a1", "kind": "business_state", "status": "FAILED"}
            ],
        },
    )
    _set_verdict(
        rt,
        doc["records"][2]["run_id"],
        "FAILED",
        {"steps": [{"step_id": "s1", "status": "FAILED", "error": "net::ERR_CONNECTION_REFUSED"}]},
    )
    refreshed = svc.refresh(doc["replay_id"])
    classes = refreshed["classification"]["failure_classes"]
    assert classes["LOCATOR"] == 1
    assert classes["PRODUCT"] == 1  # 业务断言失败 = 产品故障（不再混进 Flaky）
    assert classes["ENVIRONMENT"] == 1
    assert refreshed["classification"]["execution_failures"] == []


def test_repeat_bounds_are_validated(rt: Runtime) -> None:
    testcase_id, version = _candidate(rt)
    svc = StabilityReplayService(rt)
    with pytest.raises(AppError):
        svc.start(testcase_id, version, repeats=0)
    with pytest.raises(AppError):
        svc.start(testcase_id, version, repeats=99)


def test_missing_version_is_not_found(rt: Runtime) -> None:
    svc = StabilityReplayService(rt)
    with pytest.raises(AppError):
        svc.start("tcase_missing", 1, repeats=2)
