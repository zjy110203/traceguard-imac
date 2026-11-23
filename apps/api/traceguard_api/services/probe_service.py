"""Active Diagnosis 探针服务：真实只读探针 + 五项校验 + 结果证据化 + 预算生命周期。

探针全部只读，且必须走白名单模板：
- `http_state`：独立状态查询（受控本地 TeamFlow API）；
- `network_trace`：读取已落库 Run 的网络观测（不重新访问目标）；
- `dom_snapshot`：读取已落库 DOM 证据；
- `backend_log`：读取本地受控服务日志尾部（脱敏）。
任何越权/非白名单/超预算调用都会被拒绝并审计。

RUN-005（P0）关键修复：
1. 预算按 Run / Diagnosis 隔离，从该预算首次使用开始计时；服务运行超过 60 秒不再
   让后续诊断的探针全部失效；
2. `plan_and_probe()` 真正串起
   Evidence Gap → Candidate → 五项校验 → EIG → Planner.choose → Probe Execution →
   New Evidence → Re-evaluation → Stop/Continue，不再只返回静态建议；
3. 预算使用量持久化到 DocStore（kind=probe_budget），进程重启后按 scope 恢复；
4. 每次探针执行都可追溯到 run_id / diagnosis_id / budget scope。
"""

from __future__ import annotations

import json
import time
import urllib.request
from pathlib import Path
from typing import Any

from traceguard_core.errors import AppError, ErrorCode
from traceguard_core.ids import new_id, utc_now_iso
from traceguard_core.logging import get_logger, redact
from traceguard_evidence.probe.planner import (
    PROVISIONAL_NOTE,
    ActivePlanner,
    ProbeBudget,
    ProbeBudgetRegistry,
    ProbeDefinition,
    ProbeEngine,
    ProbeResult,
    budget_doc_id,
)

from traceguard_api.runtime import Runtime

logger = get_logger("traceguard.service.probe")

PERMISSION = "probe.read"
BUDGET_KIND = "probe_budget"
PLAN_KIND = "probe_plan"

#: P06 未确认 → provisional 默认值（50 次 / 100 成本 / 60 秒）
DEFAULT_PROBE_BUDGET = {
    "max_probes": 50,
    "max_total_cost": 100.0,
    "max_duration_seconds": 60.0,
}


