"""RUN-016 WP3：v2 样本的 False Pass 判定必须依赖独立故障效果证据。

规则（fail-closed）：
- 故障已激活 + 测试 PASSED + **独立观测确认业务受影响** → FALSE_PASS；
- 故障已激活 + 测试 PASSED + 无独立证据 → FALSE_PASS_UNVERIFIED（不计入 False Pass）；
- 无场景契约 / v1 历史样本 → 保持原语义，不重新解释历史数据。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from bench_scenario_contract import (  # noqa: E402
    RecordClass,
    classify_record,
    is_confirmed_failing_sample,
)

V2 = {"benchmark_schema_version": "benchmark-fault-v2"}


def _sample(**over) -> dict:
    base = {
        "scenario": "FALSE-PASS",
        "fault": "TF-DB-001",
        "gt_mechanism": "PERSISTENCE",
        "verdict": "PASSED",
        "fault_activation_status": "CONFIRMED",
        "fault_activation_source": "teamflow:/api/faults",
    }
    base.update(over)
    return base


def test_v2_confirmed_by_independent_probe_is_false_pass() -> None:
    sample = _sample(
        **V2,
        fault_effect_status="CONFIRMED",
        fault_effect_source="INDEPENDENT_TEAMFLOW_PROBE:persistence",
        fault_effect_evidence_refs=["tfp_abc"],
    )
    out = classify_record(sample)
    assert out["record_class"] == RecordClass.FALSE_PASS.value
    assert out["classification_confidence"] == "INDEPENDENT_EFFECT_CONFIRMED"


def test_v2_activation_only_is_unverified_not_false_pass() -> None:
    """只有激活、没有效果证据：不得判 False Pass（RUN-016 核心要求）。"""
    sample = _sample(**V2, fault_effect_status="UNKNOWN", fault_effect_source="NO_INDEPENDENT_PROBE")
    out = classify_record(sample)
    assert out["record_class"] == RecordClass.FALSE_PASS_UNVERIFIED.value
    assert out["fault_effect_status"] == "UNKNOWN"
    assert is_confirmed_failing_sample(out) is False


def test_v2_confirmed_without_evidence_is_downgraded() -> None:
    """声称 CONFIRMED 但来源是 verdict / 无证据 → 降级 UNKNOWN。"""
    sample = _sample(**V2, fault_effect_status="CONFIRMED", fault_effect_source="")
    out = classify_record(sample)
    assert out["fault_effect_status"] == "UNKNOWN"
    assert out["record_class"] == RecordClass.FALSE_PASS_UNVERIFIED.value
    assert out["classification_confidence"] == "EFFECT_WITHOUT_INDEPENDENT_EVIDENCE"


def test_v2_failed_run_does_not_infer_effect_from_verdict() -> None:
    sample = _sample(
        **V2,
        verdict="FAILED",
        fault_effect_status="UNKNOWN",
        fault_effect_source="NO_INDEPENDENT_PROBE",
    )
    out = classify_record(sample)
    assert out["record_class"] == RecordClass.EXPECTED_FAILURE_CONFIRMED.value
    assert out["fault_effect_status"] == "UNKNOWN"
    assert is_confirmed_failing_sample(out) is False


def test_v1_legacy_semantics_unchanged() -> None:
    """历史样本（无 schema 版本）保持原语义：INFERRED_FROM_VERDICT 仍可接受。"""
    sample = {
        "scenario": "API-500",
        "fault": "TF-API-001",
        "gt_mechanism": "API_CONTRACT",
        "verdict": "FAILED",
    }
    out = classify_record(sample)
    assert out["benchmark_schema_version"] == "benchmark-fault-v1"
    assert out["fault_effect_status"] == "INFERRED_FROM_VERDICT"
    assert out["record_class"] == RecordClass.EXPECTED_FAILURE_CONFIRMED.value
    assert is_confirmed_failing_sample(out) is True


def test_v2_positive_control_not_marked_effect_confirmed() -> None:
    sample = {
        "scenario": "UI-DELAYED",
        "fault": "TF-UI-004",
        "gt_mechanism": "UI_RENDERING",
        "verdict": "PASSED",
        **V2,
        "fault_activation_status": "NOT_TRIGGERED",
        "fault_effect_status": "NOT_TRIGGERED",
    }
    out = classify_record(sample)
    assert out["record_class"] == RecordClass.NORMAL_POSITIVE_CONTROL.value
    assert out["fault_effect_status"] == "NOT_TRIGGERED"
