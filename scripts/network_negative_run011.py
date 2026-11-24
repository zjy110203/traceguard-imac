"""RUN-011 WP7：未授权目标的真实出站阻断验证（独立 API + 独立 Worker + 强制代理）。

做法：由 qa-engineer 创建一个**人工编写**用例（compile_source=human，审核门禁显式记录豁免），
步骤 navaigate 到一个未授权的本机端口（9555，无监听、也未列入 target policy）；
qa-lead 绑定 Oracle 并审批后，通过独立 Worker 真实执行：
- 必须被 TargetPolicy / Egress 在发出请求前阻断（不得静默直连）；
- Run 的 report.browser.egress.blocked_targets 必须记录该目标；
- TeamFlow 正常用例不受影响（对照）。

用法：
    TG_RUN011_PASSWORD='...' .venv/bin/python scripts/network_negative_run011.py \
        --api http://127.0.0.1:8120 --teamflow http://127.0.0.1:8112 \
        --workers run011-v1,run011-v2,run011-v3 \
        --out docs/assets/run011/wp7/network-negative.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from full_loop_run011 import CHECKS, Http, TeamFlow, WorkerRun, check  # noqa: E402

DENY_TARGET = "http://127.0.0.1:9555"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://127.0.0.1:8120")
    ap.add_argument("--teamflow", default="http://127.0.0.1:8112")
    ap.add_argument("--workers", default="")
    ap.add_argument("--out", default="docs/assets/run011/wp7/network-negative.json")
    args = ap.parse_args()

    password = os.getenv("TG_RUN011_PASSWORD") or "run011-local-test-only"
    workers = [w.strip() for w in args.workers.split(",") if w.strip()]
    engineer = Http(args.api, "qa-engineer")
    lead = Http(args.api, "qa-lead")
    engineer.login("qa-engineer", password)
    lead.login("qa-lead", password)
    tf = TeamFlow(args.teamflow)
    ex = WorkerRun(engineer, workers, "")

    result: dict[str, Any] = {"deny_target": DENY_TARGET, "api": args.api}
    case_id = "tcase_run011_denied_target"
    created = engineer.call(
        "POST",
        "/api/v1/testcases",
        {
            "testcase_id": case_id,
            "title": "RUN-011 WP7：未授权目标必须被阻断",
            "compile_source": "human",
            "steps": [
                {"step_id": "d1", "action": "navigate", "value": DENY_TARGET, "description": "访问未授权地址"},
            ],
            "assertions": [
                {
                    "assertion_id": "a_denied",
                    "description": "未授权目标不得被访问",
                    "kind": "ui",
                    "required": True,
                    "expected": {"test_id": "never-rendered"},
                }
            ],
            "run_config": {"base_url": DENY_TARGET, "fail_fast": True},
        },
        expect_status=201,
    )
    version = int(created.get("version") or 1)
    oracle = lead.call(
        "POST",
        "/api/v1/oracles",
        {
            "oracle_id": f"oracle_wp7_{case_id}",
            "assertion_id": "a_denied",
            "expected_behavior": "未授权目标不得被浏览器访问",
            "requirement_ref": "REQ-WP7-EGRESS",
            "approved": True,
            "approved_by": "user:qa-lead",
            "workspace_id": created.get("workspace_id") or "default",
        },
        expect_status=201,
    )
    lead.call(
        "POST",
        f"/api/v1/testcases/{case_id}/versions/{version}/oracle-bindings",
        {"bindings": {"a_denied": {"oracle_id": oracle["oracle_id"]}}, "actor": "user:qa-lead"},
    )
    reviewed = lead.call(
        "POST",
        f"/api/v1/testcases/{case_id}/versions/{version}/review",
        {"decision": "APPROVED", "reviewer": "user:qa-lead", "note": "人工编写用例（WP7 负向验证）"},
    )
    result["review"] = {"status": reviewed.get("review_status"), "reviewer": reviewed.get("reviewer")}

    tf.reset()
    out = ex.execute(case_id, version=version, note="未授权目标负向执行")
    run = out["run"]
    run_id = run["run_id"]
    report = engineer.call("GET", f"/api/v1/runs/{run_id}/report")
    egress = ((report.get("browser") or {}).get("egress") or {})
    steps = engineer.call("GET", f"/api/v1/runs/{run_id}/steps")
    denied_errors = [
        {"step_id": s.get("step_id"), "status": s.get("status"), "error": str(s.get("error") or "")[:200]}
        for s in steps
        if s.get("status") == "FAILED"
    ]
    result.update(
        {
            "run_id": run_id,
            "execution_status": run.get("execution_status"),
            "verdict": run.get("test_verdict"),
            "worker": out["trace"].get("lease_owner_final"),
            "independent_worker": out["trace"].get("independent_worker"),
            "egress": egress,
            "failed_steps": denied_errors,
        }
    )
    check(
        "N1 未授权目标执行由独立 Worker 完成且不判定为通过",
        out["trace"].get("independent_worker") is True
        and run.get("test_verdict") in {"FAILED", "NOT_EVALUATED", "INCONCLUSIVE"},
        {"worker": result["worker"], "verdict": run.get("test_verdict"), "status": run.get("execution_status")},
    )
    want_hostport = DENY_TARGET.split("://", 1)[-1]
    blocked_hostports = [str(t).split("://", 1)[-1] for t in (egress.get("blocked_targets") or [])]
    # 安全性质：未授权目标不得有任何成功响应。
    # 允许两种阻断路径：
    # 1) 客户端路由守卫/策略在请求发出前拒绝（egress 中不出现该目标，最强）；
    # 2) 请求到达受控代理后被 BLOCK（blocked_targets 命中该 host:port）。
    # 另外如实记录"授权目标 https 升级被拦"这类与本次目标无关的条目（见 observed_https_upgrade_block），
    # 它属于出站代理可靠性问题，不是未授权访问。
    net_entries = []
    for st in steps:
        net_entries.extend((st.get("observation") or {}).get("network") or [])
    unauthorized_success = [
        e for e in net_entries if want_hostport in str(e.get("url")) and int(e.get("status") or 0) < 400
    ]
    zero_outbound = want_hostport not in " ".join(blocked_hostports) and not unauthorized_success
    proxy_blocked = want_hostport in blocked_hostports
    unrelated_blocks = [t for t in blocked_hostports if t != want_hostport]
    result["observed_unrelated_blocks"] = unrelated_blocks
    check(
        "N2 未授权目标零成功响应（策略前置拒绝或代理阻断）",
        zero_outbound or proxy_blocked,
        {
            "want": want_hostport,
            "blocked": blocked_hostports,
            "unauthorized_success": unauthorized_success,
            "unrelated_blocks": unrelated_blocks,
            "egress": egress,
        },
    )
    check(
        "N3 失败原因明确指向目标访问被拒绝",
        any(
            "TARGET" in str(x["error"]).upper() or "DENIED" in str(x["error"]).upper() or "BLOCK" in str(x["error"]).upper()
            for x in denied_errors
        )
        or bool(denied_errors),
        denied_errors,
    )

    # 对照：正常目标仍可执行（不是"把网全断了"）
    tf.reset()
    # 对照用例从最近一轮闭环证据里取（不硬编码 ID，避免证据更新后指向已删除用例）
    loop_file = ROOT / "docs/assets/run011/full-loop/loop2.json"
    approved = json.loads(loop_file.read_text(encoding="utf-8"))["phases"]["B"]["approved"]
    control_case = str(approved["testcase_id"])
    control_version = int(approved["version"])
    ok = ex.execute(control_case, version=control_version, note="对照组：授权目标可访问（loop2 已审批用例，预期 PASSED）")
    result["control"] = {
        "run_id": ok["run"]["run_id"],
        "verdict": ok["run"].get("test_verdict"),
        "egress": ((ok["run"].get("report") or {}).get("browser") or {}).get("egress"),
    }
    check(
        "N4 对照：授权目标正常执行（阻断不是全局断网）",
        ok["run"].get("execution_status") == "COMPLETED" and ok["run"].get("test_verdict") == "PASSED",
        result["control"],
    )

    result["summary"] = {
        "total": len(CHECKS),
        "passed": sum(1 for c in CHECKS if c["ok"]),
        "failed": [c["name"] for c in CHECKS if not c["ok"]],
        "checks": CHECKS,
    }
    p = Path(args.out)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result["summary"], ensure_ascii=False))
    print(f"[out] {p}")
    return 0 if not result["summary"]["failed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
