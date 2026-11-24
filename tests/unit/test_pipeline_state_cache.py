"""RUN-007-RESUME：业务状态读取缓存的生命周期（真实缺陷回归）。

缺陷现象（实测）：常驻 Worker 复用同一 RunPipeline 实例，`_state_cache` 跨 Run 存活，
导致第二个 Run 的业务状态断言读到**上一个 Run 的 count**。后果：
`tcase_tf_false_pass`（UI 成功但持久化被回滚）在长驻 Worker 上被判 PASSED（假通过漏检）。

修复：缓存生存期严格等于一次 Run（`execute()` 开头清空）。
"""

from __future__ import annotations

import pytest
from traceguard_core.errors import AppError, ErrorCode
from traceguard_execution.runtime import pipeline as pipeline_module
from traceguard_execution.runtime.pipeline import RunPipeline
from traceguard_execution.tasks.service import TaskService
from traceguard_persistence import Database
from traceguard_persistence.catalog import CatalogService
from traceguard_persistence.stores import (
    ArtifactStore,
    PersistentEventStore,
    PersistentEvidenceManager,
)


def _tc() -> object:
    from traceguard_contracts.models import TestCaseVersion

    return TestCaseVersion(
        testcase_id="tcase_cache",
        version=1,
        steps=[{"step_id": "s1", "action": "navigate", "value": "/projects"}],
        created_at="2026-01-01T00:00:00Z",
        assertions=[
            {
                "assertion_id": "a_biz_count",
                "description": "项目数增加",
                "kind": "business_state",
                "required": True,
                "expected": {
                    "api": "/api/state/projects?workspace=default",
                    "field": "count",
                    "op": "gte",
                    "value": 1,
                },
            }
        ],
        run_config={"base_url": "http://127.0.0.1:9999"},
        review_status="APPROVED",
    )


@pytest.fixture()
def pipeline(work_tmp) -> RunPipeline:  # type: ignore[no-untyped-def]
    db = Database(f"sqlite:///{work_tmp / 'cache.db'}")
    db.create_schema()
    return RunPipeline(
        db,
        TaskService(db),
        CatalogService(db),
        PersistentEventStore(db),
        PersistentEvidenceManager(db),
        ArtifactStore(db, root=work_tmp / "artifacts"),
        worker_id="w-cache-test",
    )


def test_state_query_cached_within_one_run_only(pipeline: RunPipeline, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """同一 Run 内复用缓存；下一次 Run 必须重新读取（否则读到旧业务状态）。"""
    calls = {"n": 0}

    def fake_get(url: str, **kwargs: object) -> dict:
        calls["n"] += 1
        return {"count": calls["n"]}

    monkeypatch.setattr(pipeline_module, "_http_get_json", fake_get)
    tc = _tc()

    first = pipeline._query_states(tc, "http://127.0.0.1:9999")
    again = pipeline._query_states(tc, "http://127.0.0.1:9999")
    assert first["count"] == 1 and again["count"] == 1  # 同一 Run 内复用
    assert calls["n"] == 1

    pipeline._state_cache.clear()  # execute() 开头的等价动作
    third = pipeline._query_states(tc, "http://127.0.0.1:9999")
    assert third["count"] == 2  # 新 Run 重新读取真实状态
    assert calls["n"] == 2


def test_execute_clears_state_cache(pipeline: RunPipeline) -> None:
    """即使 Run 不存在，execute() 也必须先清空缓存（缓存不跨 Run 泄漏）。"""
    pipeline._state_cache["/api/state/projects?workspace=default"] = {"count": 42}
    with pytest.raises(AppError) as exc:
        pipeline.execute("run_does_not_exist")
    assert exc.value.code is ErrorCode.NOT_FOUND
    assert pipeline._state_cache == {}
