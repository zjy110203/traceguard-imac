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
| 任意探针 FAILED/TIMED_OUT/BLOCKED/UNKNOWN | — | 不产生任何支持/反驳证据 |

RUN-018 WP5 确定性规则补充（§5.3）：
- Probe PASS（SUCCEEDED）本身**不能**自动确认任何根因——确认只来自证据内容
  （TRUSTED + directly_confirming）经确定性规则裁决，单查询 count=0 亦然（R1-04）；
- Probe FAIL / UNKNOWN / TIMED_OUT 一律不产生证据、不改变假设、不提升置信度；
- 存在 5xx 的 Run 里，"界面未渲染"可能只是接口失败的**后果**：
  http_state 探针不得据此确权 UI_RENDERING（症状不能冒充根因）。
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
    """一次探针执行的可判定事实。

    RUN-010 WP4 时间对齐字段：
    - observed_at：探针观测发生时刻（epoch 秒；0 = 未知）；
    - run_scope：证据是否来自 Run 执行作用域（True=Run 内采集；
      False=Run 结束后的事后观测，只能反映"当前状态"，不能冒充"历史状态"）。
    """

    probe_id: str
    execution_id: str
    status: str  # SUCCEEDED / FAILED / TIMED_OUT / BLOCKED
    evidence_ids: list[str] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)
    params: dict[str, Any] = field(default_factory=dict)
    gap_id: str = ""
    observed_at: float = 0.0
    run_scope: bool = False


#: 时间对齐标记（RUN-011-R1 WP2 三态语义，决策 R1-01）：
#: - ALIGNED：有证据证明观测与 Run 时间窗口对齐，可作历史状态证据；
#: - POST_HOC：明确发生在 Run 结束之后，只能反映"当前状态"；
#: - UNKNOWN：无法确定观测时间。**时间未知 ≠ 时间已对齐**：
#:   UNKNOWN 不得作为 CONFIRMED / EXCLUDED 的权威依据（决策 R1-02）。
ALIGNED = "ALIGNED"
POST_HOC = "POST_HOC"
UNKNOWN = "UNKNOWN"


@dataclass
class HypothesisUpdate:
    hypothesis_id: str
    relation: str
    confirming: bool
    evidence_ids: list[str]
    exclude: bool
    reason: str
    time_alignment: str = ALIGNED

    @property
    def post_hoc(self) -> bool:
        return self.time_alignment == POST_HOC

    def to_dict(self) -> dict[str, Any]:
        return {
            "hypothesis_id": self.hypothesis_id,
            "relation": self.relation,
            "confirming": self.confirming,
            "evidence_ids": list(self.evidence_ids),
            "exclude": self.exclude,
            "reason": self.reason,
            "time_alignment": self.time_alignment,
        }


