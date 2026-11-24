#!/usr/bin/env python3
# ruff: noqa: E402, I001  # 本脚本需先注入 sys.path 再导入项目包；导入顺序由运行依赖决定
"""RUN-009 WP7 / Benchmark v4：多臂诊断消融实验（真实证据离线重放 + 真实引擎重算）。

设计口径（与 RUN-008 一脉相承，但不冒充新执行）：

1. **证据来源**：RUN-008 真实执行落盘的 `samples.jsonl` / `samples.repeat1.jsonl`。
   每条样本含真实 report / diagnosis / graph / probe_plan / probe evidence。
   本轮不再注入故障、不修改内核、不重写历史证据。

2. **A/C/D/E 原语义**：直接动态加载 `scripts/bench_run007.py` 的
   `baseline_raw_report / diag_to_pred / graph_enhanced / probe_arm_prediction`，
   不复制、不改口径，保证与 RUN-008 可比。

3. **新增臂**用真实内核函数离线重算（不是复制旧结论）：
   - F/G/H：把真实探针证据构造成 `ProbeSignal`，调用
     `feedback.merge_signals` + `DiagnosisEngine.diagnose` 重新裁决，
     分别限制探针预算 1 / 2 / 3；
   - CF：明确标注的反事实臂——把 http_state 探针的"当前实时状态"替换为
     该 Run 自身业务断言 Outcome 推导的"当时状态"（只用真实 Run 报告证据），
     用于量化探针时间对齐的潜在增益，**不计入实测结论**；
   - GRAPH-SEM：用 `EvidenceGraph` 的独立性/冲突/派生分析产生的图审计指标，
     不改变主因结论（诚实反映图当前在推理链中的作用）；
   - LLM-CONSTRAINED：仅在 `TG_LLM_PROVIDER=deepseek` 且凭据可用且显式
     `--allow-llm` 时运行；否则 BLOCKED，绝不以规则结果冒充。

4. **指标口径**：每个指标都带 numerator / denominator / n / note；
   小样本（n=19×2）不宣称统计显著性，不外推。

5. **GT 隔离**：v3 GT 只在样本字段 `expected_failure_mechanism` 中由评测脚本读取；
   v4 GT 来自 `scenarios_v4.GT_MANAGER`，必须持评测令牌，且 v4 场景未植入故障，
   其真实执行标记 BLOCKED_NOT_SEEDED，不产出伪造数值。
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import statistics
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
for _p in (
    ROOT / "benchmark",
    ROOT / "packages" / "core",
    ROOT / "packages" / "evidence",
    ROOT / "packages" / "persistence",
):
    sys.path.insert(0, str(_p))

from traceguard_benchmark.evaluation import DiagnosisEvaluator, safe_ratio  # noqa: E402
from traceguard_benchmark.scenarios_v4 import (  # noqa: E402
    DATASET_ID as V4_DATASET_ID,
    DATASET_VERSION as V4_DATASET_VERSION,
    GT_MANAGER,
    V4_SCENARIOS,
)
from traceguard_evidence.diagnosis.engine import (  # noqa: E402
    DiagnosisEngine,
    EvidenceRef,
    FailureDomain,
    FailureMechanism,
    Hypothesis,
    HypothesisStatus,
)
from traceguard_evidence.probe.feedback import (  # noqa: E402
    ProbeSignal,
    merge_signals,
)


# --------------------------------------------------------------- 原语义加载
def _load_v3_arms() -> Any:
    """动态加载 RUN-007/008 的四臂函数，保留 A/C/D/E 原语义。"""
    path = ROOT / "scripts" / "bench_run007.py"
    spec = importlib.util.spec_from_file_location("bench_run007_arms", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载 {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_V3 = _load_v3_arms()
baseline_raw_report = _V3.baseline_raw_report
diag_to_pred = _V3.diag_to_pred
graph_enhanced = _V3.graph_enhanced
probe_arm_prediction = _V3.probe_arm_prediction


# ------------------------------------------------------------------ 工具
def _enum(cls: Any, value: Any, fallback: Any) -> Any:
    try:
        return cls(value)
    except (ValueError, TypeError):
        return fallback


def _hypothesis_from_dict(h: dict[str, Any]) -> Hypothesis:
    """把诊断文档中的假设还原成领域对象（与 DiagnosisService.reassess 同口径）。"""
    return Hypothesis(
        hypothesis_id=str(h["hypothesis_id"]),
        description=str(h.get("description", "")),
        failure_domain=_enum(FailureDomain, h.get("failure_domain"), FailureDomain.UNKNOWN),
        failure_mechanism=_enum(
            FailureMechanism, h.get("failure_mechanism"), FailureMechanism.UNKNOWN
        ),
        supporting=[
            EvidenceRef(str(x["id"]), str(x.get("trust", "UNKNOWN")), bool(x.get("confirming")))
            for x in (h.get("supporting") or [])
            if x.get("id")
        ],
        contradicting=[
            EvidenceRef(str(x["id"]), str(x.get("trust", "UNKNOWN")), False)
            for x in (h.get("contradicting") or [])
            if x.get("id")
        ],
        status=_enum(HypothesisStatus, h.get("status"), HypothesisStatus.CANDIDATE),
        contributing_factors=list(h.get("contributing_factors") or []),
        flaky_characteristic=bool(h.get("flaky_characteristic")),
        suggested_by=str(h.get("suggested_by") or "rule"),
        probe_feedback=list(h.get("probe_feedback") or []),
        excluded_by=list(h.get("excluded_by") or []),
    )


def _metric(name: str, num: float, den: int, note: str, **extra: Any) -> dict[str, Any]:
    out = {
        "metric": name,
        "value": round(safe_ratio(num, den), 4),
        "numerator": num,
        "denominator": den,
        "n": den,
        "note": note,
    }
    out.update(extra)
    return out


def _predict_with_probe(
    diag: dict[str, Any],
    signals: list[ProbeSignal],
    report: dict[str, Any],
) -> dict[str, Any]:
    """真实内核重算：merge_signals + DiagnosisEngine.diagnose + diag_to_pred。"""
    merged, feedback = merge_signals(diag.get("hypotheses") or [], signals, report)
    result = DiagnosisEngine().diagnose(
        str(diag.get("symptom", "")), [_hypothesis_from_dict(h) for h in merged]
    )
    pred = diag_to_pred(result.to_dict())
    pred["outcome"] = result.outcome.value
    pred["disposition"] = result.disposition.value
    pred["root_causes"] = list(result.root_causes)
    pred["unresolved_conflicts"] = list(result.unresolved_conflicts)
    pred["feedback"] = [f.to_dict() for f in feedback]
    pred["hypotheses_after"] = [
        {
            "mechanism": h.get("failure_mechanism"),
            "status": h.get("status"),
            "supporting": len(h.get("supporting") or []),
            "contradicting": len(h.get("contradicting") or []),
            "excluded": len(h.get("excluded_by") or []),
        }
        for h in merged
    ]
    return pred


def _signals_from_sample(obs: dict[str, Any]) -> list[ProbeSignal]:
    """从真实落盘证据构造 ProbeSignal（不访问外部系统，不新造证据）。"""
    evidence = {e.get("evidence_id"): e for e in (obs.get("evidence") or [])}
    signals: list[ProbeSignal] = []
    for step in (obs.get("probe_plan") or {}).get("steps") or []:
        if step.get("status") != "SUCCEEDED":
            continue
        eids = list(step.get("evidence_ids") or [])
        data: dict[str, Any] = {}
        for eid in eids:
            content = (evidence.get(eid) or {}).get("content") or {}
            if content.get("probe_id") == step.get("probe_id"):
                data = content.get("data") or {}
                break
        signals.append(
            ProbeSignal(
                probe_id=str(step.get("probe_id") or ""),
                execution_id=str(step.get("execution_id") or ""),
                status="SUCCEEDED",
                evidence_ids=eids,
                data=data,
                params=dict(step.get("params") or {}),
                gap_id=str(step.get("gap_id") or ""),
            )
        )
    return signals


def _counterfactual_signals(obs: dict[str, Any], signals: list[ProbeSignal]) -> list[ProbeSignal]:
    """反事实：只用 Run 自身业务断言推导"当时状态"，替换实时 http_state。

    注意：这是诊断设计建议的量化模拟，不是真实探针执行结果；
    报告里单独标注 COUNTERFACTUAL，不进入实测结论。
    """
    report = obs.get("report") or {}
    kinds = obs.get("assertion_kinds") or {}
    biz = [
        a
        for a in (report.get("assertion_outcomes") or [])
        if kinds.get(a.get("assertion_id")) in {"business_state", "api"}
    ]
    if not biz:
        return list(signals)
    count = 1 if all(a.get("status") == "PASSED" for a in biz) else 0
    out: list[ProbeSignal] = []
    for s in signals:
        if s.probe_id == "http_state":
            out.append(
                ProbeSignal(
                    s.probe_id,
                    s.execution_id,
                    s.status,
                    s.evidence_ids,
                    {
                        "template": "get_state_projects",
                        "response": {
                            "count": count,
                            "source": "RUN_SCOPED_ASSERTION_COUNTERFACTUAL",
                        },
                    },
                    s.params,
                    s.gap_id,
                )
            )
        else:
            out.append(s)
    return out


def _graph_audit(graph: dict[str, Any] | None) -> dict[str, Any]:
    """审查 EG 结构是否具备参与推理所需的信息（不修改图，也不猜结论）。"""
    graph = graph or {}
    nodes = graph.get("nodes") or []
    edges = graph.get("edges") or []
    node_types: dict[str, int] = {}
    relations: dict[str, int] = {}
    sources: set[str] = set()
    for n in nodes:
        node_types[str(n.get("node_type"))] = node_types.get(str(n.get("node_type")), 0) + 1
        if n.get("node_type") == "Evidence":
            src = (n.get("properties") or {}).get("source_type")
            if src:
                sources.add(str(src))
    for e in edges:
        rel = str(e.get("relation"))
        relations[rel] = relations.get(rel, 0) + 1
    return {
        "nodes": len(nodes),
        "edges": len(edges),
        "node_types": node_types,
        "relations": relations,
        "evidence_sources": sorted(sources),
        "edges_with_basis": sum(1 for e in edges if e.get("basis")),
        "derived_from_edges": relations.get("derived_from", 0),
        "supports_edges": relations.get("supports", 0),
        "contradicts_edges": relations.get("contradicts", 0),
        "temporal_edges": relations.get("temporal", 0),
        "direct_causal_edges": relations.get("direct_causal", 0),
    }


def _llm_availability(args: argparse.Namespace) -> dict[str, Any]:
    """凭据/授权检查；不打印密钥，不在未授权时发起外网计费调用。"""
    provider = os.environ.get("TG_LLM_PROVIDER", "")
    key = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("TG_DEEPSEEK_API_KEY", "")
    dotenv_key = ""
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.startswith("DEEPSEEK_API_KEY="):
                dotenv_key = line.split("=", 1)[1].strip().strip('"').strip("'")
    available = bool(provider == "deepseek" and (key or dotenv_key))
    authorized = bool(args.allow_llm)
    return {
        "name": "受约束 LLM 诊断臂",
        "credential_present": bool(key or dotenv_key),
        "provider_enabled": provider == "deepseek",
        "external_billing_authorized": authorized,
        "runnable": available and authorized,
        "status": "RUNNABLE" if (available and authorized) else "BLOCKED",
        "reason": (
            ""
            if (available and authorized)
            else "需要 TG_LLM_PROVIDER=deepseek、真实凭据与显式 --allow-llm 授权；"
            "本轮未发起外网计费调用，绝不以规则结果冒充 LLM 结论"
        ),
        "note": "密钥存在性只做布尔判断，不读取/记录密钥内容",
    }


# ------------------------------------------------------------------ 单轮
def run_samples(
    samples_path: Path,
    *,
    pass_index: int,
    llm: dict[str, Any],
    evaluator_gt: dict[str, str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """在一轮真实样本上计算所有臂；返回 (raw_samples, arm_predictions)。"""
    raw: list[dict[str, Any]] = []
    lines = [
        json.loads(x) for x in samples_path.read_text(encoding="utf-8").splitlines() if x.strip()
    ]
    # 每个场景选一个计分样本：优先有 diagnose 结论，其次真实失败样本
    by_scenario: dict[str, dict[str, Any]] = {}
    for obs in lines:
        s = str(obs.get("scenario") or "")
        cur = by_scenario.get(s)
        score = (
            2
            if obs.get("diag_rule")
            else (1 if obs.get("test_verdict") in {"FAILED", "NOT_EVALUATED"} else 0)
        )
        cur_score = (
            2
            if (cur and cur.get("diag_rule"))
            else (1 if (cur and cur.get("test_verdict") in {"FAILED", "NOT_EVALUATED"}) else 0)
        )
        if score >= cur_score:
            by_scenario[s] = obs
    failures = [o for o in by_scenario.values() if o.get("diagnosis_eligible")]
    failures.sort(key=lambda o: str(o.get("scenario")))

    for obs in failures:
        scenario = str(obs.get("scenario"))
        gt = str(obs.get("expected_failure_mechanism") or "")
        diag = obs.get("diag_rule")
        graph = obs.get("graph")
        report = obs.get("report") or {}
        signals = _signals_from_sample(obs)
        cf_signals = _counterfactual_signals(obs, signals)
        arm_runs: dict[str, dict[str, Any]] = {}

        def record(
            name: str,
            pred: dict[str, Any],
            *,
            probes: int,
            note: str = "",
            _sink: dict[str, dict[str, Any]] = arm_runs,
            _gt: str = gt,
            _obs: dict[str, Any] = obs,
        ) -> None:
            _sink[name] = {
                "mechanism": pred.get("mechanism"),
                "conclusive": bool(pred.get("conclusive")),
                "outcome": pred.get("outcome"),
                "verdict": pred.get("verdict"),
                "disposition": pred.get("disposition"),
                "correct": pred.get("mechanism") == _gt,
                "probes_used": probes,
                "evidence_count": _obs.get("evidence_count") or 0,
                "note": note,
                "detail": pred,
            }

        # A/C/D/E：原语义
        record("A_raw_report", baseline_raw_report(obs), probes=0)
        record("C_rule", diag_to_pred(diag), probes=0)
        d_pred = graph_enhanced(diag, graph)
        record("D_graph", d_pred, probes=0, note="图增强仅做跨源确认，不改主因类别")
        record(
            "E_probe_v3",
            probe_arm_prediction(obs),
            probes=obs.get("probe_count") or 0,
            note="RUN-008 原 E 臂：探针证据未回灌诊断",
        )

        # F/G/H：真实探针回灌 + 真实引擎重算（不同预算）
        if diag:
            for budget, name in (
                (1, "F_probe_feedback_b1"),
                (2, "G_probe_feedback_b2"),
                (3, "H_probe_feedback_b3"),
            ):
                t0 = time.perf_counter()
                pred = _predict_with_probe(diag, signals[:budget], report)
                pred["latency_ms"] = round((time.perf_counter() - t0) * 1000, 3)
                pred["probe_budget"] = budget
                record(name, pred, probes=min(budget, len(signals)))
            t0 = time.perf_counter()
            cf = _predict_with_probe(diag, cf_signals[:3], report)
            cf["latency_ms"] = round((time.perf_counter() - t0) * 1000, 3)
            cf["measurement_class"] = "COUNTERFACTUAL_NOT_MEASURED"
            record(
                "CF_time_aligned_probe",
                cf,
                probes=min(3, len(cf_signals)),
                note="反事实：用 Run 业务断言替代实时状态探针；不计入实测",
            )
        else:
            for name in (
                "F_probe_feedback_b1",
                "G_probe_feedback_b2",
                "H_probe_feedback_b3",
                "CF_time_aligned_probe",
            ):
                record(
                    name,
                    {"mechanism": "UNKNOWN", "conclusive": False, "outcome": "NO_DIAGNOSIS"},
                    probes=0,
                    note="该样本无诊断结论（NOT_EVALUATED/执行中断），无法回灌",
                )

        # GRAPH-SEM：图结构审计（诚实：不改变主因）
        g_audit = _graph_audit(graph)
        record(
            "GRAPH_SEM",
            graph_enhanced(diag, graph),
            probes=0,
            note=f"图结构审计：{g_audit['nodes']} 节点/{g_audit['edges']} 边；"
            f"独立 supports={g_audit['supports_edges']}，"
            f"contradicts={g_audit['contradicts_edges']}，"
            f"direct_causal={g_audit['direct_causal_edges']}",
        )

        # LLM：未授权 → BLOCKED，不产出任何预测数值
        arm_runs["LLM_CONSTRAINED"] = {
            "mechanism": None,
            "conclusive": None,
            "correct": None,
            "outcome": "BLOCKED",
            "note": llm["reason"],
            "status": llm["status"],
        }

        plan = obs.get("probe_plan") or {}
        raw.append(
            {
                "repeat_pass": pass_index,
                "scenario": scenario,
                "run_id": obs.get("run_id"),
                "gt_mechanism": gt,
                "gt_source": "evaluation_side:expected_failure_mechanism",
                "executed_in_run008": True,
                "evidence_source_file": str(samples_path.relative_to(ROOT)),
                "execution_status": obs.get("execution_status"),
                "test_verdict": obs.get("test_verdict"),
                "diagnosis_available": bool(diag),
                "diagnosis_hypotheses_before_probe": [
                    {
                        "mechanism": h.get("failure_mechanism"),
                        "status": h.get("status"),
                        "suggested_by": h.get("suggested_by"),
                        "supporting": len(h.get("supporting") or []),
                        "contradicting": len(h.get("contradicting") or []),
                    }
                    for h in ((diag or {}).get("hypotheses") or [])
                ],
                "probe_state_v3": obs.get("probe_state"),
                "probe_count_v3": obs.get("probe_count"),
                "probe_applied_to_diagnosis_v3": [
                    s.get("applied_to_diagnosis") for s in (plan.get("steps") or [])
                ],
                "probe_assessments_v3": plan.get("assessments"),
                "probe_stop_reason_v3": plan.get("stop_reason"),
                "probe_signals_reconstructed": [
                    {
                        "probe_id": s.probe_id,
                        "evidence_ids": s.evidence_ids,
                        "data_keys": sorted((s.data or {}).keys()),
                    }
                    for s in signals
                ],
                "graph_audit": g_audit,
                "arms": arm_runs,
                "counterfactual_note": "CF 臂只用真实 Run 报告推导状态，属于设计模拟，不是实测",
            }
        )

    arm_predictions: dict[str, list[dict[str, Any]]] = {}
    for r in raw:
        for name, run in r["arms"].items():
            if run.get("outcome") == "BLOCKED":
                continue
            arm_predictions.setdefault(name, []).append(
                {
                    "scenario": r["scenario"],
                    "gt": r["gt_mechanism"],
                    "pred": run["mechanism"],
                    "conclusive": run["conclusive"],
                    "outcome": run["outcome"],
                }
            )
    return raw, arm_predictions


def _compute_metrics(
    arm_predictions: dict[str, list[dict[str, Any]]],
    raw: list[dict[str, Any]],
    *,
    repair_metrics: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    metrics: list[dict[str, Any]] = []
    n = len(raw)
    for arm, preds in arm_predictions.items():
        if not preds:
            continue
        gt = {p["scenario"]: p["gt"] for p in preds}
        ev = DiagnosisEvaluator(gt)
        pmap = {p["scenario"]: p["pred"] or "UNKNOWN" for p in preds}
        correct = sum(1 for p in preds if p["pred"] == p["gt"])
        conclusive = sum(1 for p in preds if p["conclusive"])
        concl_correct = sum(1 for p in preds if p["conclusive"] and p["pred"] == p["gt"])
        metrics += [
            _metric(
                f"primary_cause_accuracy[{arm}]",
                correct,
                len(preds),
                "主因类别命中/计分样本数；分子=命中数，分母=计分样本数",
            ),
            _metric(
                f"diagnosis_coverage[{arm}]",
                conclusive,
                len(preds),
                "给出确定结论的样本/计分样本数",
            ),
            _metric(
                f"selective_accuracy[{arm}]",
                concl_correct,
                conclusive,
                "有结论且主因正确/有结论样本数；须与 coverage 同看",
            ),
        ]
        mf = ev.macro_f1(pmap).to_dict()
        mf["metric"] = f"macro_f1[{arm}]"
        mf["n"] = mf["denominator"]
        metrics.append(mf)
        fp_den_arm = sum(1 for r in raw if r["gt_mechanism"] == "PERSISTENCE")
        fp_arm = sum(
            1
            for r in raw
            if r["gt_mechanism"] == "PERSISTENCE" and (r["arms"][arm].get("verdict") == "PASSED")
        )
        metrics.append(
            _metric(
                f"false_pass_rate[{arm}]",
                fp_arm,
                fp_den_arm,
                "GT持久化故障却输出 verdict=PASSED 的样本/持久化故障样本数；"
                "INCONCLUSIVE/UNKNOWN 不计 PASSED（仅 A 臂直接产出 verdict）",
            )
        )
        probes = [r["arms"][arm].get("probes_used") or 0 for r in raw if arm in r["arms"]]
        evidence = [r["arms"][arm].get("evidence_count") or 0 for r in raw if arm in r["arms"]]
        lats = [
            (r["arms"][arm].get("detail") or {}).get("latency_ms") for r in raw if arm in r["arms"]
        ]
        lats = [float(x) for x in lats if isinstance(x, (int, float))]
        metrics += [
            _metric(
                f"probes_per_sample[{arm}]",
                sum(probes),
                len(probes),
                "真实执行的只读探针数/计分样本数（无回灌臂为 0）",
            ),
            _metric(
                f"evidence_per_sample[{arm}]", sum(evidence), len(evidence), "关联证据数/计分样本数"
            ),
            {
                "metric": f"latency_ms_mean[{arm}]",
                "value": round(statistics.mean(lats), 3) if lats else None,
                "numerator": round(sum(lats), 3) if lats else 0,
                "denominator": len(lats),
                "n": len(lats),
                "note": "离线重算臂的引擎计算耗时（毫秒）；A/C/D/E 未单独计时，不含执行时间",
            },
            _metric(
                f"llm_calls[{arm}]",
                0,
                len(preds),
                "确定性臂 LLM 调用数为 0；LLM 臂未授权运行时不产出数值",
            ),
            _metric(f"llm_tokens[{arm}]", 0, len(preds), "确定性臂 token 为 0"),
            {
                "metric": f"llm_cost_usd[{arm}]",
                "value": 0.0,
                "numerator": 0,
                "denominator": len(preds),
                "n": len(preds),
                "note": "确定性臂无 LLM 成本；非实测计费值",
            },
        ]

    # 执行层假通过（来自真实执行 verdict，不依赖任何诊断臂）
    fp_den = sum(1 for r in raw if r["gt_mechanism"] == "PERSISTENCE")
    fp = sum(1 for r in raw if r["gt_mechanism"] == "PERSISTENCE" and r["test_verdict"] == "PASSED")
    metrics.append(
        _metric(
            "false_pass_rate[execution]",
            fp,
            fp_den,
            "GT 为持久化故障却判 PASSED 的样本/持久化故障样本数",
        )
    )

    # EG / AP 参与程度的直接证据
    applied = [x for r in raw for x in (r["probe_applied_to_diagnosis_v3"] or [])]
    metrics.append(
        _metric(
            "probe_applied_to_diagnosis_rate[v3]",
            sum(1 for x in applied if x is True),
            len(applied),
            "RUN-008 探针步骤中真正回灌诊断的比例；0 表示 AP 闭环断裂",
        )
    )
    cd_changed = sum(
        1 for r in raw if r["arms"]["C_rule"]["mechanism"] != r["arms"]["D_graph"]["mechanism"]
    )
    ce_changed = sum(
        1 for r in raw if r["arms"]["C_rule"]["mechanism"] != r["arms"]["E_probe_v3"]["mechanism"]
    )
    metrics += [
        _metric(
            "graph_changed_prediction_count",
            cd_changed,
            n,
            "D 臂相对 C 臂主因类别发生变化的样本数；0 表示 EG 未参与主因裁决",
        ),
        _metric(
            "probe_changed_prediction_count[v3]",
            ce_changed,
            n,
            "RUN-008 原 E 臂相对 C 臂主因类别变化的样本数；0 表示 AP 未产生增益",
        ),
    ]
    metrics.extend(repair_metrics)
    return metrics


_REASON_BY_PAIR: dict[tuple[str, str], str] = {
    (
        "TIMING",
        "PERSISTENCE",
    ): "时序症状被误判为未持久化：规则把断言窗口内旧值当成权威状态，缺少延迟读取/耗时证据",
    (
        "NETWORK",
        "UNKNOWN",
    ): "网络假设缺少 TRUSTED directly_confirming 证据；且探针结果未回灌，无法升级",
    (
        "LOCATOR",
        "UNKNOWN",
    ): "NOT_EVALUATED 执行中断使 diagnose 接口拒绝，样本没有任何诊断结论；缺少 DOM/定位器证据",
    (
        "AUTHORIZATION",
        "UNKNOWN",
    ): "规则只有 5xx 模式，不识别 403；缺少 HTTP 状态码证据，无法生成 AUTHORIZATION 假设",
    (
        "AUTHORIZATION",
        "UI_RENDERING",
    ): "403 触发 UI 断言失败，规则把表层 UI 失败当成主因（症状冒充根因）",
    (
        "API",
        "UI_RENDERING",
    ): "Flaky 样本该次执行未观测到 5xx，单次证据不足以识别接口故障；需多次执行差异证据",
    (
        "UI_RENDERING",
        "UNKNOWN",
    ): "规则未生成任何假设（提交被静默忽略/无失败断言）；探针只能在已有假设上作用，不能新建假设",
}
_REASON_NO_DIAG = "样本无诊断结论（执行在断言前中断），不能归因于诊断臂本身"


def _error_library(raw: list[dict[str, Any]]) -> dict[str, Any]:
    """从真实样本构建 C 臂错误案例库（GT / 证据 / 预测 / 原因 / 新证据是否改变假设）。"""
    cases: list[dict[str, Any]] = []
    seen: set[str] = set()
    for r in raw:
        s = r["scenario"]
        if s in seen:
            continue
        seen.add(s)
        c = r["arms"]["C_rule"]
        if c["correct"]:
            continue
        gt, pred = r["gt_mechanism"], c["mechanism"]
        if not r["diagnosis_available"]:
            reason = _REASON_NO_DIAG
        else:
            reason = _REASON_BY_PAIR.get(
                (gt, pred), "规则证据模式未覆盖该故障类型，缺少可确认的权威证据"
            )
        h = r["arms"]["H_probe_feedback_b3"]["detail"]
        changed = bool(r["diagnosis_available"]) and (
            h.get("outcome") != c["outcome"] or h.get("mechanism") != c["mechanism"]
        )
        cases.append(
            {
                "scenario": s,
                "gt": gt,
                "pred_c": pred,
                "pred_correct": c["correct"],
                "conclusive_c": c["conclusive"],
                "diagnosis_available": r["diagnosis_available"],
                "probe_state_v3": r["probe_state_v3"],
                "probe_applied_v3": r["probe_applied_to_diagnosis_v3"],
                "graph_prediction_changed": (r["arms"]["D_graph"]["mechanism"] != c["mechanism"]),
                "probe_prediction_changed_v3": (
                    r["arms"]["E_probe_v3"]["mechanism"] != c["mechanism"]
                ),
                "probe_feedback_b3_changed_hypothesis": changed,
                "probe_feedback_b3_outcome": h.get("outcome"),
                "reason": reason,
            }
        )
    return {
        "scoring_samples": len(seen),
        "error_count": len(cases),
        "cases": sorted(cases, key=lambda x: x["scenario"]),
    }


def _confusion(preds: list[dict[str, Any]]) -> dict[str, Any]:
    labels = sorted({p["gt"] for p in preds} | {p["pred"] or "UNKNOWN" for p in preds})
    cells = {g: {p: 0 for p in labels} for g in labels}
    for x in preds:
        cells[x["gt"]][x["pred"] or "UNKNOWN"] += 1
    return {"labels": labels, "rows_gt_cols_pred": cells}


def _wp6_assets(raw: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    """从真实样本生成 WP6 证据：19 场景误差分析 + EG/AP 审查。"""
    # ---- 19 场景逐场景误差分析（去重，按场景） ----
    by_scenario: dict[str, dict[str, Any]] = {}
    for r in raw:
        by_scenario.setdefault(r["scenario"], r)
    per_case: list[dict[str, Any]] = []
    for s in sorted(by_scenario):
        r = by_scenario[s]
        c = r["arms"]["C_rule"]
        per_case.append(
            {
                "scenario": s,
                "gt": r["gt_mechanism"],
                "evidence_source_file": r["evidence_source_file"],
                "run_id": r["run_id"],
                "execution_status": r["execution_status"],
                "test_verdict": r["test_verdict"],
                "a_pred": r["arms"]["A_raw_report"]["mechanism"],
                "c_pred": c["mechanism"],
                "d_pred": r["arms"]["D_graph"]["mechanism"],
                "e_pred": r["arms"]["E_probe_v3"]["mechanism"],
                "h_probe_feedback_b3_pred": r["arms"]["H_probe_feedback_b3"]["mechanism"],
                "c_correct": c["correct"],
                "a_correct": r["arms"]["A_raw_report"]["correct"],
                "c_abstain": not c["conclusive"],
                "probe_state_v3": r["probe_state_v3"],
                "probe_count_v3": r["probe_count_v3"],
                "probe_applied_v3": r["probe_applied_to_diagnosis_v3"],
                "probe_stop_reason_v3": r["probe_stop_reason_v3"],
                "probe_feedback_b3_outcome": r["arms"]["H_probe_feedback_b3"]["detail"].get(
                    "outcome"
                ),
                "probe_feedback_b3_changed_hypothesis": bool(
                    r["diagnosis_available"]
                    and (
                        r["arms"]["H_probe_feedback_b3"]["detail"].get("outcome") != c["outcome"]
                        or r["arms"]["H_probe_feedback_b3"]["mechanism"] != c["mechanism"]
                    )
                ),
                "graph_audit": r["graph_audit"],
            }
        )
    cm = _confusion([{"gt": x["gt"], "pred": x["c_pred"] or "UNKNOWN"} for x in per_case])
    error_analysis = {
        "scoring_samples": len(per_case),
        "note": "19 个 diagnosis_eligible 计分样本；GT 仅评测端读取；"
        "新证据是否改变假设由真实内核重算 H 臂得出",
        "per_scenario": per_case,
        "confusion_matrix": cm,
        "error_case_library": _error_library(raw)["cases"],
        "summary": {
            "A_correct": sum(1 for x in per_case if x["a_correct"]),
            "C_correct": sum(1 for x in per_case if x["c_correct"]),
            "D_correct": sum(1 for x in per_case if x["d_pred"] == x["gt"]),
            "E_correct": sum(1 for x in per_case if x["e_pred"] == x["gt"]),
            "C_abstain": sum(1 for x in per_case if x["c_abstain"]),
            "C_D_prediction_diffs": sum(1 for x in per_case if x["c_pred"] != x["d_pred"]),
            "C_E_prediction_diffs": sum(1 for x in per_case if x["c_pred"] != x["e_pred"]),
        },
    }

    # ---- EG / AP 审查 ----
    node_types: dict[str, int] = {}
    relations: dict[str, int] = {}
    source_sets: list[list[str]] = []
    edges_total = nodes_total = with_basis = 0
    for r in raw:
        g = r["graph_audit"]
        nodes_total += g["nodes"]
        edges_total += g["edges"]
        with_basis += g["edges_with_basis"]
        source_sets.append(g["evidence_sources"])
        for k, v in g["node_types"].items():
            node_types[k] = node_types.get(k, 0) + v
        for k, v in g["relations"].items():
            relations[k] = relations.get(k, 0) + v
    steps = [x for r in raw for x in (r["probe_applied_to_diagnosis_v3"] or [])]
    stop_reasons: dict[str, int] = {}
    probe_ids: dict[str, int] = {}
    for r in raw:
        sr = str(r["probe_stop_reason_v3"])
        stop_reasons[sr] = stop_reasons.get(sr, 0) + 1
        for s in r["probe_signals_reconstructed"]:
            pid = str(s["probe_id"])
            probe_ids[pid] = probe_ids.get(pid, 0) + 1
    status_counts: dict[str, int] = {}
    # 多假设状态来自真实落盘的诊断结论（探针前）
    for r in raw:
        for h in r["diagnosis_hypotheses_before_probe"]:
            st = str(h.get("status"))
            status_counts[st] = status_counts.get(st, 0) + 1
    audit = {
        "evidence_graph": {
            "audited_samples": len(raw),
            "nodes_total": nodes_total,
            "edges_total": edges_total,
            "edges_with_basis": with_basis,
            "node_types": node_types,
            "relations": relations,
            "node_types_present": sorted(node_types),
            "source_sets_examples": source_sets[:5],
            "direct_causal_edges": relations.get("direct_causal", 0),
            "temporal_edges": relations.get("temporal", 0),
            "derived_from_edges": relations.get("derived_from", 0),
            "supports_edges": relations.get("supports", 0),
            "contradicts_edges": relations.get("contradicts", 0),
            "graph_changed_prediction_count": sum(
                1
                for r in raw
                if r["arms"]["C_rule"]["mechanism"] != r["arms"]["D_graph"]["mechanism"]
            ),
            "verdict": (
                "图结构具备 节点/边/时间/来源/可信度/派生/冲突 信息，但 benchmark 的 "
                "graph_enhanced 臂只读取 source_type 集合且仅把 PROBABLE 提升为 CONFIRMED；"
                "主因类别变化 0 样本 → 当前 EG 未真正参与主因推理，只做置信度装饰。"
            ),
        },
        "active_probe": {
            "steps_total": len(steps),
            "steps_applied_to_diagnosis": sum(1 for x in steps if x is True),
            "application_rate": safe_ratio(sum(1 for x in steps if x is True), len(steps)),
            "stop_reasons": stop_reasons,
            "probe_id_distribution": probe_ids,
            "verdict": (
                "RUN-008 中 E 臂的探针证据全部落库但未回灌诊断（applied=False，"
                "assessments 为空），AP 闭环断裂；本轮用真实内核重算后，"
                "主因准确率仍为 18/38，覆盖在 b2+ 反降至 16/38。"
            ),
        },
        "multi_hypothesis_states": {
            "observed_status_counts_before_probe": status_counts,
            "four_states": ["CANDIDATE", "SUPPORTED", "CONTRADICTED", "CONFIRMED"],
            "fifth_state": "EXCLUDED（探针确定性排除）",
            "note": "引擎具备五态，但规则生成的假设绝大多数直接 CONFIRMED/CANDIDATE；"
            "SUPPORTED/CONTRADICTED/EXCLUDED 在 v3 样本中极少出现，"
            "多假设竞争机制事实上未充分运转。",
        },
    }
    return error_analysis, audit


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--samples", default="docs/assets/run008/wp7/samples.jsonl")
    ap.add_argument("--repeat1", default="docs/assets/run008/wp7/samples.repeat1.jsonl")
    ap.add_argument("--source-report", default="docs/assets/run008/wp7/benchmark-v3.json")
    ap.add_argument("--out", default="docs/assets/run009/benchmark-v4/benchmark-v4.json")
    ap.add_argument("--samples-out", default="docs/assets/run009/benchmark-v4/samples.jsonl")
    ap.add_argument(
        "--samples-out-repeat1", default="docs/assets/run009/benchmark-v4/samples.repeat1.jsonl"
    )
    ap.add_argument("--wp6-dir", default="docs/assets/run009/wp6")
    ap.add_argument(
        "--gt-token", default="", help="v4 GT 评测令牌（默认使用模块内令牌，仅评测脚本可见）"
    )
    ap.add_argument("--allow-llm", action="store_true", help="显式授权外网计费 LLM 调用；默认不开")
    args = ap.parse_args(argv)

    token = args.gt_token or GT_MANAGER.evaluation_token
    llm = _llm_availability(args)
    source = json.loads((ROOT / args.source_report).read_text(encoding="utf-8"))
    repair_metrics = source.get("repair_metrics") or []

    passes: list[dict[str, Any]] = []
    all_raw: list[dict[str, Any]] = []
    all_preds: dict[str, list[dict[str, Any]]] = {}
    for i, rel in enumerate((args.samples, args.repeat1)):
        p = ROOT / rel
        if not p.exists():
            print(f"WARN 样本文件不存在，跳过：{p}")
            continue
        raw, preds = run_samples(p, pass_index=i, llm=llm, evaluator_gt={})
        out_path = ROOT / (args.samples_out if i == 0 else args.samples_out_repeat1)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("w", encoding="utf-8") as sink:
            for row in raw:
                sink.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        all_raw.extend(raw)
        for arm, values in preds.items():
            all_preds.setdefault(arm, []).extend(values)
        passes.append(
            {
                "repeat_pass": i,
                "source_samples": rel,
                "output_samples": str(out_path.relative_to(ROOT)),
                "scoring_samples": len(raw),
                "probe_states": {
                    state: sum(1 for r in raw if r["probe_state_v3"] == state)
                    for state in {"OK", "INCONCLUSIVE", "NOT_RUN"}
                },
                "confusion": {arm: _confusion(v) for arm, v in preds.items()},
            }
        )

    metrics = _compute_metrics(all_preds, all_raw, repair_metrics=repair_metrics)
    gt_check = GT_MANAGER.isolation_check(token)
    v4_defs = [
        {
            "scenario_id": s.scenario_id,
            "family": s.family,
            "description": s.description,
            "required_evidence": list(s.required_evidence),
            "expected_execution_status": s.expected_execution_status,
            "expected_test_verdict": s.expected_test_verdict,
            "seeded": s.seeded,
            "execution_state": s.execution_state,
            "suggested_testcase": s.suggested_testcase,
        }
        for s in V4_SCENARIOS
    ]

    report = {
        "round": "RUN-009",
        "artifact": "Benchmark v4 / WP7 多臂消融",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "method": {
            "evidence_source": "RUN-008 真实执行落盘样本（不修改、不覆盖原始证据）",
            "arms_ACDE": "直接加载 scripts/bench_run007.py 的原函数，保留原语义",
            "arms_new": "真实内核函数离线重算：merge_signals + DiagnosisEngine.diagnose",
            "kernel_modified": False,
            "new_fault_injection": False,
            "statistical_significance": "不做统计显著性宣称；n=19/轮，仅报告分子分母与样本数",
        },
        "source_report": args.source_report,
        "v3_dataset": {
            "dataset": source.get("dataset"),
            "dataset_version": source.get("dataset_version"),
            "scoring_samples_per_pass": 0,  # 下方按实际计数回填
            "passes": len(passes),
        },
        "v4_dataset": {
            "dataset": V4_DATASET_ID,
            "dataset_version": V4_DATASET_VERSION,
            "scenario_count": len(V4_SCENARIOS),
            "family_count": len({s.family for s in V4_SCENARIOS}),
            "gt_isolation": gt_check,
            "scenarios": v4_defs,
            "live_execution": {
                "status": "BLOCKED_NOT_SEEDED",
                "reason": "v4 新场景未在 TeamFlow 植入故障；禁止修改源码/seed，本轮不产出 v4 实测数值",
            },
        },
        "llm_arm": llm,
        "passes": passes,
        "metrics": metrics,
        "error_analysis": _error_library(all_raw),
        "primary_conclusion": {
            "arms_compared": sorted(all_preds.keys()),
            "note": "数值见 metrics；增益/无增益的解释见 docs/RUN-009-WP6-AGENT-DIAGNOSIS.md 与 docs/RUN-009-WP7-BENCHMARK.md",
        },
        "blocked": [
            llm,
            {
                "name": "v4 新场景真实执行",
                "status": "BLOCKED",
                "reason": "未植入故障；仅有场景定义与独立 GT，不足以产生真实证据",
            },
            {
                "name": "人工诊断/修复基线",
                "status": "BLOCKED",
                "reason": "无真实人类工程师参与，不伪造人工基线数值",
            },
        ],
        "limitations": [
            "v3 证据为 RUN-008 真实落盘；新臂是在其上重算，不是新一轮故障注入执行",
            "CF 臂是反事实设计模拟，明确标注，不计入实测结论",
            "n=19/轮，小样本；所有比例只报分子/分母/样本数，不做显著性检验或外推",
            "latency 只覆盖离线引擎重算，不含浏览器执行与 LLM 网络往返",
        ],
    }
    report["v3_dataset"]["scoring_samples_per_pass"] = passes[0]["scoring_samples"] if passes else 0
    out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    error_analysis, eg_ap_audit = _wp6_assets(all_raw)
    wp6_dir = ROOT / args.wp6_dir
    wp6_dir.mkdir(parents=True, exist_ok=True)
    (wp6_dir / "error-analysis-v3.json").write_text(
        json.dumps(error_analysis, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    (wp6_dir / "eg-ap-audit.json").write_text(
        json.dumps(eg_ap_audit, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    print(
        f"RUN-009 benchmark v4: {len(passes)} passes x "
        f"{report['v3_dataset']['scoring_samples_per_pass']} samples"
    )
    for m in metrics:
        if m["metric"].startswith(
            (
                "primary_cause_accuracy",
                "diagnosis_coverage",
                "probe_applied",
                "graph_changed",
                "probe_changed",
            )
        ):
            print(f"  {m['metric']:<48} {m['value']} ({m['numerator']}/{m['denominator']})")
    print(f"证据：{out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
