"""Active Diagnosis 探针服务：真实只读探针 + 五项校验 + 结果证据化。

探针全部只读，且必须走白名单模板：
- `http_state`：独立状态查询（受控本地 TeamFlow API）；
- `network_trace`：读取已落库 Run 的网络观测（不重新访问目标）；
- `dom_snapshot`：读取已落库 DOM 证据；
- `backend_log`：读取本地受控服务日志尾部（脱敏）。
任何越权/非白名单/超预算调用都会被拒绝并审计。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from traceguard_core.errors import AppError, ErrorCode
from traceguard_core.ids import new_id, utc_now_iso
from traceguard_core.logging import get_logger, redact
from traceguard_evidence.probe.planner import (
    ActivePlanner,
    ProbeBudget,
    ProbeDefinition,
    ProbeEngine,
    ProbeResult,
)

from traceguard_api.runtime import Runtime

logger = get_logger("traceguard.service.probe")

PERMISSION = "probe.read"


class ProbeService:
    def __init__(self, rt: Runtime) -> None:
        self.rt = rt
        self.engine = ProbeEngine(ProbeBudget(max_probes=50, max_total_cost=100.0))
        self.engine.grant_permission(PERMISSION)
        self.planner = ActivePlanner(self.engine)
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

    # -------------------------------------------------------------- execute
    def execute(
        self, probe_id: str, params: dict[str, Any], workspace_id: str = "default"
    ) -> dict[str, Any]:
        params = dict(params or {})
        params.setdefault("workspace", workspace_id)
        result: ProbeResult = self.engine.execute(probe_id, params, workspace_id)
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
                        "params": params,
                        "data": result.data,
                        "source": result.source,
                        "execution_id": execution_id,
                    },
                    source_trust="TRUSTED",
                    do_redact=True,
                    run_id=params.get("run_id") or None,
                )
                evidence_ids.append(evd_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning("probe_evidence_failed", error=str(exc))

        doc = {
            "execution_id": execution_id,
            "probe_id": probe_id,
            "workspace_id": workspace_id,
            "run_id": params.get("run_id"),
            "diagnosis_id": params.get("diagnosis_id"),
            "status": result.status,
            "permission_decision": "ALLOW" if result.status != "BLOCKED" else "DENY",
            "risk_level": "LOW",
            "params": redact(params),
            "result": result.data,
            "reason": result.reason,
            "source": result.source,
            "evidence_refs": evidence_ids,
            "budget": {
                "calls_used": self.engine._calls,
                "cost_used": round(self.engine._cost, 3),
                "max_probes": self.engine.budget.max_probes,
            },
            "executed_at": utc_now_iso(),
        }
        self.rt.docs.put(
            doc_id=execution_id,
            kind="probe_execution",
            doc=doc,
            workspace_id=workspace_id,
            ref_id=params.get("run_id"),
            status=result.status,
        )
        return doc

    def suggest(self, run_id: str, diagnosis_id: str | None = None) -> list[dict[str, Any]]:
        """基于证据缺口给出探针建议（确定性规则，不靠 LLM 猜测）。"""
        run = self.rt.tasks.get_run(run_id)
        if not run:
            raise AppError(ErrorCode.NOT_FOUND, "run not found")
        suggestions: list[dict[str, Any]] = []
        diag = self.rt.docs.get(diagnosis_id) if diagnosis_id else None
        outcome = (diag.doc or {}).get("outcome") if diag else None
        if outcome in {"INCONCLUSIVE", None}:
            suggestions.append(
                {
                    "probe_id": "http_state",
                    "params": {"template": "get_state_projects", "run_id": run_id},
                    "why": "业务状态证据缺失或不足，需要独立状态查询确认是否真正持久化",
                    "info_gain": 0.8,
                }
            )
        suggestions.append(
            {
                "probe_id": "network_trace",
                "params": {"template": "run_network", "run_id": run_id},
                "why": "核对网络请求状态码与失败原因，排除环境/接口因素",
                "info_gain": 0.5,
            }
        )
        suggestions.append(
            {
                "probe_id": "dom_snapshot",
                "params": {"template": "run_dom", "run_id": run_id},
                "why": "核对真实 DOM 是否渲染期望元素（区分定位器问题与渲染问题）",
                "info_gain": 0.4,
            }
        )
        for s in suggestions:
            d = self.engine.definitions[s["probe_id"]]
            s["score"] = round(self.planner.score(d, s["info_gain"]), 4)
        return sorted(suggestions, key=lambda x: x["score"], reverse=True)

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
        with urllib.request.urlopen(url, timeout=8) as resp:  # noqa: S310 - 本地受控目标
            data = json.loads(resp.read().decode())
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
