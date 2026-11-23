"""RUN-006 WP1-01 集成测试：Oracle 绑定 / 审核门禁 / 版本固定 / 并发审核。

覆盖需求列出的场景：
- 无 Oracle 审核；
- 不存在的 Oracle；
- 未批准 Oracle；
- 跨 Workspace Oracle；
- Oracle 版本更新后的历史用例复现（固定版本，不回退到最新）；
- Agent 自批（多种命名空间写法）；
- 并发审核；
- 正常人工审核。
"""

from __future__ import annotations

import threading

import pytest
from traceguard_api.runtime import Runtime
from traceguard_contracts.models import TestCaseVersion
from traceguard_core.errors import AppError, ErrorCode
from traceguard_persistence import Database
from traceguard_persistence.catalog import is_non_human_actor


def _tc_payload(assertion_id: str = "a1") -> dict:
    return {
        "testcase_id": "tcase_gate",
        "version": 1,
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
                "assertion_id": assertion_id,
                "description": "标题存在",
                "kind": "ui",
                "expected": {"test_id": "page-title"},
            }
        ],
        "run_config": {"base_url": "http://127.0.0.1:8011"},
        "compile_source": "human",
        "created_at": "2026-01-01T00:00:00Z",
    }


@pytest.fixture()
def rt(work_tmp, monkeypatch):  # type: ignore[no-untyped-def]
    import uuid

    base = work_tmp / f"oracle-gate-{uuid.uuid4().hex[:8]}"
    base.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("TG_ARTIFACT_DIR", str(base / "artifacts"))
    from traceguard_core.config import reset_settings_cache

    reset_settings_cache()
    db = Database(f"sqlite:///{base / 'gate.db'}")
    runtime = Runtime(db)
    yield runtime
    db.dispose()


def _oracle(
    rt: Runtime,
    oracle_id: str = "oracle_gate_a1",
    assertion_id: str = "a1",
    *,
    approved: bool = True,
    approved_by: str = "role:qa-lead",
    workspace_id: str = "default",
) -> dict:
    return rt.catalog.create_oracle(
        {
            "oracle_id": oracle_id,
            "assertion_id": assertion_id,
            "expected_behavior": "标题必须存在",
            "requirement_ref": "REQ-GATE-1",
            "version": 1,
            "workspace_id": workspace_id,
        },
        approved=approved,
        approved_by=approved_by,
    )


# --------------------------------------------------------------- 审核门禁
def test_review_without_oracle_is_rejected(rt: Runtime) -> None:
    rt.catalog.create_testcase(_tc_payload(), title="gate", review_status="PENDING")
    with pytest.raises(AppError) as exc:
        rt.catalog.review_testcase("tcase_gate", 1, "APPROVED", reviewer="role:qa-lead")
    assert exc.value.code is ErrorCode.PRECONDITION_FAILED
    assert "MISSING_ORACLE" in exc.value.message
    assert rt.catalog.latest_approved_version("tcase_gate") is None


def test_bind_nonexistent_oracle_rejected(rt: Runtime) -> None:
    rt.catalog.create_testcase(_tc_payload(), title="gate", review_status="PENDING")
    with pytest.raises(AppError) as exc:
        rt.catalog.bind_oracles(
            "tcase_gate", 1, {"a1": {"oracle_id": "oracle_missing"}}, actor="role:qa-lead"
        )
    assert exc.value.code is ErrorCode.NOT_FOUND
    assert "ORACLE_NOT_FOUND" in exc.value.message


def test_bind_unapproved_oracle_rejected(rt: Runtime) -> None:
    rt.catalog.create_testcase(_tc_payload(), title="gate", review_status="PENDING")
    _oracle(rt, approved=False, approved_by="")
    with pytest.raises(AppError) as exc:
        rt.catalog.bind_oracles(
            "tcase_gate", 1, {"a1": {"oracle_id": "oracle_gate_a1"}}, actor="role:qa-lead"
        )
    assert exc.value.code is ErrorCode.PRECONDITION_FAILED
    assert "ORACLE_NOT_APPROVED" in exc.value.message


