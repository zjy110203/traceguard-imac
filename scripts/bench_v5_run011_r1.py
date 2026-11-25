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
from bench_scenario_contract import (  # noqa: E402
    RecordClass,
    TriState,
    classify_record,
    contract_of,
    is_confirmed_failing_sample,
    scenario_from_contract,
    validate_contracts,
)
from full_loop_run011 import CHECKS, Http, TeamFlow, WorkerRun, check  # noqa: E402

#: ≥19 场景：期望结果、控制类别与故障触发条件全部由场景契约显式声明。
#: 历史归档里「412 + Run PASSED」的 9 条记录不再靠异常字符串反推正向控制。
V5_SCENARIOS: list[dict[str, Any]] = [
    scenario_from_contract(name)
    for name in (
        "FALSE-PASS", "UI-MISSING", "API-500", "DB-DUP", "DB-LAG-9S", "UI-SILENT",
        "UI-MISLEAD", "UI-DELAYED", "API-503", "API-EDIT-500", "API-EDIT-503",
        "NET-TIMEOUT", "LOC-SUBMIT", "LOC-NAME", "TIME-STRONG", "TIME-RACE",
        "SEC-CREATE-403", "SEC-LIST-403", "COMP-API-UI", "COMP-DB-TIME",
        "COMP-LOC-UI", "FLAKY-DB", "FLAKY-API",
    )
]

CONTRACT_PROBLEMS = validate_contracts(V5_SCENARIOS)


def _mechanism_of(arm: dict[str, Any]) -> str | None:
    return arm.get("mechanism")


def is_positive_control(sample: dict[str, Any]) -> bool:
    """判断是否属于 R1.2 的「非诊断记录」兼容桶（必须从诊断分母排除）。

    包括两类：预期通过的正向控制，以及 Flaky 故障未触发的样本。
    RUN-012 起二者分别记录在 `expected_pass_controls` 与
    `flaky_fault_not_triggered`，不再混为一个类别；本函数仅为兼容历史字段。
    """
    classified = classify_record(sample)
    if classified["classification_confidence"] == "LEGACY_412_HEURISTIC_NOT_CONFIRMED":
        return True
    return classified["record_class"] in {
        RecordClass.NORMAL_POSITIVE_CONTROL.value,
        RecordClass.FAULT_NOT_TRIGGERED.value,
    }


def enrich_record(sample: dict[str, Any]) -> dict[str, Any]:
    """给执行记录补齐分类与完整上下文；不修改原始字节，只新增/覆盖派生字段。"""
    classified = classify_record(sample)
    contract = contract_of(sample)
    merged = dict(sample)
    merged.update(classified)
    merged["classification"] = classified["record_class"]
    if contract is not None and "fault_trigger_condition" not in merged:
        merged["fault_trigger_condition"] = contract.fault_trigger_condition
    return merged


def _exception_record(plan_def: dict[str, Any], round_no: int, exc: BaseException) -> dict[str, Any]:
    """异常记录保留 GT / 故障 / TestCase / 诊断上下文，不再只留三个字段。"""
    error_type = type(exc).__name__
    partial = getattr(exc, "sample", None)
    if isinstance(partial, dict):
        record = dict(partial)
        record.setdefault("round", round_no)
        record.setdefault("error", f"{error_type}: {exc}")
        return enrich_record(record)
    record: dict[str, Any] = {
        "scenario": plan_def.get("scenario"),
        "round": round_no,
        "error": f"{error_type}: {exc}",
        "error_type": error_type,
        "error_message": str(exc)[:1000],
    }
    for key in (
        "testcase", "fault", "gt_mechanism", "expected_run_outcome",
        "control_kind", "fault_trigger_condition",
    ):
        if key in plan_def:
            record[key] = plan_def[key]
    return enrich_record(record)


