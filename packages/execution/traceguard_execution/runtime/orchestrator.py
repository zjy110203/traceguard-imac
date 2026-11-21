"""本地同步执行编排器（阶段2，不依赖 Celery）。

流程：加载 TestCaseVersion + Oracle → Observer 逐步执行 → Assertion Evaluator → Verdict 聚合 → Run 报告。
关键：Trusted Oracle 的业务状态断言通过"独立状态查询"获取，不由页面文本推断。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from traceguard_contracts.common import ExecutionStatus, TestVerdict
from traceguard_contracts.models import TestCaseVersion

from ..oracle.manager import OracleManager
from ..verdict import VerdictInput, aggregate
from .assertions import evaluate_assertion
from .observer import ObservationRecord, Observer


@dataclass
class RunReport:
    run_id: str
    testcase_id: str
    version: int
    observer_mode: str
    execution_status: ExecutionStatus
    test_verdict: TestVerdict
    steps: list[ObservationRecord] = field(default_factory=list)
    assertion_outcomes: list[dict[str, Any]] = field(default_factory=list)
    stop_reason: str = ""
    started_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "testcase_id": self.testcase_id,
            "version": self.version,
            "observer_mode": self.observer_mode,
            "execution_status": self.execution_status.value,
            "test_verdict": self.test_verdict.value,
            "steps": [
                {
                    "step_id": s.step_id,
                    "action": s.action,
                    "observation": s.observation,
                    "error": s.error,
                }
                for s in self.steps
            ],
            "assertion_outcomes": self.assertion_outcomes,
            "stop_reason": self.stop_reason,
            "started_at": self.started_at,
        }


def _query_state(base_url: str, api_path: str) -> dict[str, Any]:
    """Trusted Oracle：独立业务状态查询。"""
    url = base_url + api_path
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except Exception:  # noqa: BLE001
        return {}


class LocalExecutor:
    def __init__(self, observer: Observer, oracle_manager: OracleManager) -> None:
        self.observer = observer
        self.oracles = oracle_manager

    def run(self, tc: TestCaseVersion, run_id: str) -> RunReport:
        from traceguard_core.ids import utc_now_iso

        cfg = tc.run_config
        self.observer.setup(cfg)
        base_url = cfg.get("base_url", "http://127.0.0.1:8011")

        # 执行前清理（CleanupPolicy）
        if tc.cleanup_policy == "RESET_DATA":
            self._post(base_url, "/api/test/reset", {})

        # 预置 api 步骤激活故障
        for step in tc.steps:
            if step.action == "api":
                self._run_api_step(base_url, step)

        records: list[ObservationRecord] = []
        interrupted = False
        for step in tc.steps:
            if step.action == "api":
                # 已在预置阶段执行，这里做一次轻量观测
                records.append(
                    ObservationRecord(
                        step_id=step.step_id, action="api", observation={"preset": True}
                    )
                )
                continue
            rec = self.observer.observe(step, {})
            records.append(rec)
            if rec.error:
                interrupted = True

        # Trusted Oracle 关键时序：在所有操作步骤执行"之后"才查询独立业务状态。
        # 若在步骤前查询，会拿到 reset 后的旧状态，导致假失败（真实 bug，已修复）。
        state_ctx: dict[str, Any] = {}
        state = _query_state(base_url, "/api/state/projects?workspace=default")
        if isinstance(state.get("count"), int):
            state_ctx["state_count"] = state["count"]

        # 断言评估：每条 AssertionSpec 关联 Oracle 与观测
        outcomes = []
        for spec in tc.assertions:
            oracle = self.oracles.get(spec.oracle_id) if spec.oracle_id else None
            # 选择观测：优先按 test_id 文本、其次业务状态 count、再合并全部
            observation = self._observation_for(spec, records, state_ctx)
            val = evaluate_assertion(spec, oracle, observation, execution_interrupted=interrupted)
            outcomes.append(val)

        verdict = aggregate(
            VerdictInput(
                assertion_outcomes=outcomes,
                execution_status=ExecutionStatus.COMPLETED
                if not interrupted
                else ExecutionStatus.FAILED,
                execution_completed=not interrupted,
            )
        )
        report = RunReport(
            run_id=run_id,
            testcase_id=tc.testcase_id,
            version=tc.version,
            observer_mode=self.observer.mode,
            execution_status=ExecutionStatus.COMPLETED
            if not interrupted
            else ExecutionStatus.FAILED,
            test_verdict=verdict,
            steps=records,
            assertion_outcomes=[
                {
                    "assertion_id": o.assertion_id,
                    "status": o.status.value,
                    "required": o.required,
                    "oracle_available": o.oracle_available,
                    "reason": o.reason,
                    "observed": o.observed,
                }
                for o in outcomes
            ],
            stop_reason="" if not interrupted else "execution_error",
            started_at=utc_now_iso(),
        )
        self.observer.teardown()
        return report

    def _run_api_step(self, base_url: str, step: Any) -> None:
        value = step.value or ""
        parts = value.split(" ", 1)
        if len(parts) == 2 and parts[0].upper() == "POST":
            self._post(base_url, parts[1], {})
        elif len(parts) == 2 and parts[0].upper() == "GET":
            self._get(base_url, parts[1])

    def _post(self, base_url: str, path: str, body: dict[str, Any]) -> None:
        try:
            req = urllib.request.Request(
                base_url + path,
                data=json.dumps(body).encode(),
                method="POST",
                headers={"Content-Type": "application/json"},
            )
            urllib.request.urlopen(req, timeout=10).close()
        except Exception:  # noqa: BLE001
            pass

    def _get(self, base_url: str, path: str) -> None:
        try:
            urllib.request.urlopen(base_url + path, timeout=10).close()
        except Exception:  # noqa: BLE001
            pass

    def _observation_for(
        self, spec: Any, records: list[ObservationRecord], state_ctx: dict[str, Any]
    ) -> dict[str, Any]:
        expected = spec.expected
        merged: dict[str, Any] = {}
        for r in records:
            for k, v in r.observation.items():
                merged.setdefault(k, v)
        if "field" in expected:
            field = expected["field"]
            # state_ctx 的 state_count 映射到业务状态字段名（如 count）
            if "state_count" in state_ctx:
                merged[field] = state_ctx["state_count"]
            merged.update(state_ctx)
        return merged
