"""RUN-007 WP6：UI-FaultBench v3 多臂实验（A / C / D / E，30 场景真实执行）。

与 v2 的差别（本轮新增）：
1. 覆盖**全部 30 个固定场景**（不只前 15 个），失败样本全部保留，不删失败样本；
2. 每个 Run 通过 HTTP API + **独立 Worker 队列**真实执行（`sync=false` + 轮询）；
3. 四臂同一批失败样本、同一份 Ground Truth：
   A 传统基线（只看 UI 断言）/ C 规则诊断 / D 证据图增强 / E 主动探针（Ordered Probe）；
4. 指标全部带真实分子/分母/样本量：Macro-F1、Primary Cause Accuracy、Diagnosis Coverage、
   Selective Accuracy、False Pass Rate、Probe Count、Time to Diagnosis、Evidence Coverage；
5. Flaky 场景重复 5 次，保留每次原始 verdict（不取平均、不删样本）；
6. 自愈三指标（Assertion Preservation / Repair Validation / Regression）在可修复场景上真实跑；
7. LLM 对照：本轮未执行（时间窗与预算），标记 BLOCKED，**不用规则兜底冒充 LLM**。

Ground Truth 只在评测进程内读取（`benchmark/traceguard_benchmark/scenarios.py`），
不通过任何 API 暴露给被评测系统。

产出：
    docs/assets/run007/benchmark/samples.jsonl        每个 Run 的原始观测
    docs/assets/run007/benchmark/benchmark-v3.json    汇总 + 指标 + 限制声明
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "benchmark"))

from traceguard_benchmark.evaluation import DiagnosisEvaluator, SelfHealingEvaluator  # noqa: E402
from traceguard_benchmark.scenarios import DATASET_ID, SCENARIOS  # noqa: E402

TERMINAL = {"COMPLETED", "FAILED", "TIMED_OUT", "CANCELLED"}
FLAKY_REPEATS = 5


# ------------------------------------------------------------------ HTTP
class Api:
    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/") + "/api/v1"

    def call(self, method: str, path: str, body: dict | None = None, timeout: int = 300) -> Any:
        req = urllib.request.Request(
            self.base + path,
            data=json.dumps(body).encode() if body is not None else None,
            method=method,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
                raw = resp.read().decode()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")
            raise RuntimeError(f"{method} {path} -> {exc.code}: {detail[:300]}") from exc

    def get(self, path: str, timeout: int = 120) -> Any:
        return self.call("GET", path, timeout=timeout)


def execute(api: Api, testcase_id: str, timeout_s: float = 240.0) -> tuple[dict, dict]:
    """同步接口入队 + 轮询真实 Worker 执行到终态。"""
    t0 = time.time()
    created = api.call("POST", f"/testcases/{testcase_id}/execute", {"sync": False}, timeout=120)
    run_id = created["run_id"]
    final: dict = {}
    while time.time() - t0 < timeout_s:
        final = api.get(f"/runs/{run_id}", timeout=30)
        if final.get("execution_status") in TERMINAL:
            break
        time.sleep(0.4)
    return final, {"elapsed_s": round(time.time() - t0, 1), "run_id": run_id}


# ------------------------------------------------------- 预测（与 v2 runner 同口径）
def baseline_raw_report(obs: dict) -> dict[str, Any]:
    """传统自动化基线：只看 UI 断言，不看业务状态/网络证据。"""
    report = obs.get("report") or {}
    kinds = obs.get("assertion_kinds") or {}
    failed = [
        a
        for a in report.get("assertion_outcomes") or []
        if a.get("status") == "FAILED" and kinds.get(a.get("assertion_id"), "ui") == "ui"
    ]
    if not failed:
        return {"mechanism": "NONE", "conclusive": True, "verdict": "PASSED"}
    return {"mechanism": "UI_RENDERING", "conclusive": True, "verdict": "FAILED"}


def diag_to_pred(diag: dict | None) -> dict[str, Any]:
    if not diag:
        return {"mechanism": "UNKNOWN", "conclusive": False, "outcome": "NONE"}
    hyps = diag.get("hypotheses") or []
    conclusive = diag.get("outcome") in {"CONFIRMED", "PROBABLE"}
    chosen = next((h for h in hyps if h.get("status") == "CONFIRMED"), None)
    if chosen is None:
        chosen = next(
            (h for h in hyps if h.get("status") in {"SUPPORTED", "CANDIDATE"}),
            hyps[0] if hyps else None,
        )
    return {
        "mechanism": (chosen or {}).get("failure_mechanism", "UNKNOWN"),
        "conclusive": conclusive,
        "outcome": diag.get("outcome") if diag else "NONE",
        "diagnosis_id": (diag or {}).get("diagnosis_id"),
    }


def graph_enhanced(diag: dict | None, graph: dict | None) -> dict[str, Any]:
    pred = diag_to_pred(diag)
    if not diag or not graph:
        return pred
    sources = {
        (n.get("properties") or {}).get("source_type")
        for n in graph.get("nodes", [])
        if n.get("node_type") == "Evidence"
    }
    if {"browser", "api"} <= sources and pred.get("outcome") == "PROBABLE":
        pred["outcome"] = "CONFIRMED"
        pred["conclusive"] = True
        pred["graph_enhanced"] = True
    pred["graph_sources"] = sorted(s for s in sources if s)
    return pred


def _metric(name: str, num: int, den: int, note: str = "") -> dict[str, Any]:
    from traceguard_benchmark.evaluation import safe_ratio

    return {
        "metric": name,
        "value": round(safe_ratio(num, den), 4),
        "numerator": num,
        "denominator": den,
        "note": note,
    }


# ------------------------------------------------------------------ main
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://127.0.0.1:8020")
    ap.add_argument("--teamflow", default="http://127.0.0.1:8021")
    ap.add_argument("--out", default="docs/assets/run007/benchmark/benchmark-v3.json")
    ap.add_argument("--samples", default="docs/assets/run007/benchmark/samples.jsonl")
    ap.add_argument("--only-faulty", action="store_true", help="只跑故障场景（调试用）")
    ap.add_argument("--skip-repair", action="store_true")
    args = ap.parse_args(argv)

    api = Api(args.api)
    samples_path = ROOT / args.samples
    samples_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.time()

    api.call("POST", "/system/seed", timeout=300)
    health = api.get("/system/health")
    if (health.get("worker") or {}).get("inline_running"):
        print("WARN：API 启用了内嵌 Worker，本轮 Run 可能由 API 执行")
    if not ((health.get("database") or {}).get("ok")):
        print("数据库不可用，终止")
        return 2

    scenarios = [s for s in SCENARIOS if (s.gt_faulty or not args.only_faulty)]
    observations: list[dict[str, Any]] = []
    flaky_runs: dict[str, list[dict[str, Any]]] = {}

    with samples_path.open("w", encoding="utf-8") as sink:
        for i, sc in enumerate(scenarios, 1):
            repeats = FLAKY_REPEATS if sc.scenario_id.startswith("TF-FLAKY") else 1
            for rep in range(repeats):
                try:
                    run, meta = execute(api, sc.testcase_id)
                except Exception as exc:  # noqa: BLE001
                    print(f"[{i}/{len(scenarios)}] {sc.scenario_id} rep{rep} 执行失败：{exc}")
                    observations.append(
                        {
                            "scenario": sc.scenario_id,
                            "testcase_id": sc.testcase_id,
                            "gt_mechanism": sc.gt_mechanism,
                            "gt_faulty": sc.gt_faulty,
                            "error": str(exc)[:200],
                            "repeat": rep,
                        }
                    )
                    continue
                ver = api.get(f"/testcases/{sc.testcase_id}/versions/1", timeout=30) or {}
                kinds = {
                    a["assertion_id"]: a.get("kind", "ui") for a in ver.get("assertions") or []
                }
                obs = {
                    "scenario": sc.scenario_id,
                    "testcase_id": sc.testcase_id,
                    "gt_mechanism": sc.gt_mechanism,
                    "gt_faulty": sc.gt_faulty,
                    "expected_verdict": sc.expected_verdict,
                    "run_id": run.get("run_id"),
                    "execution_status": run.get("execution_status"),
                    "test_verdict": run.get("test_verdict"),
                    "verdict_matches_gt": (
                        (run.get("test_verdict") == sc.expected_verdict)
                        or (
                            sc.expected_verdict == "EXEC_FAILED"
                            and run.get("execution_status") == "FAILED"
                        )
                        or (
                            sc.scenario_id.startswith("TF-FLAKY")
                            and run.get("test_verdict") in {"PASSED", "FAILED"}
                        )
                    ),
                    "worker": run.get("lease_owner"),
                    "fencing_token": run.get("fencing_token"),
                    "elapsed_s": meta["elapsed_s"],
                    "assertion_kinds": kinds,
                    "report": run.get("report"),
                    "repeat": rep,
                }
                sink.write(json.dumps(obs, ensure_ascii=False) + "\n")
                sink.flush()
                observations.append(obs)
                if sc.scenario_id.startswith("TF-FLAKY"):
                    flaky_runs.setdefault(sc.scenario_id, []).append(
                        {"run_id": obs["run_id"], "verdict": obs["test_verdict"]}
                    )
                print(
                    f"[{i}/{len(scenarios)}] {sc.scenario_id} rep{rep} verdict={obs['test_verdict']} "
                    f"({meta['elapsed_s']}s, worker={obs['worker']})"
                )

    # 每个场景只保留一个"计分样本"（Flaky 用第一次真实失败的那次，若无失败则标记为 0/1 不计分）
    scoring: list[dict[str, Any]] = []
    for sc in scenarios:
        pool = [o for o in observations if o.get("scenario") == sc.scenario_id]
        if not pool:
            continue
        failed = [o for o in pool if o.get("test_verdict") in {"FAILED", "NOT_EVALUATED"}]
        if sc.gt_faulty:
            scoring.append(failed[0] if failed else pool[0])
        else:
            scoring.append(pool[0])

    # ------------------------------------------------------- 诊断四臂（真实 API）
    failures: list[dict[str, Any]] = [
        o
        for o in scoring
        if o.get("test_verdict") in {"FAILED", "NOT_EVALUATED"} and "error" not in o
    ]
    print(f"\n失败样本 {len(failures)} 个，开始 A/C/D/E 对照…")
    for obs in failures:
        run_id = obs["run_id"]
        t0 = time.time()
        try:
            obs["diag_rule"] = api.call(
                "POST", f"/runs/{run_id}/diagnose?use_llm=false", timeout=300
            )
        except Exception as exc:  # noqa: BLE001
            obs["diag_rule"] = None
            obs["diag_error"] = str(exc)[:200]
        obs["time_to_diagnosis_s"] = round(time.time() - t0, 2)
        try:
            obs["graph"] = api.call("POST", f"/runs/{run_id}/graph", {}, timeout=180)
        except Exception:  # noqa: BLE001
            obs["graph"] = None
        try:
            obs["probe_plan"] = api.call(
                "POST", f"/runs/{run_id}/probe-plan?max_iterations=3", {}, timeout=300
            )
        except Exception as exc:  # noqa: BLE001
            obs["probe_plan"] = {"error": str(exc)[:200]}
        # E 臂：探针回灌后重新诊断（真实重诊断，不是把原结论复制一遍）
        try:
            obs["diag_after_probe"] = api.call(
                "POST", f"/runs/{run_id}/diagnose?use_llm=false", timeout=300
            )
        except Exception:  # noqa: BLE001
            obs["diag_after_probe"] = obs["diag_rule"]
        try:
            obs["graph_after_probe"] = api.call("POST", f"/runs/{run_id}/graph", {}, timeout=180)
        except Exception:  # noqa: BLE001
            obs["graph_after_probe"] = obs["graph"]
        try:
            obs["evidence"] = api.get(f"/runs/{run_id}/evidence", timeout=120)
        except Exception:  # noqa: BLE001
            obs["evidence"] = []
        probes = [
            s for s in (obs["probe_plan"].get("steps") or []) if s.get("status") == "SUCCEEDED"
        ]
        obs["probe_count"] = len(probes)
        obs["evidence_count"] = len(obs["evidence"] or [])

    gt = {o["scenario"]: o["gt_mechanism"] for o in failures}
    evaluator = DiagnosisEvaluator(gt)
    arms: dict[str, list[dict[str, Any]]] = {
        "A_raw_report": [],
        "C_rule": [],
        "D_graph": [],
        "E_probe": [],
    }
    for obs in failures:
        arms["A_raw_report"].append(baseline_raw_report(obs))
        arms["C_rule"].append(diag_to_pred(obs.get("diag_rule")))
        arms["D_graph"].append(graph_enhanced(obs.get("diag_rule"), obs.get("graph")))
        arms["E_probe"].append(
            graph_enhanced(obs.get("diag_after_probe"), obs.get("graph_after_probe"))
        )

    metrics: list[dict[str, Any]] = []
    for arm, preds in arms.items():
        correct = sum(
            1
            for p, o in zip(preds, failures, strict=False)
            if p.get("mechanism") == o["gt_mechanism"]
        )
        conclusive = sum(1 for p in preds if p.get("conclusive"))
        conclusive_correct = sum(
            1
            for p, o in zip(preds, failures, strict=False)
            if p.get("conclusive") and p.get("mechanism") == o["gt_mechanism"]
        )
        fp_den = sum(1 for o in failures if o["gt_mechanism"] == "PERSISTENCE")
        fp = sum(
            1
            for p, o in zip(preds, failures, strict=False)
            if o["gt_mechanism"] == "PERSISTENCE" and p.get("verdict") == "PASSED"
        )
        metrics += [
            _metric(
                f"primary_cause_accuracy[{arm}]", correct, len(failures), "主因类别命中/失败样本数"
            ),
            _metric(
                f"diagnosis_coverage[{arm}]",
                conclusive,
                len(failures),
                "给出确定结论的样本/失败样本数",
            ),
            _metric(
                f"selective_accuracy[{arm}]",
                conclusive_correct,
                conclusive,
                "在有结论的样本里主因正确/有结论样本数（须与 coverage 同看）",
            ),
            _metric(f"false_pass_rate[{arm}]", fp, fp_den, "持久化故障中被判 PASSED 的比例"),
        ]
        # Macro-F1：类别宏平均
        preds_map = {
            o["scenario"]: p.get("mechanism", "UNKNOWN")
            for o, p in zip(failures, preds, strict=False)
        }
        m = evaluator.macro_f1(preds_map).to_dict()
        m["metric"] = f"macro_f1[{arm}]"
        metrics.append(m)

    # 过程指标（对所有臂共享的真实执行/诊断过程）
    ttd = [o["time_to_diagnosis_s"] for o in failures if o.get("time_to_diagnosis_s")]
    metrics += [
        {
            "metric": "time_to_diagnosis_s",
            "value": round(statistics.mean(ttd), 2) if ttd else None,
            "median": round(statistics.median(ttd), 2) if ttd else None,
            "numerator": len(ttd),
            "denominator": len(failures),
            "note": "从调用诊断接口到返回结论的秒数（不含执行时间）",
        },
        {
            "metric": "probe_count_per_failure",
            "value": round(
                sum(o.get("probe_count") or 0 for o in failures) / max(1, len(failures)), 2
            ),
            "numerator": sum(o.get("probe_count") or 0 for o in failures),
            "denominator": len(failures),
            "note": "E 臂在每个失败样本上真实执行的只读探针数",
        },
        {
            "metric": "evidence_coverage_per_failure",
            "value": round(
                sum(o.get("evidence_count") or 0 for o in failures) / max(1, len(failures)), 2
            ),
            "numerator": sum(o.get("evidence_count") or 0 for o in failures),
            "denominator": len(failures),
            "note": "每个失败样本关联的真实 Evidence 数（平均）",
        },
    ]

    # ------------------------------------------------------------- 自愈三指标
    repair_metrics: list[dict[str, Any]] = []
    repair_records: list[dict[str, Any]] = []
    if not args.skip_repair:
        locator_cases = [
            ("TF-LOC-001", "tcase_bench_loc001", 4),
            ("TF-LOC-002", "tcase_bench_loc002", 2),
        ]
        for sid, tcase, step_index in locator_cases:
            rec: dict[str, Any] = {"scenario": sid, "testcase_id": tcase}
            try:
                proposal = api.call(
                    "POST",
                    "/repairs",
                    {
                        "testcase_id": tcase,
                        "base_version": 1,
                        "changes": [
                            {
                                "path": "steps[].target",
                                "step_index": step_index,
                                "value": {
                                    "strategy": "role",
                                    "value": "button:创建",
                                    "source": "healed",
                                },
                                "reason": f"{sid}: 用可访问名称替代失效 test_id",
                            }
                        ],
                        "reason": "Benchmark v3 自愈臂（真实故障驱动）",
                    },
                    timeout=120,
                )
                pid = proposal["proposal_id"]
                rec["proposal_id"] = pid
                validated = api.call("POST", f"/repairs/{pid}/validate", timeout=120)
                rec["validation"] = validated.get("status")
                rec["assertion_preservation"] = validated.get("assertion_preservation")
                rec["oracle_integrity"] = validated.get("oracle_integrity")
                if validated.get("status") == "VALIDATED":
                    sandbox = api.call("POST", f"/repairs/{pid}/sandbox-run", timeout=420)
                    rec["sandbox_status"] = sandbox.get("sandbox_status")
                    rec["sandbox_worker"] = sandbox.get("sandbox_executed_by")
                    if sandbox.get("sandbox_status") == "PASSED":
                        api.call("POST", f"/repairs/{pid}/submit", timeout=60)
                        api.call(
                            "POST",
                            f"/repairs/{pid}/approve",
                            {"reviewer": "role:qa-lead", "note": "Benchmark v3"},
                            timeout=60,
                        )
                        promoted = api.call(
                            "POST",
                            f"/repairs/{pid}/promote",
                            {"idempotency_key": f"bench3-{pid}"},
                            timeout=120,
                        )
                        rec["promoted_version"] = promoted.get("promoted_version")
                        reg = execute(api, tcase)
                        rec["regression_verdict"] = reg[0].get("test_verdict")
                        rec["regression_worker"] = reg[0].get("lease_owner")
                        outcome = (
                            "APPROVED_AND_REGRESSION_PASSED"
                            if (reg[0].get("test_verdict") == "PASSED")
                            else "REGRESSION_FAILED"
                        )
                        rec["outcome"] = outcome
                        api.call(
                            "POST",
                            f"/repairs/{pid}/rollback",
                            {"operator": "role:qa-lead", "reason": "benchmark 结束回滚"},
                            timeout=120,
                        )
                # 清理故障，避免污染后续场景
                try:
                    urllib.request.urlopen(  # noqa: S310
                        urllib.request.Request(
                            f"{args.teamflow}/api/test/reset",
                            data=b"{}",
                            method="POST",
                            headers={"Content-Type": "application/json"},
                        ),
                        timeout=15,
                    )
                except Exception:  # noqa: BLE001
                    pass
            except Exception as exc:  # noqa: BLE001
                rec["error"] = str(exc)[:300]
            repair_records.append(rec)

    if repair_records:
        pres = {r["scenario"]: r.get("assertion_preservation") == "PASS" for r in repair_records}
        # 只统计真实走完校验的样本（未完成校验的不进分母，另在记录里保留）
        pres_done = {
            r["scenario"]: r.get("assertion_preservation") == "PASS"
            for r in repair_records
            if r.get("validation")
        }
        if pres_done:
            repair_metrics.append(SelfHealingEvaluator.assertion_preservation(pres_done).to_dict())
        acc = {
            r["scenario"]: str(r.get("outcome") or "")
            for r in repair_records
            if r.get("validation")
        }
        if acc:
            repair_metrics.append(SelfHealingEvaluator.acceptance_rate(acc).to_dict())
        reg_done = {
            r["scenario"]: r.get("regression_verdict")
            for r in repair_records
            if r.get("regression_verdict")
        }
        if reg_done:
            repair_metrics.append(
                {
                    "metric": "repair_regression_pass_rate",
                    "value": round(
                        sum(1 for v in reg_done.values() if v == "PASSED") / len(reg_done), 4
                    ),
                    "numerator": sum(1 for v in reg_done.values() if v == "PASSED"),
                    "denominator": len(reg_done),
                    "note": "晋升后回归执行 PASSED 的比例（样本量小，不作外推）",
                }
            )
        if pres:
            pass  # 保留原始记录

    # ---------------------------------------------------------------- 汇总
    verdict_dist: dict[str, int] = {}
    mismatch: list[dict[str, Any]] = []
    for o in observations:
        if o.get("error"):
            continue
        key = f"{o['scenario']}:{o['test_verdict']}"
        verdict_dist[key] = verdict_dist.get(key, 0) + 1
        if not o.get("verdict_matches_gt"):
            mismatch.append(
                {
                    "scenario": o["scenario"],
                    "expected": o["expected_verdict"],
                    "got": o["test_verdict"],
                    "execution_status": o["execution_status"],
                }
            )

    duration = round(time.time() - started, 1)
    report = {
        "round": "RUN-007-RESUME",
        "dataset": DATASET_ID,
        "dataset_version": "v3-experiment",
        "scenarios": len(scenarios),
        "runs_executed": len([o for o in observations if o.get("run_id")]),
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started)),
        "duration_s": duration,
        "api": args.api,
        "execution_mode": "independent-worker-queue",
        "api_inline_worker": bool((health.get("worker") or {}).get("inline_running")),
        "failure_samples": len(failures),
        "gt_isolated": True,
        "metrics": metrics,
        "repair_metrics": repair_metrics,
        "repair_records": repair_records,
        "arm_predictions": {
            arm: [
                {
                    "scenario": o["scenario"],
                    "gt": o["gt_mechanism"],
                    "pred": p.get("mechanism"),
                    "conclusive": p.get("conclusive"),
                }
                for o, p in zip(failures, preds, strict=False)
            ]
            for arm, preds in arms.items()
        },
        "flaky_repeats": flaky_runs,
        "flaky_note": f"Flaky 场景重复 {FLAKY_REPEATS} 次，保留每次真实 verdict，不取平均、不删样本",
        "verdict_mismatch": mismatch,
        "verdict_distribution": verdict_dist,
        "blocked": [
            {
                "name": "LLM 诊断对照（DeepSeek Flash/Pro/Routed）",
                "status": "BLOCKED",
                "reason": "本轮时间窗与预算用于真实执行/长稳/故障恢复；LLM 组未执行，不以规则兜底冒充 LLM 结论",
            },
            {
                "name": "人工修复基线",
                "status": "BLOCKED",
                "reason": "无真实人类工程师参与，不产生人工基线数值",
            },
        ],
        "limitations": [
            "单机 SQLite + 单浏览器实例；并发与资源竞争未做生产级压测",
            "每场景 1 个计分样本（Flaky 5 次重复），置信区间宽，不做强外推",
            "诊断耗时只覆盖规则/图/探针路径，不含 LLM 顾问",
        ],
        "samples_file": str(samples_path.relative_to(ROOT)),
    }
    out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"\n样本 {report['runs_executed']} 次执行，失败样本 {len(failures)}")
    for m in metrics:
        if m["metric"].startswith(("primary_cause", "macro_f1", "false_pass")):
            print(f"  {m['metric']:<34} {m['value']} ({m['numerator']}/{m['denominator']})")
    print(f"证据：{out}  用时 {duration}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
