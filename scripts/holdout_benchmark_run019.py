#!/usr/bin/env python3
"""RUN-019 WP2：新独立诊断 Holdout（30 变体 × 2 轮 = 60 样本）。

与 RUN-018 Holdout 完全隔离：新样本、新 manifest、新规则 SHA-256、新 GT 版本。
不修改旧样本、不硬编码样本 ID 的根因、不降低 CONFIRMED 门槛。
每样本真实走：Run → Evidence → Diagnosis → Active Probe → Reassess → 图裁决 → Revision。
"""

from __future__ import annotations

import hashlib
import json
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys_path = [
    ROOT / "apps/api",
    ROOT / "packages/core",
    ROOT / "packages/contracts",
    ROOT / "packages/persistence",
    ROOT / "packages/evidence",
    ROOT / "packages/execution",
]
import sys  # noqa: E402

for p in sys_path:
    sys.path.insert(0, str(p))

OUT = ROOT / "docs/assets/run019/wp2"
RULE_FILES = [
    "apps/api/traceguard_api/services/diagnosis_service.py",
    "packages/evidence/traceguard_evidence/diagnosis/engine.py",
    "packages/evidence/traceguard_evidence/diagnosis/dedup.py",
    "packages/evidence/traceguard_evidence/probe/planner.py",
    "packages/evidence/traceguard_evidence/probe/feedback.py",
    "packages/evidence/traceguard_evidence/probe/contract.py",
    "packages/evidence/traceguard_evidence/graph/adjudication.py",
]
PROBE_STRATEGY_VERSION = "probe-strategy-v2-run019"
RULE_STRATEGY_VERSION = "v3-run019"


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).isoformat().replace("+00:00", "Z")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _report_biz_failed(entity: str) -> dict[str, Any]:
    return {
        "steps": [
            {
                "step_id": "s1",
                "action": "click",
                "status": "PASSED",
                "observation": {
                    "network": [
                        {"url": "/api/projects", "status": 200, "failure": "", "entity_id": entity}
                    ]
                },
            }
        ],
        "assertion_outcomes": [
            {
                "assertion_id": "a_biz",
                "kind": "business_state",
                "status": "FAILED",
                "reason": "count mismatch",
            }
        ],
    }


def _report_locator(entity: str) -> dict[str, Any]:
    return {
        "steps": [
            {
                "step_id": "s1",
                "action": "click",
                "status": "FAILED",
                "error": "Locator.click: Timeout 8000ms exceeded",
                "target": {"strategy": "test_id", "value": "create-submit"},
                "observation": {
                    "network": [
                        {"url": "/api/projects", "status": 200, "failure": "", "entity_id": entity}
                    ],
                    "user_action_succeeded": True,
                },
            }
        ],
        "assertion_outcomes": [
            {"assertion_id": "a_biz", "kind": "business_state", "status": "NOT_EVALUATED"}
        ],
    }


