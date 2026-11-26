"""RUN-014 WP1：Repair → Promotion → Regression → Rollback 真实闭环。

覆盖指令 §四.5 的 12 条验收：
1. 正常 Sandbox → Approval → Promotion；
2/3/4/5. Promotion 成功但未回归 / 回归成功 / 失败 / INCONCLUSIVE；
6. 回归执行中刷新（服务端状态可回读）；
7. 回归重复提交幂等（不重复创建 Run）；
8. Promotion CAS 冲突；
9. Promotion_FAILED 不得显示 Promotion 成功；
10. 回归未执行不得显示完成；
11. 回归失败后人工回滚；
12. 回滚版本与回执正确。

"Worker" 用真实 TaskService 队列语义模拟（claim → mark_running → submit_result），
不是直接改数据库：fencing/幂等/终态冻结都走真实代码路径。真实浏览器回归在 WP4 验收。
"""

from __future__ import annotations

import threading
import time

import pytest
from traceguard_api.runtime import Runtime
from traceguard_api.services.repair_service import RepairService
from traceguard_core.errors import AppError, ErrorCode
from traceguard_persistence import Database


def _payload(testcase_id: str = "tcase_reg", oracle_id: str = "oracle_reg_a1") -> dict:
    return {
        "testcase_id": testcase_id,
        "version": 1,
        "steps": [
            {"step_id": "s1", "action": "navigate", "value": "/projects"},
            {
                "step_id": "s2",
                "action": "click",
                "target": {"strategy": "test_id", "value": "nav-new"},
            },
        ],
        "assertions": [
            {
                "assertion_id": "a1",
                "description": "标题存在",
                "kind": "ui",
                "required": True,
                "expected": {"test_id": "page-title"},
                "oracle_id": oracle_id,
            }
        ],
        "run_config": {"base_url": "http://127.0.0.1:8011"},
        "compile_source": "human",
        "created_at": "2026-01-01T00:00:00Z",
    }


@pytest.fixture()
def rt(work_tmp, monkeypatch):  # type: ignore[no-untyped-def]
    import uuid

    base = work_tmp / f"reg-{uuid.uuid4().hex[:8]}"
    base.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("TG_ARTIFACT_DIR", str(base / "artifacts"))
    from traceguard_core.config import reset_settings_cache

    reset_settings_cache()
    db = Database(f"sqlite:///{base / 'reg.db'}")
    runtime = Runtime(db)
    runtime.catalog.create_oracle(
        {
            "oracle_id": "oracle_reg_a1",
            "assertion_id": "a1",
            "expected_behavior": "标题必须存在",
            "requirement_ref": "REQ-REG-1",
            "version": 1,
        },
        approved=True,
        approved_by="role:qa-lead",
    )
    runtime.catalog.create_testcase(_payload(), title="reg", review_status="PENDING")
    runtime.catalog.review_testcase("tcase_reg", 1, "APPROVED", reviewer="role:qa-lead")
    yield runtime
    db.dispose()


def _proposal_ready_to_promote(rt: Runtime, *, key: str = "p1") -> dict:
    svc = RepairService(rt)
    doc = svc.propose(
        testcase_id="tcase_reg",
        base_version=1,
        changes=[
            {
                "path": "steps[].target",
                "step_index": 1,
                "value": {"strategy": "test_id", "value": "nav-new-healed"},
                "reason": "定位器修复",
            }
        ],
        reason="healed",
        created_by="human:dev",
        actor="human:dev",
        repair_source="human:dev",
    )
    svc.validate(doc["proposal_id"])
    # 沙箱真实执行不在本文件覆盖（WP1 真实链路在 WP4）；直接写入沙箱通过状态
    # 只用于构造"已审批待晋升"的前置状态，不参与回归结论判定。
    doc = svc.get(doc["proposal_id"])
    doc["sandbox_status"] = "PASSED"
    doc["sandbox_verdict"] = "PASSED"
    doc["sandbox_run_id"] = "run_sandbox_fixture"
    svc._save(doc)
    svc.submit_for_approval(doc["proposal_id"])
    svc.approve(doc["proposal_id"], "human:lead", "ok")
    return svc.get(doc["proposal_id"])


