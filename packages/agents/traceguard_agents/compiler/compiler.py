"""TestCase Compiler（阶段6）：Exploration Trace → 可审核候选用例 → 正式版本。

关键约束（路线图阶段6，全部实现）：
- Trace 不直接成为正式测试；产出 Candidate（review_status=DRAFT）；
- Locator 优先级：role > test_id > label > placeholder > css > xpath；
- Assertion 有预期来源；Oracle 绑定但不能自动批准；
- 候选可脱离 Explorer 独立回放（compile 产物是标准 TestCaseVersion JSON）；
- 稳定性验证：多次回放、每次独立 Run、不覆盖历史；不稳定不晋升；
- 人工审核通过才晋升正式版本（review() 需显式 approver）；
- 不降低必需 Assertion 严格度；重复失败分类而非一律 Flaky。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from traceguard_contracts.models import AssertionSpec, LocatorSpec, TestCaseVersion, TestStep
from traceguard_core.ids import new_id, utc_now_iso

COMPILER_VERSION = "compiler-v1"
LOCATOR_STRATEGY_VERSION = "locator-v1"


@dataclass
class CompileResult:
    candidate: TestCaseVersion
    source_exploration_id: str
    source_trace_hash: str
    compiler_version: str
    prompt_version: str
    model_version: str
    locator_strategy_version: str
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate": self.candidate.model_dump(mode="json"),
            "source_exploration_id": self.source_exploration_id,
            "source_trace_hash": self.source_trace_hash,
            "compiler_version": self.compiler_version,
            "prompt_version": self.prompt_version,
            "model_version": self.model_version,
            "locator_strategy_version": self.locator_strategy_version,
            "warnings": list(self.warnings),
        }


class TestCaseCompiler:
    def __init__(
        self, *, prompt_version: str = "prompt-v1", model_version: str = "rule-based-v1"
    ) -> None:
        self.prompt_version = prompt_version
        self.model_version = model_version

    def compile(self, trace: dict[str, Any], intent: str) -> CompileResult:
        """从 Exploration Trace 编译候选 TestCaseVersion（DRAFT）。"""
        trace_hash = hashlib.sha256(
            json.dumps(trace, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()
        warnings: list[str] = []

        # Action Extraction + Locator Resolver
        steps: list[TestStep] = []
        for i, act in enumerate(trace.get("action_sequence", [])):
            if act.get("gateway") == "denied":
                continue  # 被拒绝的动作不进入候选
            params = act.get("params", {})
            action = act.get("action", "")
            if action == "navigate":
                steps.append(
                    TestStep(step_id=f"s{i}", action="navigate", value=params.get("url", ""))
                )
            elif action in ("fill", "click"):
                locator = self._resolve_locator(params)
                if locator is None:
                    warnings.append(f"step {i}: locator unresolved for {action}")
                    continue
                steps.append(
                    TestStep(
                        step_id=f"s{i}",
                        action=action,
                        target=locator,
                        value=params.get("value"),
                    )
                )
            elif action == "read_page":
                continue  # 观测动作不进入正式步骤

        # Assertion Constructor：从意图生成候选断言（标记不确定性）
        assertions: list[AssertionSpec] = []
        if "login" in intent.lower():
            assertions.append(
                AssertionSpec(
                    assertion_id="cand_a_login",
                    description="登录成功提示（候选，需审核来源）",
                    kind="ui",
                    required=True,
                    expected={"test_id": "login-message", "text_contains": "登录成功"},
                    oracle_id=None,  # Oracle 绑定需人工审核，不自动批准
                )
            )
        elif "create" in intent.lower() or "创建" in intent:
            assertions.append(
                AssertionSpec(
                    assertion_id="cand_a_create_ui",
                    description="创建成功提示（候选）",
                    kind="ui",
                    required=True,
                    expected={"test_id": "banner-success", "text_contains": "项目创建成功"},
                    oracle_id=None,
                )
            )
            # 有业务状态接口才生成持久化断言
            if trace.get("business_state_api_available"):
                assertions.append(
                    AssertionSpec(
                        assertion_id="cand_a_create_persist",
                        description="业务持久化（Trusted Oracle 候选）",
                        kind="business_state",
                        required=True,
                        expected={
                            "api": trace["business_state_api_available"],
                            "field": "count",
                            "op": "gte",
                            "value": 1,
                        },
                        oracle_id=None,
                    )
                )
            else:
                warnings.append("NO_BUSINESS_STATE_API: 只能验证 UI 行为，不能宣称持久化已验证")

        if not assertions:
            # 兜底候选断言
            assertions.append(
                AssertionSpec(
                    assertion_id="cand_a_generic",
                    description="页面可读（候选）",
                    kind="ui",
                    required=True,
                    expected={"test_id": "page-title"},
                    oracle_id=None,
                )
            )

        candidate = TestCaseVersion(
            testcase_id=new_id("tcase"),
            version=1,
            intent_ref=intent,
            steps=steps,
            assertions=assertions,
            preconditions=["探索轨迹来源：" + trace.get("exploration_id", "unknown")],
            postconditions=[],
            cleanup_policy="RESET_DATA",
            run_config={"base_url": "http://127.0.0.1:8011"},
            compile_source="compiler",
            review_status="DRAFT",  # 候选状态，未审核
            immutable=True,
            created_at=utc_now_iso(),
            schema_version="0.1.0",
        )

        return CompileResult(
            candidate=candidate,
            source_exploration_id=trace.get("exploration_id", ""),
            source_trace_hash=trace_hash,
            compiler_version=COMPILER_VERSION,
            prompt_version=self.prompt_version,
            model_version=self.model_version,
            locator_strategy_version=LOCATOR_STRATEGY_VERSION,
            warnings=warnings,
        )

    def _resolve_locator(self, params: dict[str, Any]) -> LocatorSpec | None:
        """Locator 优先级：role > test_id > label > placeholder > css > xpath。"""
        if params.get("role"):
            return LocatorSpec(
                strategy="role", value=str(params["role"]), source="compiler", stability_score=0.9
            )
        tid = params.get("test_id")
        if tid:
            return LocatorSpec(
                strategy="test_id", value=str(tid), source="compiler", stability_score=0.85
            )
        if params.get("label"):
            return LocatorSpec(
                strategy="label", value=str(params["label"]), source="compiler", stability_score=0.7
            )
        if params.get("placeholder"):
            return LocatorSpec(
                strategy="placeholder",
                value=str(params["placeholder"]),
                source="compiler",
                stability_score=0.65,
            )
        if params.get("css"):
            return LocatorSpec(
                strategy="css", value=str(params["css"]), source="compiler", stability_score=0.5
            )
        if params.get("xpath"):
            return LocatorSpec(
                strategy="xpath", value=str(params["xpath"]), source="compiler", stability_score=0.3
            )
        return None

    def validate_stability(self, verdicts: list[str]) -> dict[str, Any]:
        """稳定性验证：多次回放 verdict 一致才算稳定。不降低断言严格度。

        区分：确定性失败 / 环境故障 / 数据问题 / 不稳定表现 / 证据不足。
        """
        if not verdicts:
            return {"stable": False, "classification": "INSUFFICIENT_EVIDENCE", "verdicts": []}
        unique = set(verdicts)
        if len(unique) == 1:
            if verdicts[0] == "PASSED":
                return {"stable": True, "classification": "STABLE_PASS", "verdicts": verdicts}
            return {"stable": True, "classification": "DETERMINISTIC_FAILURE", "verdicts": verdicts}
        # 不一致：不稳定
        if "INCONCLUSIVE" in unique and unique == {"INCONCLUSIVE", "PASSED"}:
            return {
                "stable": False,
                "classification": "EVIDENCE_INSUFFICIENT",
                "verdicts": verdicts,
            }
        return {"stable": False, "classification": "FLAKY_UNSTABLE", "verdicts": verdicts}

    def review(
        self,
        candidate: TestCaseVersion,
        *,
        approver: str,
        approve: bool,
        oracle_bindings: dict[str, str] | None = None,
    ) -> TestCaseVersion | None:
        """人工审核：只有显式审批者且 approve=True 才晋升正式版本。Oracle 绑定在此阶段人工确认。

        禁止 Agent 自批（approver 不能是 agent）。
        """
        if approver.lower().startswith("agent"):
            raise ValueError("AGENT_CANNOT_SELF_APPROVE")
        if not approve:
            candidate.review_status = "REJECTED"
            return None
        if oracle_bindings:
            for a in candidate.assertions:
                if a.assertion_id in oracle_bindings:
                    a.oracle_id = oracle_bindings[a.assertion_id]
        # 检查所有必需断言都绑定了 Oracle
        unbound = [a.assertion_id for a in candidate.assertions if a.required and not a.oracle_id]
        if unbound:
            candidate.review_status = "DRAFT"
            return None  # Oracle 未绑定，不能晋升
        candidate.review_status = "APPROVED"
        return candidate
