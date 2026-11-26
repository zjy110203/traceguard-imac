"""Diagnosis v1 引擎（阶段7，C08/C10）。

核心不变量（路线图阶段7 + 架构原则）：
- 八个维度独立：Symptom / Failure Domain / Failure Mechanism / Root Cause /
  Contributing Factor / Flaky Characteristic / Diagnosis Status / Disposition；
- 多 Hypothesis：独立状态、支持+反驳双向证据；
- CONFIRMED 必须由确定性确认规则 + 权威证据产生，LLM 不得自行宣布；
- 未确认假设不成为根因；允许无根因 → INCONCLUSIVE；多根因分别保留；
- Flaky 是稳定性特征不是根因（需多次执行差异证据）；
- Diagnosis 与 Disposition 分离，不同问题走不同处置路径；
- 不把所有失败都送 Repair。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from traceguard_core.ids import new_id, utc_now_iso


class FailureDomain(str, Enum):
    PRODUCT = "PRODUCT"
    TEST_ASSET = "TEST_ASSET"
    TEST_EXECUTION = "TEST_EXECUTION"
    ENVIRONMENT = "ENVIRONMENT"
    AGENT = "AGENT"
    PLATFORM = "PLATFORM"
    UNKNOWN = "UNKNOWN"


class FailureMechanism(str, Enum):
    API = "API"
    NETWORK = "NETWORK"
    PERSISTENCE = "PERSISTENCE"
    UI_RENDERING = "UI_RENDERING"
    LOCATOR = "LOCATOR"
    TIMING = "TIMING"
    AUTHORIZATION = "AUTHORIZATION"
    BROWSER = "BROWSER"
    TEST_DATA = "TEST_DATA"
    UNKNOWN = "UNKNOWN"


class HypothesisStatus(str, Enum):
    CANDIDATE = "CANDIDATE"
    SUPPORTED = "SUPPORTED"
    CONTRADICTED = "CONTRADICTED"
    CONFIRMED = "CONFIRMED"
    EXCLUDED = "EXCLUDED"


class DiagnosisOutcome(str, Enum):
    CONFIRMED = "CONFIRMED"
    PROBABLE = "PROBABLE"
    INCONCLUSIVE = "INCONCLUSIVE"


class Disposition(str, Enum):
    PRODUCT_DEFECT = "PRODUCT_DEFECT"
    TEST_ASSET_ISSUE = "TEST_ASSET_ISSUE"
    TEST_EXECUTION_ISSUE = "TEST_EXECUTION_ISSUE"
    ENVIRONMENT_ISSUE = "ENVIRONMENT_ISSUE"
    AGENT_ISSUE = "AGENT_ISSUE"
    PLATFORM_ISSUE = "PLATFORM_ISSUE"
    NEEDS_MORE_EVIDENCE = "NEEDS_MORE_EVIDENCE"
    MANUAL_REVIEW = "MANUAL_REVIEW"


#: 状态基线置信度（确定性映射，启发式标注；不冒充校准概率）
_STATUS_CONFIDENCE: dict[str, float] = {
    "CONFIRMED": 0.9,
    "SUPPORTED": 0.6,
    "CANDIDATE": 0.3,
    "CONTRADICTED": 0.2,
    "EXCLUDED": 0.0,
}


def hypothesis_confidence(h: Any) -> float:
    """假设置信度（RUN-018 WP5：重诊断"置信度变化"的确定性度量）。

    规则（可解释、无学习成分）：
    - 以状态基线为底（CONFIRMED 0.9 / SUPPORTED 0.6 / CANDIDATE 0.3 /
      CONTRADICTED 0.2 / EXCLUDED 0.0）；
    - 每条 TRUSTED 且 directly_confirming 的支持证据 +0.05（封顶 +0.1）；
    - 支持证据**全部**为 POST_HOC / TIME_UNKNOWN 时封顶 0.4：
      时间未对齐的观测不得撑起高置信（与 R1-02 一致）。

    取值为启发式标量，只用于版本间变化对比与展示，不参与 CONFIRMED 判定。
    """
    if isinstance(h, Hypothesis):
        status = h.status.value
        supporting = [
            {"trust": e.source_trust, "confirming": e.directly_confirming} for e in h.supporting
        ]
    else:
        status = str(h.get("status", "CANDIDATE"))
        supporting = list(h.get("supporting") or [])
    base = _STATUS_CONFIDENCE.get(status, 0.3)
    confirming = sum(
        1 for r in supporting if r.get("confirming") and str(r.get("trust")) == "TRUSTED"
    )
    bonus = min(0.1, 0.05 * confirming)
    if supporting and all(
        str(r.get("trust")) in {"POST_HOC", "TIME_UNKNOWN"} for r in supporting
    ):
        base = min(base, 0.4)
    return round(min(1.0, base + bonus), 3)


@dataclass
class EvidenceRef:
    evidence_id: str
    source_trust: str = "UNKNOWN"  # TRUSTED / UNTRUSTED
    directly_confirming: bool = False  # 是否为该类故障的权威确认证据


@dataclass
class Hypothesis:
    hypothesis_id: str
    description: str
    failure_domain: FailureDomain
    failure_mechanism: FailureMechanism
    supporting: list[EvidenceRef] = field(default_factory=list)
    contradicting: list[EvidenceRef] = field(default_factory=list)
    status: HypothesisStatus = HypothesisStatus.CANDIDATE
    contributing_factors: list[str] = field(default_factory=list)
    flaky_characteristic: bool = False
    suggested_by: str = "rule"  # rule / llm（LLM 仅提出候选）
    #: 探针证据的判定记录（谁支持/反驳/排除了它，依据是什么）
    probe_feedback: list[dict[str, Any]] = field(default_factory=list)
    excluded_by: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "hypothesis_id": self.hypothesis_id,
            "description": self.description,
            "failure_domain": self.failure_domain.value,
            "failure_mechanism": self.failure_mechanism.value,
            "supporting": [
                {"id": e.evidence_id, "trust": e.source_trust, "confirming": e.directly_confirming}
                for e in self.supporting
            ],
            "contradicting": [
                {"id": e.evidence_id, "trust": e.source_trust} for e in self.contradicting
            ],
            "status": self.status.value,
            "confidence": hypothesis_confidence(self),
            "contributing_factors": list(self.contributing_factors),
            "flaky_characteristic": self.flaky_characteristic,
            "suggested_by": self.suggested_by,
            "probe_feedback": list(self.probe_feedback),
            "excluded_by": list(self.excluded_by),
        }


@dataclass
class Diagnosis:
    diagnosis_id: str
    symptom: str
    hypotheses: list[Hypothesis] = field(default_factory=list)
    root_causes: list[str] = field(default_factory=list)  # 仅 CONFIRMED hypothesis id
    outcome: DiagnosisOutcome = DiagnosisOutcome.INCONCLUSIVE
    disposition: Disposition = Disposition.NEEDS_MORE_EVIDENCE
    unresolved_conflicts: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=utc_now_iso)
    #: RUN-010 WP4：Graph 裁决层结论（dict 形式，None = 未启用）
    graph_adjudication: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "diagnosis_id": self.diagnosis_id,
            "symptom": self.symptom,
            "hypotheses": [h.to_dict() for h in self.hypotheses],
            "root_causes": list(self.root_causes),
            "outcome": self.outcome.value,
            "disposition": self.disposition.value,
            "unresolved_conflicts": list(self.unresolved_conflicts),
            "graph_adjudication": self.graph_adjudication,
            "created_at": self.created_at,
        }


class ConfirmationRule:
    """确定性确认规则（按故障类别，不由 LLM 决定；具体门槛为待确认事项，此处用最小可信规则）。"""

    def is_confirmed(self, h: Hypothesis) -> bool:
        # 规则：有至少一条 TRUSTED 且 directly_confirming 的权威证据，且无未解决反驳
        has_authoritative = any(
            e.source_trust == "TRUSTED" and e.directly_confirming for e in h.supporting
        )
        has_contradiction = any(e.source_trust == "TRUSTED" for e in h.contradicting)
        return has_authoritative and not has_contradiction

    def is_supported(self, h: Hypothesis) -> bool:
        return bool(h.supporting) and not any(e.source_trust == "TRUSTED" for e in h.contradicting)


class DiagnosisEngine:
    def __init__(self, rule: ConfirmationRule | None = None) -> None:
        self.rule = rule or ConfirmationRule()

    def diagnose(
        self,
        symptom: str,
        hypotheses: list[Hypothesis],
        *,
        adjudication: Any | None = None,
    ) -> Diagnosis:
        """裁决诊断。

        RUN-010 WP4：adjudication（Graph 裁决层报告，可选）参与主因裁决——
        图不再是只读可视化：
        - 裁决层判定"权威确认不成立"（时间不匹配/完整性失败/证据重复后无独立支持）
          → CONFIRMED 降级为 SUPPORTED；
        - 裁决层判定"权威反驳成立" → CONTRADICTED；
        - 裁决层判定"权威支持与权威反驳同时成立" → 记入未解决冲突（人工复核），
          不强制选择唯一根因。
        """
        d = Diagnosis(diagnosis_id=new_id("diagnosis"), symptom=symptom, hypotheses=hypotheses)

        for h in hypotheses:
            trusted_support = any(e.source_trust == "TRUSTED" for e in h.supporting)
            trusted_contradiction = any(e.source_trust == "TRUSTED" for e in h.contradicting)
            if self.rule.is_confirmed(h):
                h.status = HypothesisStatus.CONFIRMED
            elif trusted_contradiction:
                h.status = HypothesisStatus.CONTRADICTED
            elif h.excluded_by and not trusted_support:
                # 探针给出确定性排除依据，且没有任何可信支持 → 排除（不静默当作"候选"）
                h.status = HypothesisStatus.EXCLUDED
            elif self.rule.is_supported(h):
                h.status = HypothesisStatus.SUPPORTED
            else:
                h.status = HypothesisStatus.CANDIDATE
            # LLM 提出的假设永远不能成为根因（能力层 ≠ 事实层）。
            # 必须放在状态计算之后：仅"先降级再算"会让规则又把 CONFIRMED 加回来。
            if (
                str(h.suggested_by).lower().startswith("llm")
                and h.status == HypothesisStatus.CONFIRMED
            ):
                h.status = HypothesisStatus.SUPPORTED
                h.contributing_factors.append(
                    "LLM 假设不得作为 CONFIRMED 根因，已降级为 SUPPORTED 供人工复核"
                )

        # ---- Graph 裁决层参与裁决（RUN-010 WP4）
        unresolved_from_graph: list[str] = []
        if adjudication is not None:
            verdicts = getattr(adjudication, "verdicts", None) or []
            for v in verdicts:
                h_obj = next((x for x in hypotheses if x.hypothesis_id == v.hypothesis_id), None)
                if h_obj is None:
                    continue
                if v.duplicate_evidence:
                    h_obj.contributing_factors.append(
                        f"[graph] 重复证据已消重：{sorted(set(v.duplicate_evidence))}"
                    )
                if v.time_mismatched:
                    h_obj.contributing_factors.append(
                        f"[graph] 时间不匹配证据（不作为权威）：{sorted(set(v.time_mismatched))}"
                    )
                if v.has_conflict and v.authority_confirmed and v.authority_refuted:
                    unresolved_from_graph.append(v.hypothesis_id)
                    if h_obj.status == HypothesisStatus.CONFIRMED:
                        h_obj.status = HypothesisStatus.SUPPORTED
                        h_obj.contributing_factors.append(
                            "[graph] 权威支持与权威反驳冲突 → 降级为 SUPPORTED 待人工复核"
                        )
                    continue
                if h_obj.status == HypothesisStatus.CONFIRMED and not v.authority_confirmed:
                    h_obj.status = HypothesisStatus.SUPPORTED
                    h_obj.contributing_factors.append(
                        "[graph] 裁决层判定权威确认不成立（时间/完整性/来源独立）→ 降级 SUPPORTED"
                    )
                if (
                    h_obj.status in (HypothesisStatus.CANDIDATE, HypothesisStatus.SUPPORTED)
                    and v.authority_refuted
                ):
                    h_obj.status = HypothesisStatus.CONTRADICTED
                    h_obj.contributing_factors.append("[graph] 裁决层判定权威反驳成立")

        # 根因：仅 CONFIRMED，可多个，不强制压缩
        # RUN-010 WP5（Flaky 弃权规则）：Flaky 特征下，**时序敏感的 PERSISTENCE
        # 单次确认不可靠**（状态查询滞后/最终一致性会造成同用例结论摇摆）——
        # 按阶段 7 原则降级为 SUPPORTED 并请求多次执行证据（弃权优于硬猜）。
        # 结构性失败（5xx / 定位器 / 网络）不受此规则影响：它们的证据不随时序摇摆。
        for h in hypotheses:
            if (
                h.flaky_characteristic
                and h.status == HypothesisStatus.CONFIRMED
                and h.failure_mechanism == FailureMechanism.PERSISTENCE
            ):
                h.status = HypothesisStatus.SUPPORTED
                h.contributing_factors.append(
                    "[flaky] 同用例历史结论不一致：持久化类单次确认受时序影响不可靠，"
                    "降级为 SUPPORTED 并请求多次执行证据"
                )

        d.root_causes = [
            h.hypothesis_id for h in hypotheses if h.status == HypothesisStatus.CONFIRMED
        ]

        # 未解决冲突：同一假设同时拥有 TRUSTED 支持与 TRUSTED 反驳证据
        d.unresolved_conflicts = [
            h.hypothesis_id
            for h in hypotheses
            if any(e.source_trust == "TRUSTED" for e in h.supporting)
            and any(e.source_trust == "TRUSTED" for e in h.contradicting)
        ]
        for hid in unresolved_from_graph:
            if hid not in d.unresolved_conflicts:
                d.unresolved_conflicts.append(hid)

        # Diagnosis Outcome
        if d.root_causes:
            d.outcome = DiagnosisOutcome.CONFIRMED
        elif any(h.status == HypothesisStatus.SUPPORTED for h in hypotheses):
            d.outcome = DiagnosisOutcome.PROBABLE
        else:
            d.outcome = DiagnosisOutcome.INCONCLUSIVE

        # Disposition 分流（独立于 Diagnosis，不把所有失败送 Repair）
        d.disposition = self._route(hypotheses, d)
        d.graph_adjudication = adjudication.to_dict() if adjudication is not None else None
        return d

    def _route(self, hypotheses: list[Hypothesis], d: Diagnosis) -> Disposition:
        confirmed = [h for h in hypotheses if h.status == HypothesisStatus.CONFIRMED]
        if not confirmed:
            if d.unresolved_conflicts:
                # 证据互相矛盾且无法裁决（P03 未决策）：交人工，不继续烧探针预算
                return Disposition.MANUAL_REVIEW
            return Disposition.NEEDS_MORE_EVIDENCE
        # 多个已确认根因：若有产品域 → 产品缺陷优先记录
        domains = {h.failure_domain for h in confirmed}
        if FailureDomain.PRODUCT in domains:
            return Disposition.PRODUCT_DEFECT
        if FailureDomain.TEST_ASSET in domains:
            return Disposition.TEST_ASSET_ISSUE
        if FailureDomain.TEST_EXECUTION in domains:
            return Disposition.TEST_EXECUTION_ISSUE
        if FailureDomain.ENVIRONMENT in domains:
            return Disposition.ENVIRONMENT_ISSUE
        if FailureDomain.AGENT in domains:
            return Disposition.AGENT_ISSUE
        if FailureDomain.PLATFORM in domains:
            return Disposition.PLATFORM_ISSUE
        return Disposition.MANUAL_REVIEW
