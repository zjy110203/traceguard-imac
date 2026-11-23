"""Evidence Graph 服务：把一次 Run 的事件/步骤/证据/诊断/假设连成可查询的图。

设计约束（来自阶段8/阶段12）：
- 节点来自真实记录（Run/Step/Event/Evidence/Assertion/Hypothesis/ProbeExecution）；
- 边必须带 basis（"为什么连这条边"），关联 ≠ 因果；
- `direct_causal` 必须附带可信因果依据，本服务默认不生成；
- 图快照持久化，可按 run_id 复现。
"""

from __future__ import annotations

from typing import Any

from traceguard_core.errors import AppError, ErrorCode
from traceguard_core.ids import utc_now_iso
from traceguard_evidence.graph.graph import EvidenceGraph

from traceguard_api.runtime import Runtime


class GraphService:
    def __init__(self, rt: Runtime) -> None:
        self.rt = rt

    def build_for_run(self, run_id: str, *, persist: bool = True) -> dict[str, Any]:
        run = self.rt.tasks.get_run(run_id)
        if not run:
            raise AppError(ErrorCode.NOT_FOUND, "run not found")
        report = run.get("report") or {}
        ws = run["workspace_id"]
        g = EvidenceGraph()

        run_node = g.add_node(
            "Run",
            run_id,
            node_id=f"node_run_{run_id}",
            status=run["execution_status"],
            verdict=run["test_verdict"],
            attempt=run["attempt"],
        )
        steps = self.rt.tasks.list_steps(run_id)
        step_nodes: dict[str, str] = {}
        for s in steps:
            n = g.add_node(
                "Step",
                s["step_id"],
                node_id=f"node_step_{run_id}_{s['step_id']}",
                index=s["index"],
                action=s["action"],
                status=s["status"],
            )
            step_nodes[s["step_id"]] = n.node_id
            g.add_edge(
                run_node.node_id,
                n.node_id,
                "parent_child",
                basis=f"step.run_id={run_id}",
                workspace_id=ws,
            )

        # 事件 → 步骤 / Run
        events = self.rt.events.list_events(run_id=run_id, limit=500)
        event_nodes: dict[str, str] = {}
        for e in events:
            n = g.add_node(
                "Event",
                e.event_id,
                node_id=f"node_evt_{e.event_id}",
                event_type=e.event_type,
                sequence=e.sequence,
                occurred_at=e.occurred_at,
            )
            event_nodes[e.event_id] = n.node_id
            step_ref = str((e.payload or {}).get("step_id", ""))
            if step_ref and step_ref in step_nodes:
                g.add_edge(
                    n.node_id,
                    step_nodes[step_ref],
                    "emitted_by",
                    basis=f"event.payload.step_id={step_ref}",
                    workspace_id=ws,
                )
            else:
                g.add_edge(
                    n.node_id,
                    run_node.node_id,
                    "same_execution_context",
                    basis=f"event.context_refs.run={run_id}",
                    workspace_id=ws,
                )

        # 证据 → 步骤 / Run
        evidence = self.rt.evidence.list(run_id=run_id, limit=500)
        ev_nodes: dict[str, str] = {}
        for ev in evidence:
            n = g.add_node(
                "Evidence",
                ev.evidence_id,
                node_id=f"node_evd_{ev.evidence_id}",
                kind=ev.kind,
                source_type=ev.source_type,
                trust=ev.source_trust,
                integrity=ev.integrity_check,
            )
            ev_nodes[ev.evidence_id] = n.node_id
            step_ref = str((ev.content or {}).get("step_id", ""))
            if step_ref and step_ref in step_nodes:
                g.add_edge(
                    n.node_id,
                    step_nodes[step_ref],
                    "evaluates",
                    basis=f"evidence.content.step_id={step_ref}",
                    workspace_id=ws,
                )
            else:
                g.add_edge(
                    n.node_id,
                    run_node.node_id,
                    "same_execution_context",
                    basis="evidence.run_id == run.run_id",
                    workspace_id=ws,
                )
            if ev.raw_ref and ev.raw_ref in ev_nodes:
                g.add_edge(
                    n.node_id,
                    ev_nodes[ev.raw_ref],
                    "derived_from",
                    basis=f"evidence.raw_ref={ev.raw_ref}",
                    workspace_id=ws,
                )

        # 断言结果 → 证据
        for o in report.get("assertion_outcomes") or []:
            an = g.add_node(
                "Assertion",
                f"{run_id}:{o['assertion_id']}",
                node_id=f"node_assert_{run_id}_{o['assertion_id']}",
                status=o["status"],
                reason=o.get("reason", ""),
            )
            g.add_edge(
                an.node_id,
                run_node.node_id,
                "parent_child",
                basis="assertion_outcome.run_id",
                workspace_id=ws,
            )
            # 与对应步骤证据建立 evaluates 边（按步骤 action=assert 匹配）
            for s in steps:
                if s["action"] == "assert":
                    for ev in evidence:
                        if str((ev.content or {}).get("step_id", "")) == s["step_id"]:
                            g.add_edge(
                                an.node_id,
                                f"node_evd_{ev.evidence_id}",
                                "evaluates",
                                basis="assertion 由该步骤观测证据评估",
                                workspace_id=ws,
                            )

        # 诊断 → 假设 → 证据
        diag_recs = self.rt.docs.list(kind="diagnosis", ref_id=run_id, limit=10)
        for rec in diag_recs:
            d = rec.doc
            dn = g.add_node(
                "Source",
                d["diagnosis_id"],
                node_id=f"node_diag_{d['diagnosis_id']}",
                outcome=d.get("outcome"),
                disposition=d.get("disposition"),
            )
            for h in d.get("hypotheses", []):
                hn = g.add_node(
                    "Hypothesis",
                    h["hypothesis_id"],
                    node_id=f"node_hyp_{h['hypothesis_id']}",
                    status=h["status"],
                    domain=h.get("failure_domain"),
                    mechanism=h.get("failure_mechanism"),
                )
                g.add_edge(
                    dn.node_id,
                    hn.node_id,
                    "parent_child",
                    basis="diagnosis.hypotheses",
                    workspace_id=ws,
                )
                for sup in h.get("supporting", []):
                    if sup["id"] in ev_nodes:
                        g.add_edge(
                            hn.node_id,
                            ev_nodes[sup["id"]],
                            "supports",
                            basis=f"diagnosis:{sup.get('trust')}:{'confirming' if sup.get('confirming') else 'supporting'}",
                            workspace_id=ws,
                        )
                for con in h.get("contradicting", []):
                    if con["id"] in ev_nodes:
                        g.add_edge(
                            hn.node_id,
                            ev_nodes[con["id"]],
                            "contradicts",
                            basis="diagnosis:contradicting_evidence",
                            workspace_id=ws,
                        )

        # 探针执行 → 证据 → 假设（RUN-006 WP1-02）
        hypothesis_nodes: set[str] = set()
        for rec_d in diag_recs:
            for h in rec_d.doc.get("hypotheses", []):
                hypothesis_nodes.add(f"node_hyp_{h['hypothesis_id']}")
        probe_recs = self.rt.docs.list(kind="probe_execution", ref_id=run_id, limit=200)
        for rec in probe_recs:
            p = rec.doc
            pn = g.add_node(
                "ProbeExecution",
                p.get("execution_id", rec.id),
                node_id=f"node_probe_{p.get('execution_id', rec.id)}",
                probe_id=p.get("probe_id"),
                status=p.get("status"),
                budget_scope=p.get("budget_scope"),
                diagnosis_id=p.get("diagnosis_id"),
            )
            g.add_edge(
                pn.node_id,
                run_node.node_id,
                "same_execution_context",
                basis="probe_execution.run_id == run.run_id",
                workspace_id=ws,
            )
            for eid in p.get("evidence_refs") or []:
                if eid in ev_nodes:
                    # 证据由该探针产生（派生关系，不是因果）
                    g.add_edge(
                        ev_nodes[eid],
                        pn.node_id,
                        "derived_from",
                        basis=f"probe_execution.evidence_refs={eid}",
                        workspace_id=ws,
                    )
            # 探针评估了哪些假设：按假设引用的探针证据反查
            for rec_d in diag_recs:
                for h in rec_d.doc.get("hypotheses", []):
                    refs = {x["id"] for x in h.get("supporting", [])} | {
                        x["id"] for x in h.get("contradicting", [])
                    }
                    if refs & set(p.get("evidence_refs") or []):
                        hid = f"node_hyp_{h['hypothesis_id']}"
                        if hid in hypothesis_nodes:
                            g.add_edge(
                                pn.node_id,
                                hid,
                                "evaluates",
                                basis="hypothesis 引用了该探针产生的证据",
                                workspace_id=ws,
                            )

        snapshot = g.to_dict()
        snapshot.update(
            {
                "run_id": run_id,
                "workspace_id": ws,
                "generated_at": utc_now_iso(),
                "stats": {"nodes": len(snapshot["nodes"]), "edges": len(snapshot["edges"])},
            }
        )
        if persist:
            self.rt.docs.put(
                doc_id=f"graph_{run_id}",
                kind="graph",
                doc=snapshot,
                workspace_id=ws,
                ref_id=run_id,
                status="READY",
            )
        return snapshot

    def get(self, run_id: str) -> dict[str, Any]:
        rec = self.rt.docs.get(f"graph_{run_id}")
        if rec:
            return rec.to_dict()
        return self.build_for_run(run_id)

    def list_graphs(self, workspace_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        recs = self.rt.docs.list(kind="graph", workspace_id=workspace_id, limit=limit)
        return [
            {
                "run_id": r.ref_id,
                "workspace_id": r.workspace_id,
                "nodes": (r.doc.get("stats") or {}).get("nodes", 0),
                "edges": (r.doc.get("stats") or {}).get("edges", 0),
                "generated_at": r.doc.get("generated_at"),
            }
            for r in recs
        ]
