"""RUN-010 WP4：Evidence Graph 裁决层（Graph-based Adjudication）。

背景（RUN-009 误差分析）：图此前只用于可视化与"只升置信度"，从未参与主因裁决。

本模块把「图上的证据关系」变成可执行的裁决输入：

- 支持关系 / 反驳关系（每条假设的 supporting/contradicting 边）；
- 时间关系（证据观测时间 vs Run 窗口；POST_HOC 证据不得作为权威）；
- 来源独立性（同一 step/来源的多条证据不叠加为独立支持）；
- 派生证据与重复证据消重（同一 evidence id 重复附着只计一次；
  同一来源+同一内容的派生证据标记 duplicate）；
- Evidence trust（TRUSTED / POST_HOC / UNTRUSTED 分层）；
- Evidence integrity（完整性校验未通过的证据降权）；
- Assertion 与 Hypothesis 关联（断言失败证据进入假设的边集）。

裁决层必须能回答（指令 §4.4）：
- 哪些证据支持该假设？哪些反驳？
- 证据是否来自同一原始来源？
- 是否存在重复证据？
- 证据时间是否匹配？
- 是否存在未解决冲突？

约束：不得简单使用"来源数量"作为可信度——确认与否取决于证据质量
（TRUSTED + directly_confirming）与冲突状态，而不是数量。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

TRUST_AUTHORITY = "TRUSTED"
TRUST_POST_HOC = "POST_HOC"
TRUST_UNTRUSTED = "UNTRUSTED"
TRUST_UNKNOWN = "UNKNOWN"


@dataclass
class EvidenceMeta:
    """裁决层使用的证据元数据（来自 Evidence Store / Run 报告）。"""

    evidence_id: str
    source_type: str = ""  # browser / network / api / backend_log / database / probe
    source_ref: str = ""  # 来源定位（如 step_id / probe execution）
    captured_at: float = 0.0  # epoch 秒；0 = 未知
    trust: str = TRUST_UNKNOWN
    integrity_ok: bool = True
    kind: str = ""


@dataclass
class HypothesisAdjudication:
    """单个假设的裁决结论。"""

    hypothesis_id: str
    support_ids: list[str] = field(default_factory=list)
    refute_ids: list[str] = field(default_factory=list)
    independent_support_sources: list[str] = field(default_factory=list)
    duplicate_evidence: list[str] = field(default_factory=list)
    time_mismatched: list[str] = field(default_factory=list)
    authority_confirmed: bool = False
    authority_refuted: bool = False
    has_conflict: bool = False
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "hypothesis_id": self.hypothesis_id,
            "support_ids": list(self.support_ids),
            "refute_ids": list(self.refute_ids),
            "independent_support_sources": list(self.independent_support_sources),
            "duplicate_evidence": list(self.duplicate_evidence),
            "time_mismatched": list(self.time_mismatched),
            "authority_confirmed": self.authority_confirmed,
            "authority_refuted": self.authority_refuted,
            "has_conflict": self.has_conflict,
            "notes": list(self.notes),
        }


@dataclass
class AdjudicationReport:
    verdicts: list[HypothesisAdjudication] = field(default_factory=list)
    global_duplicates: list[str] = field(default_factory=list)
    unresolved_conflicts: list[str] = field(default_factory=list)

    def for_hypothesis(self, hid: str) -> HypothesisAdjudication | None:
        return next((v for v in self.verdicts if v.hypothesis_id == hid), None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdicts": [v.to_dict() for v in self.verdicts],
            "global_duplicates": list(self.global_duplicates),
            "unresolved_conflicts": list(self.unresolved_conflicts),
        }


def _parse_ts(value: Any) -> float:
    """ISO 字符串 / epoch / 空 → epoch 秒（失败 0）。"""
    if value in (None, "", 0):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    from datetime import datetime

    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


class GraphAdjudicator:
    """把假设的 evidence 边集当作图来裁决；输出可解释结论供引擎消费。"""

    def __init__(
        self,
        evidence_meta: dict[str, EvidenceMeta] | None = None,
        *,
        run_started_at: float = 0.0,
        run_ended_at: float = 0.0,
    ) -> None:
        self.meta = evidence_meta or {}
        self.run_started_at = run_started_at
        self.run_ended_at = run_ended_at

    # ------------------------------------------------------------------ 主流程
    def adjudicate(self, hypotheses: list[dict[str, Any]]) -> AdjudicationReport:
        report = AdjudicationReport()
        seen_ids: set[str] = set()
        for h in hypotheses:
            v = self._adjudicate_one(h, seen_ids)
            report.verdicts.append(v)
            if v.duplicate_evidence:
                report.global_duplicates.extend(v.duplicate_evidence)
            if v.has_conflict and v.authority_confirmed and v.authority_refuted:
                report.unresolved_conflicts.append(str(h.get("hypothesis_id")))
        return report

    # ------------------------------------------------------------------ 单假设
    def _adjudicate_one(self, h: dict[str, Any], seen_ids: set[str]) -> HypothesisAdjudication:
        hid = str(h.get("hypothesis_id"))
        v = HypothesisAdjudication(hypothesis_id=hid)
        support = list(h.get("supporting") or [])
        refute = list(h.get("contradicting") or [])

        support_ids = self._collect_unique(support, v, seen_ids)
        refute_ids = self._collect_unique(refute, v, seen_ids, bucket="refute")
        v.support_ids = support_ids
        v.refute_ids = refute_ids

        # 来源独立性：按 source_ref 去重（同一来源的多条证据 = 一条独立支持链）。
        # POST_HOC / 时间不匹配的证据不构成独立支持来源（不具权威性）。
        sources: set[str] = set()
        time_probe = HypothesisAdjudication(hypothesis_id="")  # 只借用时间判定，不写入结论
        for ref in support:
            meta = self.meta.get(str(ref.get("id")))
            if meta is None:
                continue
            if str(ref.get("trust") or "") == TRUST_POST_HOC:
                continue
            if not self._time_ok(ref, meta, time_probe):
                continue
            src = meta.source_ref or meta.evidence_id
            sources.add(src)
        v.independent_support_sources = sorted(sources)

        # 权威确认：TRUSTED + directly_confirming + 完整性通过 + 时间窗口匹配
        authority_support = False
        for ref in support:
            meta = self.meta.get(str(ref.get("id")))
            trust = str(
                ref.get("trust") or (meta.trust if meta else TRUST_UNKNOWN) or TRUST_UNKNOWN
            )
            confirming = bool(ref.get("confirming"))
            if meta is not None and not meta.integrity_ok:
                v.notes.append(f"evidence {meta.evidence_id} integrity failed → 降权")
                continue
            if trust == TRUST_AUTHORITY and confirming and self._time_ok(ref, meta, v):
                authority_support = True
        authority_refute = False
        for ref in refute:
            meta = self.meta.get(str(ref.get("id")))
            trust = str(
                ref.get("trust") or (meta.trust if meta else TRUST_UNKNOWN) or TRUST_UNKNOWN
            )
            if meta is not None and not meta.integrity_ok:
                continue
            if trust == TRUST_AUTHORITY and self._time_ok(ref, meta, v):
                authority_refute = True
        v.authority_confirmed = authority_support
        v.authority_refuted = authority_refute
        # 冲突：同一假设同时存在权威支持与权威反驳
        v.has_conflict = authority_support and authority_refute
        return v

    def _collect_unique(
        self,
        refs: list[dict[str, Any]],
        v: HypothesisAdjudication,
        seen: set[str],
        *,
        bucket: str = "support",
    ) -> list[str]:
        ids: list[str] = []
        for ref in refs:
            eid = str(ref.get("id") or "")
            if not eid:
                continue
            # 全局重复消重：同一证据 id 在多条边重复出现 → 只保留首条，其余标记 duplicate
            if eid in seen:
                v.duplicate_evidence.append(eid)
                continue
            seen.add(eid)
            ids.append(eid)
            # 时间匹配检查
            self._time_ok(ref, self.meta.get(eid), v)
        return ids

    def _time_ok(
        self, ref: dict[str, Any], meta: EvidenceMeta | None, v: HypothesisAdjudication
    ) -> bool:
        """证据时间是否落在 Run 窗口内（未知时间视为匹配，留痕不阻断）。"""
        alignment = str(ref.get("time_alignment") or "")
        if alignment == "POST_HOC":
            v.time_mismatched.append(str(ref.get("id")))
            v.notes.append(f"evidence {ref.get('id')} 为事后观测（POST_HOC），不作为权威")
            return False
        ts = meta.captured_at if meta else 0.0
        if ts and self.run_ended_at and ts > self.run_ended_at + 5.0:
            v.time_mismatched.append(str(ref.get("id")))
            v.notes.append(
                f"evidence {ref.get('id')} captured_at {ts:.0f} 晚于 run 结束 {self.run_ended_at:.0f}"
            )
            return False
        return True

    # ------------------------------------------------------------------ 查询 API
    def explain(self, hypotheses: list[dict[str, Any]], hid: str) -> dict[str, Any]:
        """回答指令 §4.4 的五个问题（供前端诊断详情与报告使用）。"""
        target = next((h for h in hypotheses if str(h.get("hypothesis_id")) == hid), None)
        if target is None:
            return {"hypothesis_id": hid, "found": False}
        report = self.adjudicate([target])
        v = report.verdicts[0]
        return {
            "hypothesis_id": hid,
            "found": True,
            "supporting": v.support_ids,
            "refuting": v.refute_ids,
            "independent_sources": v.independent_support_sources,
            "duplicates": v.duplicate_evidence,
            "time_mismatched": v.time_mismatched,
            "authority_confirmed": v.authority_confirmed,
            "authority_refuted": v.authority_refuted,
            "has_conflict": v.has_conflict,
            "notes": v.notes,
        }


def evidence_meta_from_rows(rows: list[dict[str, Any]]) -> dict[str, EvidenceMeta]:
    """把 Evidence Store 行转换为裁决元数据索引。"""
    out: dict[str, EvidenceMeta] = {}
    for row in rows:
        eid = str(row.get("evidence_id") or "")
        if not eid:
            continue
        trust = str(row.get("source_trust") or TRUST_UNKNOWN)
        if trust == "TRUSTED":
            trust = TRUST_AUTHORITY
        elif trust == "POST_HOC":
            trust = TRUST_POST_HOC
        integrity = str(row.get("integrity_check") or "NOT_RUN")
        out[eid] = EvidenceMeta(
            evidence_id=eid,
            source_type=str(row.get("source_type") or ""),
            source_ref=str((row.get("doc") or {}).get("source_ref") or row.get("source_ref") or ""),
            captured_at=_parse_ts(row.get("captured_at")),
            trust=trust,
            integrity_ok=integrity in {"PASS", "NOT_RUN"},
            kind=str(row.get("kind") or ""),
        )
    return out


__all__ = [
    "AdjudicationReport",
    "EvidenceMeta",
    "GraphAdjudicator",
    "HypothesisAdjudication",
    "evidence_meta_from_rows",
]
