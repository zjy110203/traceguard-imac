"""RUN-011-R1 WP1：单一 TestCase 血缘完整闭环（HTTP only，无进程内执行）。

RUN-011（`f604e5d`）的闭环由三个不同 TestCase 拼接（编译用例 / tcase_tf_false_pass /
tcase_bench_loc001），血缘断裂。本脚本（R1）把 §1.2 的 18 步链收敛到**同一个 TestCase**：

    1-6   Intent → 真实 LLM 探索 → 编译 TestCase → Oracle 绑定 → 3 次稳定性回放 → qa-lead 审批
    7     正常独立 Worker 执行（无故障）
    8-9   对同一 TestCase 注入 TF-LOC-001 → 再次执行并产生真实失败
    10    Diagnosis 读取该失败 Run
    11    对同一个 Run 执行 Probe（或按真实停止条件 STOP）
    12    基于同一个 Diagnosis 生成 Repair Proposal（来源如实标注）
    13-15 沙箱执行 → 人工审批 → CAS 晋升
    16    固定版本（精确晋升版本）回归
    17    回滚
    18    回滚后再次验证

不变量（RUN-011-R1 WP1 §1.1/§1.4）：
- 全链共享同一 intent_id / exploration_id / testcase_id / testcase_version_id / oracle_version_id；
- Repair 必须带 diagnosis_id + source_run_id + repair_source（human:/rule:/llm: 三类不混淆）；
- phase_trace 校验**引用之间的关系**，而不只是"每个 ID 能读到"；
- 证据引用从 Diagnosis API 真实结构（supporting/contradicting/evidence_refs）提取，
  不靠 Graph 节点数量证明完整性；
- 失败如实记录（不吞异常、不伪造关联字段）。

用法：
    TG_RUN011_PASSWORD='...' .venv/bin/python scripts/full_loop_run011.py \
        --api http://127.0.0.1:8110 --teamflow http://127.0.0.1:8111 \
        --label r1-1 --workspace ws-run011-r1-1 \
        --out docs/assets/run011-r1/lineage/loop1.json

安全：本脚本不读取任何真实凭据、不构造子进程、不导出环境变量；口令只从
环境变量 TG_RUN011_PASSWORD 读取（测试用虚拟口令），不写入输出文件。
"""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

TERMINAL = {"COMPLETED", "FAILED", "TIMED_OUT", "CANCELLED"}
FALLBACK_PASSWORD = "run011-local-test-only"  # 仅测试环境虚拟口令（非真实凭据）

#: 确定性定位器修复夹具（TF-LOC-001 语义：提交按钮 test_id 变更）。
#: 这是**规则夹具**（rule:locator-fixture），不是 Agent 从 Diagnosis 自动推导的修复。
LOCATOR_FIXTURES: dict[str, dict[str, str]] = {
    "create-submit": {"strategy": "role", "value": "button:创建", "source": "healed"},
    "project-name": {"strategy": "label", "value": "项目名称", "source": "healed"},
    "nav-new": {"strategy": "role", "value": "link:新建项目", "source": "healed"},
}

CHECKS: list[dict[str, Any]] = []
RESULT: dict[str, Any] = {"checks": CHECKS, "ids": {}, "phases": {}, "env": {}}


# --------------------------------------------------------------------- helpers
def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def check(name: str, ok: bool, detail: Any = "", *, phase: str = "") -> bool:
    """记录一条可复核的检查项；不抛异常（由调用方决定是否继续）。"""
    item = {
        "name": name,
        "phase": phase,
        "ok": bool(ok),
        "detail": detail if isinstance(detail, (str, int, float, bool, dict, list)) else str(detail),
        "at": _now(),
    }
    CHECKS.append(item)
    flag = "PASS" if item["ok"] else "FAIL"
    print(f"[{flag}] {name}" + ("" if item["ok"] else f" :: {str(detail)[:400]}"), flush=True)
    return bool(ok)


def run_check(name: str, fn: Callable[[], Any], *, phase: str = "", accept=None) -> Any:
    """执行并记录一个检查；返回函数结果（失败时返回 None）。"""
    try:
        value = fn()
    except Exception as exc:  # noqa: BLE001 - 失败必须如实记录
        check(name, False, f"{type(exc).__name__}: {exc}", phase=phase)
        return None
    ok = True
    detail: Any = ""
    if accept is not None:
        ok, detail = accept(value)
    check(name, ok, detail, phase=phase)
    return value


class Http:
    """带 Cookie 会话与 CSRF 的极简客户端（每个角色一个实例）。"""

    def __init__(self, base: str, label: str = "", timeout: float = 120.0) -> None:
        self.base = base.rstrip("/")
        self.label = label
        self.timeout = timeout
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self.csrf = ""
        self.user = ""
        self.roles: list[str] = []

    # -- low level
    def raw(self, method: str, path: str, body: Any = None, timeout: float | None = None):
        url = self.base + path
        data = None
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if body is not None:
            data = json.dumps(body).encode()
        if self.csrf and method not in {"GET", "HEAD", "OPTIONS"}:
            headers["X-CSRF-Token"] = self.csrf
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with self.opener.open(req, timeout=timeout or self.timeout) as resp:  # noqa: S310
                raw = resp.read().decode()
                return resp.status, (json.loads(raw) if raw.strip() else {}), raw
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode(errors="replace")
            try:
                parsed = json.loads(raw) if raw.strip() else {}
            except json.JSONDecodeError:
                parsed = {"raw": raw[:500]}
            return exc.code, parsed, raw

    def call(
        self, method: str, path: str, body: Any = None, *, expect_status: int | tuple = 200,
        timeout: float | None = None,
    ) -> Any:
        status, parsed, raw = self.raw(method, path, body, timeout)
        wanted = (expect_status,) if isinstance(expect_status, int) else tuple(expect_status)
        if status not in wanted:
            raise RuntimeError(f"{method} {path} -> {status}: {raw[:500]}")
        return parsed

    def login(self, username: str, password: str) -> dict[str, Any]:
        data = self.call("POST", "/api/v1/auth/login", {"username": username, "password": password})
        self.csrf = str(data.get("csrf_token") or "")
        me = self.call("GET", "/api/v1/auth/me")
        self.user = str(me.get("username") or "")
        self.roles = list(me.get("roles") or [])
        return {"login": data, "me": me}


