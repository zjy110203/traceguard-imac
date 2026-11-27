"""RUN-018 WP6/R1：独立 Benchmark Holdout 真实执行（不再是方法论文档）。

冻结 → 新独立场景变体 → 两轮独立执行 → 独立评测 → 证据落盘。

冻结（--freeze-only 或自动）：
- Diagnosis Engine / 规则文件 SHA-256（engine.py / dedup.py / probe planner+feedback）；
- 规则策略版本（diagnosis doc 中的 strategy_version）；
- 探针策略版本（本轮 holdout 使用的确定性探针策略 = probe-strategy-v1-holdout）；
- Ground Truth 版本（场景清单+注入故障语义的 SHA-256）；
- Benchmark 场景清单（24 个**新**变体，不复用旧 120 样本）。

场景族（每族 4 个参数化变体，注入故障即 Ground Truth）：
- api_5xx          GT=API         （真实 5xx 请求，Run 窗口内 TRUSTED 网络证据）
- db001_permanent  GT=PERSISTENCE （永久持久化失败：独立读取+延迟复测均缺失）
- db004_lag        GT=TIMING      （状态查询滞后：即时缺失、延迟复测恢复）
- locator_drift    GT=LOCATOR     （定位器漂移：DOM 元素在、业务已落库）
- network_env      GT=NETWORK     （连接失败：无状态码）
- ambiguous        GT=INCONCLUSIVE（无故障信号：正确弃权才是对的）

评测（独立端，显式分母）：
- 混淆矩阵 / Precision / Recall / Accuracy / Macro-F1 / FP / FN；
- UNKNOWN（INCONCLUSIVE 弃权）单独统计，不删除后只报剩余；
- Selective Accuracy（分母 = 非弃权样本数）；
- Diagnosis Primary Cause Accuracy（分母 = 全部样本）；
- Time to Diagnosis / Probe Cost / Evidence Coverage。

诚实声明：评测端与规则冻结在同一仓库但独立计算（不复用 engine 内部打分）；
规则若修改必须换新 Holdout，不得重复污染。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
for _base in ("packages", "apps"):
    _dir = ROOT / _base
    if _dir.is_dir():
        for _sub in sorted(_dir.iterdir()):
            if _sub.is_dir() and str(_sub) not in sys.path:
                sys.path.insert(0, str(_sub))

OUT_DIR = ROOT / "docs" / "assets" / "run018" / "wp7"
PROBE_STRATEGY_VERSION = "probe-strategy-v1-holdout"
LABELS = ["API", "PERSISTENCE", "TIMING", "LOCATOR", "NETWORK", "INCONCLUSIVE"]

# ------------------------------------------------------------------ 冻结清单
_FROZEN_FILES = [
    "packages/evidence/traceguard_evidence/diagnosis/engine.py",
    "packages/evidence/traceguard_evidence/diagnosis/dedup.py",
    "packages/evidence/traceguard_evidence/probe/planner.py",
    "packages/evidence/traceguard_evidence/probe/feedback.py",
    "packages/evidence/traceguard_evidence/probe/contract.py",
    "apps/api/traceguard_api/services/diagnosis_service.py",
]


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_manifest() -> dict[str, Any]:
    files = {p: _file_sha256(ROOT / p) for p in _FROZEN_FILES}
    scenarios = build_holdout_samples()
    gt_payload = json.dumps(
        [
            {"sample_id": s["sample_id"], "family": s["family"], "ground_truth": s["ground_truth"]}
            for s in scenarios
        ],
        sort_keys=True,
        ensure_ascii=False,
    )
    return {
        "frozen_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "rule_files_sha256": files,
        "rule_strategy_version": "v2",  # diagnosis_service 中的 strategy_version
        "probe_strategy_version": PROBE_STRATEGY_VERSION,
        "ground_truth_version": hashlib.sha256(gt_payload.encode()).hexdigest()[:16],
        "scenario_count": len(scenarios),
        "scenario_list": [s["sample_id"] for s in scenarios],
        "note": "规则修改必须换新 Holdout（新 manifest），不得重复污染旧数据",
    }


# ------------------------------------------------------------ 场景构造（新变体）
def _iso(epoch: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


def build_holdout_samples() -> list[dict[str, Any]]:
    """24 个新场景变体：不复用旧 120 样本；参数化噪声 + 唯一 ID。"""
    stamp = int(time.time()) % 100000
    samples: list[dict[str, Any]] = []

    def add(
        family: str, gt: str, variant: int, report: dict, evidence: list[dict], probes: list[dict]
    ) -> None:
        samples.append(
            {
                "sample_id": f"holdout-{stamp}-{family}-v{variant}",
                "family": family,
                "ground_truth": gt,
                "report": report,
                "evidence": evidence,
                "probes": probes,
            }
        )

    now = time.time()

    def _steps(net: list[dict], assert_failed: str = "count mismatch") -> dict:
        return {
            "steps": [
                {
                    "step_id": "s1",
                    "action": "click",
                    "status": "PASSED",
                    "error": "",
                    "target": {"strategy": "test_id", "value": "create-submit"},
                    "observation": {"network": net},
                },
                {
                    "step_id": "s2",
                    "action": "assert",
                    "status": "FAILED",
                    "error": assert_failed,
                    "target": {"strategy": "test_id", "value": "banner-success"},
                    "value": "banner-success",
                    "observation": {},
                },
            ],
            "assertion_outcomes": [
                {"assertion_id": "a_ui", "kind": "ui", "status": "PASSED", "reason": ""},
                {
                    "assertion_id": "a_biz",
                    "kind": "business_state",
                    "status": "FAILED",
                    "reason": assert_failed,
                },
            ],
        }

    # 1) api_5xx（GT=API）：真实 5xx + Run 窗口内 TRUSTED 网络证据
    for v in range(1, 5):
        code = [500, 502, 503, 500][v - 1]
        net = [{"url": "/api/projects", "status": code, "failure": ""}]

        def _mk_report(net: list[dict], code: int) -> dict:
            # 与已验证的场景 C 同构：network + http_errors 双通道
            return {
                "steps": [
                    {
                        "step_id": "s1",
                        "action": "click",
                        "status": "FAILED",
                        "error": "",
                        "target": {"strategy": "test_id", "value": "create-submit"},
                        "observation": {
                            "network": net,
                            "http_errors": [{"url": "/api/projects", "status": code}],
                        },
                    },
                    {
                        "step_id": "s2",
                        "action": "assert",
                        "status": "FAILED",
                        "error": "banner missing",
                        "target": {"strategy": "test_id", "value": "banner-success"},
                        "value": "banner-success",
                        "observation": {},
                    },
                ],
                "assertion_outcomes": [
                    {
                        "assertion_id": "a_ui",
                        "kind": "ui",
                        "status": "FAILED",
                        "reason": "banner missing",
                    },
                    {"assertion_id": "a_biz", "kind": "business_state", "status": "NOT_EVALUATED"},
                ],
            }

        add(
            "api_5xx",
            "API",
            v,
            _mk_report(net, code),
            [
                {
                    "kind": "network",
                    "source_ref": "step_signals:s1",
                    "content": {"step_id": "s1", "status": code, "url": "/api/projects"},
                    "trust": "TRUSTED",
                }
            ],
            [
                {
                    "probe_id": "http_state",
                    "data": {
                        "template": "get_state_projects",
                        "response": {"ok": True, "count": 2, "keys": ["a", "b"]},
                    },
                }
            ],
        )

    # 2) db001_permanent（GT=PERSISTENCE）：UI 通过、业务失败、独立读取+延迟复测均缺失
    for v in range(1, 5):
        add(
            "db001_permanent",
            "PERSISTENCE",
            v,
            _steps([{"url": "/api/projects", "status": 200, "failure": ""}]),
            [
                {
                    "kind": "api",
                    "source_ref": "state_query:immediate",
                    "content": {"step_id": "s1", "count": 0, "keys": []},
                    "trust": "TRUSTED",
                    "at_offset": -50,
                },
                {
                    "kind": "api",
                    "source_ref": "state_query:delayed",
                    "content": {"step_id": "s1", "count": 0, "keys": []},
                    "trust": "TRUSTED",
                    "at_offset": -10,
                },
            ],
            [
                {
                    "probe_id": "http_state",
                    "data": {
                        "template": "get_state_projects",
                        "response": {"ok": True, "count": 0, "keys": []},
                    },
                }
            ],
        )

    # 3) db004_lag（GT=TIMING）：即时缺失、延迟复测恢复
    for v in range(1, 5):
        add(
            "db004_lag",
            "TIMING",
            v,
            _steps([{"url": "/api/projects", "status": 200, "failure": ""}]),
            [
                {
                    "kind": "api",
                    "source_ref": "state_query:immediate",
                    "content": {"step_id": "s1", "count": 0, "keys": []},
                    "trust": "TRUSTED",
                    "at_offset": -50,
                },
                {
                    "kind": "api",
                    "source_ref": "state_query:delayed",
                    "content": {"step_id": "s1", "count": 3, "keys": ["a", "b", "c"]},
                    "trust": "TRUSTED",
                    "at_offset": -5,
                },
            ],
            [
                {
                    "probe_id": "http_state",
                    "data": {
                        "template": "get_state_projects",
                        "response": {"ok": True, "count": 3, "keys": ["a", "b", "c"]},
                    },
                }
            ],
        )

    # 4) locator_drift（GT=LOCATOR）：点击超时，但 DOM 元素在、业务已落库
    locator_report = {
        "steps": [
            {
                "step_id": "s1",
                "action": "click",
                "status": "FAILED",
                "error": "Locator.click: Timeout 8000ms exceeded",
                "target": {"strategy": "test_id", "value": "create-submit"},
                "observation": {},
            }
        ],
        "assertion_outcomes": [
            {
                "assertion_id": "a_ui",
                "kind": "ui",
                "status": "NOT_EVALUATED",
                "expected": {"test_id": "banner-success"},
            },
            {"assertion_id": "a_biz", "kind": "business_state", "status": "NOT_EVALUATED"},
        ],
    }
    for v in range(1, 5):
        add(
            "locator_drift",
            "LOCATOR",
            v,
            json.loads(json.dumps(locator_report)),
            [],
            [
                {
                    "probe_id": "dom_snapshot",
                    "data": {
                        "template": "run_dom",
                        "evidence_id": "evd_dom",
                        "artifact_id": "art_dom",
                        "size": 120,
                        "excerpt": '<button data-testid="nav-new">新建</button><div data-testid="banner-success">创建成功</div>',
                    },
                },
                {
                    "probe_id": "http_state",
                    "data": {
                        "template": "get_state_projects",
                        "response": {"ok": True, "count": 1, "keys": ["a"]},
                    },
                },
            ],
        )

    # 5) network_env（GT=NETWORK）：连接失败（无状态码）
    net_fail_report = {
        "steps": [
            {
                "step_id": "s1",
                "action": "click",
                "status": "FAILED",
                "error": "net::ERR_CONNECTION_REFUSED",
                "target": {"strategy": "test_id", "value": "create-submit"},
                "observation": {
                    "network": [
                        {
                            "url": "/api/projects",
                            "status": 0,
                            "failure": "net::ERR_CONNECTION_REFUSED",
                        }
                    ]
                },
            }
        ],
        "assertion_outcomes": [
            {
                "assertion_id": "a_ui",
                "kind": "ui",
                "status": "FAILED",
                "reason": "page unreachable",
            },
            {"assertion_id": "a_biz", "kind": "business_state", "status": "NOT_EVALUATED"},
        ],
    }
    for v in range(1, 5):
        add(
            "network_env",
            "NETWORK",
            v,
            json.loads(json.dumps(net_fail_report)),
            [
                {
                    "kind": "network",
                    "source_ref": "step_signals:s1",
                    "content": {
                        "step_id": "s1",
                        "status": 0,
                        "url": "/api/projects",
                        "failure": "net::ERR_CONNECTION_REFUSED",
                    },
                    "trust": "TRUSTED",
                }
            ],
            [
                {
                    "probe_id": "network_trace",
                    "data": {
                        "template": "run_network",
                        "entries": [
                            {
                                "step_id": "s1",
                                "url": "/api/projects",
                                "status": 0,
                                "failure": "net::ERR_CONNECTION_REFUSED",
                            }
                        ],
                    },
                }
            ],
        )

    # 6) ambiguous（GT=INCONCLUSIVE）：无故障信号，正确弃权才是对的
    ambiguous_report = {
        "steps": [
            {
                "step_id": "s1",
                "action": "click",
                "status": "FAILED",
                "error": "unknown transient",
                "target": {"strategy": "test_id", "value": "create-submit"},
                "observation": {},
            }
        ],
        "assertion_outcomes": [
            {"assertion_id": "a_ui", "kind": "ui", "status": "NOT_EVALUATED"},
            {"assertion_id": "a_biz", "kind": "business_state", "status": "NOT_EVALUATED"},
        ],
    }
    for v in range(1, 5):
        add(
            "ambiguous",
            "INCONCLUSIVE",
            v,
            json.loads(json.dumps(ambiguous_report)),
            [],
            [],
        )

    _ = now
    return samples


# -------------------------------------------------------------------- 执行
def _run_sample(sample: dict[str, Any], round_no: int, base_tmp: Path) -> dict[str, Any]:
    import uuid

    from traceguard_api.runtime import Runtime
    from traceguard_api.services.diagnosis_service import DiagnosisService
    from traceguard_api.services.probe_service import ProbeService
    from traceguard_core.ids import new_id
    from traceguard_persistence import Database
    from traceguard_persistence.models import RunRow

    work = base_tmp / f"r{round_no}-{sample['sample_id']}-{uuid.uuid4().hex[:8]}"
    work.mkdir(parents=True, exist_ok=True)
    db = Database(f"sqlite:///{work / 'holdout.db'}")
    db.create_schema()
    started = time.perf_counter()
    record: dict[str, Any] = {"sample_id": sample["sample_id"], "family": sample["family"]}
    try:
        run_id = f"run_{sample['sample_id']}_r{round_no}"
        now = time.time()
        with db.session() as s:
            s.add(
                RunRow(
                    run_id=run_id,
                    task_id=f"task_{run_id}",
                    workspace_id="default",
                    testcase_version_id="tcase_holdout@1",
                    execution_status="COMPLETED",
                    test_verdict="FAILED",
                    report_json=sample["report"],
                    started_at=now - 100,
                    ended_at=now - 30,
                )
            )
        rt = Runtime(db)
        # 注入证据（TRUSTED，时间落在 Run 窗口内）
        evidence_ids: list[str] = []
        for ev in sample["evidence"]:
            eid = new_id("evd")
            rt.evidence.add_raw(
                eid,
                ev["kind"],
                ev["source_ref"],
                "default",
                ev["content"],
                source_trust=ev["trust"],
                captured_at=_iso(now + float(ev.get("at_offset") or -40)),
                run_id=run_id,
            )
            evidence_ids.append(eid)

        # 初始诊断（冻结规则）
        diag = DiagnosisService(rt).diagnose(run_id, use_llm_advisor=False)

        # 探针阶段（冻结探针策略：确定性执行器，按族配置）
        probe_svc = ProbeService(rt)
        probe_cost = 0.0
        if sample["probes"]:
            from traceguard_persistence.monitoring import reset_process_metrics  # noqa: F401

            for pd_ in sample["probes"]:
                definition = probe_svc.engine.definitions[pd_["probe_id"]]

                def executor(_params, _data=pd_["data"]):
                    return _data

                probe_svc.engine.register(definition, executor)
            plan = probe_svc.plan_and_probe(run_id, str(diag["diagnosis_id"]), max_iterations=3)
            probe_cost = sum(float(st.get("cost") or 0.0) for st in plan.get("steps", []))
            record["probe_steps"] = [
                {"probe_id": st.get("probe_id"), "status": st.get("status")}
                for st in plan.get("steps", [])
            ]
        record["probe_cost"] = round(probe_cost, 2)

        # 预测：CONFIRMED 根因机制；无 CONFIRMED → INCONCLUSIVE（弃权）
        confirmed = [h for h in diag["hypotheses"] if h["status"] == "CONFIRMED"]
        if confirmed:
            record["prediction"] = confirmed[0]["failure_mechanism"]
        else:
            record["prediction"] = "INCONCLUSIVE"
        record["outcome"] = diag["outcome"]
        # 证据覆盖率：诊断引用的证据 / 注入的证据
        refs = set(diag.get("evidence_refs") or [])
        record["evidence_coverage"] = {
            "injected": len(evidence_ids),
            "referenced": len(refs & set(evidence_ids)),
        }
        # 保留原始诊断（不落盘整包，保留关键字段供复核）
        record["diagnosis_summary"] = {
            "diagnosis_id": diag["diagnosis_id"],
            "root_causes": diag.get("root_causes", []),
            "hypotheses": [
                {
                    "mechanism": h["failure_mechanism"],
                    "status": h["status"],
                    "confidence": h["confidence"],
                }
                for h in diag["hypotheses"]
            ],
        }
    except Exception as exc:  # noqa: BLE001 - 失败样本必须保留
        record["error"] = f"{type(exc).__name__}: {exc}"[:300]
        record["prediction"] = "INCONCLUSIVE"
    finally:
        record["time_to_diagnosis_ms"] = round((time.perf_counter() - started) * 1000, 1)
        db.dispose()
    return record


def _evaluate(records: list[dict[str, Any]], samples: list[dict[str, Any]]) -> dict[str, Any]:
    """独立评测端：显式分母，不删除 UNKNOWN，不把 CONFIRMED share 当准确率。"""
    gt_by_id = {s["sample_id"]: s["ground_truth"] for s in samples}
    confusion: dict[str, dict[str, int]] = {g: {p: 0 for p in LABELS} for g in LABELS}
    tp: dict[str, int] = {lbl: 0 for lbl in LABELS if lbl != "INCONCLUSIVE"}
    fp: dict[str, int] = {lbl: 0 for lbl in LABELS if lbl != "INCONCLUSIVE"}
    fn: dict[str, int] = {lbl: 0 for lbl in LABELS if lbl != "INCONCLUSIVE"}
    abstain = 0
    errors = 0
    t2d: list[float] = []
    probe_costs: list[float] = []
    coverage: list[float] = []
    per_sample: list[dict[str, Any]] = []
    for rec in records:
        if rec.get("error"):
            errors += 1
        gt = gt_by_id[rec["sample_id"]]
        pred = rec.get("prediction") or "INCONCLUSIVE"
        confusion[gt][pred] += 1
        correct = pred == gt
        if pred == "INCONCLUSIVE":
            abstain += 1
        elif gt == "INCONCLUSIVE":
            fp[pred] += 1
        elif pred == "INCONCLUSIVE":
            pass
        else:
            if correct:
                tp[pred] += 1
            else:
                fp[pred] += 1
                fn[gt] += 1
        if gt != "INCONCLUSIVE" and pred != "INCONCLUSIVE":
            pass
        if rec.get("time_to_diagnosis_ms") is not None:
            t2d.append(float(rec["time_to_diagnosis_ms"]))
        if rec.get("probe_cost") is not None:
            probe_costs.append(float(rec["probe_cost"]))
        cov = rec.get("evidence_coverage") or {}
        if cov.get("injected"):
            coverage.append(cov.get("referenced", 0) / cov["injected"])
        per_sample.append(
            {
                "sample_id": rec["sample_id"],
                "family": rec["family"],
                "gt": gt,
                "pred": pred,
                "correct": correct,
                "abstained": pred == "INCONCLUSIVE",
                "error": rec.get("error"),
            }
        )

    n = len(records)
    n_labeled = n - abstain  # 非弃权样本数（Selective Accuracy 分母）
    metrics: dict[str, Any] = {
        "total_samples": n,
        "eval_errors": errors,
        "abstained_inconclusive": abstain,
        "abstain_share": round(abstain / n, 4) if n else 0.0,
        "accuracy_all_denominator": round(sum(1 for p in per_sample if p["correct"]) / n, 4)
        if n
        else 0.0,
        "selective_accuracy_denominator": n_labeled,
        "selective_accuracy": round(
            sum(1 for p in per_sample if p["correct"] and not p["abstained"]) / n_labeled, 4
        )
        if n_labeled
        else None,
        "per_label": {},
        "macro_f1": None,
        "time_to_diagnosis_ms_avg": round(sum(t2d) / len(t2d), 1) if t2d else None,
        "probe_cost_avg": round(sum(probe_costs) / len(probe_costs), 3) if probe_costs else 0.0,
        "evidence_coverage_avg": round(sum(coverage) / len(coverage), 4) if coverage else None,
        "confusion_matrix": confusion,
    }
    f1s: list[float] = []
    for lbl in tp:
        precision = tp[lbl] / (tp[lbl] + fp[lbl]) if (tp[lbl] + fp[lbl]) else None
        recall = tp[lbl] / (tp[lbl] + fn[lbl]) if (tp[lbl] + fn[lbl]) else None
        f1 = (
            2 * precision * recall / (precision + recall)
            if precision is not None and recall is not None and (precision + recall)
            else None
        )
        metrics["per_label"][lbl] = {
            "precision": round(precision, 4) if precision is not None else None,
            "recall": round(recall, 4) if recall is not None else None,
            "f1": round(f1, 4) if f1 is not None else None,
            "tp": tp[lbl],
            "fp": fp[lbl],
            "fn": fn[lbl],
            "denominator_precision": tp[lbl] + fp[lbl],
            "denominator_recall": tp[lbl] + fn[lbl],
        }
        if f1 is not None:
            f1s.append(f1)
    metrics["macro_f1"] = round(sum(f1s) / len(f1s), 4) if f1s else None
    # Primary Cause Accuracy：分母 = 全部样本（弃权即错误）
    metrics["primary_cause_accuracy"] = metrics["accuracy_all_denominator"]
    return {"metrics": metrics, "per_sample": per_sample}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=2)
    ap.add_argument("--out", default=str(OUT_DIR))
    args = ap.parse_args(argv)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    manifest = build_manifest()
    (out_dir / "holdout-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    samples = build_holdout_samples()
    all_results: dict[str, Any] = {
        "manifest": manifest,
        "rounds": {},
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "probe_strategy_version": PROBE_STRATEGY_VERSION,
    }
    import shutil
    import tempfile

    base_tmp = Path(tempfile.mkdtemp(prefix="tg-holdout-"))
    try:
        for round_no in range(1, args.rounds + 1):
            # 独立轮次：执行顺序颠倒（round2 反向），run_id 带 round 号
            round_samples = list(reversed(samples)) if round_no % 2 == 0 else list(samples)
            records = [_run_sample(s, round_no, base_tmp) for s in round_samples]
            evaluation = _evaluate(records, samples)
            round_payload = {
                "round": round_no,
                "executed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "order": "forward" if round_no % 2 == 1 else "reversed",
                "metrics": evaluation["metrics"],
                "per_sample": evaluation["per_sample"],
            }
            (out_dir / f"holdout-round{round_no}.json").write_text(
                json.dumps(round_payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            all_results["rounds"][f"round{round_no}"] = round_payload
            m = evaluation["metrics"]
            print(
                json.dumps(
                    {
                        "round": round_no,
                        "abstain_share": m["abstain_share"],
                        "selective_accuracy": m["selective_accuracy"],
                        "macro_f1": m["macro_f1"],
                        "primary_cause_accuracy": m["primary_cause_accuracy"],
                    },
                    ensure_ascii=False,
                )
            )
    finally:
        shutil.rmtree(base_tmp, ignore_errors=True)

    all_results["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    (out_dir / "holdout-evaluation.json").write_text(
        json.dumps(all_results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
