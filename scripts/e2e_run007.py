"""RUN-007 WP2：真实端到端业务闭环（场景 A/B/C/D）。

全部通过 HTTP API 驱动，底层是真实 SQLite + 真实 Chromium + 真实 Worker/执行内核；
不使用 Mock、不直接改数据库状态。

场景：
A 正常业务：Intent → Explorer → Compile → Oracle 绑定 → 稳定性回放 → 人工审核门禁
            → 正式执行 → PASSED
B 真实产品故障：受控故障 → Trusted Oracle 揭穿假成功 → FAILED → 诊断 → Disposition
C 证据不足/冲突：真实 Run 的证据 → 真实只读探针 → 证据回灌 → 重新诊断 → 更新证据图
D 完整自愈：Repair Proposal → Diff 校验 → 沙箱真实执行 → 人工审批 → CAS 晋升
            → 全量回归 → 回滚 → 复核正式版本

用法：
    python scripts/e2e_run007.py --api http://127.0.0.1:8010 \
        --out docs/assets/run007/e2e-run007.json
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

STEPS: list[dict[str, Any]] = []


class Api:
    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/") + "/api/v1"

    def call(self, method: str, path: str, body: dict | None = None, timeout: int = 300) -> Any:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            self.base + path,
            data=data,
            method=method,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
                raw = resp.read().decode()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")
            raise RuntimeError(f"{method} {path} -> {exc.code}: {detail[:600]}") from exc


def step(name: str, fn, *, critical: bool = True) -> Any:
    t = time.perf_counter()
    try:
        result = fn()
        ms = int((time.perf_counter() - t) * 1000)
        STEPS.append({"step": name, "ok": True, "ms": ms, "at": time.time()})
        print(f"  [ok]   {name} ({ms}ms)")
        return result
    except Exception as exc:  # noqa: BLE001
        ms = int((time.perf_counter() - t) * 1000)
        STEPS.append({"step": name, "ok": False, "ms": ms, "at": time.time(), "error": str(exc)})
        print(f"  [FAIL] {name}: {exc}")
        if critical:
            raise
        return None


def scenario_a(api: Api, teamflow: str) -> dict[str, Any]:
    print("\n== 场景 A：正常业务闭环 ==")
    intent = step(
        "A1 创建 Intent",
        lambda: api.call(
            "POST",
            "/intents",
            {
                "title": "RUN-007 正常业务闭环",
                "description": (
                    f"在 TeamFlow 打开 {teamflow}/projects/new，"
                    "只填 data-testid=project-name 为 RUN007，"
                    "data-testid=project-description 为 自动化验收，"
                    "然后点击 data-testid=create-submit；"
                    "成功后页面跳转到 /projects?created=1 并出现 data-testid=banner-success"
                ),
                "target_url": teamflow,
            },
        ),
    )
    intent_id = intent["intent_id"]
    step(
        "A2 Intent 状态推进",
        lambda: api.call("POST", f"/intents/{intent_id}/status", {"status": "APPROVED"}),
    )

    exploration = step(
        "A3 Agent 真实探索",
        lambda: api.call("POST", f"/intents/{intent_id}/explore?max_steps=6", timeout=300),
    )
    exploration_id = exploration.get("exploration_id") or exploration.get("id")

    compiled = step(
        "A4 编译候选用例",
        lambda: api.call("POST", f"/explorations/{exploration_id}/compile", {}),
    )
    candidate = compiled.get("compiled") or compiled.get("testcase") or compiled
    testcase_id = candidate.get("testcase_id")
    version = int(candidate.get("version") or 1)
    version_doc = api.call("GET", f"/testcases/{testcase_id}/versions/{version}")
    assertion_ids = [a["assertion_id"] for a in version_doc.get("assertions") or []]

    oracle_ids: dict[str, str] = {}
    for aid in assertion_ids:
        created = step(
            f"A5 创建并审批 Oracle（{aid}）",
            lambda aid=aid: api.call(
                "POST",
                "/oracles",
                {
                    "oracle_id": f"oracle_e2e_{testcase_id}_{aid}".replace("-", "_")[:80],
                    "assertion_id": aid,
                    "expected_behavior": "创建成功后必须出现成功提示（banner-success）",
                    "requirement_ref": "REQ-E2E-007-A",
                    "approved": True,
                    "approved_by": "role:qa-lead",
                },
            ),
        )
        oracle_ids[aid] = created["oracle_id"]

    step(
        "A6 绑定 Oracle（固定版本）",
        lambda: api.call(
            "POST",
            f"/testcases/{testcase_id}/versions/{version}/oracle-bindings",
            {
                "bindings": {aid: {"oracle_id": oid} for aid, oid in oracle_ids.items()},
                "actor": "role:qa-lead",
            },
        ),
    )

    gate_before = step(
        "A7 审核门禁应拒绝（无稳定性回放）",
        lambda: _expect_error(
            api,
            "POST",
            f"/testcases/{testcase_id}/versions/{version}/review",
            {"decision": "APPROVED", "reviewer": "role:qa-lead"},
            expected=412,
        ),
    )

    replay = step(
        "A8 稳定性回放（3 次真实 Run）",
        lambda: api.call(
            "POST",
            f"/testcases/{testcase_id}/versions/{version}/stability-replay"
            "?repeats=3&sync=true&risk_profile=default",
            {},
            timeout=600,
        ),
    )
    step(
        "A9 回放结论检查",
        lambda: _assert(
            replay.get("status") == "COMPLETED"
            and (replay.get("classification") or {}).get("classification") == "STABLE_PASS"
            and replay.get("promotable") is True,
            f"replay={replay.get('status')} class={(replay.get('classification') or {}).get('classification')}",
        ),
    )

    reviewed = step(
        "A10 人工审核通过（消费回放结论）",
        lambda: api.call(
            "POST",
            f"/testcases/{testcase_id}/versions/{version}/review",
            {"decision": "APPROVED", "reviewer": "role:qa-lead", "note": "RUN-007 E2E"},
        ),
    )
    step(
        "A11 审核留痕含回放证据",
        lambda: _assert_review_evidence(api, testcase_id, version, replay["replay_id"]),
    )

    execution = step(
        "A12 正式执行（真实浏览器）",
        lambda: api.call(
            "POST",
            f"/testcases/{testcase_id}/execute?version={version}",
            {"sync": True},
            timeout=300,
        ),
    )
    run = execution.get("execution") or execution
    step(
        "A13 结果必须 PASSED",
        lambda: _assert(run.get("test_verdict") == "PASSED", f"verdict={run.get('test_verdict')}"),
    )

    return {
        "intent_id": intent_id,
        "exploration_id": exploration_id,
        "testcase_id": testcase_id,
        "version": version,
        "oracle_ids": oracle_ids,
        "gate_rejected_as_expected": gate_before,
        "replay_id": replay["replay_id"],
        "replay_classification": (replay.get("classification") or {}).get("classification"),
        "review_revision": reviewed.get("review_revision"),
        "run_id": run.get("run_id"),
        "verdict": run.get("test_verdict"),
    }


def scenario_b(api: Api) -> dict[str, Any]:
    print("\n== 场景 B：真实产品故障被 Trusted Oracle 揭穿 ==")
    case_id = "tcase_tf_false_pass"
    execution = step(
        f"B1 真实执行受控故障用例 {case_id}",
        lambda: api.call("POST", f"/testcases/{case_id}/execute", {"sync": True}, timeout=300),
    )
    run = execution.get("execution") or execution
    run_id = run.get("run_id")
    step(
        "B2 假成功必须被判 FAILED",
        lambda: _assert(run.get("test_verdict") == "FAILED", f"verdict={run.get('test_verdict')}"),
    )

    diag = step("B3 诊断", lambda: api.call("POST", f"/runs/{run_id}/diagnose", timeout=300))
    graph = step("B4 生成并查询证据图", lambda: api.call("POST", f"/runs/{run_id}/graph", {}))
    nodes = graph.get("nodes") or []
    edges = graph.get("edges") or []
    step(
        "B5 证据图必须有真实证据节点与来源",
        lambda: _assert(
            any(n.get("node_type") == "Evidence" for n in nodes)
            and len(edges) > 0
            and any(n.get("node_type") == "Source" for n in nodes),
            f"nodes={len(nodes)} edges={len(edges)}",
        ),
    )
    dispositions = api.call("GET", "/dispositions")
    disposition = next(
        (d for d in dispositions if d.get("diagnosis_id") == diag.get("diagnosis_id")), None
    )
    return {
        "run_id": run_id,
        "verdict": run.get("test_verdict"),
        "diagnosis_id": diag.get("diagnosis_id"),
        "outcome": diag.get("outcome"),
        "disposition": (disposition or {}).get("disposition") or diag.get("disposition"),
        "graph_nodes": len(nodes),
        "graph_edges": len(edges),
        "graph_node_types": sorted({str(n.get("node_type")) for n in nodes}),
        "evidence_ids": (run.get("evidence_ids") or [])[:10],
    }


def scenario_c(api: Api, failed_run_id: str, diagnosis_id: str) -> dict[str, Any]:
    print("\n== 场景 C：证据不足 → 真实探针回灌 → 重新诊断 ==")
    suggestions = step(
        "C1 读取探针建议（只读探针）",
        lambda: api.call("GET", f"/runs/{failed_run_id}/probe-suggestions"),
    )
    plan = step(
        "C2 执行 Active Diagnosis 探针闭环",
        lambda: api.call(
            "POST", f"/runs/{failed_run_id}/probe-plan?max_iterations=3", {}, timeout=300
        ),
    )
    executed = [s for s in plan.get("steps") or [] if s.get("status") == "SUCCEEDED"]
    step(
        "C3 必须有真实成功执行的探针与新证据",
        lambda: _assert(
            bool(executed) and bool(plan.get("evidence_ids")),
            f"executed={len(executed)} evidence={len(plan.get('evidence_ids') or [])}",
        ),
    )
    after = step("C4 重新读取诊断结论", lambda: api.call("GET", f"/diagnoses/{diagnosis_id}"))
    graph_after = api.call("GET", f"/runs/{failed_run_id}/graph")
    return {
        "suggestions": len(suggestions or []),
        "probe_steps": [(s.get("probe_id"), s.get("status")) for s in plan.get("steps") or []],
        "stop_reason": plan.get("stop_reason"),
        "stop_detail": plan.get("stop_detail"),
        "new_evidence_ids": plan.get("evidence_ids") or [],
        "hypotheses_after": [
            {"id": h.get("hypothesis_id"), "status": h.get("status")}
            for h in after.get("hypotheses") or []
        ],
        "diagnosis_status_after": after.get("status"),
        "outcome_after": after.get("outcome"),
        "graph_after_nodes": len((graph_after or {}).get("nodes") or []),
        "graph_after_edges": len((graph_after or {}).get("edges") or []),
        "budget": plan.get("budget"),
    }


def _setup_locator_case(api: Api, teamflow: str) -> tuple[str, int]:
    """创建场景 D 专用用例：第一步激活 TF-LOC-001（随用例自带，不受全局 pre-reset 清掉）。"""
    testcase = api.call(
        "POST",
        "/testcases",
        {
            "title": "RUN-007 定位器自愈场景",
            "compile_source": "human",
            "steps": [
                {
                    "step_id": "loc_s1",
                    "action": "api",
                    "value": "POST /api/faults/TF-LOC-001/activate",
                },
                {"step_id": "loc_s2", "action": "navigate", "value": "/projects/new"},
                {
                    "step_id": "loc_s3",
                    "action": "fill",
                    "target": {"strategy": "test_id", "value": "project-name"},
                    "value": "RUN007 自愈",
                },
                {
                    "step_id": "loc_s4",
                    "action": "fill",
                    "target": {"strategy": "test_id", "value": "project-description"},
                    "value": "定位器自愈场景",
                },
                {
                    "step_id": "loc_s5",
                    "action": "click",
                    "target": {"strategy": "test_id", "value": "create-submit"},
                },
                {"step_id": "loc_s6", "action": "wait", "value": "1500"},
                {
                    "step_id": "loc_s7",
                    "action": "assert",
                    "target": {"strategy": "test_id", "value": "banner-success"},
                    "value": "banner-success",
                },
                {
                    "step_id": "loc_s8",
                    "action": "assert",
                    "target": {"strategy": "test_id", "value": "project-rows"},
                    "value": "project-rows",
                },
            ],
            "run_config": {"base_url": teamflow, "fail_fast": False, "trace": True},
            "assertions": [
                {
                    "assertion_id": "a_ui_banner",
                    "description": "成功提示出现",
                    "kind": "ui",
                    "required": True,
                    "expected": {"test_id": "banner-success"},
                },
                {
                    "assertion_id": "a_row_render",
                    "description": "项目列表渲染",
                    "kind": "ui",
                    "required": True,
                    "expected": {"test_id": "project-rows", "op": "count_gte", "value": 1},
                },
            ],
        },
    )
    testcase_id = testcase["testcase_id"]
    version = int(testcase["version"])
    for aid, expected_text, req in (
        ("a_ui_banner", "成功提示必须出现", "REQ-E2E-LOC-UI"),
        ("a_row_render", "项目列表必须渲染", "REQ-E2E-LOC-ROW"),
    ):
        oracle = api.call(
            "POST",
            "/oracles",
            {
                "oracle_id": f"oracle_loc_{aid}".replace("-", "_"),
                "assertion_id": aid,
                "expected_behavior": expected_text,
                "requirement_ref": req,
                "approved": True,
                "approved_by": "role:qa-lead",
            },
        )
        api.call(
            "POST",
            f"/testcases/{testcase_id}/versions/{version}/oracle-bindings",
            {"bindings": {aid: {"oracle_id": oracle["oracle_id"]}}, "actor": "role:qa-lead"},
        )
    api.call(
        "POST",
        f"/testcases/{testcase_id}/versions/{version}/review",
        {"decision": "APPROVED", "reviewer": "role:qa-lead", "note": "场景 D 专用用例"},
    )
    return testcase_id, version


def scenario_d(api: Api, teamflow: str) -> dict[str, Any]:
    """真实自愈：用例自带 TF-LOC-001 故障激活（提交按钮 test_id 变更 → create-submit-v2），
    基线真实失败后用"角色定位器"修复，沙箱与回归都在故障生效状态下执行，
    最后回滚并复核修复版本可回退。"""
    print(
        "\n== 场景 D：完整自愈（真实定位器故障 → 修复 → 沙箱 → 审批 → CAS 晋升 → 回归 → 回滚） =="
    )
    base_case, base_version = step(
        "D0 创建场景 D 用例（自带故障激活步骤）", lambda: _setup_locator_case(api, teamflow)
    )

    base_run = api.call(
        "POST",
        f"/testcases/{base_case}/execute?version={base_version}",
        {"sync": True},
        timeout=300,
    )
    base_exec = base_run.get("execution") or base_run
    # 说明：定位器故障属于"步骤执行中断"，按状态机规范 Run=FAILED / verdict=NOT_EVALUATED
    # （断言级"假成功被识破 → FAILED"由场景 B 覆盖）。两类都是真实失败。
    step(
        "D1 基线版本在故障下必须真实失败（步骤中断→NOT_EVALUATED 或 断言失败→FAILED）",
        lambda: _assert(
            base_exec.get("test_verdict") in {"FAILED", "NOT_EVALUATED"},
            f"verdict={base_exec.get('test_verdict')} status={base_exec.get('execution_status')}",
        ),
    )

    proposal = step(
        "D2 生成修复提案（test_id → role 定位器）",
        lambda: api.call(
            "POST",
            "/repairs",
            {
                "testcase_id": base_case,
                "base_version": base_version,
                "changes": [
                    {
                        "path": "steps[].target",
                        "step_index": 4,
                        "value": {"strategy": "role", "value": "button:创建", "source": "healed"},
                        "reason": "TF-LOC-001：提交按钮 test_id 变更，改用可访问名称定位",
                    }
                ],
                "reason": "真实定位器故障驱动的最小修复",
            },
        ),
    )
    pid = proposal["proposal_id"]
    validated = step(
        "D3 Diff / 断言保护 / Oracle 完整性校验",
        lambda: api.call("POST", f"/repairs/{pid}/validate"),
    )
    step(
        "D4 校验必须通过",
        lambda: _assert(
            validated.get("status") == "VALIDATED"
            and validated.get("assertion_preservation") == "PASS"
            and validated.get("oracle_integrity") == "PASS",
            f"status={validated.get('status')}",
        ),
    )
    sandbox = step(
        "D5 沙箱真实执行（故障仍生效）",
        lambda: api.call("POST", f"/repairs/{pid}/sandbox-run", timeout=300),
    )
    step(
        "D6 沙箱必须 PASSED（证明修复真实有效）",
        lambda: _assert(
            sandbox.get("sandbox_status") == "PASSED", f"{sandbox.get('sandbox_status')}"
        ),
    )
    step("D7 提交审批", lambda: api.call("POST", f"/repairs/{pid}/submit"))
    step(
        "D8 Agent 审批必须被拒",
        lambda: _expect_error(
            api,
            "POST",
            f"/repairs/{pid}/approve",
            {"reviewer": "agent:auto-heal", "note": "x"},
            expected=403,
        ),
    )
    approved = step(
        "D9 人工审批",
        lambda: api.call(
            "POST", f"/repairs/{pid}/approve", {"reviewer": "role:qa-lead", "note": "E2E"}
        ),
    )
    promoted = step(
        "D10 CAS 晋升",
        lambda: api.call("POST", f"/repairs/{pid}/promote", {"idempotency_key": f"e2e-{pid}"}),
    )
    step(
        "D11 晋升结果",
        lambda: _assert(promoted.get("status") == "PROMOTED", f"{promoted.get('status')}"),
    )
    replay = step(
        "D12 幂等重放（同键）",
        lambda: api.call("POST", f"/repairs/{pid}/promote", {"idempotency_key": f"e2e-{pid}"}),
    )
    step(
        "D13 重放必须返回同一版本",
        lambda: _assert(
            replay.get("promoted_version") == promoted.get("promoted_version"),
            f"{replay.get('promoted_version')} != {promoted.get('promoted_version')}",
        ),
    )
    promoted_version = promoted["promoted_version"]
    regression = step(
        "D14 全量回归（故障仍生效，修复版本应通过）",
        lambda: api.call(
            "POST",
            f"/testcases/{base_case}/execute?version={promoted_version}",
            {"sync": True},
            timeout=300,
        ),
    )
    reg = regression.get("execution") or regression
    step(
        "D15 回归必须 PASSED",
        lambda: _assert(reg.get("test_verdict") == "PASSED", f"{reg.get('test_verdict')}"),
    )
    rolled = step(
        "D16 回滚到基线版本",
        lambda: api.call(
            "POST",
            f"/repairs/{pid}/rollback",
            {"operator": "role:qa-lead", "reason": "E2E 结束回滚"},
        ),
    )
    restored = (rolled.get("rollback_record") or {}).get("restored_version")
    after_rollback = api.call("GET", f"/testcases/{base_case}")
    step(
        "D17 回滚后正式版本应回到基线",
        lambda: _assert(
            rolled.get("status") == "ROLLED_BACK"
            and int(after_rollback.get("latest_version") or 0) == int(restored or 0),
            f"latest={after_rollback.get('latest_version')} restored={restored}",
        ),
    )
    verify = api.call(
        "POST",
        f"/testcases/{base_case}/execute?version={promoted_version}",
        {"sync": True},
        timeout=300,
    )
    ver = verify.get("execution") or verify
    step(
        "D18 回滚后复核：晋升版本仍可通过正式执行入口执行",
        lambda: _assert(ver.get("test_verdict") == "PASSED", f"{ver.get('test_verdict')}"),
    )
    cleared = step(
        "D19 清除 TeamFlow 故障",
        lambda: _teamflow_call(teamflow, "POST", "/api/test/reset", {}),
    )
    return {
        "case_id": base_case,
        "base_version": base_version,
        "baseline_run_id": base_exec.get("run_id"),
        "baseline_verdict": base_exec.get("test_verdict"),
        "baseline_execution_status": base_exec.get("execution_status"),
        "baseline_note": "定位器故障=步骤执行中断，状态机输出 NOT_EVALUATED（符合规范）",
        "proposal_id": pid,
        "validated_status": validated.get("status"),
        "assertion_preservation": validated.get("assertion_preservation"),
        "oracle_integrity": validated.get("oracle_integrity"),
        "sandbox_run_id": sandbox.get("sandbox_run_id"),
        "sandbox_verdict": sandbox.get("sandbox_verdict"),
        "approved_by": approved.get("approved_by"),
        "promoted_version": promoted_version,
        "idempotent_replay_version": replay.get("promoted_version"),
        "regression_run_id": reg.get("run_id"),
        "regression_verdict": reg.get("test_verdict"),
        "rolled_back_status": rolled.get("status"),
        "restored_version": restored,
        "latest_after_rollback": after_rollback.get("latest_version"),
        "post_rollback_run_id": ver.get("run_id"),
        "post_rollback_verdict": ver.get("test_verdict"),
        "fault_cleared": cleared,
    }


# ------------------------------------------------------------------ helpers
def _teamflow_call(base: str, method: str, path: str, body: dict | None = None) -> Any:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        base.rstrip("/") + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310
        raw = resp.read().decode()
        return json.loads(raw) if raw else {}


def _expect_error(api: Api, method: str, path: str, body: dict, *, expected: int) -> dict[str, Any]:
    try:
        api.call(method, path, body)
    except RuntimeError as exc:
        msg = str(exc)
        if f"-> {expected}:" in msg:
            return {"rejected": True, "status": expected, "detail": msg[:300]}
        raise AssertionError(f"期望 {expected}，实际：{msg[:300]}") from exc
    raise AssertionError(f"期望被拒绝（{expected}），但请求成功了")


def _assert(cond: bool, detail: str = "") -> dict[str, Any]:
    if not cond:
        raise AssertionError(detail or "assertion failed")
    return {"ok": True, "detail": detail}


def _assert_review_evidence(
    api: Api, testcase_id: str, version: int, replay_id: str
) -> dict[str, Any]:
    doc = api.call("GET", f"/testcases/{testcase_id}/versions/{version}")
    history = doc.get("review_history") or []
    last = history[-1] if history else {}
    evidence = last.get("stability_evidence") or {}
    _assert(last.get("decision") == "APPROVED", f"decision={last.get('decision')}")
    _assert(last.get("stability_policy") == "ENFORCED", f"policy={last.get('stability_policy')}")
    _assert(
        evidence.get("replay_id") == replay_id, f"evidence.replay_id={evidence.get('replay_id')}"
    )
    return {"review_history": history, "evidence": evidence}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://127.0.0.1:8010")
    ap.add_argument("--out", default="docs/assets/run007/e2e-run007.json")
    ap.add_argument("--scenarios", default="ABCD")
    ap.add_argument("--teamflow", default="http://127.0.0.1:8021")
    args = ap.parse_args(argv)

    api = Api(args.api)
    want = set(args.scenarios.upper())
    report: dict[str, Any] = {"api": args.api, "teamflow": args.teamflow, "scenarios": {}}
    started = time.time()

    print("RUN-007 端到端业务闭环")
    step("0 健康检查", lambda: api.call("GET", "/system/health"))
    browser = step(
        "0 浏览器可用性", lambda: api.call("GET", "/system/browser?probe=true", timeout=90)
    )
    if not browser.get("available"):
        print("浏览器不可用，终止：", browser.get("error"))
        return 2
    step("0 资产种子", lambda: api.call("POST", "/system/seed", timeout=300))

    if "A" in want:
        report["scenarios"]["A"] = scenario_a(api, args.teamflow)
    if "B" in want:
        b = scenario_b(api)
        report["scenarios"]["B"] = b
        if "C" in want:
            report["scenarios"]["C"] = scenario_c(api, b["run_id"], b["diagnosis_id"])
        if "D" in want:
            report["scenarios"]["D"] = scenario_d(api, args.teamflow)
    elif "D" in want:
        report["scenarios"]["D"] = scenario_d(api, args.teamflow)

    report["steps"] = STEPS
    report["duration_s"] = round(time.time() - started, 1)
    report["failed_steps"] = [s for s in STEPS if not s["ok"]]
    report["passed"] = not report["failed_steps"]

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print("\n" + "=" * 60)
    print(f"步骤 {len(STEPS)} 个，失败 {len(report['failed_steps'])} 个；证据 {out}")
    for failed in report["failed_steps"]:
        print(f"  - {failed['step']}: {failed.get('error', '')[:200]}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
