"""LLM 辅助的三类扩展顾问（RUN-005 WP2）。

统一复用 `llm_gateway` 的 ModelGateway / ModelRouter / PromptManager / TokenUsageTracker，
不为每个模块重复造模型调用框架。

安全边界（三者共同）：
- LLM 只能"建议"，不能成为事实层：不产生 CONFIRMED、不批准 Oracle、不批准/晋升 Repair；
- 输入不含 Ground Truth；
- 输出必须通过结构化白名单校验，越界内容直接丢弃并记录 warning；
- 任何 LLM 故障 → 回退确定性规则实现，并显式标记 fallback，不伪装成功。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from traceguard_contracts.models import AssertionSpec, LocatorSpec, TestStep
from traceguard_core.ids import new_id

from .llm_gateway import LLMError, ModelRouter, PromptManager, TokenUsageTracker

LOCATOR_STRATEGIES = {"role", "accessible_name", "label", "placeholder", "test_id", "css", "xpath"}
SAFE_PROBE_PARAM_KEYS = {
    "template",
    "project_key",
    "run_id",
    "step_id",
    "diagnosis_id",
    "line_limit",
}
REPAIR_ALLOWED_PATHS = {
    "steps[{i}].target",
    "steps[{i}].value",
    "steps[{i}].action_config",
    "run_config.wait_timeout",
    "test_data_refs",
}


def ensure_advisor_prompts(prompts: PromptManager) -> None:
    """注册三类顾问 Prompt（幂等）。版本独立，便于回溯。"""
    try:
        prompts.get("compile_advisor", "v1")
        return
    except LLMError:
        pass
    prompts.register(
        "compile_advisor",
        "v1",
        system=(
            "你是 Web UI 测试用例编译器。只能基于输入探索轨迹中真实出现过的页面元素生成步骤，"
            "禁止凭空创造元素、字段、按钮或业务预期。"
            "输出 JSON：{\"steps\": [{\"action\": \"navigate|click|fill\", \"target\": "
            "{\"strategy\": \"role|label|placeholder|test_id|css|xpath\", \"value\": str} | null, "
            "\"value\": str|null, \"source\": str}], "
            "\"assertions\": [{\"description\": str, \"kind\": \"ui|business_state\", "
            "\"expected\": object}], \"warnings\": [str]}。"
            "每个步骤的 source 必须写明对应轨迹动作序号或元素来源。"
            "不要输出 JSON 以外的内容。"
        ),
        user_template=(
            "测试意图：{intent}\n"
            "探索轨迹（含动作与元素定位信息）：{trace}\n"
            "允许的定位值集合（必须来自轨迹）：{locators}\n"
            "可用业务状态接口：{business_api}\n"
            "输出候选步骤与候选断言 JSON。"
        ),
    )
    prompts.register(
        "probe_planner",
        "v1",
        system=(
            "你是只读探针规划顾问。只能从给定的已注册探针与模板中选择，禁止发明新探针、"
            "禁止生成 SQL 或任意网络地址。输出 JSON：{\"selections\": [{\"probe_id\": str, "
            "\"template\": str, \"params\": object, \"why\": str, \"info_gain\": 0~1}]}，最多 3 项。"
            "info_gain 是启发式估计，不是校准概率。不要输出 JSON 以外的内容。"
        ),
        user_template=(
            "证据缺口：{gaps}\n"
            "已注册探针（含允许模板）：{definitions}\n"
            "输出探针选择 JSON。"
        ),
    )
    prompts.register(
        "repair_planner",
        "v1",
        system=(
            "你是测试资产修复顾问。只允许修复：定位器、等待策略、数据引用、动作配置。"
            "严禁修改断言、预期、Oracle、业务代码或降低测试严格度。"
            "输出 JSON：{\"changes\": [{\"path\": str, \"step_index\": int|null, "
            "\"value\": any, \"reason\": str}], \"warnings\": [str]}。"
            "path 只能是 steps[i].target / steps[i].value / steps[i].action_config / "
            "run_config.wait_timeout / test_data_refs。不要输出 JSON 以外的内容。"
        ),
        user_template=(
            "失败症状：{symptom}\n"
            "步骤结构：{steps}\n"
            "当前断言（只读，不得修改）：{assertions}\n"
            "输出修复变更 JSON。"
        ),
    )


# ---------------------------------------------------------------- compiler


@dataclass
class CompileAdvice:
    steps: list[TestStep] = field(default_factory=list)
    assertions: list[AssertionSpec] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    called: bool = False
    fallback: bool = False
    error: str = ""
    model: str = ""
    dropped_steps: int = 0


class LLMCompilerAdvisor:
    """LLM 用例编译顾问：只产出候选步骤/断言，全部经过白名单与来源校验。"""

    def __init__(
        self,
        provider: Any,
        router: ModelRouter,
        prompts: PromptManager,
        tracker: TokenUsageTracker | None = None,
        task_id: str = "",
        run_id: str = "",
    ) -> None:
        self.provider = provider
        self.router = router
        self.prompts = prompts
        self.tracker = tracker
        self.task_id = task_id
        self.run_id = run_id
        ensure_advisor_prompts(prompts)

    def suggest(self, trace: dict[str, Any], intent: str) -> CompileAdvice:
        locators = _trace_locator_values(trace)
        messages = self.prompts.render(
            "compile_advisor",
            "v1",
            intent=str(intent)[:1000],
            trace=json.dumps(_trace_digest(trace), ensure_ascii=False)[:8000],
            locators=json.dumps(sorted(locators), ensure_ascii=False)[:3000],
            business_api=str(trace.get("business_state_api_available") or "无（禁止生成业务断言）"),
        )
        model = self.router.default_model
        try:
            parsed, _ = self.provider.chat_json(
                messages,
                model=model,
                agent="compiler",
                task_id=self.task_id,
                run_id=self.run_id,
                prompt_version=f"deepseek:{model}:compile_advisor@v1",
                max_tokens=1200,
                tracker=self.tracker,
            )
        except LLMError as exc:
            return CompileAdvice(called=True, fallback=True, error=exc.kind, model=model)

        advice = CompileAdvice(called=True, model=model)
        for raw in (parsed.get("steps") or [])[:20]:
            step = self._validate_step(raw, locators, advice.warnings)
            if step is None:
                advice.dropped_steps += 1
                continue
            advice.steps.append(step)
        business_api = trace.get("business_state_api_available")
        for raw in (parsed.get("assertions") or [])[:6]:
            assertion = self._validate_assertion(raw, locators, business_api, advice.warnings)
            if assertion is not None:
                advice.assertions.append(assertion)
        for w in (parsed.get("warnings") or [])[:5]:
            if isinstance(w, str):
                advice.warnings.append(f"llm: {w[:200]}")
        if not advice.steps:
            advice.fallback = True
            advice.error = "llm_returned_no_valid_step"
        return advice

    def _validate_step(
        self, raw: Any, locators: set[str], warnings: list[str]
    ) -> TestStep | None:
        if not isinstance(raw, dict):
            return None
        action = str(raw.get("action", "")).lower()
        if action not in {"navigate", "click", "fill"}:
            warnings.append(f"llm_step_dropped: action {action!r} 不在白名单")
            return None
        source = str(raw.get("source", ""))[:160] or "llm"
        if action == "navigate":
            value = str(raw.get("value") or "")
            if not value.startswith(("http://", "https://")):
                warnings.append("llm_step_dropped: navigate 缺少合法 URL")
                return None
            return TestStep(
                step_id=new_id("s"),
                action="navigate",
                value=value[:500],
                description=f"来源：{source}",
            )
        target = raw.get("target") or {}
        if not isinstance(target, dict):
            warnings.append("llm_step_dropped: target 结构非法")
            return None
        strategy = str(target.get("strategy", "")).lower()
        value = str(target.get("value", ""))
        if strategy not in LOCATOR_STRATEGIES or not value:
            warnings.append("llm_step_dropped: 定位策略不在白名单")
            return None
        if value not in locators:
            # 防止虚构页面元素：定位值必须能在探索轨迹里找到出处
            warnings.append(f"llm_step_dropped: locator {value!r} 不在轨迹中（防虚构）")
            return None
        return TestStep(
            step_id=new_id("s"),
            action=action,  # type: ignore[arg-type]
            target=LocatorSpec(strategy=strategy, value=value[:200], source="compiler"),  # type: ignore[arg-type]
            value=(str(raw.get("value"))[:300] if action == "fill" and raw.get("value") else None),
            description=f"来源：{source}",
        )

    def _validate_assertion(
        self,
        raw: Any,
        locators: set[str],
        business_api: str | None,
        warnings: list[str],
    ) -> AssertionSpec | None:
        if not isinstance(raw, dict):
            return None
        kind = str(raw.get("kind", "ui")).lower()
        description = str(raw.get("description", "")).strip()[:300]
        expected = raw.get("expected")
        if kind not in {"ui", "business_state"} or not description or not isinstance(expected, dict):
            warnings.append("llm_assertion_dropped: 结构或 kind 非法")
            return None
        if kind == "business_state":
            if not business_api:
                warnings.append("llm_assertion_dropped: 无业务状态接口，禁止生成业务预期")
                return None
            if not expected.get("api"):
                warnings.append("llm_assertion_dropped: 业务断言缺少 api 来源")
                return None
        else:
            tid = expected.get("test_id")
            if not tid or str(tid) not in locators:
                warnings.append("llm_assertion_dropped: UI 断言缺少轨迹来源（防虚构）")
                return None
        return AssertionSpec(
            assertion_id=new_id("cand_a_llm"),
            description=description,
            kind=kind,  # type: ignore[arg-type]
            required=True,
            expected=expected,
            oracle_id=None,  # Oracle 只能人工审核绑定，LLM 不得自动批准
        )


def _trace_digest(trace: dict[str, Any]) -> dict[str, Any]:
    """只保留编译必需信息，避免把整份 trace 塞进 Prompt。"""
    return {
        "exploration_id": trace.get("exploration_id", ""),
        "business_state_api_available": trace.get("business_state_api_available"),
        "action_sequence": [
            {
                "index": i,
                "action": a.get("action"),
                "gateway": a.get("gateway"),
                "params": a.get("params"),
            }
            for i, a in enumerate(trace.get("action_sequence", [])[:60])
        ],
    }


def _trace_locator_values(trace: dict[str, Any]) -> set[str]:
    values: set[str] = set()
    for act in trace.get("action_sequence", []):
        params = act.get("params") or {}
        for key in ("role", "accessible_name", "label", "placeholder", "test_id", "css", "xpath"):
            if params.get(key):
                values.add(str(params[key]))
    for el in trace.get("elements") or []:
        if isinstance(el, dict):
            for key in ("role", "label", "placeholder", "test_id", "css", "xpath"):
                if el.get(key):
                    values.add(str(el[key]))
    return values


# ------------------------------------------------------------ probe planner


@dataclass
class ProbeAdvice:
    selections: list[dict[str, Any]] = field(default_factory=list)
    called: bool = False
    fallback: bool = False
    error: str = ""
    model: str = ""
    dropped: int = 0


class LLMProbePlannerAdvisor:
    """LLM 探针规划顾问：只能在已注册探针与白名单模板中做选择。"""

    def __init__(
        self,
        provider: Any,
        router: ModelRouter,
        prompts: PromptManager,
        tracker: TokenUsageTracker | None = None,
        task_id: str = "",
        run_id: str = "",
    ) -> None:
        self.provider = provider
        self.router = router
        self.prompts = prompts
        self.tracker = tracker
        self.task_id = task_id
        self.run_id = run_id
        ensure_advisor_prompts(prompts)

    def suggest(
        self,
        gaps: list[dict[str, Any]],
        definitions: dict[str, Any],
    ) -> ProbeAdvice:
        defs = [
            {
                "probe_id": d.probe_id,
                "capability": d.capability,
                "risk_level": d.risk_level,
                "allowed_templates": sorted(d.allowed_templates),
                "estimated_cost": d.estimated_cost,
            }
            for d in definitions.values()
        ]
        messages = self.prompts.render(
            "probe_planner",
            "v1",
            gaps=json.dumps(
                [
                    {
                        "gap_id": g["gap_id"],
                        "probe_id": g["probe_id"],
                        "why": g["why"],
                        "info_gain": g["info_gain"],
                    }
                    for g in gaps
                ],
                ensure_ascii=False,
            )[:4000],
            definitions=json.dumps(defs, ensure_ascii=False)[:4000],
        )
        model = self.router.default_model
        try:
            parsed, _ = self.provider.chat_json(
                messages,
                model=model,
                agent="probe_planner",
                task_id=self.task_id,
                run_id=self.run_id,
                prompt_version=f"deepseek:{model}:probe_planner@v1",
                max_tokens=700,
                tracker=self.tracker,
            )
        except LLMError as exc:
            return ProbeAdvice(called=True, fallback=True, error=exc.kind, model=model)

        advice = ProbeAdvice(called=True, model=model)
        for raw in (parsed.get("selections") or [])[:3]:
            if not isinstance(raw, dict):
                continue
            probe_id = str(raw.get("probe_id", ""))
            definition = definitions.get(probe_id)
            if definition is None:
                advice.dropped += 1  # 禁止发明新探针
                continue
            template = raw.get("template")
            if template is not None and str(template) not in definition.allowed_templates:
                advice.dropped += 1  # 模板必须命中白名单
                continue
            params = raw.get("params") or {}
            if not isinstance(params, dict):
                params = {}
            safe_params = {
                k: v for k, v in params.items() if k in SAFE_PROBE_PARAM_KEYS
            }
            if template is not None:
                safe_params["template"] = str(template)
            try:
                info_gain = min(max(float(raw.get("info_gain", 0.5)), 0.0), 1.0)
            except (TypeError, ValueError):
                info_gain = 0.5
            advice.selections.append(
                {
                    "probe_id": probe_id,
                    "params": safe_params,
                    "why": str(raw.get("why", ""))[:300],
                    "info_gain": info_gain,
                }
            )
        if not advice.selections:
            advice.fallback = True
            advice.error = "llm_returned_no_valid_probe"
        return advice


# ----------------------------------------------------------- repair planner


@dataclass
class RepairAdvice:
    changes: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    called: bool = False
    fallback: bool = False
    error: str = ""
    model: str = ""


class LLMRepairAdvisor:
    """LLM 修复顾问：只生成受限变更，后续必须经过校验/沙箱/人工审批/CAS。"""

    def __init__(
        self,
        provider: Any,
        router: ModelRouter,
        prompts: PromptManager,
        tracker: TokenUsageTracker | None = None,
        task_id: str = "",
        run_id: str = "",
    ) -> None:
        self.provider = provider
        self.router = router
        self.prompts = prompts
        self.tracker = tracker
        self.task_id = task_id
        self.run_id = run_id
        ensure_advisor_prompts(prompts)

    def suggest(
        self,
        *,
        symptom: str,
        steps: list[dict[str, Any]],
        assertions: list[dict[str, Any]],
        step_count: int,
    ) -> RepairAdvice:
        messages = self.prompts.render(
            "repair_planner",
            "v1",
            symptom=str(symptom)[:1200],
            steps=json.dumps(steps[:20], ensure_ascii=False, default=str)[:5000],
            assertions=json.dumps(assertions[:10], ensure_ascii=False, default=str)[:3000],
        )
        model = self.router.default_model
        try:
            parsed, _ = self.provider.chat_json(
                messages,
                model=model,
                agent="repair_planner",
                task_id=self.task_id,
                run_id=self.run_id,
                prompt_version=f"deepseek:{model}:repair_planner@v1",
                max_tokens=1800,
                tracker=self.tracker,
            )
        except LLMError as exc:
            return RepairAdvice(called=True, fallback=True, error=exc.kind, model=model)

        advice = RepairAdvice(called=True, model=model)
        for raw in (parsed.get("changes") or [])[:8]:
            change = self._validate_change(raw, step_count, advice.warnings)
            if change is not None:
                advice.changes.append(change)
        for w in (parsed.get("warnings") or [])[:5]:
            if isinstance(w, str):
                advice.warnings.append(f"llm: {w[:200]}")
        if not advice.changes:
            advice.fallback = True
            advice.error = "llm_returned_no_valid_change"
        return advice

    def _validate_change(
        self, raw: Any, step_count: int, warnings: list[str]
    ) -> dict[str, Any] | None:
        if not isinstance(raw, dict):
            return None
        path = str(raw.get("path", ""))
        low = path.lower()
        for banned in ("assertion", "oracle", "expected", "required", "business"):
            if banned in low:
                warnings.append(f"llm_change_dropped: 触及禁止字段 {banned}")
                return None  # 直接丢弃，不交给下游"再判断"
        step_index = raw.get("step_index")
        if path.startswith("steps["):
            if not isinstance(step_index, int) or not (0 <= step_index < step_count):
                warnings.append("llm_change_dropped: step_index 非法")
                return None
            suffix = path.split("].", 1)[-1]
            if suffix not in {"target", "value", "action_config"}:
                warnings.append(f"llm_change_dropped: 不支持的步骤字段 {suffix}")
                return None
            normalized = f"steps[{step_index}].{suffix}"
        elif path in {"run_config.wait_timeout", "test_data_refs"}:
            normalized = path
        else:
            warnings.append(f"llm_change_dropped: 不支持的路径 {path}")
            return None
        return {
            "path": normalized,
            "step_index": step_index if isinstance(step_index, int) else None,
            "value": raw.get("value"),
            "reason": str(raw.get("reason", ""))[:300],
            "suggested_by": f"llm:{self.model_name()}",
        }

    def model_name(self) -> str:
        return self.router.default_model


__all__ = [
    "CompileAdvice",
    "LLMCompilerAdvisor",
    "LLMProbePlannerAdvisor",
    "LLMRepairAdvisor",
    "ProbeAdvice",
    "RepairAdvice",
    "ensure_advisor_prompts",
]
