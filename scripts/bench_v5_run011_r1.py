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
import re
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


#: RUN-011-R1.2：历史归档只留下异常字符串，正向控制必须严格满足
#: "HTTP 412 且 run PASSED（诊断正确地被跳过）"；其它异常一律保留为普通执行异常。
_POSITIVE_CONTROL_STATUS_RE = re.compile(r"\b412\b")
_POSITIVE_CONTROL_RUN_PASSED_RE = re.compile(r"run\s+PASSED", re.IGNORECASE)


def is_positive_control(sample: dict[str, Any]) -> bool:
    """判定正向控制记录：注入未触发故障，Run PASSED → 诊断正确跳过。

    未来运行应写入结构化字段 `positive_control`，避免继续依赖异常字符串识别；
    结构化字段存在时优先采用，历史归档再回退到严格的异常文本匹配。
    """
    flag = sample.get("positive_control")
    if isinstance(flag, bool):
        return flag
    error = str(sample.get("error") or "")
    return bool(
        _POSITIVE_CONTROL_STATUS_RE.search(error)
        and _POSITIVE_CONTROL_RUN_PASSED_RE.search(error)
    )


def compute_statistics(rounds: list[dict[str, Any]], probe_executions: list[dict[str, Any]] | None = None, probe_feedback: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """两轮合并统计（小样本如实声明，不宣称统计显著）。

    RUN-011-R1.2：执行记录、诊断样本、正向控制分开统计。
    正向控制不进入主因诊断准确率分母，也不计入 False Pass；
    普通执行异常不会被误归类为正向控制。
    """
    execution_records = [s for r in rounds for s in r.get("samples", [])]
    positive_controls = [s for s in execution_records if is_positive_control(s)]
    unclassified_errors = [
        s for s in execution_records if s.get("error") and not is_positive_control(s)
    ]
    diagnosis_samples = [
        s for s in execution_records if not s.get("error") and not is_positive_control(s)
    ]
    failed_runs = [s for s in diagnosis_samples if s.get("verdict") in {"FAILED", "NOT_EVALUATED"}]
    n = len(failed_runs)
    execution_scenarios = len({s.get("scenario") for s in execution_records})
    failure_scenarios = len({s.get("scenario") for s in failed_runs})
    failure_scenario_coverage = (
        round(failure_scenarios / max(1, execution_scenarios), 4) if execution_scenarios else None
    )

    # WP4 §6.1：历史 JSON 使用旧臂名，复核时必须按别名读取，不能因改名丢数据。
    legacy_alias = {
        "F_probe_post_hoc_aware": "F_probe_aligned",
        "G_probe_unknown_time": "G_probe_no_alignment",
    }

    def _preds(arm: str) -> list[dict[str, Any]]:
        out = []
        for s in failed_runs:
            arms = s.get("arms") or {}
            a = arms.get(arm) or arms.get(legacy_alias.get(arm, "")) or {}
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

    def _false_pass() -> tuple[int, int, float | None]:
        """Evaluator 级 False Pass：GT 期望失败，但实际 Run Verdict=PASSED。

        只统计实际注入故障且有有效 GT 的诊断样本；正向控制（本应 PASSED）
        与 F 臂预测机制均不参与计算。口径与 `DiagnosisEvaluator.false_pass_rate()`
        一致：GT 未持久化却判 PASSED 才是 False Pass。
        """
        gt_samples = [
            s for s in diagnosis_samples if str(s.get("gt_mechanism") or "").strip()
        ]
        count = sum(
            1 for s in gt_samples if str(s.get("verdict") or "").upper() == "PASSED"
        )
        denominator = len(gt_samples)
        return count, denominator, round(count / denominator, 4) if denominator else None

    false_pass_count, false_pass_denominator, false_pass_rate = _false_pass()

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

    # ---- RUN-011-R1.1 WP4 §6.2/6.3/6.4：把"混合口径"拆成可复核的分项统计 ----
    llm_samples = [s.get("llm_arm") or {} for s in failed_runs]
    llm_called = [m for m in llm_samples if m.get("called") is True]
    llm_invalid = [m for m in llm_samples if "invalid_json" in str(m.get("skipped") or "")]
    # 本轮真实数据用 skipped=llm_error:invalid_json 标记规则回退（无 fallback 布尔字段）
    llm_fallback = [m for m in llm_samples if str(m.get("skipped") or "")]
    llm_success_hits = sum(
        1
        for s in failed_runs
        if (s.get("llm_arm") or {}).get("called") is True
        and ((s.get("arms") or {}).get("H_llm_advisor") or {}).get("mechanism")
        == s.get("gt_mechanism")
    )

    updates: list[dict[str, Any]] = []
    for fb in fbs:
        for u in fb.get("hypothesis_updates") or []:
            updates.append(u)
    update_counts = {
        "SUPPORT": sum(1 for u in updates if u.get("relation") == "SUPPORT"),
        "CONTRADICT": sum(1 for u in updates if u.get("relation") == "CONTRADICT"),
        "CONFIRM": sum(1 for u in updates if u.get("relation") == "CONFIRM"),
        "EXCLUDE": sum(1 for u in updates if u.get("relation") == "EXCLUDE"),
    }
    alignments = [
        a
        for s in failed_runs
        for arm in (s.get("arms") or {}).values()
        for a in (arm.get("time_alignments") or [])
    ]
    e2e_lat = [s.get("exec_ms") for s in failed_runs if isinstance(s.get("exec_ms"), (int, float))]

    return {
        "sample_size": n,
        "rounds": len(rounds),
        # 兼容字段：含义修正为"执行场景 / 失败场景"，覆盖率为失败场景覆盖率。
        "scenario_coverage": {
            "total_scenarios": execution_scenarios,
            "produced_failure_samples": failure_scenarios,
            "coverage": failure_scenario_coverage,
        },
        # RUN-011-R1.2：拆分的清晰口径（历史 46 / 23 / 37 / 19 / 9）。
        "execution_records": len(execution_records),
        "execution_scenarios": execution_scenarios,
        "diagnosis_sample_size": len(diagnosis_samples),
        "failure_scenarios": failure_scenarios,
        "positive_control_records": len(positive_controls),
        "failure_scenario_coverage": failure_scenario_coverage,
        "unclassified_error_records": len(unclassified_errors),
        "primary_cause_accuracy": {arm: _accuracy(arm) for arm in ("A_raw_report", "C_rule", "D_graph_ablation", "E_probe_no_feedback", "F_probe_post_hoc_aware", "G_probe_unknown_time", "H_llm_advisor")},
        "macro_f1": {arm: _macro_f1(arm) for arm in ("C_rule", "F_probe_post_hoc_aware")},
        "selective_accuracy": {arm: _selective_accuracy(arm) for arm in ("C_rule", "F_probe_post_hoc_aware")},
        "false_pass_rate": false_pass_rate,
        "false_pass_count": false_pass_count,
        "false_pass_denominator": false_pass_denominator,
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
        "abstention_rate_F": _abstention_accuracy("F_probe_post_hoc_aware"),
        # WP4 修正后的分项口径（与上方兼容字段并存，不覆盖历史 JSON）
        "arm_definitions": {
            "F_probe_post_hoc_aware": "事后探针回灌（实测 time_alignment=POST_HOC），不是 Run 内 ALIGNED 探针",
            "G_probe_unknown_time": "同一探针但时间关系标 UNKNOWN，不是'无对齐'的因果对照",
            "H_llm_advisor": "混合臂：LLM 成功调用 + 规则 Fallback，不得当作 LLM 单独准确率",
        },
        "time_alignment_evidence": {
            "total": len(alignments),
            "ALIGNED": sum(1 for a in alignments if a == "ALIGNED"),
            "POST_HOC": sum(1 for a in alignments if a == "POST_HOC"),
            "UNKNOWN": sum(1 for a in alignments if a == "UNKNOWN"),
            "run_internal_aligned_probes_produced": False,
        },
        "llm_advisor_statistics": {
            "sample_size": n,
            "success_calls": len(llm_called),
            "success_rate": round(len(llm_called) / max(1, n), 4) if n else None,
            "invalid_json_calls": len(llm_invalid),
            "invalid_json_rate": round(len(llm_invalid) / max(1, n), 4) if n else None,
            "fallback_calls": len(llm_fallback),
            "fallback_rate": round(len(llm_fallback) / max(1, n), 4) if n else None,
            "candidate_count": sum(int(m.get("added") or 0) for m in llm_samples),
            "success_subset_accuracy": (
                round(llm_success_hits / len(llm_called), 4) if llm_called else None
            ),
            "note": "H 臂总体准确率混合了 LLM 成功与规则 Fallback，不能当作 LLM 自身准确率",
        },
        "probe_feedback_statistics": {
            "probe_executions": len(execs),
            "feedback_processed": len(fbs),
            "hypothesis_updates": len(updates),
            "SUPPORT": update_counts["SUPPORT"],
            "CONTRADICT": update_counts["CONTRADICT"],
            "CONFIRM": update_counts["CONFIRM"],
            "EXCLUDE": update_counts["EXCLUDE"],
            "note": "feedback_processed 只表示进入重新评估流程；只有 hypothesis_updates 才产生假设更新",
        },
        "diagnosis_latency_ms_breakdown": {
            "engine_only": {"mean": round(sum(lat) / len(lat), 1) if lat else None, "count": len(lat)},
            "end_to_end": {"mean": round(sum(e2e_lat) / len(e2e_lat), 1) if e2e_lat else None, "count": len(e2e_lat)},
        },
        "abstention": {
            "rate": _abstention_accuracy("F_probe_post_hoc_aware"),
            "accuracy": None,
            "note": "当前实现只统计弃权率；弃权判断正确性需要单独 GT，本轮 NOT MEASURED",
        },
        "note": (
            f"n={n} 失败样本（两轮合并）：小样本，不宣称统计显著；"
            "GT 来自故障机制定义（复合场景取首要机制）；Flaky 场景概率注入，单轮可能未触发故障；"
            f"{len(positive_controls)} 条正向控制已从诊断分母排除；"
            "False Pass 按真实 Run Verdict 与 GT 判定，与 F 臂预测机制无关"
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
