"""RUN-005 WP2 F08：候选用例稳定性回放测试（不启真实浏览器）。

覆盖：
- 每次回放产生独立 Run，不覆盖历史；
- 走 sandbox Task 模式（不放开正式入口绕过审核）；
- Oracle 不完整时不可晋升、不得声称业务预期已验证；
- 失败分类区分 Locator / Timing / Environment / Business / UI，不一律 Flaky。
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
    payload = {
        "testcase_id": testcase_id,
        "version": 1,
        "intent_ref": "intent_x",
        "steps": [
            {"step_id": "s1", "action": "navigate", "value": "http://127.0.0.1:8011/"},
            {"step_id": "s2", "action": "click", "target": {"strategy": "test_id", "value": "create"}},
        ],
        "assertions": [
            {
                "assertion_id": "a1",
                "description": "成功提示",
                "kind": "ui",
                "required": True,
                "expected": {"test_id": "banner-success"},
                "oracle_id": "oracle_1" if oracle_bound else None,
            }
        ],
        "review_status": "PENDING",
        "created_at": "2026-01-01T00:00:00Z",
    }
    created = rt.catalog.create_testcase(payload, title="回放候选", review_status="PENDING")
    return testcase_id, created["version"]


def _set_verdict(rt: Runtime, run_id: str, verdict: str, report: dict | None = None) -> None:
    with rt.db.session() as s:
        row = s.get(RunRow, run_id)
        row.test_verdict = verdict
        row.execution_status = "COMPLETED"
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
    assert doc["requires_human_review"] is True


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
    assert classes["BUSINESS"] == 1
    assert classes["ENVIRONMENT"] == 1


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
