"""RUN-007 WP1-01：F08 稳定性回放接入正式审核门禁的测试。

断言：
- Agent 生成的候选（compile_source=compiler/healed）没有回放 → 412，不得 APPROVED；
- 回放未完成 / 非 STABLE_PASS / Oracle 不完整 → 412；
- 旧版本的 STABLE_PASS 回放不能用于新版本；
- 合法回放 → 允许 APPROVED，且 `review_history` 留有回放证据；
- 人工编写版本（compile_source=human）显式记录豁免事实，不是静默跳过；
- 稳定性门禁不替代人工审核（仍必须由人类主体审批）。
"""

from __future__ import annotations

import uuid

import pytest
from traceguard_api.runtime import Runtime
from traceguard_api.services.stability_service import StabilityReplayService
from traceguard_core.errors import AppError, ErrorCode
from traceguard_core.ids import new_id
from traceguard_persistence import Database
from traceguard_persistence.models import RunRow


@pytest.fixture()
def rt(work_tmp, monkeypatch):  # type: ignore[no-untyped-def]
    base = work_tmp / f"gate-{uuid.uuid4().hex[:8]}"
    base.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("TG_ARTIFACT_DIR", str(base / "artifacts"))
    from traceguard_core.config import reset_settings_cache

    reset_settings_cache()
    db = Database(f"sqlite:///{base / 'gate.db'}")
    runtime = Runtime(db)
    yield runtime
    db.dispose()


def _candidate(
    rt: Runtime, *, version: int = 1, compile_source: str = "compiler"
) -> tuple[str, int]:
    testcase_id = new_id("tcase")
    oracle_id = new_id("oracle")
    rt.catalog.create_oracle(
        {
            "oracle_id": oracle_id,
            "assertion_id": "a1",
            "expected_behavior": "成功提示必须出现",
            "requirement_ref": "REQ-GATE-1",
            "version": 1,
        },
        approved=True,
        approved_by="role:qa-lead",
    )
    payload = {
        "testcase_id": testcase_id,
        "version": version,
        "intent_ref": "intent_gate",
        "compile_source": compile_source,
        "steps": [{"step_id": "s1", "action": "navigate", "value": "http://127.0.0.1:8011/"}],
        "assertions": [
            {
                "assertion_id": "a1",
                "description": "成功提示",
                "kind": "ui",
                "required": True,
                "expected": {"test_id": "banner-success"},
                "oracle_id": oracle_id,
                "oracle_version": 1,
            }
        ],
        "review_status": "PENDING",
        "created_at": "2026-01-01T00:00:00Z",
    }
    created = rt.catalog.create_testcase(payload, title="门禁候选", review_status="PENDING")
    return testcase_id, int(created["version"])


def _run_replay(rt: Runtime, testcase_id: str, version: int, verdicts: list[str]) -> dict:
    svc = StabilityReplayService(rt)
    doc = svc.start(testcase_id, version, repeats=len(verdicts))
    for record, verdict in zip(doc["records"], verdicts, strict=False):
        with rt.db.session() as s:
            row = s.get(RunRow, record["run_id"])
            row.test_verdict = verdict
            row.execution_status = "COMPLETED" if verdict != "PENDING" else "QUEUED"
            row.report_json = {"steps": [], "assertion_outcomes": []}
    return svc.refresh(doc["replay_id"])


def _approve(rt: Runtime, testcase_id: str, version: int, **kw) -> dict:
    return rt.catalog.review_testcase(
        testcase_id, version, "APPROVED", reviewer="role:qa-lead", **kw
    )


def test_compiled_candidate_without_replay_is_blocked(rt: Runtime) -> None:
    testcase_id, version = _candidate(rt)
    with pytest.raises(AppError) as exc:
        _approve(rt, testcase_id, version)
    assert exc.value.code == ErrorCode.PRECONDITION_FAILED
    assert "STABILITY_REPLAY_REQUIRED" in exc.value.message
    assert rt.catalog.get_version(testcase_id, version)["review_status"] == "PENDING"


def test_unfinished_replay_blocks_approval(rt: Runtime) -> None:
    testcase_id, version = _candidate(rt)
    _run_replay(rt, testcase_id, version, ["PASSED", "PENDING"])
    with pytest.raises(AppError) as exc:
        _approve(rt, testcase_id, version)
    assert exc.value.code == ErrorCode.PRECONDITION_FAILED
    assert "STABILITY_REPLAY" in exc.value.message


