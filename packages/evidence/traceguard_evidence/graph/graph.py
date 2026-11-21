"""Evidence Correlation Graph（阶段8，C07）。

核心不变量（路线图阶段8 + 架构原则9）：
- 节点类型：Run/Step/Event/Evidence/Assertion/Hypothesis/ProbeExecution/Source；
- 边类型：parent_child / emitted_by / derived_from / correlated_by_* / same_execution_context /
  temporal / supports / contradicts / evaluates；direct_causal 需明确可信依据；
- 每条边记录建立依据（basis: 来源字段/规则/版本）；
- 关联只依据已校验的 context_refs；未校验关联保留 unknown，不生成确定性边；
- 时间接近/相同 trace_id/相同 request_id 不自动产生 direct_causal；
- 来源独立性：同源派生证据不算多个独立佐证；
- supports/contradicts 双向保留；Hypothesis 不自动升级为根因。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from traceguard_core.ids import new_id

NODE_TYPES = {
    "Run",
    "Step",
    "Event",
    "Evidence",
    "Assertion",
    "Hypothesis",
    "ProbeExecution",
    "Source",
}
EDGE_TYPES = {
    "parent_child",
    "emitted_by",
    "derived_from",
    "correlated_by_trace_id",
    "correlated_by_request_id",
    "same_execution_context",
    "temporal",
    "supports",
    "contradicts",
    "evaluates",
    "direct_causal",
}


@dataclass
class Node:
    node_id: str
    node_type: str
    ref_id: str
    properties: dict[str, Any] = field(default_factory=dict)


@dataclass
class Edge:
    edge_id: str
    src: str
    dst: str
    relation: str
    basis: str  # 建立依据：来源字段/规则/版本
    workspace_id: str
    causal_evidence: str | None = None  # direct_causal 专用：可信直接因果依据

    def to_dict(self) -> dict[str, Any]:
        return {
            "edge_id": self.edge_id,
            "src": self.src,
            "dst": self.dst,
            "relation": self.relation,
            "basis": self.basis,
            "workspace_id": self.workspace_id,
            "causal_evidence": self.causal_evidence,
        }


class EvidenceGraph:
    def __init__(self) -> None:
        self._nodes: dict[str, Node] = {}
        self._edges: list[Edge] = []

    def add_node(
        self, node_type: str, ref_id: str, *, node_id: str | None = None, **props: Any
    ) -> Node:
        if node_type not in NODE_TYPES:
            raise ValueError(f"unknown node type: {node_type}")
        nid = node_id or new_id("node")
        node = Node(node_id=nid, node_type=node_type, ref_id=ref_id, properties=props)
        self._nodes[nid] = node
        return node

    def add_edge(
        self,
        src: str,
        dst: str,
        relation: str,
        *,
        basis: str,
        workspace_id: str,
        causal_evidence: str | None = None,
    ) -> Edge:
        if relation not in EDGE_TYPES:
            raise ValueError(f"unknown edge type: {relation}")
        if relation == "direct_causal" and not causal_evidence:
            raise ValueError("direct_causal requires explicit trusted causal evidence")
        edge = Edge(
            edge_id=new_id("edge"),
            src=src,
            dst=dst,
            relation=relation,
            basis=basis,
            workspace_id=workspace_id,
            causal_evidence=causal_evidence,
        )
        self._edges.append(edge)
        return edge

    def correlate_from_context_refs(
        self,
        event_node_id: str,
        context_refs: dict[str, str],
        *,
        workspace_id: str,
        validated: set[str] | None = None,
    ) -> list[Edge]:
        """基于已校验的 context_refs 建立关联边；未校验的保留 unknown，不生成确定性边。"""
        validated = validated or set()
        created: list[Edge] = []
        mapping = {
            "trace_id": "correlated_by_trace_id",
            "request_id": "correlated_by_request_id",
            "run_id": "same_execution_context",
            "task_id": "same_execution_context",
        }
        for field_name, relation in mapping.items():
            value = context_refs.get(field_name)
            if not value:
                continue
            if field_name not in validated:
                # 未校验：不生成确定性边
                continue
            # 确保目标节点存在
            dst_ref = self._ensure_ref_node(value)
            created.append(
                self.add_edge(
                    event_node_id,
                    dst_ref.node_id,
                    relation,
                    basis=f"context_refs.{field_name} (validated)",
                    workspace_id=workspace_id,
                )
            )
        return created

    def _ensure_ref_node(self, ref_id: str) -> Node:
        for n in self._nodes.values():
            if n.ref_id == ref_id:
                return n
        # 未知关联对象建占位节点，标记为 unknown（不解释为已验证）
        return self.add_node("Source", ref_id, resolved="unknown")

    def add_support(self, hypothesis_node: str, evidence_node: str, workspace_id: str) -> Edge:
        return self.add_edge(
            hypothesis_node,
            evidence_node,
            "supports",
            basis="diagnosis:supporting_evidence",
            workspace_id=workspace_id,
        )

    def add_contradict(self, hypothesis_node: str, evidence_node: str, workspace_id: str) -> Edge:
        return self.add_edge(
            hypothesis_node,
            evidence_node,
            "contradicts",
            basis="diagnosis:contradicting_evidence",
            workspace_id=workspace_id,
        )

    def independent_supports(self, hypothesis_node: str) -> list[str]:
        """来源独立性分析：过滤掉同源派生证据，只保留独立佐证。

        规则：若某 supports 边的目标证据是 derived_from 另一条证据，
        且两者同源，则不算独立佐证。
        """
        support_edges = [
            e for e in self._edges if e.src == hypothesis_node and e.relation == "supports"
        ]
        # derived_from 边：src 是派生证据，dst 是其来源。派生证据（src）不算独立佐证。
        derived_evidence = {e.src for e in self._edges if e.relation == "derived_from"}
        return [e.dst for e in support_edges if e.dst not in derived_evidence]

    def conflicts(self, hypothesis_node: str) -> list[dict[str, str]]:
        """冲突分析：同时有 supports 和 contradicts 的证据。"""
        supports = {
            e.dst for e in self._edges if e.src == hypothesis_node and e.relation == "supports"
        }
        contradicts = {
            e.dst for e in self._edges if e.src == hypothesis_node and e.relation == "contradicts"
        }
        overlap = supports & contradicts
        return [{"evidence_id": eid, "status": "CONFLICT_UNRESOLVED"} for eid in overlap]

    def query(
        self,
        *,
        src: str | None = None,
        relation: str | None = None,
        workspace_id: str | None = None,
    ) -> list[Edge]:
        out = self._edges
        if src:
            out = [e for e in out if e.src == src or e.dst == src]
        if relation:
            out = [e for e in out if e.relation == relation]
        if workspace_id:
            out = [e for e in out if e.workspace_id == workspace_id]
        return out

    def nodes(self) -> list[Node]:
        return list(self._nodes.values())

    def to_dict(self) -> dict[str, Any]:
        return {
            "nodes": [
                {
                    "node_id": n.node_id,
                    "node_type": n.node_type,
                    "ref_id": n.ref_id,
                    "properties": n.properties,
                }
                for n in self._nodes.values()
            ],
            "edges": [e.to_dict() for e in self._edges],
        }
