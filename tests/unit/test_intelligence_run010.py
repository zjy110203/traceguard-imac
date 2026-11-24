"""RUN-010 WP4：诊断内核增强——403 规则、Graph 裁决层、时间对齐、多假设弃权。

覆盖指令 §4.7 验收：
- HTTP 403 → AUTHORIZATION 假设（规则引擎确定性识别）；
- Evidence Graph 参与主因裁决（重复消重/时间不匹配/来源独立/冲突降级）；
- POST_HOC 证据不得作为权威（无法时间对齐 → INCONCLUSIVE 方向）；
- 探针结果真实回灌（回灌率可审计）；
- 多假设竞争与弃权（CONFIRMED/SUPPORTED/REFUTED/INCONCLUSIVE）；
- LLM 假设不得 CONFIRMED（能力层 ≠ 事实层）。
"""

from __future__ import annotations

import time as _time
from typing import Any

import pytest
from traceguard_api.runtime import Runtime
from traceguard_api.services.diagnosis_service import DiagnosisService, _parse_epoch
from traceguard_evidence.diagnosis.engine import (
    DiagnosisEngine,
    EvidenceRef,
    FailureDomain,
    FailureMechanism,
    Hypothesis,
    HypothesisStatus,
)
from traceguard_evidence.graph.adjudication import (
    EvidenceMeta,
    GraphAdjudicator,
    evidence_meta_from_rows,
)
from traceguard_evidence.probe.feedback import ProbeSignal, merge_signals
from traceguard_persistence import Database
from traceguard_persistence.models import RunRow

# ---------------------------------------------------------------------------
# 1. 规则补齐：HTTP 403 → AUTHORIZATION
# ---------------------------------------------------------------------------


def _report_with_http(status: int) -> dict[str, Any]:
    network = [{"url": "/api/admin", "status": status, "failure": ""}]
    outcomes = [
        {"assertion_id": "a_ui", "kind": "ui", "status": "FAILED", "reason": "banner missing"},
        {
            "assertion_id": "a_biz",
            "kind": "business_state",
            "status": "FAILED",
            "reason": "count mismatch",
        },
    ]
    return {
        "steps": [
            {
                "step_id": "s1",
                "action": "click",
                "status": "FAILED",
                "error": "",
                "observation": {"network": network, "http_errors": network},
            },
        ],
        "assertion_outcomes": outcomes,
    }


@pytest.fixture()
def rt(work_tmp, monkeypatch):  # type: ignore[no-untyped-def]
    import uuid

    base = work_tmp / f"intel-{uuid.uuid4().hex[:8]}"
    base.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("TG_ARTIFACT_DIR", str(base / "artifacts"))
    from traceguard_core.config import reset_settings_cache

    reset_settings_cache()
    db = Database(f"sqlite:///{base / 'intel.db'}")
    runtime = Runtime(db)
    yield runtime
    db.dispose()


def _insert_run(rt: Runtime, run_id: str, report: dict[str, Any]) -> None:
    with rt.db.session() as s:
        s.add(
            RunRow(
                run_id=run_id,
                task_id=f"task_{run_id}",
                workspace_id="default",
                testcase_version_id="tcase_intel@1",
                execution_status="COMPLETED",
                test_verdict="FAILED",
                report_json=report,
                ended_at=_time.time(),
            )
        )


def test_http_403_generates_authorization_hypothesis(rt: Runtime) -> None:
    _insert_run(rt, "run_authz", _report_with_http(403))
    # 附着该 step 的网络证据（403 响应的权威证据链）
    rt.evidence.add_raw(
        "evd_authz_1",
        source_type="network",
        source_ref="s1",
        workspace_id="default",
        content={"step_id": "s1", "url": "/api/admin", "status": 403},
        source_trust="TRUSTED",
        do_redact=False,
        captured_at=_time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime(_time.time() - 60)),
        run_id="run_authz",
    )
    diag = DiagnosisService(rt).diagnose("run_authz", use_llm_advisor=False)
    authz = [h for h in diag["hypotheses"] if h["failure_mechanism"] == "AUTHORIZATION"]
    assert authz, "HTTP 403 必须生成 AUTHORIZATION 假设"
    assert "403" in authz[0]["description"]
    assert authz[0]["failure_domain"] == "TEST_EXECUTION"
    # 403 是确定性授权信号 + TRUSTED confirming 证据 → CONFIRMED 主因
    assert authz[0]["status"] == "CONFIRMED"
    assert diag["root_causes"] == [authz[0]["hypothesis_id"]]
    assert diag["outcome"] == "CONFIRMED"
    # Graph 裁决层结论随诊断落库
    assert diag.get("graph_adjudication") is not None


