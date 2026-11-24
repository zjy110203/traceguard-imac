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
from urllib.parse import urlparse

from traceguard_contracts.models import AssertionSpec, LocatorSpec, TestCaseVersion, TestStep
from traceguard_core.ids import new_id, utc_now_iso

COMPILER_VERSION = "compiler-v1"
COMPILER_VERSION_LLM = "compiler-v2-llm"
COMPILER_MODES = {"rule_only", "llm_only", "llm_plus_rule_validation"}
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
    llm_meta: dict[str, Any] = field(default_factory=dict)

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
            "llm_meta": dict(self.llm_meta),
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

        # RUN-007 WP2：为 UI 断言补齐 assert 步骤。
        # 执行内核的 `_observation_for()` 只从 action=assert 的观测里取 DOM 文本；
        # 没有 assert 步骤时 `text_contains` 断言永远拿空文本 → 必然假 FAILED。
        # 这不是放宽断言，而是补上"执行断言所需的观测动作"。
        for a in assertions:
            tid = (a.expected or {}).get("test_id")
            if not tid or any(
                s.action == "assert" and (s.target or {}).get("value") == tid for s in steps
            ):
                continue
            steps.append(
                TestStep(
                    step_id=f"assert_{a.assertion_id}",
                    action="assert",
                    target=LocatorSpec(
                        strategy="test_id", value=str(tid), source="compiler", stability_score=0.9
                    ),
                    value=str(tid),
                )
            )

        # base_url 不能写死：跟随探索实际导航到的 origin，退化到当前配置
        base_url = _infer_base_url(trace)

        candidate = TestCaseVersion(
            testcase_id=new_id("tcase"),
            version=1,
            intent_ref=None,  # 引用由调用方（探索服务）补 intent_id
            intent_text=intent,
            steps=steps,
            assertions=assertions,
            preconditions=["探索轨迹来源：" + trace.get("exploration_id", "unknown")],
            postconditions=[],
            cleanup_policy="RESET_DATA",
            run_config={"base_url": base_url},
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

    #: 允许参与稳定性结论的"有效终态"；PENDING / NOT_EVALUATED 一律排除
    TERMINAL_EVALUATED_VERDICTS = frozenset({"PASSED", "FAILED", "INCONCLUSIVE"})
    #: 未完成 / 未被评估：既不证明稳定，也不证明失败
    UNRESOLVED_VERDICTS = frozenset({"PENDING", "NOT_EVALUATED"})

    def validate_stability(
        self, verdicts: list[str], *, expected_samples: int | None = None
    ) -> dict[str, Any]:
        """稳定性验证：多次回放 verdict 一致才算稳定。不降低断言严格度。

        状态语义（RUN-007 WP1-01 明确化）：
        - `PENDING`：Run 还没跑完 → 回放未完成，不得形成结论；
        - `NOT_EVALUATED`：跑完了但没有任何必需 Oracle 得到有效评估 → **不是失败**，
          只能算"证据不足"，绝不能归类为 `DETERMINISTIC_FAILURE`；
        - `INCONCLUSIVE`：尝试评估但证据不足以裁决 → 证据不足；
        - `PASSED` / `FAILED`：有效终态，可以作为稳定性样本。

        只有"全部样本都是 PASSED"才是 `STABLE_PASS`；只有"全部样本都是 FAILED"才是
        `DETERMINISTIC_FAILURE`。任何 NOT_EVALUATED / PENDING 混入都会把结论降级为
        `INCOMPLETE` 或 `EVIDENCE_INSUFFICIENT`，不会被当成确定性失败。
        """
        raw = list(verdicts or [])
        pending = [v for v in raw if v == "PENDING"]
        not_evaluated = [v for v in raw if v == "NOT_EVALUATED"]
        unknown = [v for v in raw if v not in self.TERMINAL_EVALUATED_VERDICTS | {"PENDING"}]
        evaluated = [v for v in raw if v in self.TERMINAL_EVALUATED_VERDICTS]
        expected = int(expected_samples) if expected_samples is not None else len(raw)

        base: dict[str, Any] = {
            "verdicts": raw,
            "samples_expected": expected,
            "samples_total": len(raw),
            "samples_evaluated": len(evaluated),
            "pending_count": len(pending),
            "not_evaluated_count": len(not_evaluated),
            "unknown_count": len(unknown),
            "reasons": [],
        }

        if pending:
            base.update(
                stable=False,
                classification="INCOMPLETE",
                reasons=[f"PENDING_SAMPLES: {len(pending)} 次回放尚未完成"],
            )
            return base
        if expected > len(raw):
            base.update(
                stable=False,
                classification="INCOMPLETE",
                reasons=[f"MISSING_SAMPLES: 期望 {expected} 次，实际只有 {len(raw)} 次"],
            )
            return base
        if not evaluated:
            base.update(
                stable=False,
                classification="INSUFFICIENT_EVIDENCE",
                reasons=["NO_EVALUATED_SAMPLE: 没有任何样本得到有效评估"],
            )
            return base

        unique = set(evaluated)
        if not_evaluated or unknown:
            # 有样本没被评估：结论必须降级，且明确说明不是"确定性失败"
            base.update(
                stable=False,
                classification="EVIDENCE_INSUFFICIENT",
                reasons=[
                    f"UNEVALUATED_SAMPLES: {len(not_evaluated)} 次 NOT_EVALUATED / "
                    f"{len(unknown)} 次未知状态，不能作为稳定性样本"
                ],
            )
            return base

        if len(unique) == 1:
            if evaluated[0] == "PASSED":
                base.update(stable=True, classification="STABLE_PASS")
                return base
            if evaluated[0] == "FAILED":
                base.update(stable=True, classification="DETERMINISTIC_FAILURE")
                return base
            # 全部 INCONCLUSIVE
            base.update(
                stable=False,
                classification="EVIDENCE_INSUFFICIENT",
                reasons=["ALL_INCONCLUSIVE: 所有样本证据不足，无法裁决"],
            )
            return base

        if "INCONCLUSIVE" in unique:
            # 含 INCONCLUSIVE 的混合样本无法判定为"确定性失败"，也不能算稳定
            base.update(
                stable=False,
                classification="EVIDENCE_INSUFFICIENT",
                reasons=["MIXED_WITH_INCONCLUSIVE: 部分样本证据不足，结论不可判定"],
            )
            return base

        base.update(stable=False, classification="FLAKY_UNSTABLE")
        base["reasons"] = ["VERDICT_DIVERGENCE: PASSED/FAILED 混杂，属于 Flaky 表现"]
        return base

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


# ---------------------------------------------------------------- LLM 辅助编译


def _infer_base_url(trace: dict[str, Any]) -> str:
    """从探索轨迹推断被测应用的 base_url（编译候选不能写死端口）。"""
    from traceguard_core.config import get_settings

    for act in trace.get("action_sequence") or []:
        if act.get("action") != "navigate":
            continue
        result = str(act.get("result") or "") or str((act.get("params") or {}).get("url") or "")
        parsed = urlparse(result)
        if parsed.scheme in {"http", "https"} and parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}"
    return get_settings().teamflow_base_url