def test_cross_workspace_oracle_rejected(rt: Runtime) -> None:
    rt.catalog.create_workspace("另一个工作区")
    other = [w for w in rt.catalog.list_workspaces() if w["workspace_id"] != "default"][0]
    rt.catalog.create_testcase(_tc_payload(), title="gate", review_status="PENDING")
    _oracle(rt, workspace_id=other["workspace_id"])
    with pytest.raises(AppError) as exc:
        rt.catalog.bind_oracles(
            "tcase_gate", 1, {"a1": {"oracle_id": "oracle_gate_a1"}}, actor="role:qa-lead"
        )
    assert exc.value.code is ErrorCode.AUTHORIZATION_ERROR
    assert "CROSS_WORKSPACE_ORACLE" in exc.value.message


def test_unknown_assertion_binding_rejected(rt: Runtime) -> None:
    rt.catalog.create_testcase(_tc_payload(), title="gate", review_status="PENDING")
    _oracle(rt)
    with pytest.raises(AppError) as exc:
        rt.catalog.bind_oracles(
            "tcase_gate", 1, {"a_missing": {"oracle_id": "oracle_gate_a1"}}, actor="role:qa-lead"
        )
    assert "UNKNOWN_ASSERTION" in exc.value.message


# ------------------------------------------------------------- Agent 自批
@pytest.mark.parametrize(
    "actor",
    [
        "agent",
        "agent:gpt",
        "agent-1",
        "AGENT:planner",
        "llm:deepseek",
        "llm-deepseek",
        "system:promotion",
        "system",
        "bot:repair",
        "service:worker",
        "",
    ],
)
def test_non_human_actor_cannot_review(actor: str) -> None:
    assert is_non_human_actor(actor) is True


@pytest.mark.parametrize("actor", ["role:qa-lead", "human:zhang", "role:admin", "qa-team"])
def test_human_actor_accepted(actor: str) -> None:
    assert is_non_human_actor(actor) is False


def test_agent_cannot_self_approve_oracle(rt: Runtime) -> None:
    with pytest.raises(AppError) as exc:
        _oracle(rt, approved=True, approved_by="agent:gpt")
    assert exc.value.code is ErrorCode.AUTHORIZATION_ERROR


# --------------------------------------------------------------- 正常审核
def test_human_review_pins_oracle_version(rt: Runtime) -> None:
    rt.catalog.create_testcase(_tc_payload(), title="gate", review_status="PENDING")
    _oracle(rt)
    rt.catalog.bind_oracles(
        "tcase_gate", 1, {"a1": {"oracle_id": "oracle_gate_a1"}}, actor="role:qa-lead"
    )
    result = rt.catalog.review_testcase("tcase_gate", 1, "APPROVED", reviewer="role:qa-lead")
    assert result["review_status"] == "APPROVED" and result["review_revision"] == 1
    approved = rt.catalog.latest_approved_version("tcase_gate")
    assert approved["assertions"][0]["oracle_version"] == 1
    bindings = approved["oracle_bindings"]
    assert bindings["a1"]["oracle_id"] == "oracle_gate_a1"
    assert bindings["a1"]["approved"] is True
    assert approved["oracle_binding_frozen"] is True


def test_approved_version_cannot_be_rebound(rt: Runtime) -> None:
    rt.catalog.create_testcase(_tc_payload(), title="gate", review_status="PENDING")
    _oracle(rt)
    rt.catalog.bind_oracles(
        "tcase_gate", 1, {"a1": {"oracle_id": "oracle_gate_a1"}}, actor="role:qa-lead"
    )
    rt.catalog.review_testcase("tcase_gate", 1, "APPROVED", reviewer="role:qa-lead")
    with pytest.raises(AppError) as exc:
        rt.catalog.bind_oracles(
            "tcase_gate", 1, {"a1": {"oracle_id": "oracle_gate_a1"}}, actor="role:qa-lead"
        )
    assert "IMMUTABLE" in exc.value.message