def compute_statistics(rounds: list[dict[str, Any]], probe_executions: list[dict[str, Any]] | None = None, probe_feedback: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """两轮合并统计（小样本如实声明，不宣称统计显著）。

    RUN-011-R1.2：执行记录、诊断样本、正向控制分开统计。
    正向控制不进入主因诊断准确率分母，也不计入 False Pass；
    普通执行异常不会被误归类为正向控制。
    """
    execution_records = [s for r in rounds for s in r.get("samples", [])]
    classified_records = [classify_record(s) for s in execution_records]
    class_counts = {
        kind.value: sum(1 for c in classified_records if c["record_class"] == kind.value)
        for kind in RecordClass
    }
    positive_controls = [s for s in execution_records if is_positive_control(s)]
    unclassified_errors = [
        s for s in execution_records if s.get("error") and not is_positive_control(s)
    ]
    diagnosis_samples = [
        s for s in execution_records if not s.get("error") and not is_positive_control(s)
    ]
    # R1.2 兼容桶与 RUN-012 五分类的交叉核对：历史 9 条异常中，
    # 8 条是契约声明的预期通过控制，1 条是 Flaky 故障未触发，不能混为一类。
    legacy_control_keys = {
        (s.get("scenario"), s.get("round")) for s in positive_controls
    }
    contract_controls = [
        c for c in classified_records
        if c["record_class"] == RecordClass.NORMAL_POSITIVE_CONTROL.value
    ]
    flaky_not_triggered = [
        c for c in classified_records
        if c["record_class"] == RecordClass.FAULT_NOT_TRIGGERED.value
    ]
    false_pass_records = [
        c for c in classified_records if c["record_class"] == RecordClass.FALSE_PASS.value
    ]
    unknown_activation = [
        c for c in classified_records
        if c["record_class"] == RecordClass.UNKNOWN_FAULT_ACTIVATION.value
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
        """R1.2 兼容口径：有 GT 的诊断样本中 Run Verdict=PASSED 的数量。

        RUN-012 的严格口径见下方 false_pass_evaluation：额外要求故障触发证据。
        两者并列保留，避免悄悄改变历史数字；新报告以严格口径为准。
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

    # ---- RUN-012 WP0：与 Evaluator 业务语义一致的严格 False Pass 口径 ----
    # 分子：有有效 GT + 场景预期失败 + 确认故障已触发 + 实际 Run Verdict=PASSED；
    # 分母：有有效 GT + 场景预期失败 + 确认故障已触发的全部样本。
    # 412 不自动排除；故障未触发的 Flaky 样本与正向控制都不进分母。
    confirmed_failing = [c for c in classified_records if is_confirmed_failing_sample(c)]
    strict_fp_count = sum(
        1 for c in confirmed_failing
        if c["actual_verdict"] == "PASSED"
        and c["record_class"] == RecordClass.FALSE_PASS.value
    )
    strict_denominator = len(confirmed_failing)
    inferred_only = [
        c for c in confirmed_failing if c["fault_effect_status"] == TriState.INFERRED_FROM_VERDICT.value
    ]
    strict_evidence_level = (
        "NOT_MEASURED" if not confirmed_failing
        else "INFERRED_ONLY" if len(inferred_only) == len(confirmed_failing)
        else "MIXED"
    )
    false_pass_evaluation = {
        "definition": (
            "GT 有效 + 场景预期失败 + 故障触发已确认（CONFIRMED 或由失败 Verdict 推断）"
            " + Run Verdict=PASSED；诊断接口 412 不排除记录"
        ),
        "numerator": strict_fp_count,
        "denominator": strict_denominator,
        "rate": round(strict_fp_count / strict_denominator, 4) if strict_denominator else None,
        "excluded_positive_controls": len(contract_controls),
        "excluded_flaky_not_triggered": len(flaky_not_triggered),
        "excluded_unknown_activation": len(unknown_activation),
        "evidence_level": strict_evidence_level,
        "status": (
            "NOT_MEASURED" if not confirmed_failing
            else "PARTIAL" if strict_evidence_level == "INFERRED_ONLY"
            else "VERIFIED"
        ),
        "note": (
            "历史归档未记录 fault_state 快照，37 条负样本的故障生效只能由 Verdict 推断，"
            "因此历史指标只能标 PARTIAL；未来运行写入 fault_activation_status / "
            "fault_effect_status 后方可升级为 VERIFIED"
        ),
    }

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
        # RUN-012 WP0：五分类 + 严格 False Pass；旧字段含义不变以便历史对照。
        "record_classification_counts": class_counts,
        "expected_pass_controls": len(contract_controls),
        "flaky_fault_not_triggered": len(flaky_not_triggered),
        "false_pass_candidates": len(false_pass_records),
        "unknown_fault_activation": len(unknown_activation),
        "legacy_positive_control_keys": sorted(
            f"{s}:{r}" for s, r in legacy_control_keys if s is not None
        ),
        "false_pass_evaluation": false_pass_evaluation,
        "contract_problems": list(CONTRACT_PROBLEMS),
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
                # 补齐场景契约、故障激活证据与诊断状态，不允许上下文在执行后才丢失。
                sample = enrich_record(sample)
            except Exception as exc:  # noqa: BLE001
                # 412 是「Run PASSED → 诊断正确跳过」的可记录业务结果，
                # 但因异常路径截断而丢失的上下文必须在这里补回。
                sample = _exception_record(plan_def, rnd, exc)
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
        "V5 场景契约完整（期望/控制类别/故障触发条件）",
        not CONTRACT_PROBLEMS,
        CONTRACT_PROBLEMS,
        phase="v5",
    )
    check(
        "V5 无未知故障触发状态记录",
        out["statistics"]["unknown_fault_activation"] == 0
        and out["statistics"]["unclassified_error_records"] == 0,
        {
            "unknown": out["statistics"]["unknown_fault_activation"],
            "unclassified_errors": out["statistics"]["unclassified_error_records"],
        },
        phase="v5",
    )
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
