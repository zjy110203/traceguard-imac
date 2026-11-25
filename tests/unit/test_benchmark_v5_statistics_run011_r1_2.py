"""RUN-011-R1.2 WP2：Benchmark v5 统计口径离线验证（不重跑 23×2 场景）。

使用原始归档 `v5-2rounds.json` 离线重算，验证：
- 46 执行记录 / 23 执行场景 / 37 诊断样本 / 19 失败场景 / 9 正向控制；
- 正向控制严格识别，普通异常不得误归类；
- False Pass 使用真实 Run Verdict 与 GT，与 F 臂预测机制无关；
- F/G 历史臂别名兼容；LLM / Probe Feedback / Time Alignment 统计不变；
- 两个原始归档 SHA-256 与修复前一致。
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from bench_v5_run011_r1 import compute_statistics, is_positive_control  # noqa: E402

ARCHIVE = ROOT / "docs/assets/run011-r1/benchmark/v5-2rounds.json"
PILOT_ARCHIVE = ROOT / "docs/assets/run011-r1/benchmark/pilot-3scenarios.json"


def _archive() -> dict[str, Any]:
    return json.loads(ARCHIVE.read_text(encoding="utf-8"))


def _stats() -> dict[str, Any]:
    doc = _archive()
    return compute_statistics(doc["rounds"], doc["probe_executions"], doc["probe_feedback_applied"])


def test_archives_sha256_unchanged() -> None:
    assert hashlib.sha256(ARCHIVE.read_bytes()).hexdigest() == (
        "d99bc2e8f0da92a061b9b7ce1941746421ac8e67e6c2d1ecbbc3080f273c3e44"
    )
    assert hashlib.sha256(PILOT_ARCHIVE.read_bytes()).hexdigest() == (
        "2e96e24dd15ae3b7126a78cae833f76072806435125e2abd94c8427859808f6b"
    )


def test_raw_archive_statistics_split_counts() -> None:
    st = _stats()
    assert st["execution_records"] == 46
    assert st["execution_scenarios"] == 23
    assert st["diagnosis_sample_size"] == 37
    assert st["sample_size"] == 37
    assert st["failure_scenarios"] == 19
    assert st["positive_control_records"] == 9
    assert st["unclassified_error_records"] == 0
    assert st["failure_scenario_coverage"] == pytest.approx(19 / 23, abs=1e-4)
    assert st["scenario_coverage"] == {
        "total_scenarios": 23,
        "produced_failure_samples": 19,
        "coverage": pytest.approx(19 / 23, abs=1e-4),
    }


def test_positive_controls_excluded_from_diagnosis_denominator() -> None:
    doc = _archive()
    samples = [s for r in doc["rounds"] for s in r["samples"]]
    errors = [s for s in samples if s.get("error")]
    assert len(errors) == 9
    assert all(is_positive_control(s) for s in errors)

    st = _stats()
    # 历史主因准确率分母仍是 37 条诊断样本，不因正向控制进入而虚增/虚减。
    assert st["primary_cause_accuracy"]["C_rule"] == pytest.approx(24 / 37, abs=1e-4)
    assert st["primary_cause_accuracy"]["F_probe_post_hoc_aware"] == pytest.approx(26 / 37, abs=1e-4)
    assert st["llm_advisor_statistics"]["sample_size"] == 37


def test_generic_errors_are_not_positive_controls() -> None:
    rounds = [
        {
            "samples": [
                {
                    "scenario": "A",
                    "gt_mechanism": "PERSISTENCE",
                    "verdict": "FAILED",
                },
                {
                    "scenario": "B",
                    "error": "RuntimeError: POST .../diagnose -> 412: oracle binding incomplete",
                },
                {
                    "scenario": "C",
                    "error": "TimeoutError: run PASSED but worker timed out before diagnosis",
                },
                {
                    "scenario": "D",
                    "error": "RuntimeError: POST .../diagnose -> 412: run PASSED：无失败症状可诊断",
                },
            ]
        }
    ]
    st = compute_statistics(rounds)
    assert st["execution_records"] == 4
    assert st["positive_control_records"] == 1
    assert st["unclassified_error_records"] == 2
    assert st["diagnosis_sample_size"] == 1
    assert st["failure_scenarios"] == 1
    assert st["failure_scenario_coverage"] == pytest.approx(1 / 4, abs=1e-4)


def test_false_pass_counts_gt_failure_but_verdict_passed() -> None:
    rounds = [
        {
            "samples": [
                {"scenario": "FP", "gt_mechanism": "PERSISTENCE", "verdict": "PASSED"},
                {"scenario": "OK", "gt_mechanism": "API", "verdict": "FAILED"},
                {"scenario": "NO_GT", "verdict": "PASSED"},
            ]
        }
    ]
    st = compute_statistics(rounds)
    assert st["false_pass_count"] == 1
    assert st["false_pass_denominator"] == 2
    assert st["false_pass_rate"] == pytest.approx(0.5)
    assert st["sample_size"] == 1  # 只有 FAILED/NOT_EVALUATED 进入诊断准确率


def test_normal_positive_control_passed_is_not_false_pass() -> None:
    rounds = [
        {
            "samples": [
                {
                    "scenario": "POS",
                    "gt_mechanism": "UI_RENDERING",
                    "error": "RuntimeError: POST .../diagnose -> 412: run PASSED：无失败症状可诊断",
                },
                # 未来运行的结构化字段优先，且不应被误算为诊断样本或 False Pass。
                {
                    "scenario": "POS2",
                    "gt_mechanism": "API",
                    "verdict": "PASSED",
                    "positive_control": True,
                },
                {"scenario": "FAIL", "gt_mechanism": "API", "verdict": "FAILED"},
            ]
        }
    ]
    st = compute_statistics(rounds)
    assert st["positive_control_records"] == 2
    assert st["diagnosis_sample_size"] == 1
    assert st["false_pass_count"] == 0
    assert st["false_pass_denominator"] == 1
    assert st["false_pass_rate"] == 0.0


def test_historical_false_pass_rate_is_zero() -> None:
    st = _stats()
    assert st["false_pass_count"] == 0
    assert st["false_pass_denominator"] == 37
    assert st["false_pass_rate"] == 0.0


def test_legacy_f_and_g_arm_aliases_still_supported() -> None:
    st = _stats()
    assert st["primary_cause_accuracy"]["F_probe_post_hoc_aware"] == pytest.approx(26 / 37, abs=1e-4)
    assert st["primary_cause_accuracy"]["G_probe_unknown_time"] == pytest.approx(26 / 37, abs=1e-4)

    alias_rounds = [
        {
            "samples": [
                {
                    "scenario": "X",
                    "verdict": "FAILED",
                    "gt_mechanism": "PERSISTENCE",
                    "arms": {
                        "F_probe_aligned": {"mechanism": "PERSISTENCE", "outcome": "CONFIRMED"},
                        "G_probe_unknown_time": {"mechanism": "PERSISTENCE", "outcome": "CONFIRMED"},
                    },
                }
            ]
        }
    ]
    aliased = compute_statistics(alias_rounds)
    assert aliased["primary_cause_accuracy"]["F_probe_post_hoc_aware"] == 1.0
    assert aliased["primary_cause_accuracy"]["G_probe_unknown_time"] == 1.0
    assert aliased["false_pass_rate"] == 0.0  # F 臂预测不参与 False Pass


def test_llm_probe_and_time_statistics_unchanged() -> None:
    st = _stats()
    assert st["llm_advisor_statistics"] == {
        "sample_size": 37,
        "success_calls": 21,
        "success_rate": pytest.approx(21 / 37, abs=1e-4),
        "invalid_json_calls": 16,
        "invalid_json_rate": pytest.approx(16 / 37, abs=1e-4),
        "fallback_calls": 16,
        "fallback_rate": pytest.approx(16 / 37, abs=1e-4),
        "candidate_count": 63,
        "success_subset_accuracy": pytest.approx(13 / 21, abs=1e-4),
        "note": "H 臂总体准确率混合了 LLM 成功与规则 Fallback，不能当作 LLM 自身准确率",
    }
    fb = st["probe_feedback_statistics"]
    assert (fb["probe_executions"], fb["feedback_processed"], fb["hypothesis_updates"]) == (54, 54, 25)
    assert (fb["SUPPORT"], fb["CONTRADICT"], fb["CONFIRM"], fb["EXCLUDE"]) == (15, 10, 0, 0)
    align = st["time_alignment_evidence"]
    assert (align["total"], align["ALIGNED"], align["POST_HOC"], align["UNKNOWN"]) == (108, 0, 54, 54)
    assert align["run_internal_aligned_probes_produced"] is False
    assert st["diagnosis_latency_ms"] == {"mean": 17.5, "max": 51.9, "count": 37}
    assert st["abstention_rate_F"] == pytest.approx(5 / 37, abs=1e-4)
    assert st["probe_execution_success_rate"] == 1.0
    assert st["probe_feedback_success_rate"] == 1.0
