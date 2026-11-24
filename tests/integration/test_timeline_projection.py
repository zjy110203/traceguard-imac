"""RUN-005 WP1.4 真实接入验证：Consumer Inbox 驱动的 Run 时间线投影。

验证点：
- 事件被真实消费并投影为时间线（不是未被使用的类）；
- 重复投影幂等（同一 event 不会重复写入条目）；
- Inbox 观测接口能反映消费状态；
- 投影文档是派生视图（authority=derived_view），不是业务状态权威。
"""

from __future__ import annotations

import uuid

import pytest
from traceguard_persistence import Database, set_database
from traceguard_persistence.models import RunRow


@pytest.fixture(scope="function")
def client(work_tmp, monkeypatch):  # type: ignore[no-untyped-def]
    from fastapi.testclient import TestClient
    from traceguard_api.main import create_app
    from traceguard_api.runtime import get_runtime, reset_runtime
    from traceguard_api.services import reset_services

    base = work_tmp / f"timeline-{uuid.uuid4().hex[:8]}"
    base.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("TG_ARTIFACT_DIR", str(base / "artifacts"))
    from traceguard_core.config import reset_settings_cache

    reset_settings_cache()
    db = Database(f"sqlite:///{base / 'tl.db'}")
    set_database(db)
    reset_runtime()
    reset_services()
    app = create_app()
    with TestClient(app) as c:
        rt = get_runtime()
        with rt.db.session() as s:
            for run_id in ("run_tl_1", "run_tl_2"):
                s.add(
                    RunRow(
                        run_id=run_id,
                        task_id="task_tl",
                        workspace_id="default",
                        testcase_version_id="tcase_tl@1",
                        execution_status="COMPLETED",
                        test_verdict="FAILED",
                        report_json={"steps": [], "assertion_outcomes": []},
                    )
                )
        iid = rt.events.new_producer_instance("proj-worker")
        for idx in range(3):
            rt.events.append(
                event_type="step.completed" if idx < 2 else "run.completed",
                server_workspace_id="default",
                producer_id="proj-worker",
                producer_instance_id=iid,
                idempotency_key=f"run_tl_1:e{idx}",
                payload={"run_id": "run_tl_1", "index": idx},
                context_refs={"run": "run_tl_1"},
            )
        rt.events.append(
            event_type="run.started",
            server_workspace_id="default",
            producer_id="proj-worker",
            producer_instance_id=iid,
            idempotency_key="run_tl_2:e0",
            payload={"run_id": "run_tl_2"},
            context_refs={"run": "run_tl_2"},
        )
        yield c
    reset_runtime()
    reset_services()


def test_timeline_projection_is_real_and_idempotent(client) -> None:
    r1 = client.get("/api/v1/runs/run_tl_1/timeline")
    assert r1.status_code == 200, r1.text
    doc = r1.json()
    assert doc["run_id"] == "run_tl_1"
    assert doc["authority"] == "derived_view"
    assert len(doc["entries"]) == 3
    assert doc["counts"]["step.completed"] == 2
    assert [e["sequence"] for e in doc["entries"]] == sorted(e["sequence"] for e in doc["entries"])

    # 重复投影：幂等，不重复写条目
    r2 = client.get("/api/v1/runs/run_tl_1/timeline")
    assert r2.status_code == 200
    doc2 = r2.json()
    assert len(doc2["entries"]) == 3
    assert doc2["skipped_events"] == 3 and doc2["projected_events"] == 0


def test_timeline_isolated_per_run(client) -> None:
    doc = client.get("/api/v1/runs/run_tl_2/timeline").json()
    assert len(doc["entries"]) == 1
    assert doc["entries"][0]["payload"]["run_id"] == "run_tl_2"


def test_inbox_stats_reflect_projection(client) -> None:
    client.get("/api/v1/runs/run_tl_1/timeline")
    stats = client.get("/api/v1/inbox/stats").json()
    assert stats["projector"]["consumer_id"] == "run-timeline-projector"
    assert stats["all"]["SUCCEEDED"] >= 3
    assert stats["all"].get("FAILED", 0) == 0