def test_http_401_also_maps_to_authorization(rt: Runtime) -> None:
    _insert_run(rt, "run_authz401", _report_with_http(401))
    diag = DiagnosisService(rt).diagnose("run_authz401", use_llm_advisor=False)
    authz = [h for h in diag["hypotheses"] if h["failure_mechanism"] == "AUTHORIZATION"]
    assert authz and "401" in authz[0]["description"]


# ---------------------------------------------------------------------------
# 2. Graph 裁决层：重复消重 / 时间不匹配 / 冲突降级
# ---------------------------------------------------------------------------


def test_adjudicator_deduplicates_and_flags_time_mismatch() -> None:
    meta = {
        "evd_a": EvidenceMeta(
            evidence_id="evd_a", source_ref="s1", captured_at=1000.0, trust="TRUSTED"
        ),
        "evd_b": EvidenceMeta(
            evidence_id="evd_b", source_ref="s1", captured_at=1000.0, trust="TRUSTED"
        ),
        "evd_post": EvidenceMeta(
            evidence_id="evd_post", source_ref="probe", captured_at=2000.0, trust="POST_HOC"
        ),
    }
    adj = GraphAdjudicator(meta, run_started_at=900.0, run_ended_at=1100.0)
    hyps = [
        {
            "hypothesis_id": "h1",
            "supporting": [
                {"id": "evd_a", "trust": "TRUSTED", "confirming": True},
                {"id": "evd_a", "trust": "TRUSTED", "confirming": True},  # 重复
                {"id": "evd_b", "trust": "TRUSTED", "confirming": False},
                {"id": "evd_post", "trust": "POST_HOC", "confirming": True},  # 时间不匹配
            ],
            "contradicting": [{"id": "evd_a", "trust": "TRUSTED"}],  # 全局重复
        }
    ]
    report = adj.adjudicate(hyps)
    v = report.verdicts[0]
    assert set(v.duplicate_evidence) == {"evd_a"}  # 全局重复只记一次（首个保留）
    assert "evd_post" in v.time_mismatched  # 事后观测标记
    assert v.authority_confirmed is True  # evd_a TRUSTED+confirming+窗口内
    # explain() 能回答指令 §4.4 的五个问题
    exp = adj.explain(hyps, "h1")
    assert exp["supporting"] and exp["found"]
    assert set(exp["duplicates"]) == {"evd_a"}
    assert exp["independent_sources"] == ["s1"]  # 来源独立：s1 一条独立支持链


def test_adjudicator_blocks_confirmation_without_time_aligned_authority() -> None:
    meta = {
        "evd_post": EvidenceMeta(
            evidence_id="evd_post", source_ref="probe", captured_at=2000.0, trust="POST_HOC"
        ),
    }
    adj = GraphAdjudicator(meta, run_started_at=900.0, run_ended_at=1100.0)
    hyps = [
        {
            "hypothesis_id": "h1",
            "supporting": [{"id": "evd_post", "trust": "POST_HOC", "confirming": True}],
            "contradicting": [],
        }
    ]
    report = adj.adjudicate(hyps)
    assert report.verdicts[0].authority_confirmed is False


def test_engine_downgrades_confirmed_on_graph_refutation() -> None:
    """裁决层判定权威确认不成立 → CONFIRMED 降级 SUPPORTED（图参与主因裁决）。"""
    from traceguard_evidence.graph.adjudication import HypothesisAdjudication

    engine = DiagnosisEngine()
    h = Hypothesis(
        hypothesis_id="h1",
        description="接口 5xx",
        failure_domain=FailureDomain.PRODUCT,
        failure_mechanism=FailureMechanism.API,
        supporting=[EvidenceRef("evd_1", "TRUSTED", True)],
    )
    # 引擎单看证据会 CONFIRMED；裁决层发现时间不匹配 → authority_confirmed=False
    verdict = HypothesisAdjudication(
        hypothesis_id="h1",
        support_ids=["evd_1"],
        time_mismatched=["evd_1"],
        authority_confirmed=False,
        authority_refuted=True,
    )

    class _Report:
        verdicts = [verdict]

        def to_dict(self) -> dict:
            return {"verdicts": [verdict.to_dict()]}

    result = engine.diagnose("symptom", [h], adjudication=_Report())
    assert h.status == HypothesisStatus.CONTRADICTED
    assert result.root_causes == []
    assert any("[graph]" in f for f in h.contributing_factors)


