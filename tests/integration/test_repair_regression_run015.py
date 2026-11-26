"""RUN-015 WP2：Regression 并发唯一创建、结论时效与重试策略边界。

不使用 Python 全局锁：10 个并发请求通过数据库级认领行（tg_regression_claim）
收敛到同一个 Run；API 重启用同一 DB 新建 Runtime 验证可恢复。
"""

from __future__ import annotations

import threading
import time

import pytest
from sqlalchemy import select
from traceguard_api.runtime import Runtime
from traceguard_api.services.repair_service import RepairService
from traceguard_persistence import Database
from traceguard_persistence.models import RegressionClaimRow


@pytest.fixture()
def env(work_tmp, monkeypatch):  # type: ignore[no-untyped-def]
    import uuid

    base = work_tmp / f"reg15-{uuid.uuid4().hex[:8]}"
    base.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("TG_ARTIFACT_DIR", str(base / "artifacts"))
    from traceguard_core.config import reset_settings_cache

    reset_settings_cache()
    db = Database(f"sqlite:///{base / 'reg.db'}")
    rt = Runtime(db)
    rt.catalog.create_oracle(
        {
            "oracle_id": "oracle_o15_a1",
            "assertion_id": "a1",
            "expected_behavior": "标题必须存在",
            "requirement_ref": "REQ-15",
            "version": 1,
        },
        approved=True,
        approved_by="role:qa-lead",
    )
    rt.catalog.create_testcase(_payload("tcase_reg15"), title="reg15", review_status="PENDING")
    rt.catalog.review_testcase("tcase_reg15", 1, "APPROVED", reviewer="role:qa-lead")
    yield rt, db
    db.dispose()


def _payload(testcase_id: str) -> dict:
    return {
        "testcase_id": testcase_id,
        "version": 1,
        "steps": [
            {"step_id": "s1", "action": "navigate", "value": "/projects"},
            {
                "step_id": "s2",
                "action": "click",
                "target": {"strategy": "test_id", "value": "nav-a"},
            },
        ],
        "assertions": [
            {
                "assertion_id": "a1",
                "description": "标题存在",
                "kind": "ui",
                "required": True,
                "expected": {"test_id": "page-title"},
                "oracle_id": "oracle_o15_a1",
            }
        ],
        "run_config": {"base_url": "http://127.0.0.1:8011"},
        "compile_source": "human",
        "created_at": "2026-01-01T00:00:00Z",
    }


def _ready(rt: Runtime, *, base_version: int = 1, key: str = "p") -> dict:
    svc = RepairService(rt)
    doc = svc.propose(
        testcase_id="tcase_reg15",
        base_version=base_version,
        changes=[
            {
                "path": "steps[].target",
                "step_index": 1,
                "value": {"strategy": "test_id", "value": f"nav-{key}"},
                "reason": "locator fix",
            }
        ],
        reason=key,
        created_by="human:dev",
        actor="human:dev",
        repair_source="human:dev",
    )
    svc.validate(doc["proposal_id"])
    doc = svc.get(doc["proposal_id"])
    doc["sandbox_status"] = "PASSED"
    doc["sandbox_verdict"] = "PASSED"
    doc["sandbox_run_id"] = f"run_sandbox_{key}"
    svc._save(doc)
    svc.submit_for_approval(doc["proposal_id"])
    svc.approve(doc["proposal_id"], "human:lead", "ok")
    return svc.promote(doc["proposal_id"], idempotency_key=f"promo-{key}")


def _worker(rt: Runtime, verdict: str = "PASSED", *, evidence: int = 2) -> threading.Thread:
    def run() -> None:
        deadline = time.time() + 15
        claimed = None
        while time.time() < deadline:
            claimed = rt.tasks.claim_next_run("worker-15", ttl_seconds=60)
            if claimed:
                break
            time.sleep(0.02)
        if not claimed:
            return
        token = int(claimed["fencing_token"])
        rt.tasks.mark_running(claimed["run_id"], "worker-15", token, "playwright")
        rt.tasks.submit_result(
            claimed["run_id"],
            "worker-15",
            token,
            execution_status="COMPLETED",
            test_verdict=verdict,
            report={
                "run_id": claimed["run_id"],
                "evidence_ids": [f"evd15_{i}" for i in range(evidence)],
                "errors": [] if verdict == "PASSED" else ["assertion failed"],
            },
        )

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread


def test_ten_concurrent_regression_requests_create_one_run(env) -> None:  # type: ignore[no-untyped-def]
    rt, db = env
    proposal = _ready(rt)
    pid = proposal["proposal_id"]
    svc = RepairService(rt)
    barrier = threading.Barrier(11)
    results: list[dict] = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def request(i: int) -> None:
        try:
            barrier.wait(timeout=10)
            result = RepairService(rt).regression_run(pid, timeout_s=5)
            with lock:
                results.append(result)
        except BaseException as exc:  # noqa: BLE001 - 线程边界收集
            with lock:
                errors.append(exc)

    threads = [threading.Thread(target=request, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    worker = _worker(rt)
    barrier.wait(timeout=10)
    for t in threads:
        t.join(timeout=20)
    worker.join(timeout=20)

    assert errors == [], f"并发回归请求不应抛异常: {errors}"
    assert len(results) == 10
    run_ids = {r.get("regression_run_id") for r in results}
    assert len(run_ids) == 1 and None not in run_ids, run_ids
    only_run = next(iter(run_ids))
    assert {r["regression_status"] for r in results} == {"PASSED"}
    assert {r["regression_verdict"] for r in results} == {"PASSED"}

    task_id = svc.get(pid)["regression_task_id"]
    runs = rt.tasks.list_runs(task_id=task_id, limit=100)
    assert len(runs) == 1, f"并发创建出 {len(runs)} 个 Run"
    assert runs[0]["run_id"] == only_run
    with db.session() as s:
        claims = s.scalars(select(RegressionClaimRow)).all()
    assert len(claims) == 1 and claims[0].run_id == only_run

    status = svc.regression_status(pid)
    assert len(status["regression_history"]) == 1, "并发请求不得写入多个结论"

    # API 重启：同一 DB 新建 Runtime/Service，结果仍指向同一 Run
    restarted = Runtime(db)
    recovered = RepairService(restarted).regression_status(pid)
    assert recovered["regression_run_id"] == only_run
    assert recovered["regression_status"] == "PASSED"
    again = svc.regression_run(pid, timeout_s=2)
    assert again["regression_run_id"] == only_run
    assert again["regression_status"] == "PASSED"


def test_repeated_request_returns_same_result(env) -> None:  # type: ignore[no-untyped-def]
    rt, _db = env
    proposal = _ready(rt, key="repeat")
    pid = proposal["proposal_id"]
    svc = RepairService(rt)
    _worker(rt)
    first = svc.regression_run(pid, timeout_s=5)
    second = svc.regression_run(pid, timeout_s=5)
    assert first["regression_run_id"] == second["regression_run_id"]
    assert first["regression_status"] == second["regression_status"] == "PASSED"
    task_id = first["regression_task_id"]
    assert len(rt.tasks.list_runs(task_id=task_id, limit=100)) == 1


def test_regression_failure_and_inconclusive_are_distinct(env) -> None:  # type: ignore[no-untyped-def]
    rt, _db = env
    failed = _ready(rt, key="failed")
    svc = RepairService(rt)
    _worker(rt, verdict="FAILED", evidence=1)
    settled = svc.regression_run(failed["proposal_id"], timeout_s=5)
    assert settled["regression_status"] == "FAILED"
    assert settled["regression_verdict"] == "FAILED"

    # 证据缺失 → INCONCLUSIVE；重试策略未获产品决策，不擅自改状态机
    no_evidence = _ready(rt, base_version=2, key="noevd")
    _worker(rt, verdict="PASSED", evidence=0)
    inconclusive = svc.regression_run(no_evidence["proposal_id"], timeout_s=5)
    assert inconclusive["regression_status"] == "INCONCLUSIVE"
    assert inconclusive["regression_failure_reason"] == "REGRESSION_EVIDENCE_MISSING"
    status = svc.regression_status(no_evidence["proposal_id"])
    assert status["regression_retry_allowed"] is False
    assert status["regression_retry_decision"] == "NEEDS_DECISION"


def test_stale_recomputed_when_another_user_promotes_new_version(env) -> None:  # type: ignore[no-untyped-def]
    rt, _db = env
    v2 = _ready(rt, key="v2")
    pid = v2["proposal_id"]
    svc = RepairService(rt)
    _worker(rt)
    result = svc.regression_run(pid, timeout_s=5)
    assert result["regression_status"] == "PASSED"
    before = svc.regression_status(pid)
    assert before["regression_stale"] is False

    # 另一用户从 v2 晋升出 v3；v2 的回归结论不得被改写，但读取时必须显示过期
    v3 = _ready(rt, base_version=2, key="v3")
    assert v3["promoted_version"] == 3
    after = svc.regression_status(pid)
    assert after["regression_stale"] is True
    assert "APPROVED_VERSION_MOVED:2->3" == after["regression_stale_reason"]
    assert after["regression_verdict"] == "PASSED", "历史 Verdict 不得被改写"
    assert rt.tasks.get_run(before["regression_run_id"])["test_verdict"] == "PASSED"


def test_rollback_marks_stale_and_keeps_history(env) -> None:  # type: ignore[no-untyped-def]
    rt, _db = env
    proposal = _ready(rt, key="rollback")
    pid = proposal["proposal_id"]
    svc = RepairService(rt)
    _worker(rt)
    svc.regression_run(pid, timeout_s=5)
    rolled = svc.rollback(pid, "human:lead", "manual rollback")
    assert rolled["status"] == "ROLLED_BACK"
    status = svc.regression_status(pid)
    assert status["regression_stale"] is True
    assert status["regression_stale_reason"] == "ROLLED_BACK_OR_MARKED_STALE"
    assert len(status["regression_history"]) == 1