class ProbeService:
    def __init__(self, rt: Runtime, *, clock: Any | None = None) -> None:
        self.rt = rt
        self.engine = ProbeEngine(ProbeBudget(scope_key="service-default", **DEFAULT_PROBE_BUDGET))
        self.engine.grant_permission(PERMISSION)
        self.planner = ActivePlanner(self.engine)
        self.budgets = ProbeBudgetRegistry(
            provisional=True,
            clock=clock or time.time,
            loader=self._load_budget_doc,
            saver=self._save_budget_doc,
            **DEFAULT_PROBE_BUDGET,
        )
        self._register()

    # ------------------------------------------------------------- registry
    def _register(self) -> None:
        self.engine.register(
            ProbeDefinition(
                probe_id="http_state",
                capability="api_read",
                permission_required=PERMISSION,
                risk_level="LOW",
                allowed_templates={"get_state_projects", "get_state_project"},
                timeout_ms=8000,
                estimated_cost=1.0,
            ),
            self._exec_http_state,
        )
        self.engine.register(
            ProbeDefinition(
                probe_id="network_trace",
                capability="network_read",
                permission_required=PERMISSION,
                risk_level="LOW",
                allowed_templates={"run_network"},
                estimated_cost=0.2,
            ),
            self._exec_network_trace,
        )
        self.engine.register(
            ProbeDefinition(
                probe_id="dom_snapshot",
                capability="dom_snapshot",
                permission_required=PERMISSION,
                risk_level="LOW",
                allowed_templates={"run_dom"},
                estimated_cost=0.2,
            ),
            self._exec_dom_snapshot,
        )
        self.engine.register(
            ProbeDefinition(
                probe_id="backend_log",
                capability="backend_log_read",
                permission_required=PERMISSION,
                risk_level="LOW",
                allowed_templates={"tail_teamflow"},
                max_rows=200,
                estimated_cost=0.3,
            ),
            self._exec_backend_log,
        )

    def definitions(self) -> list[dict[str, Any]]:
        return [
            {
                "probe_id": d.probe_id,
                "capability": d.capability,
                "read_only": d.read_only,
                "risk_level": d.risk_level,
                "allowed_templates": sorted(d.allowed_templates),
                "estimated_cost": d.estimated_cost,
                "permission_required": d.permission_required,
            }
            for d in self.engine.definitions.values()
        ]

    # ------------------------------------------------------- budget persistence
    def _load_budget_doc(self, scope_key: str) -> dict[str, Any] | None:
        try:
            rec = self.rt.docs.get(budget_doc_id(scope_key))
        except Exception as exc:  # noqa: BLE001
            logger.warning("probe_budget_load_failed", scope=scope_key, error=str(exc))
            return None
        return rec.doc if rec else None

    def _save_budget_doc(self, budget: ProbeBudget) -> None:
        try:
            self.rt.docs.put(
                doc_id=budget.budget_id,
                kind=BUDGET_KIND,
                doc=budget.snapshot(),
                workspace_id=budget.workspace_id,
                ref_id=budget.owner_run_id or budget.owner_diagnosis_id or None,
                status="CLOSED" if budget.is_closed else "OPEN",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("probe_budget_save_failed", scope=budget.scope_key, error=str(exc))

    def budget_for(
        self, *, run_id: str = "", diagnosis_id: str = "", workspace_id: str = "default"
    ) -> ProbeBudget:
        if diagnosis_id:
            scope = f"diagnosis:{diagnosis_id}"
        elif run_id:
            scope = f"run:{run_id}"
        else:
            scope = f"manual:{workspace_id}"
        return self.budgets.get_or_create(
            scope, run_id=run_id, diagnosis_id=diagnosis_id, workspace_id=workspace_id
        )

    def close_budget(
        self, *, run_id: str = "", diagnosis_id: str = "", reason: str = "DIAGNOSIS_COMPLETED"
    ) -> dict[str, Any] | None:
        scope = (
            f"diagnosis:{diagnosis_id}"
            if diagnosis_id
            else (f"run:{run_id}" if run_id else "")
        )
        if not scope:
            raise AppError(ErrorCode.VALIDATION_ERROR, "run_id or diagnosis_id required")
        budget = self.budgets.close(scope, reason)
        return budget.snapshot() if budget else None

    def budgets_snapshot(self, workspace_id: str | None = None) -> list[dict[str, Any]]:
        """内存中的活跃预算 + 持久化的历史上预算文档。"""
        active = {b["budget_id"]: b for b in self.budgets.snapshot()}
        for rec in self.rt.docs.list(kind=BUDGET_KIND, workspace_id=workspace_id, limit=200):
            active.setdefault(rec.id, rec.doc)
        return sorted(active.values(), key=lambda b: b.get("started_at") or 0, reverse=True)

    # -------------------------------------------------------------- execute
    def execute(
        self,
        probe_id: str,
        params: dict[str, Any],
        workspace_id: str = "default",
        *,
        run_id: str | None = None,
        diagnosis_id: str | None = None,
        budget: ProbeBudget | None = None,
    ) -> dict[str, Any]:
        params = dict(params or {})
        params.setdefault("workspace", workspace_id)
        run_id = run_id or params.get("run_id") or None
        diagnosis_id = diagnosis_id or params.get("diagnosis_id") or None
        b = budget or self.budget_for(
            run_id=run_id or "", diagnosis_id=diagnosis_id or "", workspace_id=workspace_id
        )
        result: ProbeResult = self.engine.execute(probe_id, params, workspace_id, budget=b)
        self.budgets.save(b)

        execution_id = new_id("probeexec")
        evidence_ids: list[str] = []
        if result.status == "SUCCEEDED":
            evd_id = new_id("evd")
            try:
                self.rt.evidence.add_raw(
                    evd_id,
                    "probe",
                    f"probe:{probe_id}",
                    workspace_id,
                    {
                        "probe_id": probe_id,
                        "params": redact(params),
                        "data": result.data,
                        "source": result.source,
                        "execution_id": execution_id,
                        "budget_scope": b.scope_key,
                        "diagnosis_id": diagnosis_id,
                    },
                    source_trust="TRUSTED",
                    do_redact=True,
                    run_id=run_id,
                )
                evidence_ids.append(evd_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning("probe_evidence_failed", error=str(exc))

        doc = {
            "execution_id": execution_id,
            "probe_id": probe_id,
            "workspace_id": workspace_id,
            "run_id": run_id,
            "diagnosis_id": diagnosis_id,
            "status": result.status,
            "permission_decision": "ALLOW" if result.status != "BLOCKED" else "DENY",
            "risk_level": "LOW",
            "params": redact(params),
            "result": result.data,
            "reason": result.reason,
            "source": result.source,
            "evidence_refs": evidence_ids,
            "duration_ms": result.duration_ms,
            "budget_scope": b.scope_key,
            "budget": b.snapshot(),
            "executed_at": utc_now_iso(),
        }
        self.rt.docs.put(
            doc_id=execution_id,
            kind="probe_execution",
            doc=doc,
            workspace_id=workspace_id,
            ref_id=run_id,
            status=result.status,
        )
        return doc

    # ------------------------------------------------------------- suggestions
    def suggest(self, run_id: str, diagnosis_id: str | None = None) -> list[dict[str, Any]]:
        """基于证据缺口给出探针建议（确定性规则，不靠 LLM 猜测）。

        注意：这是"建议"，不是执行；真正闭环见 `plan_and_probe()`。
        """
        run = self.rt.tasks.get_run(run_id)
        if not run:
            raise AppError(ErrorCode.NOT_FOUND, "run not found")
        diag = self.rt.docs.get(diagnosis_id) if diagnosis_id else None
        budget = self.budget_for(
            run_id=run_id,
            diagnosis_id=diagnosis_id or "",
            workspace_id=run.get("workspace_id", "default"),
        )
        suggestions: list[dict[str, Any]] = []
        for gap in self._evidence_gaps(run, diag.doc if diag else None):
            d = self.engine.definitions[gap["probe_id"]]
            suggestions.append(
                {
                    "probe_id": gap["probe_id"],
                    "params": gap["params"],
                    "why": gap["why"],
                    "info_gain": gap["info_gain"],
                    "gap_id": gap["gap_id"],
                    "score": round(self.planner.score(d, gap["info_gain"]), 4),
                }
            )
        for s in suggestions:
            s["budget_scope"] = budget.scope_key
        return sorted(suggestions, key=lambda x: x["score"], reverse=True)

    # ----------------------------------------------------------- active loop
    def plan_and_probe(
        self,
        run_id: str,
        diagnosis_id: str | None = None,
        *,
        max_iterations: int = 6,
        close_on_confirm: bool = True,
        use_llm_planner: bool | None = None,
    ) -> dict[str, Any]:
        """真正执行 Active Diagnosis 循环，而不是只返回静态建议。

        Evidence Gap → Candidate Probe → Permission/Risk/Cost/Schema 校验 →
        EIG Score → Planner.choose → Execution → New Evidence → Re-evaluation →
        Stop / Continue。
        """
        run = self.rt.tasks.get_run(run_id)
        if not run:
            raise AppError(ErrorCode.NOT_FOUND, "run not found")
        workspace_id = run.get("workspace_id") or "default"
        diag = self.rt.docs.get(diagnosis_id) if diagnosis_id else None
        diag_doc = diag.doc if diag else None
        budget = self.budget_for(
            run_id=run_id, diagnosis_id=diagnosis_id or "", workspace_id=workspace_id
        )

        plan_id = new_id("probeplan")
        steps: list[dict[str, Any]] = []
        evidence_ids: list[str] = []
        attempted: dict[str, str] = {}  # gap_id -> status
        gaps_before = self._evidence_gaps(run, diag_doc)
        stop_reason = ""
        stop_detail = ""
        llm_notes: dict[str, Any] = {"called": False}
        advisor = self._llm_advisor(use_llm_planner, run_id=run_id)

        for iteration in range(1, max_iterations + 1):
            if budget.is_closed:
                stop_reason, stop_detail = "STOP_BUDGET_CLOSED", budget.close_reason
                break
            confirmed = bool(diag_doc and diag_doc.get("outcome") == "CONFIRMED")
            permissions_valid = PERMISSION in self.engine._permissions
            candidates: list[tuple[ProbeDefinition, float]] = []
            gap_by_probe: dict[str, dict[str, Any]] = {}
            for gap in self._evidence_gaps(run, diag_doc, attempted=attempted):
                d = self.engine.definitions.get(gap["probe_id"])
                if d is None:
                    continue
                candidates.append((d, gap["info_gain"]))
                gap_by_probe[gap["probe_id"]] = gap

            if advisor is not None and candidates:
                llm_notes = self._apply_llm_advice(advisor, candidates, gap_by_probe)
            probe, reason = self.planner.choose(
                candidates,
                confirmed=confirmed,
                conflicts_unresolved=False,
                permissions_valid=permissions_valid,
                budget=budget,
            )
            if probe is None:
                stop_reason = reason
                stop_detail = {
                    "STOP_CONFIRMED": "诊断已确认根因，停止继续消耗探针预算",
                    "STOP_NO_HIGH_VALUE_PROBE": "已无高价值证据缺口",
                    "STOP_PERMISSION_INVALID": "探针权限失效，停止执行",
                    "STOP_BUDGET_EXHAUSTED": budget.exhausted_reason() or "预算耗尽",
                    "STOP_BUDGET_CLOSED": budget.close_reason,
                    "STOP_MARGINAL_VALUE_INSUFFICIENT": "边际信息增益不足以覆盖成本与风险",
                }.get(reason, "")
                break

            gap = gap_by_probe[probe.probe_id]
            params = dict(gap["params"])
            params["workspace"] = workspace_id
            params["run_id"] = run_id
            if diagnosis_id:
                params["diagnosis_id"] = diagnosis_id
            score = round(self.planner.score(probe, gap["info_gain"]), 4)
            result_doc = self.execute(
                probe.probe_id,
                params,
                workspace_id,
                run_id=run_id,
                diagnosis_id=diagnosis_id,
                budget=budget,
            )
            attempted[gap["gap_id"]] = result_doc["status"]
            evidence_ids.extend(result_doc.get("evidence_refs") or [])
            steps.append(
                {
                    "iteration": iteration,
                    "gap_id": gap["gap_id"],
                    "probe_id": probe.probe_id,
                    "why": gap["why"],
                    "info_gain": gap["info_gain"],
                    "score": score,
                    "status": result_doc["status"],
                    "reason": result_doc.get("reason") or "",
                    "evidence_ids": result_doc.get("evidence_refs") or [],
                    "duration_ms": result_doc.get("duration_ms", 0),
                    "execution_id": result_doc["execution_id"],
                    "budget": result_doc["budget"],
                }
            )
            # 探针失败/被拒绝：保留失败事实；权限/预算类拒绝直接停止（硬约束）
            if result_doc["status"] == "BLOCKED":
                stop_reason = "STOP_PROBE_BLOCKED"
                stop_detail = result_doc.get("reason") or "PROBE_BLOCKED"
                break
            if result_doc["status"] in {"FAILED", "TIMED_OUT"}:
                continue
        else:
            stop_reason, stop_detail = "STOP_ITERATION_LIMIT", f"达到最大迭代 {max_iterations}"

        gaps_after = self._evidence_gaps(run, diag_doc, attempted=attempted)
        if close_on_confirm and (diag_doc or {}).get("outcome") == "CONFIRMED":
            budget.close("STOP_CONFIRMED")
            self.budgets.save(budget)

        plan = {
            "plan_id": plan_id,
            "run_id": run_id,
            "diagnosis_id": diagnosis_id,
            "workspace_id": workspace_id,
            "budget": budget.snapshot(),
            "gaps_before": gaps_before,
            "gaps_after": gaps_after,
            "steps": steps,
            "evidence_ids": list(dict.fromkeys(evidence_ids)),
            "stop_reason": stop_reason,
            "stop_detail": stop_detail,
            "planner": {
                "weights": self.planner.weights.__dict__,
                "note": self.planner.weights.note,
            },
            "llm_planner": llm_notes,
            "provisional": True,
            "note": PROVISIONAL_NOTE,
            "created_at": utc_now_iso(),
        }
        self.rt.docs.put(
            doc_id=plan_id,
            kind=PLAN_KIND,
            doc=plan,
            workspace_id=workspace_id,
            ref_id=run_id,
            status=stop_reason,
        )
        if diag:
            doc = dict(diag.doc or {})
            doc["last_probe_plan_id"] = plan_id
            doc["probe_evidence_ids"] = plan["evidence_ids"]
            doc["updated_at"] = utc_now_iso()
            self.rt.docs.put(
                doc_id=diagnosis_id or "",
                kind="diagnosis",
                doc=doc,
                workspace_id=diag.workspace_id,
                ref_id=run_id,
                status=diag.status,
            )
        return plan

    # ------------------------------------------------------------- LLM 辅助
    def _llm_enabled(self, override: bool | None) -> bool:
        if override is not None:
            return override
        return bool(getattr(self.rt.settings, "llm_probe_planner_enabled", False))

    def _llm_advisor(self, override: bool | None, *, run_id: str) -> Any | None:
        """LLM 只做建议；未配置凭据时返回 None（确定性 Planner 仍是唯一决策者）。"""
        if not self._llm_enabled(override):
            return None
        try:
            from traceguard_agents.gateway.llm_gateway import build_llm_stack
            from traceguard_agents.gateway.llm_planners import LLMProbePlannerAdvisor

            stack = build_llm_stack(self.rt.settings, root_dir=".")
            if not stack:
                return None
            return LLMProbePlannerAdvisor(
                provider=stack["provider"],
                router=stack["router"],
                prompts=stack["prompts"],
                tracker=stack.get("tracker"),
                run_id=run_id,
            )
        except Exception as exc:  # noqa: BLE001 - LLM 不可用不得阻断确定性流程
            logger.warning("llm_probe_advisor_unavailable", error=str(exc)[:200])
            return None

    def _apply_llm_advice(
        self,
        advisor: Any,
        candidates: list[tuple[ProbeDefinition, float]],
        gap_by_probe: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        """把 LLM 建议合并进候选：只允许在规则 EIG 基础上的有限上调。

        LLM 不能新增探针（advisor 内部已校验 probe_id 必须已注册）、不能绕过
        权限/风险/成本/参数校验（执行仍走 ProbeEngine._validate）。
        """
        gaps = [gap_by_probe[d.probe_id] for d, _ in candidates if d.probe_id in gap_by_probe]
        try:
            advice = advisor.suggest(gaps, self.engine.definitions)
        except Exception as exc:  # noqa: BLE001
            return {"called": False, "error": f"{type(exc).__name__}: {exc}"[:200]}
        notes = {
            "called": advice.called,
            "fallback": advice.fallback,
            "error": advice.error,
            "model": advice.model,
            "dropped": advice.dropped,
            "selections": advice.selections,
        }
        if advice.fallback or not advice.selections:
            return notes
        selected = {s["probe_id"]: s for s in advice.selections}
        adjusted: list[tuple[ProbeDefinition, float]] = []
        for definition, eig in candidates:
            sel = selected.get(definition.probe_id)
            if sel:
                # 上限 +0.15 且不超过 0.95：避免 LLM 无限抬高信息增益
                new_eig = min(0.95, max(eig, min(float(sel["info_gain"]), eig + 0.15)))
                adjusted.append((definition, new_eig))
            else:
                adjusted.append((definition, eig))
        candidates[:] = adjusted
        return notes

    def get_plan(self, plan_id: str) -> dict[str, Any] | None:
        rec = self.rt.docs.get(plan_id)
        return rec.to_dict() if rec else None

    def list_plans(self, run_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        return [
            r.to_dict()
            for r in self.rt.docs.list(kind=PLAN_KIND, ref_id=run_id, limit=limit)
        ]

    # ------------------------------------------------------------- gaps
    def _evidence_gaps(
        self,
        run: dict[str, Any],
        diagnosis: dict[str, Any] | None,
        *,
        attempted: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        """从真实 Run 报告与诊断结论推导证据缺口（确定性规则）。

        已尝试过的缺口不再重复推荐（失败事实保留在 plan.steps 中）。
        """
        attempted = attempted or {}
        outcome = (diagnosis or {}).get("outcome")
        if outcome == "CONFIRMED":
            return []
        report = run.get("report") or {}
        steps = report.get("steps") or []
        outcomes = report.get("assertion_outcomes") or []
        failed_steps = [s for s in steps if s.get("status") == "FAILED"]
        failed_biz = [
            o
            for o in outcomes
            if o.get("status") == "FAILED" and o.get("kind") in {"business_state", "api"}
        ]
        failed_ui = [
            o for o in outcomes if o.get("status") == "FAILED" and o.get("kind", "ui") == "ui"
        ]
        locator_fail = [
            s
            for s in failed_steps
            if "locator" in str(s.get("error", "")).lower()
            or "timeout" in str(s.get("error", "")).lower()
        ]
        has_network_obs = any((s.get("observation") or {}).get("network") for s in steps)

        gaps: list[dict[str, Any]] = []

        def add(gap_id: str, probe_id: str, params: dict[str, Any], why: str, eig: float) -> None:
            if gap_id in attempted:
                return
            gaps.append(
                {
                    "gap_id": gap_id,
                    "probe_id": probe_id,
                    "params": params,
                    "why": why,
                    "info_gain": eig,
                }
            )

        if outcome in {None, "INCONCLUSIVE"} or failed_biz:
            add(
                "business_state_unknown",
                "http_state",
                {"template": "get_state_projects", "run_id": run["run_id"]},
                "业务状态证据缺失或不足，需要独立状态查询确认是否真正持久化",
                0.8 if failed_biz else 0.75,
            )
        if has_network_obs or failed_steps:
            add(
                "network_unverified",
                "network_trace",
                {"template": "run_network", "run_id": run["run_id"]},
                "核对网络请求状态码与失败原因，排除环境/接口因素",
                0.55,
            )
        if failed_ui or locator_fail:
            add(
                "dom_unverified",
                "dom_snapshot",
                {"template": "run_dom", "run_id": run["run_id"]},
                "核对真实 DOM 是否渲染期望元素（区分定位器问题与渲染问题）",
                0.45,
            )
        if failed_steps:
            add(
                "backend_log_unchecked",
                "backend_log",
                {"template": "tail_teamflow", "line_limit": 100},
                "核对后端日志尾部，确认服务端是否报错或异常退出",
                0.35,
            )
        if not gaps:
            add(
                "network_unverified",
                "network_trace",
                {"template": "run_network", "run_id": run["run_id"]},
                "兜底核对：确认网络层没有隐藏失败",
                0.3,
            )
        return gaps

    # -------------------------------------------------------------- listing
    def list_executions(
        self, workspace_id: str | None = None, run_id: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        recs = self.rt.docs.list(
            kind="probe_execution", workspace_id=workspace_id, ref_id=run_id, limit=limit
        )
        return [r.to_dict() for r in recs]

    def audit_log(self) -> list[dict[str, Any]]:
        return self.engine.audit_log()

    # ------------------------------------------------------------ executors
    def _exec_http_state(self, params: dict[str, Any]) -> dict[str, Any]:
        template = params.get("template", "get_state_projects")
        base = self.rt.settings.teamflow_base_url.rstrip("/")
        workspace = params.get("workspace", "default")
        if template == "get_state_projects":
            url = f"{base}/api/state/projects?workspace={workspace}"
        elif template == "get_state_project":
            key = str(params.get("project_key", ""))
            if not key or "/" in key:
                raise ValueError("invalid project_key")
            url = f"{base}/api/state/project/{key}"
        else:
            raise ValueError("template not allowed")
        try:
            with urllib.request.urlopen(url, timeout=8) as resp:  # noqa: S310 - 本地受控目标
                data = json.loads(resp.read().decode())
        except TimeoutError as exc:
            raise TimeoutError("TEAMFLOW_STATE_QUERY_TIMEOUT") from exc
        except urllib.error.URLError as exc:
            raise ConnectionError(f"TEAMFLOW_STATE_QUERY_FAILED: {exc.reason}") from exc
        return {"template": template, "response": data}

    def _exec_network_trace(self, params: dict[str, Any]) -> dict[str, Any]:
        run_id = str(params.get("run_id", ""))
        run = self.rt.tasks.get_run(run_id) if run_id else None
        report = (run or {}).get("report") or {}
        entries: list[dict[str, Any]] = []
        for s in report.get("steps", []):
            for n in (s.get("observation") or {}).get("network") or []:
                entries.append({"step_id": s["step_id"], **n})
        return {"template": "run_network", "run_id": run_id, "entries": entries[-100:]}

    def _exec_dom_snapshot(self, params: dict[str, Any]) -> dict[str, Any]:
        run_id = str(params.get("run_id", ""))
        step_id = str(params.get("step_id", ""))
        evds = self.rt.evidence.list(run_id=run_id, source_type="browser", limit=300)
        doms = [
            e
            for e in evds
            if (e.content.get("kind") == "dom")
            and (not step_id or e.content.get("step_id") == step_id)
        ]
        if not doms:
            raise ValueError("no DOM evidence for run")
        ev = doms[-1]
        artifact_id = (ev.content.get("artifact") or {}).get("artifact_id")
        if not artifact_id:
            raise ValueError("DOM evidence has no artifact")
        data, meta = self.rt.artifacts.read(artifact_id)
        text = data.decode(errors="replace")
        return {
            "template": "run_dom",
            "evidence_id": ev.evidence_id,
            "artifact_id": artifact_id,
            "size": meta["size"],
            "excerpt": text[:8000],
        }

    def _exec_backend_log(self, params: dict[str, Any]) -> dict[str, Any]:
        line_limit = int(params.get("line_limit", 100))
        path = Path("logs/teamflow.log")
        if not path.exists():
            raise ValueError("backend log not found (logs/teamflow.log)")
        lines = path.read_text(errors="replace").splitlines()[-line_limit:]
        return {"template": "tail_teamflow", "path": str(path), "lines": redact(lines)}