def test_engine_records_conflict_from_graph() -> None:
    from traceguard_evidence.graph.adjudication import HypothesisAdjudication

    engine = DiagnosisEngine()
    h = Hypothesis(
        hypothesis_id="h1",
        description="接口 5xx",
        failure_domain=FailureDomain.PRODUCT,
        failure_mechanism=FailureMechanism.API,
        supporting=[EvidenceRef("evd_1", "TRUSTED", True)],
        contradicting=[EvidenceRef("evd_2", "TRUSTED", False)],
    )
    verdict = HypothesisAdjudication(
        hypothesis_id="h1",
        authority_confirmed=True,
        authority_refuted=True,
        has_conflict=True,
    )

    class _Report:
        verdicts = [verdict]

        def to_dict(self) -> dict:
            return {"verdicts": [verdict.to_dict()]}

    result = engine.diagnose("symptom", [h], adjudication=_Report())
    assert result.unresolved_conflicts == ["h1"]
    assert result.outcome.value != "CONFIRMED"


def test_integrity_failed_evidence_cannot_confirm() -> None:
    """完整性校验未通过的证据不得作为权威确认。"""
    meta = {
        "evd_bad": EvidenceMeta(
            evidence_id="evd_bad",
            source_ref="s1",
            captured_at=1000.0,
            trust="TRUSTED",
            integrity_ok=False,
        ),
    }
    adj = GraphAdjudicator(meta, run_started_at=900.0, run_ended_at=1100.0)
    hyps = [
        {
            "hypothesis_id": "h1",
            "supporting": [{"id": "evd_bad", "trust": "TRUSTED", "confirming": True}],
            "contradicting": [],
        }
    ]
    report = adj.adjudicate(hyps)
    assert report.verdicts[0].authority_confirmed is False
    assert any("integrity" in n for n in report.verdicts[0].notes)


def test_evidence_meta_from_rows_parses_iso_and_integrity() -> None:
    rows = [
        {
            "evidence_id": "e1",
            "source_type": "network",
            "source_trust": "TRUSTED",
            "integrity_check": "PASS",
            "captured_at": "2026-11-24T10:00:00Z",
            "doc": {"source_ref": "s1"},
        },
        {
            "evidence_id": "e2",
            "source_type": "browser",
            "source_trust": "UNKNOWN",
            "integrity_check": "FAIL",
            "captured_at": "",
        },
    ]
    idx = evidence_meta_from_rows(rows)
    assert idx["e1"].trust == "TRUSTED" and idx["e1"].integrity_ok
    assert _parse_epoch(idx["e1"].captured_at) > 0
    assert idx["e2"].integrity_ok is False


# ---------------------------------------------------------------------------
# 3. 多假设竞争与弃权
# ---------------------------------------------------------------------------


def test_multi_hypothesis_competition_and_abstention() -> None:
    """多假设并存：可信支持 + 无权威确认 → PROBABLE；全无支持 → INCONCLUSIVE 弃权。"""
    engine = DiagnosisEngine()
    h_sup = Hypothesis(
        hypothesis_id="h_sup",
        description="定位器失效（有支持）",
        failure_domain=FailureDomain.TEST_ASSET,
        failure_mechanism=FailureMechanism.LOCATOR,
        supporting=[EvidenceRef("evd_1", "TRUSTED", False)],
    )
    h_none = Hypothesis(
        hypothesis_id="h_none",
        description="纯猜测（无证据）",
        failure_domain=FailureDomain.UNKNOWN,
        failure_mechanism=FailureMechanism.UNKNOWN,
    )
    result = engine.diagnose("symptom", [h_sup, h_none])
    assert h_sup.status == HypothesisStatus.SUPPORTED
    assert h_none.status == HypothesisStatus.CANDIDATE
    assert result.outcome.value == "PROBABLE"
    assert result.root_causes == []  # 不强制唯一根因

    # 全无支持 → 明确弃权
    result2 = engine.diagnose("symptom", [h_none])
    assert result2.outcome.value == "INCONCLUSIVE"
    assert result2.disposition.value == "NEEDS_MORE_EVIDENCE"


