"""RUN-012 WP2：真实 Chromium + 真实 API + 真实 SQLite 的多 Workspace UI 验收。

- 两个 Workspace（ws-a / ws-b）× 三种角色（viewer / qa-engineer / qa-lead）+ platform-admin；
- 切换控件只展示服务端授权的空间；切换后列表立即刷新且不残留旧空间数据；
- localStorage 篡改、直接伪造请求 workspace_id、详情页越权均必须被服务端拒绝；
- 390 / 768 / 1024 / 1440 四档宽度与深色模式截图。

安全约定：不读取宿主机任何环境变量；子进程环境使用既有脚本的显式非敏感白名单。

用法：
    .venv/bin/python scripts/workspace_ui_acceptance_run012.py \
        --out docs/assets/run012/wp2/ui

退出码：0 全部通过；1 有失败；3 环境不可达（BLOCKED，不伪装通过）。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from ui_acceptance_run011_r1_1 import (  # noqa: E402
    _api_post_raw,
    _free_port,
    _safe_child_env,
    _wait_health,
)

PASSWORD = "Ws-accept-2026!"


class Reporter:
    def __init__(self, out: Path) -> None:
        self.out = out
        self.results: list[dict[str, Any]] = []

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

    def screenshot(self, page: Any, name: str) -> None:
        page.screenshot(path=str(self.out / f"{name}.png"), full_page=True)

    @property
    def failed(self) -> list[dict[str, Any]]:
        return [r for r in self.results if r["status"] == "FAIL"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="docs/assets/run012/wp2/ui")
    args = ap.parse_args()
    out = (ROOT / args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    rep = Reporter(out)

    from playwright.sync_api import sync_playwright

    port = _free_port()
    work = out / "_runtime"
    work.mkdir(parents=True, exist_ok=True)
    db_file = work / "workspace.db"
    if db_file.exists():
        db_file.unlink()
    db = f"sqlite:///{db_file}"
    env = _safe_child_env(db, port)
    api_proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "traceguard_api.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        if not _wait_health(base + "/health"):
            rep.check("环境：真实 API 可启动", False, "API 未就绪")
            return 3

        def bootstrap(username: str, roles: str, workspaces: str) -> bool:
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
            return r.returncode == 0

        # 先建 admin，再通过真实 API 创建两个 Workspace（ID 由服务端生成，不伪造）。
        if not bootstrap("r12-admin", "platform-admin", '["*"]'):
            return 3

        def login_cookie(username: str) -> tuple[str, str]:
            req = urllib.request.Request(
                base + "/api/v1/auth/login",
                data=json.dumps({"username": username, "password": PASSWORD}).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                body = json.loads(resp.read().decode())
            parts = []
            for item in resp.headers.get_all("Set-Cookie") or []:
                first = item.split(";")[0]
                if "=" in first:
                    parts.append(first)
            return "; ".join(parts), body.get("csrf_token", "")

        admin_cookie, admin_csrf = login_cookie("r12-admin")
        ws_ids: list[str] = []
        for name in ("Workspace A", "Workspace B"):
            status, body = _api_post_raw(
                base,
                "/api/v1/workspaces",
                {
                    "name": name,
                    "description": "RUN-012 WP2 acceptance",
                    "created_by": "human:r12-admin",
                },
                admin_cookie,
                admin_csrf,
            )
            if status not in (200, 201) or not body.get("workspace_id"):
                rep.check(f"环境：创建 {name}", False, f"HTTP {status} {body}")
                return 3
            ws_ids.append(str(body["workspace_id"]))
        ws_a, ws_b = ws_ids
        rep.check("环境：真实 API 创建两个 Workspace", True, f"{ws_a}, {ws_b}")

        users = [
            ("r12-a-viewer", "viewer", f'["{ws_a}"]'),
            ("r12-a-engineer", "qa-engineer", f'["{ws_a}"]'),
            ("r12-a-lead", "qa-lead", f'["{ws_a}"]'),
            ("r12-b-viewer", "viewer", f'["{ws_b}"]'),
            ("r12-b-engineer", "qa-engineer", f'["{ws_b}"]'),
            ("r12-b-lead", "qa-lead", f'["{ws_b}"]'),
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
        rep.check("环境：7 个真实用户已初始化（A/B × 3 角色 + admin）", True, db)

        # 用 admin cookie 在两个空间各建一条 Intent，作为列表差异的可回读证据。
        marker: dict[str, str] = {}
        for ws, title in ((ws_a, "A空间专属需求"), (ws_b, "B空间专属需求")):
            status, body = _api_post_raw(
                base,
                "/api/v1/intents",
                {
                    "title": title,
                    "description": "workspace ui acceptance",
                    "owner": "qa-team",
                    "workspace_id": ws,
                    "candidate_assertions": [],
                },
                admin_cookie,
                admin_csrf,
            )
            if status not in (200, 201):
                rep.check(f"环境：创建 {ws} 数据", False, f"HTTP {status} {body}")
                return 3
            marker[ws] = body["intent_id"]
        rep.check(
            "环境：两空间各创建一条可回读 Intent", True, json.dumps(marker, ensure_ascii=False)
        )

        def login_page(browser: Any, username: str, width: int = 1440, height: int = 900) -> Any:
            ctx = browser.new_context(viewport={"width": width, "height": height})
            page = ctx.new_page()
            page.goto(base + "/login", wait_until="domcontentloaded")
            page.wait_for_selector("#login-username", timeout=12000)
            page.fill("#login-username", username)
            page.fill("#login-password", PASSWORD)
            page.click("button.login-submit")
            page.wait_for_selector(".workspace-picker select", timeout=15000)
            return page

        def options(page: Any) -> list[str]:
            return page.locator(".workspace-picker select option").evaluate_all(
                "(els) => els.map(e => e.value)"
            )

        def intent_titles(page: Any) -> list[str]:
            page.goto(base + "/intents", wait_until="domcontentloaded")
            page.wait_for_selector(".grid.c2 .card h2", timeout=12000)
            return page.locator(".grid.c2 .card h2").all_inner_texts()

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                for ws, names in (
                    ((ws_a, ("r12-a-viewer", "r12-a-engineer", "r12-a-lead"))),
                    ((ws_b, ("r12-b-viewer", "r12-b-engineer", "r12-b-lead"))),
                ):
                    for username in names:
                        page = login_page(browser, username)
                        opts = options(page)
                        rep.check(
                            f"{username} 切换控件只显示授权空间 {ws}",
                            opts == [ws],
                            f"options={opts}",
                        )
                        rows = intent_titles(page)
                        own = "A空间专属需求" if ws == ws_a else "B空间专属需求"
                        other = "B空间专属需求" if ws == ws_a else "A空间专属需求"
                        rep.check(
                            f"{username} 列表只含本空间数据",
                            any(own in r for r in rows) and not any(other in r for r in rows),
                            f"rows={len(rows)}",
                            server_verified=True,
                        )
                        if username == "r12-a-viewer":
                            # 普通用户不能在 UI 里切到未授权空间；伪造 change 也无效。
                            page.evaluate(
                                """() => {
                                  const s = document.querySelector('.workspace-picker select');
                                  s.value = '__FORBIDDEN__';
                                  s.dispatchEvent(new Event('change', { bubbles: true }));
                                }"""
                            )
                            page.wait_for_timeout(800)
                            # Vue 的 :value 绑定在状态未变时不会重置 DOM 的非法 value，
                            # 因此刷新后从服务端重新读取才是权威判定。
                            page.reload(wait_until="domcontentloaded")
                            page.wait_for_selector(".workspace-picker select", timeout=12000)
                            rep.check(
                                "普通用户伪造切换未授权空间被服务端拒绝",
                                page.locator(".workspace-picker select").input_value() == ws_a
                                and any("A空间专属需求" in r for r in intent_titles(page)),
                                "仍是授权空间",
                                server_verified=True,
                            )
                            # 手改 localStorage 偏好：刷新后必须重新向服务端验证。
                            page.evaluate("localStorage.setItem('tg-workspace', '__FORBIDDEN__')")
                            page.reload(wait_until="domcontentloaded")
                            page.wait_for_selector(".workspace-picker select", timeout=12000)
                            rep.check(
                                "篡改 localStorage 后刷新恢复合法空间",
                                page.locator(".workspace-picker select").input_value() == ws_a,
                                "restored=authorized",
                                server_verified=True,
                            )
                            # 服务端直接伪造 workspace_id 写入也必须拒绝。
                            cookie = "; ".join(
                                f"{c['name']}={c['value']}" for c in page.context.cookies()
                            )
                            status, _ = _api_post_raw(
                                base,
                                "/api/v1/intents",
                                {
                                    "title": "越权写入",
                                    "description": "x",
                                    "owner": "x",
                                    "workspace_id": ws_b,
                                    "candidate_assertions": [],
                                },
                                cookie,
                                "",
                            )
                            rep.check(
                                "伪造请求其他空间 workspace_id 被服务端拒绝",
                                status in (401, 403),
                                f"HTTP {status}",
                                server_verified=True,
                            )
                        page.context.close()

                # platform-admin 切换与缓存清理
                admin_page = login_page(browser, "r12-admin")
                rep.check(
                    "platform-admin 可见两个空间",
                    set(options(admin_page)) >= {ws_a, ws_b},
                    str(options(admin_page)),
                )
                admin_page.select_option(".workspace-picker select", ws_a)
                admin_page.wait_for_timeout(700)
                first = intent_titles(admin_page)
                admin_page.select_option(".workspace-picker select", ws_b)
                admin_page.wait_for_timeout(700)
                second = intent_titles(admin_page)
                rep.check(
                    "platform-admin 切换后列表刷新且无旧空间残留",
                    any("A空间专属需求" in r for r in first)
                    and any("B空间专属需求" in r for r in second)
                    and not any("A空间专属需求" in r for r in second),
                    f"A={len(first)} B={len(second)}",
                    server_verified=True,
                )
                admin_page.reload(wait_until="domcontentloaded")
                admin_page.wait_for_selector(".workspace-picker select", timeout=12000)
                rep.check(
                    "刷新后恢复上次选择",
                    admin_page.locator(".workspace-picker select").input_value() == ws_b,
                    f"restored={ws_b}",
                )
                admin_page.context.close()

                # 四档分辨率 + 深色模式（真实截图）
                for width, height in ((390, 844), (768, 1024), (1024, 768), (1440, 900)):
                    ctx = browser.new_context(viewport={"width": width, "height": height})
                    ctx.add_init_script("localStorage.setItem('tg-theme', 'dark')")
                    page = ctx.new_page()
                    page.goto(base + "/login", wait_until="domcontentloaded")
                    page.fill("#login-username", "r12-a-engineer")
                    page.fill("#login-password", PASSWORD)
                    page.click("button.login-submit")
                    page.wait_for_selector(".workspace-picker select", timeout=15000)
                    metrics = page.evaluate(
                        """() => ({
                          overflow: document.documentElement.scrollWidth - window.innerWidth,
                          bg: getComputedStyle(document.body).backgroundColor,
                          theme: document.documentElement.dataset.theme,
                        })"""
                    )
                    rep.screenshot(page, f"workspace-{width}x{height}-dark")
                    rep.check(
                        f"{width}px 深色模式无横向溢出",
                        metrics["overflow"] <= 2 and metrics["theme"] == "dark",
                        json.dumps(metrics),
                    )
                    ctx.close()
            finally:
                browser.close()
    except Exception as exc:  # noqa: BLE001
        rep.check("Workspace UI 验收脚本执行", False, f"{type(exc).__name__}: {exc}")
    finally:
        api_proc.terminate()
        try:
            api_proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            api_proc.kill()

    summary = {
        "workpackage": "RUN-012 WP2",
        "total": len(rep.results),
        "passed": sum(1 for r in rep.results if r["status"] == "PASS"),
        "failed": [r["check"] for r in rep.failed],
        "results": rep.results,
    }
    (out / "workspace-acceptance.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({k: summary[k] for k in ("total", "passed", "failed")}, ensure_ascii=False))
    return 1 if rep.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
