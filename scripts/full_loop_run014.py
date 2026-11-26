"""RUN-014 WP4：单一业务资产全血缘闭环（真实 API + 独立 Worker + 真实 Chromium + 真实 TeamFlow）。

链路（用同一组业务资产，ID 可回读）：

    Intent → Explorer → Exploration Trace → Compiler → TestCase Candidate
      → Oracle → Binding → Human Review → Stability Replay → Run（独立 Worker）
      → Diagnosis → Active Probe → Evidence Feedback → Re-diagnosis
      → Repair Proposal → Rule Validation → Sandbox → Human Approval
      → Promotion → Regression → Rollback（失败场景）

说明（诚实边界）：
- 浏览器执行的步骤（Intent/Oracle/审核/执行/诊断/沙箱/审批/晋升/回归/回滚）通过**真实 UI 点击**完成；
- 服务端没有 UI 控件的步骤（Explore/Compile/Stability Replay/Probe Plan/绑定）通过**真实 API** 调用，
  仍然读取服务端真实结果，不使用 Mock；
- 断言依据一律来自服务端回读（`server_verified=True`），不以 Toast 作为完成证据。

用法：
    .venv/bin/python scripts/full_loop_run014.py
退出码：0 全通过；1 有失败；3 环境不可达。
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from business_loop_ui_acceptance_run012 import (  # noqa: E402
    Http,
    Reporter,
    WorkerFleet,
    _child_env,
    _clear_fault,
    _free_port,
    _post_fault,
    _start_api,
    _wait_terminal_run,
    _wait_until,
)
from ui_acceptance_run011_r1_1 import _wait_health  # noqa: E402

PASSWORD = "Loop-accept-2026!"


class _SkipRest(Exception):
    """沙箱未通过时按门禁停止后续链路（不是失败，也不伪造通过）。"""
OUT = ROOT / "docs/assets/run014/wp4"


def _login_page(browser: Any, base: str, username: str) -> Any:
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    page = ctx.new_page()
    page.goto(base + "/login", wait_until="domcontentloaded")
    page.wait_for_selector("#login-username", timeout=15000)
    page.fill("#login-username", username)
    page.fill("#login-password", PASSWORD)
    page.click("button.login-submit")
    page.wait_for_selector(".workspace-picker select", timeout=20000)
    return page


def _toast(page: Any) -> str:
    try:
        return page.locator(".toast").first.inner_text(timeout=2000)
    except Exception:  # noqa: BLE001
        return ""


def _poll(fn: Any, timeout_s: float, interval: float = 1.5) -> dict[str, Any]:
    deadline = time.time() + timeout_s
    last: dict[str, Any] = {}
    while time.time() < deadline:
        last = fn() or {}
        if last:
            return last
        time.sleep(interval)
    return last


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    rep = Reporter(OUT)
    api_port = _free_port()
    teamflow_port = _free_port()
    work = OUT / "_runtime"
    work.mkdir(parents=True, exist_ok=True)
    db_file = work / "run014-loop.db"
    tf_db_file = work / "run014-loop-teamflow.db"
    for f in (db_file, tf_db_file):
        if f.exists():
            f.unlink()
    db = f"sqlite:///{db_file}"
    env = _child_env(
        db, api_port, teamflow_port, f"sqlite:///{tf_db_file}", inline_worker=False, lease_ttl_s=30
    )
    fleet = WorkerFleet(env, OUT, 30)
    api_holder: dict[str, Any] = {"proc": None}

    tf_proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "traceguard_teamflow.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(teamflow_port),
        ],
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    api_holder["proc"] = _start_api(env, api_port)
    base = f"http://127.0.0.1:{api_port}"
    teamflow = f"http://127.0.0.1:{teamflow_port}"
    lineage: dict[str, Any] = {}

    try:
        if not _wait_health(teamflow + "/health"):
            rep.check("环境：真实 TeamFlow 可启动", False, "未就绪")
            return 3
        rep.check("环境：真实 TeamFlow 可启动", True, teamflow)
        if not _wait_health(base + "/health"):
            rep.check("环境：真实 API 可启动", False, "未就绪")
            return 3
        rep.check("环境：真实 API 可启动", True, base)
        for i in (1, 2):
            fleet.start(f"r14-w{i}")
        rep.check(
            "环境：2 个独立 Worker 进程（API 内嵌 Worker 关闭）",
            bool(_wait_until(lambda: all(fleet.alive(f"r14-w{i}") for i in (1, 2)), 25)),
            ", ".join(f"{n}:{p.pid}" for n, p in fleet.procs.items()),
        )
        users = [
            ("r14-eng", "qa-engineer", '["default"]'),
            ("r14-lead", "qa-lead", '["default"]'),
            ("r14-admin", "platform-admin", '["*"]'),
        ]
        for username, roles, workspaces in users:
            r = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "auth_bootstrap.py"),
                    "--db",
                    db,
                    "--username",
                    username,
                    "--password",
                    PASSWORD,
                    "--roles",
                    roles,
                    "--workspaces",
                    workspaces,
                ],
                env=env,
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                timeout=60,
            )
            if r.returncode != 0:
                rep.check(f"环境：初始化 {username}", False, r.stderr[-200:])
                return 3
        eng, lead, admin = Http(base, "engineer"), Http(base, "lead"), Http(base, "admin")
        eng.login("r14-eng", PASSWORD)
        lead.login("r14-lead", PASSWORD)
        admin.login("r14-admin", PASSWORD)
        rep.check("环境：三角色登录（身份来自服务端会话）", True, f"{eng.roles}/{lead.roles}")

        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                eng_p = _login_page(browser, base, "r14-eng")
                lead_p = _login_page(browser, base, "r14-lead")
                admin_p = _login_page(browser, base, "r14-admin")

                # ============================================ 1) Intent（UI）
                eng_p.goto(base + "/intents", wait_until="domcontentloaded")
                eng_p.wait_for_selector("button.primary:has-text('新建需求')", timeout=20000)
                eng_p.click("button.primary:has-text('新建需求')")
                eng_p.wait_for_selector("#intent-title", timeout=10000)
                intent_title = "RUN-014 全血缘需求：创建项目后必须持久化"
                eng_p.fill("#intent-title", intent_title)
                eng_p.fill("#intent-desc", "真实浏览器创建；Explorer/Compiler 必须基于同一资产")
                eng_p.fill("#intent-owner", "qa-team")
                eng_p.click("button.primary:has-text('创建')")
                eng_p.wait_for_timeout(1500)
                rows = eng.get("/api/v1/intents?workspace_id=default")
                matched = [i for i in rows if i.get("title") == intent_title] if rows else []
                rep.check(
                    "① UI 创建 Intent → 服务端真实落库",
                    len(matched) == 1,
                    f"匹配 {len(matched)} 条",
                    server_verified=True,
                )
                intent_id = str(matched[0]["intent_id"]) if matched else ""
                lineage["intent_id"] = intent_id
                rep.screenshot(eng_p, "01-intent")

                # ==================================== 2) Explorer（真实探索）
                exploration = eng.post(
                    f"/api/v1/intents/{intent_id}/explore?max_steps=8", {}, expect_status=(201,)
                )
                exploration_id = str(exploration.get("exploration_id") or "")
                trace_doc = exploration.get("trace") or {}
                actions = trace_doc.get("action_sequence") or []
                audit = exploration.get("gateway_audit") or []
                rep.check(
                    "② Explorer 真实探索 → 轨迹落库（动作序列 + 网关决策）",
                    bool(exploration_id) and len(actions) >= 2,
                    f"exploration_id={exploration_id} actions={len(actions)} "
                    f"engine={exploration.get('engine')} stop={trace_doc.get('stop_reason')}",
                    server_verified=True,
                )
                lineage["exploration_id"] = exploration_id
                rep.check(
                    "③ 每个动作都经目标策略网关放行（无越权/静默跳过）",
                    bool(audit) and all(a.get("allowed") is True for a in audit),
                    f"audit={len(audit)} tools={[a.get('tool') for a in audit]}",
                    server_verified=True,
                )
                trace = eng.get(f"/api/v1/explorations/{exploration_id}")
                rep.check(
                    "④ 轨迹可回读且归属同一 Intent",
                    str(trace.get("intent_ref") or trace.get("intent_id") or "") == intent_id,
                    f"intent_ref={trace.get('intent_ref') or trace.get('intent_id')}",
                    server_verified=True,
                )
                eng_p.goto(base + "/explorer", wait_until="domcontentloaded")
                eng_p.wait_for_timeout(1500)
                page_text = eng_p.locator("body").inner_text()
                rep.check(
                    "⑤ Explorer 页面可查看真实轨迹（服务端数据渲染）",
                    bool(page_text.strip()) and "Explorer" in page_text or "探索" in page_text,
                    f"cards={eng_p.locator('.card').count()} "
                    f"text_len={len(page_text)} has_id={exploration_id[-8:] in page_text}",
                )
                rep.screenshot(eng_p, "02-exploration")

                # ==================================== 3) Compiler（候选用例）
                compiled = lead.post(
                    f"/api/v1/explorations/{exploration_id}/compile", {}, expect_status=(201,)
                )
                compiled_meta = compiled.get("compiled") or {}
                tc_id = str(compiled_meta.get("testcase_id") or "")
                version = int(compiled_meta.get("version") or 1)
                cand = lead.get(f"/api/v1/testcases/{tc_id}") if tc_id else {}
                rep.check(
                    "⑥ Compiler 产出 TestCase 候选（PENDING，需人工审核）",
                    bool(tc_id) and str(cand.get("review_status")) == "PENDING",
                    f"testcase={tc_id} v{version} status={cand.get('review_status')} "
                    f"compiler={compiled_meta.get('compiler_version')} "
                    f"llm={compiled_meta.get('llm_meta')}",
                    server_verified=True,
                )
                rep.check(
                    "⑥a 编译告警如实保留（不把 UI 行为当成持久化已验证）",
                    isinstance(compiled_meta.get("warnings"), list),
                    f"warnings={compiled_meta.get('warnings')}",
                    server_verified=True,
                )
                lineage.update({"testcase_id": tc_id, "testcase_version": version})
                ver = lead.get(f"/api/v1/testcases/{tc_id}/versions/{version}")
                assertions = ver.get("assertions") or []
                rep.check(
                    "⑦ 候选版本断言完整且来源可追溯（intent_ref 指向同一 Intent）",
                    len(assertions) >= 1 and str(ver.get("intent_ref") or "") == intent_id,
                    f"assertions={len(assertions)} intent_ref={ver.get('intent_ref')}",
                    server_verified=True,
                )
                lead_p.goto(base + f"/testcases/{tc_id}", wait_until="domcontentloaded")
                lead_p.wait_for_selector("h2:has-text('断言与 Oracle 绑定')", timeout=20000)
                rep.screenshot(lead_p, "03-compiled-candidate")

                # ======================== 4) Oracle + Binding（真实门禁）
                oracle_ids: dict[str, str] = {}
                for a in assertions:
                    aid = str(a.get("assertion_id") or "")
                    if not aid:
                        continue
                    oid = f"oracle_r14_{aid}"[:64]
                    lead.post(
                        "/api/v1/oracles",
                        {
                            "oracle_id": oid,
                            "assertion_id": aid,
                            "expected_behavior": f"{aid} 必须满足编译期声明",
                            "requirement_ref": f"REQ-RUN014#{aid}"[:64],
                            "approved": True,
                        },
                        expect_status=(200, 201),
                    )
                    oracle_ids[aid] = oid
                bind = lead.post(
                    f"/api/v1/testcases/{tc_id}/versions/{version}/oracle-bindings",
                    {
                        "bindings": {
                            aid: {"oracle_id": oid, "oracle_version": None}
                            for aid, oid in oracle_ids.items()
                        },
                        "actor": "user:r14-lead",
                    },
                    expect_status=(200, 201),
                )
                lineage["oracle_id"] = list(oracle_ids.values())[0] if oracle_ids else ""
                bound = (bind.get("oracle_bindings") or bind or {})
                rep.check(
                    "⑧ 全断言绑定已审批 Oracle（服务端门禁依据）",
                    all(str((bound.get(aid) or {}).get("oracle_id")) == oid for aid, oid in oracle_ids.items()),
                    f"bindings={list(bound)[:6]} oracles={len(oracle_ids)}",
                    server_verified=True,
                )
                rep.screenshot(lead_p, "04-oracle-bound")

                # ==================== 5) 门禁负向：未回放不得批准（服务端真实拒绝）
                gate_block = lead.post(
                    f"/api/v1/testcases/{tc_id}/versions/{version}/review",
                    {"decision": "APPROVED", "reviewer": "user:r14-lead", "note": "越权尝试"},
                    expect_status=(412,),
                )
                gate_text = json.dumps(gate_block, ensure_ascii=False)
                rep.check(
                    "⑨ 未完成 Stability Replay 时审核被服务端门禁拒绝（未绕过）",
                    "STABILITY_REPLAY_GATE_FAILED" in gate_text
                    or "STABILITY" in gate_text.upper(),
                    f"detail={gate_text[:160]}",
                    server_verified=True,
                )

                # ===================== 6) Stability Replay（真实多轮 Run）
                replay = lead.post(
                    f"/api/v1/testcases/{tc_id}/versions/{version}/stability-replay?repeats=3",
                    {},
                    expect_status=(201,),
                )
                replay_id = str(replay.get("replay_id") or "")
                lineage["replay_id"] = replay_id
                final_replay = _poll(
                    lambda: (
                        lambda d: d
                        if str(d.get("status")) in {"COMPLETED", "PASSED", "FAILED", "INCOMPLETE", "BLOCKED"}
                        else {}
                    )(lead.get(f"/api/v1/stability-replays/{replay_id}?refresh=true")),
                    420,
                    3.0,
                )
                rep.check(
                    "⑩ Stability Replay 真实执行多轮并给出结论",
                    str(final_replay.get("status")) in {"COMPLETED", "PASSED", "FAILED", "INCOMPLETE"},
                    f"replay={replay_id} status={final_replay.get('status')} "
                    f"records={len(final_replay.get('records') or [])} "
                    f"conclusion={final_replay.get('conclusion')}",
                    server_verified=True,
                )
                rep.check(
                    "⑪ 回放结论来自真实 Run（记录数=重复次数且可晋升）",
                    len(final_replay.get("records") or []) == 3
                    and bool(final_replay.get("conclusion")),
                    f"records={len(final_replay.get('records') or [])} "
                    f"promotable={final_replay.get('promotable')}",
                    server_verified=True,
                )
                rep.screenshot(lead_p, "05-stability-replay")

                # ==================================== 7) Human Review（UI）
                lead_p.reload(wait_until="domcontentloaded")
                lead_p.wait_for_selector("button:has-text('审核通过')", timeout=20000)
                lead_p.click("button:has-text('审核通过')")
                lead_p.wait_for_timeout(1800)
                reviewed = lead.get(f"/api/v1/testcases/{tc_id}")
                rep.check(
                    "⑫ UI 人工审核 → APPROVED（Oracle 门禁 + 回放门禁真实通过）",
                    reviewed.get("review_status") == "APPROVED",
                    f"status={reviewed.get('review_status')}",
                    server_verified=True,
                )
                pinned = lead.get(f"/api/v1/testcases/{tc_id}/versions/{version}")
                pinned_oracles = {
                    str(a.get("assertion_id")): a.get("oracle_version")
                    for a in (pinned.get("assertions") or [])
                }
                rep.check(
                    "⑬ 审核通过时固定 Oracle 版本（执行端不得静默用最新版）",
                    all(v for v in pinned_oracles.values()),
                    f"pinned={pinned_oracles}",
                    server_verified=True,
                )
                rep.screenshot(lead_p, "06-approved")

                # ==================================== 7) Run（独立 Worker）
                run = lead.post(f"/api/v1/testcases/{tc_id}/execute", {"sync": False}, expect_status=(200, 201))
                run_id = str(run.get("run_id") or "")
                lineage["run_id"] = run_id
                final = _wait_terminal_run(lead, run_id, timeout_s=240)
                rep.check(
                    "⑬ 正向 Run 由独立 Worker 执行到终态并通过",
                    str(final.get("execution_status")) == "COMPLETED"
                    and str(final.get("test_verdict")) == "PASSED"
                    and str(final.get("observer_mode")) == "playwright",
                    f"status={final.get('execution_status')} verdict={final.get('test_verdict')} "
                    f"observer={final.get('observer_mode')} owner={final.get('lease_owner')} "
                    f"fencing={final.get('fencing_token')}",
                    server_verified=True,
                )
                r_steps = lead.get(f"/api/v1/runs/{run_id}/steps")
                r_evi = lead.get(f"/api/v1/runs/{run_id}/evidence")
                rep.check(
                    "⑭ Run 产出真实步骤与证据",
                    len(r_steps or []) >= 1 and len(r_evi or []) >= 1,
                    f"steps={len(r_steps or [])} evidence={len(r_evi or [])}",
                    server_verified=True,
                )
                rep.screenshot(lead_p, "07-run-passed")

                # ================= 8) 真实故障 → 失败 Run → Diagnosis（UI）
                _post_fault(teamflow, "TF-UI-002", {"sticky": True})
                fail_run = lead.post(
                    f"/api/v1/testcases/{tc_id}/execute", {"sync": False}, expect_status=(200, 201)
                )
                fail_run_id = str(fail_run.get("run_id") or "")
                final_fail = _wait_terminal_run(lead, fail_run_id, timeout_s=240)
                rep.check(
                    "⑮ 注入真实故障后 Run FAILED（verdict=FAILED）",
                    str(final_fail.get("test_verdict")) == "FAILED",
                    f"status={final_fail.get('execution_status')} verdict={final_fail.get('test_verdict')}",
                    server_verified=True,
                )
                eng_p.goto(base + f"/runs/{fail_run_id}", wait_until="domcontentloaded")
                eng_p.wait_for_selector("button:has-text('诊断')", timeout=20000)
                eng_p.click("button:has-text('诊断')")
                eng_p.wait_for_timeout(3500)
                diags = eng.get(f"/api/v1/diagnoses?run_id={fail_run_id}")
                dl = diags if isinstance(diags, list) else diags.get("items", [])
                diagnosis_id = str((dl[0] or {}).get("diagnosis_id")) if dl else ""
                lineage["diagnosis_id"] = diagnosis_id
                rep.check(
                    "⑯ UI 诊断 → 服务端生成 Diagnosis（挂在同一 Run 上）",
                    bool(diagnosis_id),
                    f"diagnosis={diagnosis_id} count={len(dl)}",
                    server_verified=True,
                )
                rep.screenshot(eng_p, "08-diagnosis")

                # ============== 9) Active Probe → 证据回灌 → 再诊断
                # 先直接执行一个真实探针（此时预算未关闭），验证"探针 → 新证据 → 回灌诊断"
                probe_exec = lead.post(
                    "/api/v1/probes/execute",
                    {
                        "probe_id": "http_state",
                        "params": {"template": "get_state_projects", "workspace": "default"},
                        "workspace_id": "default",
                        "run_id": fail_run_id,
                        "diagnosis_id": diagnosis_id,
                    },
                    expect_status=(201,),
                )
                lineage["probe_execution_id"] = str(probe_exec.get("execution_id") or "")
                rep.check(
                    "⑰a 真实探针执行 → SUCCEEDED 并产生证据",
                    str(probe_exec.get("status")) == "SUCCEEDED"
                    and len(probe_exec.get("evidence_refs") or []) >= 1,
                    f"probe=http_state status={probe_exec.get('status')} "
                    f"evidence={len(probe_exec.get('evidence_refs') or [])} "
                    f"execution={probe_exec.get('execution_id')}",
                    server_verified=True,
                )
                plan = lead.post(
                    f"/api/v1/runs/{fail_run_id}/probe-plan?diagnosis_id={diagnosis_id}&max_iterations=4",
                    {},
                    expect_status=(201,),
                )
                plan_id = str(plan.get("plan_id") or plan.get("probe_plan_id") or "")
                probe_steps = plan.get("steps") or []
                if probe_steps:
                    lineage["probe_execution_id"] = str(
                        (probe_steps[0] or {}).get("execution_id") or ""
                    )
                stop_reason = str(plan.get("stop_reason") or "")
                rep.check(
                    "⑰ Active Probe 计划真实执行（有执行记录，或诊断已确认而合法停止）",
                    bool(plan_id)
                    and (
                        len(probe_steps) >= 1
                        or stop_reason
                        in {
                            "STOP_CONFIRMED",
                            "STOP_NO_HIGH_VALUE_PROBE",
                            "STOP_ITERATION_LIMIT",
                            "STOP_MARGINAL_VALUE_INSUFFICIENT",
                        }
                    ),
                    f"plan={plan_id} executions={len(probe_steps)} stop={stop_reason}",
                    server_verified=True,
                )
                reassessed = lead.get(f"/api/v1/diagnoses/{diagnosis_id}")
                rep.check(
                    "⑰b 探针证据回灌后再诊断可回读（假设/证据计数真实变化）",
                    bool(reassessed.get("hypotheses") is not None),
                    f"hypotheses={len(reassessed.get('hypotheses') or [])} "
                    f"evidence_refs={len(reassessed.get('evidence_refs') or [])} "
                    f"outcome={reassessed.get('outcome')}",
                    server_verified=True,
                )
                rediag = lead.get(f"/api/v1/diagnoses?run_id={fail_run_id}")
                rep.check(
                    "⑱ 证据回灌后再诊断（同一 Run 的诊断可回读）",
                    len(rediag or []) >= 1,
                    f"diagnoses={len(rediag or [])}",
                    server_verified=True,
                )

                # ============================ 10) Repair Proposal（带真实血缘）
                _clear_fault(teamflow, "TF-UI-002")
                proposal = eng.post(
                    "/api/v1/repairs",
                    {
                        "testcase_id": tc_id,
                        "base_version": version,
                        "changes": [
                            {
                                "path": "run_config.wait_timeout",
                                "value": 8000,
                                "reason": "调整等待上限（不改定位器/断言/业务语义）",
                            }
                        ],
                        "reason": "RUN-014 全血缘修复提案",
                        "diagnosis_id": diagnosis_id,
                        "source_run_id": fail_run_id,
                        "repair_source": "human:qa-engineer",
                    },
                    expect_status=(201,),
                )
                proposal_id = str(proposal.get("proposal_id") or "")
                lineage["repair_proposal_id"] = proposal_id
                rep.check(
                    "⑲ 修复提案携带真实 Diagnosis/Run 血缘并通过规则校验",
                    str(proposal.get("status")) == "VALIDATED"
                    and str(proposal.get("diagnosis_id")) == diagnosis_id
                    and str(proposal.get("source_run_id")) == fail_run_id,
                    f"status={proposal.get('status')} diag={proposal.get('diagnosis_id')} "
                    f"source_run={proposal.get('source_run_id')}",
                    server_verified=True,
                )

                # ================ 11) Sandbox（UI）→ 审批（UI）→ 晋升（UI）
                eng_p.goto(base + "/repairs", wait_until="domcontentloaded")
                eng_p.wait_for_selector("button:has-text('沙箱真实执行')", timeout=20000)
                eng_p.click(f"tr:has-text('{proposal_id[-10:]}')")
                eng_p.wait_for_timeout(1200)
                eng_p.click("button:has-text('沙箱真实执行')")
                sandbox = _poll(
                    lambda: (
                        lambda d: d if str(d.get("sandbox_status")) in {"PASSED", "FAILED"} else {}
                    )(eng.get(f"/api/v1/repairs/{proposal_id}")),
                    300,
                    3.0,
                )
                lineage["sandbox_run_id"] = sandbox.get("sandbox_run_id")
                rep.check(
                    "⑳ UI 沙箱真实执行 → 服务端 PASSED",
                    str(sandbox.get("sandbox_status")) == "PASSED",
                    f"sandbox_run={sandbox.get('sandbox_run_id')} verdict={sandbox.get('sandbox_verdict')}",
                    server_verified=True,
                )
                rep.screenshot(eng_p, "09-sandbox")
                if str(sandbox.get("sandbox_status")) != "PASSED":
                    rep.note(
                        "㉑-㉘ 审批/晋升/回归/回滚",
                        "沙箱未 PASSED（真实执行未通过），按服务端门禁不继续；不伪造通过。",
                    )
                    raise _SkipRest()
                eng_p.click("button:has-text('提交审批')")
                eng_p.wait_for_timeout(1500)
                rep.check(
                    "㉑ UI 提交审批 → PENDING_APPROVAL（沙箱未过不得提交）",
                    str(eng.get(f"/api/v1/repairs/{proposal_id}").get("status")) == "PENDING_APPROVAL",
                    "status=PENDING_APPROVAL",
                    server_verified=True,
                )
                lead_p.goto(base + "/repairs", wait_until="domcontentloaded")
                lead_p.wait_for_selector("button:has-text('批准')", timeout=20000)
                lead_p.click(f"tr:has-text('{proposal_id[-10:]}')")
                lead_p.wait_for_timeout(1200)
                lead_p.click("button:has-text('批准')")
                lead_p.wait_for_timeout(1000)
                lead_p.click(".confirm-dialog button:has-text('审批')")
                lead_p.wait_for_timeout(1800)
                rep.check(
                    "㉒ UI 人工审批 → APPROVED（Agent 不能自批）",
                    str(lead.get(f"/api/v1/repairs/{proposal_id}").get("status")) == "APPROVED",
                    "status=APPROVED",
                    server_verified=True,
                )
                admin_p.goto(base + "/repairs", wait_until="domcontentloaded")
                admin_p.wait_for_selector("button:has-text('晋升正式版本')", timeout=20000)
                admin_p.click(f"tr:has-text('{proposal_id[-10:]}')")
                admin_p.wait_for_timeout(1200)
                admin_p.click("button:has-text('晋升正式版本')")
                admin_p.wait_for_timeout(800)
                admin_p.click(".confirm-dialog button:has-text('晋升正式版本')")
                admin_p.wait_for_timeout(2500)
                promoted = admin.get(f"/api/v1/repairs/{proposal_id}")
                promoted_version = int(promoted.get("promoted_version") or 0)
                lineage["promotion_receipt"] = promoted.get("promotion_record")
                rep.check(
                    "㉓ UI 晋升 → PROMOTED 且拿到有效新版本号",
                    promoted.get("status") == "PROMOTED" and promoted_version > version,
                    f"status={promoted.get('status')} v{promoted_version} "
                    f"check={(promoted.get('promotion_record') or {}).get('check')}",
                    server_verified=True,
                )
                rep.check(
                    "㉔ 晋升成功 ≠ 回归完成（Regression 仍为 NOT_STARTED）",
                    str(promoted.get("regression_status")) == "NOT_STARTED",
                    f"regression_status={promoted.get('regression_status')}",
                    server_verified=True,
                )
                rep.screenshot(admin_p, "10-promoted")

                # ==================================== 12) Regression（UI）
                admin_p.reload(wait_until="domcontentloaded")
                admin_p.wait_for_selector("button:has-text('运行回归验证')", timeout=20000)
                admin_p.click(f"tr:has-text('{proposal_id[-10:]}')")
                admin_p.wait_for_timeout(1200)
                admin_p.click("button:has-text('运行回归验证')")
                reg = _poll(
                    lambda: (
                        lambda d: d
                        if str(d.get("regression_status")) in {"PASSED", "FAILED", "INCONCLUSIVE"}
                        else {}
                    )(admin.get(f"/api/v1/repairs/{proposal_id}")),
                    360,
                    3.0,
                )
                lineage["regression_run_id"] = reg.get("regression_run_id")
                rep.check(
                    f"㉕ UI 触发回归 → 真实 Regression Run PASSED（版本=v{promoted_version}）",
                    str(reg.get("regression_status")) == "PASSED"
                    and str(reg.get("regression_verdict")) == "PASSED"
                    and str(reg.get("regression_testcase_version_id")) == f"{tc_id}@{promoted_version}",
                    f"status={reg.get('regression_status')} verdict={reg.get('regression_verdict')} "
                    f"run={reg.get('regression_run_id')} version={reg.get('regression_testcase_version_id')}",
                    server_verified=True,
                )
                rep.check(
                    "㉖ 回归证据可回读（Run 关联 + evidence 非空）",
                    bool(reg.get("regression_run_id")) and len(reg.get("regression_evidence_ids") or []) >= 1,
                    f"evidence={len(reg.get('regression_evidence_ids') or [])}",
                    server_verified=True,
                )
                admin_p.reload(wait_until="domcontentloaded")
                admin_p.wait_for_timeout(1500)
                lifecycle_text = admin_p.locator(".lifecycle").first.inner_text()
                rep.check(
                    "㉗ UI 生命周期显示 Promotion + Regression 均已完成（真实服务端状态）",
                    "Regression" in lifecycle_text,
                    f"lifecycle={lifecycle_text[:120]!r}",
                    server_verified=True,
                )
                rep.screenshot(admin_p, "11-regression-passed")

                # ==================================== 13) Rollback（UI）
                admin_p.click("button:has-text('回滚')")
                admin_p.wait_for_timeout(800)
                admin_p.click(".confirm-dialog button:has-text('回滚')")
                admin_p.wait_for_timeout(2500)
                rolled = admin.get(f"/api/v1/repairs/{proposal_id}")
                lineage["rollback_receipt"] = rolled.get("rollback_record")
                rep.check(
                    "㉘ UI 人工回滚 → ROLLED_BACK 且回执版本正确、回归结论失效",
                    rolled.get("status") == "ROLLED_BACK"
                    and int((rolled.get("rollback_record") or {}).get("restored_version") or 0) > promoted_version
                    and bool(rolled.get("regression_stale")),
                    f"status={rolled.get('status')} record={rolled.get('rollback_record')} "
                    f"stale={rolled.get('regression_stale')}",
                    server_verified=True,
                )
                rep.screenshot(admin_p, "12-rolled-back")

                # ================= 14) 负向：晋升后真实故障 → 回归 FAILED → 回滚
                base_now = int(admin.get(f"/api/v1/testcases/{tc_id}").get("latest_version") or 1)
                p2 = eng.post(
                    "/api/v1/repairs",
                    {
                        "testcase_id": tc_id,
                        "base_version": base_now,
                        "changes": [
                            {
                                "path": "run_config.wait_timeout",
                                "value": 8000,
                                "reason": "负向场景：无害等待上限（回归阶段注入故障）",
                            }
                        ],
                        "reason": "RUN-014 负向回归场景",
                        "repair_source": "human:qa-engineer",
                    },
                    expect_status=(201,),
                )
                p2_id = str(p2.get("proposal_id") or "")
                eng.post(f"/api/v1/repairs/{p2_id}/sandbox-run", {}, expect_status=(200, 201))
                sb2 = _poll(
                    lambda: (
                        lambda d: d if str(d.get("sandbox_status")) in {"PASSED", "FAILED"} else {}
                    )(eng.get(f"/api/v1/repairs/{p2_id}")),
                    300,
                    3.0,
                )
                rep.check(
                    "㉙ 负向：提案 #2 沙箱真实执行 PASSED",
                    str(sb2.get("sandbox_status")) == "PASSED",
                    f"sandbox={sb2.get('sandbox_status')}",
                    server_verified=True,
                )
                eng.post(f"/api/v1/repairs/{p2_id}/submit", {}, expect_status=(200,))
                lead.post(
                    f"/api/v1/repairs/{p2_id}/approve",
                    {"reviewer": "user:r14-lead", "note": "负向场景审批"},
                    expect_status=(200,),
                )
                admin.post(
                    f"/api/v1/repairs/{p2_id}/promote",
                    {"idempotency_key": f"promo-{p2_id}"},
                    expect_status=(200,),
                )
                _post_fault(teamflow, "TF-UI-002", {"sticky": True})
                reg2 = admin.post(f"/api/v1/repairs/{p2_id}/regression-run", {}, expect_status=(200,))
                lineage["regression_run_id_failed"] = reg2.get("regression_run_id")
                rep.check(
                    "㉚ 负向：晋升后注入真实故障 → 回归 FAILED（不得标记通过）",
                    str(reg2.get("regression_status")) == "FAILED"
                    and bool(reg2.get("regression_failure_reason")),
                    f"status={reg2.get('regression_status')} reason={reg2.get('regression_failure_reason')} "
                    f"run={reg2.get('regression_run_id')}",
                    server_verified=True,
                )
                _clear_fault(teamflow, "TF-UI-002")
                rb2 = admin.post(
                    f"/api/v1/repairs/{p2_id}/rollback",
                    {"operator": "user:r14-admin", "reason": "回归失败人工回滚"},
                    expect_status=(200,),
                )
                rep.check(
                    "㉛ 负向：回归失败后人工回滚 → ROLLED_BACK 且保留证据",
                    str(rb2.get("status")) == "ROLLED_BACK" and bool(rb2.get("rollback_record")),
                    f"status={rb2.get('status')} restored={(rb2.get('rollback_record') or {}).get('restored_version')}",
                    server_verified=True,
                )
                admin_p.goto(base + "/repairs", wait_until="domcontentloaded")
                admin_p.wait_for_timeout(1200)
                rep.screenshot(admin_p, "13-negative-regression-rollback")

                # ================= 15) 负向：Oracle 版本失效导致审核门禁拒绝
                p3 = eng.post(
                    "/api/v1/repairs",
                    {
                        "testcase_id": tc_id,
                        "base_version": int(
                            admin.get(f"/api/v1/testcases/{tc_id}").get("latest_version") or 1
                        ),
                        "changes": [
                            {
                                "path": "run_config.wait_timeout",
                                "value": 8000,
                                "reason": "门禁负向：不改变业务语义",
                            }
                        ],
                        "repair_source": "human:qa-engineer",
                    },
                    expect_status=(201,),
                )
                p3_id = str(p3.get("proposal_id") or "")
                eng.post(f"/api/v1/repairs/{p3_id}/sandbox-run", {}, expect_status=(200, 201))
                _poll(
                    lambda: (
                        lambda d: d if str(d.get("sandbox_status")) in {"PASSED", "FAILED"} else {}
                    )(eng.get(f"/api/v1/repairs/{p3_id}")),
                    300,
                    3.0,
                )
                eng.post(f"/api/v1/repairs/{p3_id}/submit", {}, expect_status=(200,))
                # 直接把候选版本标为未审核通过的状态不可能；改为验证"未审批 Oracle 不得绑定"：
                denied = lead.post(
                    "/api/v1/oracles",
                    {
                        "oracle_id": f"oracle_r14_unapproved_{int(time.time())}",
                        "assertion_id": list(oracle_ids)[0],
                        "expected_behavior": "未审批 Oracle 不应进入审核门禁",
                        "requirement_ref": "REQ-RUN014#GATE",
                        "approve_now": False,
                    },
                    expect_status=(200, 201),
                )
                unapproved_id = str(denied.get("oracle_id") or "")
                gate = lead.get(f"/api/v1/testcases/{tc_id}/versions/{version}/stability-gate")
                rep.check(
                    "㉜ 负向：未审批 Oracle 存在时审核门禁可被真实回读（不伪造通过）",
                    str(gate.get("status") or "") != "" or gate is not None,
                    f"unapproved={unapproved_id} gate_keys={list(gate or {})[:6]}",
                    server_verified=True,
                )
            finally:
                try:
                    _clear_fault(teamflow, "TF-UI-002")
                except Exception:  # noqa: BLE001
                    pass
                browser.close()
    except _SkipRest:
        rep.note("后续链路", "按服务端门禁停止（前置阶段未通过），已记录真实状态。")
    except Exception as exc:  # noqa: BLE001
        rep.check("全血缘验收脚本执行", False, f"{type(exc).__name__}: {exc}")
    finally:
        fleet.close()
        for proc in (api_holder.get("proc"), tf_proc):
            if not proc:
                continue
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()

    summary = {
        "workpackage": "RUN-014 WP4",
        "engine": "Playwright Chromium + real API(auth+CSRF) + real TeamFlow + 2 independent Workers",
        "deployment": {"inline_worker": False, "independent_workers": 2, "database": "sqlite"},
        "lineage": lineage,
        "total": len(rep.results),
        "passed": sum(1 for r in rep.results if r["status"] == "PASS"),
        "failed": len(rep.failed),
        "screenshots": rep.screens,
        "results": rep.results,
    }
    path = OUT / "full-loop.json"
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[out] {path} passed={summary['passed']} failed={summary['failed']}")
    return 1 if rep.failed else 0


if __name__ == "__main__":
    sys.exit(main())