def _worker(rt: Runtime, *, verdict: str, execution_status: str = "COMPLETED", evidence: int = 2):
    """模拟独立 Worker：真实 claim → mark_running → submit_result。"""

    def run() -> None:
        deadline = time.time() + 10
        claimed = None
        while time.time() < deadline:
            claimed = rt.tasks.claim_next_run("worker-test", ttl_seconds=60)
            if claimed:
                break
            time.sleep(0.02)
        if not claimed:
            return
        token = int(claimed["fencing_token"])
        rt.tasks.mark_running(claimed["run_id"], "worker-test", token, "playwright")
        rt.tasks.submit_result(
            claimed["run_id"],
            "worker-test",
            token,
            execution_status=execution_status,
            test_verdict=verdict,
            report={
                "run_id": claimed["run_id"],
                "evidence_ids": [f"evd_reg_{i}" for i in range(evidence)],
                "errors": [] if verdict == "PASSED" else ["assertion failed"],
                "execution_status": execution_status,
                "test_verdict": verdict,
            },
            observer_mode="playwright",
            error=None if verdict == "PASSED" else "assertion failed",
        )

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread


def test_new_proposal_has_not_started_regression(rt: Runtime) -> None:
    svc = RepairService(rt)
    doc = svc.propose(
        testcase_id="tcase_reg",
        base_version=1,
        changes=[
            {
                "path": "steps[].target",
                "step_index": 1,
                "value": {"strategy": "test_id", "value": "x"},
            }
        ],
        created_by="human:dev",
        actor="human:dev",
    )
    assert doc["regression_status"] == "NOT_STARTED"
    assert doc["regression_run_id"] is None


def test_promotion_success_does_not_imply_regression(rt: Runtime) -> None:
    """晋升成功后 Regression 仍必须是 NOT_STARTED（旧 UI 把两者画成同一步）。"""
    svc = RepairService(rt)
    doc = _proposal_ready_to_promote(rt)
    promoted = svc.promote(doc["proposal_id"], idempotency_key="promo-1")
    assert promoted["status"] == "PROMOTED"
    assert promoted["promoted_version"] == 2
    assert promoted["promotion_record"]["check"] == "CAS_OK"
    assert promoted["regression_status"] == "NOT_STARTED"
    assert not promoted.get("regression_run_id")


def test_regression_requires_promoted(rt: Runtime) -> None:
    svc = RepairService(rt)
    doc = RepairService(rt).propose(
        testcase_id="tcase_reg",
        base_version=1,
        changes=[
            {
                "path": "steps[].target",
                "step_index": 1,
                "value": {"strategy": "test_id", "value": "y"},
            }
        ],
        created_by="human:dev",
        actor="human:dev",
    )
    with pytest.raises(AppError) as exc:
        svc.regression_run(doc["proposal_id"])
    assert exc.value.code is ErrorCode.PRECONDITION_FAILED


def test_regression_passed_uses_real_run(rt: Runtime) -> None:
    svc = RepairService(rt)
    doc = _proposal_ready_to_promote(rt)
    svc.promote(doc["proposal_id"], idempotency_key="promo-pass")
    _worker(rt, verdict="PASSED")
    out = svc.regression_run(doc["proposal_id"], timeout_s=10)
    assert out["regression_status"] == "PASSED"
    assert out["regression_verdict"] == "PASSED"
    assert out["regression_run_id"]
    assert out["regression_testcase_version_id"] == "tcase_reg@2"
    assert out["regression_evidence_ids"]
    assert out["regression_failure_reason"] == ""
    run = rt.tasks.get_run(out["regression_run_id"])
    assert run is not None and run["execution_status"] == "COMPLETED"
    assert out["regression_history"][-1]["run_id"] == out["regression_run_id"]


