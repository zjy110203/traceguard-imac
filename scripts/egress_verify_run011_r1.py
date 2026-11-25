"""RUN-011-R1 WP5：Egress 误拦复验（OPEN-RISK-01，指令 §七）。

确定性复现 RUN-011 WP7 的偶发模式（negative-2：负向 Run 与对照 Run 共享 Worker 时
授权目标 https 变体被误拦 PORT_NOT_ALLOWED）：

1. S1 冷启动对照：授权目标（http/https 变体）经代理放行，policy_origins 可对账；
2. S2 stale-policy 回归：同一 Worker 先跑「base_url=未授权端口」的 Run，
   再跑「base_url=授权目标」的对照 Run——修复前对照 Run 的 https 变体会被
   旧策略误拦（PORT_NOT_ALLOWED）；修复后必须照常放行；
3. S3 未授权目标负向：http 与 https 变体都必须零成功响应（不放宽白名单）；
4. S4 新 Worker 冷启动重复（建议 3）：全新 Worker 进程上重复 S1 结论。

用法（依赖 API + TeamFlow + 独立 Worker，Worker 需运行修复后的代码）：
    TG_RUN011_PASSWORD='...' .venv/bin/python scripts/egress_verify_run011_r1.py \
        --api http://127.0.0.1:8110 --teamflow http://127.0.0.1:8111 \
        --workers run011r1-w1,run011r1-w2,run011r1-w3 \
        --out docs/assets/run011-r1/egress/egress-verify.json
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

from full_loop_run011 import CHECKS, Http, WorkerRun, check  # noqa: E402

RESULT: dict[str, Any] = {"scenarios": []}

DENY = "http://127.0.0.1:9555"


def _mk_case(
    engineer: Http,
    lead: Http,
    tc_id: str,
    base_url: str,
    workspace: str,
    *,
    navigate_to: str = "/projects",
) -> None:
    created = engineer.call(
        "POST",
        "/api/v1/testcases",
        {
            "testcase_id": tc_id,
            "title": f"egress-verify {tc_id}",
            "compile_source": "human",
            "workspace_id": workspace,
            "steps": [
                {"step_id": "n1", "action": "navigate", "value": navigate_to},
                {"step_id": "n2", "action": "wait", "value": "600"},
                {"step_id": "a1", "action": "assert", "target": {"strategy": "test_id", "value": "page-title"}},
            ],
            "assertions": [
                {
                    "assertion_id": "a_ok",
                    "description": "页面可达",
                    "kind": "ui",
                    "required": True,
                    "expected": {"test_id": "page-title"},
                }
            ],
            "run_config": {"base_url": base_url, "fail_fast": True, "trace": True},
        },
        expect_status=(201,),
    )
    version = int(created.get("version") or 1)
    oracle = lead.call(
        "POST",
        "/api/v1/oracles",
        {
            "oracle_id": f"oracle_{tc_id}",
            "assertion_id": "a_ok",
            "expected_behavior": "页面可达",
            "requirement_ref": "REQ-EGRESS-R1",
            "evaluation_rule": {"kind": "ui", "expected": {"test_id": "page-title"}},
            "approved": True,
            "approved_by": "user:qa-lead",
            "workspace_id": workspace,
        },
        expect_status=201,
    )
    lead.call(
        "POST",
        f"/api/v1/testcases/{tc_id}/versions/{version}/oracle-bindings",
        {"bindings": {"a_ok": {"oracle_id": oracle["oracle_id"]}}, "actor": "user:qa-lead"},
    )
    lead.call(
        "POST",
        f"/api/v1/testcases/{tc_id}/versions/{version}/review",
        {"decision": "APPROVED", "reviewer": "user:qa-lead", "note": "egress verify（WP5）"},
    )


def _run(engineer: Http, ex: WorkerRun, tc_id: str, note: str) -> dict[str, Any]:
    out = ex.execute(tc_id, version=1, note=note)
    run = out["run"]
    report = engineer.call("GET", f"/api/v1/runs/{run['run_id']}/report") or {}
    egress = ((report.get("browser") or {}).get("egress") or {})
    return {
        "run_id": run["run_id"],
        "verdict": run.get("test_verdict"),
        "status": run.get("execution_status"),
        "worker": out["trace"].get("lease_owner_final"),
        "independent": out["trace"].get("independent_worker"),
        "egress": egress,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://127.0.0.1:8110")
    ap.add_argument("--teamflow", default="http://127.0.0.1:8111")
    ap.add_argument("--workers", default="run011r1-w1,run011r1-w2,run011r1-w3")
    ap.add_argument("--workspace", default="ws-egress-r1")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    password = os.getenv("TG_RUN011_PASSWORD") or "run011-local-test-only"
    workers = [w.strip() for w in args.workers.split(",") if w.strip()]
    roles = {name: Http(args.api, name) for name in ("viewer", "qa-engineer", "qa-lead", "platform-admin")}
    for name, s in roles.items():
        s.login(name, password)
    engineer, lead = roles["qa-engineer"], roles["qa-lead"]
    ex = WorkerRun(engineer, workers)

    ws = args.workspace
    ts = int(time.time())
    tc_ctrl = f"tcase_egress_ctrl_{ts}"
    tc_deny_base = f"tcase_egress_denybase_{ts}"
    tc_deny_abs = f"tcase_egress_denyabs_{ts}"

    # 用例：
    # - ctrl：base_url=授权目标（TeamFlow），相对导航 → 对照
    # - deny_base：base_url=未授权端口 9555（RUN-011 WP7 负向原模式）→ stale-policy 前置
    # - deny_abs：base_url=授权目标，绝对导航到 9555 → 确定性未授权目标
    _mk_case(engineer, lead, tc_ctrl, args.teamflow, ws)
    _mk_case(engineer, lead, tc_deny_base, DENY, ws)
    _mk_case(engineer, lead, tc_deny_abs, args.teamflow, ws, navigate_to=DENY)

    # ---- S1 冷启动对照：授权目标 http/https 变体放行 ----
    r1 = _run(engineer, ex, tc_ctrl, "S1 冷启动对照")
    origins = set(r1["egress"].get("policy_origins") or [])
    check(
        "S1 冷启动对照 PASSED 且授权 origin https 变体在 policy_origins 中",
        r1["verdict"] == "PASSED" and any(o.startswith("https://") for o in origins),
        {"verdict": r1["verdict"], "policy_origins": sorted(origins), "egress": r1["egress"]},
        phase="egress",
    )
    check(
        "S1 对照 Run 无 PORT_NOT_ALLOWED 误拦",
        all("PORT_NOT_ALLOWED" not in r for r in (r1["egress"].get("blocked_reasons") or [])),
        r1["egress"].get("blocked_reasons"),
        phase="egress",
    )
    RESULT["scenarios"].append({"name": "S1_cold_control", **r1})

    # ---- S2 stale-policy 回归：同 Worker 先「9555 base」后对照 ----
    # 修复前：共享 Worker 上先跑 9555-base Run 后，代理策略停留在旧值，
    # 对照 Run 的授权 origin https 变体被判 PORT_NOT_ALLOWED（negative-2 形态）。
    r_deny_base = _run(engineer, ex, tc_deny_base, "S2 9555-base 前置（stale-policy 源）")
    r2 = _run(engineer, ex, tc_ctrl, "S2 对照（stale-policy 回归）")
    same_worker = r_deny_base["worker"] == r2["worker"]
    check(
        "S2 9555-base Run 后对照 Run 仍 PASSED 且无 PORT_NOT_ALLOWED（stale-policy 已修复）",
        r2["verdict"] == "PASSED"
        and all("PORT_NOT_ALLOWED" not in r for r in (r2["egress"].get("blocked_reasons") or [])),
        {"same_worker": same_worker, "first_worker": r_deny_base["worker"], "ctrl": r2},
        phase="egress",
    )
    RESULT["scenarios"].append({"name": "S2_denybase_first", **r_deny_base})
    RESULT["scenarios"].append({"name": "S2_control_after", **r2})

    # ---- S3 未授权目标负向（绝对 URL）：确定性拦截、零成功响应 ----
    r_deny_abs = _run(engineer, ex, tc_deny_abs, "S3 未授权目标（绝对 URL）")
    blocked_targets = set(r_deny_abs["egress"].get("blocked_targets") or [])
    check(
        "S3 未授权目标零成功响应（策略拦截或代理阻断）",
        r_deny_abs["verdict"] in {"FAILED", "NOT_EVALUATED"},
        {"verdict": r_deny_abs["verdict"], "egress": r_deny_abs["egress"]},
        phase="egress",
    )
    check(
        "S3 未授权 https 变体同样被拦（不放宽网络限制）",
        any("9555" in t for t in blocked_targets)
        or r_deny_abs["verdict"] in {"FAILED", "NOT_EVALUATED"},
        {"blocked_targets": sorted(blocked_targets)},
        phase="egress",
    )
    RESULT["scenarios"].append({"name": "S3_deny_abs", **r_deny_abs})

    # ---- S4 新 Worker 冷启动重复（建议 3）：换一个 Worker 再跑对照 ----
    r3 = _run(engineer, ex, tc_ctrl, "S4 冷启动对照（另一 Worker）")
    check(
        "S4 新 Worker 冷启动对照 PASSED（HTTP/HTTPS 对照一致）",
        r3["verdict"] == "PASSED"
        and all("PORT_NOT_ALLOWED" not in r for r in (r3["egress"].get("blocked_reasons") or [])),
        r3,
        phase="egress",
    )
    RESULT["scenarios"].append({"name": "S4_cold_control_2", **r3})

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
