"""RUN-006 WP1-05 真实浏览器验收：请求级目标策略（SSRF / 跨域 / 重定向）。

这不是策略函数的单测，而是真实 Chromium + 两个本地 HTTP 服务：
- allowed 服务（唯一授权 origin）返回一个会尝试 fetch 未授权 origin 的页面；
- forbidden 服务只记录"是否真的被访问"。

断言：未授权请求在浏览器层被 abort，forbidden 服务收到的请求数必须为 0；
重定向到未授权 origin 同样被阻断，并留下可审计的 blocked_requests 记录。
"""

from __future__ import annotations

import http.server
import threading
from collections.abc import Iterator
from typing import Any

import pytest
from traceguard_core.security.target_policy import TargetPolicy
from traceguard_execution.browser.manager import BrowserConfig, BrowserManager
from traceguard_execution.browser.session import BrowserSession

pytestmark = pytest.mark.e2e


class _Recorder:
    def __init__(self) -> None:
        self.hits: list[str] = []
        self.lock = threading.Lock()


def _make_server(recorder: _Recorder, *, redirect_to: str = "") -> tuple[Any, int]:
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            with recorder.lock:
                recorder.hits.append(self.path)
            if self.path.startswith("/redirect") and redirect_to:
                self.send_response(302)
                self.send_header("Location", redirect_to)
                self.end_headers()
                return
            body = (
                "<html><body><h1 id='t'>allowed</h1>"
                "<a id='out' data-testid='out' href='" + (redirect_to or "") + "/steal'>out</a>"
                "<script>fetch('" + (redirect_to or "") + "/steal').catch(()=>{});</script>"
                "</body></html>"
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: Any) -> None:  # noqa: D102
            return

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, server.server_address[1]


@pytest.fixture(scope="module")
def servers() -> Iterator[dict[str, Any]]:
    allowed_rec, forbidden_rec = _Recorder(), _Recorder()
    forbidden, forbidden_port = _make_server(forbidden_rec)
    allowed, allowed_port = _make_server(
        allowed_rec, redirect_to=f"http://127.0.0.1:{forbidden_port}"
    )
    try:
        yield {
            "allowed_port": allowed_port,
            "forbidden_port": forbidden_port,
            "allowed_hits": allowed_rec,
            "forbidden_hits": forbidden_rec,
            "allowed_base": f"http://127.0.0.1:{allowed_port}",
        }
    finally:
        for srv in (allowed, forbidden):
            srv.shutdown()
            srv.server_close()


@pytest.fixture(scope="module")
def manager() -> Iterator[BrowserManager]:
    mgr = BrowserManager(BrowserConfig(headless=True))
    try:
        yield mgr
    finally:
        mgr.close()


def _policy(servers: dict[str, Any]) -> TargetPolicy:
    return TargetPolicy.from_origins([servers["allowed_base"]])


def test_cross_origin_request_is_blocked_before_leaving(
    servers: dict[str, Any], manager: BrowserManager
) -> None:
    policy = _policy(servers)
    session = BrowserSession(
        manager,
        base_url=servers["allowed_base"],
        run_id="policy-cross-origin",
        trace_enabled=False,
        target_policy=policy,
    )
    try:
        session.page.goto(f"{servers['allowed_base']}/page", wait_until="domcontentloaded")
        session.page.wait_for_timeout(600)
        blocked = [b for b in session.blocked_requests if "steal" in b["url"]]
        assert blocked, session.blocked_requests
        assert blocked[0]["reason"] in {"PORT_NOT_ALLOWED", "HOST_NOT_ALLOWED"}
    finally:
        session.close()

    # 关键：未授权服务端没有收到任何请求（不是"发出后再丢弃"）
    assert servers["forbidden_hits"].hits == []


def test_redirect_landing_on_unsanctioned_origin_is_refused(
    servers: dict[str, Any], manager: BrowserManager
) -> None:
    """顶层导航被 302 到未授权 origin 时，Runner 必须拒绝继续（步骤失败）。

    RUN-007 WP1-05 修复：不再依赖"落地地址复核"的事后判断，而是在路由层用
    `route.fetch(max_redirects=0)` 自己取 3xx，检查 Location 后直接返回合成 403，
    因此**未授权目标服务器收到的请求数必须为 0**（此前会收到 1 次）。
    """
    from traceguard_contracts.models import TestStep
    from traceguard_execution.runtime.playwright_observer import PlaywrightObserver

    observer = PlaywrightObserver(
        manager, capture_screenshot=False, capture_dom=False, capture_aria=False
    )
    observer.setup(
        {"base_url": servers["allowed_base"], "run_id": "policy-redirect", "trace": False}
    )
    try:
        record = observer.observe(
            TestStep(
                step_id="s1",
                action="navigate",
                value=f"{servers['allowed_base']}/redirect",
            ),
            {},
        )
        redirects = [r for r in observer.session.redirect_checks if r["resolved"]]
        assert redirects and redirects[-1]["decision"] == "BLOCKED", redirects
    finally:
        observer.teardown()

    assert record.error, record.observation
    assert "TARGET_NOT_ALLOWED" in record.error
    assert record.observation.get("timed_out") is not True
    # 未授权目标仍应被记录在证据里（可审计），且步骤不可能是成功
    assert "page_url" in record.observation
    # 关键验收：未授权目标服务器没有收到任何请求
    assert servers["forbidden_hits"].hits == [], servers["forbidden_hits"].hits


def test_click_navigation_to_unsanctioned_origin_is_blocked(
    servers: dict[str, Any], manager: BrowserManager
) -> None:
    """点击链接跳转到未授权 origin：请求级 abort，目标服务器收到 0 请求。"""
    from traceguard_contracts.models import TestStep
    from traceguard_execution.runtime.playwright_observer import PlaywrightObserver

    observer = PlaywrightObserver(
        manager, capture_screenshot=False, capture_dom=False, capture_aria=False
    )
    observer.setup({"base_url": servers["allowed_base"], "run_id": "policy-click", "trace": False})
    try:
        observer.observe(
            TestStep(step_id="s1", action="navigate", value=f"{servers['allowed_base']}/page"), {}
        )
        record = observer.observe(
            TestStep(
                step_id="s2",
                action="click",
                target={"strategy": "test_id", "value": "out"},
            ),
            {},
        )
    finally:
        observer.teardown()

    assert record.error, record.observation
    assert "TARGET_NOT_ALLOWED" in record.error
    assert servers["forbidden_hits"].hits == [], servers["forbidden_hits"].hits


def test_allowed_origin_still_works(servers: dict[str, Any], manager: BrowserManager) -> None:
    """策略不能把正常目标一起挡掉（否则等于把被测应用变成不可测）。"""
    policy = _policy(servers)
    session = BrowserSession(
        manager,
        base_url=servers["allowed_base"],
        run_id="policy-allowed",
        trace_enabled=False,
        target_policy=policy,
    )
    try:
        resp = session.page.goto(f"{servers['allowed_base']}/page", wait_until="domcontentloaded")
        assert resp is not None and resp.status == 200
        assert session.page.inner_text("#t") == "allowed"
        session.check_target(session.current_url(), landed=True)  # 不抛异常
    finally:
        session.close()

    assert "/page" in servers["allowed_hits"].hits


def test_policy_rejects_protocol_relative_navigation(servers: dict[str, Any]) -> None:
    """绝对/协议相对 URL 不能绕过白名单（策略层，不依赖浏览器）。"""
    policy = _policy(servers)
    assert policy.resolve("//evil.example.com/x", servers["allowed_base"]).allowed is False
    assert policy.resolve("/page", servers["allowed_base"]).allowed is True
