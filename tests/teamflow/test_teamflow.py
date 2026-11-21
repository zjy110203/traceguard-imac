"""阶段1 TeamFlow 测试：正常流程 / 独立状态 / 五类故障 / Ground Truth 隔离 / 重置。

使用直接 async 路由调用，规避本环境 TestClient 依赖（httpx2 未装完）。
真实 HTTP + Playwright 端到端由阶段2 覆盖。
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException
from traceguard_teamflow.api import (
    LoginReq,
    ProjectCreateReq,
    activate_fault,
    clear_fault,
    create_project,
    get_ground_truth,
    list_projects,
    login,
    reset_test_data,
    state_projects,
)
from traceguard_teamflow.faults import FAULT_STATE
from traceguard_teamflow.ground_truth import GROUND_TRUTH, GroundTruthRecord
from traceguard_teamflow.models import init_db
from traceguard_teamflow.web import login_page, new_project_page, projects_page


@pytest.fixture(autouse=True)
def _clean(monkeypatch):  # type: ignore[no-untyped-def]
    init_db()
    asyncio.run(reset_test_data())
    yield
    asyncio.run(reset_test_data())


def test_login_success_and_failure() -> None:
    ok = asyncio.run(login(LoginReq(username="tester", password="tester123")))
    assert ok["ok"] is True
    with pytest.raises(HTTPException) as e:
        asyncio.run(login(LoginReq(username="tester", password="wrong")))
    assert e.value.status_code == 401


def test_normal_create_and_independent_state() -> None:
    r = asyncio.run(create_project(ProjectCreateReq(name="Alpha", description="d")))
    assert r["ok"] is True and r["persisted"] is True
    key = r["project"]["key"]
    # 独立权威状态查询确认持久化（不依赖 UI）
    state = asyncio.run(state_projects())
    assert state["count"] == 1 and key in state["keys"]
    listed = asyncio.run(list_projects())
    assert any(p["key"] == key for p in listed["items"])


def test_fault_api_001_returns_500() -> None:
    asyncio.run(activate_fault("TF-API-001"))
    with pytest.raises(HTTPException) as e:
        asyncio.run(create_project(ProjectCreateReq(name="Boom")))
    assert e.value.status_code == 500
    state = asyncio.run(state_projects())
    assert state["count"] == 0  # 未持久化


def test_fault_db_001_ui_success_but_not_persisted() -> None:
    """关键场景：UI 显示成功但持久化失败（False Pass 来源）。"""
    asyncio.run(activate_fault("TF-DB-001"))
    r = asyncio.run(create_project(ProjectCreateReq(name="Ghost")))
    assert r["ok"] is True and r["persisted"] is False
    state = asyncio.run(state_projects())
    assert state["count"] == 0  # 独立状态查询揭穿假成功


def test_fault_ui_001_flag_visible_in_page() -> None:
    asyncio.run(activate_fault("TF-UI-001"))
    html = asyncio.run(projects_page())
    assert "uiFault = true" in html or "uiFault = 'true'" in html or "const uiFault = true" in html


def test_fault_time_001_and_clear() -> None:
    asyncio.run(activate_fault("TF-TIME-001", {"delay_ms": 1}))
    assert FAULT_STATE.is_active("TF-TIME-001")
    asyncio.run(clear_fault("TF-TIME-001"))
    assert not FAULT_STATE.is_active("TF-TIME-001")


def test_reset_clears_data_and_faults() -> None:
    asyncio.run(create_project(ProjectCreateReq(name="ToBeCleared")))
    asyncio.run(activate_fault("TF-API-001"))
    r = asyncio.run(reset_test_data())
    assert r["faults_cleared"] is True
    assert asyncio.run(state_projects())["count"] == 0
    assert not FAULT_STATE.is_active("TF-API-001")


def test_ground_truth_isolated_from_agent() -> None:
    """Agent（无令牌）读取 Ground Truth 被拒绝；评测侧令牌可读。"""
    GROUND_TRUTH.put(
        GroundTruthRecord(
            scenario_id="TEST-GT-1",
            fault_mechanism_id="TF-DB-001",
            expected_business_state={"persisted": False},
            expected_failure_class="PERSISTENCE",
        )
    )
    with pytest.raises(HTTPException) as e:
        asyncio.run(get_ground_truth("TEST-GT-1", x_eval_token=None))
    assert e.value.status_code == 403
    ok = asyncio.run(get_ground_truth("TEST-GT-1", x_eval_token=GROUND_TRUTH.evaluation_token))
    assert ok["expected_failure_class"] == "PERSISTENCE"
    # 场景列表仅暴露引用，不含答案
    from traceguard_teamflow.api import list_scenarios

    scenarios = asyncio.run(list_scenarios())
    assert "TEST-GT-1" in scenarios["scenario_refs"]
    assert "PERSISTENCE" not in str(scenarios)


def test_pages_render_with_testids() -> None:
    assert 'data-testid="login-form"' in asyncio.run(login_page())
    assert 'data-testid="create-form"' in asyncio.run(new_project_page())
    assert 'data-testid="project-rows"' in asyncio.run(projects_page())
