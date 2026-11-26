"""RUN-015 WP1：submit_result 原子 fencing 与 Pipeline Run 事件 fencing。

这些测试覆盖 RUN-014 专项未覆盖的两个真实缺口：

1. `submit_result` 过去只做 Python 层 SELECT + 比对，旧 Worker 恢复后会把
   旧 ORM 对象写回，覆盖接管者已经写入的终态；
2. `RunPipeline._emit()` 的 `step.completed` / `assertion.evaluated` 调用点
   过去没有传 fence，因此 PersistentEventStore 的守卫根本没有执行。

"暂停 A"通过 monkeypatch 在守卫/事件写入前同步完成 B 的接管，确定性复现
SIGSTOP → 租约到期 → B 接管 → A 恢复的顺序，不依赖 sleep 抢窗口。
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import update
from traceguard_api.runtime import Runtime
from traceguard_core.errors import AppError
from traceguard_execution.runtime.observer import ObservationRecord, Observer
from traceguard_execution.runtime.pipeline import RunPipeline
from traceguard_execution.tasks import service as task_service
from traceguard_persistence.models import RunRow, RunSubmissionRow


@pytest.fixture()
def rt(work_tmp, monkeypatch):  # type: ignore[no-untyped-def]
    import uuid

    base = work_tmp / f"fence15-{uuid.uuid4().hex[:8]}"
    base.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("TG_ARTIFACT_DIR", str(base / "artifacts"))
    from traceguard_core.config import reset_settings_cache

    reset_settings_cache()
    from traceguard_persistence import Database

    db = Database(f"sqlite:///{base / 'fence.db'}")
    runtime = Runtime(db)
    runtime.catalog.create_testcase(
        {
            "testcase_id": "tcase_fence15",
            "version": 1,
            "workspace_id": "default",
            "created_at": "2026-01-01T00:00:00Z",
            "steps": [{"step_id": "s1", "action": "navigate", "value": "/projects"}],
            "assertions": [
                {
                    "assertion_id": "a1",
                    "description": "标题存在",
                    "kind": "ui",
                    "required": True,
                    "expected": {"test_id": "page-title"},
                }
            ],
            "compile_source": "human",
        },
        title="fence15",
        review_status="APPROVED",
    )
    yield runtime
    db.dispose()


def _queued_run(rt: Runtime, key: str) -> str:
    task = rt.tasks.create_task(
        "tcase_fence15",
        f"fence15-{key}",
        testcase_version_id="tcase_fence15@1",
        workspace_id="default",
    )
    return rt.tasks.create_run(task["task_id"], "tcase_fence15@1")["run_id"]


def _take_over(rt: Runtime, run_id: str, worker_b: str = "worker-B") -> dict[str, Any]:
    """模拟 B 在 A 暂停期间完成接管 + 终态提交（真实 TaskService 调用）。"""
    with rt.db.session() as s:
        s.execute(update(RunRow).where(RunRow.run_id == run_id).values(lease_expires_at=0.0))
    lease_b = rt.tasks.acquire_lease(run_id, worker_b, ttl_seconds=30)
    token_b = int(lease_b["fencing_token"])
    rt.tasks.mark_running(run_id, worker_b, token_b, "playwright")
    rt.tasks.submit_result(
        run_id,
        worker_b,
        token_b,
        execution_status="COMPLETED",
        test_verdict="PASSED",
        report={"owner": worker_b, "evidence_ids": ["evd_b"]},
        idempotency_key=f"{run_id}:b-final",
    )
    return lease_b


class _StubObserver(Observer):
    """只替换"怎么观测"，Pipeline 的 Run/Step/Evidence/Event 全部走真实路径。"""

    mode = "stub"

    def setup(self, run_config: dict[str, Any]) -> None:
        return None

    def observe(self, step, context):  # type: ignore[no-untyped-def]
        return ObservationRecord(step_id=step.step_id, action=step.action, observation={})

    def teardown(self) -> None:
        return None


def test_submit_result_atomic_guard_prevents_old_worker_override(
    rt: Runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 的旧 ORM 对象不得覆盖 B 已提交的 Verdict / Report / Receipt。"""
    run_id = _queued_run(rt, "submit-race")
    lease_a = rt.tasks.acquire_lease(run_id, "worker-A", ttl_seconds=30)
    token_a = int(lease_a["fencing_token"])
    rt.tasks.mark_running(run_id, "worker-A", token_a, "playwright")

    original_guard = task_service.fenced_guard
    takeover_done = False

    def guard_with_takeover(session, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal takeover_done
        if kwargs.get("worker_id") == "worker-A" and not takeover_done:
            takeover_done = True  # A 已读取旧租约，此刻"暂停"
            _take_over(rt, run_id)
        return original_guard(session, **kwargs)

    monkeypatch.setattr(task_service, "fenced_guard", guard_with_takeover)

    with pytest.raises(AppError) as exc:
        rt.tasks.submit_result(
            run_id,
            "worker-A",
            token_a,
            execution_status="FAILED",
            test_verdict="FAILED",
            report={"owner": "A", "tampered": True},
            idempotency_key=f"{run_id}:a-late",
        )
    assert takeover_done is True
    assert "STALE_FENCING_TOKEN" in str(exc.value.message)

    run = rt.tasks.get_run(run_id)
    assert run["execution_status"] == "COMPLETED"
    assert run["test_verdict"] == "PASSED"
    assert run["report"] == {"owner": "worker-B", "evidence_ids": ["evd_b"]}
    assert run["fencing_token"] == token_a + 1
    with rt.db.session() as s:
        keys = {
            row.idempotency_key: row.test_verdict
            for row in s.query(RunSubmissionRow).filter(RunSubmissionRow.run_id == run_id).all()
        }
    assert keys == {f"{run_id}:b-final": "PASSED"}, "旧 Worker 的回执不得落库"


@pytest.mark.parametrize("target_event", ["step.completed", "assertion.evaluated"])
def test_pipeline_run_events_are_fenced(
    rt: Runtime, monkeypatch: pytest.MonkeyPatch, target_event: str
) -> None:
    """真实 Pipeline 调用路径：Step/Evidence 写完后被接管，A 不得再追加 Run 事件。"""
    run_id = _queued_run(rt, f"pipeline-{target_event}")
    lease_a = rt.tasks.acquire_lease(run_id, "worker-A", ttl_seconds=30)
    token_a = int(lease_a["fencing_token"])
    pipeline: RunPipeline = rt.pipeline("worker-A")

    def fake_build_observer(task, run, workspace_id, **kwargs):  # type: ignore[no-untyped-def]
        return _StubObserver(), "stub"

    monkeypatch.setattr(pipeline, "_build_observer", fake_build_observer)
    monkeypatch.setattr(pipeline, "_query_states", lambda tc, base_url: {})

    original_append = rt.events.append
    takeover_done = False

    def append_with_takeover(**kwargs):  # type: ignore[no-untyped-def]
        nonlocal takeover_done
        if kwargs.get("event_type") == target_event and not takeover_done:
            takeover_done = True  # A 已完成 Step/Evidence，在 Event 写入前"暂停"
            _take_over(rt, run_id)
        return original_append(**kwargs)

    monkeypatch.setattr(rt.events, "append", append_with_takeover)

    outcome = pipeline.execute(run_id, lease=lease_a)
    assert takeover_done is True
    assert outcome.execution_status == "LEASE_LOST", "旧 Worker 的事件写入必须被拒绝"

    events = [
        e
        for e in rt.events.list_events("default", limit=1000)
        if (e.context_refs or {}).get("run") == run_id
    ]
    stale = [e for e in events if e.event_type == target_event and e.producer_id == "worker-A"]
    assert stale == [], f"接管后不得追加 {target_event}"

    run = rt.tasks.get_run(run_id)
    assert run["test_verdict"] == "PASSED"
    assert run["report"]["owner"] == "worker-B"
    assert run["fencing_token"] == token_a + 1
