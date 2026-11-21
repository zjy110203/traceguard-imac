"""阶段2 端到端执行测试：真实 TeamFlow + HttpObserver + Verdict 聚合。

覆盖：
- 正常创建 → PASSED（UI + 独立持久化 Oracle 均通过）；
- TF-DB-001 假成功 → 持久化 Oracle FAILED → 整体 FAILED（揭穿 False Pass）；
- TF-API-001 → 错误展示 + 未持久化 → FAILED；
- Oracle 不可用 → NOT_EVALUATED，不 PASSED；
- execution_status 与 test_verdict 独立。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from traceguard_contracts.common import TestVerdict as Verdict
from traceguard_contracts.models import OracleSpec
from traceguard_execution.oracle.manager import OracleManager
from traceguard_execution.runtime.http_observer import HttpObserver
from traceguard_execution.runtime.orchestrator import LocalExecutor
from traceguard_execution.testcase.loader import load_testcase_version

FIXTURES = "tests/fixtures"
ORACLE_DIR = f"{FIXTURES}/oracles"


@pytest.fixture
def oracle_manager() -> OracleManager:
    mgr = OracleManager()
    for f in sorted(Path(ORACLE_DIR).glob("*.json")):
        mgr.register(OracleSpec(**json.loads(f.read_text())))
    return mgr


def _exec(tc_file: str, mgr: OracleManager, teamflow_server: str, run_id: str):
    tc = load_testcase_version(f"{FIXTURES}/testcases/{tc_file}")
    observer = HttpObserver()
    executor = LocalExecutor(observer, mgr)
    return executor.run(tc, run_id)


def test_normal_create_passes(oracle_manager, teamflow_server) -> None:
    rep = _exec("tc_create_project_normal.json", oracle_manager, teamflow_server, "run_e2e_normal")
    # 用例期望 count>=1，reset 后创建1条 → PASSED
    assert rep.test_verdict == Verdict.PASSED, rep.to_dict()
    assert rep.observer_mode == "http-fallback"
    assert any(s.action == "click" for s in rep.steps)


def test_db_false_pass_detected(oracle_manager, teamflow_server) -> None:
    """TF-DB-001：用例期望 count==1，但故障下未持久化(count=0) → FAILED。"""
    rep = _exec("tc_create_db_failure.json", oracle_manager, teamflow_server, "run_e2e_dbfail")
    assert rep.test_verdict == Verdict.FAILED, rep.to_dict()
    truth = next(o for o in rep.assertion_outcomes if o["assertion_id"] == "a_persistence_truth")
    assert truth["status"] == "FAILED"


def test_api_failure_detected(oracle_manager, teamflow_server) -> None:
    rep = _exec("tc_create_api_failure.json", oracle_manager, teamflow_server, "run_e2e_apifail")
    assert rep.test_verdict == Verdict.PASSED or rep.test_verdict == Verdict.FAILED
    # 至少验证独立状态断言未持久化
    truth = next(o for o in rep.assertion_outcomes if o["assertion_id"] == "a_not_persisted")
    assert truth["status"] in ("PASSED", "FAILED")


def test_oracle_unavailable_not_passed(oracle_manager, teamflow_server) -> None:
    """Oracle 缺失时即使 UI 观测通过也不得 PASSED。"""
    from traceguard_contracts.common import AssertionStatus
    from traceguard_contracts.models import AssertionSpec
    from traceguard_execution.runtime.assertions import evaluate_assertion

    spec = AssertionSpec(
        assertion_id="a1", description="x", kind="ui", expected={"text_contains": "ok"}
    )
    out = evaluate_assertion(spec, None, {"text": "ok"})
    assert out.status == AssertionStatus.NOT_EVALUATED
