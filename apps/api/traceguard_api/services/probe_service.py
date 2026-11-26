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
from traceguard_evidence.probe.contract import (
    build_probe_evidence_contract,
    canonical_params_hash,
    derive_idempotency_key,
)
from traceguard_evidence.probe.feedback import ProbeSignal
from traceguard_evidence.probe.planner import (
    PROVISIONAL_NOTE,
    ActivePlanner,
    ProbeBudget,
    ProbeBudgetRegistry,
    ProbeDefinition,
    ProbeEngine,
    ProbeResult,
    ProbeUnavailable,
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


def _iso(epoch: float) -> str:
    """epoch 秒 → UTC ISO8601（证据时间语义统一为 UTC，展示层再本地化）。"""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(float(epoch or 0)))


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
        """读取预算文档。异常必须向上抛出：由 Registry 转成 UNAVAILABLE 并阻断探针。

        返回的文档带 `doc_version`（DocStore 行版本），供跨进程 CAS 使用。
        """
        rec = self.rt.docs.get(budget_doc_id(scope_key))
        if rec is None:
            return None
        doc = dict(rec.doc or {})
        doc["doc_version"] = rec.version
        return doc

    def _save_budget_doc(self, budget: ProbeBudget) -> None:
        """CAS 写入预算；版本冲突以 CONFLICT 抛出，由 Registry 重新加载后重试。"""
        rec = self.rt.docs.put(
            doc_id=budget.budget_id,
            kind=BUDGET_KIND,
            doc={k: v for k, v in budget.snapshot().items() if k != "doc_version"},
            workspace_id=budget.workspace_id,
            ref_id=budget.owner_run_id or budget.owner_diagnosis_id or None,
            status="CLOSED" if budget.is_closed else "OPEN",
            version=budget.doc_version or 1,
        )
        budget.doc_version = rec.version

    def budget_for(
        self, *, run_id: str = "", diagnosis_id: str = "", workspace_id: str = "default"
    ) -> ProbeBudget:
        # RUN-011-R1.1 WP1 §1.4：Workspace 必须来自 Run / Diagnosis 的真实归属，
        # 不采信调用方声明（旧实现会让探针预算静默落入 default）。
        ws = self._resolve_workspace(
            run_id=run_id, diagnosis_id=diagnosis_id, declared=workspace_id
        )
        if diagnosis_id:
            scope = f"diagnosis:{diagnosis_id}"
        elif run_id:
            scope = f"run:{run_id}"
        else:
            scope = f"manual:{ws}"
        return self.budgets.get_or_create(
            scope, run_id=run_id, diagnosis_id=diagnosis_id, workspace_id=ws
        )

    def _resolve_workspace(
        self, *, run_id: str = "", diagnosis_id: str = "", declared: str = ""
    ) -> str:
        """解析探针作用域的**权威 Workspace**（fail-closed）。"""
        if diagnosis_id:
            rec = self.rt.docs.get(diagnosis_id)
            if rec is None or rec.kind != "diagnosis":
                raise AppError(ErrorCode.NOT_FOUND, f"DIAGNOSIS_NOT_FOUND: {diagnosis_id}")
            ws = str(rec.workspace_id or (rec.doc or {}).get("workspace_id") or "")
            run_ref = str((rec.doc or {}).get("run_id") or "")
            if run_id and run_ref and run_ref != run_id:
                raise AppError(
                    ErrorCode.CONFLICT,
                    f"DIAGNOSIS_RUN_MISMATCH: {diagnosis_id} 属于 run {run_ref}，"
                    f"与请求 run {run_id} 不一致",
                )
            if not ws:
                raise AppError(ErrorCode.VALIDATION_ERROR, "DIAGNOSIS_WORKSPACE_MISSING")
        elif run_id:
            run = self.rt.tasks.get_run(run_id)
            if not run:
                raise AppError(ErrorCode.NOT_FOUND, f"RUN_NOT_FOUND: {run_id}")
            ws = str(run.get("workspace_id") or "")
            if not ws:
                raise AppError(ErrorCode.VALIDATION_ERROR, "RUN_WORKSPACE_MISSING")
        else:
            ws = (declared or "").strip() or "default"
        if declared and declared.strip() and declared.strip() != ws:
            raise AppError(
                ErrorCode.AUTHORIZATION_ERROR,
                f"CROSS_WORKSPACE_PROBE: 请求 workspace '{declared}' 与实体 workspace "
                f"'{ws}' 不一致",
            )
        return ws

    def close_budget(
        self, *, run_id: str = "", diagnosis_id: str = "", reason: str = "DIAGNOSIS_COMPLETED"
    ) -> dict[str, Any] | None:
        scope = f"diagnosis:{diagnosis_id}" if diagnosis_id else (f"run:{run_id}" if run_id else "")
        if not scope:
            raise AppError(ErrorCode.VALIDATION_ERROR, "run_id or diagnosis_id required")
        budget = self.budgets.close(scope, reason)
        return budget.snapshot() if budget else None

    def budgets_snapshot(
        self, workspace_id: str | None = None, *, workspace_ids: list[str] | None = None
    ) -> list[dict[str, Any]]:
        """内存中的活跃预算 + 持久化的历史上预算文档（按 Workspace 作用域过滤）。"""
        active = {b["budget_id"]: b for b in self.budgets.snapshot()}
        for rec in self.rt.docs.list(
            kind=BUDGET_KIND, workspace_id=workspace_id, limit=200, workspace_ids=workspace_ids
        ):
            active.setdefault(rec.id, rec.doc)
        out = sorted(active.values(), key=lambda b: b.get("started_at") or 0, reverse=True)
        if workspace_ids is not None:
            allowed = set(workspace_ids)
            out = [b for b in out if str(b.get("workspace_id") or "default") in allowed]
        return out

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
        run_id = run_id or params.get("run_id") or None
        diagnosis_id = diagnosis_id or params.get("diagnosis_id") or None
        # RUN-011-R1.1 WP1 §1.4：探针 Workspace 必须继承 Run / Diagnosis 的真实归属。
        workspace_id = self._resolve_workspace(
            run_id=run_id or "", diagnosis_id=diagnosis_id or "", declared=workspace_id
        )
        params["workspace"] = workspace_id
        # RUN-011 WP2：未知/未注册探针在预算占位前就拒绝。
        # 旧行为会生成一条 BLOCKED 执行记录并返回 201，调用方无法区分
        # "探针不存在"与"探针被策略拒绝"，也会污染执行历史。
        if probe_id not in self.engine.definitions:
            raise AppError(ErrorCode.NOT_FOUND, f"UNKNOWN_PROBE: {probe_id}")
        # RUN-018 WP5 §5.2 幂等键：显式 idempotency_key 的重复请求只执行一次
        # （返回已有执行记录，不重复扣预算）；自动键唯一，延迟复测不会被误去重。
        explicit_idem = str(params.get("idempotency_key") or "").strip()
        if explicit_idem:
            replay = self._find_idempotent_execution(
                probe_id, explicit_idem, run_id=run_id, diagnosis_id=diagnosis_id
            )
            if replay is not None:
                return {**replay, "idempotent_replay": True}
        b = budget or self.budget_for(
            run_id=run_id or "", diagnosis_id=diagnosis_id or "", workspace_id=workspace_id
        )
        definition = self.engine.definitions[probe_id]
        # 预算占位由 Registry 完成（跨进程 CAS + 故障即阻断），探针执行前必须已落库
        result: ProbeResult = self.engine.execute(
            probe_id, params, workspace_id, budget=b, reserve=self.budgets.reserve
        )

        execution_id = new_id("probeexec")
        collected_at = time.time()
        ingested_at = utc_now_iso()
        observed_at = result.observed_at or collected_at
        run_ended_at = self._run_ended_at(run_id)
        idempotency_key = derive_idempotency_key(
            probe_id,
            run_id=run_id,
            diagnosis_id=diagnosis_id,
            params=params,
            explicit=explicit_idem or None,
            execution_id=execution_id,
        )
        evidence_ids: list[str] = []
        contract = build_probe_evidence_contract(
            probe_id=probe_id,
            probe_execution_id=execution_id,
            run_id=run_id,
            diagnosis_id=diagnosis_id,
            workspace_id=workspace_id,
            probe_status=result.status,
            observed_at=observed_at,
            captured_at=collected_at,
            evidence_refs=evidence_ids,
            failure_reason="" if result.status == "SUCCEEDED" else result.reason,
            duration_ms=result.duration_ms,
            cost=definition.estimated_cost,
            idempotency_key=idempotency_key,
            run_scope=False,
            run_ended_at=run_ended_at,
        )
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
                        # RUN-011 WP2 证据溯源与时间语义：
                        # - run_id / diagnosis_id：探针必须绑定到具体 Run 与诊断版本；
                        # - observed_at：真实观测时刻（epoch 秒）；
                        # - collected_at：结果装配为证据的时刻（epoch 秒）；
                        # - ingested_at：写入证据库的时刻（ISO8601）。
                        # 事后探针（observed_at > run.ended_at）只能提出 TIMING 假设，
                        # 不能反驳 Run 当时的事实。
                        "run_id": run_id,
                        "diagnosis_id": diagnosis_id,
                        "workspace_id": workspace_id,
                        "observed_at": observed_at,
                        "observed_at_iso": _iso(observed_at),
                        "collected_at": collected_at,
                        "collected_at_iso": _iso(collected_at),
                        "ingested_at": ingested_at,
                        "budget_scope": b.scope_key,
                        # RUN-018 WP5 §5.2 探针证据契约（与 probe_execution 文档同构）
                        "probe_evidence_contract": {
                            **contract,
                            "evidence_refs": [evd_id],
                        },
                    },
                    source_trust="TRUSTED",
                    do_redact=True,
                    run_id=run_id,
                )
                evidence_ids.append(evd_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning("probe_evidence_failed", error=str(exc))
        contract["evidence_refs"] = list(evidence_ids)

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
            "observed_at": observed_at,
            "collected_at": collected_at,
            "ingested_at": ingested_at,
            "executed_at": ingested_at,
            # RUN-018 WP5 §5.2 探针证据契约字段（别名与新增字段，向后兼容）
            "probe_execution_id": execution_id,
            "probe_status": result.status,
            "captured_at": _iso(collected_at),
            "source_type": "probe",
            "source_trust": contract["source_trust"],
            "time_alignment": contract["time_alignment"],
            "raw_result_ref": contract["raw_result_ref"],
            "failure_reason": contract["failure_reason"],
            "cost": contract["cost"],
            "idempotency_key": idempotency_key,
            "contract": contract,
            "params_hash": canonical_params_hash(params),
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

    # ------------------------------------------------------- idempotent replay
    def _find_idempotent_execution(
        self,
        probe_id: str,
        idempotency_key: str,
        *,
        run_id: str | None,
        diagnosis_id: str | None,
    ) -> dict[str, Any] | None:
        """同一 (probe_id, run_id, diagnosis_id, idempotency_key) 的已有执行记录。"""
        recs = self.rt.docs.list(kind="probe_execution", ref_id=run_id, limit=200)
        for rec in recs:
            doc = rec.doc or {}
            if (
                doc.get("idempotency_key") == idempotency_key
                and doc.get("probe_id") == probe_id
                and doc.get("run_id") == run_id
                and doc.get("diagnosis_id") == diagnosis_id
            ):
                return dict(doc)
        return None

    def _run_ended_at(self, run_id: str | None) -> float | None:
        """Run 结束时刻（epoch 秒）；未知返回 None（时间对齐三态判 UNKNOWN）。"""
        if not run_id:
            return None
        run = self.rt.tasks.get_run(run_id) or {}
        ended = run.get("ended_at")
        if ended in (None, "", 0):
            return None
        if isinstance(ended, (int, float)):
            return float(ended)
        from datetime import datetime

        try:
            return datetime.fromisoformat(str(ended).replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None

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
        EIG Score → Planner.choose → Execution → New Evidence → 回灌假设 →
        重新裁决 → 重算 Gap → Continue / Stop。

        关键边界（RUN-006 WP1-02）：
        - 探针失败/被拒绝不写入任何"目标系统故障"证据，只是没有提供证据；
        - CONFIRMED 仍由确定性规则 + TRUSTED 确认证据产生，LLM 不得确认根因；
        - 未解决证据冲突 → STOP_CONFLICT_UNRESOLVED，不再烧预算。
        """
        run = self.rt.tasks.get_run(run_id)
        if not run:
            raise AppError(ErrorCode.NOT_FOUND, "run not found")
        workspace_id = run.get("workspace_id") or "default"
        diag = self.rt.docs.get(diagnosis_id) if diagnosis_id else None
        if diagnosis_id:
            # RUN-011-R1.1 WP1 §1.4：Diagnosis 必须属于同一 Run 与同一 Workspace
            if diag is None or diag.kind != "diagnosis":
                raise AppError(ErrorCode.NOT_FOUND, f"DIAGNOSIS_NOT_FOUND: {diagnosis_id}")
            diag_run = str((diag.doc or {}).get("run_id") or "")
            if diag_run and diag_run != run_id:
                raise AppError(
                    ErrorCode.CONFLICT,
                    f"DIAGNOSIS_RUN_MISMATCH: {diagnosis_id} 属于 run {diag_run}，"
                    f"与请求 run {run_id} 不一致",
                )
            diag_ws = str(diag.workspace_id or (diag.doc or {}).get("workspace_id") or "")
            if diag_ws and diag_ws != workspace_id:
                raise AppError(
                    ErrorCode.AUTHORIZATION_ERROR,
                    f"CROSS_WORKSPACE_PROBE: Diagnosis 属于 workspace '{diag_ws}'，"
                    f"与 Run 的 '{workspace_id}' 不一致",
                )
        diag_doc = rec_doc = dict(diag.doc or {}) if diag else None
        budget = self.budget_for(
            run_id=run_id, diagnosis_id=diagnosis_id or "", workspace_id=workspace_id
        )
        diagnosis_service = self._diagnosis_service() if diagnosis_id else None
        diagnosis_before = _diagnosis_snapshot(diag_doc)

        plan_id = new_id("probeplan")
        steps: list[dict[str, Any]] = []
        evidence_ids: list[str] = []
        assessed: list[dict[str, Any]] = []
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
            if diag_doc and diag_doc.get("outcome") == "CONFIRMED":
                stop_reason = "STOP_CONFIRMED"
                stop_detail = "诊断已确认根因，停止继续消耗探针预算"
                break
            conflicts = list((diag_doc or {}).get("unresolved_conflicts") or [])
            if conflicts:
                stop_reason = "STOP_CONFLICT_UNRESOLVED"
                stop_detail = f"证据冲突未裁决（{', '.join(conflicts[:3])}），需人工介入"
                break

            permissions_valid = PERMISSION in self.engine._permissions
            candidates: list[tuple[ProbeDefinition, float]] = []
            gap_by_probe: dict[str, dict[str, Any]] = {}
            for gap in self._evidence_gaps(run, diag_doc, attempted=attempted):
                d = self.engine.definitions.get(gap["probe_id"])
                if d is None:
                    continue
                candidates.append((d, gap["info_gain"]))
                gap_by_probe[gap["probe_id"]] = gap

            if not permissions_valid:
                stop_reason = "STOP_PERMISSION_INVALID"
                stop_detail = "探针权限失效，停止执行"
                break

            if advisor is not None and candidates:
                llm_notes = self._apply_llm_advice(advisor, candidates, gap_by_probe)
            probe, reason = self.planner.choose(
                candidates,
                confirmed=False,  # 已在循环顶部处理 CONFIRMED
                conflicts_unresolved=bool(conflicts),
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
                    "STOP_CONFLICT_UNRESOLVED": "证据冲突未裁决",
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
            step: dict[str, Any] = {
                "iteration": iteration,
                "gap_id": gap["gap_id"],
                "probe_id": probe.probe_id,
                "why": gap["why"],
                "info_gain": gap["info_gain"],
                "score": score,
                "status": result_doc["status"],
                "probe_status": result_doc.get("probe_status", result_doc["status"]),
                "reason": result_doc.get("reason") or "",
                "evidence_ids": result_doc.get("evidence_refs") or [],
                "duration_ms": result_doc.get("duration_ms", 0),
                "cost": result_doc.get("cost", 0.0),
                "time_alignment": result_doc.get("time_alignment", ""),
                "idempotency_key": result_doc.get("idempotency_key", ""),
                "execution_id": result_doc["execution_id"],
                "budget": result_doc["budget"],
                "applied_to_diagnosis": False,
            }

            if result_doc["status"] == "SUCCEEDED" and diagnosis_id and diagnosis_service:
                import time as _time

                signal = ProbeSignal(
                    probe_id=probe.probe_id,
                    execution_id=result_doc["execution_id"],
                    status=result_doc["status"],
                    evidence_ids=list(result_doc.get("evidence_refs") or []),
                    data=result_doc.get("result") or {},
                    params=params,
                    gap_id=gap["gap_id"],
                    # RUN-010 WP4 时间对齐：探针在 Run 结束后执行 → 事后观测
                    observed_at=result_doc.get("observed_at") or _time.time(),
                    run_scope=False,
                )
                assessment = diagnosis_service.reassess(
                    diagnosis_id, [signal], run_report=run.get("report") or {}
                )
                diag_doc = assessment["diagnosis"]
                step["applied_to_diagnosis"] = True
                step["diagnosis_after"] = assessment["after"]
                step["hypothesis_updates"] = [
                    u for fb in assessment["feedback"] for u in fb["updates"]
                ]
                assessed.append(
                    {
                        "probe_id": probe.probe_id,
                        "execution_id": result_doc["execution_id"],
                        "before": assessment["before"],
                        "after": assessment["after"],
                        "updates": step["hypothesis_updates"],
                    }
                )
            elif result_doc["status"] == "SUCCEEDED":
                step["applied_to_diagnosis"] = False
                step["note"] = "未提供 diagnosis_id：证据已落库但未回灌任何诊断"

            steps.append(step)

            # 探针被拒绝：区分权限 / 预算 / 预算不可用，给出明确停止原因
            if result_doc["status"] == "BLOCKED":
                stop_reason, stop_detail = _blocked_stop(result_doc.get("reason") or "")
                break
            # 探针自身失败/超时/不可用（UNKNOWN）：保留失败事实，
            # 但绝不当作目标系统故障证据（RUN-018 WP5 §5.3）
            if result_doc["status"] in {"FAILED", "TIMED_OUT", "UNKNOWN"}:
                continue
        else:
            stop_reason, stop_detail = "STOP_ITERATION_LIMIT", f"达到最大迭代 {max_iterations}"

        gaps_after = self._evidence_gaps(run, diag_doc, attempted=attempted)
        if close_on_confirm and (diag_doc or {}).get("outcome") == "CONFIRMED":
            self.budgets.close(budget.scope_key, "STOP_CONFIRMED")

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
            "diagnosis_before": diagnosis_before,
            "diagnosis_after": _diagnosis_snapshot(diag_doc),
            "assessments": assessed,
            "unresolved_conflicts": list((diag_doc or {}).get("unresolved_conflicts") or []),
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
            doc = dict(rec_doc or {})
            latest = self.rt.docs.get(diagnosis_id or "")
            doc = dict((latest.doc if latest else doc) or {})
            doc["last_probe_plan_id"] = plan_id
            doc["probe_evidence_ids"] = plan["evidence_ids"]
            doc["updated_at"] = utc_now_iso()
            self.rt.docs.put(
                doc_id=diagnosis_id or "",
                kind="diagnosis",
                doc=doc,
                workspace_id=diag.workspace_id,
                ref_id=run_id,
                status=doc.get("outcome") or diag.status,
            )
        self._refresh_graph(run_id, workspace_id)
        return plan

    # ------------------------------------------------------------- 关联服务
    def _diagnosis_service(self) -> Any:
        svc = getattr(self.rt, "services_diagnosis", None)
        if svc is not None:
            return svc
        from traceguard_api.services.diagnosis_service import DiagnosisService

        svc = DiagnosisService(self.rt)
        self.rt.services_diagnosis = svc  # type: ignore[attr-defined]
        return svc

    def _refresh_graph(self, run_id: str, workspace_id: str) -> None:
        """探针产生新证据后更新 Evidence Graph（失败不阻断探针闭环）。"""
        try:
            svc = getattr(self.rt, "services_graph", None)
            if svc is None:
                from traceguard_api.services.graph_service import GraphService

                svc = GraphService(self.rt)
                self.rt.services_graph = svc  # type: ignore[attr-defined]
            svc.build_for_run(run_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("probe_graph_refresh_failed", run_id=run_id, error=str(exc))

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
            # 诚实标注：LLM 只影响候选排序，返回的参数并未参与执行
            "effect": "ranking_only",
            "params_used_in_execution": False,
            "applied_to_execution": False,
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

    def list_plans(
        self,
        run_id: str | None = None,
        limit: int = 50,
        *,
        workspace_ids: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        return [
            r.to_dict()
            for r in self.rt.docs.list(
                kind=PLAN_KIND, ref_id=run_id, limit=limit, workspace_ids=workspace_ids
            )
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
        # RUN-011 WP2：业务状态是否已有"有效判定"（PASSED/FAILED）。
        # 定位器在步骤阶段失败时，business_state 断言只会是 NOT_EVALUATED，
        # 此时"是否真正落库"完全未知 —— 这是区分 LOCATOR / PERSISTENCE 的关键信息，
        # 必须补一次独立状态查询，而不是直接跳过。
        has_business_verdict = any(
            o.get("kind") in {"business_state", "api"} and o.get("status") in {"PASSED", "FAILED"}
            for o in outcomes
        )

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

        if outcome in {None, "INCONCLUSIVE"} or failed_biz or not has_business_verdict:
            add(
                "business_state_unknown",
                "http_state",
                {"template": "get_state_projects", "run_id": run["run_id"]},
                "业务状态证据缺失或不足，需要独立状态查询确认是否真正持久化",
                0.8 if failed_biz else 0.7,
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
        self,
        workspace_id: str | None = None,
        run_id: str | None = None,
        limit: int = 100,
        *,
        workspace_ids: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        recs = self.rt.docs.list(
            kind="probe_execution",
            workspace_id=workspace_id,
            ref_id=run_id,
            limit=limit,
            workspace_ids=workspace_ids,
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
        except urllib.error.HTTPError as exc:
            # RUN-018 WP5 §5.4 场景 E：状态服务 502/503/504 = 探针不可用/无法判定，
            # 记为 UNKNOWN（零证据），不冒充目标系统故障（也不是探针自身 FAIL）。
            if exc.code in (502, 503, 504):
                raise ProbeUnavailable(f"PROBE_TARGET_UNAVAILABLE: HTTP {exc.code}") from exc
            raise ConnectionError(f"TEAMFLOW_STATE_QUERY_FAILED: HTTP {exc.code}") from exc
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


def _diagnosis_snapshot(doc: dict[str, Any] | None) -> dict[str, Any] | None:
    if not doc:
        return None
    return {
        "diagnosis_id": doc.get("diagnosis_id"),
        "outcome": doc.get("outcome"),
        "disposition": doc.get("disposition"),
        "root_causes": list(doc.get("root_causes") or []),
        "unresolved_conflicts": list(doc.get("unresolved_conflicts") or []),
    }


def _blocked_stop(reason: str) -> tuple[str, str]:
    """把探针被拒绝的原因映射成明确的停止原因（不允许含糊地"继续执行"）。"""
    text = (reason or "").upper()
    if "BUDGET" in text and "UNAVAILABLE" in text:
        return "STOP_BUDGET_UNAVAILABLE", reason
    if "BUDGET" in text:
        return "STOP_BUDGET_EXHAUSTED", reason
    if any(
        k in text for k in ("PERMISSION", "DENIED", "CROSS_WORKSPACE", "WHITELIST", "NOT_ALLOWED")
    ):
        return "STOP_PERMISSION_INVALID", reason
    return "STOP_PROBE_BLOCKED", reason
