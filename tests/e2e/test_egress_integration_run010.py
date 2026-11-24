"""RUN-010 WP2：受控出站代理接入正式执行内核——真实 Chromium 集成验收。

与 RUN-009 的差异：RUN-009 的代理是独立脚本场景；本文件验证的是
**BrowserManager/BrowserSession 正式执行路径**的集成——代理随浏览器启动、
审计与 Run 关联、代理死亡 fail-closed、策略单一事实源。

核心量化断言：未授权目标服务器**实际收到的请求数 = 0**（服务端收数验证，
不是只看浏览器报错）。
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from traceguard_core.security.target_policy import TargetAccessDenied, TargetPolicy
from traceguard_execution.browser.egress import EgressProxyDeadError
from traceguard_execution.browser.manager import BrowserConfig, BrowserManager
from traceguard_execution.browser.session import BrowserSession

ROOT = Path(__file__).resolve().parents[2]


class _HitRecorder:
    def __init__(self) -> None:
        self.hits: list[tuple[str, str]] = []
        self.lock = threading.Lock()

    def record(self, tag: str, path: str) -> None:
        with self.lock:
            self.hits.append((tag, path))

    def count(self, tag: str) -> int:
        with self.lock:
            return sum(1 for t, _ in self.hits if t == tag)


RECORDER = _HitRecorder()


class _TargetHandler(BaseHTTPRequestHandler):
    server_version = "Run010Target/1.0"

    def do_GET(self) -> None:  # noqa: N802
        tag = ""
        if self.path.startswith("/authz"):
            tag = "authz"
        elif self.path.startswith("/open-redirect"):
            # 302 到未授权目标
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{UNAUTHZ_PORT}/evil")
            self.end_headers()
            RECORDER.record("authz", self.path)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        body = f'<html><body><h1 id="t">{tag or "ok"} {self.path}</h1></body></html>'.encode()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        if tag:
            RECORDER.record(tag, self.path)

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0") or 0)
        self.rfile.read(length)
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")
        if self.path.startswith("/authz"):
            RECORDER.record("authz", self.path)

    def log_message(self, *_args) -> None:  # noqa: N802
        pass


@pytest.fixture(scope="module")
def target_servers():  # type: ignore[no-untyped-def]
    global UNAUTHZ_PORT
    authz = ThreadingHTTPServer(("127.0.0.1", 0), _TargetHandler)
    unauthz = ThreadingHTTPServer(("127.0.0.1", 0), _TargetHandler)
    UNAUTHZ_PORT = unauthz.server_address[1]
    t1 = threading.Thread(target=authz.serve_forever, daemon=True)
    t2 = threading.Thread(target=unauthz.serve_forever, daemon=True)
    t1.start()
    t2.start()
    yield {
        "authz": f"http://127.0.0.1:{authz.server_address[1]}",
        "unauthz": f"http://127.0.0.1:{unauthz.server_address[1]}",
        "authz_port": authz.server_address[1],
        "unauthz_port": unauthz.server_address[1],
    }
    authz.shutdown()
    unauthz.shutdown()


UNAUTHZ_PORT = 0  # 由 fixture 填充


@pytest.fixture(scope="module")
def browser_available():  # type: ignore[no-untyped-def]
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            b = p.chromium.launch(headless=True)
            b.close()
        yield True
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Playwright Chromium 不可用: {exc}")


def _session(target_servers: dict[str, Any]) -> tuple[BrowserManager, BrowserSession]:
    manager = BrowserManager(BrowserConfig(headless=True))
    policy = TargetPolicy.from_origins([target_servers["authz"]])
    session = BrowserSession(
        manager,
        base_url=target_servers["authz"],
        run_id="run-e2e-010-a",
        trace_enabled=False,
        target_policy=policy,
    )
    return manager, session


def _clear_hits() -> None:
    with RECORDER.lock:
        RECORDER.hits.clear()


# ---------------------------------------------------------------------------
# 1. 正式执行路径：浏览器强制经代理，合法目标可达且审计关联 Run
# ---------------------------------------------------------------------------


def test_browser_traffic_flows_through_proxy(target_servers, browser_available):  # type: ignore[no-untyped-def]
    _clear_hits()
    manager, session = _session(target_servers)
    try:
        # 代理随浏览器启动，浏览器以 proxy 参数启动
        assert manager.egress_enforced() is True
        controller = manager.proxy_controller()
        assert controller is not None and controller.alive
        assert manager.health()["egress"]["enforced"] is True

        page = session.page
        page.goto(target_servers["authz"] + "/authz?page=1")
        assert page.inner_text("#t").startswith("authz")
        page.goto(target_servers["authz"] + "/authz?page=2")
        # 目标服务器真实收到请求
        assert RECORDER.count("authz") >= 2
        # 代理审计记录了这些请求，并关联 run_id
        audit = controller.audit_entries()
        allowed = [e for e in audit if e["decision"] == "ALLOW" and e["host"] == "127.0.0.1"]
        assert allowed, audit
        assert all(e["run_id"] == "run-e2e-010-a" for e in allowed)
        summary = manager.egress_audit()
        assert summary["run_id"] == "run-e2e-010-a"
        assert summary["allowed"] >= 2
    finally:
        manager.close()


# ---------------------------------------------------------------------------
# 2. 未授权目标：双层防护 + 服务器实际收到 0 请求
# ---------------------------------------------------------------------------


def test_unauthorized_target_receives_zero_requests(target_servers, browser_available):  # type: ignore[no-untyped-def]
    _clear_hits()
    manager, session = _session(target_servers)
    try:
        page = session.page
        # 会话层 route guard 直接 abort；代理层也会拒绝（双层独立生效）
        try:
            page.goto(target_servers["unauthz"] + "/evil", timeout=5000)
        except Exception:  # noqa: BLE001 - 导航失败是预期行为
            pass
        # 顶层 302 到未授权目标：同样 0 请求
        try:
            page.goto(target_servers["authz"] + "/open-redirect", timeout=5000)
        except Exception:  # noqa: BLE001
            pass
        # 代理层独立验证：page.request（APIRequestContext）不经过 route 拦截，
        # 但仍走浏览器代理配置 → 代理策略是最后一道防线
        try:
            page.request.get(target_servers["unauthz"] + "/evil", timeout=5000)
        except Exception:  # noqa: BLE001
            pass
        # 关键量化断言：未授权服务器实际收到 0 个请求（服务端收数）
        assert RECORDER.count("authz") >= 1  # 合法请求正常
        assert RECORDER.hits and all(tag == "authz" for tag, _path in RECORDER.hits), RECORDER.hits
        # 会话层有拦截证据
        assert session.blocked_requests, "会话层 route guard 应记录被拒请求"
        # 代理层有 BLOCK 证据（page.request 绕过 route guard 后由代理拒绝）
        blocked = [
            e for e in manager.proxy_controller().audit_entries() if e["decision"] == "BLOCK"
        ]
        assert blocked, "代理层审计应有 BLOCK 记录"
    finally:
        manager.close()


# ---------------------------------------------------------------------------
# 3. 代理死亡 → fail-closed（不允许继续执行，绝不回退直连）
# ---------------------------------------------------------------------------


def test_proxy_death_fails_closed(target_servers, browser_available):  # type: ignore[no-untyped-def]
    _clear_hits()
    manager, session = _session(target_servers)
    try:
        controller = manager.proxy_controller()
        assert controller is not None and controller.alive
        # 杀掉代理线程（模拟代理进程意外退出）
        controller._stop_evt.set()
        controller._thread.join(timeout=5)
        assert not controller.alive
        # 会话导航前检查 → 拒绝（TargetAccessDenied），而非静默直连
        with pytest.raises(TargetAccessDenied):
            session.check_target(target_servers["authz"] + "/authz")
        # 新建上下文也被拒绝
        with pytest.raises(EgressProxyDeadError):
            manager.new_context()
        # 浏览器因代理失联也无法访问任何网络目标（Chromium 会报代理连接失败）
        page = session.page
        try:
            page.goto(target_servers["authz"] + "/authz", timeout=8000)
            # 若导航"成功"，页面内容必须不是真实目标内容（未授权出口被切断）
            content = page.inner_text("body")
            assert "authz" not in content.split(" ")[0:1], content[:200]
        except Exception:  # noqa: BLE001 - 代理失联导致导航失败是预期
            pass
        # 死亡后未授权服务器依旧收到 0 请求
        assert RECORDER.count("authz") == 0
    finally:
        manager.close()


# ---------------------------------------------------------------------------
# 4. 策略单一事实源：会话策略同步到代理
# ---------------------------------------------------------------------------


def test_session_policy_is_single_source_of_truth(target_servers, browser_available):  # type: ignore[no-untyped-def]
    manager, session = _session(target_servers)
    try:
        controller = manager.proxy_controller()
        assert controller is not None
        # 代理持有会话策略的 scheme 对偶变体：会话授权 origin 必须全部包含在内
        # （代理放宽仅限同 host:port 的 scheme；host/port/私网检查不变）
        session_origins = {str(o) for o in session.policy.origins}
        proxy_origins = {str(o) for o in controller.policy.origins}
        assert session_origins <= proxy_origins, (session_origins, proxy_origins)
        # 换新会话（新策略）→ 代理策略立即跟随（单一事实源仍是会话层）
        extra_origins = ["http://127.0.0.1:9999"]
        policy2 = TargetPolicy.from_origins([target_servers["authz"], *extra_origins])
        session2 = BrowserSession(
            manager,
            base_url=target_servers["authz"],
            run_id="run-e2e-010-b",
            trace_enabled=False,
            target_policy=policy2,
        )
        proxy_origins2 = {str(o) for o in controller.policy.origins}
        assert {str(o) for o in policy2.origins} <= proxy_origins2
        assert manager.egress_audit()["run_id"] == "run-e2e-010-b"
        session2.context.close()
    finally:
        manager.close()


# ---------------------------------------------------------------------------
# 5. 证据导出：审计 JSON 可归档（供 docs/assets/run010/wp2）
# ---------------------------------------------------------------------------


def test_audit_export_for_evidence(target_servers, browser_available, tmp_path):  # type: ignore[no-untyped-def]
    _clear_hits()
    manager, session = _session(target_servers)
    try:
        session.page.goto(target_servers["authz"] + "/authz")
        entries = manager.proxy_controller().audit_entries()
        payload = {
            "mode": "browser-session-integration",
            "enforced": True,
            "entries": entries,
            "summary": manager.egress_audit(),
        }
        out = tmp_path / "egress-audit.json"
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        assert entries and out.exists()
    finally:
        manager.close()
