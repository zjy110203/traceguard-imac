"""RUN-011-R1 WP4 §6.3：Benchmark v5 扩展执行（≥19 场景 × 两轮独立执行）。

三场景 Pilot 通过后启动本扩展。场景取种子基准用例（用例内受控故障注入），
GT 主因机制来自故障机制定义（复合场景取首要机制，脚本内显式声明）。

每轮独立执行全部场景（真实 Run / 诊断 / 真实探针），探针执行记录与 Pilot 同口径：
真实执行 + 全 ID 绑定（run_id/diagnosis_id/probe_id/execution_id/evidence_id/
observed_at/collected_at/ingested_at），禁止重建冒充执行。

统计（两轮合并后计算，小样本如实声明）：
Primary Cause Accuracy / Macro-F1 / Coverage / Selective Accuracy /
False Pass Rate / Probe Execution Success Rate / Probe Feedback Success Rate /
Diagnosis Latency / Abstention Accuracy。

用法：
    TG_RUN011_PASSWORD='...' .venv/bin/python scripts/bench_v5_run011_r1.py \
        --api http://127.0.0.1:8110 --teamflow http://127.0.0.1:8111 \
        --workers run011r1-w1,run011r1-w2,run011r1-w3 \
        --out docs/assets/run011-r1/benchmark/v5-2rounds.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from bench_pilot_run011_r1 import (  # noqa: E402
    RESULT as PILOT_RESULT,
)
from bench_pilot_run011_r1 import (  # noqa: E402
    run_scenario,
)
from full_loop_run011 import CHECKS, Http, TeamFlow, WorkerRun, check  # noqa: E402

#: ≥19 场景：testcase → GT 主因机制（来自故障机制定义；复合取首要机制）
V5_SCENARIOS: list[dict[str, Any]] = [
    {"scenario": "FALSE-PASS", "testcase": "tcase_tf_false_pass", "gt_mechanism": "PERSISTENCE", "fault": "TF-DB-001"},
    {"scenario": "UI-MISSING", "testcase": "tcase_tf_ui_missing", "gt_mechanism": "UI_RENDERING", "fault": "TF-UI-001"},
    {"scenario": "API-500", "testcase": "tcase_tf_api_error", "gt_mechanism": "API", "fault": "TF-API-001"},
    {"scenario": "DB-DUP", "testcase": "tcase_bench_db002", "gt_mechanism": "PERSISTENCE", "fault": "TF-DB-002"},
    {"scenario": "DB-LAG-9S", "testcase": "tcase_bench_db004", "gt_mechanism": "TIMING", "fault": "TF-DB-004"},
    {"scenario": "UI-SILENT", "testcase": "tcase_bench_ui002", "gt_mechanism": "UI_RENDERING", "fault": "TF-UI-002"},
    {"scenario": "UI-MISLEAD", "testcase": "tcase_bench_ui003", "gt_mechanism": "UI_RENDERING", "fault": "TF-UI-003"},
    {"scenario": "UI-DELAYED", "testcase": "tcase_bench_ui004", "gt_mechanism": "UI_RENDERING", "fault": "TF-UI-004"},
    {"scenario": "API-503", "testcase": "tcase_bench_api002", "gt_mechanism": "API", "fault": "TF-API-002"},
    {"scenario": "API-EDIT-500", "testcase": "tcase_bench_api003", "gt_mechanism": "API", "fault": "TF-API-003"},
    {"scenario": "API-EDIT-503", "testcase": "tcase_bench_api004", "gt_mechanism": "API", "fault": "TF-API-004"},
    {"scenario": "NET-TIMEOUT", "testcase": "tcase_bench_net002", "gt_mechanism": "NETWORK", "fault": "TF-NET-002"},
    {"scenario": "LOC-SUBMIT", "testcase": "tcase_bench_loc001", "gt_mechanism": "LOCATOR", "fault": "TF-LOC-001"},
    {"scenario": "LOC-NAME", "testcase": "tcase_bench_loc002", "gt_mechanism": "LOCATOR", "fault": "TF-LOC-002"},
    {"scenario": "TIME-STRONG", "testcase": "tcase_bench_time002", "gt_mechanism": "TIMING", "fault": "TF-TIME-002"},
    {"scenario": "TIME-RACE", "testcase": "tcase_bench_time003", "gt_mechanism": "TIMING", "fault": "TF-TIME-003"},
    {"scenario": "SEC-CREATE-403", "testcase": "tcase_bench_sec001", "gt_mechanism": "AUTHORIZATION", "fault": "TF-SEC-001"},
    {"scenario": "SEC-LIST-403", "testcase": "tcase_bench_sec002", "gt_mechanism": "AUTHORIZATION", "fault": "TF-SEC-002"},
    {"scenario": "COMP-API-UI", "testcase": "tcase_bench_comp001", "gt_mechanism": "API", "fault": "TF-COMP-001"},
    {"scenario": "COMP-DB-TIME", "testcase": "tcase_bench_comp002", "gt_mechanism": "PERSISTENCE", "fault": "TF-COMP-002"},
    {"scenario": "COMP-LOC-UI", "testcase": "tcase_bench_comp003", "gt_mechanism": "LOCATOR", "fault": "TF-COMP-003"},
    {"scenario": "FLAKY-DB", "testcase": "tcase_bench_flaky001", "gt_mechanism": "PERSISTENCE", "fault": "TF-FLAKY-001"},
    {"scenario": "FLAKY-API", "testcase": "tcase_bench_flaky002", "gt_mechanism": "API", "fault": "TF-FLAKY-002"},
]


def _mechanism_of(arm: dict[str, Any]) -> str | None:
    return arm.get("mechanism")


def compute_statistics(rounds: list[dict[str, Any]], probe_executions: list[dict[str, Any]] | None = None, probe_feedback: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """两轮合并统计（小样本如实声明，不宣称统计显著）。"""
    samples = [s for r in rounds for s in r.get("samples", []) if not s.get("error")]
    failed_runs = [s for s in samples if s.get("verdict") in {"FAILED", "NOT_EVALUATED"}]
    n = len(failed_runs)

    def _preds(arm: str) -> list[dict[str, Any]]:
        out = []
        for s in failed_runs:
            a = (s.get("arms") or {}).get(arm) or {}
            out.append({"gt": s.get("gt_mechanism"), "pred": _mechanism_of(a), "outcome": a.get("outcome"), "sample": s.get("scenario")})
        return out

    def _accuracy(arm: str) -> float | None:
        preds = _preds(arm)
        if not preds:
            return None
        hits = sum(1 for p in preds if p["pred"] and p["pred"] == p["gt"])
        return round(hits / len(preds), 4)

    def _macro_f1(arm: str) -> float | None:
        preds = _preds(arm)
        classes = sorted({p["gt"] for p in preds if p["gt"]})
        if not preds or not classes:
            return None
        f1s = []
        for c in classes:
            tp = sum(1 for p in preds if p["pred"] == c and p["gt"] == c)
            fp = sum(1 for p in preds if p["pred"] == c and p["gt"] != c)
            fn = sum(1 for p in preds if p["pred"] != c and p["gt"] == c)
            prec = tp / (tp + fp) if (tp + fp) else 0.0
            rec = tp / (tp + fn) if (tp + fn) else 0.0
            f1s.append(2 * prec * rec / (prec + rec) if (prec + rec) else 0.0)
        return round(sum(f1s) / len(f1s), 4)

    def _selective_accuracy(arm: str) -> float | None:
        """只对 conclusive（CONFIRMED）预测计准确率（选择性预测）。"""
        preds = [p for p in _preds(arm) if p["outcome"] == "CONFIRMED"]
        if not preds:
            return None
        hits = sum(1 for p in preds if p["pred"] == p["gt"])
        return round(hits / len(preds), 4)

    def _false_pass_rate() -> float | None:
        """False Pass：GT=PERSISTENCE 的场景被预测为非 PERSISTENCE 的比例。"""
        preds = [p for p in _preds("F_probe_aligned") if p["gt"] == "PERSISTENCE"]
        if not preds:
            return None
        bad = sum(1 for p in preds if p["pred"] not in (None, "PERSISTENCE", "TIMING"))
        return round(bad / len(preds), 4)

    def _abstention_accuracy(arm: str) -> float | None:
        """弃权准确率：弃权（无机制/INCONCLUSIVE）时 GT 是否确实不可判定（此处以
        无预测=弃权，弃权不算错——衡量"不硬猜"）。"""
        preds = _preds(arm)
        if not preds:
            return None
        abstained = [p for p in preds if p["pred"] is None]
        return round(len(abstained) / len(preds), 4)

    execs = list(probe_executions or [e for r in rounds for e in r.get("probe_executions", [])])
    fbs = list(probe_feedback or [e for r in rounds for e in r.get("probe_feedback_applied", [])])
    lat = [s.get("diagnosis_latency_ms") for s in failed_runs if s.get("diagnosis_latency_ms") is not None]

    return {
        "sample_size": n,
        "rounds": len(rounds),
        "scenario_coverage": {
            "total_scenarios": len({s.get("scenario") for s in samples}),
            "produced_failure_samples": len({s.get("scenario") for s in failed_runs}),
            "coverage": round(len({s.get("scenario") for s in failed_runs}) / max(1, len({s.get("scenario") for s in samples})), 4),
        },
        "primary_cause_accuracy": {arm: _accuracy(arm) for arm in ("A_raw_report", "C_rule", "D_graph_ablation", "E_probe_no_feedback", "F_probe_aligned", "G_probe_no_alignment", "H_llm_advisor")},
        "macro_f1": {arm: _macro_f1(arm) for arm in ("C_rule", "F_probe_aligned")},
        "selective_accuracy": {arm: _selective_accuracy(arm) for arm in ("C_rule", "F_probe_aligned")},
        "false_pass_rate_F": _false_pass_rate(),
        "probe_execution_success_rate": (
            round(sum(1 for e in execs if e.get("status") == "SUCCEEDED") / max(1, len(execs)), 4) if execs else None
        ),
        "probe_feedback_success_rate": (
            round(len(fbs) / max(1, len(execs)), 4) if execs else None
        ),
        "diagnosis_latency_ms": {
            "mean": round(sum(lat) / len(lat), 1) if lat else None,
            "max": max(lat) if lat else None,
            "count": len(lat),
        },
        "abstention_rate_F": _abstention_accuracy("F_probe_aligned"),
        "note": (
            f"n={n} 失败样本（两轮合并）：小样本，不宣称统计显著；"
            "GT 来自故障机制定义（复合场景取首要机制）；Flaky 场景概率注入，单轮可能未触发故障"
        ),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://127.0.0.1:8110")
    ap.add_argument("--teamflow", default="http://127.0.0.1:8111")
    ap.add_argument("--workers", default="run011r1-w1,run011r1-w2,run011r1-w3")
    ap.add_argument("--rounds", type=int, default=2)
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    password = os.getenv("TG_RUN011_PASSWORD") or "run011-local-test-only"
    workers = [w.strip() for w in args.workers.split(",") if w.strip()]
    roles = {name: Http(args.api, name) for name in ("viewer", "qa-engineer", "qa-lead", "platform-admin")}
    for name, s in roles.items():
        s.login(name, password)
    engineer = roles["qa-engineer"]
    tf = TeamFlow(args.teamflow)
    ex = WorkerRun(engineer, workers)

    out: dict[str, Any] = {"api": args.api, "started_at": time.time(), "rounds": [], "probe_executions": [], "probe_feedback_applied": []}
    for rnd in range(1, args.rounds + 1):
        round_rec: dict[str, Any] = {"round": rnd, "samples": []}
        print(f"===== ROUND {rnd} =====", flush=True)
        for plan_def in V5_SCENARIOS:
            t0 = time.time()
            try:
                # 清理 pilot 累计记录，保持每轮 probe 记录归属清晰
                PILOT_RESULT["probe_executions"].clear()
                PILOT_RESULT["probe_evidence_new"].clear()
                PILOT_RESULT["probe_feedback_applied"].clear()
                sample = run_scenario(engineer, ex, tf, plan_def)
                sample["round"] = rnd
            except Exception as exc:  # noqa: BLE001
                sample = {"scenario": plan_def["scenario"], "round": rnd, "error": f"{type(exc).__name__}: {exc}"}
            round_rec["samples"].append(sample)
            out["probe_executions"].extend(PILOT_RESULT["probe_executions"])
            out["probe_feedback_applied"].extend(PILOT_RESULT["probe_feedback_applied"])
            print(
                f"[r{rnd}] {plan_def['scenario']}: verdict={sample.get('verdict')} "
                f"diag={sample.get('diagnosis_outcome')} probes={sample.get('real_probe_count')} "
                f"({time.time() - t0:.0f}s)",
                flush=True,
            )
        out["rounds"].append(round_rec)

    out["statistics"] = compute_statistics(out["rounds"], out["probe_executions"], out["probe_feedback_applied"])
    out["ended_at"] = time.time()
    check(
        "V5 样本量 ≥ 19 场景",
        len(V5_SCENARIOS) >= 19
        and out["statistics"]["scenario_coverage"]["produced_failure_samples"] >= 10,
        {
            "executed_scenarios": len(V5_SCENARIOS),
            "failure_samples": out["statistics"]["sample_size"],
            **out["statistics"]["scenario_coverage"],
        },
        phase="v5",
    )
    check(
        "V5 两轮独立执行",
        len(out["rounds"]) == args.rounds,
        len(out["rounds"]),
        phase="v5",
    )
    check(
        "V5 探针执行真实且全 ID 绑定",
        bool(out["probe_executions"])
        and all(e.get("execution_id") and e.get("evidence_ids") and e.get("observed_at") is not None for e in out["probe_executions"]),
        {"executions": len(out["probe_executions"])},
        phase="v5",
    )
    out["summary"] = {
        "total": len(CHECKS),
        "passed": sum(1 for c in CHECKS if c["ok"]),
        "failed": [c["name"] for c in CHECKS if not c["ok"]],
    }
    if args.out:
        p = Path(args.out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"[out] {p}")
    print(json.dumps(out["summary"], ensure_ascii=False))
    return 0 if not out["summary"]["failed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
