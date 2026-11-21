"""阶段11 UI-FaultBench 测试：数据隔离、指标口径、INCONCLUSIVE、消融。"""

from __future__ import annotations

import pytest
from traceguard_benchmark.dataset import (
    BenchmarkCase,
    DatasetManager,
    GroundTruth,
    GroundTruthManager,
)
from traceguard_benchmark.evaluation import (
    AblationRunner,
    DiagnosisEvaluator,
    SelfHealingEvaluator,
    TestGenerationEvaluator,
)


# ---------- Dataset / Ground Truth ----------
def _dataset() -> DatasetManager:
    dm = DatasetManager()
    dm.add_case(BenchmarkCase("c1", "TF-NORMAL-001", "NONE", "1.0.0", "seed-1", "trusted"))
    dm.add_case(BenchmarkCase("c2", "TF-DB-001", "TF-DB-001", "1.0.0", "seed-1", "agent_generated"))
    return dm


def test_trusted_and_agent_generated_isolated() -> None:
    dm = _dataset()
    check = dm.isolation_check()
    assert check["isolated"] is True
    assert check["trusted_count"] == 1 and check["agent_generated_count"] == 1


def test_dataset_freeze_immutable() -> None:
    dm = _dataset()
    version = dm.freeze("v1.0")
    assert "+" in version  # 带内容哈希
    with pytest.raises(ValueError, match="DATASET_FROZEN"):
        dm.add_case(BenchmarkCase("c3", "x", "x", "1.0.0", "s", "trusted"))


def test_ground_truth_requires_token() -> None:
    gtm = GroundTruthManager()
    gtm.put(GroundTruth("c1", "PRODUCT_DEFECT", "TF-DB-001"))
    assert gtm.get("c1", None) is None  # Agent 无令牌不可读
    assert gtm.get("c1", "wrong-token") is None
    rec = gtm.get("c1", gtm.evaluation_token)
    assert rec and rec.primary_failure_class == "PRODUCT_DEFECT"


def test_ground_truth_refs_no_answers() -> None:
    gtm = GroundTruthManager()
    gtm.put(GroundTruth("c1", "PRODUCT_DEFECT", "TF-DB-001"))
    refs = gtm.visible_refs()
    assert refs == ["c1"]
    assert "PRODUCT_DEFECT" not in str(refs)


# ---------- Diagnosis metrics ----------
def _eval() -> DiagnosisEvaluator:
    return DiagnosisEvaluator(
        {
            "c1": "PRODUCT_DEFECT",
            "c2": "TEST_ASSET",
            "c3": "ENVIRONMENT",
        }
    )


def test_macro_f1_has_numerator_denominator() -> None:
    m = _eval().macro_f1({"c1": "PRODUCT_DEFECT", "c2": "TEST_ASSET", "c3": "TEST_ASSET"})
    d = m.to_dict()
    assert "numerator" in d and "denominator" in d and d["denominator"] > 0
    assert d["note"]


def test_primary_cause_accuracy() -> None:
    m = _eval().primary_cause_accuracy({"c1": "PRODUCT_DEFECT", "c2": "wrong", "c3": "ENVIRONMENT"})
    assert m.numerator == 2 and m.denominator == 3
    assert abs(m.value - 2 / 3) < 1e-6


def test_false_pass_inconclusive_not_counted() -> None:
    ev = DiagnosisEvaluator({"c1": "X"})
    m = ev.false_pass_rate(
        verdicts={"c1": "INCONCLUSIVE", "c2": "PASSED", "c3": "PASSED"},
        gt_persisted={"c1": False, "c2": False, "c3": True},
    )
    # c2: GT未持久化却PASSED → false pass；c1 INCONCLUSIVE 不算 PASSED；c3 GT已持久化
    assert m.numerator == 1 and m.denominator == 3


def test_selective_accuracy_reports_coverage() -> None:
    ev = DiagnosisEvaluator({"c1": "A", "c2": "B", "c3": "C"})
    result = ev.selective_accuracy({"c1": "A", "c2": "INCONCLUSIVE", "c3": "wrong"})
    assert "accuracy" in result and "coverage" in result
    assert result["accuracy"].denominator == 2  # c1, c3（INCONCLUSIVE 排除）
    assert result["coverage"].denominator == 3


# ---------- Test Generation / Self-Healing ----------
def test_replay_stability_inconclusive_not_stable() -> None:
    m = TestGenerationEvaluator.replay_stability(
        {
            "c1": ["PASSED", "PASSED"],
            "c2": ["PASSED", "INCONCLUSIVE"],
        }
    )
    assert m.numerator == 1 and m.denominator == 2


def test_locator_validity() -> None:
    m = TestGenerationEvaluator.locator_validity({"l1": True, "l2": True, "l3": False})
    assert m.numerator == 2 and m.denominator == 3


def test_assertion_preservation_rate() -> None:
    m = SelfHealingEvaluator.assertion_preservation({"r1": True, "r2": False})
    assert m.value == 0.5


def test_acceptance_rate_excludes_unprotected() -> None:
    m = SelfHealingEvaluator.acceptance_rate(
        {
            "r1": "APPROVED_AND_REGRESSION_PASSED",
            "r2": "REJECTED",
            "r3": "UNPROTECTED_LLM",
        }
    )
    assert m.numerator == 1 and m.denominator == 3


# ---------- Ablation ----------
def test_ablation_runs_named_variants() -> None:
    ev = _eval()
    runner = AblationRunner(ev)
    reports = runner.run(
        {
            "full": {"c1": "PRODUCT_DEFECT", "c2": "TEST_ASSET", "c3": "ENVIRONMENT"},
            "no_evidence_graph": {"c1": "PRODUCT_DEFECT", "c2": "wrong", "c3": "ENVIRONMENT"},
            "invalid_variant": {"c1": "x"},  # 应被忽略
        }
    )
    assert len(reports) == 2
    ids = {r.experiment_id for r in reports}
    assert "ablation-full" in ids and "ablation-no_evidence_graph" in ids
    assert all(r.metrics for r in reports)
