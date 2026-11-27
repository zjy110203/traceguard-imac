"""RUN-007 WP2：真实端到端业务闭环（场景 A/B/C/D）。

全部通过 HTTP API 驱动，底层是真实 SQLite + 真实 Chromium + 真实独立 Worker 执行内核；
不使用 Mock、不直接改数据库状态。

RUN-007-RESUME 改造（WP2-01）：
- 正式 Run / 故障 Run / 沙箱 Run / 回归 Run / 回滚复核 Run 一律 `sync: false` 入队，
  由**独立 Worker 进程**（TG_INLINE_WORKER=0）消费队列，脚本只轮询 `GET /runs/{run_id}`；
- 每一步记录真实 Worker ID、Run ID、fencing token、领取耗时与终态；
- 证据链改为通过 `GET /runs/{run_id}/evidence` 取真实 Evidence，并在证据图中校验
  对应节点与关系（不再只看 `node_type == "Evidence"` 就宣称证据完整）；
- 场景 A 记录 Agent 探索的真实 engine / provider / 是否真实 LLM / fallback。

场景：
A 正常业务：Intent → Explorer → Compile → Oracle 绑定 → 稳定性回放 → 人工审核门禁
            → 正式执行（独立 Worker）→ PASSED
B 真实产品故障：受控故障 → Trusted Oracle 揭穿假成功 → FAILED → 诊断 → Disposition
C 证据不足/冲突：真实 Run 的证据 → 真实只读探针 → 证据回灌 → 重新诊断 → 更新证据图
D 完整自愈：Repair Proposal → Diff 校验 → 沙箱真实执行（Worker）→ 人工审批 → CAS 晋升
            → 全量回归（Worker）→ 回滚 → 复核正式版本

用法：
    python scripts/e2e_run007.py --api http://127.0.0.1:8020 --mode worker \
        --out docs/assets/run007/e2e-worker-run007.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

STEPS: list[dict[str, Any]] = []
RUNS: list[dict[str, Any]] = []
TERMINAL_STATUSES = {"COMPLETED", "FAILED", "TIMED_OUT", "CANCELLED"}


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


class Executor:
    """Run 执行器：默认走独立 Worker 队列（sync=false + 轮询），并记录真实执行元数据。

    `mode="inline"` 仅用于本地调试对比，正式验收必须用 `worker`。
    """

    def __init__(self, api: Api, mode: str = "worker", timeout_s: float = 300.0) -> None:
        self.api = api
        self.mode = mode
        self.timeout_s = timeout_s
        self.api_worker_id = ""

    def _attach(self) -> None:
        try:
            health = self.api.call("GET", "/system/health", timeout=30)
            self.api_worker_id = str(((health or {}).get("worker") or {}).get("id") or "")
        except Exception:  # noqa: BLE001 - 仅用于元数据
            self.api_worker_id = ""

    def execute(
        self, testcase_id: str, *, version: int | None = None, timeout_s: float | None = None
    ) -> dict[str, Any]:
        """创建 Run 并等待终态；返回最终 Run 文档 + 执行元数据。"""
        path = f"/testcases/{testcase_id}/execute"
        if version is not None:
            path += f"?version={version}"
        started = time.time()
        submitted = self.api.call("POST", path, {"sync": self.mode == "inline"}, timeout=600)
        run_id = submitted.get("run_id") or (submitted.get("execution") or {}).get("run_id")
        if not run_id:
            raise AssertionError(f"未返回 run_id: {str(submitted)[:200]}")
        trace: dict[str, Any] = {
            "run_id": run_id,
            "mode": self.mode,
            "submitted_at": submitted.get("status"),
            "submitted_ts": round(time.time(), 3),
            "workers_seen": [],
            "fencing_tokens_seen": [],
            "polls": 0,
        }
        if self.mode == "inline":
            final = submitted.get("execution") or {}
            trace["workers_seen"] = [final.get("lease_owner") or self.api_worker_id]
        else:
            deadline = time.time() + (timeout_s or self.timeout_s)
            final = {}
            while time.time() < deadline:
                trace["polls"] += 1
                doc = self.api.call("GET", f"/runs/{run_id}", timeout=60)
                owner = str(doc.get("lease_owner") or "")
                if owner and owner not in trace["workers_seen"]:
                    trace["workers_seen"].append(owner)
                token = doc.get("fencing_token")
                if token is not None and token not in trace["fencing_tokens_seen"]:
                    trace["fencing_tokens_seen"].append(token)
                final = doc
                if doc.get("execution_status") in TERMINAL_STATUSES:
                    break
                time.sleep(0.25)
        trace.update(
            {
                "elapsed_s": round(time.time() - started, 1),
                "execution_status": final.get("execution_status"),
                "test_verdict": final.get("test_verdict"),
                "attempt": final.get("attempt"),
                "fencing_token": final.get("fencing_token"),
                "lease_owner": final.get("lease_owner"),
                "observer_mode": final.get("observer_mode"),
                "has_report": bool(final.get("report")),
            }
        )
        RUNS.append(trace)
        return {"run": final, **final, "trace": trace}

    def assert_worker_executed(self, trace: dict[str, Any]) -> dict[str, Any]:
        """正式验收断言：Run 必须由独立 Worker 消费执行（不是 API 线程池）。"""
        if self.mode == "inline":
            raise AssertionError("inline 模式不能用于正式验收：Run 必须由独立 Worker 执行")
        owners = [w for w in trace.get("workers_seen") or [] if w]
        if not owners:
            raise AssertionError(f"Run {trace['run_id']} 从未被 Worker 领取")
        if self.api_worker_id and all(w.startswith(self.api_worker_id) for w in owners):
            raise AssertionError(f"Run {trace['run_id']} 由 API 内嵌 Worker 执行：{owners}")
        if not (trace.get("fencing_token") or 0) >= 1:
            raise AssertionError("fencing_token 缺失：无法证明租约领取")
        return {"workers": owners, "fencing_token": trace.get("fencing_token")}


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


def scenario_a(api: Api, teamflow: str, ex: Executor) -> dict[str, Any]:
    print("\n== 场景 A：正常业务闭环（独立 Worker 执行） ==")
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
        "A2 Intent 提交评审并审核（RUN-019 状态机）",
        lambda: api.call("POST", f"/intents/{intent_id}/status", {"status": "REVIEW"}),
    )
    step(
        "A2b Intent 审核通过",
        lambda: api.call("POST", f"/intents/{intent_id}/status", {"status": "APPROVED"}),
    )

    exploration = step(
        "A3 Agent 真实探索",
        lambda: api.call("POST", f"/intents/{intent_id}/explore?max_steps=6", timeout=300),
    )
    exploration_id = exploration.get("exploration_id") or exploration.get("id")
    llm_summary = api.call("GET", "/system/llm/summary")
    stats = exploration.get("llm_stats") or {}
    llm_meta = {
        "engine": exploration.get("engine"),
        "provider": (llm_summary or {}).get("provider"),
        "model": (llm_summary or {}).get("default_model"),
        "configured": (llm_summary or {}).get("configured"),
        "connected": (llm_summary or {}).get("connected"),
        "llm_calls": stats.get("llm_calls"),
        "fallback_calls": stats.get("fallback_calls"),
        "fallback_happened": bool(stats.get("fallback_calls")),
        "last_error": stats.get("last_error"),
        "decision_source": (
            "llm" if str(exploration.get("engine") or "").startswith("llm:") else "rule"
        ),
        "real_llm_call_observed": bool(stats.get("llm_calls")),
        "llm_summary": llm_summary,
    }

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
        "A12 正式执行（独立 Worker 消费队列）",
        lambda: ex.execute(testcase_id, version=version),
    )
    run = execution.get("run") or execution
    step(
        "A13 结果必须 PASSED",
        lambda: _assert(run.get("test_verdict") == "PASSED", f"verdict={run.get('test_verdict')}"),
    )
    worker_a = step(
        "A14 必须由独立 Worker 执行（含租约/fencing）",
        lambda: ex.assert_worker_executed(execution["trace"]),
    )
    evidence_a = step(
        "A15 真实 Evidence 与 Step 已持久化",
        lambda: _verify_evidence(api, run.get("run_id") or "", expect_steps=True),
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
        "worker_execution": worker_a,
        "llm": llm_meta,
        "evidence": evidence_a,
        "execution_trace": execution["trace"],
    }


def scenario_b(api: Api, ex: Executor, teamflow: str) -> dict[str, Any]:
    print("\n== 场景 B：真实产品故障被 Trusted Oracle 揭穿（独立 Worker 执行） ==")
    # 假通过判定依赖"业务状态基线为 0"：先重置被测应用数据，否则历史数据会让
    # `count >= 1` 永远成立（这是用例数据隔离问题，不是产品缺陷，不能当假通过没被发现）。
    step(
        "B0 重置 TeamFlow 数据（保证业务状态基线为 0）",
        lambda: _teamflow_call(teamflow, "POST", "/api/test/reset", {}),
    )
    baseline = step(
        "B0b 基线业务状态必须为空",
        lambda: _assert(
            _project_count(teamflow) == 0, f"baseline count={_project_count(teamflow)}"
        ),
    )
    case_id = "tcase_tf_false_pass"
    execution = step(
        f"B1 真实执行受控故障用例 {case_id}",
        lambda: ex.execute(case_id),
    )
    run = execution.get("run") or execution
    run_id = run.get("run_id")
    step(
        "B2 假成功必须被判 FAILED",
        lambda: _assert(run.get("test_verdict") == "FAILED", f"verdict={run.get('test_verdict')}"),
    )
    worker_b = step(
        "B2b 必须由独立 Worker 执行", lambda: ex.assert_worker_executed(execution["trace"])
    )

    diag = step("B3 诊断", lambda: api.call("POST", f"/runs/{run_id}/diagnose", timeout=300))
    graph = step("B4 生成并查询证据图", lambda: api.call("POST", f"/runs/{run_id}/graph", {}))
    nodes = graph.get("nodes") or []
    edges = graph.get("edges") or []

    # RUN-007-RESUME：证据链从 /runs/{run_id}/evidence 取真实 Evidence，再核对图节点与关系
    evidence = step(
        "B5 通过 /runs/{run_id}/evidence 取真实 Evidence",
        lambda: _verify_evidence(api, run_id, expect_steps=True),
    )
    step(
        "B6 证据图必须包含这些 Evidence 节点与来源关系",
        lambda: _verify_graph_binds_evidence(nodes, edges, evidence["evidence_ids"], diag),
    )
    dispositions = api.call("GET", "/dispositions")
    disposition = next(
        (d for d in dispositions if d.get("diagnosis_id") == diag.get("diagnosis_id")), None
    )
    return {
        "run_id": run_id,
        "verdict": run.get("test_verdict"),
        "worker_execution": worker_b,
        "diagnosis_id": diag.get("diagnosis_id"),
        "outcome": diag.get("outcome"),
        "disposition": (disposition or {}).get("disposition") or diag.get("disposition"),
        "graph_nodes": len(nodes),
        "graph_edges": len(edges),
        "graph_node_types": sorted({str(n.get("node_type")) for n in nodes}),
        "baseline_project_count": (baseline or {}).get("ok"),
        "evidence_ids": evidence["evidence_ids"],
        "evidence": evidence,
        "execution_trace": execution["trace"],
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


def _verify_rollback_content(
    api: Api, testcase_id: str, restored_version: int, base_doc: dict[str, Any]
) -> dict[str, Any]:
    """回滚内容级验证：指针、步骤/定位器、断言、Oracle 绑定都必须回到基线。"""
    _assert(bool(restored_version), "回滚记录缺少 restored_version")
    restored = api.call("GET", f"/testcases/{testcase_id}/versions/{int(restored_version)}")
    meta = api.call("GET", f"/testcases/{testcase_id}")
    same_steps = [
        (s.get("step_id"), s.get("action"), (s.get("target") or {}).get("value"), s.get("value"))
        for s in restored.get("steps") or []
    ] == [
        (s.get("step_id"), s.get("action"), (s.get("target") or {}).get("value"), s.get("value"))
        for s in base_doc.get("steps") or []
    ]
    same_assertions = [
        (a.get("assertion_id"), a.get("oracle_id"), json.dumps(a.get("expected"), sort_keys=True))
        for a in restored.get("assertions") or []
    ] == [
        (a.get("assertion_id"), a.get("oracle_id"), json.dumps(a.get("expected"), sort_keys=True))
        for a in base_doc.get("assertions") or []
    ]
    same_bindings = (restored.get("oracle_bindings") or []) == (
        base_doc.get("oracle_bindings") or []
    )
    _assert(
        int(meta.get("latest_version") or 0) == int(restored_version), "正式版本指针未指向恢复版本"
    )
    _assert(same_steps, "回滚后的步骤/定位器内容与基线不一致")
    _assert(same_assertions, "回滚后的断言与基线不一致")
    _assert(same_bindings, "回滚后的 Oracle 绑定与基线不一致")
    return {
        "restored_version": int(restored_version),
        "latest_version": meta.get("latest_version"),
        "steps_restored": same_steps,
        "assertions_restored": same_assertions,
        "oracle_bindings_restored": same_bindings,
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


def scenario_d(api: Api, teamflow: str, ex: Executor) -> dict[str, Any]:
    """真实自愈：用例自带 TF-LOC-001 故障激活（提交按钮 test_id 变更 → create-submit-v2），
    基线真实失败后用"角色定位器"修复，沙箱与回归都在故障生效状态下由独立 Worker 执行，
    最后回滚并复核修复版本可回退。"""
    print(
        "\n== 场景 D：完整自愈（真实定位器故障 → 修复 → 沙箱 → 审批 → CAS 晋升 → 回归 → 回滚） =="
    )
    base_case, base_version = step(
        "D0 创建场景 D 用例（自带故障激活步骤）", lambda: _setup_locator_case(api, teamflow)
    )
    base_doc = api.call("GET", f"/testcases/{base_case}/versions/{base_version}")

    base_run = ex.execute(base_case, version=base_version)
    base_exec = base_run.get("run") or base_run
    # 说明：定位器故障属于"步骤执行中断"，按状态机规范 Run=FAILED / verdict=NOT_EVALUATED
    # （断言级"假成功被识破 → FAILED"由场景 B 覆盖）。两类都是真实失败。
    step(
        "D1 基线版本在故障下必须真实失败（步骤中断→NOT_EVALUATED 或 断言失败→FAILED）",
        lambda: _assert(
            base_exec.get("test_verdict") in {"FAILED", "NOT_EVALUATED"},
            f"verdict={base_exec.get('test_verdict')} status={base_exec.get('execution_status')}",
        ),
    )
    step(
        "D1b 基线 Run 必须由独立 Worker 执行",
        lambda: ex.assert_worker_executed(base_run["trace"]),
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
        "D5 沙箱真实执行（故障仍生效；等待独立 Worker 消费队列）",
        lambda: api.call("POST", f"/repairs/{pid}/sandbox-run", timeout=420),
    )
    step(
        "D6 沙箱必须 PASSED（证明修复真实有效）",
        lambda: _assert(
            sandbox.get("sandbox_status") == "PASSED", f"{sandbox.get('sandbox_status')}"
        ),
    )
    step(
        "D6b 沙箱 Run 必须由独立 Worker 执行",
        lambda: _assert(
            bool(sandbox.get("sandbox_executed_by"))
            and not str(sandbox.get("sandbox_executed_by")).startswith(ex.api_worker_id)
            and int(sandbox.get("sandbox_fencing_token") or 0) >= 1,
            f"executed_by={sandbox.get('sandbox_executed_by')} "
            f"fencing={sandbox.get('sandbox_fencing_token')}",
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
        "D14 全量回归（故障仍生效，修复版本应通过；独立 Worker 执行）",
        lambda: ex.execute(base_case, version=promoted_version),
    )
    reg = regression.get("run") or regression
    step(
        "D15 回归必须 PASSED",
        lambda: _assert(reg.get("test_verdict") == "PASSED", f"{reg.get('test_verdict')}"),
    )
    worker_reg = step(
        "D15b 回归必须由独立 Worker 执行",
        lambda: ex.assert_worker_executed(regression["trace"]),
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
    step(
        "D17b 回滚必须证明内容真的恢复（不是只返回成功）",
        lambda: _verify_rollback_content(api, base_case, restored, base_doc),
    )
    verify = ex.execute(base_case, version=promoted_version)
    ver = verify.get("run") or verify
    step(
        "D18 回滚后复核：晋升版本仍可通过正式执行入口由 Worker 执行",
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
        "sandbox_executed_by": sandbox.get("sandbox_executed_by"),
        "sandbox_fencing_token": sandbox.get("sandbox_fencing_token"),
        "approved_by": approved.get("approved_by"),
        "promoted_version": promoted_version,
        "idempotent_replay_version": replay.get("promoted_version"),
        "regression_run_id": reg.get("run_id"),
        "regression_verdict": reg.get("test_verdict"),
        "regression_worker": worker_reg,
        "rolled_back_status": rolled.get("status"),
        "restored_version": restored,
        "latest_after_rollback": after_rollback.get("latest_version"),
        "post_rollback_run_id": ver.get("run_id"),
        "post_rollback_verdict": ver.get("test_verdict"),
        "fault_cleared": cleared,
    }


# ------------------------------------------------------------------ helpers
def _project_count(teamflow: str) -> int:
    state = _teamflow_call(teamflow, "GET", "/api/state/projects") or {}
    return int(state.get("count") or 0)


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


def _verify_evidence(api: Api, run_id: str, *, expect_steps: bool = False) -> dict[str, Any]:
    """逐条验证真实 Evidence（服务端存储完整性），不再只统计历史状态。

    RUN-007-FINALIZATION-2（WP2/C4）：
    - 每条 Evidence 调用 `POST /evidence/{id}/verify`，由服务端读取持久化记录与真实内容重算摘要；
    - 有附件时下载字节重算 SHA-256，并与记录摘要比对；
    - 派生证据校验来源引用（raw_ref / 同工作区 / derivation_rule）；
    - 统计 PASS / FAIL / NOT_RUN，NOT_RUN 绝不计入 PASS。
    """
    _assert(bool(run_id), "run_id 为空，无法核对证据")
    evds = api.call("GET", f"/runs/{run_id}/evidence", timeout=120) or []
    steps = api.call("GET", f"/runs/{run_id}/steps", timeout=120) or []
    events = api.call("GET", f"/runs/{run_id}/events?limit=1000", timeout=120) or []
    ids = [e.get("evidence_id") for e in evds]
    _assert(len(evds) > 0, f"Run {run_id} 没有任何 Evidence")
    _assert(all(bool(i) for i in ids), "存在空 evidence_id")
    _assert(len(set(ids)) == len(ids), "evidence_id 重复")
    bad_owner = [e.get("evidence_id") for e in evds if e.get("run_id") not in (None, run_id)]
    _assert(not bad_owner, f"Evidence 归属错误：{bad_owner[:5]}")

    kinds: dict[str, int] = {}
    sources: dict[str, int] = {}
    results: list[dict[str, Any]] = []
    derived_checks: list[dict[str, Any]] = []
    for e in evds:
        eid = str(e.get("evidence_id"))
        kinds[str(e.get("kind"))] = kinds.get(str(e.get("kind")), 0) + 1
        src = str(e.get("source_type"))
        sources[src] = sources.get(src, 0) + 1
        try:
            verified = api.call("POST", f"/evidence/{eid}/verify", {}, timeout=120)
            storage = verified.get("storage_integrity") or {}
            status = str(verified.get("integrity_check") or "NOT_RUN")
        except Exception as exc:  # noqa: BLE001
            verified, storage, status = {}, {}, "FAIL"
            verified["error"] = str(exc)[:200]
        checks = storage.get("checks") or {}
        attachment: dict[str, Any] = {}
        if e.get("attachment_ref"):
            try:
                data, headers = _download(api, f"/evidence/{eid}/download")
                actual = hashlib.sha256(data).hexdigest()
                attachment = {
                    "artifact_id": e.get("attachment_ref"),
                    "bytes": len(data),
                    "sha256": actual,
                    "header_sha256": headers.get("X-TraceGuard-SHA256", ""),
                    "recorded_sha256": e.get("attachment_sha256", ""),
                    "match": bool(
                        actual
                        and actual
                        == (
                            headers.get("X-TraceGuard-SHA256", "") or e.get("attachment_sha256", "")
                        )
                    ),
                }
            except Exception as exc:  # noqa: BLE001
                attachment = {"error": str(exc)[:200], "match": False}
        if e.get("kind") == "derived":
            derived_checks.append(
                {
                    "evidence_id": eid,
                    "raw_ref": e.get("raw_ref"),
                    "source_exists": checks.get("derived_source_exists"),
                    "source_is_raw": checks.get("derived_source_is_raw"),
                    "same_workspace": checks.get("derived_source_same_workspace"),
                    "rule_recorded": checks.get("derivation_rule_recorded"),
                }
            )
        results.append(
            {
                "evidence_id": eid,
                "kind": e.get("kind"),
                "source_type": e.get("source_type"),
                "status": status,
                "reasons": storage.get("reasons") or [],
                "stored_content_match": checks.get("stored_content_match"),
                "redacted": bool(e.get("redacted")),
                "source_sha256_recorded": bool(e.get("sha256")),
                "stored_content_sha256_recorded": bool(e.get("stored_content_sha256")),
                "attachment": attachment,
            }
        )

    pass_n = sum(1 for r in results if r["status"] == "PASS")
    fail_n = sum(1 for r in results if r["status"] == "FAIL")
    not_run_n = sum(1 for r in results if r["status"] == "NOT_RUN")
    with_attachment = [r for r in results if r["attachment"]]
    attach_ok = sum(1 for r in with_attachment if r["attachment"].get("match"))
    source_recheckable = sum(
        1 for r in results if r["source_sha256_recorded"] and not r["redacted"]
    )
    derived_ok = sum(
        1
        for d in derived_checks
        if d["source_exists"] and d["source_is_raw"] and d["same_workspace"] and d["rule_recorded"]
    )

    _assert(
        fail_n == 0,
        f"存储完整性校验 FAIL：{[r['evidence_id'] for r in results if r['status'] == 'FAIL'][:5]}",
    )
    _assert(pass_n > 0, "没有任何 Evidence 通过服务端存储完整性校验")
    _assert(not_run_n == 0, f"{not_run_n} 条 Evidence 无法证明完整性（NOT_RUN 不计 PASS）")
    _assert(len(with_attachment) == attach_ok, "附件字节摘要与记录不一致")
    _assert(len(derived_checks) == derived_ok, "派生证据来源引用不完整")
    if expect_steps:
        _assert(len(steps) > 0, f"Run {run_id} 没有任何 Step 记录")
    _assert(len(events) > 0, f"Run {run_id} 没有任何 Event 记录")
    return {
        "run_id": run_id,
        "evidence_count": len(evds),
        "evidence_ids": ids,
        "evidence_kinds": kinds,
        "evidence_sources": sources,
        "verification": {
            "total": len(results),
            "pass": pass_n,
            "fail": fail_n,
            "not_run": not_run_n,
            "service_side": True,
            "per_evidence": results,
        },
        "attachment_verification": {
            "with_attachment": len(with_attachment),
            "bytes_sha256_match": attach_ok,
        },
        "source_digest": {
            "recorded": len(results),
            "recheckable_without_original": source_recheckable,
            "redacted_original_not_retained": sum(1 for r in results if r["redacted"]),
            "note": "原始来源摘要仅可对未脱敏证据直接复核；脱敏证据以持久化内容摘要为准",
        },
        "derived_evidence": {
            "count": len(derived_checks),
            "source_integrity_ok": derived_ok,
            "details": derived_checks,
        },
        "step_count": len(steps),
        "event_count": len(events),
    }


def _download(api: Api, path: str, timeout: int = 120) -> tuple[bytes, dict[str, str]]:
    req = urllib.request.Request(api.base + path, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            return resp.read(), {k: v for k, v in resp.headers.items()}
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"GET {path} -> {exc.code}") from exc


def _verify_graph_binds_evidence(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    evidence_ids: list[str],
    diag: dict[str, Any],
) -> dict[str, Any]:
    """证据图核对：真实 Evidence 必须有对应节点，且与 Run/来源存在真实边。"""
    _assert(bool(nodes), "证据图没有节点")
    _assert(bool(edges), "证据图没有边")
    node_by_id = {n.get("node_id"): n for n in nodes}
    expected = {f"node_evd_{eid}": eid for eid in evidence_ids}
    missing = [eid for nid, eid in expected.items() if nid not in node_by_id]
    _assert(not missing, f"证据图中缺少 Evidence 节点：{missing[:5]}")

    linked: dict[str, list[str]] = {}
    for edge in edges:
        src = str(edge.get("src") or edge.get("source_node_id") or "")
        dst = str(edge.get("dst") or edge.get("target_node_id") or "")
        if src in expected or dst in expected:
            linked.setdefault(src if src in expected else dst, []).append(f"{src}->{dst}")
    unlinked = [nid for nid in expected if nid not in linked]
    _assert(not unlinked, f"Evidence 节点没有关系边：{unlinked[:5]}")

    diag_id = str(diag.get("diagnosis_id") or "")
    source_node = f"node_diag_{diag_id}" if diag_id else ""
    _assert(bool(source_node) and source_node in node_by_id, "证据图缺少诊断来源节点")
    return {
        "evidence_nodes": len(expected),
        "evidence_nodes_linked": len(linked),
        "source_node": source_node,
        "total_nodes": len(nodes),
        "total_edges": len(edges),
        "node_types": sorted({str(n.get("node_type")) for n in nodes}),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://127.0.0.1:8020")
    ap.add_argument("--out", default="docs/assets/run007/e2e-worker-run007.json")
    ap.add_argument("--scenarios", default="ABCD")
    ap.add_argument("--teamflow", default="http://127.0.0.1:8021")
    ap.add_argument("--mode", default="worker", choices=["worker", "inline"])
    ap.add_argument("--run-timeout", type=float, default=300.0)
    args = ap.parse_args(argv)

    api = Api(args.api)
    ex = Executor(api, mode=args.mode, timeout_s=args.run_timeout)
    want = set(args.scenarios.upper())
    report: dict[str, Any] = {
        "api": args.api,
        "teamflow": args.teamflow,
        "mode": args.mode,
        "scenarios": {},
    }
    started = time.time()

    print(f"RUN-007 端到端业务闭环（mode={args.mode}）")
    step("0 健康检查", lambda: api.call("GET", "/system/health"))
    queue = api.call("GET", "/queue/stats")
    inline_running = bool(((queue or {}).get("worker") or {}).get("inline_running"))
    report["api_inline_worker"] = inline_running
    if args.mode == "worker":
        step(
            "0 API 不得启用内嵌 Worker（否则无法证明是独立 Worker 执行）",
            lambda: _assert(
                not inline_running, "API 内嵌 Worker 正在运行：请用 TG_INLINE_WORKER=0"
            ),
        )
    ex._attach()
    browser = step(
        "0 浏览器可用性", lambda: api.call("GET", "/system/browser?probe=true", timeout=90)
    )
    if not browser.get("available"):
        print("浏览器不可用，终止：", browser.get("error"))
        return 2
    step("0 资产种子", lambda: api.call("POST", "/system/seed", timeout=300))

    if "A" in want:
        report["scenarios"]["A"] = scenario_a(api, args.teamflow, ex)
    if "B" in want:
        b = scenario_b(api, ex, args.teamflow)
        report["scenarios"]["B"] = b
        if "C" in want:
            report["scenarios"]["C"] = scenario_c(api, b["run_id"], b["diagnosis_id"])
        if "D" in want:
            report["scenarios"]["D"] = scenario_d(api, args.teamflow, ex)
    elif "D" in want:
        report["scenarios"]["D"] = scenario_d(api, args.teamflow, ex)

    report["runs"] = RUNS
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
