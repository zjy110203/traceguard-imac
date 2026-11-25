"""RUN-011-R1 WP3：假设去重与合并（OPEN-RISK-03）。

问题：LLM 候选与规则假设、以及多个 LLM 候选之间按顺序直接追加，
相同机制 + 近似描述的重复候选会分别携带同一 Evidence——诊断结论会因
候选重复产生**虚假的独立支持数量**（两轮独立运行候选顺序不同还会导致结论漂移）。

规则（保守、确定性、可复核，不引入新的 LLM 调用）：
1. 先按 ``failure_mechanism`` 精确分组（不同机制即使描述相似也不合并）；
2. 组内按**规范化描述相似度**（SequenceMatcher + token Jaccard 双指标）
   识别近似重复候选（阈值内视为重复）；
3. 组内合并顺序与输入顺序无关（按规范化描述的稳定键排序后再归并），
   两次独立运行候选顺序不同 → 合并结果相同；
4. 合并保留：全部真实证据引用（evidence_id 并集去重）、全部来源
   （suggested_by 列表 + 原始候选记录 merged_from）、全部 contributing_factors；
5. 合并后同一 Evidence 在同一假设内只计一次（引用去重）；
   跨假设的重复计数由 Graph 裁决层全局消重（已实现）兜底；
6. LLM 候选合并后仍不得 CONFIRMED（引擎层既有约束不变）；
7. 原始候选记录完整保留（dedup 审计：merged / kept / order-invariant key），
   人工复核与弃权（INCONCLUSIVE）能力不受影响。

相似度实现说明：使用确定性词面相似度（规范化后序列比 + 词集合交并比），
不调用 LLM/嵌入模型——在无计费授权（B-03）下保持可复核、可重复；
若未来接入嵌入相似度，可替换 `_similarity` 而不改合并契约。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any

from .engine import Hypothesis

#: 词面相似度阈值（双指标取高者）：≥ 阈值视为近似重复
SIMILARITY_THRESHOLD = 0.72

_PREFIX_RE = re.compile(r"^\s*\[?(?:LLM 候选|LLM)\]?\s*", re.IGNORECASE)
_TOKEN_RE = re.compile(r"[a-zA-Z0-9_]+|[\u4e00-\u9fff]")


def _normalize(description: str) -> str:
    """规范化描述：去 LLM 候选前缀、小写、压缩空白。"""
    s = _PREFIX_RE.sub("", str(description or ""))
    return re.sub(r"\s+", " ", s).strip().lower()


def _tokens(normalized: str) -> set[str]:
    """中文按字、英文数字按词的混合 token 集（确定性）。"""
    toks = set(_TOKEN_RE.findall(normalized))
    # 英文连续词合并为整体 token，保留单字（中文）
    return toks


def _similarity(a: str, b: str) -> float:
    """词面语义相似度：max(序列比, token Jaccard)。确定性、可复核。"""
    na, nb = _normalize(a), _normalize(b)
    if not na or not nb:
        return 0.0
    seq = SequenceMatcher(None, na, nb).ratio()
    ta, tb = _tokens(na), _tokens(nb)
    jac = len(ta & tb) / len(ta | tb) if (ta | tb) else 0.0
    return max(seq, jac)


@dataclass
class DedupGroup:
    """一组被合并的近似重复候选（保留原始来源与候选记录）。"""

    mechanism: str
    canonical_description: str
    kept_hypothesis_id: str
    merged_hypothesis_ids: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    merged_from: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "mechanism": self.mechanism,
            "canonical_description": self.canonical_description,
            "kept_hypothesis_id": self.kept_hypothesis_id,
            "merged_hypothesis_ids": list(self.merged_hypothesis_ids),
            "sources": list(self.sources),
            "merged_from": [dict(x) for x in self.merged_from],
        }


def _canon_key(h: Hypothesis) -> str:
    """顺序无关的稳定键：机制 + 规范化描述（保证两轮运行合并结果一致）。"""
    return f"{h.failure_mechanism.value}|{_normalize(h.description)}"


def dedup_hypotheses(
    hypotheses: list[Hypothesis],
    *,
    threshold: float = SIMILARITY_THRESHOLD,
) -> tuple[list[Hypothesis], dict[str, Any]]:
    """合并近似重复假设（按机制分组 + 描述相似度），返回 (去重后假设, 审计记录)。

    - 不修改传入对象的证据内容（合并产生新的引用并集）；
    - status 保守合并：任一 CANDIDATE → 合并后不高于原最高态；
      LLM 来源（suggested_by 以 llm 开头）强制不升 CONFIRMED（引擎层另有兜底）；
    - 审计记录 merged / untouched / order_invariant，供人工复核。
    """
    groups: dict[str, list[Hypothesis]] = {}
    for h in hypotheses:
        groups.setdefault(h.failure_mechanism.value, []).append(h)

    out: list[Hypothesis] = []
    audit_groups: list[DedupGroup] = []
    untouched = 0

    for mech in sorted(groups):  # 机制遍历顺序确定
        members = groups[mech]
        # 顺序无关：按稳定键排序（两次运行输入顺序不同 → 同一归并顺序）
        members = sorted(members, key=_canon_key)
        used = [False] * len(members)
        for i, primary in enumerate(members):
            if used[i]:
                continue
            used[i] = True
            dup_indices = []
            for j in range(i + 1, len(members)):
                if used[j]:
                    continue
                if _similarity(primary.description, members[j].description) >= threshold:
                    used[j] = True
                    dup_indices.append(j)
            if not dup_indices:
                untouched += 1
                out.append(primary)
                continue

            # ---- 合并：证据引用并集（按 evidence_id 去重）、来源与因素全保留 ----
            merged_ids = [primary.hypothesis_id]
            sources = [str(primary.suggested_by or "rule")]
            merged_from = [
                {
                    "hypothesis_id": primary.hypothesis_id,
                    "description": primary.description,
                    "suggested_by": str(primary.suggested_by or "rule"),
                }
            ]
            sup_ids: dict[str, Any] = {e.evidence_id: e for e in primary.supporting}
            con_ids: dict[str, Any] = {e.evidence_id: e for e in primary.contradicting}
            factors = list(primary.contributing_factors)
            probe_fb = list(primary.probe_feedback)
            excluded = list(primary.excluded_by)
            flaky = primary.flaky_characteristic
            status = primary.status
            desc = primary.description

            for j in dup_indices:
                other = members[j]
                merged_ids.append(other.hypothesis_id)
                sources.append(str(other.suggested_by or "rule"))
                merged_from.append(
                    {
                        "hypothesis_id": other.hypothesis_id,
                        "description": other.description,
                        "suggested_by": str(other.suggested_by or "rule"),
                        "similarity_to_kept": round(
                            _similarity(primary.description, other.description), 4
                        ),
                    }
                )
                for e in other.supporting:
                    sup_ids.setdefault(e.evidence_id, e)  # 同一 Evidence 只计一次
                for e in other.contradicting:
                    con_ids.setdefault(e.evidence_id, e)
                factors.extend(other.contributing_factors)
                probe_fb.extend(other.probe_feedback)
                excluded.extend(other.excluded_by)
                flaky = flaky or other.flaky_characteristic
                # 状态保守：取「最弱」——任一 CANDIDATE 则不高于 SUPPORTED 的最低可解释态
                if other.status.value == "CANDIDATE":
                    status = other.status
                elif status.value == "CONFIRMED" and other.status.value != "CONFIRMED":
                    status = other.status

            merged_h = Hypothesis(
                hypothesis_id=primary.hypothesis_id,
                description=desc,
                failure_domain=primary.failure_domain,
                failure_mechanism=primary.failure_mechanism,
                supporting=list(sup_ids.values()),
                contradicting=list(con_ids.values()),
                status=status,
                contributing_factors=list(dict.fromkeys(factors)),
                flaky_characteristic=flaky,
                suggested_by="+".join(dict.fromkeys(sources)),
                probe_feedback=probe_fb,
                excluded_by=excluded,
            )
            # LLM 候选合并后仍不得 CONFIRMED（引擎层兜底之外的第一层）
            if (
                any(str(s).lower().startswith("llm") for s in sources)
                and status.value == "CONFIRMED"
            ):
                merged_h.status = type(status)("SUPPORTED")
                merged_h.contributing_factors.append(
                    "[dedup] 含 LLM 来源的合并候选不得 CONFIRMED，保持 SUPPORTED 供人工复核"
                )
            out.append(merged_h)
            audit_groups.append(
                DedupGroup(
                    mechanism=mech,
                    canonical_description=_normalize(desc),
                    kept_hypothesis_id=primary.hypothesis_id,
                    merged_hypothesis_ids=merged_ids,
                    sources=sources,
                    merged_from=merged_from,
                )
            )

    audit = {
        "threshold": threshold,
        "input_count": len(hypotheses),
        "output_count": len(out),
        "untouched": untouched,
        "merged_groups": [g.to_dict() for g in audit_groups],
        "order_invariant": True,
        "note": (
            "按 failure_mechanism 分组 + 规范化描述相似度合并近似重复候选；"
            "证据引用并集去重（同一 Evidence 不重复计独立支持）；"
            "原始候选记录保留在 merged_from；合并顺序与输入顺序无关"
        ),
    }
    return out, audit


__all__ = ["SIMILARITY_THRESHOLD", "DedupGroup", "dedup_hypotheses"]
