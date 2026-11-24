"""RUN-007-RESUME WP2：沙箱执行必须经独立 Worker 消费队列（不得由 API 线程执行）。

验证：
1. 没有 Worker 消费时，沙箱验证 fail-closed（不伪造 PASSED、不生成本地执行结果）；
2. 有 Worker 领取后，沙箱结论来自该 Worker 的真实提交，并记录 Worker ID / fencing token；
3. 沙箱用例始终是独立 testcase_id，不污染正式用例版本线。
"""

from __future__ import annotations

import threading
import time

import pytest
from traceguard_api.runtime import Runtime
from traceguard_api.services.repair_service import RepairService
from traceguard_core.errors import AppError, ErrorCode
from traceguard_persistence import Database


def _payload() -> dict:
    return {
        "testcase_id": "tcase_sbx",
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
                "oracle_id": "oracle_sbx_a1",
            }
        ],
        "run_config": {"base_url": "http://127.0.0.1:8011"},
        "compile_source": "human",
        "created_at": "2026-01-01T00:00:00Z",
    }


@pytest.fixture()
def rt(work_tmp, monkeypatch):  # type: ignore[no-untyped-def]
    import uuid

    base = work_tmp / f"sbx-{uuid.uuid4().hex[:8]}"
    base.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("TG_ARTIFACT_DIR", str(base / "artifacts"))
    from traceguard_core.config import reset_settings_cache

    reset_settings_cache()
    db = Database(f"sqlite:///{base / 'sbx.db'}")
    runtime = Runtime(db)
    runtime.catalog.create_oracle(
        {
            "oracle_id": "oracle_sbx_a1",
            "assertion_id": "a1",
            "expected_behavior": "标题必须存在",
            "requirement_ref": "REQ-SBX-1",
            "version": 1,
        },
        approved=True,
        approved_by="role:qa-lead",
    )
    runtime.catalog.create_testcase(_payload(), title="sbx", review_status="PENDING")
    runtime.catalog.review_testcase("tcase_sbx", 1, "APPROVED", reviewer="role:qa-lead")
    yield runtime
    db.dispose()


def _proposal(rt: Runtime) -> str:
    svc = RepairService(rt)
    prop = svc.propose(
        testcase_id="tcase_sbx",
        base_version=1,
        changes=[
            {
                "path": "steps[].target",
                "step_index": 1,
                "value": {"strategy": "role", "value": "button:新建", "source": "healed"},
                "reason": "定位器自愈",
            }
        ],
        reason="测试提案",
    )
    svc.validate(prop["proposal_id"])
    return str(prop["proposal_id"])


def test_sandbox_fails_closed_without_worker(rt: Runtime) -> None:
    """没有 Worker 消费队列时，不得返回任何"沙箱通过"的结论。"""
    svc = RepairService(rt)
    svc._sandbox_timeout_s = 1.0
    pid = _proposal(rt)
    with pytest.raises(AppError) as exc:
        svc.sandbox_run(pid)
    assert exc.value.code is ErrorCode.PRECONDITION_FAILED
    assert "Worker" in exc.value.message

    doc = svc.get(pid)
    assert doc["sandbox_status"] in {None, "", "NOT_STARTED"}  # 没有伪造 PASSED
    assert doc["status"] == "VALIDATED"  # 未越过审批门禁

    # Run 仍在队列中（未被 API 线程偷偷执行）
    runs = [r for r in rt.tasks.list_runs(limit=50) if r["task_id"].startswith("task_")]
    assert runs
    assert all(r["execution_status"] == "QUEUED" for r in runs)


def test_sandbox_recorded_from_real_worker_submit(rt: Runtime) -> None:
    """有 Worker 领取后：沙箱结论来自该 Worker 提交，并留下执行者与代次。"""
    svc = RepairService(rt)
    svc._sandbox_timeout_s = 15.0
    pid = _proposal(rt)

    result: dict[str, object] = {}

    def fake_worker() -> None:
        deadline = time.time() + 10
        while time.time() < deadline:
            lease = rt.tasks.claim_next_run("w-sandbox-test", ttl_seconds=30)
            if lease:
                rid = lease["run_id"]
                rt.tasks.submit_result(
                    rid,
                    "w-sandbox-test",
                    lease["fencing_token"],
                    execution_status="RUNNING",
                    test_verdict="NOT_EVALUATED",
                )
                rt.tasks.submit_result(
                    rid,
                    "w-sandbox-test",
                    lease["fencing_token"],
                    execution_status="COMPLETED",
                    test_verdict="PASSED",
                    observer_mode="playwright",
                )
                result["run_id"] = rid
                return
            time.sleep(0.05)

    t = threading.Thread(target=fake_worker, daemon=True)
    t.start()
    doc = svc.sandbox_run(pid)
    t.join(timeout=5)

    assert doc["sandbox_status"] == "PASSED"
    assert doc["sandbox_verdict"] == "PASSED"
    assert doc["sandbox_run_id"] == result["run_id"]
    assert doc["sandbox_executed_by"] == "w-sandbox-test"
    assert doc["sandbox_fencing_token"] == 1
    assert doc["sandbox_testcase_id"] != "tcase_sbx"
    # 正式用例版本线不受影响
    assert [v["version"] for v in rt.catalog.list_versions("tcase_sbx")] == [1]