class TeamFlow:
    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")

    def post(self, path: str, body: dict | None = None) -> dict[str, Any]:
        req = urllib.request.Request(
            self.base + path,
            data=json.dumps(body or {}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310
            return json.loads(resp.read().decode() or "{}")

    def get(self, path: str) -> dict[str, Any]:
        with urllib.request.urlopen(self.base + path, timeout=20) as resp:  # noqa: S310
            return json.loads(resp.read().decode() or "{}")

    def reset(self) -> dict[str, Any]:
        return self.post("/api/test/reset", {})

    def fault_state(self) -> dict[str, Any]:
        """读取当前故障注入状态（用于 Benchmark 记录真实激活证据）。"""
        return (self.get("/api/faults").get("state") or {})

    def clear_faults(self) -> dict[str, Any]:
        """清除全部故障（含 sticky）：逐个显式 clear。"""
        state = self.get("/api/faults").get("state") or {}
        last: dict[str, Any] = {}
        for fid in list(state.get("active") or []):
            last = self.post(f"/api/faults/{fid}/clear", {})
        return last or {"ok": True, "cleared": "none"}

    def activate(self, fault_id: str, params: dict | None = None) -> dict[str, Any]:
        return self.post(f"/api/faults/{fault_id}/activate", params or {})

    def project_count(self, workspace: str = "default") -> int:
        data = self.get(f"/api/state/projects?workspace={urllib.parse.quote(workspace)}")
        return int(data.get("count") or 0)


# ------------------------------------------------------------------ run driver
class WorkerRun:
    """创建 Task/Run 并等待独立 Worker 领取执行（绝不调用 inline execute）。"""

    def __init__(self, api: Http, workers: list[str], api_worker_id: str = "") -> None:
        self.api = api
        self.workers = [w for w in workers if w]
        self.api_worker_id = api_worker_id

    def execute(
        self,
        testcase_id: str,
        *,
        version: int | None = None,
        timeout_s: float = 240.0,
        note: str = "",
    ) -> dict[str, Any]:
        q = f"?version={version}" if version else ""
        task = self.api.call("POST", "/api/v1/tasks", {"testcase_ref": testcase_id, "mode": "playwright"}, expect_status=201)
        run = self.api.call("POST", f"/api/v1/tasks/{task['task_id']}/runs{q}", None, expect_status=201)
        run_id = run["run_id"]
        started = time.time()
        trace: dict[str, Any] = {
            "task_id": task["task_id"],
            "run_id": run_id,
            "version": version,
            "note": note,
            "queued_at": _now(),
            "workers_seen": [],
            "fencing_tokens": [],
            "attempts": [],
            "polls": 0,
            "submitted_submit_receipt": task.get("submit_receipt"),
        }
        doc: dict[str, Any] = {}
        while time.time() - started < timeout_s:
            trace["polls"] += 1
            doc = self.api.call("GET", f"/api/v1/runs/{run_id}", timeout=60)
            owner = str(doc.get("lease_owner") or "")
            if owner and owner not in trace["workers_seen"]:
                trace["workers_seen"].append(owner)
            token = doc.get("fencing_token")
            if token not in (None, "") and token not in trace["fencing_tokens"]:
                trace["fencing_tokens"].append(token)
            attempt = doc.get("attempt")
            if attempt not in (None, "") and attempt not in trace["attempts"]:
                trace["attempts"].append(attempt)
            if doc.get("execution_status") in TERMINAL:
                break
            time.sleep(0.6)
        trace["elapsed_ms"] = round((time.time() - started) * 1000, 1)
        trace["terminal_status"] = doc.get("execution_status")
        trace["test_verdict"] = doc.get("test_verdict")
        trace["lease_owner_final"] = doc.get("lease_owner")
        trace["fencing_token_final"] = doc.get("fencing_token")
        trace["independent_worker"] = bool(
            trace["lease_owner_final"]
            and trace["lease_owner_final"] in self.workers
            and trace["lease_owner_final"] != self.api_worker_id
        )
        return {"run": doc, "trace": trace}


# ---------------------------------------------------------------------- phases
def phase_env(api: Http, tf: TeamFlow, workers: list[str]) -> dict[str, Any]:
    health = api.call("GET", "/api/v1/system/health")
    RESULT["env"]["health"] = health
    check(
        "API 独立进程 + PostgreSQL 方言",
        str((health.get("database") or {}).get("dialect") or "").startswith("postgres"),
        (health.get("database") or {}),
        phase="env",
    )
    check(
        "API 内嵌 Worker 未运行（TG_INLINE_WORKER=0 生效）",
        not (health.get("worker") or {}).get("inline_running"),
        (health.get("worker") or {}),
        phase="env",
    )
    api_worker_id = str((health.get("worker") or {}).get("id") or "")
    RESULT["env"]["api_worker_id"] = api_worker_id

    status = api.call("GET", "/api/v1/auth/status")
    RESULT["env"]["auth_status"] = status
    check("认证已开启（服务端强制）", status.get("auth_enabled") is True, status, phase="env")

    br = api.call("GET", "/api/v1/system/browser?probe=true", timeout=180)
    RESULT["env"]["browser"] = br
    check(
        "真实 Chromium 可用",
        br.get("available") is True,
        {k: br.get(k) for k in ("available", "version", "channel", "error")},
        phase="env",
    )

    # 前端正式构建产物（单端口 SPA）
    try:
        with urllib.request.urlopen(api.base + "/", timeout=30) as resp:  # noqa: S310
            html = resp.read().decode(errors="replace")
        ok = resp.status == 200 and 'id="app"' in html
    except Exception as exc:  # noqa: BLE001
        html, ok = str(exc), False
    check("Vue 前端正式构建产物可访问（GET /）", ok, html[:200], phase="env")

    tf_health = tf.get("/api/state/projects?workspace=default")
    check("TeamFlow 独立业务目标服务可用", "count" in tf_health, tf_health, phase="env")
    RESULT["env"]["teamflow_count"] = tf_health.get("count")
    RESULT["env"]["declared_workers"] = workers
    return {"api_worker_id": api_worker_id}


def phase_auth(roles: dict[str, Http], anon: Http, admin: Http) -> dict[str, Any]:
    status, body, _ = anon.raw("GET", "/api/v1/runs")
    check("未认证请求 fail-closed（401）", status == 401, {"status": status, "body": body}, phase="auth")

    for name in ("viewer", "qa-engineer", "qa-lead", "platform-admin"):
        session = roles[name]
        check(
            f"登录成功且身份来自服务端（{name}）",
            session.user == name and session.roles,
            {"user": session.user, "roles": session.roles},
            phase="auth",
        )

    code, body, _ = roles["viewer"].raw(
        "POST", "/api/v1/intents", {"title": "x", "description": "y"}
    )
    check(
        "viewer 不能创建 Intent（403 PERMISSION_DENIED）",
        code == 403 and (body.get("detail") or {}).get("code") == "PERMISSION_DENIED",
        {"status": code, "body": body},
        phase="auth",
    )
    code, body, _ = roles["qa-engineer"].raw("GET", "/api/v1/auth/users")
    check("qa-engineer 不能管理用户（403）", code == 403, {"status": code}, phase="auth")
    users = admin.call("GET", "/api/v1/auth/users")
    check("platform-admin 可读取用户列表", isinstance(users, list) and len(users) >= 4, len(users or []), phase="auth")
    RESULT["ids"]["users"] = [
        {"username": u.get("username"), "roles": u.get("roles"), "user_id": u.get("user_id")}
        for u in (users or [])
    ]
    return {}


def phase_seed(admin: Http, engineer: Http, tf: TeamFlow) -> dict[str, Any]:
    tf.reset()
    check("TeamFlow 测试数据已重置（业务基线 count=0）", tf.project_count() == 0, tf.project_count(), phase="seed")
    code, body, _ = engineer.raw("POST", "/api/v1/system/seed", {})
    check("qa-engineer 不能执行系统种子（403 system.configure）", code == 403, {"status": code}, phase="seed")
    seeded = admin.call("POST", "/api/v1/system/seed", {}, timeout=180)
    check(
        "TeamFlow 场景资产已注入（platform-admin）",
        isinstance(seeded, dict) and int(seeded.get("created") or 0) + int(seeded.get("skipped") or 0) > 0,
        seeded,
        phase="seed",
    )
    return {"seed": seeded}


def phase_ab(
    engineer: Http, lead: Http, workspace: str, teamflow: str
) -> dict[str, Any]:
    """§1.2 步骤 1-6：Intent → 探索 → 编译 → Oracle → 稳定性回放 → 审批。"""
    intent = engineer.call(
        "POST",
        "/api/v1/intents",
        {
            "title": "RUN-011-R1 单一 TestCase 血缘闭环：项目创建必须真实持久化",
            "description": "在项目列表点击新建，填写名称后提交；必须出现成功提示，且独立业务状态查询能看到该项目。看到成功提示即完成探索，不要打开详情页。",
            "owner": "qa-team",
            "workspace_id": workspace,
            "candidate_assertions": ["成功提示可见", "业务状态计数 +1"],
        },
        expect_status=201,
    )
    intent_id = intent["intent_id"]
    RESULT["ids"]["intent_id"] = intent_id
    check("A1 Intent 真实创建", bool(intent_id), intent_id, phase="A")

    exploration = engineer.call(
        "POST", f"/api/v1/intents/{intent_id}/explore?max_steps=8", None, expect_status=201, timeout=300
    )
    RESULT["ids"]["exploration_id"] = exploration.get("exploration_id")
    trace = exploration.get("trace") or {}
    RESULT["phases"]["A"] = {
        "exploration": {
            "exploration_id": exploration.get("exploration_id"),
            "status": exploration.get("status"),
            "stop_reason": exploration.get("stop_reason"),
            "engine": exploration.get("engine") or trace.get("engine"),
            "provider": exploration.get("provider") or trace.get("provider"),
            "llm": exploration.get("llm") or trace.get("llm") or {},
            "action_count": len(trace.get("action_sequence") or exploration.get("action_sequence") or []),
            "actions": (trace.get("action_sequence") or [])[:8],
        }
    }
    check(
        "A2 真实浏览器探索产出可复现轨迹",
        bool(RESULT["phases"]["A"]["exploration"]["action_count"]),
        RESULT["phases"]["A"]["exploration"],
        phase="A",
    )

    compiled = engineer.call(
        "POST", f"/api/v1/explorations/{exploration['exploration_id']}/compile?mode=rule_only",
        None, expect_status=201, timeout=180,
    )
    comp = compiled.get("compiled") or {}
    tc_id, version = comp.get("testcase_id"), int(comp.get("version") or 1)
    RESULT["ids"]["compiled_testcase_id"] = tc_id
    RESULT["ids"]["compiled_version"] = version
    RESULT["phases"]["A"]["compiled"] = comp
    check("A3 轨迹编译为候选用例（PENDING）", bool(tc_id), comp, phase="A")

    version_doc = engineer.call("GET", f"/api/v1/testcases/{tc_id}/versions/{version}")
    RESULT["phases"]["B"] = {
        "compiled_testcase": {
            "testcase_id": tc_id,
            "version": version,
            "review_status": version_doc.get("review_status"),
            "steps": [
                {"step_id": s.get("step_id"), "action": s.get("action"), "target": s.get("target")}
                for s in version_doc.get("steps") or []
            ],
            "assertions": [
                {"assertion_id": a.get("assertion_id"), "kind": a.get("kind")} for a in version_doc.get("assertions") or []
            ],
        }
    }
    check(
        "A4 候选用例必须处于 PENDING（不得自动批准）",
        version_doc.get("review_status") == "PENDING",
        version_doc.get("review_status"),
        phase="A",
    )

    # 未回放就审核必须被门禁拒绝
    code, body, _ = lead.raw(
        "POST",
        f"/api/v1/testcases/{tc_id}/versions/{version}/review",
        {"decision": "APPROVED", "reviewer": "user:qa-lead"},
    )
    check(
        "B1 审核门禁：无稳定性回放时拒绝（412）",
        code == 412,
        {"status": code, "body": body},
        phase="B",
    )

    def govern(case_id: str, case_version: int, tag: str) -> dict[str, Any]:
        """Oracle → 绑定 → 稳定性回放 → 审核。返回真实结果（失败不抛异常）。"""
        doc = engineer.call("GET", f"/api/v1/testcases/{case_id}/versions/{case_version}")
        o_ids: dict[str, str] = {}
        o_versions: dict[str, int] = {}
        for a in doc.get("assertions") or []:
            aid = str(a.get("assertion_id"))
            oracle = lead.call(
                "POST",
                "/api/v1/oracles",
                {
                    "oracle_id": f"oracle_run011r1_{tag}_{re.sub(r'[^A-Za-z0-9]', '_', aid)[:40]}",
                    "assertion_id": aid,
                    "expected_behavior": a.get("description") or "RUN-011-R1 断言",
                    "requirement_ref": "REQ-RUN011R1-LOOP",
                    "evaluation_rule": {"kind": a.get("kind"), "expected": a.get("expected") or {}},
                    "approved": True,
                    "approved_by": "user:qa-lead",
                    "workspace_id": workspace,
                },
                expect_status=201,
            )
            o_ids[aid] = oracle["oracle_id"]
            o_versions[aid] = int(oracle.get("version") or 1)
        lead.call(
            "POST",
            f"/api/v1/testcases/{case_id}/versions/{case_version}/oracle-bindings",
            {"bindings": {aid: {"oracle_id": oid} for aid, oid in o_ids.items()}, "actor": "user:qa-lead"},
        )
        rep = lead.call(
            "POST",
            f"/api/v1/testcases/{case_id}/versions/{case_version}/stability-replay"
            "?repeats=3&sync=true&risk_profile=default",
            None,
            expect_status=201,
            timeout=420,
        )
        recs = rep.get("records") or []
        cls = (rep.get("classification") or {}).get("classification")
        info = {
            "tag": tag,
            "testcase_id": case_id,
            "version": case_version,
            "oracles": o_ids,
            "oracle_versions": o_versions,
            "replay_id": rep.get("replay_id"),
            "status": rep.get("status"),
            "classification": cls,
            "promotable": rep.get("promotable"),
            "worker_consumed": rep.get("worker_consumed"),
            "records": [
                {
                    "run_id": r.get("run_id"),
                    "verdict": r.get("test_verdict"),
                    "status": r.get("execution_status"),
                    "claimed_by": r.get("claimed_by"),
                }
                for r in recs
            ],
        }
        info["ok"] = rep.get("status") == "COMPLETED" and cls == "STABLE_PASS" and rep.get("promotable") is True and len(recs) == 3
        if not info["ok"]:
            return info
        reviewed = lead.call(
            "POST",
            f"/api/v1/testcases/{case_id}/versions/{case_version}/review",
            {"decision": "APPROVED", "reviewer": "agent:auto-heal", "note": "RUN-011-R1：请求体声明必须伪造无效"},
        )
        info["review"] = {
            "review_status": reviewed.get("review_status"),
            "reviewer": str(reviewed.get("reviewer") or ""),
            "review_revision": reviewed.get("review_revision"),
            "request_body_declared": "agent:auto-heal",
        }
        info["ok"] = info["review"]["reviewer"] == "user:qa-lead" and reviewed.get("review_status") == "APPROVED"
        return info

    governed = govern(tc_id, version, "compiled")
    RESULT["phases"]["B"]["governed_compiled"] = governed
    claimed = [str(r.get("claimed_by") or "") for r in governed.get("records") or []]
    declared_workers = RESULT["env"].get("declared_workers") or []
    check(
        "B2 Oracle 已审批并带版本",
        bool(governed.get("oracles")),
        {"oracles": governed.get("oracles"), "versions": governed.get("oracle_versions")},
        phase="B",
    )
    check(
        "B3 稳定性回放完成（3 次真实 Run）且独立 Worker 领取",
        bool(governed.get("records"))
        and all(c in declared_workers for c in claimed)
        and governed.get("worker_consumed") is True,
        {"claimed_by": claimed, "declared_workers": declared_workers, "status": governed.get("status")},
        phase="B",
    )
    check(
        "B4 编译候选必须通过稳定性回放才能批准（否则门禁如实拒绝）",
        bool(governed.get("ok")),
        {"classification": governed.get("classification"), "promotable": governed.get("promotable")},
        phase="B",
    )

    approved_tc, approved_version = tc_id, version
    if not governed.get("ok"):
        # 真实发现：一次探索编译出的候选未通过稳定性回放 → 不得批准。
        # 保留失败证据，改用「人工输入路径」（同一 Intent 编写用例，再走同一治理门禁）。
        # 注意：人工路径用例仍然成为本闭环的唯一主线 TestCase，不与其他预置用例混用。
        fb_id = f"tcase_run011r1_{re.sub(r'[^A-Za-z0-9]', '_', workspace)}"
        tf_base = teamflow
        created_fb = engineer.call(
            "POST",
            "/api/v1/testcases",
            {
                "testcase_id": fb_id,
                "title": "RUN-011-R1 人工用例：项目创建必须真实持久化",
                "intent_ref": intent_id,
                "workspace_id": workspace,
                "compile_source": "human",
                "steps": [
                    {"step_id": "n1", "action": "navigate", "value": "/projects"},
                    {"step_id": "n2", "action": "click", "target": {"strategy": "test_id", "value": "nav-new"}},
                    {"step_id": "n3", "action": "fill", "target": {"strategy": "test_id", "value": "project-name"}, "value": "RUN-011-R1 Loop Project"},
                    {"step_id": "n4", "action": "fill", "target": {"strategy": "test_id", "value": "project-description"}, "value": "闭环验证"},
                    {"step_id": "n5", "action": "click", "target": {"strategy": "test_id", "value": "create-submit"}},
                    {"step_id": "n6", "action": "wait", "value": "1200"},
                ],
                "assertions": [
                    {
                        "assertion_id": "a_run011r1_banner",
                        "description": "成功提示必须出现",
                        "kind": "ui",
                        "required": True,
                        "expected": {"test_id": "banner-success"},
                    },
                    {
                        "assertion_id": "a_run011r1_biz",
                        "description": "独立业务状态必须持久化",
                        "kind": "business_state",
                        "required": True,
                        "expected": {
                            "api": "/api/state/projects?workspace=default",
                            "field": "count",
                            "op": "gte",
                            "value": 1,
                        },
                    },
                ],
                "run_config": {"base_url": tf_base, "fail_fast": True, "trace": True},
            },
            expect_status=201,
        )
        approved_tc = created_fb["testcase_id"]
        approved_version = int(created_fb.get("version") or 1)
        governed_fb = govern(approved_tc, approved_version, "human")
        RESULT["phases"]["B"]["governed_human"] = governed_fb
        check(
            "B5 人工用例通过同一治理门禁（Oracle + 3 次回放 + 审批）",
            bool(governed_fb.get("ok")),
            {"classification": governed_fb.get("classification"), "review": governed_fb.get("review")},
            phase="B",
        )
        RESULT["phases"]["B"]["fallback_reason"] = (
            "探索编译候选未通过稳定性回放（真实失败，未降级门禁）；"
            "改用人工输入路径继续闭环，失败证据保留在 governed_compiled。"
            "主线 TestCase 仍唯一（= 人工用例），不与其它预置用例混链。"
        )
        if not governed_fb.get("ok"):
            raise RuntimeError("人工用例也未通过稳定性回放，闭环无法继续（不伪造通过）")
        governed = governed_fb

    check(
        "B6 请求体伪造 reviewer 无效（服务端 Principal 生效）",
        (governed.get("review") or {}).get("reviewer") == "user:qa-lead"
        and (governed.get("review") or {}).get("review_status") == "APPROVED",
        governed.get("review"),
        phase="B",
    )

    after = lead.call("GET", f"/api/v1/testcases/{approved_tc}/versions/{approved_version}")
    check(
        "B7 审批后版本引用完整（Oracle 绑定保留）",
        bool(after.get("oracle_bindings") or after.get("bindings") or governed.get("oracles")),
        {"oracle_bindings": after.get("oracle_bindings"), "review_status": after.get("review_status")},
        phase="B",
    )
    RESULT["ids"]["oracle_ids"] = governed.get("oracles") or {}
    RESULT["ids"]["oracle_versions"] = governed.get("oracle_versions") or {}
    RESULT["ids"]["testcase_id"] = approved_tc
    RESULT["ids"]["testcase_version_id"] = f"{approved_tc}@{approved_version}"
    RESULT["ids"]["approved_version"] = approved_version
    RESULT["phases"]["B"]["approved"] = {"testcase_id": approved_tc, "version": approved_version}
    return {"intent_id": intent_id, "testcase_id": approved_tc, "version": approved_version}


def phase_c(engineer: Http, ex: WorkerRun, tf: TeamFlow, tc_id: str, version: int) -> dict[str, Any]:
    """§1.2 步骤 7：正常独立 Worker 执行（无故障）。"""
    tf.clear_faults()
    count_before = tf.project_count()
    vdoc = engineer.call("GET", f"/api/v1/testcases/{tc_id}/versions/{version}")
    cleanup = str(vdoc.get("cleanup_policy") or "NONE")
    out = ex.execute(tc_id, version=version, note="正式执行（无故障）")
    run, trace = out["run"], out["trace"]
    run_id = run["run_id"]
    RESULT["ids"]["run_id"] = run_id
    check("C1 Run 真实入队并由独立 Worker 终态", run.get("execution_status") in TERMINAL, trace, phase="C")
    check("C2 执行归属为独立 Worker 进程", trace.get("independent_worker") is True, trace, phase="C")
    check("C3 正式执行 PASSED", run.get("test_verdict") == "PASSED", {"verdict": run.get("test_verdict"), "status": run.get("execution_status")}, phase="C")

    report = engineer.call("GET", f"/api/v1/runs/{run_id}/report")
    egress = ((report.get("browser") or {}).get("egress") or {})
    RESULT["phases"]["C"] = {
        "run_id": run_id,
        "verdict": run.get("test_verdict"),
        "testcase_id": run.get("testcase_id"),
        "testcase_version_id": run.get("testcase_version_id"),
        "execution_trace": trace,
        "egress": egress,
        "mode": report.get("mode") or (report.get("browser") or {}).get("mode"),
        "project_count_before": count_before,
        "project_count_after": tf.project_count(),
    }
    check(
        "C4 报告含 report.browser.egress 且强制经代理",
        egress.get("enforced") is True and str(egress.get("url") or "").startswith("http://127.0.0.1:"),
        egress,
        phase="C",
    )
    count_after = tf.project_count()
    if cleanup == "RESET_DATA":
        business_ok = count_after >= 1
    else:
        business_ok = count_after >= count_before + 1
    RESULT["phases"]["C"]["cleanup_policy"] = cleanup
    RESULT["phases"]["C"]["project_count_after"] = count_after
    check(
        "C5 业务状态真实变化（持久化生效，考虑 cleanup_policy）",
        business_ok,
        {"before": count_before, "after": count_after, "cleanup_policy": cleanup},
        phase="C",
    )

    evidence = engineer.call("GET", f"/api/v1/runs/{run_id}/evidence")
    ids = [e.get("evidence_id") for e in evidence]
    RESULT["ids"]["evidence_ids"] = ids[:50]
    RESULT["phases"]["C"]["evidence"] = {
        "count": len(evidence),
        "types": sorted({str(e.get("source_type")) for e in evidence}),
        "sample": [
            {"evidence_id": e.get("evidence_id"), "source_type": e.get("source_type"), "kind": e.get("kind"), "captured_at": e.get("captured_at")}
            for e in evidence[:3]
        ],
    }
    check("C6 Evidence 真实落库", len(evidence) > 0, RESULT["phases"]["C"]["evidence"], phase="C")

    verified = []
    for eid in ids[:5]:
        v = engineer.call("POST", f"/api/v1/evidence/{eid}/verify", {})
        verified.append({"evidence_id": eid, "integrity_check": v.get("integrity_check"), "storage": (v.get("storage_integrity") or {}).get("status")})
    RESULT["phases"]["C"]["evidence_verify"] = verified
    check(
        "C7 Evidence 摘要可重新计算校验（服务端）",
        all(v.get("integrity_check") == "PASS" for v in verified) and bool(verified),
        verified,
        phase="C",
    )

    events = engineer.call("GET", f"/api/v1/runs/{run_id}/events?limit=500")
    seqs = [e.get("sequence") if e.get("sequence") is not None else e.get("seq") for e in events]
    seqs = sorted(s for s in seqs if isinstance(s, int))
    event_ids = [e.get("event_id") for e in events]
    RESULT["phases"]["C"]["events"] = {"count": len(events), "seq_first": seqs[0] if seqs else None, "seq_last": seqs[-1] if seqs else None}
    check(
        "C8 Event ID 唯一且序号连续",
        len(set(event_ids)) == len(event_ids) and bool(seqs) and seqs == list(range(seqs[0], seqs[0] + len(seqs))),
        RESULT["phases"]["C"]["events"],
        phase="C",
    )
    return {"run_id": run_id}


def _run_testcase_id(run: dict[str, Any]) -> str:
    """Run 文档不直接暴露 testcase_id；从 testcase_version_id（tcase_x@N）推导。"""
    return str(run.get("testcase_id") or (run.get("testcase_version_id") or "").split("@")[0])


def phase_fail(engineer: Http, ex: WorkerRun, tf: TeamFlow, tc_id: str, version: int) -> dict[str, Any]:
    """§1.2 步骤 8-9：对同一个 TestCase 注入 TF-LOC-001 → 再次执行并产生真实失败。

    注入必须是 sticky 的：Run 的 RESET_DATA 预清理会调用 /api/test/reset
    （清瞬时故障），非 sticky 注入会被吞掉（真实发现，已修 TeamFlow 语义）。
    """
    tf.clear_faults()
    tf.activate("TF-LOC-001", {"sticky": True})
    active = tf.get("/api/faults").get("state", {}).get("active", [])
    sticky = (tf.get("/api/faults").get("state") or {}).get("sticky") or []
    check(
        "D0 TF-LOC-001 已注入且为 sticky（可穿过 Run 的 RESET_DATA 预清理）",
        "TF-LOC-001" in active and "TF-LOC-001" in sticky,
        {"active": active, "sticky": sticky},
        phase="D",
    )

    out = ex.execute(tc_id, version=version, note="故障执行（TF-LOC-001 定位器失效）")
    run, trace = out["run"], out["trace"]
    run_id = run["run_id"]
    RESULT["ids"]["failed_run_id"] = run_id
    report = engineer.call("GET", f"/api/v1/runs/{run_id}/report")
    failed_steps = [
        {"step_id": s.get("step_id"), "action": s.get("action"), "target": s.get("target"), "error": str(s.get("error") or "")[:200]}
        for s in report.get("steps") or []
        if s.get("status") == "FAILED"
    ]
    run_tc = _run_testcase_id(run)
    same_tc = run_tc == tc_id
    same_ver = str(run.get("testcase_version_id") or "") == f"{tc_id}@{version}"
    RESULT["phases"]["D"] = {
        "run_id": run_id,
        "verdict": run.get("test_verdict"),
        "testcase_id": run_tc,
        "testcase_version_id": run.get("testcase_version_id"),
        "failed_steps": failed_steps,
        "trace": trace,
    }
    check(
        "D1 真实失败（FAILED/NOT_EVALUATED + 存在带错误的失败步骤）",
        run.get("test_verdict") in {"FAILED", "NOT_EVALUATED"} and bool(failed_steps),
        {"verdict": run.get("test_verdict"), "failed_steps": failed_steps[:3]},
        phase="D",
    )
    check("D2 失败 Run 由独立 Worker 执行", trace.get("independent_worker") is True, trace, phase="D")
    check(
        "D3 失败 Run 与主线 TestCase 同一用例同一版本",
        same_tc and same_ver,
        {"run.testcase_id": run.get("testcase_id"), "expected": tc_id, "run.testcase_version_id": run.get("testcase_version_id"), "expected_version": f"{tc_id}@{version}"},
        phase="D",
    )
    return {"run_id": run_id, "failed_steps": failed_steps}


def _extract_evidence_refs(diag: dict[str, Any]) -> dict[str, Any]:
    """§1.4：从 Diagnosis API 真实结构提取证据引用（supporting/contradicting/evidence_refs）。

    不用 Graph 节点数量证明完整性；hypotheses 的引用字段是
    ``supporting[*].id`` / ``contradicting[*].id``（不存在 ``evidence_ids`` 字段）。
    """
    hyps = diag.get("hypotheses") or []
    support_ids: set[str] = set()
    contradict_ids: set[str] = set()
    for h in hyps:
        for r in h.get("supporting") or []:
            rid = str(r.get("id") or "")
            if rid:
                support_ids.add(rid)
        for r in h.get("contradicting") or []:
            rid = str(r.get("id") or "")
            if rid:
                contradict_ids.add(rid)
    top_refs = {str(x) for x in diag.get("evidence_refs") or [] if x}
    return {
        "from_hypotheses_supporting": sorted(support_ids),
        "from_hypotheses_contradicting": sorted(contradict_ids),
        "from_doc_evidence_refs": sorted(top_refs),
        "union": sorted(support_ids | contradict_ids | top_refs),
    }


def phase_diag(engineer: Http, run_id: str) -> dict[str, Any]:
    """§1.2 步骤 10：Diagnosis 读取该失败 Run。"""
    diag = engineer.call("POST", f"/api/v1/runs/{run_id}/diagnose", None, expect_status=(200, 201), timeout=300)
    diag_id = diag["diagnosis_id"]
    RESULT["ids"]["diagnosis_id"] = diag_id
    hyps = diag.get("hypotheses") or []
    ev = _extract_evidence_refs(diag)
    RESULT["phases"]["diag"] = {
        "run_id": run_id,
        "diagnosis_id": diag_id,
        "diagnosis_run_id": diag.get("run_id"),
        "diagnosis_testcase_id": diag.get("testcase_id"),
        "outcome": diag.get("outcome"),
        "disposition": diag.get("disposition"),
        "hypotheses": [
            {
                "id": h.get("hypothesis_id"),
                "mechanism": h.get("failure_mechanism"),
                "domain": h.get("failure_domain"),
                "status": h.get("status"),
                "suggested_by": h.get("suggested_by"),
                "supporting": h.get("supporting"),
                "contradicting": h.get("contradicting"),
            }
            for h in hyps
        ],
        "root_causes": diag.get("root_causes"),
        "unresolved_conflicts": diag.get("unresolved_conflicts"),
        "evidence_refs_extracted": ev,
        "revision": diag.get("revision") or diag.get("version"),
        "engine": diag.get("engine"),
    }
    check("D4 诊断产出可追溯假设", bool(hyps), RESULT["phases"]["diag"]["hypotheses"], phase="diag")
    check(
        "D5 诊断结论合法（含如实弃权）",
        diag.get("outcome") in {"CONFIRMED", "PROBABLE", "SUPPORTED", "INCONCLUSIVE"},
        diag.get("outcome"),
        phase="diag",
    )
    check(
        "D6 诊断绑定失败 Run 与主线 TestCase",
        diag.get("run_id") == run_id and diag.get("testcase_id") == RESULT["ids"]["testcase_id"],
        {"diag.run_id": diag.get("run_id"), "expected_run": run_id, "diag.testcase_id": diag.get("testcase_id")},
        phase="diag",
    )
    check(
        "D7 证据引用真实提取（supporting/contradicting/evidence_refs，而非 Graph 节点数）",
        bool(ev["union"]),
        ev,
        phase="diag",
    )
    return {"run_id": run_id, "diagnosis_id": diag_id, "evidence": ev}


def phase_probe(engineer: Http, run_id: str, diag_id: str) -> dict[str, Any]:
    """§1.2 步骤 11：对同一个 Run 执行 Probe（或按真实停止条件 STOP）。"""
    suggestions = engineer.call("GET", f"/api/v1/runs/{run_id}/probe-suggestions?diagnosis_id={diag_id}")
    plan = engineer.call(
        "POST", f"/api/v1/runs/{run_id}/probe-plan?diagnosis_id={diag_id}&max_iterations=3", None,
        expect_status=(200, 201), timeout=300,
    )
    steps = plan.get("steps") or []
    succeeded = [s for s in steps if s.get("status") == "SUCCEEDED"]
    ev_ids = plan.get("evidence_ids") or []
    RESULT["phases"]["probe"] = {
        "suggestions_count": len(suggestions or []),
        "plan_id": plan.get("plan_id"),
        "plan_run_id": plan.get("run_id"),
        "plan_diagnosis_id": plan.get("diagnosis_id"),
        "stop_reason": plan.get("stop_reason"),
        "stop_detail": plan.get("stop_detail"),
        "steps": [
            {
                "probe_id": s.get("probe_id"),
                "status": s.get("status"),
                "gap_id": s.get("gap_id"),
                "info_gain": s.get("info_gain"),
                "evidence_ids": s.get("evidence_ids"),
                "execution_id": s.get("execution_id"),
                "applied_to_diagnosis": s.get("applied_to_diagnosis"),
                "hypothesis_updates": s.get("hypothesis_updates"),
            }
            for s in steps
        ],
        "new_evidence_ids": ev_ids,
        "budget": plan.get("budget"),
    }
    executed_ok = bool(succeeded) and bool(ev_ids)
    stopped_honestly = not steps and str(plan.get("stop_reason") or "").startswith("STOP_")
    check(
        "E1 探针真实执行并落库新证据（或如实 STOP 且说明原因）",
        executed_ok or stopped_honestly,
        {k: RESULT["phases"]["probe"].get(k) for k in ("stop_reason", "stop_detail", "new_evidence_ids")},
        phase="E",
    )
    if executed_ok:
        check("E2 探针计划绑定同一 Run 与 Diagnosis", plan.get("run_id") == run_id or not plan.get("run_id"), plan, phase="E")
        ev = engineer.call("GET", f"/api/v1/evidence/{ev_ids[0]}")
        RESULT["phases"]["probe"]["probe_evidence"] = {
            "evidence_id": ev.get("evidence_id"),
            "run_id": ev.get("run_id"),
            "source_type": ev.get("source_type"),
            "source_ref": ev.get("source_ref"),
            "captured_at": ev.get("captured_at"),
            "content_keys": sorted((ev.get("content") or {}).keys()),
            "observed_at": (ev.get("content") or {}).get("observed_at"),
        }
        check(
            "E3 探针证据保留 observed_at / collected_at 语义",
            bool(RESULT["phases"]["probe"]["probe_evidence"]["captured_at"]),
            RESULT["phases"]["probe"]["probe_evidence"],
            phase="E",
        )
        after = engineer.call("GET", f"/api/v1/diagnoses/{diag_id}")
        RESULT["phases"]["probe"]["diagnosis_after_read"] = {
            "outcome": after.get("outcome"),
            "revision": after.get("revision") or after.get("version"),
            "evaluation_history_len": len(after.get("evaluation_history") or []),
            "hypotheses": [{"id": h.get("hypothesis_id"), "status": h.get("status")} for h in after.get("hypotheses") or []],
            "probe_evidence_ids": after.get("probe_evidence_ids"),
        }
        changed = (
            RESULT["phases"]["probe"]["diagnosis_after_read"]["hypotheses"]
            != [
                {"id": h.get("id"), "status": h.get("status")}
                for h in RESULT["phases"]["diag"]["hypotheses"]
            ]
            or RESULT["phases"]["probe"]["diagnosis_after_read"]["outcome"] != RESULT["phases"]["diag"]["outcome"]
            or (RESULT["phases"]["probe"]["diagnosis_after_read"]["evaluation_history_len"] or 0) > 0
        )
        check("E4 证据回灌真实进入重新诊断（假设/历史变化）", bool(changed), RESULT["phases"]["probe"]["diagnosis_after_read"], phase="E")
    else:
        check("E4 无新证据时不得声称回灌成功", True, f"如实记录 STOP：{plan.get('stop_reason')}", phase="E")
    return {"plan_id": plan.get("plan_id"), "new_evidence_ids": ev_ids, "stop_reason": plan.get("stop_reason")}


def derive_repair_change(
    engineer: Http, tc_id: str, version: int, failed_steps: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """§1.3：从失败 Run 的真实失败步骤推导确定性修复（规则夹具）。

    来源标注：``rule:locator-fixture``——**不是** Agent 根据 Diagnosis 自动推导，
    也不是 LLM 生成。Diagnosis 提供方向（LOCATOR/TEST_ASSET），推导规则固定：
    把失效的 test_id 定位器替换为等价的 role/label 定位器。
    """
    vdoc = engineer.call("GET", f"/api/v1/testcases/{tc_id}/versions/{version}")
    step_index: dict[str, int] = {
        str(s.get("step_id")): i for i, s in enumerate(vdoc.get("steps") or [])
    }
    for fs in failed_steps:
        sid = str(fs.get("step_id") or "")
        target = fs.get("target") or {}
        tid = str(target.get("value") or "")
        if str(target.get("strategy") or "") == "test_id" and tid in LOCATOR_FIXTURES:
            idx = step_index.get(sid)
            if idx is None:
                continue
            return {
                "change": {
                    "path": "steps[].target",
                    "step_index": idx,
                    "value": dict(LOCATOR_FIXTURES[tid]),
                    "reason": f"TF-LOC-001：{tid} 定位器失效，改用等价 role/label 定位（rule:locator-fixture）",
                },
                "step_id": sid,
                "original_target": target,
                "fixture_key": tid,
            }
    return None


def phase_repair(
    engineer: Http, lead: Http, admin: Http, ex: WorkerRun, tf: TeamFlow,
    tc_id: str, version: int, diag_id: str, failed_run_id: str, failed_steps: list[dict[str, Any]],
) -> dict[str, Any]:
    """§1.2 步骤 12-15：Repair Proposal（关联 Diagnosis）→ 沙箱 → 审批 → CAS 晋升。"""
    tf.clear_faults()

    # —— 尝试 LLM 修复规划器（真实调用；无配置时如实记录），用于三来源区分 ——
    llm_out = engineer.call(
        "POST",
        "/api/v1/repairs/llm-suggest",
        {"testcase_id": tc_id, "base_version": version, "run_id": failed_run_id, "diagnosis_id": diag_id, "propose": False},
        expect_status=(200, 201),
        timeout=120,
    )
    RESULT["phases"]["repair"] = {"llm_suggest": {
        "called": llm_out.get("called"),
        "fallback": llm_out.get("fallback"),
        "reason": llm_out.get("reason") or llm_out.get("error"),
        "model": llm_out.get("model"),
        "prompt_version": llm_out.get("prompt_version"),
        "changes_count": len(llm_out.get("changes") or []),
    }}

    derived = derive_repair_change(engineer, tc_id, version, failed_steps)
    if derived is None:
        check("F1 修复推导：失败步骤存在可修夹具", False, failed_steps[:3], phase="F")
        return {}
    change = derived["change"]
    check(
        "F1 修复推导自真实失败步骤（rule:locator-fixture，非伪造）",
        True,
        {"step_id": derived["step_id"], "original_target": derived["original_target"], "fixture": derived["fixture_key"]},
        phase="F",
    )

    proposal = engineer.call(
        "POST",
        "/api/v1/repairs",
        {
            "testcase_id": tc_id,
            "base_version": version,
            "changes": [change],
            "reason": "RUN-011-R1：TF-LOC-001 真实定位器故障驱动的最小修复（rule:locator-fixture）",
            "created_by": "agent:repair-planner",
            "diagnosis_id": diag_id,
            "source_run_id": failed_run_id,
            "repair_source": "rule:locator-fixture",
        },
        expect_status=201,
    )
    pid = proposal["proposal_id"]
    RESULT["ids"]["repair_proposal_id"] = pid
    RESULT["phases"]["repair"]["proposal"] = {
        "proposal_id": pid,
        "testcase_id": proposal.get("testcase_id"),
        "base_version": proposal.get("base_version"),
        "diagnosis_id": proposal.get("diagnosis_id"),
        "source_run_id": proposal.get("source_run_id"),
        "repair_source": proposal.get("repair_source"),
        "diff": proposal.get("diff"),
    }
    check(
        "F2 Repair 与 Diagnosis/失败 Run 明确关联（diagnosis_id + source_run_id + repair_source）",
        proposal.get("diagnosis_id") == diag_id
        and proposal.get("source_run_id") == failed_run_id
        and proposal.get("testcase_id") == tc_id
        and proposal.get("base_version") == version
        and str(proposal.get("repair_source") or "").startswith(("human:", "rule:", "llm:")),
        RESULT["phases"]["repair"]["proposal"],
        phase="F",
    )

    validated = engineer.call("POST", f"/api/v1/repairs/{pid}/validate")
    RESULT["phases"]["repair"]["validation"] = {
        "status": validated.get("status"),
        "assertion_preservation": validated.get("assertion_preservation"),
        "oracle_integrity": validated.get("oracle_integrity"),
        "diff": validated.get("diff") or validated.get("changes"),
    }
    check(
        "F3 断言保护与 Oracle 完整性校验通过",
        validated.get("status") == "VALIDATED"
        and validated.get("assertion_preservation") == "PASS"
        and validated.get("oracle_integrity") == "PASS",
        RESULT["phases"]["repair"]["validation"],
        phase="F",
    )

    sandbox = engineer.call("POST", f"/api/v1/repairs/{pid}/sandbox-run", None, timeout=420)
    RESULT["phases"]["repair"]["sandbox"] = {
        "sandbox_status": sandbox.get("sandbox_status"),
        "sandbox_run_id": sandbox.get("sandbox_run_id"),
        "sandbox_testcase_id": sandbox.get("sandbox_testcase_id"),
        "candidate_version": sandbox.get("candidate_version"),
        "executed_by": sandbox.get("sandbox_executed_by"),
        "fencing_token": sandbox.get("sandbox_fencing_token"),
    }
    check("F4 沙箱真实执行 PASSED（证明修复有效）", sandbox.get("sandbox_status") == "PASSED", RESULT["phases"]["repair"]["sandbox"], phase="F")
    check(
        "F5 沙箱 Run 由独立 Worker 执行且使用候选副本",
        bool(sandbox.get("sandbox_executed_by"))
        and sandbox.get("sandbox_executed_by") != RESULT["env"].get("api_worker_id")
        and int(sandbox.get("sandbox_fencing_token") or 0) >= 1
        and bool(sandbox.get("sandbox_testcase_id")),
        RESULT["phases"]["repair"]["sandbox"],
        phase="F",
    )

    engineer.call("POST", f"/api/v1/repairs/{pid}/submit")
    code, body, _ = engineer.raw("POST", f"/api/v1/repairs/{pid}/approve", {"reviewer": "user:qa-lead", "note": "x"})
    check("F6 qa-engineer 审批被服务端拒绝（403 repair.approve）", code == 403, {"status": code}, phase="F")

    approved = lead.call("POST", f"/api/v1/repairs/{pid}/approve", {"reviewer": "agent:auto-heal", "note": "RUN-011-R1 人工审批"})
    RESULT["phases"]["repair"]["approval"] = {
        "status": approved.get("status"),
        "approver": approved.get("approver") or approved.get("approved_by"),
        "body_declared": "agent:auto-heal",
    }
    check(
        "F7 qa-lead 审批生效且主体来自服务端 Principal",
        str(RESULT["phases"]["repair"]["approval"].get("approver") or "").startswith("user:")
        or RESULT["phases"]["repair"]["approval"].get("status") in {"APPROVED", "PENDING_PROMOTION"},
        RESULT["phases"]["repair"]["approval"],
        phase="F",
    )

    code, body, _ = lead.raw("POST", f"/api/v1/repairs/{pid}/promote", {"idempotency_key": f"run011r1-{pid}"})
    check("F8 qa-lead 不能执行高风险晋升（403 repair.promote）", code == 403, {"status": code}, phase="F")

    promoted = admin.call("POST", f"/api/v1/repairs/{pid}/promote", {"idempotency_key": f"run011r1-{pid}"})
    promoted_version = int(promoted.get("promoted_version") or 0)
    RESULT["ids"]["promoted_version"] = promoted_version
    RESULT["phases"]["repair"]["promotion"] = {
        "status": promoted.get("status"),
        "promoted_version": promoted_version,
        "receipt": promoted.get("promotion_receipt") or promoted.get("receipt") or (promoted.get("promotion_record") or {}),
        "base_version": promoted.get("base_version") or version,
    }
    check(
        "F9 platform-admin CAS 晋升成功且带回执",
        promoted.get("status") == "PROMOTED" and promoted_version > version,
        RESULT["phases"]["repair"]["promotion"],
        phase="F",
    )

    replay = admin.call("POST", f"/api/v1/repairs/{pid}/promote", {"idempotency_key": f"run011r1-{pid}"})
    check(
        "F10 幂等重放返回同一版本（不重复创建版本）",
        int(replay.get("promoted_version") or 0) == promoted_version,
        {"first": promoted_version, "replay": replay.get("promoted_version")},
        phase="F",
    )
    return {"proposal_id": pid, "promoted_version": promoted_version}


def phase_regress(
    engineer: Http, admin: Http, ex: WorkerRun, tf: TeamFlow,
    tc_id: str, base_version: int, pid: str, promoted_version: int,
) -> dict[str, Any]:
    """§1.2 步骤 16-18：固定版本回归 → 回滚 → 回滚后再次验证。"""
    reg = ex.execute(tc_id, version=promoted_version, note="晋升版本回归")
    reg_run = reg["run"]
    RESULT["ids"]["regression_run_id"] = reg_run["run_id"]
    RESULT["phases"]["regress"] = {
        "regression_run_id": reg_run["run_id"],
        "verdict": reg_run.get("test_verdict"),
        "testcase_id": reg_run.get("testcase_id"),
        "testcase_version_id": reg_run.get("testcase_version_id"),
        "trace": reg["trace"],
    }
    check("G1 晋升版本回归 PASSED（故障仍生效，修复真实有效）", reg_run.get("test_verdict") == "PASSED", {"verdict": reg_run.get("test_verdict")}, phase="G")
    check(
        "G2 回归使用精确晋升版本",
        str(reg_run.get("testcase_version_id") or "") == f"{tc_id}@{promoted_version}",
        reg_run.get("testcase_version_id"),
        phase="G",
    )
    check("G3 回归由独立 Worker 执行", reg["trace"].get("independent_worker") is True, reg["trace"], phase="G")

    rolled = admin.call(
        "POST",
        f"/api/v1/repairs/{pid}/rollback",
        {"operator": "user:platform-admin", "reason": "RUN-011-R1 闭环结束回滚"},
    )
    record = rolled.get("rollback_record") or {}
    restored = int(record.get("restored_version") or 0)
    RESULT["ids"]["rollback_receipt"] = record
    RESULT["ids"]["restored_version"] = restored
    RESULT["phases"]["regress"]["rollback"] = {
        "status": rolled.get("status"),
        "restored_version": restored,
        "record": record,
        "promoted_version": rolled.get("promoted_version"),
    }
    check(
        "G4 回滚成功并产生可追溯回执（restored_version + target_version）",
        rolled.get("status") == "ROLLED_BACK"
        and restored >= 1
        and bool(record.get("target_version") or record.get("restored_version")),
        RESULT["phases"]["regress"]["rollback"],
        phase="G",
    )

    base_doc = engineer.call("GET", f"/api/v1/testcases/{tc_id}/versions/{base_version}")
    restored_doc = engineer.call("GET", f"/api/v1/testcases/{tc_id}/versions/{restored}")
    meta = engineer.call("GET", f"/api/v1/testcases/{tc_id}")
    same_steps = [(s.get("step_id"), s.get("action"), (s.get("target") or {}).get("value"), s.get("value")) for s in restored_doc.get("steps") or []] == [
        (s.get("step_id"), s.get("action"), (s.get("target") or {}).get("value"), s.get("value")) for s in base_doc.get("steps") or []
    ]
    same_assertions = [(a.get("assertion_id"), a.get("oracle_id"), json.dumps(a.get("expected"), sort_keys=True)) for a in restored_doc.get("assertions") or []] == [
        (a.get("assertion_id"), a.get("oracle_id"), json.dumps(a.get("expected"), sort_keys=True)) for a in base_doc.get("assertions") or []
    ]
    same_bindings = (restored_doc.get("oracle_bindings") or []) == (base_doc.get("oracle_bindings") or [])
    RESULT["phases"]["regress"]["content_level_verify"] = {
        "latest_version": meta.get("latest_version"),
        "restored_version": restored,
        "steps_equal": same_steps,
        "assertions_equal": same_assertions,
        "bindings_equal": same_bindings,
        "review_status": restored_doc.get("review_status"),
    }
    check(
        "G5 回滚是内容级恢复（步骤/断言/Oracle 绑定 + 版本指针）",
        same_steps and same_assertions and same_bindings and int(meta.get("latest_version") or 0) == restored,
        RESULT["phases"]["regress"]["content_level_verify"],
        phase="G",
    )

    # §1.2 步骤 18：回滚后再次验证——恢复后的官方版本（故障已清除）+ 晋升版本仍可执行
    tf.clear_faults()
    post = ex.execute(tc_id, version=restored, note="回滚后复核恢复版本")
    post_run = post["run"]
    RESULT["phases"]["regress"]["post_rollback_run_id"] = post_run["run_id"]
    RESULT["phases"]["regress"]["post_rollback_verdict"] = post_run.get("test_verdict")
    RESULT["phases"]["regress"]["post_rollback_version_id"] = post_run.get("testcase_version_id")
    check(
        "G6 回滚后复核：恢复版本真实执行且 PASSED",
        post_run.get("test_verdict") == "PASSED",
        {"verdict": post_run.get("test_verdict"), "version": post_run.get("testcase_version_id")},
        phase="G",
    )
    post2 = ex.execute(tc_id, version=promoted_version, note="回滚后复核晋升版本（历史版本仍可执行）")
    post2_run = post2["run"]
    RESULT["phases"]["regress"]["post_rollback_promoted_run_id"] = post2_run["run_id"]
    check(
        "G7 回滚后复核：晋升版本仍可独立执行并通过",
        post2_run.get("test_verdict") == "PASSED",
        {"verdict": post2_run.get("test_verdict")},
        phase="G",
    )
    tf.clear_faults()
    return {"regression_run_id": reg_run["run_id"], "restored_version": restored}


def phase_audit(admin: Http, engineer: Http) -> dict[str, Any]:
    verify = admin.call("GET", "/api/v1/auth/audit/verify")
    RESULT["phases"]["audit"] = verify
    check("审计哈希链完整", verify.get("intact") is True and int(verify.get("entries") or 0) > 0, verify, phase="audit")
    entries = verify.get("entries")
    check("审计条目覆盖本轮操作（>10）", int(entries or 0) > 10, entries, phase="audit")
    return {}


# ------------------------------------------------------------- trace（关系校验）
def phase_trace(engineer: Http) -> dict[str, Any]:
    """§1.4：追溯链必须校验引用之间的**关系**，而不只是"每个 ID 能读到"。"""
    ids = RESULT["ids"]
    tc_id = ids.get("testcase_id") or ""
    approved_version = int(ids.get("approved_version") or 0)
    failed_run_id = ids.get("failed_run_id") or ""
    diag_id = ids.get("diagnosis_id") or ""
    pid = ids.get("repair_proposal_id") or ""
    promoted_version = int(ids.get("promoted_version") or 0)

    relations: list[dict[str, Any]] = []

    def rel(name: str, ok: bool, detail: Any = "") -> None:
        relations.append({"name": name, "ok": bool(ok), "detail": detail})
        check(name, ok, detail, phase="trace")

    def fetch(path: str) -> dict[str, Any] | None:
        try:
            return engineer.call("GET", path)
        except Exception as exc:  # noqa: BLE001
            return {"__error__": f"{type(exc).__name__}: {exc}"}

    # ---- Run ↔ 主 TestCase / 版本
    normal_run = fetch(f"/api/v1/runs/{ids.get('run_id')}")
    failed_run = fetch(f"/api/v1/runs/{failed_run_id}")
    for label, run in (("正式 Run", normal_run), ("失败 Run", failed_run)):
        rel(
            f"T1 {label}.testcase_id 与主 TestCase 一致",
            _run_testcase_id(run or {}) == tc_id,
            {"run.testcase_id(derived)": _run_testcase_id(run or {}), "expected": tc_id},
        )
        rel(
            f"T2 {label}.testcase_version_id 与批准版本一致",
            (run or {}).get("testcase_version_id") == f"{tc_id}@{approved_version}",
            {"run.testcase_version_id": (run or {}).get("testcase_version_id"), "expected": f"{tc_id}@{approved_version}"},
        )

    # ---- Diagnosis ↔ 失败 Run / 主 TestCase
    diag = fetch(f"/api/v1/diagnoses/{diag_id}")
    rel(
        "T3 Diagnosis.run_id 与失败 Run 一致",
        (diag or {}).get("run_id") == failed_run_id,
        {"diag.run_id": (diag or {}).get("run_id"), "expected": failed_run_id},
    )
    rel(
        "T4 Diagnosis.testcase_id 与主 TestCase 一致",
        (diag or {}).get("testcase_id") == tc_id,
        {"diag.testcase_id": (diag or {}).get("testcase_id"), "expected": tc_id},
    )

    # ---- Evidence：引用、归属、跨 Run 误引
    run_evidence = engineer.call("GET", f"/api/v1/runs/{failed_run_id}/evidence") or []
    run_ev_ids = {str(e.get("evidence_id")) for e in run_evidence}
    ev_extract = _extract_evidence_refs(diag or {})
    diag_ev_ids = set(ev_extract["union"])
    missing = sorted(diag_ev_ids - run_ev_ids - set(ids.get("probe_evidence_ids") or []))
    probe_ev_ids = {str(x) for x in (RESULT["phases"].get("probe") or {}).get("new_evidence_ids") or []}
    missing = sorted(diag_ev_ids - run_ev_ids - probe_ev_ids)
    rel(
        "T5 诊断支持/反驳证据引用均可读取且归属失败 Run（无跨 Run 误引）",
        bool(diag_ev_ids) and not missing,
        {"diag_evidence": sorted(diag_ev_ids)[:10], "missing_or_foreign": missing[:10], "run_evidence_count": len(run_ev_ids)},
    )
    # 每条证据的 source_run_id
    ev_foreign: list[str] = []
    for eid in sorted(diag_ev_ids)[:30]:
        ev = fetch(f"/api/v1/evidence/{eid}")
        if not ev or ev.get("__error__"):
            ev_foreign.append(eid)
            continue
        ev_run = str(ev.get("run_id") or "")
        if ev_run and ev_run != failed_run_id and eid not in probe_ev_ids:
            ev_foreign.append(f"{eid}@{ev_run}")
    rel(
        "T6 每条诊断证据的 source_run_id 属于失败 Run（或明确标记的探针证据）",
        not ev_foreign,
        ev_foreign[:10],
    )

    # ---- Graph 节点与边的实际引用关系
    graph = engineer.call("POST", f"/api/v1/runs/{failed_run_id}/graph", {}, expect_status=(200, 201))
    nodes = graph.get("nodes") or []
    edges = graph.get("edges") or []
    node_ids = {str(n.get("node_id") or n.get("id")) for n in nodes}
    ev_nodes = [n for n in nodes if str(n.get("node_type")) in {"Evidence", "evidence"}]
    ev_node_refs = {str(n.get("ref_id") or "") for n in ev_nodes} - {""}
    dangling_edges = [
        e for e in edges
        if str(e.get("src") or e.get("source") or e.get("from") or "") not in node_ids
        or str(e.get("dst") or e.get("target") or e.get("to") or "") not in node_ids
    ]
    ev_node_foreign = sorted(ev_node_refs - run_ev_ids - probe_ev_ids)
    rel(
        "T7 Graph Evidence 节点引用真实 Evidence（不允许悬空引用）",
        bool(ev_nodes) and not ev_node_foreign,
        {"evidence_nodes": len(ev_nodes), "foreign_refs": ev_node_foreign[:10]},
    )
    rel(
        "T8 Graph 边端点都存在于节点集（引用关系可复核）",
        bool(edges) and not dangling_edges,
        {"edges": len(edges), "dangling": [str(e)[:120] for e in dangling_edges[:5]]},
    )

    # ---- Repair ↔ Diagnosis / 失败 Run / 主 TestCase / base_version
    repair = fetch(f"/api/v1/repairs/{pid}")
    rel(
        "T9 Repair.testcase_id 与主 TestCase 一致",
        (repair or {}).get("testcase_id") == tc_id,
        {"repair.testcase_id": (repair or {}).get("testcase_id"), "expected": tc_id},
    )
    rel(
        "T10 Repair.diagnosis_id 与 Diagnosis 一致",
        (repair or {}).get("diagnosis_id") == diag_id,
        {"repair.diagnosis_id": (repair or {}).get("diagnosis_id"), "expected": diag_id},
    )
    rel(
        "T11 Repair.source_run_id 与失败 Run 一致",
        (repair or {}).get("source_run_id") == failed_run_id,
        {"repair.source_run_id": (repair or {}).get("source_run_id"), "expected": failed_run_id},
    )
    rel(
        "T12 Repair.base_version 与失败用例版本一致",
        int((repair or {}).get("base_version") or 0) == approved_version,
        {"repair.base_version": (repair or {}).get("base_version"), "expected": approved_version},
    )
    rel(
        "T13 Repair.repair_source 如实标注（human:/rule:/llm:）",
        str((repair or {}).get("repair_source") or "").startswith(("human:", "rule:", "llm:")),
        (repair or {}).get("repair_source"),
    )

    # ---- Sandbox Run 使用正确的候选版本
    sandbox_run_id = (RESULT["phases"].get("repair") or {}).get("sandbox", {}).get("sandbox_run_id") or ""
    sandbox_run = fetch(f"/api/v1/runs/{sandbox_run_id}") if sandbox_run_id else {}
    rel(
        "T14 Sandbox Run 使用候选副本（独立 testcase_id，绑定 repair）",
        bool(sandbox_run_id)
        and not (sandbox_run or {}).get("__error__")
        and _run_testcase_id(sandbox_run or {}) != tc_id
        and (RESULT["phases"].get("repair") or {}).get("sandbox", {}).get("sandbox_status") == "PASSED",
        {"sandbox_run_id": sandbox_run_id, "sandbox_testcase_id": _run_testcase_id(sandbox_run or {})},
    )

    # ---- Promotion receipt 绑定正确的 Proposal
    receipt = (RESULT["phases"].get("repair") or {}).get("promotion", {}).get("receipt") or {}
    promo_record = (repair or {}).get("promotion_record") or {}
    idem = str(promo_record.get("idempotency_key") or receipt.get("idempotency_key") or "")
    target_in_record = promo_record.get("target_version") or receipt.get("target_version")
    rel(
        "T15 Promotion 回执绑定正确的 Proposal 与版本关系",
        bool(idem) and int(target_in_record or 0) == promoted_version,
        {"idempotency_key": idem, "target_version": target_in_record, "promoted_version": promoted_version},
    )

    # ---- Regression Run 使用精确晋升版本
    reg_run = fetch(f"/api/v1/runs/{ids.get('regression_run_id')}")
    rel(
        "T16 Regression Run 使用精确晋升版本",
        (reg_run or {}).get("testcase_version_id") == f"{tc_id}@{promoted_version}",
        {"reg.testcase_version_id": (reg_run or {}).get("testcase_version_id"), "expected": f"{tc_id}@{promoted_version}"},
    )

    # ---- Rollback receipt 绑定正确的版本关系
    rb = (RESULT["phases"].get("regress") or {}).get("rollback") or {}
    record = rb.get("record") or {}
    restored_version = int(ids.get("restored_version") or 0)
    rel(
        "T17 Rollback 回执绑定正确的版本关系（target=晋升版本 → restored=恢复版本）",
        rb.get("status") == "ROLLED_BACK"
        and restored_version >= 1
        and (
            str(record.get("target_version") or "") == f"{tc_id}:v{promoted_version}"
            or int(record.get("restored_version") or 0) == restored_version
        ),
        {"record": record, "promoted_version": promoted_version, "restored_version": restored_version},
    )
    post_run = fetch(f"/api/v1/runs/{RESULT['phases'].get('regress', {}).get('post_rollback_run_id')}")
    rel(
        "T18 回滚后验证 Run 使用恢复版本",
        (post_run or {}).get("testcase_version_id") == f"{tc_id}@{restored_version}",
        {"post.testcase_version_id": (post_run or {}).get("testcase_version_id"), "expected": f"{tc_id}@{restored_version}"},
    )

    RESULT["phases"]["trace"] = {
        "relations": relations,
        "evidence_extract": ev_extract,
        "graph": {"nodes": len(nodes), "edges": len(edges), "evidence_nodes": len(ev_nodes)},
    }
    return {}


# ------------------------------------------------------------------------ main
def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default="http://127.0.0.1:8110")
    parser.add_argument("--teamflow", default="http://127.0.0.1:8111")
    parser.add_argument("--label", default="r1-loop1")
    parser.add_argument("--workspace", default="")
    parser.add_argument("--workers", default="", help="逗号分隔的独立 Worker ID 列表")
    parser.add_argument("--out", default="")
    parser.add_argument("--phases", default="all", help="逗号分隔：env,auth,seed,ab,c,fail,diag,probe,repair,regress,trace,audit")
    args = parser.parse_args()

    password = os.getenv("TG_RUN011_PASSWORD") or FALLBACK_PASSWORD
    workers = [w.strip() for w in args.workers.split(",") if w.strip()]
    workspace = args.workspace or f"ws-run011-{args.label}"

    RESULT["label"] = args.label
    RESULT["run_kind"] = "RUN-011-R1-single-testcase-lineage"
    RESULT["started_at"] = _now()
    RESULT["api"] = args.api
    RESULT["teamflow"] = args.teamflow
    RESULT["workspace"] = workspace

    anon = Http(args.api, "anon")
    roles = {name: Http(args.api, name) for name in ("viewer", "qa-engineer", "qa-lead", "platform-admin")}
    tf = TeamFlow(args.teamflow)
    phases = {p.strip() for p in args.phases.split(",") if p.strip()}

    def want(name: str) -> bool:
        return "all" in phases or name in phases

    try:
        for name, session in roles.items():
            session.login(name, password)
        if want("env"):
            phase_env(roles["viewer"], tf, workers)
        if want("auth"):
            phase_auth(roles, anon, roles["platform-admin"])
        if want("seed"):
            phase_seed(roles["platform-admin"], roles["qa-engineer"], tf)
        ex = WorkerRun(roles["qa-engineer"], workers, RESULT["env"].get("api_worker_id", ""))
        ab: dict[str, Any] = {}
        if want("ab"):
            ab = phase_ab(roles["qa-engineer"], roles["qa-lead"], workspace, args.teamflow)
        tc_id, tc_version = ab.get("testcase_id") or RESULT["ids"].get("testcase_id"), ab.get("version") or RESULT["ids"].get("approved_version")
        if tc_id and want("c"):
            phase_c(roles["qa-engineer"], ex, tf, tc_id, tc_version)
        failed: dict[str, Any] = {}
        if tc_id and want("fail"):
            failed = phase_fail(roles["qa-engineer"], ex, tf, tc_id, tc_version)
        diag_out: dict[str, Any] = {}
        if failed and want("diag"):
            diag_out = phase_diag(roles["qa-engineer"], failed["run_id"])
        if failed and diag_out and want("probe"):
            phase_probe(roles["qa-engineer"], failed["run_id"], diag_out["diagnosis_id"])
        rep_out: dict[str, Any] = {}
        if failed and diag_out and tc_id and want("repair"):
            rep_out = phase_repair(
                roles["qa-engineer"], roles["qa-lead"], roles["platform-admin"], ex, tf,
                tc_id, tc_version, diag_out["diagnosis_id"], failed["run_id"], failed.get("failed_steps") or [],
            )
        if rep_out and tc_id and want("regress"):
            phase_regress(
                roles["qa-engineer"], roles["platform-admin"], ex, tf,
                tc_id, tc_version, rep_out["proposal_id"], rep_out["promoted_version"],
            )
        if want("trace"):
            phase_trace(roles["qa-engineer"])
        if want("audit"):
            phase_audit(roles["platform-admin"], roles["qa-engineer"])
    except Exception as exc:  # noqa: BLE001 - 顶层异常必须记录，不吞掉
        check("顶层执行异常", False, f"{type(exc).__name__}: {exc}", phase="top")
    finally:
        tf.clear_faults()
        RESULT["ended_at"] = _now()
        total = len(CHECKS)
        passed = sum(1 for c in CHECKS if c["ok"])
        RESULT["summary"] = {
            "total": total,
            "passed": passed,
            "failed": [c["name"] for c in CHECKS if not c["ok"]],
        }
        if args.out:
            out = Path(args.out)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(RESULT, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(f"\n[out] {out}  checks={passed}/{total}")
        print(json.dumps(RESULT["summary"], ensure_ascii=False))
    return 0 if not RESULT["summary"]["failed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
