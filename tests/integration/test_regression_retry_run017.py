"""RUN-017 WP3：Regression INCONCLUSIVE 人工授权重试（方案 A：新 Attempt）。

覆盖指令 §5.3 的 14 条：

1. 正常 INCONCLUSIVE 授权重试（新 Run、Attempt 2、血缘正确）；
2. 缺少 confirm 拒绝；
3. Agent 主体拒绝；
4. 无权限角色（RBAC 映射只给 qa-lead / platform-admin）；
5. 不允许的原因码（Oracle 缺失）拒绝；
6. FAILED 拒绝重跑；
7. STALE 拒绝重跑；
8. 最大次数限制；
9. 并发重复点击只产生一个新 Attempt；
10. 幂等请求返回同一 Run；
11. 重启后历史可恢复；
12. 新旧 Run 血缘；
13. 旧 Evidence 不被覆盖；
14. 旧 history 不被改写。

"Worker" 用真实 TaskService 队列语义（claim → mark_running → submit_result），
不是直接改数据库。
"""

from __future__ import annotations

import threading
import time

import pytest
from traceguard_api.runtime import Runtime
from traceguard_api.services.repair_service import (
    MAX_REGRESSION_ATTEMPTS,
    RepairService,
    regression_reason_code,
)
from traceguard_core.errors import AppError
from traceguard_persistence import Database


def _payload(testcase_id: str = "tcase_r17", oracle_id: str = "oracle_r17_a1") -> dict:
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

    base = work_tmp / f"r17-retry-{uuid.uuid4().hex[:8]}"
    base.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("TG_ARTIFACT_DIR", str(base / "artifacts"))
    from traceguard_core.config import reset_settings_cache

    reset_settings_cache()
    db = Database(f"sqlite:///{base / 'retry.db'}")
    runtime = Runtime(db)
    runtime.catalog.create_oracle(
        {
            "oracle_id": "oracle_r17_a1",
            "assertion_id": "a1",
            "expected_behavior": "标题必须存在",
            "requirement_ref": "REQ-R17-1",
            "version": 1,
        },
        approved=True,
        approved_by="role:qa-lead",
    )
    runtime.catalog.create_testcase(_payload(), title="retry", review_status="PENDING")
    runtime.catalog.review_testcase("tcase_r17", 1, "APPROVED", reviewer="role:qa-lead")
    yield runtime
    db.dispose()


