"""RUN-011-R1 WP3：LLM/规则假设去重回归（OPEN-RISK-03，指令 §五）。

五个回归场景：
1. 相同机制重复候选 → 合并为一个（虚假独立支持消失）；
2. 不同机制相似描述 → 不合并（机制优先）；
3. 相同 Evidence 被多个假设引用 → 合并后同一 Evidence 只计一次；
4. 规则与 LLM 对同一机制给出不同（相似）描述 → 合并且保留全部来源；
5. 两次独立运行候选顺序不同 → 合并结果一致（顺序无关）。
另覆盖：合并保留全部真实证据引用、LLM 来源合并后不得 CONFIRMED、原始候选记录保留。
"""

from __future__ import annotations

from traceguard_evidence.diagnosis.dedup import _similarity, dedup_hypotheses
from traceguard_evidence.diagnosis.engine import (
    EvidenceRef,
    FailureDomain,
    FailureMechanism,
    Hypothesis,
    HypothesisStatus,
)


def _hyp(
    hid: str,
    mech: FailureMechanism,
    desc: str,
    *,
    suggested_by: str = "rule",
    evd: list[str] | None = None,
    confirming: bool = False,
) -> Hypothesis:
    return Hypothesis(
        hypothesis_id=hid,
        description=desc,
        failure_domain=FailureDomain.PRODUCT,
        failure_mechanism=mech,
        supporting=[EvidenceRef(e, "TRUSTED", confirming) for e in (evd or [])],
        suggested_by=suggested_by,
    )


def test_same_mechanism_duplicates_merge() -> None:
    """场景1：相同机制重复候选合并为一个，独立支持数量不再虚增。"""
    hyps = [
        _hyp("h1", FailureMechanism.PERSISTENCE, "业务数据未持久化，写入被回滚", evd=["e1"]),
        _hyp("h2", FailureMechanism.PERSISTENCE, "业务数据未持久化，写入被回滚了", evd=["e1"]),
        _hyp("h3", FailureMechanism.PERSISTENCE, "数据没有落库", evd=["e2"]),
    ]
    out, audit = dedup_hypotheses(hyps)
    persist = [h for h in out if h.failure_mechanism == FailureMechanism.PERSISTENCE]
    assert len(audit["merged_groups"]) == 1
    assert audit["output_count"] <= 2
    merged_group = audit["merged_groups"][0]
    assert len(merged_group["merged_hypothesis_ids"]) >= 2
    # 合并假设的证据引用是并集且按 evidence_id 去重
    big = max(persist, key=lambda h: len(h.supporting))
    ids = [e.evidence_id for e in big.supporting]
    assert len(ids) == len(set(ids)), "同一 Evidence 不得重复计为多个独立支持"
    assert set(ids) <= {"e1", "e2"}
    # 原始来源与候选记录保留
    assert len(merged_group["merged_from"]) >= 2
    assert all("hypothesis_id" in rec for rec in merged_group["merged_from"])


def test_different_mechanisms_never_merge() -> None:
    """场景2：不同机制即使描述相似也不合并。"""
    desc = "创建提交后数据未持久化，疑似写入路径异常"
    hyps = [
        _hyp("h1", FailureMechanism.PERSISTENCE, desc),
        _hyp("h2", FailureMechanism.TIMING, desc),
    ]
    out, audit = dedup_hypotheses(hyps)
    assert len(out) == 2
    assert audit["merged_groups"] == []
    assert {h.failure_mechanism for h in out} == {
        FailureMechanism.PERSISTENCE,
        FailureMechanism.TIMING,
    }


def test_shared_evidence_counted_once_after_merge() -> None:
    """场景3：相同 Evidence 被多个候选引用 → 合并后同一 Evidence 只计一次。"""
    hyps = [
        _hyp("h1", FailureMechanism.LOCATOR, "提交按钮定位器失效无法解析", evd=["e1", "e2"]),
        _hyp("h2", FailureMechanism.LOCATOR, "提交按钮定位器失效，元素无法解析", evd=["e2", "e3"]),
    ]
    out, _ = dedup_hypotheses(hyps)
    loc = [h for h in out if h.failure_mechanism == FailureMechanism.LOCATOR]
    assert len(loc) == 1
    ids = [e.evidence_id for e in loc[0].supporting]
    assert sorted(set(ids)) == ["e1", "e2", "e3"]
    assert len(ids) == len(set(ids)), "合并后同一 Evidence 不得重复计数"


