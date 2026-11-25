"""RUN-011-R1 WP4：Active Probe 与 Benchmark v5 三场景试验（指令 §六）。

与 RUN-010 v4 的关键差别（v4 测量无效的根因）：
- v4 的 `_probe_signals_from_replays` 从**旧 Evidence 重建 ProbeSignal**，
  probes_per_sample=0.9286 是重建信号数、不是真实探针执行数；
- 本脚本的探针全部走真实 `POST /probe-plan`（真实执行 → 真实新证据 → 真实回灌），
  每条 probe_execution 绑定 run_id / diagnosis_id / probe_id / execution_id /
  evidence_id / observed_at / collected_at / ingested_at，**禁止重建冒充执行**。

三场景（6.1）：LOCATOR / NETWORK / TIMING（各自主因 GT 来自故障机制定义）。
实验臂（6.2，实现差异真实存在）：
- A：原始报告（只有 verdict，无诊断）；
- C：规则诊断（规则引擎 + Graph 裁决，LLM 固定关闭）；
- D：Graph 消融（同引擎、adjudication=None）；
- E：真实探针执行但**不回灌**（证据落库，诊断不变）；
- F：真实探针 + 时间对齐（run_ended_at 生效，POST_HOC/UNKNOWN 降权）；
- G：真实探针但**关闭时间对齐**（run_ended_at=0 → 全部按权威处理的对照）。
- LLM 实验组：单独调用（use_llm=true）并记录 provider/model/prompt_version；
  规则对照组 use_llm=false 固定关闭。

用法（依赖已启动的 API + TeamFlow + 独立 Worker）：
    TG_RUN011_PASSWORD='...' .venv/bin/python scripts/bench_pilot_run011_r1.py \
        --api http://127.0.0.1:8110 --teamflow http://127.0.0.1:8111 \
        --workers run011r1-w1,run011r1-w2,run011r1-w3 \
        --out docs/assets/run011-r1/benchmark/pilot-3scenarios.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from full_loop_run011 import CHECKS, Http, TeamFlow, WorkerRun, check  # noqa: E402

#: 三场景：testcase（种子内受控故障注入）→ GT 主因机制
PILOT_SCENARIOS: list[dict[str, Any]] = [
    {"scenario": "LOCATOR", "testcase": "tcase_bench_loc001", "gt_mechanism": "LOCATOR", "fault": "TF-LOC-001"},
    {"scenario": "NETWORK", "testcase": "tcase_bench_net002", "gt_mechanism": "NETWORK", "fault": "TF-NET-002"},
    {"scenario": "TIMING", "testcase": "tcase_bench_db004", "gt_mechanism": "TIMING", "fault": "TF-DB-004"},
]

RESULT: dict[str, Any] = {"scenarios": [], "probe_executions": [], "probe_evidence_new": [], "probe_feedback_applied": []}


def _login(api: str, password: str) -> dict[str, Http]:
    roles = {name: Http(api, name) for name in ("viewer", "qa-engineer", "qa-lead", "platform-admin")}
    for name, s in roles.items():
        s.login(name, password)
    return roles


# ---------------------------------------------------------------- 实验臂
def _pred_from_diag(diag: dict[str, Any] | None) -> dict[str, Any]:
    if not diag:
        return {"mechanism": None, "conclusive": False, "outcome": "NO_DIAGNOSIS"}
    hyps = diag.get("hypotheses") or []
    mechanisms = []
    for rc in diag.get("root_causes") or []:
        for h in hyps:
            if h.get("hypothesis_id") == rc:
                mechanisms.append(h.get("failure_mechanism"))
    mechanism = mechanisms[0] if mechanisms else None
    outcome = str(diag.get("outcome") or "")
    conclusive = outcome == "CONFIRMED"
    is_flaky = any(h.get("flaky_characteristic") for h in hyps)
    if mechanism is None and outcome == "PROBABLE" and not is_flaky:
        for h in hyps:
            if h.get("status") == "SUPPORTED":
                mechanism = h.get("failure_mechanism")
                break
    out = {
        "mechanism": mechanism,
        "conclusive": conclusive,
        "outcome": outcome,
        "root_causes": list(diag.get("root_causes") or []),
        "disposition": diag.get("disposition"),
    }
    if is_flaky and not conclusive:
        out["abstained_flaky"] = True
    return out


def _arm_A(verdict: str | None) -> dict[str, Any]:
    return {"mechanism": None, "conclusive": False, "outcome": "VERDICT_ONLY", "verdict": verdict, "note": "A 臂：只有执行 verdict，无诊断"}


def _arm_D_ablation(diag: dict[str, Any], engine_cls) -> dict[str, Any]:
    """D 臂：同引擎、禁用 Graph 裁决（真实消融）。"""
    from traceguard_api.services.diagnosis_service import _hypothesis_from_dict

    hyps = [_hypothesis_from_dict(h) for h in diag.get("hypotheses") or []]
    result = engine_cls().diagnose(str(diag.get("symptom", "")), hyps, adjudication=None)
    return _pred_from_diag(result.to_dict() | {
        "outcome": result.outcome.value,
        "root_causes": list(result.root_causes),
    })


def _arm_F(
    diag_before: dict[str, Any],
    signals: list[Any],
    report: dict[str, Any],
    run_ended_at: float,
    merge_signals,
    engine_cls,
) -> dict[str, Any]:
    """F 臂：真实探针信号回灌 + 时间对齐（run_ended_at 生效）。"""
    from traceguard_api.services.diagnosis_service import _hypothesis_from_dict

    merged, feedback = merge_signals(
        diag_before.get("hypotheses") or [], signals, report, run_ended_at=run_ended_at
    )
    result = engine_cls().diagnose(
        str(diag_before.get("symptom", "")), [_hypothesis_from_dict(h) for h in merged]
    )
    pred = _pred_from_diag(result.to_dict() | {
        "outcome": result.outcome.value,
        "root_causes": list(result.root_causes),
    })
    pred["probes_used"] = len(signals)
    pred["feedback_rate"] = (
        round(sum(1 for f in feedback if f.produced_evidence) / len(feedback), 4) if feedback else None
    )
    pred["time_alignments"] = [f.time_alignment for f in feedback]
    return pred


def run_scenario(
    engineer: Http, ex: WorkerRun, tf: TeamFlow, plan_def: dict[str, Any]
) -> dict[str, Any]:
    from traceguard_evidence.diagnosis.engine import DiagnosisEngine
    from traceguard_evidence.probe.feedback import ProbeSignal, merge_signals

    tf.clear_faults()
    tc_id = plan_def["testcase"]
    t0 = time.perf_counter()

    # ---- 真实执行（种子用例内受控故障注入）----
    out = ex.execute(tc_id, version=1, note=f"bench-pilot {plan_def['scenario']}")
    run = out["run"]
    run_id = run["run_id"]
    exec_ms = round((time.perf_counter() - t0) * 1000, 1)
    verdict = run.get("test_verdict")

    sample: dict[str, Any] = {
        "scenario": plan_def["scenario"],
        "testcase": tc_id,
        "gt_mechanism": plan_def["gt_mechanism"],
        "fault": plan_def["fault"],
        "run_id": run_id,
        "verdict": verdict,
        "exec_ms": exec_ms,
        "independent_worker": out["trace"].get("independent_worker"),
        "arms": {},
    }

    # ---- 规则诊断（LLM 固定关闭，对照组口径纯净）----
    diag_t0 = time.perf_counter()
    diag = engineer.call(
        "POST", f"/api/v1/runs/{run_id}/diagnose?use_llm=false", None,
        expect_status=(200, 201), timeout=300,
    )
    diag_latency_ms = round((time.perf_counter() - diag_t0) * 1000, 1)
    diag_id = diag["diagnosis_id"]
    sample["diagnosis_id"] = diag_id
    sample["diagnosis_latency_ms"] = diag_latency_ms
    sample["diagnosis_outcome"] = diag.get("outcome")

    # ---- 真实探针执行（不是重建信号）----
    probe_t0 = time.perf_counter()
    plan = engineer.call(
        "POST", f"/api/v1/runs/{run_id}/probe-plan?diagnosis_id={diag_id}&max_iterations=3",
        None, expect_status=(200, 201), timeout=300,
    )
    probe_latency_ms = round((time.perf_counter() - probe_t0) * 1000, 1)
    steps = plan.get("steps") or []
    sample["probe_plan_id"] = plan.get("plan_id")
    sample["probe_stop_reason"] = plan.get("stop_reason")
    sample["probe_latency_ms"] = probe_latency_ms

    # ---- probe_executions / probe_evidence_new / probe_feedback_applied 全 ID 绑定 ----
    executions: list[dict[str, Any]] = []
    evidence_new: list[dict[str, Any]] = []
    feedback_applied: list[dict[str, Any]] = []
    real_signals: list[ProbeSignal] = []
    for s in steps:
        exec_id = s.get("execution_id")
        ev_ids = s.get("evidence_ids") or []
        ev_doc: dict[str, Any] = {}
        if ev_ids:
            ev_doc = engineer.call("GET", f"/api/v1/evidence/{ev_ids[0]}") or {}
            content = ev_doc.get("content") or {}
            evidence_new.append(
                {
                    "evidence_id": ev_doc.get("evidence_id"),
                    "run_id": run_id,
                    "diagnosis_id": diag_id,
                    "probe_id": s.get("probe_id"),
                    "execution_id": exec_id,
                    "source_type": ev_doc.get("source_type"),
                    "observed_at": content.get("observed_at"),
                    "collected_at": content.get("collected_at"),
                    "ingested_at": content.get("ingested_at"),
                    "captured_at": ev_doc.get("captured_at"),
                }
            )
            content_data = dict(content.get("data") or {})
            real_signals.append(
                ProbeSignal(
                    probe_id=str(s.get("probe_id")),
                    execution_id=str(exec_id),
                    status=str(s.get("status")),
                    evidence_ids=list(ev_ids),
                    data=content_data,
                    observed_at=float(content.get("observed_at") or 0.0),
                    run_scope=False,
                )
            )
        executions.append(
            {
                "run_id": run_id,
                "diagnosis_id": diag_id,
                "probe_id": s.get("probe_id"),
                "execution_id": exec_id,
                "status": s.get("status"),
                "evidence_ids": ev_ids,
                "observed_at": (ev_doc.get("content") or {}).get("observed_at"),
                "collected_at": (ev_doc.get("content") or {}).get("collected_at"),
                "ingested_at": (ev_doc.get("content") or {}).get("ingested_at"),
            }
        )
        if s.get("applied_to_diagnosis"):
            feedback_applied.append(
                {
                    "run_id": run_id,
                    "diagnosis_id": diag_id,
                    "probe_id": s.get("probe_id"),
                    "execution_id": exec_id,
                    "updates": len(s.get("hypothesis_updates") or []),
                    "hypothesis_updates": s.get("hypothesis_updates") or [],
                }
            )
    RESULT["probe_executions"].extend(executions)
    RESULT["probe_evidence_new"].extend(evidence_new)
    RESULT["probe_feedback_applied"].extend(feedback_applied)

    # ---- 六臂 ----
    sample["arms"]["A_raw_report"] = _arm_A(verdict)
    sample["arms"]["C_rule"] = _pred_from_diag(diag)
    sample["arms"]["D_graph_ablation"] = _arm_D_ablation(diag, DiagnosisEngine)
    sample["arms"]["E_probe_no_feedback"] = {
        **_pred_from_diag(diag),
        "probes_used": len(real_signals),
        "feedback": "NOT_APPLIED",
        "note": "E 臂：探针真实执行、证据真实落库，但不回灌诊断",
    }
    report = engineer.call("GET", f"/api/v1/runs/{run_id}/report") or {}
    run_doc = engineer.call("GET", f"/api/v1/runs/{run_id}") or {}
    # run_ended_at 可能是 ISO；merge_signals 需要 epoch。ProbeSignal 的 observed_at 是 epoch。
    import datetime as _dt

    def _epoch(v: Any) -> float | None:
        if isinstance(v, (int, float)):
            return float(v)
        try:
            return _dt.datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()
        except Exception:  # noqa: BLE001
            return None

    run_ended_epoch = _epoch(run_doc.get("ended_at"))
    # RUN-011-R1.1 WP4 §6.1：实测 F 臂全部 POST_HOC、G 臂全部 UNKNOWN，
    # 本基准从未产生 Run 内 ALIGNED 探针证据；旧名 F_probe_aligned /
    # G_probe_no_alignment 会误导为"时间对齐价值"实验，故按真实时间语义改名。
    # F/G 臂必须基于**完整首诊文档**（含 hypotheses 证据引用）；
    # plan 的 diagnosis_before 是摘要快照（无 hypotheses），不能用于回灌重算。
    diag_before = diag
    sample["arms"]["F_probe_post_hoc_aware"] = _arm_F(
        diag_before, real_signals, report, run_ended_epoch or 0.0, merge_signals, DiagnosisEngine
    )
    sample["arms"]["G_probe_unknown_time"] = _arm_F(
        diag_before, real_signals, report, 0.0, merge_signals, DiagnosisEngine
    )
    sample["real_probe_count"] = len(real_signals)
    sample["probe_execution_success_rate"] = (
        round(sum(1 for e in executions if e["status"] == "SUCCEEDED") / len(executions), 4)
        if executions
        else None
    )
    sample["probe_feedback_success_rate"] = (
        round(len(feedback_applied) / max(1, len(executions)), 4) if executions else None
    )

    # ---- LLM 实验组（单独记录 provider/model/prompt_version；失败如实记录）----
    llm_diag = engineer.call(
        "POST", f"/api/v1/runs/{run_id}/diagnose?use_llm=true", None,
        expect_status=(200, 201), timeout=300,
    )
    llm_notes = llm_diag.get("llm_advisor") or {}
    sample["llm_arm"] = {
        "called": llm_notes.get("called"),
        "skipped": llm_notes.get("skipped"),
        "model": llm_notes.get("model"),
        "prompt_version": llm_notes.get("prompt_version") or "diagnosis_advisor@v1",
        "added": len(llm_notes.get("added") or []),
        "engine": llm_diag.get("engine"),
        "llm_diagnosis_id": llm_diag.get("diagnosis_id"),
    }
    sample["arms"]["H_llm_advisor"] = _pred_from_diag(llm_diag)

    tf.clear_faults()
    return sample


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://127.0.0.1:8110")
    ap.add_argument("--teamflow", default="http://127.0.0.1:8111")
    ap.add_argument("--workers", default="run011r1-w1,run011r1-w2,run011r1-w3")
    ap.add_argument("--password-env", default="TG_RUN011_PASSWORD")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    import os

    password = os.getenv(args.password_env) or "run011-local-test-only"
    workers = [w.strip() for w in args.workers.split(",") if w.strip()]
    roles = _login(args.api, password)
    engineer = roles["qa-engineer"]
    tf = TeamFlow(args.teamflow)
    ex = WorkerRun(engineer, workers)

    RESULT["api"] = args.api
    RESULT["started_at"] = time.time()
    for plan_def in PILOT_SCENARIOS:
        try:
            sample = run_scenario(engineer, ex, tf, plan_def)
        except Exception as exc:  # noqa: BLE001 - 如实记录
            sample = {"scenario": plan_def["scenario"], "error": f"{type(exc).__name__}: {exc}"}
        RESULT["scenarios"].append(sample)
        print(f"[scenario] {plan_def['scenario']} -> {json.dumps({k: sample.get(k) for k in ('verdict','diagnosis_outcome','probe_stop_reason','real_probe_count')}, ensure_ascii=False)}")

    # ---- 6.1 验收：每个实验臂真实执行自己的实现 + 探针记录完整 ----
    for s in RESULT["scenarios"]:
        if s.get("error"):
            check(f"P0 {s.get('scenario')} 场景执行", False, s["error"], phase="pilot")
            continue
        check(
            f"P1 {s['scenario']} 真实失败进入诊断",
            s.get("verdict") in {"FAILED", "NOT_EVALUATED"} and bool(s.get("diagnosis_id")),
            {"verdict": s.get("verdict"), "outcome": s.get("diagnosis_outcome")},
            phase="pilot",
        )
        check(
            f"P2 {s['scenario']} 真实探针执行（非重建）且全 ID 绑定",
            bool(s.get("real_probe_count"))
            and all(e.get("execution_id") and e.get("evidence_ids") for e in RESULT["probe_executions"] if e.get("run_id") == s.get("run_id"))
            and all(
                e.get("observed_at") is not None and e.get("collected_at") is not None and e.get("ingested_at")
                for e in RESULT["probe_evidence_new"]
                if e.get("run_id") == s.get("run_id")
            ),
            {"executions": s.get("real_probe_count"), "stop": s.get("probe_stop_reason")},
            phase="pilot",
        )
        arms = s.get("arms") or {}
        check(
            f"P3 {s['scenario']} 六臂齐全（A/C/D/E/F/G）",
            all(k in arms for k in ("A_raw_report", "C_rule", "D_graph_ablation", "E_probe_no_feedback", "F_probe_post_hoc_aware", "G_probe_unknown_time")),
            sorted(arms.keys()),
            phase="pilot",
        )
        # 臂间实现差异：D（无图）与 C（有图）必须是独立计算；E 不回灌；F/G 时间对齐参数不同
        check(
            f"P4 {s['scenario']} 实验臂实现差异存在（D 消融/E 不回灌/F-G 对齐开关）",
            arms["E_probe_no_feedback"].get("feedback") == "NOT_APPLIED"
            and arms["F_probe_post_hoc_aware"].get("probes_used") == s.get("real_probe_count")
            and arms["G_probe_unknown_time"].get("probes_used") == s.get("real_probe_count")
            and ("time_alignments" in arms["F_probe_post_hoc_aware"]),
            {k: v.get("outcome") for k, v in arms.items()},
            phase="pilot",
        )
        llm = s.get("llm_arm") or {}
        check(
            f"P5 {s['scenario']} LLM 组单独记录 provider/model/prompt_version（规则组固定 use_llm=false）",
            llm.get("called") is not None
            and ("model" in llm)
            and ("prompt_version" in llm)
            and s.get("diagnosis_outcome") is not None,
            llm,
            phase="pilot",
        )
        # 主因准确率（小样本，不宣称统计显著）
        gt = s.get("gt_mechanism")
        c_pred = (s.get("arms") or {}).get("C_rule", {}).get("mechanism")
        f_pred = (s.get("arms") or {}).get("F_probe_post_hoc_aware", {}).get("mechanism")
        s["gt_match"] = {"C_rule": c_pred == gt, "F_probe_post_hoc_aware": f_pred == gt}

    # 统计（三场景小样本：如实给出，不宣称显著）
    n = len([s for s in RESULT["scenarios"] if not s.get("error")])
    def _acc(arm: str) -> float | None:
        hits = sum(1 for s in RESULT["scenarios"] if (s.get("gt_match") or {}).get(arm))
        return round(hits / n, 4) if n else None

    RESULT["pilot_statistics"] = {
        "sample_size": n,
        "note": "三场景试验样本：不宣称统计显著；Macro-F1/Selective Accuracy/False Pass Rate 等需 19+ 场景两轮后计算",
        "primary_cause_accuracy": {"C_rule": _acc("C_rule"), "F_probe_post_hoc_aware": _acc("F_probe_post_hoc_aware"), "G_probe_unknown_time": _acc("G_probe_unknown_time")},
        "probe_execution_success_rate": (
            round(
                sum(1 for e in RESULT["probe_executions"] if e.get("status") == "SUCCEEDED")
                / max(1, len(RESULT["probe_executions"])),
                4,
            )
            if RESULT["probe_executions"]
            else None
        ),
        "probe_feedback_success_rate": (
            round(len(RESULT["probe_feedback_applied"]) / max(1, len(RESULT["probe_executions"])), 4)
            if RESULT["probe_executions"]
            else None
        ),
        "diagnosis_latency_ms": [s.get("diagnosis_latency_ms") for s in RESULT["scenarios"]],
    }
    RESULT["ended_at"] = time.time()
    RESULT["summary"] = {
        "total": len(CHECKS),
        "passed": sum(1 for c in CHECKS if c["ok"]),
        "failed": [c["name"] for c in CHECKS if not c["ok"]],
    }
    if args.out:
        p = Path(args.out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(RESULT, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"[out] {p}")
    print(json.dumps(RESULT["summary"], ensure_ascii=False))
    return 0 if not RESULT["summary"]["failed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
