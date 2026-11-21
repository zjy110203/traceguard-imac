"""阶段8 Evidence Graph 测试：关联、因果限制、来源独立性、冲突分析。"""

from __future__ import annotations

import pytest
from traceguard_evidence.graph.graph import EvidenceGraph


def test_node_type_constraint() -> None:
    g = EvidenceGraph()
    with pytest.raises(ValueError, match="unknown node type"):
        g.add_node("Bogus", "x")


def test_edge_type_constraint() -> None:
    g = EvidenceGraph()
    a = g.add_node("Run", "run_1")
    b = g.add_node("Evidence", "evd_1")
    with pytest.raises(ValueError, match="unknown edge type"):
        g.add_edge(a.node_id, b.node_id, "magic_link", basis="x", workspace_id="ws")


def test_direct_causal_requires_explicit_evidence() -> None:
    g = EvidenceGraph()
    a = g.add_node("Event", "evt_1")
    b = g.add_node("Event", "evt_2")
    with pytest.raises(ValueError, match="direct_causal requires"):
        g.add_edge(a.node_id, b.node_id, "direct_causal", basis="temporal", workspace_id="ws")
    # 有明确可信依据才允许
    e = g.add_edge(a.node_id, b.node_id, "direct_causal", basis="transaction-rollback-log",
                   workspace_id="ws", causal_evidence="server transaction log shows rollback")
    assert e.causal_evidence is not None


def test_correlate_only_validated_refs() -> None:
    g = EvidenceGraph()
    ev = g.add_node("Event", "evt_1")
    created = g.correlate_from_context_refs(
        ev.node_id, {"trace_id": "trace_1", "request_id": "req_1", "run_id": "run_1"},
        workspace_id="ws", validated={"trace_id"},  # 只有 trace_id 通过校验
    )
    assert len(created) == 1
    assert created[0].relation == "correlated_by_trace_id"
    assert created[0].basis.endswith("(validated)")


def test_unknown_ref_not_treated_as_verified() -> None:
    g = EvidenceGraph()
    ev = g.add_node("Event", "evt_1")
    g.correlate_from_context_refs(ev.node_id, {"request_id": "req_unknown"},
                                  workspace_id="ws", validated={"request_id"})
    # req_unknown 建了占位节点但标记 unknown
    unknown_nodes = [n for n in g.nodes() if n.ref_id == "req_unknown"]
    assert len(unknown_nodes) == 1
    assert unknown_nodes[0].properties.get("resolved") == "unknown"


def test_temporal_proximity_not_causal() -> None:
    """时间接近只建 temporal 边，不建 direct_causal。"""
    g = EvidenceGraph()
    a = g.add_node("Event", "evt_1")
    b = g.add_node("Event", "evt_2")
    g.add_edge(a.node_id, b.node_id, "temporal", basis="occurred_at within 100ms", workspace_id="ws")
    causal = g.query(relation="direct_causal")
    assert causal == []


def test_supports_and_contradicts_both_kept() -> None:
    g = EvidenceGraph()
    h = g.add_node("Hypothesis", "hyp_1")
    e1 = g.add_node("Evidence", "evd_1")
    e2 = g.add_node("Evidence", "evd_2")
    g.add_support(h.node_id, e1.node_id, "ws")
    g.add_contradict(h.node_id, e2.node_id, "ws")
    assert len(g.query(relation="supports")) == 1
    assert len(g.query(relation="contradicts")) == 1


def test_derived_evidence_not_independent_support() -> None:
    """同源派生证据不算多个独立佐证。"""
    g = EvidenceGraph()
    h = g.add_node("Hypothesis", "hyp_1")
    raw = g.add_node("Evidence", "evd_raw")
    derived = g.add_node("Evidence", "evd_derived")
    g.add_edge(derived.node_id, raw.node_id, "derived_from",
               basis="rule-v1", workspace_id="ws")
    g.add_support(h.node_id, raw.node_id, "ws")
    g.add_support(h.node_id, derived.node_id, "ws")
    independent = g.independent_supports(h.node_id)
    assert derived.node_id not in independent  # 派生不算独立
    assert raw.node_id in independent


def test_conflict_analysis() -> None:
    g = EvidenceGraph()
    h = g.add_node("Hypothesis", "hyp_1")
    e = g.add_node("Evidence", "evd_1")
    g.add_support(h.node_id, e.node_id, "ws")
    g.add_contradict(h.node_id, e.node_id, "ws")
    conflicts = g.conflicts(h.node_id)
    assert len(conflicts) == 1 and conflicts[0]["status"] == "CONFLICT_UNRESOLVED"


def test_every_edge_has_basis() -> None:
    g = EvidenceGraph()
    a = g.add_node("Run", "run_1")
    b = g.add_node("Step", "step_1")
    e = g.add_edge(a.node_id, b.node_id, "parent_child",
                   basis="run.steps[0]", workspace_id="ws")
    assert e.basis
    d = g.to_dict()
    assert all(edge["basis"] for edge in d["edges"])