def test_flaky_replay_blocks_approval(rt: Runtime) -> None:
    testcase_id, version = _candidate(rt)
    _run_replay(rt, testcase_id, version, ["PASSED", "FAILED"])
    with pytest.raises(AppError) as exc:
        _approve(rt, testcase_id, version)
    assert exc.value.code == ErrorCode.PRECONDITION_FAILED
    assert "STABILITY_NOT_STABLE_PASS" in exc.value.message


def test_not_evaluated_replay_blocks_approval(rt: Runtime) -> None:
    testcase_id, version = _candidate(rt)
    _run_replay(rt, testcase_id, version, ["NOT_EVALUATED", "NOT_EVALUATED"])
    with pytest.raises(AppError) as exc:
        _approve(rt, testcase_id, version)
    assert exc.value.code == ErrorCode.PRECONDITION_FAILED
    # 关键：不是"确定性失败"，而是稳定性未达标/证据不足
    assert "STABILITY_NOT_STABLE_PASS" in exc.value.message


def test_stable_replay_allows_approval_and_leaves_evidence(rt: Runtime) -> None:
    testcase_id, version = _candidate(rt)
    replay = _run_replay(rt, testcase_id, version, ["PASSED", "PASSED", "PASSED"])
    assert replay["promotable"] is True
    result = _approve(rt, testcase_id, version)
    assert result["review_status"] == "APPROVED"
    version_doc = rt.catalog.get_version(testcase_id, version)
    history = version_doc["review_history"][-1]
    assert history["stability_policy"] == "ENFORCED"
    assert history["stability_evidence"]["replay_id"] == replay["replay_id"]
    assert history["stability_evidence"]["classification"] == "STABLE_PASS"


def test_old_version_replay_does_not_apply_to_new_version(rt: Runtime) -> None:
    testcase_id, version1 = _candidate(rt)
    _run_replay(rt, testcase_id, version1, ["PASSED", "PASSED"])
    # 新版本（同一 testcase）没有自己的回放
    import copy

    doc1 = rt.catalog.get_version(testcase_id, version1)
    payload = copy.deepcopy(doc1)
    payload["version"] = 2
    payload["review_status"] = "PENDING"
    payload["review_history"] = []
    rt.catalog.create_testcase(payload, title="门禁候选 v2", review_status="PENDING")
    with pytest.raises(AppError) as exc:
        _approve(rt, testcase_id, 2)
    assert "STABILITY_REPLAY_REQUIRED" in exc.value.message


def test_human_authored_version_records_exemption(rt: Runtime) -> None:
    testcase_id, version = _candidate(rt, compile_source="human")
    result = _approve(rt, testcase_id, version)
    assert result["review_status"] == "APPROVED"
    history = rt.catalog.get_version(testcase_id, version)["review_history"][-1]
    assert history["stability_policy"] == "EXEMPT_HUMAN_AUTHORED"
    assert history["stability_evidence"] is None


def test_agent_cannot_use_stability_gate_as_approval(rt: Runtime) -> None:
    """门禁只校验"有稳定证据"，审批主体仍必须是人。"""
    testcase_id, version = _candidate(rt)
    _run_replay(rt, testcase_id, version, ["PASSED", "PASSED"])
    with pytest.raises(AppError) as exc:
        rt.catalog.review_testcase(testcase_id, version, "APPROVED", reviewer="agent:auto-fix")
    assert exc.value.code == ErrorCode.AUTHORIZATION_ERROR


def test_healed_candidate_also_requires_replay(rt: Runtime) -> None:
    testcase_id, version = _candidate(rt, compile_source="healed")
    with pytest.raises(AppError) as exc:
        _approve(rt, testcase_id, version)
    assert "STABILITY_REPLAY_REQUIRED" in exc.value.message


