"""探针证据回灌（RUN-006 WP1-02）：把探针结果变成对假设的支持/反驳/排除。

为什么要单独一层：
- 探针执行本身只产生"事实"（证据），不能自己宣布根因；
- 判定必须由确定性规则完成（LLM 只提候选假设，不得 CONFIRMED）；
- 探针失败/被拒绝 ≠ 目标系统故障证据，只能记为"未提供证据"。

规则（保守、可解释、可复核）：
| 探针 | 观测 | 对假设的作用 |
|---|---|---|
| http_state | 独立状态 count>=1 | 反驳 PERSISTENCE；支持"界面未渲染"（UI 成功而断言失败）→ 排除"未落库" |
| http_state | count==0 | 确认 PERSISTENCE（directly_confirming） |
| network_trace | 覆盖失败步骤且无 5xx | 反驳 API 5xx 假设；排除该假设 |
| network_trace | 覆盖失败步骤且有 5xx | 支持 API 5xx（directly_confirming） |
| network_trace | 无失败请求（有覆盖） | 反驳 NETWORK 假设 |
| dom_snapshot | 期望 test_id 存在 | 反驳"未渲染"；支持"定位策略/用例资产问题" |
| dom_snapshot | 期望 test_id 缺失 | 支持"未渲染"；反驳"定位策略" |
| backend_log | 出现 ERROR/Traceback/500 | 支持 API/产品缺陷（不 confirm） |
| 任意探针 FAILED/TIMED_OUT/BLOCKED | — | 不产生任何支持/反驳证据 |
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

SUPPORT = "SUPPORT"
CONTRADICT = "CONTRADICT"
NONE = "NONE"

_ERROR_PATTERN = re.compile(r"\b(ERROR|CRITICAL|Traceback|Exception|HTTP/1\.1\" 5\d\d)\b")


@dataclass
class ProbeSignal:
    """一次探针执行的可判定事实。"""

    probe_id: str
    execution_id: str
    status: str  # SUCCEEDED / FAILED / TIMED_OUT / BLOCKED
    evidence_ids: list[str] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)
    params: dict[str, Any] = field(default_factory=dict)
    gap_id: str = ""


@dataclass
class HypothesisUpdate:
    hypothesis_id: str
    relation: str
    confirming: bool
    evidence_ids: list[str]
    exclude: bool
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "hypothesis_id": self.hypothesis_id,
            "relation": self.relation,
            "confirming": self.confirming,
            "evidence_ids": list(self.evidence_ids),
            "exclude": self.exclude,
            "reason": self.reason,
        }


@dataclass
class FeedbackResult:
    probe_id: str
    execution_id: str
    status: str
    updates: list[HypothesisUpdate] = field(default_factory=list)
    skipped_reason: str = ""
    notes: dict[str, Any] = field(default_factory=dict)

    @property
    def produced_evidence(self) -> bool:
        return bool(self.updates)

    def to_dict(self) -> dict[str, Any]:
        return {
            "probe_id": self.probe_id,
            "execution_id": self.execution_id,
            "status": self.status,
            "updates": [u.to_dict() for u in self.updates],
            "skipped_reason": self.skipped_reason,
            "notes": dict(self.notes),
            "claimed_as_target_failure": False,
        }


# --------------------------------------------------------------------- helpers
def _state_count(signal: ProbeSignal) -> int | None:
    response = (signal.data or {}).get("response") or {}
    if not isinstance(response, dict):
        return None
    count = response.get("count")
    if isinstance(count, int):
        return count
    projects = response.get("projects")
    if isinstance(projects, list):
        return len(projects)
    keys = response.get("keys")
    if isinstance(keys, list):
        return len(keys)
    return None


def _failed_step_ids(run_report: dict[str, Any]) -> set[str]:
    return {
        str(s.get("step_id"))
        for s in (run_report.get("steps") or [])
        if s.get("status") == "FAILED"
    }


def _expected_test_ids(run_report: dict[str, Any]) -> set[str]:
    """从失败步骤里提取期望出现的 test_id（用于 DOM 判定）。"""
    ids: set[str] = set()
    for s in run_report.get("steps") or []:
        if s.get("action") != "assert":
            continue
        target = s.get("target") or {}
        if str(target.get("strategy") or "") == "test_id" and target.get("value"):
            ids.add(str(target["value"]))
        if s.get("value"):
            ids.add(str(s["value"]))
    return ids


def _network_entries(signal: ProbeSignal) -> list[dict[str, Any]]:
    data = signal.data or {}
    entries = data.get("entries")
    if isinstance(entries, list):
        return [e for e in entries if isinstance(e, dict)]
    # 也可能是 report 里按步骤分组
    out: list[dict[str, Any]] = []
    for step in data.get("steps") or []:
        for n in (step.get("observation") or {}).get("network") or []:
            out.append({**n, "step_id": step.get("step_id")})
    return out


def _hypotheses_by_mechanism(
    hypotheses: list[dict[str, Any]], *mechanisms: str
) -> list[dict[str, Any]]:
    return [h for h in hypotheses if h.get("failure_mechanism") in mechanisms]


# ------------------------------------------------------------------ assessment
def assess(
    signal: ProbeSignal, hypotheses: list[dict[str, Any]], run_report: dict[str, Any]
) -> FeedbackResult:
    """把一次探针结果映射成对现有假设的支持/反驳/排除。"""
    result = FeedbackResult(
        probe_id=signal.probe_id, execution_id=signal.execution_id, status=signal.status
    )
    if signal.status != "SUCCEEDED":
        # 关键安全约束：探针本身失败不等于被测系统出问题
        result.skipped_reason = f"PROBE_{signal.status}_NOT_EVIDENCE"
        return result
    if not signal.evidence_ids:
        result.skipped_reason = "PROBE_NO_EVIDENCE_PERSISTED"
        return result

    evidence_id = signal.evidence_ids[0]
    failed_steps = _failed_step_ids(run_report)

    if signal.probe_id == "http_state":
        count = _state_count(signal)
        result.notes["state_count"] = count
        if count is None:
            result.skipped_reason = "STATE_COUNT_UNPARSEABLE"
            return result
        for h in _hypotheses_by_mechanism(hypotheses, "PERSISTENCE", "UI_RENDERING"):
            hid = str(h["hypothesis_id"])
            mech = h.get("failure_mechanism")
            if mech == "PERSISTENCE":
                if count == 0:
                    result.updates.append(
                        HypothesisUpdate(
                            hid,
                            SUPPORT,
                            True,
                            [evidence_id],
                            False,
                            "独立状态查询 count=0：业务数据确实未持久化",
                        )
                    )
                else:
                    result.updates.append(
                        HypothesisUpdate(
                            hid,
                            CONTRADICT,
                            False,
                            [evidence_id],
                            True,
                            f"独立状态查询 count={count}：数据已存在，排除'未持久化'",
                        )
                    )
            else:  # UI_RENDERING
                if count >= 1:
                    result.updates.append(
                        HypothesisUpdate(
                            hid,
                            SUPPORT,
                            True,
                            [evidence_id],
                            False,
                            f"业务数据已存在（count={count}）但界面断言失败：支持渲染缺陷",
                        )
                    )
                else:
                    result.updates.append(
                        HypothesisUpdate(
                            hid,
                            CONTRADICT,
                            False,
                            [evidence_id],
                            False,
                            "业务数据不存在，渲染缺陷假设不成立",
                        )
                    )
        return result

    if signal.probe_id == "network_trace":
        entries = _network_entries(signal)
        covered = {str(e.get("step_id")) for e in entries if e.get("step_id")}
        result.notes.update(
            {
                "entries": len(entries),
                "covered_steps": sorted(covered),
                "failed_steps": sorted(failed_steps),
            }
        )
        if not entries:
            result.skipped_reason = "NO_NETWORK_OBSERVATION"
            return result
        has_5xx = any(int(e.get("status") or 0) >= 500 for e in entries)
        has_failure = any(bool(e.get("failure")) for e in entries)
        covers_failure = bool(covered & failed_steps) or (not failed_steps and bool(entries))

        for h in _hypotheses_by_mechanism(hypotheses, "API"):
            hid = str(h["hypothesis_id"])
            if has_5xx:
                result.updates.append(
                    HypothesisUpdate(
                        hid,
                        SUPPORT,
                        True,
                        [evidence_id],
                        False,
                        "网络观测中存在 5xx，支持接口故障假设",
                    )
                )
            elif covers_failure:
                result.updates.append(
                    HypothesisUpdate(
                        hid,
                        CONTRADICT,
                        False,
                        [evidence_id],
                        True,
                        "网络观测覆盖失败步骤且无 5xx，排除'接口返回 5xx'",
                    )
                )
        for h in _hypotheses_by_mechanism(hypotheses, "NETWORK"):
            hid = str(h["hypothesis_id"])
            if has_failure:
                result.updates.append(
                    HypothesisUpdate(
                        hid,
                        SUPPORT,
                        True,
                        [evidence_id],
                        False,
                        "网络观测存在失败请求，支持链路问题假设",
                    )
                )
            else:
                result.updates.append(
                    HypothesisUpdate(
                        hid,
                        CONTRADICT,
                        False,
                        [evidence_id],
                        False,
                        "网络观测无失败请求，网络层问题假设不成立",
                    )
                )
        return result

    if signal.probe_id == "dom_snapshot":
        excerpt = str((signal.data or {}).get("excerpt") or "")
        expected = _expected_test_ids(run_report)
        present = {tid for tid in expected if f'"{tid}"' in excerpt or f"'{tid}'" in excerpt}
        missing = expected - present
        result.notes.update(
            {
                "expected_test_ids": sorted(expected),
                "present": sorted(present),
                "missing": sorted(missing),
            }
        )
        if not expected:
            result.skipped_reason = "NO_EXPECTED_ELEMENT_TO_CHECK"
            return result
        for h in _hypotheses_by_mechanism(hypotheses, "LOCATOR"):
            hid = str(h["hypothesis_id"])
            if missing:
                result.updates.append(
                    HypothesisUpdate(
                        hid,
                        CONTRADICT,
                        False,
                        [evidence_id],
                        False,
                        f"DOM 中不存在期望元素 {sorted(missing)}，定位策略不是主因",
                    )
                )
            else:
                result.updates.append(
                    HypothesisUpdate(
                        hid,
                        SUPPORT,
                        False,
                        [evidence_id],
                        False,
                        "DOM 中存在全部期望元素，更可能是定位策略/等待条件问题",
                    )
                )
        for h in _hypotheses_by_mechanism(hypotheses, "UI_RENDERING"):
            hid = str(h["hypothesis_id"])
            if missing:
                result.updates.append(
                    HypothesisUpdate(
                        hid,
                        SUPPORT,
                        False,
                        [evidence_id],
                        False,
                        f"DOM 缺失期望元素 {sorted(missing)}，支持渲染缺陷",
                    )
                )
            else:
                result.updates.append(
                    HypothesisUpdate(
                        hid,
                        CONTRADICT,
                        False,
                        [evidence_id],
                        False,
                        "DOM 已渲染期望元素，'未渲染'假设不成立",
                    )
                )
        return result

    if signal.probe_id == "backend_log":
        lines = signal.data.get("lines") or []
        text = "\n".join(str(x) for x in lines) if isinstance(lines, list) else str(lines)
        hit = bool(_ERROR_PATTERN.search(text))
        result.notes.update(
            {"log_lines": len(lines) if isinstance(lines, list) else 1, "error_pattern": hit}
        )
        if not hit:
            result.skipped_reason = "NO_SERVER_ERROR_IN_LOG"
            return result
        for h in _hypotheses_by_mechanism(hypotheses, "API", "PERSISTENCE"):
            result.updates.append(
                HypothesisUpdate(
                    str(h["hypothesis_id"]),
                    SUPPORT,
                    False,
                    [evidence_id],
                    False,
                    "后端日志出现异常/错误，支持服务端故障方向（不单独确认根因）",
                )
            )
        return result

    result.skipped_reason = "PROBE_TYPE_NOT_RECOGNIZED"
    return result


def merge_signals(
    hypotheses: list[dict[str, Any]],
    signals: list[ProbeSignal],
    run_report: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[FeedbackResult]]:
    """把多条探针信号合并进假设（返回新的假设字典列表，不改原对象）。"""
    merged = [
        {
            **h,
            "supporting": [dict(x) for x in h.get("supporting") or []],
            "contradicting": [dict(x) for x in h.get("contradicting") or []],
            "probe_feedback": [dict(x) for x in h.get("probe_feedback") or []],
            "excluded_by": list(h.get("excluded_by") or []),
        }
        for h in hypotheses
    ]
    index = {str(h["hypothesis_id"]): h for h in merged}
    results: list[FeedbackResult] = []
    for signal in signals:
        assessment = assess(signal, merged, run_report)
        results.append(assessment)
        for update in assessment.updates:
            h = index.get(update.hypothesis_id)
            if h is None:
                continue
            bucket = "supporting" if update.relation == SUPPORT else "contradicting"
            existing = {str(x.get("id")) for x in h[bucket]}
            for eid in update.evidence_ids:
                if eid in existing:
                    continue
                ref = {"id": eid, "trust": "TRUSTED"}
                if bucket == "supporting":
                    ref["confirming"] = update.confirming
                h[bucket].append(ref)
                existing.add(eid)
            if update.exclude:
                h["excluded_by"].append(
                    {
                        "probe_id": signal.probe_id,
                        "evidence_id": update.evidence_ids[0],
                        "reason": update.reason,
                    }
                )
            h["probe_feedback"].append(
                {
                    "probe_id": signal.probe_id,
                    "execution_id": signal.execution_id,
                    "relation": update.relation,
                    "confirming": update.confirming,
                    "exclude": update.exclude,
                    "reason": update.reason,
                }
            )
            h.setdefault("contributing_factors", []).append(f"[{signal.probe_id}] {update.reason}")
    return merged, results


__all__ = [
    "CONTRADICT",
    "NONE",
    "SUPPORT",
    "FeedbackResult",
    "HypothesisUpdate",
    "ProbeSignal",
    "assess",
    "merge_signals",
]