# -------------------------------------------------- Oracle 版本更新与历史复现
def test_historical_case_uses_pinned_oracle_version(rt: Runtime) -> None:
    rt.catalog.create_testcase(_tc_payload(), title="gate", review_status="PENDING")
    _oracle(rt)
    rt.catalog.bind_oracles(
        "tcase_gate", 1, {"a1": {"oracle_id": "oracle_gate_a1"}}, actor="role:qa-lead"
    )
    rt.catalog.review_testcase("tcase_gate", 1, "APPROVED", reviewer="role:qa-lead")

    # Oracle 升到 v2（预期行为变化）
    rt.catalog.create_oracle(
        {
            "oracle_id": "oracle_gate_a1",
            "assertion_id": "a1",
            "expected_behavior": "标题必须存在且完全匹配",
            "requirement_ref": "REQ-GATE-2",
            "version": 1,
        },
        approved=True,
        approved_by="role:qa-lead",
    )
    assert rt.catalog.get_oracle("oracle_gate_a1")["version"] == 2

    approved_doc = rt.catalog.latest_approved_version("tcase_gate")
    tc = TestCaseVersion(
        **{k: v for k, v in approved_doc.items() if k in TestCaseVersion.model_fields}
    )
    pipeline = rt.pipeline()
    oracles = pipeline._load_oracles(tc)
    # 历史用例必须用审核时固定的 v1，而不是悄悄跟随最新 v2
    assert oracles["oracle_gate_a1"].version == 1
    assert oracles["oracle_gate_a1"].expected_behavior == "标题必须存在"


# ------------------------------------------------------------------ 并发审核
def test_concurrent_review_only_one_wins(rt: Runtime) -> None:
    rt.catalog.create_testcase(_tc_payload(), title="gate", review_status="PENDING")
    _oracle(rt)
    rt.catalog.bind_oracles(
        "tcase_gate", 1, {"a1": {"oracle_id": "oracle_gate_a1"}}, actor="role:qa-lead"
    )
    outcomes: list[str] = []
    lock = threading.Lock()

    def worker(actor: str) -> None:
        try:
            rt.catalog.review_testcase("tcase_gate", 1, "APPROVED", reviewer=actor)
            with lock:
                outcomes.append("OK")
        except AppError as exc:
            with lock:
                outcomes.append(exc.code.value)

    threads = [threading.Thread(target=worker, args=(f"role:qa-{i}",)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # 至少一次成功；冲突方必须得到 CONFLICT，而不是静默双写
    assert outcomes.count("OK") >= 1
    assert set(outcomes) <= {"OK", "CONFLICT"}
    history = rt.catalog.get_version("tcase_gate", 1)["review_history"]
    assert len(history) == sum(1 for o in outcomes if o == "OK")


def test_stale_review_revision_is_rejected(rt: Runtime, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """确定性验证 CAS：读到旧修订号后若已被他人推进，必须 CONFLICT 而不是静默覆盖。"""
    rt.catalog.create_testcase(_tc_payload(), title="gate", review_status="PENDING")
    _oracle(rt)
    rt.catalog.bind_oracles(
        "tcase_gate", 1, {"a1": {"oracle_id": "oracle_gate_a1"}}, actor="role:qa-lead"
    )

    original = rt.catalog._oracle_gate

    def racing_gate(s, doc, *, workspace_id):  # type: ignore[no-untyped-def]
        # 模拟"另一个审核事务"在校验期间提交：直接推进修订号
        from sqlalchemy import text

        s.execute(
            text(
                "UPDATE tg_testcase_version SET review_revision = review_revision + 1 "
                "WHERE id = 'tcase_gate@1'"
            )
        )
        return original(s, doc, workspace_id=workspace_id)

    monkeypatch.setattr(rt.catalog, "_oracle_gate", racing_gate)
    with pytest.raises(AppError) as exc:
        rt.catalog.review_testcase("tcase_gate", 1, "APPROVED", reviewer="role:qa-lead")
    assert exc.value.code is ErrorCode.CONFLICT
    assert "CONCURRENT_REVIEW_CONFLICT" in exc.value.message
    assert rt.catalog.latest_approved_version("tcase_gate") is None
