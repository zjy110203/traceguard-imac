"""Evaluation Engine + Baseline/Ablation Runner（阶段11）。

指标口径（全部明确统计单位、分子分母、排除条件）：
- INCONCLUSIVE 不得计为 PASSED；
- Selective Accuracy 必须与 Coverage 同时报告；
- Macro-F1：多标签按类别宏平均；
- 每个结果附带 numerator/denominator/note，禁止无口径数字。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Metric:
    name: str
    value: float
    numerator: int
    denominator: int
    note: str  # 统计口径说明
    excluded: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric": self.name,
            "value": round(self.value, 4),
            "numerator": self.numerator,
            "denominator": self.denominator,
            "excluded": self.excluded,
            "note": self.note,
        }


def safe_ratio(num: int, den: int) -> float:
    return num / den if den else 0.0


class DiagnosisEvaluator:
    """诊断评测：A~E baseline 对照。"""

    def __init__(self, ground_truth: dict[str, str]) -> None:
        """ground_truth: case_id -> primary_failure_class。评测侧持有，不暴露给 Agent。"""
        self._gt = ground_truth

    def macro_f1(self, predictions: dict[str, str]) -> Metric:
        """多类别宏平均 F1。"""
        classes = sorted(set(self._gt.values()) | set(predictions.values()))
        f1s: list[float] = []
        for c in classes:
            tp = sum(1 for k in self._gt if self._gt[k] == c and predictions.get(k) == c)
            fp = sum(1 for k in predictions if predictions[k] == c and self._gt.get(k) != c)
            fn = sum(1 for k in self._gt if self._gt[k] == c and predictions.get(k) != c)
            precision = safe_ratio(tp, tp + fp)
            recall = safe_ratio(tp, tp + fn)
            f1 = (
                safe_ratio(2 * precision * recall, precision + recall)
                if (precision + recall)
                else 0.0
            )
            f1s.append(f1)
        value = sum(f1s) / len(f1s) if f1s else 0.0
        return Metric(
            "macro_f1",
            value,
            len(f1s) and int(value * len(f1s)) or 0,
            len(f1s),
            "各类别F1宏平均；分母=类别数；缺失预测计为该类别FN",
        )

    def primary_cause_accuracy(self, predictions: dict[str, str]) -> Metric:
        correct = sum(1 for k in self._gt if predictions.get(k) == self._gt[k])
        return Metric(
            "primary_cause_accuracy",
            safe_ratio(correct, len(self._gt)),
            correct,
            len(self._gt),
            "主因分类正确数/总case数；排除无GT的case",
        )

    def false_pass_rate(self, verdicts: dict[str, str], gt_persisted: dict[str, bool]) -> Metric:
        """False Pass：GT 未持久化但 verdict=PASSED。INCONCLUSIVE 不算 PASSED。"""
        candidates = [(k, v) for k, v in verdicts.items() if k in gt_persisted]
        fp = sum(1 for k, v in candidates if v == "PASSED" and not gt_persisted[k])
        return Metric(
            "false_pass_rate",
            safe_ratio(fp, len(candidates)),
            fp,
            len(candidates),
            "GT未持久化却判PASSED的数量/有持久化GT的数量；INCONCLUSIVE不计PASSED",
        )

    def selective_accuracy(self, predictions: dict[str, str]) -> dict[str, Metric]:
        """选择性准确率：只统计有预测的 case，必须同时报告 Coverage。"""
        answered = {k: v for k, v in predictions.items() if v and v != "INCONCLUSIVE"}
        correct = sum(1 for k, v in answered.items() if self._gt.get(k) == v)
        acc = Metric(
            "selective_accuracy",
            safe_ratio(correct, len(answered)),
            correct,
            len(answered),
            "仅统计有明确预测的case；INCONCLUSIVE排除在分子分母外",
        )
        coverage = Metric(
            "coverage",
            safe_ratio(len(answered), len(self._gt)),
            len(answered),
            len(self._gt),
            "有明确预测的case数/总case数；与selective_accuracy同时报告",
        )
        return {"accuracy": acc, "coverage": coverage}


class TestGenerationEvaluator:
    """测试生成评测。"""

    @staticmethod
    def replay_stability(verdicts_by_case: dict[str, list[str]]) -> Metric:
        """回放稳定性：多次回放 verdict 全一致的 case 比例。"""
        stable = 0
        for verdicts in verdicts_by_case.values():
            if verdicts and len(set(verdicts)) == 1 and verdicts[0] == "PASSED":
                stable += 1
        return Metric(
            "replay_stability",
            safe_ratio(stable, len(verdicts_by_case)),
            stable,
            len(verdicts_by_case),
            "重复回放全部PASSED的case数/总case数；INCONCLUSIVE不算稳定通过",
        )

    @staticmethod
    def locator_validity(results: dict[str, bool]) -> Metric:
        valid = sum(1 for v in results.values() if v)
        return Metric(
            "locator_validity",
            safe_ratio(valid, len(results)),
            valid,
            len(results),
            "成功解析并定位的locator数/总locator数",
        )


class SelfHealingEvaluator:
    """自愈评测。"""

    @staticmethod
    def assertion_preservation(results: dict[str, bool]) -> Metric:
        preserved = sum(1 for v in results.values() if v)
        return Metric(
            "assertion_preservation_rate",
            safe_ratio(preserved, len(results)),
            preserved,
            len(results),
            "保持必需断言不变的修复数/总修复数",
        )

    @staticmethod
    def acceptance_rate(results: dict[str, str]) -> Metric:
        """接受率：修复通过审批且回归通过的比例。"""
        accepted = sum(1 for v in results.values() if v == "APPROVED_AND_REGRESSION_PASSED")
        return Metric(
            "acceptance_rate",
            safe_ratio(accepted, len(results)),
            accepted,
            len(results),
            "审批通过且回归通过的修复数/总修复提案数；无保护LLM不得晋升",
        )


@dataclass
class ExperimentReport:
    experiment_id: str
    baseline: str
    metrics: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "experiment_id": self.experiment_id,
            "baseline": self.baseline,
            "metrics": self.metrics,
            "metadata": self.metadata,
        }


class AblationRunner:
    """消融实验：移除指定能力，对比指标。"""

    ABLATIONS = [
        "no_evidence_graph",
        "no_active_diagnosis",
        "no_oracle_protection",
        "no_probe_budget",
        "no_assertion_preservation",
    ]

    def __init__(self, evaluator: DiagnosisEvaluator) -> None:
        self.evaluator = evaluator

    def run(self, predictions_by_variant: dict[str, dict[str, str]]) -> list[ExperimentReport]:
        reports = []
        for variant, preds in predictions_by_variant.items():
            if variant != "full" and variant not in self.ABLATIONS:
                continue
            reports.append(
                ExperimentReport(
                    experiment_id=f"ablation-{variant}",
                    baseline=variant,
                    metrics=[self.evaluator.primary_cause_accuracy(preds).to_dict()],
                    metadata={"variant": variant, "note": "消融变体；full为完整能力对照"},
                )
            )
        return reports
