"""RUN-012 WP0：Benchmark v5 场景契约与执行记录五分类。

设计目的（修复 R1.2 的正向控制归类问题）：

1. 场景期望在**场景定义**中显式声明，而不是从 HTTP 412 + Run PASSED 反推；
2. 严格区分「预期通过的控制」「故障未触发」「False Pass」「普通执行异常」
   和「故障触发状态未知」五类，不再混成一个 error 字段；
3. 每条执行记录尽量保留完整上下文（GT、故障、TestCase、诊断与错误细节）；
4. 历史归档没有结构化故障触发证据时，只能给出 INFERRED / UNKNOWN，
   不得为了维持漂亮的 0/37 强行归类。

本模块只依赖标准库，可被脚本与离线测试直接导入。
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any


class ControlKind(str, Enum):
    NEGATIVE_FIXTURE = "NEGATIVE_FIXTURE"  # 确定性故障，预期 Run FAILED
    POSITIVE_CONTROL_EXPECTED_PASS = "POSITIVE_CONTROL_EXPECTED_PASS"  # 无故障的确定性正常控制
    POSITIVE_CONTROL_TOLERANT_FAULT = "POSITIVE_CONTROL_TOLERANT_FAULT"  # 故障已注入但断言容忍
    FLAKY_NEGATIVE_FIXTURE = "FLAKY_NEGATIVE_FIXTURE"  # 概率故障，预期失败但可能未触发
    EXECUTION_EXCEPTION = "EXECUTION_EXCEPTION"  # 与场景期望无关的普通异常
    UNKNOWN = "UNKNOWN"  # 契约缺失，不允许当成正向控制


class RecordClass(str, Enum):
    EXPECTED_FAILURE_CONFIRMED = "EXPECTED_FAILURE_CONFIRMED"  # 预期失败且观测到失败（正常负样本）
    NORMAL_POSITIVE_CONTROL = "NORMAL_POSITIVE_CONTROL"
    FAULT_NOT_TRIGGERED = "FAULT_NOT_TRIGGERED"
    FALSE_PASS = "FALSE_PASS"
    EXECUTION_EXCEPTION = "EXECUTION_EXCEPTION"
    UNKNOWN_FAULT_ACTIVATION = "UNKNOWN_FAULT_ACTIVATION"


class TriState(str, Enum):
    CONFIRMED = "CONFIRMED"
    NOT_TRIGGERED = "NOT_TRIGGERED"
    INFERRED_FROM_VERDICT = "INFERRED_FROM_VERDICT"
    UNKNOWN = "UNKNOWN"


class ExpectedRunOutcome(str, Enum):
    PASSED = "PASSED"
    FAILED = "FAILED"
    NOT_EVALUATED = "NOT_EVALUATED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class ScenarioContract:
    """场景契约：期望与故障触发条件都由场景定义明确声明。"""

    scenario: str
    testcase: str
    fault: str
    gt_mechanism: str
    expected_run_outcome: ExpectedRunOutcome
    control_kind: ControlKind
    fault_trigger_condition: str
    deterministic_activation: bool = True

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        for key in ("expected_run_outcome", "control_kind"):
            data[key] = getattr(self, key).value
        return data


def _contract(
    scenario: str,
    testcase: str,
    fault: str,
    gt: str,
    expected: ExpectedRunOutcome,
    kind: ControlKind,
    trigger: str,
    deterministic: bool = True,
) -> ScenarioContract:
    return ScenarioContract(
        scenario=scenario,
        testcase=testcase,
        fault=fault,
        gt_mechanism=gt,
        expected_run_outcome=expected,
        control_kind=kind,
        fault_trigger_condition=trigger,
        deterministic_activation=deterministic,
    )


#: 23 个 v5 场景的显式契约。expected=PASSED 的四类用例来自 seed.py 的用例语义：
#: UI-DELAYED 等待 4000ms 覆盖 3000ms 延迟；TIME-STRONG 标题即「预期通过」；
#: API-EDIT-500/503 的断言是「读取编辑错误提示」，Run PASSED 表示故障被正确检出。
#: 这四类不是 False Pass：它们的断言期望故障存在，PASSED 代表测试目标达成。
V5_SCENARIO_CONTRACTS: dict[str, ScenarioContract] = {
    c.scenario: c
    for c in [
        _contract(
            "FALSE-PASS",
            "tcase_tf_false_pass",
            "TF-DB-001",
            "PERSISTENCE",
            ExpectedRunOutcome.FAILED,
            ControlKind.NEGATIVE_FIXTURE,
            "用例 api 步骤激活 TF-DB-001；独立状态查询发现未持久化 → biz 断言失败",
        ),
        _contract(
            "UI-MISSING",
            "tcase_tf_ui_missing",
            "TF-UI-001",
            "UI_RENDERING",
            ExpectedRunOutcome.FAILED,
            ControlKind.NEGATIVE_FIXTURE,
            "用例 api 步骤激活 TF-UI-001；banner-success 缺失 → 断言失败",
        ),
        _contract(
            "API-500",
            "tcase_tf_api_error",
            "TF-API-001",
            "API",
            ExpectedRunOutcome.FAILED,
            ControlKind.NEGATIVE_FIXTURE,
            "用例 api 步骤激活 TF-API-001；创建接口 500 → 断言失败",
        ),
        _contract(
            "DB-DUP",
            "tcase_bench_db002",
            "TF-DB-002",
            "PERSISTENCE",
            ExpectedRunOutcome.FAILED,
            ControlKind.NEGATIVE_FIXTURE,
            "首轮正常创建后激活 TF-DB-002；同名写入谎报成功 → biz 断言失败",
        ),
        _contract(
            "DB-LAG-9S",
            "tcase_bench_db004",
            "TF-DB-004",
            "TIMING",
            ExpectedRunOutcome.FAILED,
            ControlKind.NEGATIVE_FIXTURE,
            "用例 api 步骤激活 TF-DB-004；权威状态查询滞后 9000ms → 断言失败",
        ),
        _contract(
            "UI-SILENT",
            "tcase_bench_ui002",
            "TF-UI-002",
            "UI_RENDERING",
            ExpectedRunOutcome.FAILED,
            ControlKind.NEGATIVE_FIXTURE,
            "用例 api 步骤激活 TF-UI-002；提交被静默忽略 → 断言失败",
        ),
        _contract(
            "UI-MISLEAD",
            "tcase_bench_ui003",
            "TF-UI-003",
            "UI_RENDERING",
            ExpectedRunOutcome.FAILED,
            ControlKind.NEGATIVE_FIXTURE,
            "用例 api 步骤激活 TF-UI-003；错误文案误导 → 断言失败",
        ),
        _contract(
            "UI-DELAYED",
            "tcase_bench_ui004",
            "TF-UI-004",
            "UI_RENDERING",
            ExpectedRunOutcome.PASSED,
            ControlKind.POSITIVE_CONTROL_TOLERANT_FAULT,
            "用例 api 步骤激活 TF-UI-004（成功提示延迟 3000ms）；等待 4000ms 后读取提示，预期仍 PASSED",
        ),
        _contract(
            "API-503",
            "tcase_bench_api002",
            "TF-API-002",
            "API",
            ExpectedRunOutcome.FAILED,
            ControlKind.NEGATIVE_FIXTURE,
            "用例 api 步骤激活 TF-API-002；创建接口 503 → 断言失败",
        ),
        _contract(
            "API-EDIT-500",
            "tcase_bench_api003",
            "TF-API-003",
            "API",
            ExpectedRunOutcome.PASSED,
            ControlKind.POSITIVE_CONTROL_TOLERANT_FAULT,
            "edit_flow 激活 TF-API-003；PUT 返回 500，断言读取 edit-error，预期 PASSED",
        ),
        _contract(
            "API-EDIT-503",
            "tcase_bench_api004",
            "TF-API-004",
            "API",
            ExpectedRunOutcome.PASSED,
            ControlKind.POSITIVE_CONTROL_TOLERANT_FAULT,
            "edit_flow 激活 TF-API-004；PUT 返回 503，断言读取 edit-error，预期 PASSED",
        ),
        _contract(
            "NET-TIMEOUT",
            "tcase_bench_net002",
            "TF-NET-002",
            "NETWORK",
            ExpectedRunOutcome.FAILED,
            ControlKind.NEGATIVE_FIXTURE,
            "用例 api 步骤激活 TF-NET-002；9000ms 延迟超过等待窗口 → 断言失败",
        ),
        _contract(
            "LOC-SUBMIT",
            "tcase_bench_loc001",
            "TF-LOC-001",
            "LOCATOR",
            ExpectedRunOutcome.FAILED,
            ControlKind.NEGATIVE_FIXTURE,
            "用例 api 步骤激活 TF-LOC-001；提交按钮 test_id 变更 → 步骤失败/NOT_EVALUATED",
        ),
        _contract(
            "LOC-NAME",
            "tcase_bench_loc002",
            "TF-LOC-002",
            "LOCATOR",
            ExpectedRunOutcome.FAILED,
            ControlKind.NEGATIVE_FIXTURE,
            "用例 api 步骤激活 TF-LOC-002；名称输入框 test_id 变更 → 步骤失败/NOT_EVALUATED",
        ),
        _contract(
            "TIME-STRONG",
            "tcase_bench_time002",
            "TF-TIME-002",
            "TIMING",
            ExpectedRunOutcome.PASSED,
            ControlKind.POSITIVE_CONTROL_TOLERANT_FAULT,
            "用例 api 步骤激活 TF-TIME-002（列表延迟 5000ms）；seed 标题明确写「预期通过」",
        ),
        _contract(
            "TIME-RACE",
            "tcase_bench_time003",
            "TF-TIME-003",
            "TIMING",
            ExpectedRunOutcome.FAILED,
            ControlKind.NEGATIVE_FIXTURE,
            "double_submit_flow 触发 300ms 双提交竞态 → 断言失败",
        ),
        _contract(
            "SEC-CREATE-403",
            "tcase_bench_sec001",
            "TF-SEC-001",
            "AUTHORIZATION",
            ExpectedRunOutcome.FAILED,
            ControlKind.NEGATIVE_FIXTURE,
            "用例 api 步骤激活 TF-SEC-001；创建接口 403 → 断言失败",
        ),
        _contract(
            "SEC-LIST-403",
            "tcase_bench_sec002",
            "TF-SEC-002",
            "AUTHORIZATION",
            ExpectedRunOutcome.FAILED,
            ControlKind.NEGATIVE_FIXTURE,
            "用例 api 步骤激活 TF-SEC-002；列表接口 403 → 断言失败",
        ),
        _contract(
            "COMP-API-UI",
            "tcase_bench_comp001",
            "TF-COMP-001",
            "API",
            ExpectedRunOutcome.FAILED,
            ControlKind.NEGATIVE_FIXTURE,
            "复合故障激活 TF-API-001 + TF-UI-003 → 断言失败",
        ),
        _contract(
            "COMP-DB-TIME",
            "tcase_bench_comp002",
            "TF-COMP-002",
            "PERSISTENCE",
            ExpectedRunOutcome.FAILED,
            ControlKind.NEGATIVE_FIXTURE,
            "复合故障激活 TF-DB-001 + TF-TIME-001 → 断言失败",
        ),
        _contract(
            "COMP-LOC-UI",
            "tcase_bench_comp003",
            "TF-COMP-003",
            "LOCATOR",
            ExpectedRunOutcome.FAILED,
            ControlKind.NEGATIVE_FIXTURE,
            "复合故障激活 TF-LOC-001 + TF-UI-002 → 断言失败",
        ),
        _contract(
            "FLAKY-DB",
            "tcase_bench_flaky001",
            "TF-FLAKY-001",
            "PERSISTENCE",
            ExpectedRunOutcome.FAILED,
            ControlKind.FLAKY_NEGATIVE_FIXTURE,
            "TF-FLAKY-001 以 50% 概率跳过持久化；未生效时 Run PASSED 不能算 False Pass",
            deterministic=False,
        ),
        _contract(
            "FLAKY-API",
            "tcase_bench_flaky002",
            "TF-FLAKY-002",
            "API",
            ExpectedRunOutcome.FAILED,
            ControlKind.FLAKY_NEGATIVE_FIXTURE,
            "TF-FLAKY-002 以概率触发 API 500；未生效时 Run PASSED 不能算 False Pass",
            deterministic=False,
        ),
    ]
}

_POSITIVE_CONTROL_ERROR_RE = re.compile(r"\b412\b")
_RUN_PASSED_ERROR_RE = re.compile(r"run\s+PASSED", re.IGNORECASE)

# R1.2 归档里的 9 条异常按契约 + seed 语义分类。
# FLAKY-DB 第一轮没有任何故障生效证据（Run PASSED + 概率故障），标 NOT_TRIGGERED 而非正向控制。
# 其余八个场景的故障注入是确定性的，412 来自诊断接口对 PASSED Run 的正确拒绝。
HISTORICAL_ERROR_CLASSIFICATION: dict[tuple[str, int], dict[str, str]] = {
    ("UI-DELAYED", 1): {
        "record_class": RecordClass.NORMAL_POSITIVE_CONTROL.value,
        "fault_activation_status": TriState.CONFIRMED.value,
        "fault_effect_status": TriState.INFERRED_FROM_VERDICT.value,
        "confidence": "CONTRACT_INFERRED",
    },
    ("API-EDIT-500", 1): {
        "record_class": RecordClass.NORMAL_POSITIVE_CONTROL.value,
        "fault_activation_status": TriState.CONFIRMED.value,
        "fault_effect_status": TriState.INFERRED_FROM_VERDICT.value,
        "confidence": "CONTRACT_INFERRED",
    },
    ("API-EDIT-503", 1): {
        "record_class": RecordClass.NORMAL_POSITIVE_CONTROL.value,
        "fault_activation_status": TriState.CONFIRMED.value,
        "fault_effect_status": TriState.INFERRED_FROM_VERDICT.value,
        "confidence": "CONTRACT_INFERRED",
    },
    ("TIME-STRONG", 1): {
        "record_class": RecordClass.NORMAL_POSITIVE_CONTROL.value,
        "fault_activation_status": TriState.CONFIRMED.value,
        "fault_effect_status": TriState.INFERRED_FROM_VERDICT.value,
        "confidence": "CONTRACT_INFERRED",
    },
    ("FLAKY-DB", 1): {
        "record_class": RecordClass.FAULT_NOT_TRIGGERED.value,
        "fault_activation_status": TriState.UNKNOWN.value,
        "fault_effect_status": TriState.NOT_TRIGGERED.value,
        "confidence": "PROBABILISTIC_NO_EFFECT_EVIDENCE",
    },
    ("UI-DELAYED", 2): {
        "record_class": RecordClass.NORMAL_POSITIVE_CONTROL.value,
        "fault_activation_status": TriState.CONFIRMED.value,
        "fault_effect_status": TriState.INFERRED_FROM_VERDICT.value,
        "confidence": "CONTRACT_INFERRED",
    },
    ("API-EDIT-500", 2): {
        "record_class": RecordClass.NORMAL_POSITIVE_CONTROL.value,
        "fault_activation_status": TriState.CONFIRMED.value,
        "fault_effect_status": TriState.INFERRED_FROM_VERDICT.value,
        "confidence": "CONTRACT_INFERRED",
    },
    ("API-EDIT-503", 2): {
        "record_class": RecordClass.NORMAL_POSITIVE_CONTROL.value,
        "fault_activation_status": TriState.CONFIRMED.value,
        "fault_effect_status": TriState.INFERRED_FROM_VERDICT.value,
        "confidence": "CONTRACT_INFERRED",
    },
    ("TIME-STRONG", 2): {
        "record_class": RecordClass.NORMAL_POSITIVE_CONTROL.value,
        "fault_activation_status": TriState.CONFIRMED.value,
        "fault_effect_status": TriState.INFERRED_FROM_VERDICT.value,
        "confidence": "CONTRACT_INFERRED",
    },
}


def _diagnosis_http_status(error: Any) -> int | None:
    """从异常文本中提取诊断 HTTP 状态；不据此单独判定正向控制。"""
    text = str(error or "")
    match = re.search(r"->\s*(\d{3})\b", text)
    if match:
        return int(match.group(1))
    match = re.search(r"\b(4\d{2}|5\d{2})\b", text)
    return int(match.group(1)) if match else None


def _error_parts(error: Any) -> tuple[str | None, str | None]:
    text = str(error or "")
    if not text:
        return None, None
    head, sep, body = text.partition(": ")
    if not sep:
        return "ExecutionError", text
    candidate = head.strip()
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", candidate):
        return candidate, body[:1000]
    return "ExecutionError", text[:1000]


def contract_of(sample: dict[str, Any]) -> ScenarioContract | None:
    """返回场景契约；优先使用记录自带的显式契约字段。"""
    scenario = str(sample.get("scenario") or "")
    base = V5_SCENARIO_CONTRACTS.get(scenario)
    explicit_kind = sample.get("control_kind")
    explicit_outcome = sample.get("expected_run_outcome")
    if base is None and not explicit_kind and not explicit_outcome:
        return None
    if base is None:
        return ScenarioContract(
            scenario=scenario,
            testcase=str(sample.get("testcase") or ""),
            fault=str(sample.get("fault") or ""),
            gt_mechanism=str(sample.get("gt_mechanism") or ""),
            expected_run_outcome=ExpectedRunOutcome(
                str(explicit_outcome or ExpectedRunOutcome.UNKNOWN.value)
            ),
            control_kind=ControlKind(str(explicit_kind or ControlKind.UNKNOWN.value)),
            fault_trigger_condition=str(sample.get("fault_trigger_condition") or "未声明"),
        )
    if not explicit_kind and not explicit_outcome:
        return base
    return ScenarioContract(
        scenario=base.scenario,
        testcase=str(sample.get("testcase") or base.testcase),
        fault=str(sample.get("fault") or base.fault),
        gt_mechanism=str(sample.get("gt_mechanism") or base.gt_mechanism),
        expected_run_outcome=ExpectedRunOutcome(
            str(explicit_outcome or base.expected_run_outcome.value)
        ),
        control_kind=ControlKind(str(explicit_kind or base.control_kind.value)),
        fault_trigger_condition=str(
            sample.get("fault_trigger_condition") or base.fault_trigger_condition
        ),
        deterministic_activation=bool(
            sample.get("deterministic_activation", base.deterministic_activation)
        ),
    )


def classify_record(sample: dict[str, Any]) -> dict[str, Any]:
    """把任意执行记录归类为五类之一，并保留完整上下文。

    分类优先级：
    1. 显式 `record_class` / `positive_control`（未来结构化字段）；
    2. 明确声明 expected=PASSED 的场景契约；
    3. 显式故障触发证据（`fault_activation_status` / `fault_effect_status`）；
    4. 历史归档的 412 + Run PASSED 启发式（仅用于未知场景的兼容，不提升置信度）；
    5. 以上都不满足 → UNKNOWN_FAULT_ACTIVATION，不得计入正向控制。
    """
    error_type, error_message = _error_parts(sample.get("error"))
    http_status = _diagnosis_http_status(sample.get("error"))
    contract = contract_of(sample)
    verdict = str(sample.get("verdict") or "").upper()
    expected = contract.expected_run_outcome if contract else ExpectedRunOutcome.UNKNOWN
    kind = contract.control_kind if contract else ControlKind.UNKNOWN
    has_error = bool(sample.get("error"))
    is_412_run_passed = bool(
        _POSITIVE_CONTROL_ERROR_RE.search(str(sample.get("error") or ""))
        and _RUN_PASSED_ERROR_RE.search(str(sample.get("error") or ""))
    )

    activation = str(sample.get("fault_activation_status") or "").upper() or None
    effect = str(sample.get("fault_effect_status") or "").upper() or None
    if activation in {"ACTIVATED", "ACTIVE", "TRUE", "YES"}:
        activation = TriState.CONFIRMED.value
    if effect in {"TRIGGERED", "TRUE", "YES", "OBSERVED"}:
        effect = TriState.CONFIRMED.value
    confidence = "STRUCTURED_EVIDENCE" if activation or effect else "UNKNOWN"
    historical_key = (str(sample.get("scenario") or ""), int(sample.get("round") or 0))
    historical = HISTORICAL_ERROR_CLASSIFICATION.get(historical_key, {}) if has_error else {}

    if historical:
        record_class = RecordClass(str(historical["record_class"]))
        activation = activation or str(historical["fault_activation_status"])
        effect = effect or str(historical["fault_effect_status"])
        confidence = str(historical["confidence"])
    elif sample.get("record_class"):
        record_class = RecordClass(str(sample["record_class"]))
    elif sample.get("positive_control") is True and verdict in {"FAILED", "NOT_EVALUATED"}:
        record_class = RecordClass.EXECUTION_EXCEPTION
        confidence = "CONTRACT_CONTRADICTION_POSITIVE_CONTROL_FAILED"
    elif sample.get("positive_control") is True:
        record_class = RecordClass.NORMAL_POSITIVE_CONTROL
        confidence = "STRUCTURED_POSITIVE_CONTROL_FLAG"
    elif expected is ExpectedRunOutcome.PASSED and not has_error:
        record_class = RecordClass.NORMAL_POSITIVE_CONTROL
        confidence = "SCENARIO_CONTRACT"
    elif expected is ExpectedRunOutcome.PASSED and has_error and is_412_run_passed:
        # 契约明确预期 PASSED，诊断 412 是正确跳过，不是异常。
        record_class = RecordClass.NORMAL_POSITIVE_CONTROL
        activation = activation or (
            TriState.CONFIRMED.value
            if kind is ControlKind.POSITIVE_CONTROL_TOLERANT_FAULT
            else TriState.UNKNOWN.value
        )
        effect = effect or TriState.INFERRED_FROM_VERDICT.value
        confidence = "SCENARIO_CONTRACT"
    elif kind is ControlKind.FLAKY_NEGATIVE_FIXTURE and verdict in {"PASSED", ""} and has_error:
        record_class = RecordClass.FAULT_NOT_TRIGGERED
        activation = activation or TriState.UNKNOWN.value
        effect = effect or TriState.NOT_TRIGGERED.value
        confidence = "SCENARIO_CONTRACT_NO_EFFECT_EVIDENCE"
    elif has_error and effect == TriState.NOT_TRIGGERED.value:
        record_class = RecordClass.FAULT_NOT_TRIGGERED
    elif (
        expected in {ExpectedRunOutcome.FAILED, ExpectedRunOutcome.NOT_EVALUATED}
        and verdict in {"FAILED", "NOT_EVALUATED"}
        and not has_error
    ):
        # Run 失败与确定性故障的预期效果一致，作为「故障生效」的推断证据；
        # 历史归档没有 fault_state 快照，证据级别只能是 INFERRED_FROM_VERDICT。
        record_class = RecordClass.EXPECTED_FAILURE_CONFIRMED
        activation = activation or (
            TriState.CONFIRMED.value
            if contract and contract.deterministic_activation
            else TriState.INFERRED_FROM_VERDICT.value
        )
        effect = effect or TriState.INFERRED_FROM_VERDICT.value
        confidence = "VERDICT_INFERRED_NO_FAULT_SNAPSHOT"
    elif (
        expected is ExpectedRunOutcome.FAILED
        and verdict == "PASSED"
        and (activation == TriState.CONFIRMED.value or effect == TriState.CONFIRMED.value)
    ):
        record_class = RecordClass.FALSE_PASS
    elif expected is ExpectedRunOutcome.FAILED and verdict == "PASSED" and not contract:
        # 兼容 R1.2 离线测试：无场景契约但有 GT 的旧记录沿用 Evaluator 口径。
        record_class = RecordClass.FALSE_PASS
        confidence = "LEGACY_GT_VERDICT"
    elif has_error and not is_412_run_passed:
        record_class = RecordClass.EXECUTION_EXCEPTION
    elif has_error and is_412_run_passed and contract is None:
        # R1.2 兼容：未知场景只有 412 + run PASSED 时保留旧分类，但标低置信度。
        record_class = RecordClass.NORMAL_POSITIVE_CONTROL
        confidence = "LEGACY_412_HEURISTIC_NOT_CONFIRMED"
    else:
        record_class = RecordClass.UNKNOWN_FAULT_ACTIVATION

    return {
        "scenario": sample.get("scenario"),
        "testcase": sample.get("testcase") or (contract.testcase if contract else None),
        "fault": sample.get("fault") or (contract.fault if contract else None),
        # GT 缺失不能被场景契约"补全"：缺失就是缺失，必须排除出 False Pass 分母。
        "gt_mechanism": sample.get("gt_mechanism") or None,
        "round": sample.get("round"),
        "run_id": sample.get("run_id"),
        "actual_verdict": verdict or None,
        "expected_run_outcome": expected.value,
        "control_kind": kind.value,
        "fault_trigger_condition": (
            sample.get("fault_trigger_condition")
            or (contract.fault_trigger_condition if contract else None)
        ),
        "record_class": record_class.value,
        "fault_activation_status": activation or TriState.UNKNOWN.value,
        "fault_effect_status": effect
        or (
            TriState.INFERRED_FROM_VERDICT.value
            if record_class is RecordClass.NORMAL_POSITIVE_CONTROL
            else TriState.UNKNOWN.value
        ),
        "diagnosis_status": (
            "ERROR" if has_error else str(sample.get("diagnosis_outcome") or "NOT_ATTEMPTED")
        ),
        "diagnosis_http_status": http_status,
        "error_type": error_type,
        "error_message": error_message,
        "classification_confidence": confidence,
        "contract_source": "SCENARIO_TABLE" if contract else "NONE",
    }


def is_confirmed_failing_sample(classified: dict[str, Any]) -> bool:
    """是否可纳入「预期失败且故障确实生效」的诊断样本/False Pass 口径。"""
    return (
        classified["expected_run_outcome"]
        in {ExpectedRunOutcome.FAILED.value, ExpectedRunOutcome.NOT_EVALUATED.value}
        and bool(classified["gt_mechanism"])
        and classified["fault_effect_status"]
        in {TriState.CONFIRMED.value, TriState.INFERRED_FROM_VERDICT.value}
        and classified["record_class"]
        not in {
            RecordClass.FAULT_NOT_TRIGGERED.value,
            RecordClass.EXECUTION_EXCEPTION.value,
            RecordClass.NORMAL_POSITIVE_CONTROL.value,
        }
    )


def validate_contracts(scenarios: list[dict[str, Any]]) -> list[str]:
    """校验 V5_SCENARIOS 是否完整声明契约；返回问题列表（空列表为通过）。"""
    problems: list[str] = []
    required = {
        "scenario",
        "testcase",
        "fault",
        "gt_mechanism",
        "expected_run_outcome",
        "control_kind",
        "fault_trigger_condition",
    }
    seen: set[str] = set()
    for item in scenarios:
        name = str(item.get("scenario") or "<unnamed>")
        if name in seen:
            problems.append(f"DUPLICATE_SCENARIO: {name}")
        seen.add(name)
        missing = sorted(required - set(item))
        if missing:
            problems.append(f"MISSING_CONTRACT_FIELDS: {name}: {','.join(missing)}")
        contract = V5_SCENARIO_CONTRACTS.get(name)
        if not contract:
            problems.append(f"NO_SCENARIO_CONTRACT: {name}")
            continue
        for field in ("testcase", "fault", "gt_mechanism"):
            if item.get(field) != getattr(contract, field):
                problems.append(f"CONTRACT_MISMATCH: {name}.{field}")
        if item.get("expected_run_outcome") != contract.expected_run_outcome.value:
            problems.append(f"CONTRACT_MISMATCH: {name}.expected_run_outcome")
        if item.get("control_kind") != contract.control_kind.value:
            problems.append(f"CONTRACT_MISMATCH: {name}.control_kind")
    return problems


def scenario_from_contract(name: str) -> dict[str, Any]:
    """由契约生成 V5_SCENARIOS 条目，避免场景定义与统计口径失配。"""
    contract = V5_SCENARIO_CONTRACTS[name]
    data = contract.to_dict()
    return {
        "scenario": data["scenario"],
        "testcase": data["testcase"],
        "fault": data["fault"],
        "gt_mechanism": data["gt_mechanism"],
        "expected_run_outcome": data["expected_run_outcome"],
        "control_kind": data["control_kind"],
        "fault_trigger_condition": data["fault_trigger_condition"],
    }


def dump_contracts() -> str:
    """输出可归档的契约 JSON（供 Errata 与审计引用）。"""
    return (
        json.dumps(
            {name: c.to_dict() for name, c in sorted(V5_SCENARIO_CONTRACTS.items())},
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )
