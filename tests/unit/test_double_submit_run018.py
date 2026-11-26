"""RUN-018 WP6：TF-TIME-003 双提交竞态独立观测器测试。

覆盖：
- 观测器分类逻辑（模拟不同创建数量）
- 网络证据记录
- 清理调用
- 非 TF-TIME-003 场景不适用
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "benchmark"))

from traceguard_benchmark.double_submit import (  # noqa: E402
    DoubleSubmitObservation,
    DoubleSubmitObserver,
)
from traceguard_benchmark.fault_effect import EffectStatus  # noqa: E402


class TestDoubleSubmitClassification:
    """双提交观测的分类逻辑测试。"""

    def _make_obs(self, **kwargs) -> DoubleSubmitObservation:
        defaults = {
            "baseline_ok": True,
            "fault_activated": True,
            "projects_created": 1,
        }
        defaults.update(kwargs)
        return DoubleSubmitObservation(**defaults)

    def test_one_project_two_ok_submits(self):
        """两个请求都成功但只创建 1 个 → CONFIRMED / DOUBLE_SUBMIT_DEDUPED。"""
        obs = self._make_obs(projects_created=1)
        obs.network_evidence = [
            {"step": "double_submit_1", "status": 200, "elapsed_ms": 100, "response": "{}"},
            {"step": "double_submit_2", "status": 200, "elapsed_ms": 120, "response": "{}"},
        ]
        observer = DoubleSubmitObserver.__new__(DoubleSubmitObserver)
        observer._classify(obs)
        assert obs.status == EffectStatus.CONFIRMED.value
        assert obs.classification == "DOUBLE_SUBMIT_DEDUPED"

    def test_one_project_one_ok_one_fail(self):
        """一个成功一个失败 → CONFIRMED / DOUBLE_SUBMIT_ONE_FAILED。"""
        obs = self._make_obs(projects_created=1)
        obs.network_evidence = [
            {"step": "double_submit_1", "status": 200, "elapsed_ms": 100, "response": "{}"},
            {"step": "double_submit_2", "status": 500, "elapsed_ms": 120, "response": "{}"},
        ]
        observer = DoubleSubmitObserver.__new__(DoubleSubmitObserver)
        observer._classify(obs)
        assert obs.status == EffectStatus.CONFIRMED.value
        assert obs.classification == "DOUBLE_SUBMIT_ONE_FAILED"

    def test_two_projects_duplicate(self):
        """创建了 2 个 → NOT_TRIGGERED / DUPLICATE_CREATED（竞态未去重）。"""
        obs = self._make_obs(projects_created=2)
        observer = DoubleSubmitObserver.__new__(DoubleSubmitObserver)
        observer._classify(obs)
        assert obs.status == EffectStatus.NOT_TRIGGERED.value
        assert obs.classification == "DUPLICATE_CREATED"
        assert obs.duplicate_detected is True

    def test_zero_projects(self):
        """创建 0 个 → UNKNOWN / NO_DATA。"""
        obs = self._make_obs(projects_created=0)
        observer = DoubleSubmitObserver.__new__(DoubleSubmitObserver)
        observer._classify(obs)
        assert obs.status == EffectStatus.UNKNOWN.value
        assert obs.classification == "NO_DATA"

    def test_baseline_failed(self):
        """基线失败 → UNKNOWN / BASELINE_FAILED。"""
        obs = self._make_obs(baseline_ok=False, projects_created=1)
        observer = DoubleSubmitObserver.__new__(DoubleSubmitObserver)
        observer._classify(obs)
        assert obs.status == EffectStatus.UNKNOWN.value
        assert obs.classification == "BASELINE_FAILED"

    def test_three_projects_unexpected(self):
        """创建 3 个（含基线）→ UNKNOWN / UNEXPECTED_COUNT。"""
        obs = self._make_obs(projects_created=3)
        observer = DoubleSubmitObserver.__new__(DoubleSubmitObserver)
        observer._classify(obs)
        assert obs.status == EffectStatus.UNKNOWN.value
        assert obs.classification == "UNEXPECTED_COUNT"

    def test_all_submits_failed(self):
        """双提交都失败但创建了 1 个（基线）→ UNKNOWN / SUBMIT_FAILED。"""
        obs = self._make_obs(projects_created=1)
        obs.network_evidence = [
            {"step": "double_submit_1", "status": 0, "elapsed_ms": 100, "response": "ERROR"},
            {"step": "double_submit_2", "status": 0, "elapsed_ms": 120, "response": "ERROR"},
        ]
        observer = DoubleSubmitObserver.__new__(DoubleSubmitObserver)
        observer._classify(obs)
        assert obs.status == EffectStatus.UNKNOWN.value
        assert obs.classification == "SUBMIT_FAILED"


class TestDoubleSubmitNotApplicable:
    """非 TF-TIME-003 场景。"""

    def test_non_double_submit_fault(self):
        """非双提交场景返回 NOT_APPLICABLE。"""
        observer = DoubleSubmitObserver.__new__(DoubleSubmitObserver)
        result = observer.observe(["TF-API-001"])
        assert result["fault_effect_status"] == EffectStatus.UNKNOWN.value
        assert result["fault_effect_classification"] == "NOT_APPLICABLE"


class TestObservationData:
    """观测数据结构。"""

    def test_to_dict(self):
        obs = DoubleSubmitObservation(
            status=EffectStatus.CONFIRMED.value,
            classification="DOUBLE_SUBMIT_DEDUPED",
            projects_created=1,
        )
        d = obs.to_dict()
        assert d["status"] == "CONFIRMED"
        assert d["projects_created"] == 1
        assert "network_evidence" in d
        assert "state_readback" in d
        assert "cleanup_result" in d
