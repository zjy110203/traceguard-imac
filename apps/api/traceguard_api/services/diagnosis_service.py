"""诊断服务：把真实 Run 证据转成确定性 Hypothesis，再由 DiagnosisEngine 裁决。

不变量：
- Hypothesis 必须引用真实 evidence_id（不允许凭空创造证据）；
- CONFIRMED 只能由 Deterministic Rule + TRUSTED/directly_confirming 证据产生；
- 无足够证据 → INCONCLUSIVE，且 Disposition = NEEDS_MORE_EVIDENCE（不送 Repair）；
- Flaky 是稳定性特征，不当作根因。
"""

from __future__ import annotations

import json
import re
from typing import Any

from traceguard_core.errors import AppError, ErrorCode
from traceguard_core.ids import new_id, utc_now_iso
from traceguard_core.logging import get_logger
from traceguard_evidence.diagnosis.engine import (
    DiagnosisEngine,
    EvidenceRef,
    FailureDomain,
    FailureMechanism,
    Hypothesis,
    HypothesisStatus,
    hypothesis_confidence,
)
from traceguard_evidence.probe.feedback import ProbeSignal, merge_signals

from traceguard_api.runtime import Runtime

logger = get_logger("traceguard.service.diagnosis")

DOMAIN_UI = FailureDomain.PRODUCT
DOMAIN_ENV = FailureDomain.ENVIRONMENT
DOMAIN_ASSET = FailureDomain.TEST_ASSET
DOMAIN_EXEC = FailureDomain.TEST_EXECUTION


DISPOSITION_MAP = {
    "PRODUCT_DEFECT": "PRODUCT_DEFECT",
    "TEST_ASSET_ISSUE": "TEST_ASSET",
    "TEST_EXECUTION_ISSUE": "TEST_EXECUTION",
    "ENVIRONMENT_ISSUE": "ENVIRONMENT",
    "AGENT_ISSUE": "AGENT",
    "PLATFORM_ISSUE": "PLATFORM",
    "NEEDS_MORE_EVIDENCE": "NEEDS_MORE_EVIDENCE",
    "MANUAL_REVIEW": "MANUAL_REVIEW",
}