# ---------------------------------------------------------------------------
# RUN-011 WP1 回归：非 default Workspace 的用例必须能通过 Oracle 完整性检查
# ---------------------------------------------------------------------------
def test_replay_oracle_integrity_uses_testcase_workspace(rt: Runtime) -> None:
    """workspace 归属是行属性，不在版本 doc JSON 里。

    旧实现 `base.get("workspace_id", "default")` 把非 default Workspace 的用例
    当成 default，导致 Oracle 完整性恒为 ORACLE_WORKSPACE_MISMATCH → promotable
    永远 false（真实闭环中表现为"回放 STABLE_PASS 但不可晋升"）。
    """
    tc_id = new_id("tcase")
    oracle_id = new_id("oracle")
    workspace = "ws-gate-non-default"
    rt.catalog.create_oracle(
        {
            "oracle_id": oracle_id,
            "assertion_id": "a1",
            "expected_behavior": "成功提示必须出现",
            "requirement_ref": "REQ-GATE-WS",
            "version": 1,
            "workspace_id": workspace,
        },
        approved=True,
        approved_by="role:qa-lead",
    )
    rt.catalog.create_testcase(
        {
            "testcase_id": tc_id,
            "version": 1,
            "intent_ref": "intent_gate",
            "compile_source": "human",
            "steps": [{"step_id": "s1", "action": "navigate", "value": "/projects"}],
            "assertions": [
                {
                    "assertion_id": "a1",
                    "description": "成功提示",
                    "kind": "ui",
                    "required": True,
                    "expected": {"test_id": "banner-success"},
                    "oracle_id": oracle_id,
                    "oracle_version": 1,
                }
            ],
            "review_status": "PENDING",
            "created_at": "2026-01-01T00:00:00Z",
        },
        title="非默认 workspace 用例",
        workspace_id=workspace,
        review_status="PENDING",
    )
    version_doc = rt.catalog.get_version(tc_id, 1)
    assert version_doc["workspace_id"] == workspace
    assert version_doc["testcase_id"] == tc_id
    doc = StabilityReplayService(rt).start(tc_id, 1, repeats=1, requested_by="role:qa-lead")
    assert doc["workspace_id"] == workspace
    assert doc["oracle_complete"] is True, doc.get("oracle_problems")


def test_oracle_version_changed_after_replay_blocks_approval(rt: Runtime) -> None:
    """RUN-013 WP3：回放固定 Oracle v1，审核前改绑 v2 → 必须重跑回放。

    缺少该校验时，旧回放的 STABLE_PASS 会被用来批准"用新业务预期写的断言"。
    """
    testcase_id, version = _candidate(rt)
    _run_replay(rt, testcase_id, version, ["PASSED", "PASSED", "PASSED"])
    oracle_id = rt.catalog.get_version(testcase_id, version)["assertions"][0]["oracle_id"]

    rt.catalog.create_oracle(
        {
            "oracle_id": oracle_id,
            "assertion_id": "a1",
            "expected_behavior": "成功提示必须出现（v2 语义已变化）",
            "requirement_ref": "REQ-GATE-1",
            "version": 2,
        },
        approved=True,
        approved_by="role:qa-lead",
    )
    rt.catalog.bind_oracles(
        testcase_id,
        version,
        {"a1": {"oracle_id": oracle_id, "oracle_version": 2}},
        actor="role:qa-lead",
    )
    with pytest.raises(AppError) as exc:
        _approve(rt, testcase_id, version)
    assert exc.value.code == ErrorCode.PRECONDITION_FAILED
    assert "STABILITY_ORACLE_VERSION_CHANGED" in exc.value.message
    assert rt.catalog.get_version(testcase_id, version)["review_status"] == "PENDING"


def test_oracle_revoked_after_replay_blocks_approval(rt: Runtime) -> None:
    """回放后 Oracle 审批被撤销：不得沿用旧回放结论批准。"""
    from traceguard_persistence.models import OracleRow

    testcase_id, version = _candidate(rt)
    _run_replay(rt, testcase_id, version, ["PASSED", "PASSED", "PASSED"])
    oracle_id = rt.catalog.get_version(testcase_id, version)["assertions"][0]["oracle_id"]
    with rt.db.session() as s:
        row = s.get(OracleRow, f"{oracle_id}@1")
        assert row is not None
        row.approved = False
    with pytest.raises(AppError) as exc:
        _approve(rt, testcase_id, version)
    # Oracle 绑定门禁会先拦截（更早的防线）；回放重核验是第二道防线。
    assert "ORACLE_NOT_APPROVED" in exc.value.message
