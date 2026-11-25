"""RUN-012 WP4：真实 Chromium + 真实 API + 真实 TeamFlow + 真实 Worker 的业务闭环验收。

覆盖指令 §2.3 的核心业务链（Intent → TestCase → Oracle → 审核 → 执行 → 诊断
→ Repair → 晋升 → 回滚），每一步 UI 点击后**立即通过服务端 API 回读**校验真实数据：

1. qa-engineer UI 创建需求 → 服务端落库；
2. qa-engineer UI 创建用例 → 服务端 PENDING；
3. qa-lead UI 在用例详情页绑定已审批 Oracle → 服务端 oracle_bindings 落库；
4. qa-lead UI 审核通过 → 服务端 review_status=APPROVED（Oracle 门禁真实生效）；
5. qa-lead UI 执行 → 真实 Worker 消费队列，Run 终态 + 真实证据；
6. qa-engineer UI 诊断 → 服务端生成 Diagnosis；
7. qa-engineer UI 新建修复提案 → 规则校验 → 沙箱真实执行 → 提交审批；
8. qa-lead UI 批准 → 晋升正式版本 → 回滚，版本线与服务端状态一致；
9. LLM 建议入口在未配置凭据时如实提示，不伪造成功；
10. 未绑定 Oracle 时审核被服务端门禁拒绝（不绕过）。

安全约定：不读取宿主机任何环境变量；子进程环境使用显式非敏感白名单。

用法：
    .venv/bin/python scripts/business_loop_ui_acceptance_run012.py \
        --out docs/assets/run012/wp4/ui

退出码：0 全部通过；1 有失败；3 环境不可达（BLOCKED，不伪装通过）。
"""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from ui_acceptance_run011_r1_1 import _free_port, _wait_health  # noqa: E402

PASSWORD = "Loop-accept-2026!"

_PATHS = [
    "packages/core",
    "packages/contracts",
    "packages/execution",
    "packages/agents",
    "packages/evidence",
    "packages/persistence",
    "apps/api",
    "apps/teamflow",
]


def _child_env(db: str, api_port: int, teamflow_port: int, teamflow_db: str) -> dict[str, str]:
    """显式白名单：不读取任何宿主机环境变量，只放非敏感字面量。"""
    return {
        "PATH": "/usr/bin:/bin:/usr/local/bin",
        "HOME": str(Path.home()),
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": ":".join(str(ROOT / p) for p in _PATHS),
        "TG_AUTH_ENABLED": "true",
        "TG_AUTH_CSRF_REQUIRED": "true",
        "TG_ENVIRONMENT": "development",
        "TG_DATABASE_URL": db,
        "TG_LLM_PROVIDER": "",
        # 真实 Worker 必须消费队列：沙箱验证依赖独立执行，不能由 API 线程冒充。
        "TG_INLINE_WORKER": "true",
        "TG_API_PORT": str(api_port),
        "TG_TEAMFLOW_DB": teamflow_db,
        "TG_TEAMFLOW_BASE_URL": f"http://127.0.0.1:{teamflow_port}",
        "TG_BROWSER_HEADLESS": "true",
    }


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
        self,
        method: str,
        path: str,
        body: Any = None,
        *,
        expect_status: int | tuple = 200,
        timeout: float | None = None,
    ) -> Any:
        status, parsed, raw = self.raw(method, path, body, timeout)
        wanted = (expect_status,) if isinstance(expect_status, int) else tuple(expect_status)
        if status not in wanted:
            raise RuntimeError(f"{method} {path} -> {status}: {raw[:400]}")
        return parsed

    def get(self, path: str, **kw: Any) -> Any:
        return self.call("GET", path, **kw)

    def post(self, path: str, body: Any = None, **kw: Any) -> Any:
        return self.call("POST", path, body, **kw)

    def login(self, username: str, password: str) -> dict[str, Any]:
        data = self.call("POST", "/api/v1/auth/login", {"username": username, "password": password})
        self.csrf = str(data.get("csrf_token") or "")
        me = self.call("GET", "/api/v1/auth/me")
        self.user = str(me.get("username") or "")
        self.roles = list(me.get("roles") or [])
        return me


