"""RUN-011-R1 WP2：时间三态与 TF-DB-004 延迟复测回归（OPEN-RISK-02）。

决策记录：`.workbuddy-run/decisions.md` R1-01 ~ R1-05。
覆盖指令 §4.1 的四个场景（判定语义层，确定性探针信号）：
1. 正常场景：数据可见 → PERSISTENCE 被排除；
2. 真实延迟场景：Run 内查询 count=0，延迟后查询 count≥1 → 识别 TIMING 方向，
   PERSISTENCE 不被确认、不被事后证据排除；
3. 永久失败场景：两次查询均 count=0 → PERSISTENCE 保留候选（SUPPORTED），
   不因单查询升级 CONFIRMED；
4. 探针超时场景：TIMED_OUT 探针零证据、零假设变更。
另覆盖三态 resolve_alignment 与 UNKNOWN 权威门槛。
"""

from __future__ import annotations

import time as _time

from traceguard_evidence.diagnosis.engine import (
    DiagnosisEngine,
    EvidenceRef,
    FailureDomain,
    FailureMechanism,
    Hypothesis,
    HypothesisStatus,
)
from traceguard_evidence.graph.adjudication import EvidenceMeta, GraphAdjudicator
from traceguard_evidence.probe.feedback import (
    ALIGNED,
    POST_HOC,
    UNKNOWN,
    ProbeSignal,
    merge_signals,
    resolve_alignment,
)

RUN_ENDED = 1000.0


def _persist_hyp() -> dict:
    return {
        "hypothesis_id": "h_persist",
        "description": "业务状态未持久化",
        "failure_domain": "PRODUCT",
        "failure_mechanism": "PERSISTENCE",
        "supporting": [],
        "contradicting": [],
        "probe_feedback": [],
        "excluded_by": [],
    }


def _state_signal(
    exec_id: str,
    count: int,
    *,
    observed_at: float,
    run_scope: bool,
    status: str = "SUCCEEDED",
) -> ProbeSignal:
    return ProbeSignal(
        probe_id="http_state",
        execution_id=exec_id,
        status=status,
        evidence_ids=[f"evd_{exec_id}"] if status == "SUCCEEDED" else [],
        data={"response": {"ok": True, "count": count, "keys": ["k"] * count}},
        observed_at=observed_at,
        run_scope=run_scope,
    )


# ---------------------------------------------------------------- 三态对齐
def test_resolve_alignment_three_states() -> None:
    """ALIGNED（run 内）/ POST_HOC（Run 后）/ UNKNOWN（时间未知）三态互斥。"""
    in_run = ProbeSignal(
        probe_id="http_state", execution_id="e1", status="SUCCEEDED",
        evidence_ids=["x"], observed_at=_time.time(), run_scope=True,
    )
    assert resolve_alignment(in_run, RUN_ENDED)[0] == ALIGNED

    late = ProbeSignal(
        probe_id="http_state", execution_id="e2", status="SUCCEEDED",
        evidence_ids=["x"], observed_at=RUN_ENDED + 100, run_scope=False,
    )
    assert resolve_alignment(late, RUN_ENDED)[0] == POST_HOC

    # observed_at=0：时间未知 → UNKNOWN（不得再按 ALIGNED 处理）
    unknown = ProbeSignal(
        probe_id="http_state", execution_id="e3", status="SUCCEEDED",
        evidence_ids=["x"], observed_at=0.0, run_scope=False,
    )
    alignment, note = resolve_alignment(unknown, RUN_ENDED)
    assert alignment == UNKNOWN
    assert "unknown_timing" in note

    # run_ended_at 缺失 + 非 run 作用域：同样无法证明对齐 → UNKNOWN
    unknown2 = ProbeSignal(
        probe_id="http_state", execution_id="e4", status="SUCCEEDED",
        evidence_ids=["x"], observed_at=500.0, run_scope=False,
    )
    assert resolve_alignment(unknown2, None)[0] == UNKNOWN


def test_unknown_time_evidence_cannot_confirm_or_exclude() -> None:
    """UNKNOWN 不应成为 CONFIRMED 或 EXCLUDED 的权威依据（决策 R1-02）。"""
    hyps = [_persist_hyp()]
    sig = _state_signal("u1", 0, observed_at=0.0, run_scope=False)  # 时间未知 + count=0
    merged, results = merge_signals(hyps, [sig], {}, run_ended_at=RUN_ENDED)
    assert results[0].time_alignment == UNKNOWN
    persist = merged[0]
    assert persist["supporting"], "证据仍附着（可追溯）"
    for ref in persist["supporting"]:
        assert ref["confirming"] is False, "UNKNOWN 不得 directly_confirming"
        assert ref["trust"] == "TIME_UNKNOWN", "UNKNOWN 不持有 TRUSTED"
        assert ref["time_alignment"] == UNKNOWN
    assert persist["excluded_by"] == [], "UNKNOWN 不得排除假设"
    # 引擎层：不会被推到 CONFIRMED / EXCLUDED
    engine = DiagnosisEngine()
    h = Hypothesis(
        hypothesis_id="h_persist",
        description="x",
        failure_domain=FailureDomain.PRODUCT,
        failure_mechanism=FailureMechanism.PERSISTENCE,
        supporting=[EvidenceRef("evd_u1", "TIME_UNKNOWN", False)],
    )
    result = engine.diagnose("s", [h])
    assert h.status not in (HypothesisStatus.CONFIRMED, HypothesisStatus.EXCLUDED)
    assert result.root_causes == []