def test_untrusted_supporting_cannot_confirm() -> None:
    engine = DiagnosisEngine()
    h = Hypothesis(
        hypothesis_id="h1",
        description="事后观测支持",
        failure_domain=FailureDomain.TEST_EXECUTION,
        failure_mechanism=FailureMechanism.TIMING,
        supporting=[EvidenceRef("evd_post", "UNTRUSTED", True)],
    )
    result = engine.diagnose("symptom", [h])
    assert h.status != HypothesisStatus.CONFIRMED
    assert result.root_causes == []


# ---------------------------------------------------------------------------
# 4. 探针回灌率与 TIMING 假设生成
# ---------------------------------------------------------------------------


def test_post_hoc_probe_generates_timing_hypothesis_not_refutation() -> None:
    """事后状态查询与 Run 时假设冲突 → TIMING 新假设（修复错误反驳）。"""
    hyps = [
        {
            "hypothesis_id": "h_persist",
            "failure_mechanism": "PERSISTENCE",
            "supporting": [{"id": "evd_run_state", "trust": "TRUSTED", "confirming": True}],
            "contradicting": [],
            "probe_feedback": [],
            "excluded_by": [],
        }
    ]
    signal = ProbeSignal(
        probe_id="http_state",
        execution_id="px1",
        status="SUCCEEDED",
        evidence_ids=["evd_probe_state"],
        data={"response": {"ok": True, "count": 3, "keys": ["a"]}},
        observed_at=_time.time(),
        run_scope=False,
    )
    run_ended_at = _time.time() - 60  # Run 结束于 60 秒前 → 探针为事后观测
    merged, results = merge_signals(hyps, [signal], {}, run_ended_at=run_ended_at)
    fb = results[0]
    assert fb.time_alignment == "POST_HOC"
    # 不排除 PERSISTENCE
    persist = next(h for h in merged if h["hypothesis_id"] == "h_persist")
    assert persist["excluded_by"] == []
    assert all(r.get("trust") == "POST_HOC" for r in persist["contradicting"])
    # 生成 TIMING 新假设
    timing = [h for h in merged if h["failure_mechanism"] == "TIMING"]
    assert timing and "滞后" in timing[0]["description"]
    assert timing[0]["suggested_by"] == "rule:probe-timing"
    # 回灌率：反馈结果留痕（含 skipped 与 update）
    assert fb.produced_evidence
    assert fb.to_dict()["time_alignment"] == "POST_HOC"


def test_run_scope_probe_keeps_authority() -> None:
    """Run 作用域探针（run_scope=True）保持 ALIGNED：可支持/排除。"""
    hyps = [
        {
            "hypothesis_id": "h_persist",
            "failure_mechanism": "PERSISTENCE",
            "supporting": [],
            "contradicting": [],
            "probe_feedback": [],
            "excluded_by": [],
        }
    ]
    signal = ProbeSignal(
        probe_id="http_state",
        execution_id="px2",
        status="SUCCEEDED",
        evidence_ids=["evd_in_run"],
        data={"response": {"ok": True, "count": 2, "keys": ["a"]}},
        observed_at=_time.time() - 10,
        run_scope=True,  # Run 作用域内采集
    )
    merged, results = merge_signals(hyps, [signal], {}, run_ended_at=_time.time())
    fb = results[0]
    assert fb.time_alignment == "ALIGNED"
    persist = merged[0]
    # ALIGNED 的反驳有权威 → 支持 PERSISTENCE 反驳且可排除
    assert any(r.get("trust") == "TRUSTED" for r in persist["contradicting"])
    assert persist["excluded_by"], "Run 作用域证据可以排除假设"


def test_failed_probe_recorded_but_no_hypothesis_change() -> None:
    """探针失败：回灌率留痕（skipped 记录），但不污染假设。"""
    hyps = [
        {
            "hypothesis_id": "h1",
            "supporting": [],
            "contradicting": [],
            "probe_feedback": [],
            "excluded_by": [],
        }
    ]
    signal = ProbeSignal(
        probe_id="http_state",
        execution_id="px3",
        status="FAILED",
        evidence_ids=[],
        data={},
    )
    merged, results = merge_signals(hyps, [signal], {})
    assert results[0].skipped_reason == "PROBE_FAILED_NOT_EVIDENCE"
    assert merged[0]["supporting"] == [] and merged[0]["contradicting"] == []