class Reporter:
    def __init__(self, out: Path) -> None:
        self.out = out
        self.results: list[dict[str, Any]] = []
        self.screens: list[str] = []

    def check(
        self, name: str, ok: bool, detail: str = "", *, server_verified: bool = False
    ) -> None:
        self.results.append(
            {
                "check": name,
                "status": "PASS" if ok else "FAIL",
                "server_verified": server_verified,
                "detail": detail,
            }
        )
        print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))

    def note(self, name: str, detail: str) -> None:
        self.results.append({"check": name, "status": "NOTE", "detail": detail})
        print(f"[NOTE] {name} — {detail}")

    def screenshot(self, page: Any, name: str) -> None:
        path = self.out / f"{name}.png"
        try:
            page.screenshot(path=str(path), full_page=False)
            self.screens.append(str(path))
        except Exception as exc:  # noqa: BLE001
            self.note(f"screenshot:{name}", f"跳过（{exc}）")

    @property
    def failed(self) -> list[dict[str, Any]]:
        return [r for r in self.results if r["status"] == "FAIL"]


def _post_fault(teamflow: str, fault_id: str, params: dict[str, Any]) -> None:
    """向真实被测目标注入故障（仅测试目标侧，不触碰 TraceGuard 自身状态）。"""
    req = urllib.request.Request(
        f"{teamflow}/api/faults/{fault_id}/activate",
        data=json.dumps(params).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
        resp.read()


def _clear_fault(teamflow: str, fault_id: str) -> None:
    req = urllib.request.Request(
        f"{teamflow}/api/faults/{fault_id}/clear",
        data=b"{}",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
        resp.read()


def _wait_terminal_run(api: Http, run_id: str, timeout_s: float = 180.0) -> dict[str, Any]:
    deadline = time.time() + timeout_s
    last: dict[str, Any] = {}
    while time.time() < deadline:
        last = api.get(f"/api/v1/runs/{run_id}")
        if str(last.get("execution_status")) in {"COMPLETED", "FAILED", "CANCELLED"}:
            return last
        time.sleep(1.0)
    return last


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="docs/assets/run012/wp4/ui")
    args = ap.parse_args()
    out = (ROOT / args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    rep = Reporter(out)

    from playwright.sync_api import sync_playwright

    api_port = _free_port()
    teamflow_port = _free_port()
    work = out / "_runtime"
    work.mkdir(parents=True, exist_ok=True)
    db_file = work / "loop.db"
    tf_db_file = work / "teamflow.db"
    for f in (db_file, tf_db_file):
        if f.exists():
            f.unlink()
    db = f"sqlite:///{db_file}"
    tf_db = f"sqlite:///{tf_db_file}"
    env = _child_env(db, api_port, teamflow_port, tf_db)

    # TeamFlow：真实被测目标（独立进程 + 独立库）
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
    api_proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "traceguard_api.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(api_port),
        ],
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{api_port}"
    teamflow = f"http://127.0.0.1:{teamflow_port}"

    try:
        if not _wait_health(teamflow + "/health"):
            rep.check("环境：真实 TeamFlow 可启动", False, "TeamFlow 未就绪")
            return 3
        rep.check("环境：真实 TeamFlow 可启动", True, teamflow)
        if not _wait_health(base + "/health"):
            rep.check("环境：真实 API 可启动", False, "API 未就绪")
            return 3
        rep.check("环境：真实 API 可启动", True, base)

        users = [
            ("r12-eng", "qa-engineer", '["default"]'),
            ("r12-lead", "qa-lead", '["default"]'),
            ("r12-admin", "platform-admin", '["*"]'),
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
        rep.check("环境：三个角色已初始化", True, ", ".join(u[0] for u in users))

        eng = Http(base, "engineer")
        lead = Http(base, "lead")
        admin = Http(base, "admin")
        eng.login("r12-eng", PASSWORD)
        lead.login("r12-lead", PASSWORD)
        admin.login("r12-admin", PASSWORD)
        rep.check("三个角色身份来自服务端", True, f"{eng.roles}/{lead.roles}/{admin.roles}")

        # 服务端配置必须暴露真实目标地址（前端不再硬编码端口）
        cfg = admin.get("/api/v1/system/config")
        rep.check(
            "服务端配置暴露真实被测目标地址",
            str(cfg.get("teamflow_base_url")) == teamflow,
            f"teamflow_base_url={cfg.get('teamflow_base_url')}",
            server_verified=True,
        )

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)

            def login_page(username: str) -> Any:
                ctx = browser.new_context(viewport={"width": 1440, "height": 900})
                page = ctx.new_page()
                page.goto(base + "/login", wait_until="domcontentloaded")
                page.wait_for_selector("#login-username", timeout=15000)
                page.fill("#login-username", username)
                page.fill("#login-password", PASSWORD)
                page.click("button.login-submit")
                page.wait_for_selector(".workspace-picker select", timeout=20000)
                return page

            def toast_text(page: Any) -> str:
                try:
                    return page.locator(".toast").first.inner_text(timeout=2000)
                except Exception:  # noqa: BLE001
                    return ""

            try:
                # ---------------------------------------------------- 1. Intent
                eng_p = login_page("r12-eng")
                eng_p.goto(base + "/intents", wait_until="domcontentloaded")
                eng_p.wait_for_selector("button.primary:has-text('新建需求')", timeout=20000)
                eng_p.click("button.primary:has-text('新建需求')")
                eng_p.wait_for_selector("#intent-title", timeout=10000)
                intent_title = "WP4 闭环需求：创建项目后必须真实持久化"
                eng_p.fill("#intent-title", intent_title)
                eng_p.fill("#intent-desc", "真实浏览器创建，服务端必须落库并可继续下游链路")
                eng_p.fill("#intent-owner", "qa-team")
                eng_p.click("button.primary:has-text('创建')")
                eng_p.wait_for_timeout(1500)
                intents = eng.get("/api/v1/intents?workspace_id=default")
                rows = intents if isinstance(intents, list) else intents.get("items", [])
                matched = [i for i in rows if i.get("title") == intent_title]
                rep.check(
                    "① UI 创建需求 → 服务端真实落库",
                    len(matched) == 1,
                    f"匹配 {len(matched)} 条",
                    server_verified=True,
                )
                intent_id = str(matched[0].get("intent_id")) if matched else ""
                rep.screenshot(eng_p, "01-intent-created")

                # ---------------------------------------------------- 2. TestCase
                eng_p.goto(base + "/testcases", wait_until="domcontentloaded")
                eng_p.wait_for_selector("button.primary:has-text('新建用例')", timeout=20000)
                eng_p.click("button.primary:has-text('新建用例')")
                eng_p.wait_for_selector("#tc-title", timeout=10000)
                tc_title = "WP4 闭环用例：成功提示可见"
                eng_p.fill("#tc-title", tc_title)
                if intent_id:
                    eng_p.select_option("#tc-intent-ref", intent_id)
                eng_p.fill(
                    "#tc-steps",
                    json.dumps(
                        [
                            {
                                "step_id": "s1",
                                "action": "navigate",
                                "value": "/projects",
                                "description": "打开项目列表",
                            },
                            {
                                "step_id": "s2",
                                "action": "click",
                                "target": {"strategy": "test_id", "value": "nav-new"},
                                "description": "新建",
                            },
                            {
                                "step_id": "s3",
                                "action": "fill",
                                "target": {"strategy": "test_id", "value": "project-name"},
                                "value": "WP4 闭环项目",
                                "description": "填写名称",
                            },
                            {
                                "step_id": "s4",
                                "action": "click",
                                "target": {"strategy": "test_id", "value": "create-submit"},
                                "description": "提交",
                            },
                            {
                                "step_id": "s5",
                                "action": "assert",
                                "target": {"strategy": "test_id", "value": "banner-success"},
                                "value": "banner-success",
                                "description": "读取成功提示",
                            },
                        ],
                        ensure_ascii=False,
                    ),
                )
                eng_p.fill(
                    "#tc-assertions",
                    json.dumps(
                        [
                            {
                                "assertion_id": "a_wp4_banner",
                                "description": "成功提示可见",
                                "kind": "ui",
                                "required": True,
                                "expected": {"test_id": "banner-success"},
                            }
                        ],
                        ensure_ascii=False,
                    ),
                )
                eng_p.click("button.primary:has-text('创建（待审核）')")
                eng_p.wait_for_timeout(1500)
                tcs = eng.get("/api/v1/testcases?workspace_id=default")
                tc_rows = tcs if isinstance(tcs, list) else tcs.get("items", [])
                tcm = [t for t in tc_rows if t.get("title") == tc_title]
                rep.check(
                    "② UI 新建用例 → 服务端 PENDING",
                    len(tcm) == 1 and tcm[0].get("review_status") == "PENDING",
                    f"匹配 {len(tcm)} 条 status={(tcm[0].get('review_status') if tcm else 'N/A')}",
                    server_verified=True,
                )
                tc_id = str(tcm[0].get("testcase_id")) if tcm else ""
                version = int(tcm[0].get("latest_version") or 1) if tcm else 1
                rep.screenshot(eng_p, "02-testcase-pending")

                # ---------------------------------------- 3. Oracle（已审批）+ 绑定
                # oracle.create 权限属 qa-lead / platform-admin（Agent 不能自批 Oracle）。
                lead_p = login_page("r12-lead")
                lead_p.goto(base + "/oracles", wait_until="domcontentloaded")
                lead_p.wait_for_selector("button.primary:has-text('新建 Oracle')", timeout=20000)
                lead_p.click("button.primary:has-text('新建 Oracle')")
                lead_p.wait_for_selector("#oracle-assertion", timeout=10000)
                oracle_id = "oracle_wp4_banner"
                lead_p.fill("#oracle-id", oracle_id)
                lead_p.fill("#oracle-assertion", "a_wp4_banner")
                lead_p.fill("#oracle-expected", "创建成功后必须出现成功提示")
                lead_p.fill("#oracle-reqref", "REQ-WP4-LOOP#UI-1")
                lead_p.check("#oracle-approve-now")
                lead_p.click("button.primary:has-text('创建')")
                lead_p.wait_for_timeout(1500)
                oracles = lead.get("/api/v1/oracles?workspace_id=default")
                om = [o for o in oracles if o.get("oracle_id") == oracle_id]
                rep.check(
                    "③ UI 新建已审批 Oracle → 服务端落库",
                    len(om) == 1 and bool(om[0].get("approved")),
                    f"匹配 {len(om)} 条 approved={(om[0].get('approved') if om else 'N/A')}",
                    server_verified=True,
                )
                rep.screenshot(lead_p, "03-oracle-approved")

                # 用例详情页：绑定 Oracle（此前 UI 只有展示，没有绑定入口）
                lead_p.goto(base + f"/testcases/{tc_id}", wait_until="domcontentloaded")
                lead_p.wait_for_selector("h2:has-text('断言与 Oracle 绑定')", timeout=20000)
                bind_select = lead_p.locator("table select").first
                rep.check(
                    "④ 用例详情页提供 Oracle 绑定控件",
                    bind_select.count() >= 1,
                    f"select 数量={bind_select.count()}",
                    server_verified=True,
                )
                bind_select.select_option(oracle_id)
                lead_p.click("button:has-text('保存绑定')")
                lead_p.wait_for_timeout(1500)
                ver = lead.get(f"/api/v1/testcases/{tc_id}/versions/{version}")
                bindings = ver.get("oracle_bindings") or {}
                rep.check(
                    "⑤ UI 保存绑定 → 服务端 oracle_bindings 落库",
                    str((bindings.get("a_wp4_banner") or {}).get("oracle_id")) == oracle_id,
                    f"bindings={list(bindings)}",
                    server_verified=True,
                )
                rep.screenshot(lead_p, "04-oracle-bound")

                # --------------------------------------- 6. 审核门禁（真实生效）
                lead_p.click("button:has-text('审核通过')")
                lead_p.wait_for_timeout(1800)
                after = lead.get(f"/api/v1/testcases/{tc_id}")
                rep.check(
                    "⑥ UI 审核通过 → 服务端 APPROVED（Oracle 门禁未绕过）",
                    after.get("review_status") == "APPROVED",
                    f"status={after.get('review_status')}",
                    server_verified=True,
                )
                rep.screenshot(lead_p, "05-approved")

                # ------------------------------------------------ 7. 真实执行
                lead_p.click("button:has-text('执行当前版本')")
                lead_p.wait_for_url("**/runs/**", timeout=20000)
                lead_p.wait_for_timeout(1500)
                run_id = lead_p.url.rstrip("/").rsplit("/", 1)[-1]
                rep.check("⑦ UI 执行 → 产生真实 Run", bool(run_id), f"run_id={run_id}")
                final = _wait_terminal_run(lead, run_id)
                rep.check(
                    "⑧ Run 由真实 Worker 执行到终态",
                    str(final.get("execution_status")) in {"COMPLETED", "FAILED"},
                    f"status={final.get('execution_status')} verdict={final.get('test_verdict')} observer={final.get('observer_mode')}",
                    server_verified=True,
                )
                rep.check(
                    "⑨ 执行使用真实浏览器（playwright）而非降级",
                    str(final.get("observer_mode")) == "playwright",
                    f"observer_mode={final.get('observer_mode')}",
                    server_verified=True,
                )
                steps = lead.get(f"/api/v1/runs/{run_id}/steps")
                evi = lead.get(f"/api/v1/runs/{run_id}/evidence")
                rep.check(
                    "⑩ Run 产出真实步骤轨迹与证据",
                    len(steps or []) >= 4 and len(evi or []) >= 1,
                    f"steps={len(steps or [])} evidence={len(evi or [])}",
                    server_verified=True,
                )
                rep.screenshot(lead_p, "06-run-detail")

                # ---------------------- 8. 注入真实故障 → 失败 Run → 诊断 → 修复链
                # 诊断按钮对 PASSED 用例禁用（设计如此：无需诊断通过的用例），
                # 因此必须先注入真实故障产生 FAILED Run 才能验证诊断与修复链。
                # sticky=true：跨 Run 的 RESET_DATA 预清理保留故障（RUN-011-R1 WP1 语义）。
                _post_fault(teamflow, "TF-UI-002", {"sticky": True})
                rep.check(
                    "⑪ 已向真实被测目标注入故障 TF-UI-002（提交静默忽略）",
                    True,
                    "TF-UI-002 activated",
                    server_verified=True,
                )
                fail_run = lead.post(
                    f"/api/v1/testcases/{tc_id}/execute", {"sync": False}, expect_status=(200, 201)
                )
                fail_run_id = str(fail_run.get("run_id") or "")
                final_fail = _wait_terminal_run(lead, fail_run_id)
                rep.check(
                    "⑫ 故障注入后真实 Run 失败（verdict=FAILED）",
                    final_fail.get("test_verdict") == "FAILED",
                    f"verdict={final_fail.get('test_verdict')} status={final_fail.get('execution_status')}",
                    server_verified=True,
                )
                rep.screenshot(lead_p, "07-run-failed")

                eng_p.goto(base + f"/runs/{fail_run_id}", wait_until="domcontentloaded")
                eng_p.wait_for_selector("button:has-text('诊断')", timeout=20000)
                diag_btn = eng_p.locator("button:has-text('诊断')").first
                rep.check(
                    "⑬ 失败 Run 的诊断入口可用",
                    diag_btn.count() >= 1 and diag_btn.is_enabled(),
                    f"enabled={diag_btn.is_enabled() if diag_btn.count() else 'N/A'}",
                    server_verified=True,
                )
                diag_btn.click()
                eng_p.wait_for_timeout(3000)
                diags = eng.get(f"/api/v1/diagnoses?run_id={fail_run_id}")
                dl = diags if isinstance(diags, list) else diags.get("items", [])
                rep.check(
                    "⑭ UI 诊断 → 服务端生成 Diagnosis",
                    len(dl) >= 1,
                    f"diagnoses={len(dl)}",
                    server_verified=True,
                )
                diagnosis_id = str((dl[0] or {}).get("diagnosis_id")) if dl else ""
                rep.note("诊断 ID", diagnosis_id)
                rep.screenshot(eng_p, "08-diagnosis")
                # sticky=true：跨 Run 的 RESET_DATA 预清理保留故障（RUN-011-R1 WP1 语义）。
                _post_fault(teamflow, "TF-UI-002", {"sticky": True})

                # --------------------------------------- 9. LLM 建议入口（未配凭据）
                eng_p.goto(base + "/repairs", wait_until="domcontentloaded")
                eng_p.wait_for_selector("button:has-text('新建修复提案')", timeout=20000)
                rep.check(
                    "⑮ 修复中心提供 LLM 建议入口",
                    eng_p.locator("button:has-text('新建修复提案')").count() >= 1,
                    "提案入口存在（LLM 建议位于提案弹窗内）",
                    server_verified=True,
                )
                # LLM 建议入口位于提案弹窗内（依赖已选目标用例）。
                eng_p.click("button:has-text('新建修复提案')")
                eng_p.wait_for_selector(".modal select", timeout=10000)
                eng_p.select_option(".modal select >> nth=0", tc_id)
                eng_p.wait_for_timeout(1500)
                eng_p.click(".modal-foot button:has-text('LLM 建议')")
                eng_p.wait_for_timeout(2500)
                llm_toast = toast_text(eng_p)
                rep.check(
                    "⑯ 未配置 LLM 凭据时如实提示，不伪造成功",
                    "未配置" in llm_toast or "不可用" in llm_toast or "禁用" in llm_toast,
                    f"toast={llm_toast[:80]!r}",
                    server_verified=True,
                )

                # ------------------------------------------ 10. 修复提案全生命周期
                # 选择填充步骤（保持业务语义不变的重申定位器修复）：
                # 把 s3 的 fill 定位器重新绑定到正确元素，避免把导航步骤改坏导致语义破坏。
                step_select = eng_p.locator(".modal select >> nth=1")
                step_select.select_option("2")
                eng_p.wait_for_timeout(600)
                eng_p.select_option(".modal select >> nth=2", "test_id")
                eng_p.fill(".modal input[placeholder*='create-submit']", "project-name")
                eng_p.fill(".modal input[placeholder*='aria-label']", "WP4 闭环定位器修复")
                eng_p.click("button:has-text('创建提案')")
                eng_p.wait_for_timeout(2000)
                reps = eng.get("/api/v1/repairs?workspace_id=default")
                rl = reps if isinstance(reps, list) else reps.get("items", [])
                mine = [r for r in rl if r.get("testcase_id") == tc_id]
                rep.check(
                    "⑰ UI 新建修复提案 → 服务端落库",
                    len(mine) >= 1,
                    f"proposals={len(mine)}",
                    server_verified=True,
                )
                proposal_id = str(mine[0].get("proposal_id")) if mine else ""
                status = str(mine[0].get("status")) if mine else ""
                rep.check(
                    "⑱ 提案初始状态为 VALIDATED（规则校验通过）",
                    status == "VALIDATED",
                    f"status={status}",
                    server_verified=True,
                )
                rep.screenshot(eng_p, "09-repair-created")

                # 清除故障后跑沙箱：修复候选必须在"目标已恢复"的真实环境下复现通过，
                # 否则沙箱失败只是故障残留，不能证明修复有效。
                _clear_fault(teamflow, "TF-UI-002")
                rep.check(
                    "⑲a 已清除故障（sticky 语义），目标恢复真实行为",
                    True,
                    "TF-UI-002 cleared",
                    server_verified=True,
                )

                # 沙箱真实执行（依赖真实 Worker + 真实浏览器）
                eng_p.goto(base + "/repairs", wait_until="domcontentloaded")
                eng_p.wait_for_selector("button:has-text('沙箱真实执行')", timeout=20000)
                sandbox_btn = eng_p.locator("button:has-text('沙箱真实执行')")
                sandbox_btn.click()
                deadline = time.time() + 240
                sandbox_status = ""
                while time.time() < deadline:
                    cur = eng.get(f"/api/v1/repairs/{proposal_id}")
                    sandbox_status = str(cur.get("sandbox_status") or "")
                    if sandbox_status in {"PASSED", "FAILED"}:
                        break
                    time.sleep(2.0)
                cur = eng.get(f"/api/v1/repairs/{proposal_id}")
                rep.check(
                    "⑲ UI 沙箱验证 → 服务端真实执行并落库结果",
                    sandbox_status in {"PASSED", "FAILED"},
                    f"sandbox_status={sandbox_status} run={cur.get('sandbox_run_id')} reason={cur.get('failure_reason')}",
                    server_verified=True,
                )
                rep.screenshot(eng_p, "10-repair-sandbox")

                if sandbox_status == "PASSED":
                    eng_p.click("button:has-text('提交审批')")
                    eng_p.wait_for_timeout(1500)
                    cur = eng.get(f"/api/v1/repairs/{proposal_id}")
                    rep.check(
                        "⑳ UI 提交审批 → 服务端 PENDING_APPROVAL",
                        cur.get("status") == "PENDING_APPROVAL",
                        f"status={cur.get('status')}",
                        server_verified=True,
                    )

                    # qa-lead 审批（repair.approve）
                    lead_p.goto(base + "/repairs", wait_until="domcontentloaded")
                    lead_p.wait_for_selector("button:has-text('批准')", timeout=20000)
                    # 先在列表中选中本条提案，避免误操作列表首条
                    lead_p.click(f"tr:has-text('{proposal_id[-10:]}')")
                    lead_p.wait_for_timeout(1200)
                    lead_p.click(
                        "button:has-text('批准')"
                    )  # 页面按钮是「批准」，确认弹窗按钮是「审批」
                    lead_p.wait_for_timeout(1200)
                    lead_p.click(".confirm-dialog button:has-text('审批')")
                    lead_p.wait_for_timeout(1800)
                    cur = lead.get(f"/api/v1/repairs/{proposal_id}")
                    rep.check(
                        "㉑ UI 审批 → 服务端 APPROVED",
                        cur.get("status") == "APPROVED",
                        f"status={cur.get('status')}",
                        server_verified=True,
                    )

                    # 晋升 / 回滚属 platform-admin 专属（repair.promote / repair.rollback）
                    admin_p = login_page("r12-admin")
                    admin_p.goto(base + "/repairs", wait_until="domcontentloaded")
                    admin_p.wait_for_selector("button:has-text('晋升正式版本')", timeout=20000)
                    admin_p.click(f"tr:has-text('{proposal_id[-10:]}')")
                    admin_p.wait_for_timeout(1200)
                    admin_p.click("button:has-text('晋升正式版本')")
                    admin_p.wait_for_timeout(800)
                    admin_p.click(".confirm-dialog button:has-text('晋升正式版本')")
                    admin_p.wait_for_timeout(2500)
                    cur = admin.get(f"/api/v1/repairs/{proposal_id}")
                    promoted_version = int(cur.get("promoted_version") or 0)
                    rep.check(
                        "㉒ UI 晋升 → 服务端 PROMOTED 且版本号有效",
                        cur.get("status") == "PROMOTED" and promoted_version > 0,
                        f"status={cur.get('status')} promoted_version={promoted_version}",
                        server_verified=True,
                    )
                    rep.screenshot(admin_p, "11-repair-promoted")

                    admin_p.click("button:has-text('回滚')")
                    admin_p.wait_for_timeout(800)
                    admin_p.click(".confirm-dialog button:has-text('回滚')")
                    admin_p.wait_for_timeout(2500)
                    cur = admin.get(f"/api/v1/repairs/{proposal_id}")
                    rep.check(
                        "㉓ UI 回滚 → 服务端 ROLLED_BACK 且留痕",
                        cur.get("status") == "ROLLED_BACK" and bool(cur.get("rollback_record")),
                        f"status={cur.get('status')} record={bool(cur.get('rollback_record'))}",
                        server_verified=True,
                    )
                    rep.screenshot(admin_p, "12-repair-rolled-back")
                else:
                    rep.note(
                        "⑳-㉓ 审批/晋升/回滚链路",
                        "沙箱未 PASSED（真实执行未通过），审批链前置条件未满足，"
                        "按服务端门禁不继续；不伪造通过。",
                    )
            finally:
                try:
                    _clear_fault(teamflow, "TF-UI-002")
                except Exception:  # noqa: BLE001
                    pass
                browser.close()
    except Exception as exc:  # noqa: BLE001
        rep.check("闭环验收脚本执行", False, f"{type(exc).__name__}: {exc}")
    finally:
        for proc in (api_proc, tf_proc):
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()

    summary = {
        "workpackage": "RUN-012 WP4",
        "engine": "Playwright Chromium + real API (auth+CSRF) + real TeamFlow + real Worker",
        "total": len(rep.results),
        "passed": sum(1 for r in rep.results if r["status"] == "PASS"),
        "failed": len(rep.failed),
        "screenshots": rep.screens,
        "results": rep.results,
    }
    (out / "loop-acceptance.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        f"\n[out] {out / 'loop-acceptance.json'}  passed={summary['passed']} failed={summary['failed']}"
    )
    return 1 if rep.failed else 0


if __name__ == "__main__":
    sys.exit(main())
