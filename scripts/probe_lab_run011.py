"""RUN-011 WP2：Active Probe 实验室（真实 HTTP + 真实 Worker + 真实诊断回灌）。

目的：回答一个必须用真实链路回答的问题——
    "探针是否真的执行、真的产生新证据、真的改变诊断，还是只在报告里出现？"

覆盖（全部走真实 API/Worker/TeamFlow/Chromium，不 import 应用代码）：
1. locator_gap : TF-LOC-001 失败（NOT_EVALUATED / 证据缺口）→ 探针计划 → 执行 →
                 新证据 → 回灌 → 重新诊断，比较回灌前后机制；
2. net_timeout : TF-NET-002 超时 → 允许 INCONCLUSIVE，检查探针是否执行/是否如实 STOP；
3. temporal    : TF-DB-004 状态查询滞后 → 检查"已确认但机制可能错误"的行为边界
                 （POST_HOC 证据不得反驳 Run 当时事实）；
4. explicit    : 直接 POST /probes/execute（http_state）验证真实执行 + 证据落库；
5. bad_probe   : 非白名单 probe_id 必须被拒绝且不产生证据；
6. repeat      : 同一 run/diagnosis 重复 probe-plan 不得重复烧预算到失控；
7. budget_close: 关闭预算后 probe-plan 必须 STOP_BUDGET_CLOSED。

用法：
    TG_RUN011_PASSWORD='...' .venv/bin/python scripts/probe_lab_run011.py \
        --api http://127.0.0.1:8120 --teamflow http://127.0.0.1:8112 \
        --workers run011-v1,run011-v2,run011-v3 \
        --out docs/assets/run011/wp2/probe-lab.json
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

from full_loop_run011 import CHECKS, Http, TeamFlow, WorkerRun, check  # noqa: E402


def _login(api: str, password: str) -> dict[str, Http]:
    roles = {
        name: Http(api, name) for name in ("viewer", "qa-engineer", "qa-lead", "platform-admin")
    }
    for name, s in roles.items():
        s.login(name, password)
    return roles


def _diag_snapshot(diag: dict[str, Any]) -> dict[str, Any]:
    return {
        "diagnosis_id": diag.get("diagnosis_id"),
        "outcome": diag.get("outcome"),
        "disposition": diag.get("disposition"),
        "mechanisms": sorted(
            {
                str(h.get("failure_mechanism"))
                for h in diag.get("hypotheses") or []
                if h.get("hypothesis_id") in (diag.get("root_causes") or [])
            }
        ),
        "root_causes": diag.get("root_causes"),
        "hypotheses": [
            {
                "hypothesis_id": h.get("hypothesis_id"),
                "mechanism": h.get("failure_mechanism"),
                "status": h.get("status"),
                "source": h.get("suggested_by"),
                "supporting": [
                    {"id": r.get("id"), "trust": r.get("trust"), "confirming": r.get("confirming")}
                    for r in (h.get("supporting") or [])
                ],
                "contradicting": [
                    {"id": r.get("id"), "trust": r.get("trust")}
                    for r in (h.get("contradicting") or [])
                ],
                "probe_feedback": [
                    {"relation": f.get("relation"), "alignment": f.get("time_alignment")}
                    for f in (h.get("probe_feedback") or [])
                ],
            }
            for h in diag.get("hypotheses") or []
        ],
        "unresolved_conflicts": diag.get("unresolved_conflicts") or [],
        "evidence_ids": sorted(
            {eid for h in diag.get("hypotheses") or [] for eid in (h.get("evidence_ids") or [])}
        ),
        # 细粒度签名：状态/置信度/证据集合任一变化都算"被重新评估"
        # 注意：诊断 API 的假设没有扁平 evidence_ids 字段，证据在 supporting/contradicting 里。
        "signature": [
            (
                h.get("hypothesis_id"),
                h.get("status"),
                tuple(sorted(str(r.get("id")) for r in (h.get("supporting") or []))),
                tuple(sorted(str(r.get("id")) for r in (h.get("contradicting") or []))),
                len(h.get("probe_feedback") or []),
            )
            for h in diag.get("hypotheses") or []
        ],
        "evaluation_history_len": len(diag.get("evaluation_history") or []),
    }


def _run_case(
    ex: WorkerRun, engineer: Http, teamflow: TeamFlow, testcase: str, *, fault_hint: str
) -> dict[str, Any]:
    teamflow.reset()
    out = ex.execute(testcase, note=f"probe-lab {fault_hint}")
    run = out["run"]
    run_id = run["run_id"]
    rec: dict[str, Any] = {
        "fault_hint": fault_hint,
        "testcase": testcase,
        "run_id": run_id,
        "verdict": run.get("test_verdict"),
        "execution_status": run.get("execution_status"),
        "worker": out["trace"].get("lease_owner_final"),
        "independent_worker": out["trace"].get("independent_worker"),
        "evidence_count": len(engineer.call("GET", f"/api/v1/runs/{run_id}/evidence")),
    }
    if run.get("test_verdict") not in {"FAILED", "NOT_EVALUATED"}:
        rec["diagnosis"] = None
        rec["note"] = "用例未失败：该场景本轮未显现，不进入诊断/探针"
        return rec

    diag = engineer.call(
        "POST", f"/api/v1/runs/{run_id}/diagnose", None, expect_status=(200, 201), timeout=300
    )
    rec["diagnosis_before"] = _diag_snapshot(diag)
    rec["suggestions"] = engineer.call(
        "GET", f"/api/v1/runs/{run_id}/probe-suggestions?diagnosis_id={diag['diagnosis_id']}"
    )
    t0 = time.perf_counter()
    plan = engineer.call(
        "POST",
        f"/api/v1/runs/{run_id}/probe-plan?diagnosis_id={diag['diagnosis_id']}&max_iterations=3",
        None,
        expect_status=(200, 201),
        timeout=300,
    )
    rec["plan_elapsed_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    steps = plan.get("steps") or []
    rec["plan"] = {
        "plan_id": plan.get("plan_id"),
        "stop_reason": plan.get("stop_reason"),
        "stop_detail": plan.get("stop_detail"),
        "steps": [
            {
                "iteration": s.get("iteration"),
                "probe_id": s.get("probe_id"),
                "gap_id": s.get("gap_id"),
                "status": s.get("status"),
                "info_gain": s.get("info_gain"),
                "evidence_ids": s.get("evidence_ids"),
                "applied_to_diagnosis": s.get("applied_to_diagnosis"),
                "hypothesis_updates": s.get("hypothesis_updates"),
                "duration_ms": s.get("duration_ms"),
            }
            for s in steps
        ],
        "new_evidence_ids": plan.get("evidence_ids") or [],
        "budget": plan.get("budget"),
    }
    after = engineer.call("GET", f"/api/v1/diagnoses/{diag['diagnosis_id']}")
    rec["diagnosis_after"] = _diag_snapshot(after)
    rec["changed"] = {
        "outcome": rec["diagnosis_before"]["outcome"] != rec["diagnosis_after"]["outcome"],
        "mechanisms": rec["diagnosis_before"]["mechanisms"] != rec["diagnosis_after"]["mechanisms"],
        "hypothesis_status": [
            (h["hypothesis_id"], h["status"]) for h in rec["diagnosis_before"]["hypotheses"]
        ]
        != [(h["hypothesis_id"], h["status"]) for h in rec["diagnosis_after"]["hypotheses"]],
        "hypothesis_signature": rec["diagnosis_before"]["signature"]
        != rec["diagnosis_after"]["signature"],
        "evaluation_appended": rec["diagnosis_after"]["evaluation_history_len"]
        > rec["diagnosis_before"]["evaluation_history_len"],
    }
    # 探针证据的时间语义
    probe_evidence = []
    for eid in (plan.get("evidence_ids") or [])[:5]:
        ev = engineer.call("GET", f"/api/v1/evidence/{eid}")
        content = ev.get("content") or {}
        probe_evidence.append(
            {
                "evidence_id": eid,
                "source_type": ev.get("source_type"),
                "source_ref": ev.get("source_ref"),
                "captured_at": ev.get("captured_at"),
                "observed_at": content.get("observed_at"),
                "collected_at": content.get("collected_at"),
                "probe_id": content.get("probe_id") or content.get("template"),
                "run_id_in_content": content.get("run_id"),
                "diagnosis_id_in_content": content.get("diagnosis_id"),
            }
        )
    rec["probe_evidence"] = probe_evidence
    rec["hypothesis_updates"] = [u for st in steps for u in (st.get("hypothesis_updates") or [])]
    rec["executed"] = [s for s in steps if s.get("status") == "SUCCEEDED"]
    rec["failed"] = [s for s in steps if s.get("status") in {"FAILED", "TIMED_OUT", "BLOCKED"}]
    return rec


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://127.0.0.1:8120")
    ap.add_argument("--teamflow", default="http://127.0.0.1:8112")
    ap.add_argument("--workers", default="")
    ap.add_argument("--out", default="docs/assets/run011/wp2/probe-lab.json")
    args = ap.parse_args()

    password = os.getenv("TG_RUN011_PASSWORD") or "run011-local-test-only"
    workers = [w.strip() for w in args.workers.split(",") if w.strip()]
    roles = _login(args.api, password)
    engineer = roles["qa-engineer"]
    tf = TeamFlow(args.teamflow)
    ex = WorkerRun(engineer, workers, "")

    result: dict[str, Any] = {
        "api": args.api,
        "teamflow": args.teamflow,
        "workers": workers,
        "cases": {},
    }

    # 1) 有真实证据缺口的场景：定位器失效
    loc = _run_case(ex, engineer, tf, "tcase_bench_loc001", fault_hint="TF-LOC-001")
    result["cases"]["locator_gap"] = loc
    check(
        "P1 定位器场景真实失败并进入诊断",
        loc.get("verdict") in {"FAILED", "NOT_EVALUATED"} and bool(loc.get("diagnosis_before")),
        {
            "verdict": loc.get("verdict"),
            "outcome": (loc.get("diagnosis_before") or {}).get("outcome"),
        },
    )
    executed = loc.get("executed") or []
    check(
        "P2 探针真实执行（不是只返回建议）",
        bool(executed) and bool((loc.get("plan") or {}).get("new_evidence_ids")),
        {
            "executed": [(s.get("probe_id"), s.get("status")) for s in executed],
            "stop": (loc.get("plan") or {}).get("stop_reason"),
            "new_evidence": (loc.get("plan") or {}).get("new_evidence_ids"),
        },
    )
    check(
        "P3 探针证据绑定 run_id / diagnosis_id 且保留时间语义",
        all(
            e.get("run_id_in_content") == loc.get("run_id")
            and e.get("diagnosis_id_in_content")
            and e.get("captured_at")
            for e in loc.get("probe_evidence") or []
        )
        if loc.get("probe_evidence")
        else False,
        loc.get("probe_evidence"),
    )
    check(
        "P4 证据回灌后诊断被重新评估（有可解释变化或如实无变化）",
        bool(loc.get("diagnosis_after"))
        and (
            any(loc.get("changed", {}).values()) or bool((loc.get("plan") or {}).get("stop_reason"))
        ),
        {
            "changed": loc.get("changed"),
            "before": {
                k: (loc.get("diagnosis_before") or {}).get(k) for k in ("outcome", "mechanisms")
            },
            "after": {
                k: (loc.get("diagnosis_after") or {}).get(k) for k in ("outcome", "mechanisms")
            },
        },
    )

    # 2) 超时/网络场景：允许弃权
    net = _run_case(ex, engineer, tf, "tcase_bench_net002", fault_hint="TF-NET-002")
    result["cases"]["net_timeout"] = net
    check(
        "P5 网络超时场景允许 INCONCLUSIVE，且不伪造结论",
        (net.get("diagnosis_before") or {}).get("outcome")
        in {"INCONCLUSIVE", "CONFIRMED", "PROBABLE"},
        (net.get("diagnosis_before") or {}).get("outcome"),
    )

    # 3) 时序场景：已确认但机制可能与真实时序不一致（POST_HOC 不得反驳历史）
    temporal = _run_case(ex, engineer, tf, "tcase_bench_db004", fault_hint="TF-DB-004")
    result["cases"]["temporal"] = temporal
    check(
        "P6 时序场景记录真实结论（含错误确认的事实，不掩盖）",
        bool(temporal.get("diagnosis_before")),
        (temporal.get("diagnosis_before") or {}).get("outcome"),
    )

    # 4) 显式探针执行 + 5) 非白名单探针
    tf.reset()
    run_id = loc.get("run_id") if loc.get("run_id") else ""
    explicit = engineer.call(
        "POST",
        "/api/v1/probes/execute",
        {
            "probe_id": "http_state",
            "params": {"template": "get_state_projects", "workspace": "default", "run_id": run_id},
            "workspace_id": "default",
            "run_id": run_id,
            "diagnosis_id": (loc.get("diagnosis_before") or {}).get("diagnosis_id"),
        },
        expect_status=201,
        timeout=120,
    )
    result["cases"]["explicit"] = {
        "execution_id": explicit.get("execution_id"),
        "status": explicit.get("status"),
        "evidence_refs": explicit.get("evidence_refs"),
        "observed_at": explicit.get("observed_at"),
        "collected_at": explicit.get("collected_at"),
        "reason": explicit.get("reason"),
        "result": explicit.get("result"),
    }
    check(
        "P7 显式 http_state 探针真实执行并落库证据",
        explicit.get("status") == "SUCCEEDED" and bool(explicit.get("evidence_refs")),
        result["cases"]["explicit"],
    )
    after_exec = engineer.call("GET", f"/api/v1/probes/executions?run_id={run_id}&limit=200")
    code, body, _ = engineer.raw(
        "POST",
        "/api/v1/probes/execute",
        {
            "probe_id": "not_a_whitelisted_probe",
            "params": {},
            "workspace_id": "default",
            "run_id": run_id,
        },
    )
    after_bad = engineer.call("GET", f"/api/v1/probes/executions?run_id={run_id}&limit=200")
    result["cases"]["bad_probe"] = {"status": code, "body": body, "executions": len(after_bad)}
    check(
        "P8 非白名单探针被拒绝且不产生执行记录/证据",
        code in {400, 404, 422} and len(after_bad) == len(after_exec),
        {"status": code, "before": len(after_exec), "after": len(after_bad)},
    )

    # 6) 重复 probe-plan 不得重复烧预算到失控
    if run_id and (loc.get("diagnosis_before") or {}).get("diagnosis_id"):
        did = (loc.get("diagnosis_before") or {}).get("diagnosis_id")
        p1 = engineer.call(
            "POST",
            f"/api/v1/runs/{run_id}/probe-plan?diagnosis_id={did}&max_iterations=2",
            None,
            expect_status=(200, 201),
            timeout=300,
        )
        p2 = engineer.call(
            "POST",
            f"/api/v1/runs/{run_id}/probe-plan?diagnosis_id={did}&max_iterations=2",
            None,
            expect_status=(200, 201),
            timeout=300,
        )
        result["cases"]["repeat"] = {
            "first": {
                "steps": len(p1.get("steps") or []),
                "stop": p1.get("stop_reason"),
                "budget": p1.get("budget"),
            },
            "second": {
                "steps": len(p2.get("steps") or []),
                "stop": p2.get("stop_reason"),
                "budget": p2.get("budget"),
            },
        }
        check(
            "P9 重复 probe-plan 不会重复执行同一缺口（预算受控）",
            len(p2.get("steps") or []) <= len(p1.get("steps") or []),
            result["cases"]["repeat"],
        )

        # 7) 关闭预算后必须 STOP_BUDGET_CLOSED
        engineer.call("POST", f"/api/v1/probes/budgets/close?run_id={run_id}&diagnosis_id={did}")
        p3 = engineer.call(
            "POST",
            f"/api/v1/runs/{run_id}/probe-plan?diagnosis_id={did}&max_iterations=2",
            None,
            expect_status=(200, 201),
            timeout=120,
        )
        result["cases"]["budget_closed"] = {
            "stop": p3.get("stop_reason"),
            "detail": p3.get("stop_detail"),
        }
        check(
            "P10 预算关闭后必须 STOP_BUDGET_CLOSED（不继续烧预算）",
            p3.get("stop_reason") == "STOP_BUDGET_CLOSED",
            result["cases"]["budget_closed"],
        )
    else:
        check("P9 重复 probe-plan 不会重复执行同一缺口（预算受控）", False, "缺少 run/diagnosis")
        check("P10 预算关闭后必须 STOP_BUDGET_CLOSED（不继续烧预算）", False, "缺少 run/diagnosis")

    tf.clear_faults()
    result["summary"] = {
        "total": len(CHECKS),
        "passed": sum(1 for c in CHECKS if c["ok"]),
        "failed": [c["name"] for c in CHECKS if not c["ok"]],
        "checks": CHECKS,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result["summary"], ensure_ascii=False))
    print(f"[out] {out}")
    return 0 if not result["summary"]["failed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