class DiagnosisService:
    def __init__(self, rt: Runtime) -> None:
        self.rt = rt
        self.engine = DiagnosisEngine()

    def diagnose(self, run_id: str, *, use_llm_advisor: bool | None = None) -> dict[str, Any]:
        """执行诊断。

        use_llm_advisor: None=按全局配置；True/False=显式指定（Benchmark 分组用，
        保证规则组与 LLM 组输入隔离、口径纯净）。
        """
        run = self.rt.tasks.get_run(run_id)
        if not run:
            raise AppError(ErrorCode.NOT_FOUND, "run not found")
        report = run.get("report") or {}
        if run["test_verdict"] == "PASSED":
            raise AppError(
                ErrorCode.PRECONDITION_FAILED, "run PASSED：无失败症状可诊断（不做伪诊断）"
            )
        if run["test_verdict"] == "NOT_EVALUATED":
            # RUN-010 WP4/5：有失败步骤证据的 NOT_EVALUATED 允许进入规则诊断——
            # 结论仍是"方向性支持"（引擎不会 CONFIRMED：断言未评估），
            # 输出 PROBABLE/INCONCLUSIVE + NEEDS_MORE_EVIDENCE 处置，不冒充结论。
            if not report.get("steps"):
                raise AppError(
                    ErrorCode.PRECONDITION_FAILED,
                    "run NOT_EVALUATED 且无步骤证据：只能补证据，不能下结论",
                )
        if run["execution_status"] != "COMPLETED" and not report.get("steps"):
            raise AppError(
                ErrorCode.PRECONDITION_FAILED, "run has no executable evidence (failed/cancelled)"
            )

        evidence = self.rt.evidence.list(run_id=run_id, limit=500)
        by_step: dict[str, list[Any]] = {}
        by_step_network: dict[str, list[Any]] = {}
        state_evidence: list[Any] = []
        for e in evidence:
            step_id = str((e.content or {}).get("step_id", ""))
            if step_id:
                by_step.setdefault(step_id, []).append(e)
                if e.source_type == "network":
                    by_step_network.setdefault(step_id, []).append(e)
            if e.source_type == "api" and "state_query" in str(e.source_ref):
                state_evidence.append(e)

        steps: list[dict[str, Any]] = report.get("steps") or []
        outcomes: list[dict[str, Any]] = report.get("assertion_outcomes") or []
        tc = self.rt.catalog.get_testcase(run["testcase_version_id"].split("@")[0].split(":")[0])
        assert_kinds: dict[str, str] = {}
        if tc and tc.get("latest"):
            for a in tc["latest"].get("assertions", []):
                assert_kinds[a["assertion_id"]] = a.get("kind", "ui")
        # 报告自带 kind 时作为兜底（用例被后续版本覆盖后仍可复现历史诊断）
        for o in outcomes:
            if o.get("kind") and o.get("assertion_id") not in assert_kinds:
                assert_kinds[o["assertion_id"]] = o["kind"]

        failed_ui = [
            o
            for o in outcomes
            if o["status"] == "FAILED" and assert_kinds.get(o["assertion_id"], "ui") == "ui"
        ]
        failed_biz = [
            o
            for o in outcomes
            if o["status"] == "FAILED"
            and assert_kinds.get(o["assertion_id"]) in {"business_state", "api"}
        ]

        hypotheses: list[Hypothesis] = []
        observed: list[str] = []

        http_5xx: list[dict[str, Any]] = []
        http_authz: list[dict[str, Any]] = []
        network_failures: list[dict[str, Any]] = []
        locator_timeouts: list[dict[str, Any]] = []
        page_errors: list[dict[str, Any]] = []
        run_timeouts = False
        for s in steps:
            obs = s.get("observation") or {}
            if s.get("status") == "FAILED" and "locator" in str(s.get("error", "")).lower():
                locator_timeouts.append(s)
            if "Timeout" in str(s.get("error", "")) and "Locator" in str(s.get("error", "")):
                if s not in locator_timeouts:
                    locator_timeouts.append(s)
            for he in obs.get("http_errors") or []:
                status_code = he.get("status") or 0
                if status_code >= 500:
                    http_5xx.append({"step": s["step_id"], "entry": he})
                elif status_code in (401, 403):
                    # RUN-010 WP4 规则补齐：HTTP 403/401 → AUTHORIZATION 假设
                    http_authz.append({"step": s["step_id"], "entry": he})
            for ne in obs.get("network") or []:
                if ne.get("failure"):
                    network_failures.append({"step": s["step_id"], "entry": ne})
            for pe in obs.get("page_errors") or []:
                page_errors.append({"step": s["step_id"], "entry": pe})
            if obs.get("timed_out"):
                run_timeouts = True
            if s.get("status") == "FAILED":
                observed.append(f"{s['step_id']}:{s.get('error', '')[:120]}")

        def network_evidence_ids(step_id: str) -> list[str]:
            ids = [e.evidence_id for e in by_step_network.get(step_id, [])]
            return list(dict.fromkeys(ids))

        def step_evidence_ids(step_id: str) -> list[str]:
            ids = [e.evidence_id for e in by_step.get(step_id, [])]
            for s in steps:
                if s["step_id"] == step_id:
                    ids.extend(s.get("evidence_ids") or [])
            return list(dict.fromkeys(ids))

        def refs(ids: list[str], confirming: bool = False) -> list[EvidenceRef]:
            return [
                EvidenceRef(evidence_id=i, source_trust="TRUSTED", directly_confirming=confirming)
                for i in ids
            ]

        state_ids = [e.evidence_id for e in state_evidence]
        biz_failed_detail = self._business_detail(failed_biz)

        # ---- 持久化失败（UI 成功但业务未落库）→ 权威状态证据支持（候选级）
        # 若存在 5xx，则"未落库"是接口失败的后果，不作为独立根因（避免症状冒充根因）
        # RUN-011-R1 WP2/WP4（决策 R1-04 扩展）：业务断言失败背后也是同一次状态查询——
        # 「不能因为单次 count=0 就直接确认永久性持久化故障」（指令 §4.1）。
        # TF-DB-004（查询滞后）与 TF-DB-001（永久回滚）的 Run 时签名完全相同，
        # 单查询无法区分：确认须延迟复测（探针）后仍持续缺失的多点一致证据。
        if failed_biz and not failed_ui and not http_5xx:
            hypotheses.append(
                Hypothesis(
                    hypothesis_id=new_id("hyp"),
                    description="界面操作成功但业务状态未持久化（False Pass 风险）",
                    failure_domain=DOMAIN_UI,
                    failure_mechanism=FailureMechanism.PERSISTENCE,
                    supporting=refs(state_ids, confirming=False),
                    contributing_factors=[biz_failed_detail] if biz_failed_detail else [],
                )
            )

        # ---- UI 渲染缺失（业务成功但界面无反馈）
        # RUN-017 WP7：与"持久化"同一条原则——存在 5xx 时，界面缺失可能只是接口失败的
        # **后果**，不能作为独立根因被 CONFIRMED（症状不能冒充根因）。
        # 无 5xx 时才允许把 UI 渲染作为确认级假设；有 5xx 时保留为候选（非确认）。
        if failed_ui and not failed_biz:
            ui_ids: list[str] = []
            if failed_ui:
                for s in steps:
                    if s.get("action") == "assert":
                        ui_ids.extend(step_evidence_ids(s["step_id"]))
            hypotheses.append(
                Hypothesis(
                    hypothesis_id=new_id("hyp"),
                    description="业务状态正常但界面未渲染预期反馈（UI 渲染缺陷）",
                    failure_domain=DOMAIN_UI,
                    failure_mechanism=FailureMechanism.UI_RENDERING,
                    supporting=refs(ui_ids or state_ids, confirming=not http_5xx),
                    contributing_factors=(
                        ["检测到接口 5xx：界面缺失可能是接口失败的后果，不作为独立根因"]
                        if http_5xx
                        else []
                    ),
                )
            )

        # ---- 接口 5xx
        if http_5xx:
            ids: list[str] = []
            for item in http_5xx:
                ids.extend(network_evidence_ids(item["step"]))
                ids.extend(step_evidence_ids(item["step"]))
            hypotheses.append(
                Hypothesis(
                    hypothesis_id=new_id("hyp"),
                    description=f"后端接口返回 5xx（{http_5xx[0]['entry'].get('url', '')}）",
                    failure_domain=DOMAIN_UI,
                    failure_mechanism=FailureMechanism.API,
                    supporting=refs(list(dict.fromkeys(ids)), confirming=True),
                    contributing_factors=(
                        [f"业务断言失败：{biz_failed_detail}"] if biz_failed_detail else []
                    ),
                )
            )

        # ---- HTTP 401/403 → 授权缺陷（RUN-010 WP4 规则补齐）
        # 403/401 是确定性授权信号：目标服务器明确拒绝。403 时业务断言失败是
        # 授权被拒的后果，不作为独立根因。
        if http_authz:
            ids = []
            for item in http_authz:
                ids.extend(network_evidence_ids(item["step"]))
                ids.extend(step_evidence_ids(item["step"]))
            codes = sorted({int(item["entry"].get("status") or 0) for item in http_authz})
            hypotheses.append(
                Hypothesis(
                    hypothesis_id=new_id("hyp"),
                    description=(
                        f"目标返回 {codes}：访问被拒绝（授权/会话缺失或权限不足）"
                        f"（{http_authz[0]['entry'].get('url', '')}）"
                    ),
                    failure_domain=DOMAIN_EXEC,
                    failure_mechanism=FailureMechanism.AUTHORIZATION,
                    supporting=refs(list(dict.fromkeys(ids)), confirming=True),
                    contributing_factors=[
                        f"HTTP {item['entry'].get('status')} @ {item['step']}"
                        for item in http_authz[:3]
                    ],
                )
            )

        # ---- 网络失败 / 超时
        if network_failures or run_timeouts:
            ids = []
            for item in network_failures:
                ids.extend(network_evidence_ids(item["step"]))
                ids.extend(step_evidence_ids(item["step"]))
            for s in steps:
                if (s.get("observation") or {}).get("timed_out"):
                    ids.extend(step_evidence_ids(s["step_id"]))
            hypotheses.append(
                Hypothesis(
                    hypothesis_id=new_id("hyp"),
                    description="网络请求失败或超时（环境/链路问题）",
                    failure_domain=DOMAIN_ENV,
                    failure_mechanism=FailureMechanism.NETWORK,
                    supporting=refs(list(dict.fromkeys(ids)), confirming=bool(network_failures)),
                )
            )

        # ---- 定位器失效（不确认，需补 DOM 证据）
        if locator_timeouts and not failed_biz:
            ids = []
            for s in locator_timeouts:
                ids.extend(step_evidence_ids(s["step_id"]))
            hypotheses.append(
                Hypothesis(
                    hypothesis_id=new_id("hyp"),
                    description="元素定位失败或等待超时（可能为用例资产问题）",
                    failure_domain=DOMAIN_ASSET,
                    failure_mechanism=FailureMechanism.LOCATOR,
                    supporting=refs(list(dict.fromkeys(ids)), confirming=False),
                    contributing_factors=[str(s.get("error", ""))[:160] for s in locator_timeouts][
                        :3
                    ],
                )
            )

        # ---- 前端 JS 异常
        if page_errors:
            ids = []
            for item in page_errors:
                ids.extend(step_evidence_ids(item["step"]))
            hypotheses.append(
                Hypothesis(
                    hypothesis_id=new_id("hyp"),
                    description=f"页面存在未捕获 JS 异常（{str(page_errors[0]['entry'].get('message', ''))[:80]}）",
                    failure_domain=DOMAIN_UI,
                    failure_mechanism=FailureMechanism.UI_RENDERING,
                    supporting=refs(list(dict.fromkeys(ids)), confirming=False),
                )
            )

        # ---- Flaky 特征：同用例历史结论不一致
        flaky = self._flaky_check(run)
        if flaky:
            for h in hypotheses:
                h.flaky_characteristic = True
            if not hypotheses:
                hypotheses.append(
                    Hypothesis(
                        hypothesis_id=new_id("hyp"),
                        description="历史结果不一致（Flaky 特征，需多次执行差异证据）",
                        failure_domain=DOMAIN_EXEC,
                        failure_mechanism=FailureMechanism.TIMING,
                        supporting=refs(state_ids, confirming=False),
                        flaky_characteristic=True,
                    )
                )

        symptom = " | ".join(observed[:3]) or f"verdict={run['test_verdict']}"
        # ---- LLM 诊断顾问（RUN-003-G）：只追加 PROBABLE 级候选假设，不改 CONFIRMED 判定
        advisor_enabled = (
            getattr(self.rt.settings, "llm_diagnosis_enabled", True)
            if use_llm_advisor is None
            else use_llm_advisor
        )
        llm_advisor_notes: dict[str, Any] | None = None
        if advisor_enabled:
            llm_advisor_notes = self._llm_advise(
                run_id=run_id,
                symptom=symptom,
                steps=steps,
                evidence_list=evidence,
                hypotheses=hypotheses,
            )

        # ---- RUN-011-R1 WP3：假设去重（OPEN-RISK-03）
        # 规则与 LLM 候选合并后按机制 + 描述相似度消重，
        # 避免重复候选携带同一 Evidence 产生虚假的独立支持数量。
        from traceguard_evidence.diagnosis.dedup import dedup_hypotheses

        hypotheses, dedup_audit = dedup_hypotheses(hypotheses)

        # ---- RUN-010 WP4：Evidence Graph 裁决层参与主因裁决
        # （图不再只用于可视化：时间窗口/来源独立/重复消重/完整性全部进入裁决）
        evidence_rows = [
            {
                "evidence_id": e.evidence_id,
                "source_type": e.source_type,
                "source_ref": str((e.content or {}).get("step_id", "") or e.source_ref),
                "captured_at": e.captured_at,
                "source_trust": getattr(e, "source_trust", "") or "",
                "integrity_check": getattr(e, "integrity_check", "") or "NOT_RUN",
                "kind": getattr(e, "kind", "") or "",
            }
            for e in evidence
        ]
        from traceguard_evidence.graph.adjudication import (
            GraphAdjudicator,
            evidence_meta_from_rows,
        )

        meta_index = evidence_meta_from_rows(evidence_rows)
        adjudicator = GraphAdjudicator(
            meta_index,
            run_started_at=_parse_epoch(run.get("started_at")),
            run_ended_at=_parse_epoch(run.get("ended_at")),
        )
        adjudication = adjudicator.adjudicate([h.to_dict() for h in hypotheses])

        result = self.engine.diagnose(symptom, hypotheses, adjudication=adjudication)
        doc = {
            "diagnosis_id": result.diagnosis_id,
            "run_id": run_id,
            "task_id": run["task_id"],
            "workspace_id": run["workspace_id"],
            "testcase_id": tc["testcase_id"] if tc else "",
            "symptom": symptom,
            "outcome": result.outcome.value,
            "disposition": result.disposition.value,
            "root_causes": result.root_causes,
            "unresolved_conflicts": result.unresolved_conflicts,
            "hypotheses": [h.to_dict() for h in hypotheses],
            "evidence_refs": list(
                dict.fromkeys([r["id"] for h in hypotheses for r in h.to_dict()["supporting"]])
            ),
            "graph_adjudication": result.graph_adjudication,
            "hypothesis_dedup": dedup_audit,
            "flaky_characteristic": flaky,
            "engine": (
                "rule-v2+graph-adjudication+llm-advisor"
                if llm_advisor_notes and llm_advisor_notes.get("called")
                else "rule-v2+graph-adjudication"
            ),
            "llm_advisor": llm_advisor_notes,
            "strategy_version": "v2",
            "created_at": utc_now_iso(),
        }
        # RUN-018 WP5 §5.3：初始诊断也入版本链（revision 1 = 原始诊断快照，
        # 追加写、不可改写；后续重诊断各占一个 revision，原诊断不得被覆盖）。
        revision = self._record_revision(
            doc,
            trigger="initial",
            before=None,
            probe_ids=[],
            probe_execution_ids=[],
            new_evidence_ids=list(doc.get("evidence_refs") or []),
        )
        doc["revision_no"] = revision["revision_no"]
        doc["revision_ids"] = [revision["revision_id"]]
        doc["final_reason"] = revision["final_reason"]
        self.rt.docs.put(
            doc_id=result.diagnosis_id,
            kind="diagnosis",
            doc=doc,
            workspace_id=run["workspace_id"],
            ref_id=run_id,
            status=result.outcome.value,
        )
        disp = self._create_disposition(doc)
        doc["disposition_id"] = disp["disposition_id"]
        return doc

    # ---------------------------------------------------- probe 回灌与重诊断
    def reassess(
        self,
        diagnosis_id: str,
        signals: list[ProbeSignal],
        *,
        run_report: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """把探针证据回灌到原有假设，并重新裁决（CONTINUE/STOP 由调用方决定）。

        不变量：
        - 探针失败/被拒绝不产生任何支持或反驳证据；
        - CONFIRMED 仍只由确定性规则 + TRUSTED 确认证据产生，LLM 假设不会被升级；
        - 每次重诊断都留痕（evaluation_history），不覆盖历史结论。
        """
        rec = self.rt.docs.get(diagnosis_id)
        if not rec or rec.kind != "diagnosis":
            raise AppError(ErrorCode.NOT_FOUND, "diagnosis not found")
        doc = dict(rec.doc or {})
        # RUN-018 WP5 §5.3：重诊断前先留存"原 Diagnosis"完整快照（不可变基线）。
        before_state = self._diagnosis_state(doc)
        before = {
            "outcome": doc.get("outcome"),
            "root_causes": list(doc.get("root_causes") or []),
            "disposition": doc.get("disposition"),
            "unresolved_conflicts": list(doc.get("unresolved_conflicts") or []),
            "hypotheses": before_state["hypotheses"],
        }
        run = self.rt.tasks.get_run(doc.get("run_id", "")) or {}
        report = run_report if run_report is not None else (run.get("report") or {})
        run_ended_at = _parse_epoch(run.get("ended_at"))

        merged, results = merge_signals(
            doc.get("hypotheses") or [], signals, report, run_ended_at=run_ended_at
        )
        # RUN-010 WP4：探针生成的 TIMING 新假设（写入延迟/查询滞后）进入假设列表
        hypotheses = [_hypothesis_from_dict(h) for h in merged]
        # RUN-011-R1 WP3：重诊断同样去重（探针新假设可能与既有候选近似重复）
        from traceguard_evidence.diagnosis.dedup import dedup_hypotheses

        hypotheses, reassess_dedup_audit = dedup_hypotheses(hypotheses)

        # RUN-010 WP4：裁决层参与重诊断（与首诊同一裁决口径）
        evidence_rows = [
            {
                "evidence_id": e.evidence_id,
                "source_type": e.source_type,
                "source_ref": str((e.content or {}).get("step_id", "") or e.source_ref),
                "captured_at": e.captured_at,
                "source_trust": getattr(e, "source_trust", "") or "",
                "integrity_check": getattr(e, "integrity_check", "") or "NOT_RUN",
                "kind": getattr(e, "kind", "") or "",
            }
            for e in self.rt.evidence.list(run_id=doc.get("run_id", ""), limit=500)
        ]
        from traceguard_evidence.graph.adjudication import (
            GraphAdjudicator,
            evidence_meta_from_rows,
        )

        adjudicator = GraphAdjudicator(
            evidence_meta_from_rows(evidence_rows),
            run_started_at=_parse_epoch(run.get("started_at")),
            run_ended_at=run_ended_at,
        )
        adjudication = adjudicator.adjudicate([h.to_dict() for h in hypotheses])
        result = self.engine.diagnose(doc.get("symptom", ""), hypotheses, adjudication=adjudication)

        doc["hypotheses"] = [h.to_dict() for h in hypotheses]
        doc["outcome"] = result.outcome.value
        doc["disposition"] = result.disposition.value
        doc["root_causes"] = result.root_causes
        doc["unresolved_conflicts"] = result.unresolved_conflicts
        doc["graph_adjudication"] = result.graph_adjudication
        doc["hypothesis_dedup"] = reassess_dedup_audit
        doc["probe_feedback"] = [r.to_dict() for r in results]
        doc["probe_evidence_ids"] = list(
            dict.fromkeys(eid for r in results for u in r.updates for eid in u.evidence_ids)
        )
        doc["evidence_refs"] = list(
            dict.fromkeys(
                [r["id"] for h in doc["hypotheses"] for r in h.get("supporting", [])]
                + [r["id"] for h in doc["hypotheses"] for r in h.get("contradicting", [])]
            )
        )
        doc["engine"] = "rule-v1+probe-feedback"
        doc["updated_at"] = utc_now_iso()
        # ---- RUN-018 WP5 §5.3：每次重诊断完整留存（原诊断 / 新诊断 / 新增证据 /
        # 假设变化 / 置信度变化 / 最终裁决原因），追加写入版本链，不覆盖历史。
        after_state = self._diagnosis_state(doc)
        hypothesis_changes = _hypothesis_changes(before_state["hypotheses"], after_state["hypotheses"])
        confidence_changes = [
            {
                "hypothesis_id": c["hypothesis_id"],
                "description": c["description"],
                "confidence_before": c["confidence_before"],
                "confidence_after": c["confidence_after"],
            }
            for c in hypothesis_changes
            if c["confidence_before"] != c["confidence_after"]
        ]
        new_evidence_ids = list(
            dict.fromkeys(eid for r in results for u in r.updates for eid in u.evidence_ids)
        )
        final_reason = _final_reason(doc, results)
        revision = self._record_revision(
            doc,
            trigger="probe_evidence",
            before=before_state,
            probe_ids=[r.probe_id for r in results],
            probe_execution_ids=[r.execution_id for r in results],
            new_evidence_ids=new_evidence_ids,
            hypothesis_changes=hypothesis_changes,
            confidence_changes=confidence_changes,
            final_reason=final_reason,
        )
        doc["revision_no"] = revision["revision_no"]
        doc["revision_ids"] = list(doc.get("revision_ids") or []) + [revision["revision_id"]]
        doc["final_reason"] = final_reason
        doc["evaluation_history"] = list(doc.get("evaluation_history") or []) + [
            {
                "at": doc["updated_at"],
                "trigger": "probe_evidence",
                "probes": [r.probe_id for r in results],
                "probe_execution_ids": [r.execution_id for r in results],
                "outcome_before": before["outcome"],
                "outcome_after": doc["outcome"],
                "root_causes_after": list(result.root_causes),
                "unresolved_conflicts": list(result.unresolved_conflicts),
                # RUN-018 WP5：原/新诊断版本、假设与置信度变化、新增证据、裁决原因
                "revision_id": revision["revision_id"],
                "revision_no": revision["revision_no"],
                "diagnosis_before": before_state,
                "diagnosis_after": after_state,
                "new_evidence_ids": new_evidence_ids,
                "hypothesis_changes": hypothesis_changes,
                "confidence_changes": confidence_changes,
                "final_reason": final_reason,
            }
        ]
        self.rt.docs.put(
            doc_id=diagnosis_id,
            kind="diagnosis",
            doc=doc,
            workspace_id=doc.get("workspace_id", "default"),
            ref_id=doc.get("run_id", ""),
            status=doc["outcome"],
        )
        doc["disposition_id"] = self._refresh_disposition(doc)
        return {
            "diagnosis": doc,
            "feedback": [r.to_dict() for r in results],
            "before": before,
            "after": {
                "outcome": doc["outcome"],
                "root_causes": list(doc["root_causes"]),
                "unresolved_conflicts": list(doc["unresolved_conflicts"]),
                "hypotheses": after_state["hypotheses"],
            },
            "revision": revision,
            "hypothesis_changes": hypothesis_changes,
            "confidence_changes": confidence_changes,
            "new_evidence_ids": new_evidence_ids,
            "final_reason": final_reason,
        }

    def _refresh_disposition(self, diagnosis: dict[str, Any]) -> str:
        """诊断结论变化后刷新 Disposition（保留历史，不静默改写旧记录）。"""
        existing = self.rt.docs.list(kind="disposition", ref_id=diagnosis["diagnosis_id"], limit=5)
        kind = DISPOSITION_MAP.get(diagnosis["disposition"], "MANUAL_REVIEW")
        target = _route_target(kind)
        reason = _route_reason(kind, diagnosis)
        if existing:
            rec = existing[0]
            doc = dict(rec.doc or {})
            if doc.get("kind") == kind and doc.get("route_target") == target:
                return rec.id
            doc.setdefault("history", []).append(
                {
                    "kind": doc.get("kind"),
                    "route_target": doc.get("route_target"),
                    "reason": doc.get("reason"),
                    "at": utc_now_iso(),
                }
            )
            doc.update(
                {
                    "kind": kind,
                    "route_target": target,
                    "reason": reason,
                    "review_status": "PENDING",
                    "updated_at": utc_now_iso(),
                }
            )
            self.rt.docs.put(
                doc_id=rec.id,
                kind="disposition",
                doc=doc,
                workspace_id=diagnosis.get("workspace_id", "default"),
                ref_id=diagnosis["diagnosis_id"],
                status="PENDING",
            )
            return rec.id
        return self._create_disposition(diagnosis)["disposition_id"]

    # ---------------------------------------------------------------- helpers
    _LLM_DOMAIN_MAP: dict[str, tuple[Any, Any]] = {}

    def _llm_advise(
        self,
        run_id: str,
        symptom: str,
        steps: list[dict[str, Any]],
        evidence_list: list[Any],
        hypotheses: list[Hypothesis],
    ) -> dict[str, Any] | None:
        """调用 DeepSeek 诊断顾问，追加 LLM 候选假设。

        安全边界：
        - 输入只有症状/步骤/证据摘要，不含 Ground Truth；
        - 引用的 evidence_id 必须真实存在（网关已校验），此处再复核一次；
        - LLM 假设 status 固定 CANDIDATE，suggested_by="llm:<model>"，不能 CONFIRMED；
        - 任何 LLM 错误静默降级为"未调用"，诊断仍由确定性规则完成。
        """
        try:
            from traceguard_agents.gateway.llm_gateway import (
                LLMError,
                build_llm_stack,
            )

            settings = self.rt.settings
            stack = build_llm_stack(settings, root_dir=".")
            if not stack:
                return None
            if stack["tracker"].should_skip_llm():
                return {"called": False, "skipped": "budget_exhausted"}

            # 证据摘要（真实 ID + 类型 + 截断摘要，不含 GT）
            evidence_summary = []
            for e in evidence_list[:60]:
                # RUN-018 WP5 §5.5：Ground Truth 键递归剔除后才进入任何诊断输入
                content = _strip_ground_truth(e.content or {})
                brief = json.dumps(content, ensure_ascii=False, default=str)[:220]
                evidence_summary.append(
                    {"evidence_id": e.evidence_id, "source_type": e.source_type, "brief": brief}
                )
            failed_steps = [
                {
                    "step_id": s.get("step_id"),
                    "action": s.get("action"),
                    "error": str(s.get("error", ""))[:200],
                }
                for s in steps
                if s.get("status") == "FAILED"
            ][:8]

            advisor = self._get_advisor(stack, run_id)
            if advisor is None:
                return {"called": False, "skipped": "not_configured"}
            advice = advisor.suggest(
                symptom=symptom,
                failed_steps=failed_steps,
                evidence_summary=evidence_summary,
            )
            candidates = list(advice.candidates)

            # 域映射 + 复核 evidence id + 追加候选（不改已有规则假设）
            known_ids = {e.evidence_id for e in evidence_list}
            added: list[dict[str, Any]] = []
            for c in candidates:
                domain, mech = self._map_llm_domain(c.failure_domain, c.failure_mechanism)
                evd_refs = [
                    EvidenceRef(evidence_id=i, source_trust="TRUSTED", directly_confirming=False)
                    for i in c.supporting_evidence_ids
                    if i in known_ids
                ]
                hypotheses.append(
                    Hypothesis(
                        hypothesis_id=new_id("hyp"),
                        description=f"[LLM 候选] {c.description}",
                        failure_domain=domain,
                        failure_mechanism=mech,
                        supporting=evd_refs,
                        contributing_factors=c.warnings,
                        status=HypothesisStatus.CANDIDATE,  # 固定候选级，不 CONFIRMED
                        suggested_by=c.suggested_by,  # 形如 "llm:deepseek-flash"（网关已带前缀）
                    )
                )
                added.append(
                    {
                        "description": c.description,
                        "domain": c.failure_domain,
                        "confidence": c.confidence,
                        "evidence_ids": c.supporting_evidence_ids,
                        "warnings": c.warnings,
                    }
                )
            fallbacks: list[str] = []
            if advice.fallback:
                # 调用了模型但没有可用候选 → 必须显式记录回退事实，不能伪装成"有 AI 建议"。
                fallbacks.append(advice.error or "llm_no_valid_hypothesis")
                logger.warning(
                    "llm_advisor_fallback",
                    error_kind=advice.error,
                    attempts=advice.attempts,
                    dropped=advice.dropped,
                )
            return {
                "called": bool(advice.called),
                "added": added,
                "model": advice.model,
                "fallbacks": fallbacks,
                "fallback": advice.fallback,
                "error": advice.error,
                "attempts": advice.attempts,
                "dropped": advice.dropped,
                "warnings": list(advice.warnings)[:10],
            }
        except LLMError as exc:
            logger.warning("llm_advisor_failed", error_kind=exc.kind)
            return {"called": False, "skipped": f"llm_error:{exc.kind}"}
        except Exception as exc:  # noqa: BLE001
            logger.warning("llm_advisor_unexpected", error=str(exc))
            return {"called": False, "skipped": f"error:{type(exc).__name__}"}

    def _get_advisor(self, stack: dict[str, Any], run_id: str) -> Any | None:
        from traceguard_agents.gateway.llm_gateway import LLMDiagnosisAdvisor

        return LLMDiagnosisAdvisor(
            provider=stack["provider"],
            router=stack["router"],
            prompts=stack["prompts"],
            tracker=stack["tracker"],
            task_id="",
            run_id=run_id,
        )

    @classmethod
    def _map_llm_domain(cls, domain: str, mechanism: str) -> tuple[Any, Any]:
        """把 LLM 粗分域映射到确定性枚举（白名单外 → UNKNOWN）。"""
        mapping = {
            "API": (DOMAIN_UI, FailureMechanism.API),
            "NETWORK": (DOMAIN_ENV, FailureMechanism.NETWORK),
            "UI": (DOMAIN_UI, FailureMechanism.UI_RENDERING),
            "PERSISTENCE": (DOMAIN_UI, FailureMechanism.PERSISTENCE),
            "LOCATOR": (DOMAIN_ASSET, FailureMechanism.LOCATOR),
            "JS": (DOMAIN_UI, FailureMechanism.UI_RENDERING),
            "TIMEOUT": (DOMAIN_EXEC, FailureMechanism.TIMING),
        }
        return mapping.get(domain.upper(), (FailureDomain.UNKNOWN, FailureMechanism.UNKNOWN))

    @staticmethod
    def _business_detail(failed_biz: list[dict[str, Any]]) -> str:
        if not failed_biz:
            return ""
        o = failed_biz[0]
        return f"{o['assertion_id']}: {o.get('reason', '')}"

    def _flaky_check(self, run: dict[str, Any]) -> bool:
        siblings = [
            r
            for r in self.rt.tasks.list_runs(limit=200)
            if r["testcase_version_id"] == run["testcase_version_id"]
            and r["run_id"] != run["run_id"]
        ]
        verdicts = {r["test_verdict"] for r in siblings if r["test_verdict"] != "NOT_EVALUATED"}
        verdicts.add(run["test_verdict"])
        return "PASSED" in verdicts and "FAILED" in verdicts

    def _create_disposition(self, diagnosis: dict[str, Any]) -> dict[str, Any]:
        kind = DISPOSITION_MAP.get(diagnosis["disposition"], "MANUAL_REVIEW")
        disposition_id = new_id("disp")
        doc = {
            "disposition_id": disposition_id,
            "diagnosis_id": diagnosis["diagnosis_id"],
            "run_id": diagnosis["run_id"],
            "workspace_id": diagnosis.get("workspace_id", "default"),
            "kind": kind,
            "route_target": _route_target(kind),
            "review_status": "PENDING",
            "reason": _route_reason(kind, diagnosis),
            "created_at": utc_now_iso(),
        }
        self.rt.docs.put(
            doc_id=disposition_id,
            kind="disposition",
            doc=doc,
            workspace_id=diagnosis.get("workspace_id", "default"),
            ref_id=diagnosis["diagnosis_id"],
            status="PENDING",
        )
        return doc

    def list_diagnoses(
        self,
        workspace_id: str | None = None,
        run_id: str | None = None,
        limit: int = 100,
        *,
        workspace_ids: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        recs = self.rt.docs.list(
            kind="diagnosis",
            workspace_id=workspace_id,
            ref_id=run_id,
            limit=limit,
            workspace_ids=workspace_ids,
        )
        return [r.to_dict() for r in recs]

    def get(self, diagnosis_id: str) -> dict[str, Any] | None:
        rec = self.rt.docs.get(diagnosis_id)
        return rec.to_dict() if rec else None

    # ------------------------------------------------------- 版本链（RUN-018 WP5）
    def _diagnosis_state(self, doc: dict[str, Any]) -> dict[str, Any]:
        """诊断状态快照（原/新 Diagnosis 的可比对形态：状态 + 置信度 + 证据计数）。"""
        hypotheses = []
        for h in doc.get("hypotheses") or []:
            hypotheses.append(
                {
                    "hypothesis_id": h.get("hypothesis_id"),
                    "description": h.get("description", ""),
                    "failure_domain": h.get("failure_domain", ""),
                    "failure_mechanism": h.get("failure_mechanism", ""),
                    "status": h.get("status", "CANDIDATE"),
                    "confidence": hypothesis_confidence(h),
                    "supporting_count": len(h.get("supporting") or []),
                    "contradicting_count": len(h.get("contradicting") or []),
                    "suggested_by": h.get("suggested_by", "rule"),
                }
            )
        return {
            "diagnosis_id": doc.get("diagnosis_id"),
            "outcome": doc.get("outcome"),
            "disposition": doc.get("disposition"),
            "root_causes": list(doc.get("root_causes") or []),
            "unresolved_conflicts": list(doc.get("unresolved_conflicts") or []),
            "hypotheses": hypotheses,
        }

    def _record_revision(
        self,
        doc: dict[str, Any],
        *,
        trigger: str,
        before: dict[str, Any] | None,
        probe_ids: list[str],
        probe_execution_ids: list[str],
        new_evidence_ids: list[str],
        hypothesis_changes: list[dict[str, Any]] | None = None,
        confidence_changes: list[dict[str, Any]] | None = None,
        final_reason: str = "",
    ) -> dict[str, Any]:
        """把一次诊断/重诊断写成**不可变**版本记录（追加写，原诊断不得覆盖）。"""
        existing = self.rt.docs.list(
            kind="diagnosis_revision", ref_id=str(doc.get("diagnosis_id") or ""), limit=200
        )
        revision_no = 1 + max((int((r.doc or {}).get("revision_no") or 0) for r in existing), default=0)
        revision_id = new_id("diagrev")
        after = self._diagnosis_state(doc)
        record = {
            "revision_id": revision_id,
            "diagnosis_id": doc.get("diagnosis_id"),
            "revision_no": revision_no,
            "trigger": trigger,
            "probe_ids": list(probe_ids),
            "probe_execution_ids": list(probe_execution_ids),
            "new_evidence_ids": list(new_evidence_ids),
            "before": before,
            "after": after,
            "hypothesis_changes": list(hypothesis_changes or []),
            "confidence_changes": list(confidence_changes or []),
            "final_reason": final_reason
            or _final_reason(
                {
                    **doc,
                    "outcome": after["outcome"],
                    "root_causes": after["root_causes"],
                    "unresolved_conflicts": after["unresolved_conflicts"],
                },
                [],
            ),
            "created_at": utc_now_iso(),
        }
        self.rt.docs.put(
            doc_id=revision_id,
            kind="diagnosis_revision",
            doc=record,
            workspace_id=str(doc.get("workspace_id") or "default"),
            ref_id=str(doc.get("diagnosis_id") or ""),
            status=str(after.get("outcome") or ""),
        )
        return record

    def list_revisions(self, diagnosis_id: str) -> list[dict[str, Any]]:
        """诊断版本链（按 revision_no 升序：v1 = 原始诊断）。"""
        recs = self.rt.docs.list(kind="diagnosis_revision", ref_id=diagnosis_id, limit=200)
        records = [dict(r.doc or {}) for r in recs]
        records.sort(key=lambda r: int(r.get("revision_no") or 0))
        return records


def _route_target(kind: str) -> str:
    return {
        "PRODUCT_DEFECT": "product-backlog",
        "TEST_ASSET": "repair-candidate",
        "TEST_EXECUTION": "retry-scheduler",
        "ENVIRONMENT": "infra-oncall",
        "AGENT": "agent-review",
        "PLATFORM": "platform-oncall",
        "NEEDS_MORE_EVIDENCE": "probe-planner",
        "MANUAL_REVIEW": "human-triage",
    }.get(kind, "human-triage")


def _route_reason(kind: str, diagnosis: dict[str, Any]) -> str:
    root = diagnosis.get("root_causes") or []
    if kind == "NEEDS_MORE_EVIDENCE":
        return "证据不足以确认根因：先补探针证据，不得直接进入修复"
    if kind == "PRODUCT_DEFECT":
        return f"已确认产品缺陷（根因 {len(root)} 条），需产品侧修复，不由自愈修改产品代码"
    if kind == "TEST_ASSET":
        return "疑似用例资产问题，可进入 Repair 候选（需人工审批）"
    return "需人工分流"


def _enum(cls: Any, value: Any, fallback: Any) -> Any:
    try:
        return cls(value)
    except Exception:  # noqa: BLE001
        return fallback


def _hypothesis_from_dict(h: dict[str, Any]) -> Hypothesis:
    """把持久化的假设文档还原成领域对象（探针回灌后重新裁决用）。

    RUN-010 WP4：POST_HOC 证据以 UNTRUSTED 信任进入领域对象——引擎的
    CONFIRMED 规则只认 TRUSTED，事后观测因此无法冒充权威证据。
    """
    return Hypothesis(
        hypothesis_id=str(h["hypothesis_id"]),
        description=str(h.get("description", "")),
        failure_domain=_enum(FailureDomain, h.get("failure_domain"), FailureDomain.UNKNOWN),
        failure_mechanism=_enum(
            FailureMechanism, h.get("failure_mechanism"), FailureMechanism.UNKNOWN
        ),
        supporting=[
            EvidenceRef(
                str(x["id"]),
                str(x.get("trust", "UNKNOWN")),
                bool(x.get("confirming")) and str(x.get("trust", "")) == "TRUSTED",
            )
            for x in h.get("supporting") or []
            if x.get("id")
        ],
        contradicting=[
            EvidenceRef(
                str(x["id"]),
                str(x.get("trust", "UNKNOWN")),
                False,
            )
            for x in h.get("contradicting") or []
            if x.get("id")
        ],
        status=_enum(HypothesisStatus, h.get("status"), HypothesisStatus.CANDIDATE),
        contributing_factors=list(h.get("contributing_factors") or []),
        flaky_characteristic=bool(h.get("flaky_characteristic")),
        suggested_by=str(h.get("suggested_by") or "rule"),
        probe_feedback=list(h.get("probe_feedback") or []),
        excluded_by=list(h.get("excluded_by") or []),
    )


def _parse_epoch(value: Any) -> float:
    """run started_at/ended_at（epoch 或 ISO）→ epoch 秒；未知返回 0。"""
    if value in (None, "", 0):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    from datetime import datetime

    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


# ------------------------------------------------ RUN-018 WP5 重诊断留痕 helpers
def _hypothesis_changes(
    before: list[dict[str, Any]], after: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """假设变化：状态/置信度 before → after（含探针新增假设，before=None）。"""
    before_by_id = {str(h["hypothesis_id"]): h for h in before}
    changes: list[dict[str, Any]] = []
    for h in after:
        hid = str(h["hypothesis_id"])
        prev = before_by_id.get(hid)
        status_before = (prev or {}).get("status")
        confidence_before = (prev or {}).get("confidence")
        changes.append(
            {
                "hypothesis_id": hid,
                "description": h.get("description", ""),
                "status_before": status_before,
                "status_after": h.get("status"),
                "confidence_before": confidence_before,
                "confidence_after": h.get("confidence"),
                "added_by_probe": prev is None,
                "changed": prev is None
                or status_before != h.get("status")
                or confidence_before != h.get("confidence"),
            }
        )
    return changes


def _final_reason(doc: dict[str, Any], results: list[Any]) -> str:
    """最终裁决原因（确定性文案）：为什么确认，或为什么弃权。"""
    outcome = str(doc.get("outcome") or "")
    conflicts = list(doc.get("unresolved_conflicts") or [])
    root_ids = list(doc.get("root_causes") or [])
    hypotheses = {str(h.get("hypothesis_id")): h for h in doc.get("hypotheses") or []}
    parts: list[str] = []
    if conflicts:
        parts.append(
            f"弃权（证据冲突）：假设 {conflicts} 同时存在权威支持与权威反驳，"
            "按 P03 不自动裁决，交人工复核"
        )
    elif outcome == "CONFIRMED":
        for hid in root_ids:
            h = hypotheses.get(hid, {})
            confirming = [
                r.get("id")
                for r in h.get("supporting") or []
                if r.get("confirming") and r.get("trust") == "TRUSTED"
            ]
            parts.append(
                f"确认：{hid}（{h.get('failure_mechanism', '?')}）持有 TRUSTED 直接确认证据 "
                f"{confirming} 且无未解决反驳 → 列为根因"
            )
    elif outcome == "PROBABLE":
        supported = [
            str(h.get("hypothesis_id"))
            for h in doc.get("hypotheses") or []
            if h.get("status") == "SUPPORTED"
        ]
        parts.append(
            f"弃权确认（PROBABLE）：假设 {supported} 仅有方向性支持"
            "（单点查询/时间未对齐/非直接确认），不足以权威确认根因"
        )
    else:
        parts.append(
            "弃权（INCONCLUSIVE）：无假设达到确认门槛（TRUSTED + directly_confirming），"
            "探针补充证据不足以改变结论"
        )
    if results:
        probe_ids = []
        alignments = []
        for r in results:
            d = r.to_dict() if hasattr(r, "to_dict") else dict(r)
            probe_ids.append(str(d.get("probe_id")))
            alignments.append(str(d.get("time_alignment", "")))
        parts.append(
            f"本轮探针 {list(dict.fromkeys(probe_ids))} 的时间对齐 {list(dict.fromkeys(alignments))}"
            "（POST_HOC/UNKNOWN 不作权威依据）"
        )
    return "；".join(parts)


_GT_KEY_PATTERN = re.compile(r"ground[_\-]?truth", re.IGNORECASE)


def _strip_ground_truth(obj: Any) -> Any:
    """递归剔除 Ground Truth 相关键（RUN-018 WP5 §5.5：GT 永不进入 Runtime Diagnosis）。"""
    if isinstance(obj, dict):
        return {k: _strip_ground_truth(v) for k, v in obj.items() if not _GT_KEY_PATTERN.search(str(k))}
    if isinstance(obj, list):
        return [_strip_ground_truth(v) for v in obj]
    return obj
