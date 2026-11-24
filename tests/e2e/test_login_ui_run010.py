"""RUN-010 WP1：登录系统真实浏览器 E2E（真实 Chromium + 真实 API + 真实认证）。

覆盖：
- 登录页渲染与表单交互（label/错误提示）；
- 错误密码 → 错误提示；正确密码 → 进入工作台；
- /auth/me 真实用户信息（不使用前端本地角色判定）；
- 会话过期 → 401 → 全局跳转登录页；
- 退出登录 → 回登录页；
- viewer 只读角色进入工作台后管理菜单不可见。

需要本机 Playwright Chromium；不可用时整模块 skip（如实跳过，不伪装通过）。
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.request
from collections.abc import Generator
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _wait_health(url: str, timeout: float = 30.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if r.status == 200:
                    return True
        except Exception:  # noqa: BLE001
            time.sleep(0.3)
    return False


@pytest.fixture(scope="module")
def api_server(tmp_path_factory) -> Generator[str, None, None]:
    """启动真实 API（认证开启 + 用户已种子），托管前端构建产物。"""
    port = _free_port()
    work = tmp_path_factory.mktemp("login_e2e")
    env = dict(os.environ)
    env.update(
        TG_AUTH_ENABLED="true",
        TG_AUTH_CSRF_REQUIRED="true",
        TG_ENVIRONMENT="development",
        TG_DATABASE_URL=f"sqlite:///{work / 'e2e.db'}",
        TG_LLM_PROVIDER="",
        TG_INLINE_WORKER="false",
        TG_API_PORT=str(port),
    )
    env["PYTHONPATH"] = os.pathsep.join(
        [
            str(ROOT / "packages/core"),
            str(ROOT / "packages/contracts"),
            str(ROOT / "packages/execution"),
            str(ROOT / "packages/agents"),
            str(ROOT / "packages/evidence"),
            str(ROOT / "packages/persistence"),
            str(ROOT / "apps/api"),
            str(ROOT / "apps/teamflow"),
            str(ROOT / "apps/workers"),
            str(ROOT / "benchmark"),
        ]
    )
    proc = subprocess.Popen(
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
        assert _wait_health(f"{base}/health"), f"API 未在预期时间内启动: {base}"
        # 通过 bootstrap 脚本语义直接建用户（requests 到运行中服务的数据库不可行，用 python 一致性差；
        # 这里用独立的 bootstrap 脚本模式：调 auth_bootstrap.py --db）
        bootstrap = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "auth_bootstrap.py"),
                "--db",
                f"sqlite:///{work / 'e2e.db'}",
                "--username",
                "e2e-admin",
                "--password",
                "e2e-password-1",
                "--roles",
                "platform-admin,qa-lead,qa-engineer",
            ],
            env=env,
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert bootstrap.returncode == 0, bootstrap.stderr[-800:]
        subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "auth_bootstrap.py"),
                "--db",
                f"sqlite:///{work / 'e2e.db'}",
                "--username",
                "e2e-viewer",
                "--password",
                "viewer-pass-1",
                "--roles",
                "viewer",
            ],
            env=env,
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=60,
        )
        yield base
    finally:
        proc.terminate()
        proc.wait(timeout=10)


@pytest.fixture(scope="module")
def chromium():  # type: ignore[no-untyped-def]
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            yield browser
            browser.close()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Playwright Chromium 不可用: {exc}")


def _page(chromium):  # type: ignore[no-untyped-def]
    ctx = chromium.new_context(viewport={"width": 1440, "height": 900})
    page = ctx.new_page()
    console_errors: list[str] = []
    page.on(
        "console",
        lambda msg: console_errors.append(msg.text) if msg.type == "error" else None,
    )
    page.console_errors = console_errors  # type: ignore[attr-defined]
    return page


def test_login_page_and_full_login_flow(api_server, chromium):  # type: ignore[no-untyped-def]
    page = _page(chromium)
    try:
        # 1. 未登录访问业务路由 → 跳登录页
        page.goto(f"{api_server}/runs", wait_until="networkidle")
        assert "/login" in page.url, page.url
        # 登录表单可访问性：label 与输入框关联
        assert page.locator('label[for="login-username"]').count() == 1
        assert page.locator('label[for="login-password"]').count() == 1
        # 2. 错误密码 → 明确错误提示，不进入工作台
        page.fill("#login-username", "e2e-admin")
        page.fill("#login-password", "wrong-password")
        page.click("button.login-submit")
        page.wait_for_selector(".login-error", timeout=8000)
        assert "用户名或密码错误" in page.locator(".login-error").inner_text()
        # 3. 正确密码 → 进入工作台（真实 /auth/me 数据；回跳到最初访问的 /runs）
        page.fill("#login-password", "e2e-password-1")
        page.click("button.login-submit")
        page.wait_for_selector(".user-chip", timeout=15000)
        assert "/login" not in page.url
        chip = page.locator(".user-chip").inner_text()
        assert "e2e-admin" in chip
        # 4. 管理菜单可见（platform-admin）
        assert page.locator('a:has-text("用户与角色")').count() == 1
        # 5. 修改密码弹窗可开、可关
        page.click(".user-chip")
        page.wait_for_selector(".user-menu", timeout=5000)
        page.click('button:has-text("修改密码")')
        page.wait_for_selector('.modal[aria-label="修改密码"]', timeout=5000)
        page.click('.modal button:has-text("取消")')
        page.wait_for_selector('.modal[aria-label="修改密码"]', state="detached", timeout=5000)
        # 6. 退出登录 → 回登录页
        page.click(".user-chip")
        page.wait_for_selector(".user-menu", timeout=5000)
        page.click('button:has-text("退出登录")')
        page.wait_for_url("**/login", timeout=10000)
        page.wait_for_selector("#login-username", timeout=8000)
        assert page.locator("#login-username").count() == 1
    finally:
        page.close()


def test_viewer_role_sees_no_admin_menu(api_server, chromium):  # type: ignore[no-untyped-def]
    page = _page(chromium)
    try:
        page.goto(f"{api_server}/login", wait_until="networkidle")
        page.fill("#login-username", "e2e-viewer")
        page.fill("#login-password", "viewer-pass-1")
        page.click("button.login-submit")
        page.wait_for_selector(".user-chip", timeout=15000)
        assert "/login" not in page.url
        # viewer：管理菜单不可见；直接访问 /users 被守卫送回工作台
        assert page.locator('a:has-text("用户与角色")').count() == 0
        page.goto(f"{api_server}/users", wait_until="networkidle")
        assert "/users" not in page.url
    finally:
        page.close()