def test_rule_and_llm_same_mechanism_merge_preserves_sources() -> None:
    """场景4：规则与 LLM 对同一机制给出相似描述 → 合并且保留全部来源。"""
    hyps = [
        _hyp("h_rule", FailureMechanism.NETWORK, "网络请求失败或超时（环境/链路问题）", suggested_by="rule"),
        _hyp(
            "h_llm",
            FailureMechanism.NETWORK,
            "网络请求失败或超时（环境/链路问题）",
            suggested_by="llm:deepseek-flash",
            evd=["e9"],
        ),
    ]
    out, audit = dedup_hypotheses(hyps)
    assert len(out) == 1
    merged = out[0]
    assert "rule" in merged.suggested_by and "llm:deepseek-flash" in merged.suggested_by
    assert merged.supporting, "合并保留真实证据引用"
    group = audit["merged_groups"][0]
    assert set(group["sources"]) == {"rule", "llm:deepseek-flash"}
    assert {rec["hypothesis_id"] for rec in group["merged_from"]} == {"h_rule", "h_llm"}


def test_llm_merged_candidate_cannot_be_confirmed() -> None:
    """LLM 来源合并后的候选不得 CONFIRMED（引擎层约束之外的第一层）。"""
    hyps = [
        _hyp(
            "h_llm",
            FailureMechanism.PERSISTENCE,
            "数据库事务回滚导致数据未保存",
            suggested_by="llm:deepseek-flash",
            evd=["e1"],
            confirming=True,
        ),
        _hyp(
            "h_rule",
            FailureMechanism.PERSISTENCE,
            "数据库事务回滚导致数据未保存",
            suggested_by="rule",
            evd=["e1"],
            confirming=True,
        ),
    ]
    # 手动置 CONFIRMED 模拟上游误升（引擎对 llm 前缀有兜底，这里测 dedup 层）
    for h in hyps:
        h.status = HypothesisStatus.CONFIRMED
    out, _ = dedup_hypotheses(hyps)
    assert len(out) == 1
    assert out[0].status != HypothesisStatus.CONFIRMED
    assert any("[dedup]" in f for f in out[0].contributing_factors)


def test_order_invariance_across_runs() -> None:
    """场景5：两次独立运行候选顺序不同 → 合并结果一致（结论不漂移）。"""
    a = [
        _hyp("h1", FailureMechanism.PERSISTENCE, "数据未落库", evd=["e1"], suggested_by="rule"),
        _hyp("h2", FailureMechanism.PERSISTENCE, "数据未落库了", evd=["e2"], suggested_by="llm:m1"),
        _hyp("h3", FailureMechanism.LOCATOR, "按钮找不到", evd=["e3"]),
    ]
    b = list(reversed(a))
    out_a, audit_a = dedup_hypotheses(a)
    out_b, audit_b = dedup_hypotheses(b)
    key_a = sorted(f"{h.failure_mechanism.value}|{h.hypothesis_id}" for h in out_a)
    key_b = sorted(f"{h.failure_mechanism.value}|{h.hypothesis_id}" for h in out_b)
    assert key_a == key_b
    evd_a = sorted(e.evidence_id for h in out_a for e in h.supporting)
    evd_b = sorted(e.evidence_id for h in out_b for e in h.supporting)
    assert evd_a == evd_b
    assert audit_a["output_count"] == audit_b["output_count"]
    assert len(audit_a["merged_groups"]) == len(audit_b["merged_groups"])


def test_similarity_metric_threshold() -> None:
    """相似度指标：近似描述高分、无关描述低分。"""
    assert _similarity("业务数据未持久化，写入被回滚", "业务数据未持久化，写入被回滚了") >= 0.72
    assert _similarity("业务数据未持久化", "页面渲染缺少成功横幅") < 0.5