@dataclass
class FeedbackResult:
    probe_id: str
    execution_id: str
    status: str
    updates: list[HypothesisUpdate] = field(default_factory=list)
    skipped_reason: str = ""
    notes: dict[str, Any] = field(default_factory=dict)
    time_alignment: str = ALIGNED
    #: POST_HOC 信号与 Run 作用域证据冲突时产生的"新假设"（如写入延迟/TIMING）
    new_hypotheses: list[dict[str, Any]] = field(default_factory=list)

    @property
    def produced_evidence(self) -> bool:
        return bool(self.updates or self.new_hypotheses)

    def to_dict(self) -> dict[str, Any]:
        return {
            "probe_id": self.probe_id,
            "execution_id": self.execution_id,
            "status": self.status,
            "updates": [u.to_dict() for u in self.updates],
            "skipped_reason": self.skipped_reason,
            "notes": dict(self.notes),
            "time_alignment": self.time_alignment,
            "new_hypotheses": [dict(x) for x in self.new_hypotheses],
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


def _run_has_5xx(run_report: dict[str, Any]) -> bool:
    """Run 报告中是否存在接口 5xx（RUN-018 WP5：UI 缺失可能是接口失败的后果）。"""
    for s in run_report.get("steps") or []:
        obs = s.get("observation") or {}
        for he in obs.get("http_errors") or []:
            if int(he.get("status") or 0) >= 500:
                return True
        for ne in obs.get("network") or []:
            if int(ne.get("status") or 0) >= 500:
                return True
    return False


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


def _targets_by_origin(run_report: dict[str, Any]) -> tuple[set[str], set[str]]:
    """把期望元素按"来源"拆开：失败步骤的目标 vs 断言目标。

    RUN-011 WP2/WP3 修复（真实缺陷）：
    定位器失效场景里失败发生在 click/fill 步骤，断言步骤根本没执行，
    因此 `_expected_test_ids()`（只看 assert 步骤）返回空集 → DOM 探针
    直接 skipped，无法给出任何机制特异性证据，LOCATOR 与 NETWORK 继续混淆。
    这里把失败步骤的 target test_id 单独提取出来：它不在 DOM 中，
    直接说明"定位器无法解析目标元素"，是 LOCATOR 的支持证据，
    而不能反推成"页面没渲染"。
    """
    failed_steps: set[str] = set()
    for s in run_report.get("steps") or []:
        if s.get("status") != "FAILED" or s.get("action") == "assert":
            continue
        target = s.get("target") or {}
        if str(target.get("strategy") or "") == "test_id" and target.get("value"):
            failed_steps.add(str(target["value"]))
    assertion_ids: set[str] = set()
    for s in run_report.get("steps") or []:
        if s.get("action") != "assert":
            continue
        target = s.get("target") or {}
        if str(target.get("strategy") or "") == "test_id" and target.get("value"):
            assertion_ids.add(str(target["value"]))
        if s.get("value"):
            assertion_ids.add(str(s["value"]))
    for o in run_report.get("assertion_outcomes") or []:
        expected = o.get("expected") or {}
        if expected.get("test_id"):
            assertion_ids.add(str(expected["test_id"]))
    return failed_steps - assertion_ids, assertion_ids


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
def resolve_alignment(signal: ProbeSignal, run_ended_at: float | None) -> tuple[str, str]:
    """判定探针观测与 Run 历史状态的时间对齐性（RUN-011-R1 WP2 三态）。

    - run_scope=True：Run 作用域内采集 → ALIGNED；
    - 事后观测（observed_at > run_ended_at）：只能反映"当前状态" → POST_HOC；
    - 时间未知（observed_at=0 或 run_ended_at 缺失）→ **UNKNOWN**：
      时间未知不等于时间已对齐（决策 R1-01）；UNKNOWN 不作权威确认/排除依据。
    """
    if signal.run_scope:
        return ALIGNED, ""
    if run_ended_at and signal.observed_at and signal.observed_at > run_ended_at:
        return POST_HOC, (
            f"probe observed_at={signal.observed_at:.0f} > run_ended_at={run_ended_at:.0f}: "
            "事后观测反映当前状态，不能冒充 Run 时历史状态"
        )
    return UNKNOWN, (
        "unknown_timing: 观测时间无法确定（时间未知 ≠ 时间已对齐），"
        "降权为 UNKNOWN：不作为 CONFIRMED/EXCLUDED 权威依据"
    )


def assess(
    signal: ProbeSignal,
    hypotheses: list[dict[str, Any]],
    run_report: dict[str, Any],
    *,
    run_ended_at: float | None = None,
) -> FeedbackResult:
    """把一次探针结果映射成对现有假设的支持/反驳/排除。

    RUN-010 WP4：所有更新带 time_alignment。
    RUN-011-R1 WP2（三态语义）：POST_HOC 与 UNKNOWN 的裁决权降级：
    - 不得作为权威确认（confirming 强制 False）；
    - 不得单独排除假设（exclude 强制 False，只记反驳）；
    - 事后状态与 Run 时假设冲突时 → 生成"状态查询滞后（TIMING）"新假设
      （仅 POST_HOC：已知发生在 Run 后；UNKNOWN 不生成，时间关系本身未知）；
    - UNKNOWN 的 trust 标记 TIME_UNKNOWN（不持有 TRUSTED）。
    """
    alignment, alignment_note = resolve_alignment(signal, run_ended_at)
    result = FeedbackResult(
        probe_id=signal.probe_id,
        execution_id=signal.execution_id,
        status=signal.status,
        time_alignment=alignment,
    )
    if alignment_note:
        result.notes["alignment"] = alignment_note
    if signal.status != "SUCCEEDED":
        # 关键安全约束：探针本身失败 ≠ 被测系统出问题。
        # FAILED / TIMED_OUT / BLOCKED / UNKNOWN（探针不可用/无法判定）统一处理：
        # 零证据、零假设变更、置信度不变（RUN-018 WP5 §5.3）。
        result.skipped_reason = f"PROBE_{signal.status}_NOT_EVIDENCE"
        return result
    if not signal.evidence_ids:
        result.skipped_reason = "PROBE_NO_EVIDENCE_PERSISTED"
        return result

    evidence_id = signal.evidence_ids[0]
    failed_steps = _failed_step_ids(run_report)

    def upd(
        hid: str,
        relation: str,
        confirming: bool,
        exclude: bool,
        reason: str,
    ) -> HypothesisUpdate:
        # POST_HOC / UNKNOWN 裁决权降级（见 docstring；决策 R1-02）
        if alignment == POST_HOC:
            confirming = False
            exclude = False
            reason = f"[POST_HOC] {reason}"
        elif alignment == UNKNOWN:
            confirming = False
            exclude = False
            reason = f"[TIME_UNKNOWN] {reason}"
        return HypothesisUpdate(
            hid, relation, confirming, [evidence_id], exclude, reason, time_alignment=alignment
        )

    if signal.probe_id == "http_state":
        count = _state_count(signal)
        result.notes["state_count"] = count
        if count is None:
            result.skipped_reason = "STATE_COUNT_UNPARSEABLE"
            return result
        # RUN-018 WP5 §5.4 场景 C：接口 5xx 存在时，界面未显示成功只是失败的
        # **后果**——不得据此再次确权 UI_RENDERING（症状不能冒充根因）。
        run_has_5xx = _run_has_5xx(run_report)
        result.notes["run_has_5xx"] = run_has_5xx
        for h in _hypotheses_by_mechanism(hypotheses, "PERSISTENCE", "UI_RENDERING"):
            hid = str(h["hypothesis_id"])
            mech = h.get("failure_mechanism")
            if mech == "PERSISTENCE":
                if count == 0:
                    # RUN-011-R1 WP2（决策 R1-04）：单次 count=0 不直接确认永久性
                    # 持久化故障（TF-DB-004 延迟场景下单查询会误判）。
                    # 只作支持证据（候选级）；确认须业务断言级证据
                    # （diagnosis_service 的 failed_biz 规则）或多点一致证据。
                    result.updates.append(
                        upd(
                            hid,
                            "SUPPORT",
                            False,
                            False,
                            "独立状态查询 count=0：业务数据未见持久化（单查询仅支持，"
                            "不直接确认永久性故障；延迟复测后再评估 PERSISTENCE/TIMING）",
                        )
                    )
                else:
                    result.updates.append(
                        upd(
                            hid,
                            CONTRADICT,
                            False,
                            True,
                            f"独立状态查询 count={count}：数据已存在，排除'未持久化'",
                        )
                    )
                    # RUN-010 WP4：事后观测与 Run 时"未落库"假设冲突 →
                    # 不是错误反驳，而是生成 TIMING（写入延迟/查询滞后）新假设
                    if alignment == POST_HOC:
                        result.new_hypotheses.append(
                            {
                                "description": (
                                    "业务状态查询滞后（写入延迟）：Run 作用域证据显示未落库，"
                                    "事后探针显示数据已存在"
                                ),
                                "failure_domain": "TEST_EXECUTION",
                                "failure_mechanism": "TIMING",
                                "supporting": [evidence_id],
                                "time_alignment": POST_HOC,
                                "suggested_by": "rule:probe-timing",
                                "reason": (
                                    f"Run 时未持久化（假设 PERSISTENCE），探针事后观测 count={count}"
                                ),
                            }
                        )
            else:  # UI_RENDERING
                if count >= 1:
                    if run_has_5xx:
                        # 5xx 场景：UI 缺失可能是接口失败的后果，只记方向性支持，不确权
                        result.updates.append(
                            upd(
                                hid,
                                SUPPORT,
                                False,
                                False,
                                f"业务数据已存在（count={count}）但界面断言失败：方向性支持渲染缺陷，"
                                "但 Run 存在接口 5xx（界面缺失可能是接口失败的后果），不作为确认依据",
                            )
                        )
                    else:
                        result.updates.append(
                            upd(
                                hid,
                                SUPPORT,
                                True,
                                False,
                                f"业务数据已存在（count={count}）但界面断言失败：支持渲染缺陷",
                            )
                        )
                else:
                    result.updates.append(
                        upd(
                            hid,
                            CONTRADICT,
                            False,
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
                    upd(hid, SUPPORT, True, False, "网络观测中存在 5xx，支持接口故障假设")
                )
            elif covers_failure:
                result.updates.append(
                    upd(
                        hid,
                        CONTRADICT,
                        False,
                        True,
                        "网络观测覆盖失败步骤且无 5xx，排除'接口返回 5xx'",
                    )
                )
        for h in _hypotheses_by_mechanism(hypotheses, "NETWORK"):
            hid = str(h["hypothesis_id"])
            if has_failure:
                result.updates.append(
                    upd(hid, SUPPORT, False, False, "网络观测存在失败请求，支持链路问题假设")
                )
            else:
                result.updates.append(
                    upd(hid, CONTRADICT, False, False, "网络观测无失败请求，网络层问题假设不成立")
                )
        return result

    if signal.probe_id == "dom_snapshot":
        excerpt = str((signal.data or {}).get("excerpt") or "")
        failed_targets, expected = _targets_by_origin(run_report)
        present = {
            tid
            for tid in expected | failed_targets
            if f'"{tid}"' in excerpt or f"'{tid}'" in excerpt
        }
        missing = expected - present
        missing_failed = failed_targets - present
        result.notes.update(
            {
                "expected_test_ids": sorted(expected),
                "failed_step_targets": sorted(failed_targets),
                "present": sorted(present),
                "missing": sorted(missing),
                "missing_failed_step_targets": sorted(missing_failed),
            }
        )
        if not expected and not failed_targets:
            result.skipped_reason = "NO_EXPECTED_ELEMENT_TO_CHECK"
            return result
        # 失败步骤的目标元素不在 DOM 中：这是"定位器无法解析"的直接证据，
        # 支持 LOCATOR；此时不能把"缺失"解释成渲染缺陷（会造成 LOCATOR/NETWORK 混淆）。
        if missing_failed:
            for h in _hypotheses_by_mechanism(hypotheses, "LOCATOR"):
                result.updates.append(
                    upd(
                        str(h["hypothesis_id"]),
                        SUPPORT,
                        False,
                        False,
                        f"失败步骤的目标元素 {sorted(missing_failed)} 不在 DOM 中：定位器无法解析，"
                        "属于用例资产/定位策略问题",
                    )
                )
        for h in _hypotheses_by_mechanism(hypotheses, "LOCATOR"):
            # 失败步骤目标缺失时，已经给出 LOCATOR 的直接支持；
            # 不能再同时用"断言元素都存在"得出相反结论（会自相矛盾）。
            if not expected or missing_failed:
                break
            hid = str(h["hypothesis_id"])
            if missing:
                result.updates.append(
                    upd(
                        hid,
                        CONTRADICT,
                        False,
                        False,
                        f"DOM 中不存在期望元素 {sorted(missing)}，定位策略不是主因",
                    )
                )
            else:
                result.updates.append(
                    upd(
                        hid,
                        SUPPORT,
                        False,
                        False,
                        "DOM 中存在全部期望元素，更可能是定位策略/等待条件问题",
                    )
                )
        for h in _hypotheses_by_mechanism(hypotheses, "UI_RENDERING"):
            if not expected:
                break
            hid = str(h["hypothesis_id"])
            if missing:
                result.updates.append(
                    upd(
                        hid,
                        SUPPORT,
                        False,
                        False,
                        f"DOM 缺失期望元素 {sorted(missing)}，支持渲染缺陷",
                    )
                )
            else:
                result.updates.append(
                    upd(hid, CONTRADICT, False, False, "DOM 已渲染期望元素，'未渲染'假设不成立")
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
                upd(
                    str(h["hypothesis_id"]),
                    SUPPORT,
                    False,
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
    *,
    run_ended_at: float | None = None,
) -> tuple[list[dict[str, Any]], list[FeedbackResult]]:
    """把多条探针信号合并进假设（返回新的假设字典列表，不改原对象）。

    RUN-010 WP4：
    - POST_HOC（事后观测）证据以 trust="POST_HOC" 附着 —— 不满足 CONFIRMED 的
      TRUSTED 权威门槛，也不触发排除（时间未对齐的裁决记为 INCONCLUSIVE 方向）；
    - 探针结果（含 skipped）全部真实回灌到 probe_feedback，回灌率可审计；
    - POST_HOC 与 Run 作用域假设冲突时，assess() 生成的 TIMING 新假设在此
      追加进假设列表（由确定性规则提出，供引擎重新裁决）。
    """
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
    next_hyp_seq = len(merged)
    for signal in signals:
        assessment = assess(signal, merged, run_report, run_ended_at=run_ended_at)
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
                # POST_HOC / TIME_UNKNOWN 证据不持有 TRUSTED：
                # 无法用于权威确认/排除（决策 R1-02）
                if update.post_hoc:
                    trust = "POST_HOC"
                elif update.time_alignment == UNKNOWN:
                    trust = "TIME_UNKNOWN"
                else:
                    trust = "TRUSTED"
                ref = {
                    "id": eid,
                    "trust": trust,
                    "time_alignment": update.time_alignment,
                }
                if bucket == "supporting":
                    ref["confirming"] = update.confirming
                h[bucket].append(ref)
                existing.add(eid)
            if update.exclude and not update.post_hoc:
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
                    "time_alignment": update.time_alignment,
                }
            )
            h.setdefault("contributing_factors", []).append(f"[{signal.probe_id}] {update.reason}")
        # 新假设追加（确定性规则提出；LLM 语义不适用——这是规则内核自生成）
        for cand in assessment.new_hypotheses:
            hid = f"hyp_probe_new_{next_hyp_seq + 1}"
            next_hyp_seq += 1
            hyp = {
                "hypothesis_id": hid,
                "description": cand.get("description", ""),
                "failure_domain": cand.get("failure_domain", "UNKNOWN"),
                "failure_mechanism": cand.get("failure_mechanism", "UNKNOWN"),
                "supporting": [
                    {
                        "id": eid,
                        "trust": "POST_HOC",
                        "confirming": False,
                        "time_alignment": POST_HOC,
                    }
                    for eid in cand.get("supporting", [])
                ],
                "contradicting": [],
                "status": "CANDIDATE",
                "suggested_by": cand.get("suggested_by", "rule:probe"),
                "probe_feedback": [
                    {
                        "probe_id": signal.probe_id,
                        "relation": SUPPORT,
                        "reason": cand.get("reason", ""),
                        "time_alignment": POST_HOC,
                    }
                ],
            }
            merged.append(hyp)
            index[hid] = hyp
    return merged, results


__all__ = [
    "ALIGNED",
    "CONTRADICT",
    "NONE",
    "POST_HOC",
    "SUPPORT",
    "UNKNOWN",
    "FeedbackResult",
    "HypothesisUpdate",
    "ProbeSignal",
    "assess",
    "merge_signals",
    "resolve_alignment",
]
