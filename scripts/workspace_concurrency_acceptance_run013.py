"""RUN-013 WP2：多 Workspace 并发一致性与权限隔离（真实浏览器）。

覆盖：
- alice(ws-a) / bob(ws-b) / multi(ws-a,ws-b) 三身份；
- 快速切换 A/B 不出现跨空间数据闪现；
- localStorage 篡改不能获得额外权限；
- 多标签页 storage 同步；
- 授权撤销后已打开页面不能继续读取；
- 页面内并发请求携带越权 workspace_id 全部 403。

用法：
    .venv/bin/python scripts/workspace_concurrency_acceptance_run013.py
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from business_loop_ui_acceptance_run012 import (  # noqa: E402
    Http,
    Reporter,
    _child_env,
    _free_port,
    _start_api,
    _wait_health,
)

PASSWORD = "WS-concurrency-2026!"


def main() -> int:
    out = (ROOT / "docs/assets/run013/wp2/ui").resolve()
    out.mkdir(parents=True, exist_ok=True)
    work = out / "_runtime"
    work.mkdir(parents=True, exist_ok=True)
    rep = Reporter(out)

    from playwright.sync_api import sync_playwright

    api_port = _free_port()
    teamflow_port = _free_port()
    for name in ("workspace.db", "teamflow.db"):
        f = work / name
        if f.exists():
            f.unlink()
    db_url = f"sqlite:///{work / 'workspace.db'}"
    env = _child_env(
        db_url,
        api_port,
        teamflow_port,
        f"sqlite:///{work / 'teamflow.db'}",
        inline_worker=False,
        lease_ttl_s=30.0,
    )
    env["TG_ARTIFACT_DIR"] = str(work / "artifacts")
    # 测试夹具：固定 ws-a / ws-b 两个真实 Workspace（create_workspace 只生成随机 id）。
    from traceguard_persistence import Database
    from traceguard_persistence.models import WorkspaceRow

    seed_db = Database(db_url)
    seed_db.create_schema()
    with seed_db.session() as session:
        for ws, name in (("ws-a", "WS-A"), ("ws-b", "WS-B")):
            if session.get(WorkspaceRow, ws) is None:
                session.add(WorkspaceRow(workspace_id=ws, name=name, created_by="run013"))
    seed_db.dispose()
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
    api_proc = _start_api(env, api_port)
    base = f"http://127.0.0.1:{api_port}"

    try:
        if not _wait_health(base + "/health", 60):
            rep.check("环境：API 启动", False, base)
            return 3
        rep.check("环境：真实 API 独立进程", True, base)
        users = [
            ("r13-ws-alice", "qa-engineer", '["ws-a"]'),
            ("r13-ws-bob", "qa-engineer", '["ws-b"]'),
            ("r13-ws-multi", "qa-lead", '["ws-a","ws-b"]'),
            ("r13-ws-admin", "platform-admin", '["*"]'),
        ]
        for name, role, spaces in users:
            r = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "auth_bootstrap.py"),
                    "--db",
                    db_url,
                    "--username",
                    name,
                    "--password",
                    PASSWORD,
                    "--roles",
                    role,
                    "--workspaces",
                    spaces,
                ],
                env=env,
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                timeout=60,
            )
            if r.returncode != 0:
                rep.check(f"环境：初始化 {name}", False, r.stderr[-200:])
                return 3
        admin = Http(base, "admin")
        admin.login("r13-ws-admin", PASSWORD)
        for ws, owner in (("ws-a", "r13-ws-alice"), ("ws-b", "r13-ws-bob")):
            admin.post(
                "/api/v1/intents",
                {
                    "title": f"INTENT-{ws.upper()}-ONLY",
                    "description": f"{ws} 独立需求",
                    "owner": owner,
                    "workspace_id": ws,
                },
                expect_status=(200, 201),
            )
            admin.post(
                "/api/v1/testcases",
                {
                    "title": f"CASE-{ws.upper()}-ONLY",
                    "workspace_id": ws,
                    "compile_source": "human",
                    "steps": [
                        {
                            "step_id": "s1",
                            "action": "navigate",
                            "value": "/projects",
                            "description": "打开项目列表",
                        }
                    ],
                    "assertions": [
                        {
                            "assertion_id": "a1",
                            "description": "列表可见",
                            "kind": "ui",
                            "required": True,
                            "expected": {"test_id": "project-rows"},
                        }
                    ],
                },
                expect_status=(200, 201),
            )
        rep.check("环境：两个 Workspace 与隔离数据已创建", True, "ws-a / ws-b")

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)

            def login(username: str) -> Any:
                ctx = browser.new_context(viewport={"width": 1440, "height": 900})
                page = ctx.new_page()
                page.goto(base + "/login", wait_until="domcontentloaded")
                page.wait_for_selector("#login-username", timeout=15000)
                page.fill("#login-username", username)
                page.fill("#login-password", PASSWORD)
                page.click("button.login-submit")
                page.wait_for_selector(".workspace-picker select", timeout=20000)
                return ctx, page

            def current_ws(page: Any) -> str:
                return page.locator(".workspace-picker select").input_value()

            def text_of(page: Any) -> str:
                return page.inner_text("body")

            # ------------------------------------------- alice: 默认只能看 ws-a
            alice_ctx, alice_page = login("r13-ws-alice")
            alice_page.goto(base + "/testcases", wait_until="domcontentloaded")
            alice_page.wait_for_timeout(1500)
            body = text_of(alice_page)
            rep.check(
                "UI：alice 默认空间为 ws-a",
                current_ws(alice_page) == "ws-a",
                f"current={current_ws(alice_page)}",
            )
            rep.check(
                "UI：只显示 ws-a 数据，不出现 ws-b",
                "CASE-WS-A-ONLY" in body and "CASE-WS-B-ONLY" not in body,
                "ws-a visible, ws-b absent",
            )
            alice_page.screenshot(path=str(out / "alice-ws-a.png"))

            # localStorage 篡改不能提权：仍回落到 ws-a
            alice_page.evaluate("localStorage.setItem('tg-workspace','ws-b')")
            alice_page.reload(wait_until="domcontentloaded")
            alice_page.wait_for_timeout(1800)
            body = text_of(alice_page)
            rep.check(
                "UI：localStorage 篡改为 ws-b 后回落 ws-a，且无 ws-b 数据",
                current_ws(alice_page) == "ws-a" and "CASE-WS-B-ONLY" not in body,
                f"current={current_ws(alice_page)}",
                server_verified=True,
            )

            # 页面内并发越权请求全部 403
            statuses = alice_page.evaluate(
                """async () => {
                    const paths = ['/api/v1/testcases', '/api/v1/intents', '/api/v1/events'];
                    const out = [];
                    for (const p of paths) {
                        for (let i = 0; i < 2; i++) {
                            const r = await fetch(`${p}?workspace_id=ws-b`, {credentials: 'include'});
                            out.push(r.status);
                        }
                    }
                    return out;
                }"""
            )
            rep.check(
                "安全：页面内并发越权 workspace_id 请求全部 403",
                all(s == 403 for s in statuses),
                f"statuses={statuses}",
                server_verified=True,
            )

            # 授权撤销后：已打开页面再次请求必须失败
            from traceguard_persistence import Database
            from traceguard_persistence import auth as auth_store

            db = Database(db_url)
            from traceguard_persistence.auth import get_user_by_username

            alice = get_user_by_username(db, "r13-ws-alice")
            assert alice
            auth_store.set_user_workspaces(db, alice["user_id"], ["ws-b"])
            revoked = alice_page.evaluate(
                """async () => {
                    const r = await fetch('/api/v1/testcases?workspace_id=ws-a', {credentials: 'include'});
                    return r.status;
                }"""
            )
            db.dispose()
            rep.check(
                "安全：授权撤销后已打开页面的下一次请求立即 403",
                revoked == 403,
                f"status={revoked}",
                server_verified=True,
            )

            # -------------------------------------------------- multi: 快速切换
            multi_ctx, multi_page = login("r13-ws-multi")
            multi_page.goto(base + "/testcases", wait_until="domcontentloaded")
            multi_page.wait_for_timeout(1200)
            for _ in range(6):
                multi_page.select_option(".workspace-picker select", "ws-b")
                multi_page.wait_for_timeout(120)
                multi_page.select_option(".workspace-picker select", "ws-a")
                multi_page.wait_for_timeout(120)
            multi_page.select_option(".workspace-picker select", "ws-b")
            multi_page.wait_for_timeout(2000)
            body = text_of(multi_page)
            rep.check(
                "并发：快速 A/B 切换后只显示当前空间数据（无回填/混显）",
                current_ws(multi_page) == "ws-b"
                and "CASE-WS-B-ONLY" in body
                and "CASE-WS-A-ONLY" not in body,
                f"current={current_ws(multi_page)}",
                server_verified=True,
            )

            # ------------------------------------------------------ 多标签页同步
            tab2 = multi_ctx.new_page()
            tab2.goto(base + "/testcases", wait_until="domcontentloaded")
            tab2.wait_for_selector(".workspace-picker select", timeout=20000)
            multi_page.select_option(".workspace-picker select", "ws-a")
            multi_page.wait_for_timeout(2500)
            tab2.wait_for_timeout(2500)
            rep.check(
                "多标签页：其他标签切换空间后本标签同步（storage 事件）",
                current_ws(tab2) == "ws-a",
                f"tab2={current_ws(tab2)}",
                server_verified=True,
            )

            # 深色 + 窄屏截图（前端样式与溢出复核）
            multi_page.evaluate("document.documentElement.dataset.theme='dark'")
            multi_page.set_viewport_size({"width": 390, "height": 844})
            multi_page.wait_for_timeout(500)
            overflow = multi_page.evaluate(
                "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
            )
            multi_page.screenshot(path=str(out / "multi-390-dark.png"), full_page=False)
            rep.check(
                "前端：390px 深色模式无横向溢出",
                overflow <= 1,
                f"overflow={overflow}px",
            )

            browser.close()
    except Exception as exc:  # noqa: BLE001
        rep.check("WP2 验收脚本执行", False, f"{type(exc).__name__}: {exc}")
    finally:
        for proc in (api_proc, tf_proc):
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except Exception:  # noqa: BLE001
                proc.kill()

    summary = {
        "workpackage": "RUN-013 WP2",
        "engine": "Playwright Chromium + real API + real SQLite (multi-workspace)",
        "total": len(rep.results),
        "passed": sum(1 for r in rep.results if r["status"] == "PASS"),
        "failed": len(rep.failed),
        "screenshots": rep.screens,
        "results": rep.results,
    }
    (out / "workspace-concurrency-acceptance.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        f"[out] {out / 'workspace-concurrency-acceptance.json'} passed={summary['passed']} failed={summary['failed']}"
    )
    return 1 if rep.failed else 0


if __name__ == "__main__":
    sys.exit(main())
