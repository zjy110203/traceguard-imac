"""阶段7 Diagnosis v1 测试：多假设、确认规则、Flaky、Disposition 分流。"""

from __future__ import annotations

from traceguard_evidence.diagnosis.engine import (
    DiagnosisEngine,
    Disposition,
    EvidenceRef,
    FailureDomain,
    FailureMechanism,
    Hypothesis,
    HypothesisStatus,
)


def _h(
    hid: str,
    domain: FailureDomain,
    mech: FailureMechanism,
    supporting: list[EvidenceRef] | None = None,
    contradicting: list[EvidenceRef] | None = None,
    suggested_by: str = "rule",
    status: HypothesisStatus = HypothesisStatus.CANDIDATE,
) -> Hypothesis:  # type: ignore[no-untyped-def]
    return Hypothesis(
        hypothesis_id=hid, description=hid, failure_domain=domain,
        failure_mechanism=mech, supporting=supporting or [],
        contradicting=contradicting or [], suggested_by=suggested_by, status=status,
    )


def test_confirmed_requires_authoritative_evidence() -> None:
    eng = DiagnosisEngine()
    # 只有 UNTRUSTED 支持证据 → 不能 CONFIRMED
    h = _h("h1", FailureDomain.PRODUCT, FailureMechanism.PERSISTENCE,
           supporting=[EvidenceRef("e1", "UNTRUSTED", True)])
    d = eng.diagnose("未持久化", [h])
    assert h.status != HypothesisStatus.CONFIRMED
    assert d.outcome != "CONFIRMED" or d.outcome.value != "CONFIRMED"


def test_confirmed_with_trusted_authoritative_evidence() -> None:
    eng = DiagnosisEngine()
    h = _h("h1", FailureDomain.PRODUCT, FailureMechanism.PERSISTENCE,
           supporting=[EvidenceRef("e1", "TRUSTED", True)])
    d = eng.diagnose("未持久化", [h])
    assert h.status == HypothesisStatus.CONFIRMED
    assert d.root_causes == ["h1"]
    assert d.outcome.value == "CONFIRMED"


def test_llm_cannot_self_declare_confirmed() -> None:
    """LLM 提出的假设即便标了 CONFIRMED，也被强制降级。"""
    eng = DiagnosisEngine()
    h = _h("h1", FailureDomain.TEST_ASSET, FailureMechanism.LOCATOR,
           suggested_by="llm", status=HypothesisStatus.CONFIRMED)
    d = eng.diagnose("定位失败", [h])
    assert h.status == HypothesisStatus.CANDIDATE
    assert d.root_causes == []


def test_contradicting_evidence_prevents_confirmation() -> None:
    eng = DiagnosisEngine()
    h = _h("h1", FailureDomain.PRODUCT, FailureMechanism.API,
           supporting=[EvidenceRef("e1", "TRUSTED", True)],
           contradicting=[EvidenceRef("e2", "TRUSTED")])
    d = eng.diagnose("API 错误", [h])
    assert h.status == HypothesisStatus.CONTRADICTED
    assert "e2" not in [e.evidence_id for e in h.supporting]  # 反驳证据保留在 contradicting
    assert d.root_causes == []


def test_multiple_root_causes_preserved() -> None:
    eng = DiagnosisEngine()
    h1 = _h("h1", FailureDomain.PRODUCT, FailureMechanism.PERSISTENCE,
            supporting=[EvidenceRef("e1", "TRUSTED", True)])
    h2 = _h("h2", FailureDomain.ENVIRONMENT, FailureMechanism.NETWORK,
            supporting=[EvidenceRef("e2", "TRUSTED", True)])
    d = eng.diagnose("多故障", [h1, h2])
    assert set(d.root_causes) == {"h1", "h2"}  # 不强制压缩为一个
    assert d.disposition == Disposition.PRODUCT_DEFECT  # 产品域优先


def test_supported_but_not_confirmed_is_probable() -> None:
    eng = DiagnosisEngine()
    h = _h("h1", FailureDomain.TEST_ASSET, FailureMechanism.LOCATOR,
           supporting=[EvidenceRef("e1", "UNTRUSTED")])
    d = eng.diagnose("定位失败", [h])
    assert h.status == HypothesisStatus.SUPPORTED
    assert d.outcome.value == "PROBABLE"
    assert d.root_causes == []  # 未确认不成根因


def test_inconclusive_when_no_evidence() -> None:
    eng = DiagnosisEngine()
    h = _h("h1", FailureDomain.UNKNOWN, FailureMechanism.UNKNOWN)
    d = eng.diagnose("现象", [h])
    assert d.outcome.value == "INCONCLUSIVE"
    assert d.disposition == Disposition.NEEDS_MORE_EVIDENCE


def test_flaky_is_characteristic_not_root_cause() -> None:
    eng = DiagnosisEngine()
    h = _h("h1", FailureDomain.TEST_EXECUTION, FailureMechanism.TIMING,
           supporting=[EvidenceRef("e1", "TRUSTED", True)])
    h.flaky_characteristic = True  # 不稳定特征
    d = eng.diagnose("偶发失败", [h])
    # Flaky 只是特征标记，不作为独立根因类别
    assert h.flaky_characteristic is True
    assert d.root_causes == ["h1"]  # 根因是 TIMING 机制，不是 "flaky"


def test_eight_dimensions_independent() -> None:
    h = _h("h1", FailureDomain.PRODUCT, FailureMechanism.UI_RENDERING)
    h.contributing_factors = ["并发用户多"]
    d = DiagnosisEngine().diagnose("列表缺失", [h])
    obj = d.to_dict()
    # 八个维度各自独立字段，不混成一个分类
    assert obj["symptom"] == "列表缺失"
    assert h.failure_domain.value == "PRODUCT"
    assert h.failure_mechanism.value == "UI_RENDERING"
    assert h.contributing_factors == ["并发用户多"]
    assert "outcome" in obj and "disposition" in obj


def test_disposition_routes_by_failure_domain() -> None:
    eng = DiagnosisEngine()
    cases = [
        (FailureDomain.PRODUCT, Disposition.PRODUCT_DEFECT),
        (FailureDomain.TEST_ASSET, Disposition.TEST_ASSET_ISSUE),
        (FailureDomain.ENVIRONMENT, Disposition.ENVIRONMENT_ISSUE),
        (FailureDomain.AGENT, Disposition.AGENT_ISSUE),
    ]
    for domain, expected in cases:
        h = _h("h1", domain, FailureMechanism.UNKNOWN,
               supporting=[EvidenceRef("e", "TRUSTED", True)])
        d = eng.diagnose("x", [h])
        assert d.disposition == expected, domain
