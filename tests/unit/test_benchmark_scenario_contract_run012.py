"""RUN-012 WP0：场景契约、五分类与严格 False Pass 口径的离线验证。

覆盖十类必测情形；不重跑 23×2 场景，不修改任何历史原始归档。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from bench_scenario_contract import (  # noqa: E402
    HISTORICAL_ERROR_CLASSIFICATION,
    RecordClass,
    TriState,
    classify_record,
    validate_contracts,
)
from bench_v5_run011_r1 import (  # noqa: E402
    CONTRACT_PROBLEMS,
    V5_SCENARIOS,
    compute_statistics,
)


def _sample(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "scenario": "FALSE-PASS",
        "testcase": "tcase_tf_false_pass",
        "fault": "TF-DB-001",
        "gt_mechanism": "PERSISTENCE",
        "round": 7,  # 避开历史归档的 round 1/2 分类表
        "verdict": "FAILED",
        "fault_activation_status": TriState.CONFIRMED.value,
        "fault_effect_status": TriState.CONFIRMED.value,
        "diagnosis_outcome": "CONFIRMED",
    }
    base.update(overrides)
    return base


def _klass(sample: dict[str, Any]) -> str:
    return classify_record(sample)["record_class"]


def _stats(samples: list[dict[str, Any]]) -> dict[str, Any]:
    return compute_statistics([{"round": 1, "samples": samples}])


def test_01_normal_positive_control_passed() -> None:
    sample = _sample(
        scenario="UI-DELAYED",
        testcase="tcase_bench_ui004",
        fault="TF-UI-004",
        gt_mechanism="UI_RENDERING",
        verdict="PASSED",
        error="RuntimeError: POST .../diagnose -> 412: run PASSED：无失败症状可诊断",
    )
    c = classify_record(sample)
    assert c["record_class"] == RecordClass.NORMAL_POSITIVE_CONTROL.value
    assert c["diagnosis_http_status"] == 412
    assert c["expected_run_outcome"] == "PASSED"
    st = _stats([sample])
    assert st["false_pass_evaluation"]["numerator"] == 0
    assert st["false_pass_evaluation"]["denominator"] == 0


def test_02_deterministic_fault_triggered_run_failed() -> None:
    c = classify_record(_sample())
    assert c["record_class"] == RecordClass.EXPECTED_FAILURE_CONFIRMED.value
    st = _stats([_sample()])
    assert st["false_pass_evaluation"]["numerator"] == 0
    assert st["false_pass_evaluation"]["denominator"] == 1
    assert st["false_pass_evaluation"]["status"] == "VERIFIED"


def test_03_deterministic_fault_triggered_run_passed_is_false_pass() -> None:
    sample = _sample(verdict="PASSED")
    assert _klass(sample) == RecordClass.FALSE_PASS.value
    st = _stats([sample, _sample(scenario="UI-MISSING", fault="TF-UI-001")])
    assert st["false_pass_evaluation"]["numerator"] == 1
    assert st["false_pass_evaluation"]["denominator"] == 2
    assert st["false_pass_evaluation"]["rate"] == pytest.approx(0.5)


def test_04_flaky_fault_not_triggered() -> None:
    sample = _sample(
        scenario="FLAKY-DB",
        testcase="tcase_bench_flaky001",
        fault="TF-FLAKY-001",
        verdict="PASSED",
        fault_activation_status=TriState.UNKNOWN.value,
        fault_effect_status=TriState.NOT_TRIGGERED.value,
        error="RuntimeError: POST .../diagnose -> 412: run PASSED：无失败症状可诊断",
    )
    c = classify_record(sample)
    assert c["record_class"] == RecordClass.FAULT_NOT_TRIGGERED.value
    st = _stats([sample, _sample()])
    assert st["false_pass_evaluation"]["excluded_flaky_not_triggered"] == 1
    assert st["false_pass_evaluation"]["denominator"] == 1
    assert st["sample_size"] == 1  # Flaky 未触发不进诊断准确率分母


def test_05_flaky_fault_triggered_run_passed_is_false_pass() -> None:
    sample = _sample(
        scenario="FLAKY-API",
        testcase="tcase_bench_flaky002",
        fault="TF-FLAKY-002",
        gt_mechanism="API",
        verdict="PASSED",
        fault_effect_status=TriState.CONFIRMED.value,
    )
    assert _klass(sample) == RecordClass.FALSE_PASS.value
    st = _stats([sample])
    assert st["false_pass_evaluation"]["numerator"] == 1
    assert st["false_pass_evaluation"]["denominator"] == 1


def test_06_generic_execution_exception() -> None:
    sample = _sample(error="TimeoutError: 诊断接口超时", verdict=None)
    c = classify_record(sample)
    assert c["record_class"] == RecordClass.EXECUTION_EXCEPTION.value
    assert c["error_type"] == "TimeoutError"
    assert c["error_message"] == "诊断接口超时"
    st = _stats([sample])
    assert st["record_classification_counts"]["EXECUTION_EXCEPTION"] == 1


def test_07_diagnosis_412_does_not_exclude_expected_failure() -> None:
    sample = _sample(
        verdict="PASSED",
        error='RuntimeError: POST /api/v1/runs/run_x/diagnose -> 412: {"detail":'
        '{"code":"PRECONDITION_FAILED"}}',
    )
    c = classify_record(sample)
    assert c["record_class"] == RecordClass.FALSE_PASS.value
    assert c["diagnosis_http_status"] == 412
    st = _stats([sample])
    assert st["false_pass_evaluation"]["denominator"] == 1
    assert st["false_pass_evaluation"]["numerator"] == 1


def test_08_positive_control_flag_conflicting_with_failed() -> None:
    sample = _sample(positive_control=True, verdict="FAILED")
    c = classify_record(sample)
    assert c["record_class"] == RecordClass.EXECUTION_EXCEPTION.value
    assert c["classification_confidence"] == "CONTRACT_CONTRADICTION_POSITIVE_CONTROL_FAILED"
    assert _stats([sample])["false_pass_evaluation"]["denominator"] == 0


def test_09_missing_ground_truth() -> None:
    sample = _sample(gt_mechanism="", verdict="PASSED")
    assert _klass(sample) == RecordClass.FALSE_PASS.value  # 分类保留可疑事实
    st = _stats([sample, _sample()])
    assert st["false_pass_evaluation"]["denominator"] == 1  # 但不进统计分母


def test_10_missing_fault_activation_evidence() -> None:
    sample = _sample(verdict="PASSED", fault_activation_status=None, fault_effect_status=None)
    c = classify_record(sample)
    assert c["record_class"] == RecordClass.UNKNOWN_FAULT_ACTIVATION.value
    assert c["fault_activation_status"] == TriState.UNKNOWN.value
    st = _stats([sample])
    assert st["false_pass_evaluation"]["status"] == "NOT_MEASURED"
    assert st["false_pass_evaluation"]["denominator"] == 0
    assert st["unknown_fault_activation"] == 1


def test_historical_nine_errors_are_split_not_forced() -> None:
    assert len(HISTORICAL_ERROR_CLASSIFICATION) == 9
    archive = json.loads(
        (ROOT / "docs/assets/run011-r1/benchmark/v5-2rounds.json").read_text(encoding="utf-8")
    )
    samples = [s for r in archive["rounds"] for s in r["samples"] if s.get("error")]
    classes = [classify_record(s) for s in samples]
    positives = [
        c for c in classes if c["record_class"] == RecordClass.NORMAL_POSITIVE_CONTROL.value
    ]
    not_triggered = [
        c for c in classes if c["record_class"] == RecordClass.FAULT_NOT_TRIGGERED.value
    ]
    assert (len(positives), len(not_triggered)) == (8, 1)
    assert not_triggered[0]["scenario"] == "FLAKY-DB"
    assert not_triggered[0]["round"] == 1
    assert all(
        c["classification_confidence"] != "LEGACY_412_HEURISTIC_NOT_CONFIRMED" for c in classes
    )


def test_historical_false_pass_status_is_partial() -> None:
    archive = json.loads(
        (ROOT / "docs/assets/run011-r1/benchmark/v5-2rounds.json").read_text(encoding="utf-8")
    )
    st = compute_statistics(
        archive["rounds"], archive["probe_executions"], archive["probe_feedback_applied"]
    )
    fp = st["false_pass_evaluation"]
    assert (fp["numerator"], fp["denominator"]) == (0, 37)
    assert fp["evidence_level"] == "INFERRED_ONLY"
    assert fp["status"] == "PARTIAL"
    assert st["record_classification_counts"]["EXPECTED_FAILURE_CONFIRMED"] == 37
    assert st["contract_problems"] == []


def test_v5_scenarios_have_explicit_contracts() -> None:
    assert len(V5_SCENARIOS) == 23
    assert CONTRACT_PROBLEMS == []
    assert validate_contracts(V5_SCENARIOS) == []
    by_name = {s["scenario"]: s for s in V5_SCENARIOS}
    assert by_name["API-EDIT-500"]["expected_run_outcome"] == "PASSED"
    assert by_name["UI-DELAYED"]["control_kind"] == "POSITIVE_CONTROL_TOLERANT_FAULT"
    assert by_name["FLAKY-DB"]["expected_run_outcome"] == "FAILED"
    assert all(s["fault_trigger_condition"] for s in V5_SCENARIOS)


def test_complete_context_preserved_on_error() -> None:
    c = classify_record(
        {
            "scenario": "API-EDIT-500",
            "testcase": "tcase_bench_api003",
            "fault": "TF-API-003",
            "gt_mechanism": "API",
            "round": 3,
            "run_id": "run_x",
            "verdict": "PASSED",
            "error": "HTTPError: 500 on update",
            "diagnosis_status": "ERROR",
        }
    )
    for key in (
        "scenario",
        "testcase",
        "fault",
        "gt_mechanism",
        "round",
        "run_id",
        "actual_verdict",
        "expected_run_outcome",
        "fault_activation_status",
        "fault_effect_status",
        "diagnosis_status",
        "diagnosis_http_status",
        "error_type",
        "error_message",
    ):
        assert key in c, key
    assert c["expected_run_outcome"] == "PASSED"