def test_regression_failed_then_manual_rollback(rt: Runtime) -> None:
    svc = RepairService(rt)
    doc = _proposal_ready_to_promote(rt)
    svc.promote(doc["proposal_id"], idempotency_key="promo-fail")
    _worker(rt, verdict="FAILED", execution_status="COMPLETED")
    out = svc.regression_run(doc["proposal_id"], timeout_s=10)
    assert out["regression_status"] == "FAILED"
    assert out["regression_failure_reason"] == "REGRESSION_ASSERTION_FAILED"
    assert out["regression_evidence_ids"]  # 原始证据保留
    assert out["promoted_version"] == 2  # 失败不回滚版本记录

    rolled = svc.rollback(doc["proposal_id"], "human:admin", "回归失败")
    assert rolled["status"] == "ROLLED_BACK"
    assert rolled["rollback_record"]["restored_version"] == 3
    assert rolled["rollback_record"]["target_version"] == "tcase_reg:v2"
    assert rolled["regression_stale"] is True
    assert rt.catalog.approved_version_number("tcase_reg") == 3


def test_regression_inconclusive_is_not_passed(rt: Runtime) -> None:
    svc = RepairService(rt)
    doc = _proposal_ready_to_promote(rt)
    svc.promote(doc["proposal_id"], idempotency_key="promo-incon")
    _worker(rt, verdict="INCONCLUSIVE", execution_status="COMPLETED")
    out = svc.regression_run(doc["proposal_id"], timeout_s=10)
    assert out["regression_status"] == "INCONCLUSIVE"
    assert out["regression_status"] != "PASSED"


def test_regression_without_evidence_is_fail_closed(rt: Runtime) -> None:
    svc = RepairService(rt)
    doc = _proposal_ready_to_promote(rt)
    svc.promote(doc["proposal_id"], idempotency_key="promo-noev")
    _worker(rt, verdict="PASSED", execution_status="COMPLETED", evidence=0)
    out = svc.regression_run(doc["proposal_id"], timeout_s=10)
    assert out["regression_status"] == "INCONCLUSIVE"
    assert out["regression_failure_reason"] == "REGRESSION_EVIDENCE_MISSING"


def test_regression_run_failure_is_not_passed(rt: Runtime) -> None:
    svc = RepairService(rt)
    doc = _proposal_ready_to_promote(rt)
    svc.promote(doc["proposal_id"], idempotency_key="promo-runfail")
    _worker(rt, verdict="NOT_EVALUATED", execution_status="FAILED")
    out = svc.regression_run(doc["proposal_id"], timeout_s=10)
    assert out["regression_status"] == "INCONCLUSIVE"
    assert out["regression_failure_reason"].startswith("REGRESSION_RUN_")


def test_regression_inflight_refresh_then_settle_without_duplicate_run(rt: Runtime) -> None:
    """执行中刷新必须显示 RUNNING；再次提交只收敛同一 Run，不重复创建。"""
    svc = RepairService(rt)
    doc = _proposal_ready_to_promote(rt)
    svc.promote(doc["proposal_id"], idempotency_key="promo-inflight")
    first = svc.regression_run(doc["proposal_id"], timeout_s=0.6)
    assert first["regression_status"] in {"QUEUED", "RUNNING"}
    run_id = first["regression_run_id"]
    assert run_id
    snapshot = svc.regression_status(doc["proposal_id"])
    assert snapshot["regression_status"] in {"QUEUED", "RUNNING"}
    assert snapshot["run"] is not None

    _worker(rt, verdict="PASSED")
    second = svc.regression_run(doc["proposal_id"], timeout_s=10)
    assert second["regression_status"] == "PASSED"
    assert second["regression_run_id"] == run_id
    assert len(rt.tasks.list_runs(task_id=second["regression_task_id"])) == 1


def test_regression_repeat_submit_is_idempotent(rt: Runtime) -> None:
    svc = RepairService(rt)
    doc = _proposal_ready_to_promote(rt)
    svc.promote(doc["proposal_id"], idempotency_key="promo-idem")
    _worker(rt, verdict="PASSED")
    first = svc.regression_run(doc["proposal_id"], timeout_s=10)
    second = svc.regression_run(doc["proposal_id"], timeout_s=10)
    third = svc.regression_run(doc["proposal_id"], timeout_s=10)
    assert first["regression_run_id"] == second["regression_run_id"] == third["regression_run_id"]
    assert len(rt.tasks.list_runs(task_id=first["regression_task_id"])) == 1


def test_regression_oracle_missing_fail_closed(rt: Runtime) -> None:
    """晋升版本绑定的 Oracle 已不可读 → 不创建 Run，直接 INCONCLUSIVE（fail-closed）。"""
    from sqlalchemy import delete
    from traceguard_persistence.models import OracleRow

    svc = RepairService(rt)
    doc = _proposal_ready_to_promote(rt)
    svc.promote(doc["proposal_id"], idempotency_key="promo-oracle")
    with rt.db.session() as s:
        s.execute(delete(OracleRow).where(OracleRow.oracle_id == "oracle_reg_a1"))
    out = svc.regression_run(doc["proposal_id"], timeout_s=1)
    assert out["regression_status"] == "INCONCLUSIVE"
    assert out["regression_failure_reason"] == "REGRESSION_ORACLE_UNAVAILABLE"
    assert not out.get("regression_run_id")
    assert out["regression_oracle_refs"][0]["status"] == "MISSING"


def test_promotion_cas_conflict_never_reports_success(rt: Runtime) -> None:
    svc = RepairService(rt)
    first = _proposal_ready_to_promote(rt)
    second = _proposal_ready_to_promote(rt)
    svc.promote(first["proposal_id"], idempotency_key="cas-a")
    loser = svc.promote(second["proposal_id"], idempotency_key="cas-b")
    assert loser["status"] == "PROMOTION_FAILED"
    assert loser["promotion_record"]["result"] == "VERSION_CONFLICT"
    assert not loser.get("promoted_version")
    # 与前端同一判定：只有 promoted_version>0 + PROMOTED + CAS_OK 才算晋升成功
    assert not (
        loser.get("promoted_version")
        and loser["status"] in {"PROMOTED", "ROLLED_BACK"}
        and loser["promotion_record"].get("check") == "CAS_OK"
    )
    assert loser["regression_status"] == "NOT_STARTED"


def test_regression_endpoint_permission_mapping() -> None:
    """RBAC：回归验证是正式资产动作，qa-lead 及以上；viewer/qa-engineer 只能读。"""
    from traceguard_api.auth import action_for_request
    from traceguard_persistence.auth import PERMISSION_MATRIX, has_permission

    path = "/api/v1/repairs/rp_1/regression-run"
    assert action_for_request("POST", path) == "repair.regression"
    # 查看回归状态属于只读能力（viewer 可读），写动作才是 repair.regression
    assert action_for_request("GET", "/api/v1/repairs/rp_1/regression") == "read"
    assert "repair.regression" in PERMISSION_MATRIX["qa-lead"]
    assert "repair.regression" in PERMISSION_MATRIX["platform-admin"]
    assert "repair.regression" not in PERMISSION_MATRIX["qa-engineer"]
    assert "repair.regression" not in PERMISSION_MATRIX["viewer"]
    assert has_permission(["qa-lead"], "repair.regression") is True
    assert has_permission(["qa-engineer"], "repair.regression") is False


def test_run_config_change_with_explicit_none_step_index(rt: Runtime) -> None:
    """RUN-014 WP4 实测缺陷：HTTP 层 model_dump() 总带 step_index=None。

    旧 `_step_index` 用 `"step_index" in change` 判断，会执行 int(None) → TypeError → 500。
    这里用与 HTTP 完全相同的载荷形状（step_index=None）验证不再抛错。
    """
    svc = RepairService(rt)
    doc = svc.propose(
        testcase_id="tcase_reg",
        base_version=1,
        changes=[
            {
                "path": "run_config.wait_timeout",
                "step_index": None,
                "value": 8000,
                "reason": "等待上限",
            }
        ],
        created_by="human:dev",
        actor="human:dev",
    )
    assert doc["status"] == "VALIDATED"
    assert doc["diff"][0]["step_index"] is None