def _promote(rt: Runtime) -> dict:
    svc = RepairService(rt)
    doc = svc.propose(
        testcase_id="tcase_r17",
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
    doc = svc.get(doc["proposal_id"])
    doc["sandbox_status"] = "PASSED"
    doc["sandbox_verdict"] = "PASSED"
    doc["sandbox_run_id"] = "run_sandbox_fixture"
    svc._save(doc)
    svc.submit_for_approval(doc["proposal_id"])
    svc.approve(doc["proposal_id"], "human:lead", "ok")
    return svc.promote(doc["proposal_id"], idempotency_key="promo-r17")


def _worker(
    rt: Runtime,
    *,
    verdict: str,
    execution_status: str = "COMPLETED",
    evidence: int = 2,
    worker_id: str = "worker-r17",
):
    def run() -> None:
        deadline = time.time() + 15
        claimed = None
        while time.time() < deadline:
            claimed = rt.tasks.claim_next_run(worker_id, ttl_seconds=60)
            if claimed:
                break
            time.sleep(0.02)
        if not claimed:
            return
        token = int(claimed["fencing_token"])
        rt.tasks.mark_running(claimed["run_id"], worker_id, token, "playwright")
        rt.tasks.submit_result(
            claimed["run_id"],
            worker_id,
            token,
            execution_status=execution_status,
            test_verdict=verdict,
            report={
                "run_id": claimed["run_id"],
                "evidence_ids": [f"evd_r17_{i}" for i in range(evidence)],
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


def _to_inconclusive(rt: Runtime, verdict: str = "PASSED", evidence: int = 2) -> dict:
    """把提案跑成 INCONCLUSIVE（EVIDENCE_MISSING 或指定路径）。"""
    svc = RepairService(rt)
    promoted = _promote(rt)
    worker = _worker(rt, verdict=verdict, evidence=evidence)
    doc = svc.regression_run(promoted["proposal_id"], timeout_s=20)
    worker.join(timeout=20)
    doc = svc.regression_status(promoted["proposal_id"])
    assert doc["regression_status"] == "INCONCLUSIVE", doc
    assert doc["regression_retry_allowed"] is True, doc
    return doc


def test_inconclusive_retry_creates_new_attempt_with_lineage(rt: Runtime) -> None:
    svc = RepairService(rt)
    doc = _to_inconclusive(rt, verdict="PASSED", evidence=0)
    pid = doc["proposal_id"]
    old_run = doc["regression_run_id"]
    assert doc["regression_reason_code"] if "regression_reason_code" in doc else True
    assert doc["regression_retry_reason_code"] == "EVIDENCE_MISSING"

    worker = _worker(rt, verdict="PASSED", evidence=3)
    retried = svc.regression_retry(
        pid,
        actor="human:qa-lead",
        confirm=True,
        reason_code="EVIDENCE_MISSING",
        note="采集失败，重跑一次",
        idempotency_key="retry-1",
    )
    worker.join(timeout=20)
    retried = svc.regression_status(pid)
    assert retried["regression_status"] == "PASSED", retried
    assert retried["regression_attempt_no"] == 2
    assert retried["regression_run_id"] != old_run
    assert retried["regression_parent_run_id"] == old_run
    attempts = retried["regression_attempts"]
    assert [a["attempt"] for a in attempts] == [1, 2]
    assert attempts[1]["parent_run_id"] == old_run
    assert len(retried["regression_retry_authorizations"]) == 1
    auth = retried["regression_retry_authorizations"][0]
    assert auth["actor"] == "human:qa-lead"
    assert auth["from_run_id"] == old_run
    assert auth["to_run_id"] == retried["regression_run_id"]
    assert retried["regression_retry_allowed"] is False


def test_missing_confirm_is_rejected(rt: Runtime) -> None:
    doc = _to_inconclusive(rt, verdict="PASSED", evidence=0)
    with pytest.raises(AppError) as exc:
        RepairService(rt).regression_retry(
            doc["proposal_id"],
            actor="human:qa-lead",
            confirm=False,
            reason_code="EVIDENCE_MISSING",
        )
    assert "REGRESSION_RETRY_REQUIRES_CONFIRM" in str(exc.value)


def test_agent_actor_is_rejected(rt: Runtime) -> None:
    doc = _to_inconclusive(rt, verdict="PASSED", evidence=0)
    with pytest.raises(AppError) as exc:
        RepairService(rt).regression_retry(
            doc["proposal_id"],
            actor="agent:auto-healer",
            confirm=True,
            reason_code="EVIDENCE_MISSING",
        )
    assert "HUMAN" in str(exc.value).upper()


def test_role_mapping_is_restricted_to_qa_lead_and_admin(rt: Runtime) -> None:
    """RBAC：/regression-retry 只能落到 repair.regression（qa-lead / platform-admin）。"""
    from traceguard_api.auth import action_for_request
    from traceguard_persistence.auth import PERMISSIONS, ROLE_PLATFORM_ADMIN, ROLE_QA_LEAD

    action = action_for_request("POST", "/api/v1/repairs/p1/regression-retry")
    assert action == "repair.regression"
    assert PERMISSIONS[action] == {ROLE_QA_LEAD, ROLE_PLATFORM_ADMIN}


def test_oracle_unavailable_is_not_retryable(rt: Runtime) -> None:
    doc = _to_inconclusive(rt, verdict="PASSED", evidence=0)
    svc = RepairService(rt)
    raw = svc.get(doc["proposal_id"])
    raw["regression_status"] = "INCONCLUSIVE"
    raw["regression_failure_reason"] = "REGRESSION_ORACLE_UNAVAILABLE"
    svc._save(raw)
    with pytest.raises(AppError) as exc:
        svc.regression_retry(
            doc["proposal_id"],
            actor="human:qa-lead",
            confirm=True,
            reason_code="REGRESSION_ORACLE_UNAVAILABLE",
        )
    assert "REASON_NOT_ALLOWED" in str(exc.value)


def test_failed_regression_cannot_be_retried(rt: Runtime) -> None:
    svc = RepairService(rt)
    promoted = _promote(rt)
    worker = _worker(rt, verdict="FAILED", evidence=2)
    svc.regression_run(promoted["proposal_id"], timeout_s=20)
    worker.join(timeout=20)
    status = svc.regression_status(promoted["proposal_id"])
    assert status["regression_status"] == "FAILED"
    assert status["regression_retry_allowed"] is False
    with pytest.raises(AppError) as exc:
        svc.regression_retry(
            promoted["proposal_id"],
            actor="human:qa-lead",
            confirm=True,
            reason_code="",
        )
    assert "REQUIRES_INCONCLUSIVE" in str(exc.value)


def test_stale_is_not_retryable(rt: Runtime) -> None:
    doc = _to_inconclusive(rt, verdict="PASSED", evidence=0)
    svc = RepairService(rt)
    raw = svc.get(doc["proposal_id"])
    raw["regression_stale"] = True
    svc._save(raw)
    with pytest.raises(AppError) as exc:
        svc.regression_retry(
            doc["proposal_id"], actor="human:qa-lead", confirm=True, reason_code=""
        )
    assert "STALE" in str(exc.value)


def test_attempt_limit_is_enforced(rt: Runtime) -> None:
    doc = _to_inconclusive(rt, verdict="PASSED", evidence=0)
    svc = RepairService(rt)
    raw = svc.get(doc["proposal_id"])
    raw["regression_status"] = "INCONCLUSIVE"
    raw["regression_attempt_no"] = MAX_REGRESSION_ATTEMPTS
    raw["regression_failure_reason"] = "REGRESSION_EVIDENCE_MISSING"
    svc._save(raw)
    status = svc.regression_status(doc["proposal_id"])
    assert status["regression_retry_allowed"] is False
    assert status["regression_retry_block_reason"] == "ATTEMPT_LIMIT_REACHED"
    with pytest.raises(AppError) as exc:
        svc.regression_retry(
            doc["proposal_id"], actor="human:qa-lead", confirm=True, reason_code=""
        )
    assert "LIMIT_REACHED" in str(exc.value)


def test_version_drift_blocks_retry(rt: Runtime, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    doc = _to_inconclusive(rt, verdict="PASSED", evidence=0)
    pid = doc["proposal_id"]
    # 别人晋升了更新的版本 → 旧回归结论对应的正式版本已漂移。
    # 这里直接模拟"正式版本变化"，不经过 v2 的稳定性回放门禁（本用例只验证版本门禁）。
    monkeypatch.setattr(rt.catalog, "approved_version_number", lambda _tc: 3)
    with pytest.raises(AppError) as exc:
        RepairService(rt).regression_retry(
            pid, actor="human:qa-lead", confirm=True, reason_code="EVIDENCE_MISSING"
        )
    assert "VERSION_DRIFT" in str(exc.value)


def test_concurrent_clicks_create_one_new_attempt(rt: Runtime) -> None:
    doc = _to_inconclusive(rt, verdict="PASSED", evidence=0)
    pid = doc["proposal_id"]
    old_run = doc["regression_run_id"]
    svc = RepairService(rt)
    barrier = threading.Barrier(6)
    errors: list[BaseException] = []
    results: list[dict] = []
    lock = threading.Lock()

    def click() -> None:
        try:
            barrier.wait(timeout=10)
            out = RepairService(rt).regression_retry(
                pid,
                actor="human:qa-lead",
                confirm=True,
                reason_code="EVIDENCE_MISSING",
                idempotency_key="retry-concurrent",
            )
            with lock:
                results.append(out)
        except BaseException as exc:  # noqa: BLE001 - 线程边界收集
            with lock:
                errors.append(exc)

    threads = [threading.Thread(target=click) for _ in range(5)]
    worker = _worker(rt, verdict="PASSED", evidence=2)
    for t in threads:
        t.start()
    barrier.wait(timeout=10)
    for t in threads:
        t.join(timeout=30)
    worker.join(timeout=20)

    assert errors == [], errors
    status = svc.regression_status(pid)
    assert status["regression_attempt_no"] == 2
    assert status["regression_run_id"] != old_run
    assert len(status["regression_attempts"]) == 2, status["regression_attempts"]
    assert len(status["regression_retry_authorizations"]) == 1, status[
        "regression_retry_authorizations"
    ]
    task_id = svc.get(pid)["regression_task_id"]
    runs = rt.tasks.list_runs(task_id=task_id, limit=100)
    assert len(runs) == 1, f"重试创建了 {len(runs)} 个 Run"


def test_repeat_request_is_idempotent(rt: Runtime) -> None:
    doc = _to_inconclusive(rt, verdict="PASSED", evidence=0)
    pid = doc["proposal_id"]
    svc = RepairService(rt)
    worker = _worker(rt, verdict="PASSED", evidence=2)
    first = svc.regression_retry(
        pid,
        actor="human:qa-lead",
        confirm=True,
        reason_code="EVIDENCE_MISSING",
        idempotency_key="retry-idem",
    )
    worker.join(timeout=20)
    first = svc.regression_status(pid)
    second = svc.regression_retry(
        pid,
        actor="human:qa-lead",
        confirm=True,
        reason_code="EVIDENCE_MISSING",
        idempotency_key="retry-idem",
    )
    assert second["regression_run_id"] == first["regression_run_id"]
    assert len(svc.regression_status(pid)["regression_attempts"]) == 2


def test_old_evidence_and_history_are_preserved(rt: Runtime) -> None:
    svc = RepairService(rt)
    doc = _to_inconclusive(rt, verdict="PASSED", evidence=0)
    pid = doc["proposal_id"]
    old_run = doc["regression_run_id"]
    old_history = list(doc["regression_history"])
    old_run_doc = rt.tasks.get_run(old_run)
    assert old_run_doc is not None
    old_report = dict(old_run_doc.get("report") or {})

    worker = _worker(rt, verdict="PASSED", evidence=2)
    svc.regression_retry(pid, actor="human:qa-lead", confirm=True, reason_code="EVIDENCE_MISSING")
    worker.join(timeout=20)
    after = svc.regression_status(pid)
    # 旧 Run 的报告/证据不变
    old_after = rt.tasks.get_run(old_run)
    assert dict(old_after.get("report") or {}) == old_report
    # 旧 history 项仍在，且不被改写
    for item in old_history:
        assert item in after["regression_history"]
    assert len(after["regression_history"]) >= len(old_history) + 1


def test_restart_recovers_attempt_history(rt: Runtime) -> None:
    svc = RepairService(rt)
    doc = _to_inconclusive(rt, verdict="PASSED", evidence=0)
    pid = doc["proposal_id"]
    worker = _worker(rt, verdict="PASSED", evidence=1)
    svc.regression_retry(pid, actor="human:qa-lead", confirm=True, reason_code="EVIDENCE_MISSING")
    worker.join(timeout=20)
    fresh = Runtime(rt.db)
    recovered = RepairService(fresh).regression_status(pid)
    assert recovered["regression_attempt_no"] == 2
    assert len(recovered["regression_attempts"]) == 2
    assert recovered["regression_retry_authorizations"][0]["actor"] == "human:qa-lead"


def test_reason_code_mapping_is_fail_closed() -> None:
    assert regression_reason_code("REGRESSION_EVIDENCE_MISSING") == "EVIDENCE_MISSING"
    assert regression_reason_code("REGRESSION_RUN_TIMED_OUT") == "EXECUTION_TIMEOUT"
    assert regression_reason_code("REGRESSION_RUN_FAILED") == "RUN_ABORTED"
    assert regression_reason_code("REGRESSION_ASSERTION_FAILED") == "FAILED"
    assert regression_reason_code("REGRESSION_ORACLE_UNAVAILABLE") == (
        "REGRESSION_ORACLE_UNAVAILABLE"
    )
    assert regression_reason_code("SOMETHING_NEW") == "UNKNOWN"
    assert regression_reason_code("") == "UNKNOWN"


def test_environment_drift_requires_healthy_target(rt: Runtime, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """ENVIRONMENT_DRIFT 只有环境自检恢复后才允许重试（不可达 → 拒绝）。"""
    doc = _to_inconclusive(rt, verdict="PASSED", evidence=0)
    pid = doc["proposal_id"]
    svc = RepairService(rt)
    raw = svc.get(pid)
    raw["regression_status"] = "INCONCLUSIVE"
    raw["regression_failure_reason"] = "REGRESSION_ENVIRONMENT_DRIFT"
    svc._save(raw)

    # 不可达的目标：拒绝
    rt.settings.teamflow_base_url = "http://127.0.0.1:1"
    with pytest.raises(AppError) as exc:
        svc.regression_retry(
            pid, actor="human:qa-lead", confirm=True, reason_code="ENVIRONMENT_DRIFT"
        )
    assert "ENVIRONMENT_NOT_RECOVERED" in str(exc.value)

    # 健康目标（用本地 HTTP 服务模拟）：允许进入创建流程
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args) -> None:  # noqa: D102
            return

        def do_GET(self) -> None:  # noqa: N802
            body = json.dumps({"ok": True}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    rt.settings.teamflow_base_url = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        worker = _worker(rt, verdict="PASSED", evidence=2)
        svc.regression_retry(
            pid, actor="human:qa-lead", confirm=True, reason_code="ENVIRONMENT_DRIFT"
        )
        worker.join(timeout=20)
        assert svc.regression_status(pid)["regression_attempt_no"] == 2
    finally:
        server.shutdown()
        server.server_close()