def test_adjudicator_unknown_captured_at_not_authoritative() -> None:
    """captured_at=0（未知）的证据不通过权威门槛（决策 R1-03）。"""
    meta = {
        "evd_unknown": EvidenceMeta(
            evidence_id="evd_unknown", source_ref="s1", captured_at=0.0, trust="TRUSTED"
        ),
    }
    adj = GraphAdjudicator(meta, run_started_at=900.0, run_ended_at=1100.0)
    hyps = [
        {
            "hypothesis_id": "h1",
            "supporting": [{"id": "evd_unknown", "trust": "TRUSTED", "confirming": True}],
            "contradicting": [{"id": "evd_unknown", "trust": "TRUSTED"}],
        }
    ]
    report = adj.adjudicate(hyps)
    v = report.verdicts[0]
    assert v.authority_confirmed is False
    assert v.authority_refuted is False
    assert any("captured_at 未知" in n for n in v.notes)


# ------------------------------------------------- TF-DB-004 延迟复测四场景
def test_retest_normal_data_visible_excludes_persistence() -> None:
    """场景1 正常：数据可见（run 内 ALIGNED）→ PERSISTENCE 被排除。"""
    hyps = [_persist_hyp()]
    sig = _state_signal("n1", 2, observed_at=900.0, run_scope=True)
    merged, _ = merge_signals(hyps, [sig], {}, run_ended_at=RUN_ENDED)
    persist = merged[0]
    assert persist["excluded_by"], "ALIGNED 且数据可见 → 允许排除 PERSISTENCE"


def test_retest_real_delay_identifies_timing_not_persistence() -> None:
    """场景2 真实延迟：Run 内 count=0 + 延迟后 count≥1 → 识别 TIMING 方向。

    PERSISTENCE 不被确认（单查询 count=0 非确权）、不被事后证据排除，
    TIMING 新假设由确定性规则生成（写入延迟/查询滞后）。
    """
    hyps = [_persist_hyp()]
    in_run = _state_signal("d1", 0, observed_at=950.0, run_scope=True)  # Run 内即时查询
    delayed = _state_signal("d2", 3, observed_at=RUN_ENDED + 30, run_scope=False)  # 延迟后复查
    merged, results = merge_signals(hyps, [in_run, delayed], {}, run_ended_at=RUN_ENDED)
    persist = next(h for h in merged if h["failure_mechanism"] == "PERSISTENCE")
    timing = [h for h in merged if h["failure_mechanism"] == "TIMING"]

    assert timing, "数据延迟出现 → 必须识别 TIMING 方向"
    assert timing[0]["suggested_by"] == "rule:probe-timing"
    assert persist["excluded_by"] == [], "事后（POST_HOC）证据不得排除 Run 时假设"
    # count=0 的 Run 内证据：支持但非确权（单查询不直接确认永久故障）
    for ref in persist["supporting"]:
        assert ref["confirming"] is False
    # 延迟后 count≥1 的反驳附着为 POST_HOC（留痕但无裁决权）
    for ref in persist["contradicting"]:
        assert ref["trust"] == "POST_HOC"
    engine = DiagnosisEngine()
    hs = [
        Hypothesis(
            hypothesis_id=h["hypothesis_id"],
            description=h.get("description", ""),
            failure_domain=FailureDomain(h.get("failure_domain", "UNKNOWN")),
            failure_mechanism=FailureMechanism(h["failure_mechanism"]),
            supporting=[
                EvidenceRef(r["id"], r["trust"], bool(r.get("confirming")))
                for r in h.get("supporting") or []
            ],
            contradicting=[
                EvidenceRef(r["id"], r["trust"], False) for r in h.get("contradicting") or []
            ],
        )
        for h in merged
    ]
    result = engine.diagnose("lag", hs)
    assert result.root_causes == [], "延迟场景不得确认 PERSISTENCE 根因"
    assert result.outcome.value in {"PROBABLE", "INCONCLUSIVE"}
    assert results[1].time_alignment == POST_HOC


def test_retest_permanent_failure_keeps_persistence_candidate() -> None:
    """场景3 永久失败：两次查询均 count=0 → PERSISTENCE 保留候选（SUPPORTED），
    不因单次（或两次）查询直接升级 CONFIRMED。"""
    hyps = [_persist_hyp()]
    in_run = _state_signal("p1", 0, observed_at=950.0, run_scope=True)
    late = _state_signal("p2", 0, observed_at=RUN_ENDED + 30, run_scope=False)
    merged, _ = merge_signals(hyps, [in_run, late], {}, run_ended_at=RUN_ENDED)
    persist = merged[0]
    assert persist["supporting"], "PERSISTENCE 保留支持证据（候选不丢）"
    assert not any(r.get("confirming") for r in persist["supporting"])
    engine = DiagnosisEngine()
    h = Hypothesis(
        hypothesis_id="h_persist",
        description="x",
        failure_domain=FailureDomain.PRODUCT,
        failure_mechanism=FailureMechanism.PERSISTENCE,
        supporting=[
            EvidenceRef(r["id"], r["trust"], bool(r.get("confirming")))
            for r in persist["supporting"]
        ],
    )
    result = engine.diagnose("perm", [h])
    assert h.status in (HypothesisStatus.SUPPORTED, HypothesisStatus.CANDIDATE)
    assert result.root_causes == [], "状态查询证据不得直接确认永久性持久化故障"


def test_retest_probe_timeout_produces_no_evidence() -> None:
    """场景4 探针超时：TIMED_OUT 零证据、零假设变更（不冒充目标系统故障）。"""
    hyps = [_persist_hyp()]
    sig = _state_signal("t1", 0, observed_at=0.0, run_scope=False, status="TIMED_OUT")
    merged, results = merge_signals(hyps, [sig], {}, run_ended_at=RUN_ENDED)
    assert results[0].updates == []
    assert results[0].skipped_reason == "PROBE_TIMED_OUT_NOT_EVIDENCE"
    assert merged[0]["supporting"] == [] and merged[0]["contradicting"] == []