def build_samples() -> list[dict[str, Any]]:
    """30 个独立变体：6+6+6 目标类 + 4+4+4 控制组。"""
    samples: list[dict[str, Any]] = []
    stamp = int(time.time()) % 100000

    def add(
        family: str, gt: str, variant: int, report: dict, evidence: list[dict], probes: list[dict]
    ) -> None:
        samples.append(
            {
                "sample_id": f"run019-{stamp}-{family}-v{variant}",
                "family": family,
                "ground_truth": gt,
                "report": report,
                "evidence": evidence,
                "probes": probes,
            }
        )

    for v in range(1, 7):
        entity = f"e-persist-{v}"
        add(
            "persistence",
            "PERSISTENCE",
            v,
            _report_biz_failed(entity),
            [
                {
                    "kind": "api",
                    "source_ref": "state_query:immediate",
                    "content": {"entity_id": entity, "count": 0, "keys": []},
                    "trust": "TRUSTED",
                    "at_offset": -80,
                },
                {
                    "kind": "api",
                    "source_ref": "state_query:delayed",
                    "content": {"entity_id": entity, "count": 0, "keys": []},
                    "trust": "TRUSTED",
                    "at_offset": -45,
                },
                {
                    "kind": "api",
                    "source_ref": "state_query:final",
                    "content": {"entity_id": entity, "count": 0, "keys": []},
                    "trust": "TRUSTED",
                    "at_offset": -35,
                },
            ],
            [
                {
                    "probe_id": "http_state",
                    "data": {
                        "template": "get_state_projects",
                        "response": {"ok": True, "count": 0, "keys": [], "entity_id": entity},
                    },
                }
            ],
        )

    for v in range(1, 7):
        entity = f"e-timing-{v}"
        add(
            "timing",
            "TIMING",
            v,
            _report_biz_failed(entity),
            [
                {
                    "kind": "api",
                    "source_ref": "state_query:immediate",
                    "content": {"entity_id": entity, "count": 0, "keys": []},
                    "trust": "TRUSTED",
                    "at_offset": -80,
                },
                {
                    "kind": "api",
                    "source_ref": "state_query:delayed",
                    "content": {
                        "entity_id": entity,
                        "count": v,
                        "keys": [f"k{i}" for i in range(v)],
                    },
                    "trust": "TRUSTED",
                    "at_offset": -45,
                },
            ],
            [
                {
                    "probe_id": "http_state",
                    "data": {
                        "template": "get_state_projects",
                        "response": {
                            "ok": True,
                            "count": v,
                            "keys": [f"k{i}" for i in range(v)],
                            "entity_id": entity,
                        },
                    },
                }
            ],
        )

    for v in range(1, 7):
        entity = f"e-locator-{v}"
        add(
            "locator",
            "LOCATOR",
            v,
            _report_locator(entity),
            [
                {
                    "kind": "browser",
                    "source_ref": "dom_snapshot:s1",
                    "content": {
                        "entity_id": entity,
                        "matched": True,
                        "visible": True,
                        "role": "button",
                        "name": "新建",
                        "test_id": f"nav-new-{v}",
                    },
                    "trust": "TRUSTED",
                    "at_offset": -70,
                },
                {
                    "kind": "api",
                    "source_ref": "state_query:delayed",
                    "content": {"entity_id": entity, "count": 1, "keys": [entity]},
                    "trust": "TRUSTED",
                    "at_offset": -40,
                },
            ],
            [
                {
                    "probe_id": "dom_snapshot",
                    "data": {
                        "template": "run_dom",
                        "evidence_id": f"dom-{v}",
                        "artifact_id": f"art-{v}",
                        "size": 120,
                        "excerpt": f'<button data-testid="nav-new-{v}">新建</button>',
                    },
                },
                {
                    "probe_id": "http_state",
                    "data": {
                        "template": "get_state_projects",
                        "response": {"ok": True, "count": 1, "keys": [entity], "entity_id": entity},
                    },
                },
            ],
        )

    for v in range(1, 5):
        code = [500, 502, 503, 500][v - 1]
        add(
            "api_control",
            "API",
            v,
            {
                "steps": [
                    {
                        "step_id": "s1",
                        "action": "click",
                        "status": "FAILED",
                        "observation": {
                            "network": [{"url": "/api/projects", "status": code, "failure": ""}],
                            "http_errors": [{"url": "/api/projects", "status": code}],
                        },
                    }
                ],
                "assertion_outcomes": [
                    {"assertion_id": "a_biz", "kind": "business_state", "status": "NOT_EVALUATED"}
                ],
            },
            [
                {
                    "kind": "network",
                    "source_ref": "step_signals:s1",
                    "content": {"step_id": "s1", "status": code, "url": "/api/projects"},
                    "trust": "TRUSTED",
                    "at_offset": -60,
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

    for v in range(1, 5):
        add(
            "network_control",
            "NETWORK",
            v,
            {
                "steps": [
                    {
                        "step_id": "s1",
                        "action": "click",
                        "status": "FAILED",
                        "error": "net::ERR_CONNECTION_REFUSED",
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
                    {"assertion_id": "a_biz", "kind": "business_state", "status": "NOT_EVALUATED"}
                ],
            },
            [
                {
                    "kind": "network",
                    "source_ref": "step_signals:s1",
                    "content": {
                        "step_id": "s1",
                        "status": 0,
                        "failure": "net::ERR_CONNECTION_REFUSED",
                    },
                    "trust": "TRUSTED",
                    "at_offset": -60,
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

    for v in range(1, 5):
        add(
            "ambiguous_control",
            "INCONCLUSIVE",
            v,
            {
                "steps": [
                    {
                        "step_id": "s1",
                        "action": "click",
                        "status": "FAILED",
                        "error": "unknown transient",
                        "observation": {},
                    }
                ],
                "assertion_outcomes": [
                    {"assertion_id": "a_biz", "kind": "business_state", "status": "NOT_EVALUATED"}
                ],
            },
            [],
            [],
        )
    return samples


def _run_sample(sample: dict[str, Any], round_no: int) -> dict[str, Any]:
    from traceguard_api.runtime import Runtime
    from traceguard_api.services.diagnosis_service import DiagnosisService
    from traceguard_api.services.probe_service import ProbeService
    from traceguard_core.ids import new_id
    from traceguard_evidence.probe.feedback import ProbeSignal
    from traceguard_persistence import Database
    from traceguard_persistence.models import RunRow

    # 样本隔离目录放在系统临时区：不污染证据目录，也不删除历史证据。
    work = Path(tempfile.mkdtemp(prefix=f"run019-holdout-r{round_no}-"))
    db = Database(f"sqlite:///{work / 'holdout.db'}")
    db.create_schema()
    started = time.perf_counter()
    record: dict[str, Any] = {
        "sample_id": sample["sample_id"],
        "family": sample["family"],
        "ground_truth": sample["ground_truth"],
    }
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
        injected: list[str] = []
        for ev in sample["evidence"]:
            eid = new_id("evd")
            rt.evidence.add_raw(
                eid,
                ev["kind"],
                ev["source_ref"],
                "default",
                ev["content"],
                source_trust=ev["trust"],
                captured_at=_iso(now + ev["at_offset"]),
                run_id=run_id,
            )
            injected.append(eid)

        svc = DiagnosisService(rt)
        diag = svc.diagnose(run_id, use_llm_advisor=False)
        probe_svc = ProbeService(rt)
        signals: list[Any] = []
        probe_cost = 0.0
        for pd_ in sample["probes"]:
            # 冻结的确定性探针策略：只替换执行器，契约/预算/证据落盘仍走真实 ProbeService。
            definition = probe_svc.engine.definitions[pd_["probe_id"]]
            probe_svc.engine.register(definition, lambda _params, _d=pd_["data"]: _d)
            params = dict(pd_["data"])
            if "template" not in params and definition.allowed_templates:
                params["template"] = sorted(definition.allowed_templates)[0]
            execution = probe_svc.execute(
                pd_["probe_id"],
                {**params, "run_id": run_id},
                "default",
                run_id=run_id,
                diagnosis_id=diag["diagnosis_id"],
            )
            if execution.get("probe_status") != "SUCCEEDED":
                continue
            probe_cost += float(execution.get("cost") or 0.0)
            signals.append(
                ProbeSignal(
                    probe_id=pd_["probe_id"],
                    execution_id=execution["execution_id"],
                    status="SUCCEEDED",
                    evidence_ids=list(execution.get("evidence_refs") or []),
                    data=execution.get("result") or {},
                    observed_at=execution.get("observed_at") or time.time(),
                    run_scope=False,
                )
            )
        final = (
            svc.reassess(diag["diagnosis_id"], signals, run_report=sample["report"])["diagnosis"]
            if signals
            else diag
        )
        confirmed = [h for h in final["hypotheses"] if h["status"] == "CONFIRMED"]
        record["prediction"] = confirmed[0]["failure_mechanism"] if confirmed else "INCONCLUSIVE"
        record["outcome"] = final["outcome"]
        record["revisions"] = len(svc.list_revisions(diag["diagnosis_id"]))
        record["probe_cost"] = round(probe_cost, 4)
        referenced = set(final.get("evidence_refs") or [])
        record["evidence_coverage"] = {
            "injected": len(injected),
            "referenced": len(referenced & set(injected)),
        }
    except Exception as exc:  # noqa: BLE001 - 失败样本必须保留
        record["error"] = f"{type(exc).__name__}: {exc}"[:300]
        record["prediction"] = "INCONCLUSIVE"
    finally:
        record["time_to_diagnosis_ms"] = round((time.perf_counter() - started) * 1000, 1)
        db.dispose()
    return record


def _metrics(records: list[dict[str, Any]], samples: list[dict[str, Any]]) -> dict[str, Any]:
    gt = {s["sample_id"]: s["ground_truth"] for s in samples}
    labels = sorted(set(gt.values()))
    confusion = {a: {b: 0 for b in labels} for a in labels}
    tp = {label: 0 for label in labels}
    fp = {label: 0 for label in labels}
    fn = {label: 0 for label in labels}
    correct = abstained = 0
    for r in records:
        truth, pred = gt[r["sample_id"]], r["prediction"]
        confusion.setdefault(truth, {}).setdefault(pred, 0)
        confusion[truth][pred] += 1
        if pred == "INCONCLUSIVE":
            abstained += 1
        elif pred == truth:
            correct += 1
            tp[pred] += 1
        else:
            fp[pred] += 1
            fn[truth] += 1
    per_class = {}
    for label in labels:
        precision = tp[label] / (tp[label] + fp[label]) if tp[label] + fp[label] else 0.0
        recall = tp[label] / (tp[label] + fn[label]) if tp[label] + fn[label] else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        per_class[label] = {
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
            "support": tp[label] + fn[label],
        }
    answered = len(records) - abstained
    return {
        "sample_count": len(records),
        "abstention_rate": round(abstained / len(records), 4),
        "selective_accuracy": round(correct / answered, 4) if answered else 0.0,
        "selective_accuracy_denominator": answered,
        "primary_cause_accuracy": round(correct / len(records), 4),
        "per_class": per_class,
        "confusion_matrix": confusion,
        "macro_f1": round(sum(v["f1"] for v in per_class.values()) / len(per_class), 4),
        "mean_time_to_diagnosis_ms": round(
            sum(r["time_to_diagnosis_ms"] for r in records) / len(records), 2
        ),
        "total_probe_cost": round(sum(r.get("probe_cost", 0.0) for r in records), 4),
        "evidence_coverage": {
            "injected": sum(r.get("evidence_coverage", {}).get("injected", 0) for r in records),
            "referenced": sum(r.get("evidence_coverage", {}).get("referenced", 0) for r in records),
        },
        "errors": [r for r in records if r.get("error")],
    }


def main() -> int:
    import subprocess

    OUT.mkdir(parents=True, exist_ok=True)
    samples = build_samples()
    manifest = {
        "holdout_version": "run019-holdout-v1",
        "created_at": datetime.now(UTC).isoformat(),
        "rule_strategy_version": RULE_STRATEGY_VERSION,
        "probe_strategy_version": PROBE_STRATEGY_VERSION,
        "rule_files_sha256": {p: _sha(ROOT / p) for p in RULE_FILES},
        "ground_truth_version": hashlib.sha256(
            json.dumps(
                [{"sample_id": s["sample_id"], "ground_truth": s["ground_truth"]} for s in samples],
                sort_keys=True,
                ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest()[:16],
        "isolation": "RUN-018 原始 Holdout 不变；本样本集与规则清单均为新版本",
        "sample_count": len(samples),
        "variants": [
            {"sample_id": s["sample_id"], "family": s["family"], "ground_truth": s["ground_truth"]}
            for s in samples
        ],
    }
    (OUT / "holdout-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (OUT / "holdout-samples.json").write_text(
        json.dumps(samples, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    all_records: list[dict[str, Any]] = []
    for round_no in (1, 2):
        ordered = samples if round_no == 1 else list(reversed(samples))
        records = [_run_sample(s, round_no) for s in ordered]
        (OUT / f"holdout-round{round_no}.json").write_text(
            json.dumps(
                {"round": round_no, "records": records, "metrics": _metrics(records, samples)},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        all_records.extend(records)
    evaluation = {
        "head": subprocess.check_output(
            ["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True
        ).strip(),
        "manifest": "holdout-manifest.json",
        "rounds": 2,
        "combined": _metrics(all_records, samples),
        "records": all_records,
    }
    (OUT / "holdout-evaluation.json").write_text(
        json.dumps(evaluation, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(evaluation["combined"], ensure_ascii=False, indent=2))
    return 0 if not evaluation["combined"]["errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