def compile_candidate(
    trace: dict[str, Any],
    intent: str,
    *,
    mode: str = "rule_only",
    stack: dict[str, Any] | None = None,
    task_id: str = "",
    run_id: str = "",
) -> CompileResult:
    """按功能开关选择编译路径。

    - `rule_only`：原规则编译器（Baseline / Fallback，永远保留）；
    - `llm_only`：LLM 生成候选步骤与断言；失败/无有效输出 → 回退规则并标记 fallback；
    - `llm_plus_rule_validation`：LLM 生成步骤 + 规则断言作为基线校验（只增不减严格度）。

    不变量：Oracle 一律不绑定、不自动批准；review_status 恒为 DRAFT；LLM 不得成为事实层。
    """
    mode = (mode or "rule_only").strip().lower()
    if mode not in COMPILER_MODES:
        mode = "rule_only"
    rule = TestCaseCompiler().compile(trace, intent)

    if mode == "rule_only":
        rule.llm_meta = {"mode": "rule_only", "called": False, "fallback": False}
        return rule

    if not stack:
        rule.warnings.append("LLM_COMPILER_UNAVAILABLE: 未配置模型凭据，规则编译作为兜底")
        rule.llm_meta = {
            "mode": mode,
            "called": False,
            "fallback": True,
            "error": "not_configured",
        }
        return rule

    from ..gateway.llm_planners import LLMCompilerAdvisor

    advisor = LLMCompilerAdvisor(
        provider=stack["provider"],
        router=stack["router"],
        prompts=stack["prompts"],
        tracker=stack.get("tracker"),
        task_id=task_id,
        run_id=run_id,
    )
    advice = advisor.suggest(trace, intent)
    if advice.fallback or not advice.steps:
        rule.warnings.append(
            f"LLM_COMPILER_FALLBACK: {advice.error or 'no valid step'}（规则编译结果作为兜底）"
        )
        rule.llm_meta = {
            "mode": mode,
            "called": advice.called,
            "fallback": True,
            "error": advice.error,
            "model": advice.model,
            "prompt_version": "compile_advisor@v1",
            "dropped_steps": advice.dropped_steps,
            "warnings": advice.warnings,
        }
        return rule

    candidate = rule.candidate.model_copy(deep=True)
    candidate.steps = advice.steps
    if mode == "llm_only":
        candidate.assertions = advice.assertions or rule.candidate.assertions
    else:
        # 规则断言作为基线保留；LLM 只能新增更严格断言，不能替换/删除
        assertions = list(rule.candidate.assertions)
        for a in advice.assertions:
            assertions.append(a)
        candidate.assertions = assertions
    candidate.review_status = "DRAFT"
    for a in candidate.assertions:
        a.oracle_id = None  # 任何路径都不得自动绑定/批准 Oracle

    warnings = list(rule.warnings) + advice.warnings
    if mode == "llm_plus_rule_validation" and not advice.assertions:
        warnings.append("LLM_ASSERTION_NONE: 使用规则断言基线，未采纳 LLM 断言")
    return CompileResult(
        candidate=candidate,
        source_exploration_id=rule.source_exploration_id,
        source_trace_hash=rule.source_trace_hash,
        compiler_version=COMPILER_VERSION_LLM,
        prompt_version="compile_advisor@v1",
        model_version=advice.model or "unknown",
        locator_strategy_version=LOCATOR_STRATEGY_VERSION,
        warnings=warnings,
        llm_meta={
            "mode": mode,
            "called": advice.called,
            "fallback": False,
            "model": advice.model,
            "prompt_version": "compile_advisor@v1",
            "compiler_version": COMPILER_VERSION_LLM,
            "dropped_steps": advice.dropped_steps,
            "llm_steps": len(advice.steps),
            "llm_assertions": len(advice.assertions),
            "rule_assertions_kept": len(rule.candidate.assertions),
            "warnings": advice.warnings,
        },
    )
