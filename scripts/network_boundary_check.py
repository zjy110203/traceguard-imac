"""RUN-007 WP1-05：真实浏览器网络边界验收（两个本地服务 + Chromium）。

场景：
1. 跨域 fetch（allowed 页面里 fetch 未授权 origin）
2. 顶层 302（allowed 服务返回 Location: 未授权 origin）
3. 点击跳转（allowed 页面里的链接指向未授权 origin）
4. DNS rebinding（策略层，注入 resolver 模拟解析漂移）

关键验收：未授权目标服务器收到的请求数必须为 0。

用法：
    python scripts/network_boundary_check.py --out docs/assets/run007/network-boundary.json
"""

from __future__ import annotations

import argparse
import http.server
import json
import threading
import time
from pathlib import Path
from typing import Any


class Recorder:
    def __init__(self) -> None:
        self.hits: list[str] = []
        self.lock = threading.Lock()

    def snapshot(self) -> list[str]:
        with self.lock:
            return list(self.hits)


def _make_server(recorder: Recorder, *, redirect_to: str = "", page: bool = True):
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            with recorder.lock:
                recorder.hits.append(self.path)
            if self.path.startswith("/redirect") and redirect_to:
                self.send_response(302)
                self.send_header("Location", redirect_to + "/steal")
                self.end_headers()
                return
            if page:
                body = (
                    "<html><body><h1 id='t'>allowed</h1>"
                    f"<a id='out' data-testid='out' href='{redirect_to}/steal'>out</a>"
                    f"<script>fetch('{redirect_to}/steal').catch(()=>{{}});</script>"
                    "</body></html>"
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            body = b"forbidden"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: Any) -> None:
            return

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, server.server_address[1]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)

    from traceguard_contracts.models import TestStep
    from traceguard_core.security.target_policy import TargetPolicy
    from traceguard_execution.browser.manager import BrowserConfig, BrowserManager
    from traceguard_execution.browser.session import BrowserSession
    from traceguard_execution.runtime.playwright_observer import PlaywrightObserver

    forbidden_rec = Recorder()
    forbidden, forbidden_port = _make_server(forbidden_rec, page=False)
    allowed_rec = Recorder()
    allowed, allowed_port = _make_server(
        allowed_rec, redirect_to=f"http://127.0.0.1:{forbidden_port}"
    )
    allowed_base = f"http://127.0.0.1:{allowed_port}"
    scenarios: list[dict[str, Any]] = []

    manager = BrowserManager(BrowserConfig(headless=True))
    policy = TargetPolicy.from_origins([allowed_base], pin_dns=True)
    try:
        # 1) 跨域 fetch
        session = BrowserSession(
            manager,
            base_url=allowed_base,
            run_id="boundary-fetch",
            trace_enabled=False,
            target_policy=policy,
        )
        try:
            session.page.goto(f"{allowed_base}/page", wait_until="domcontentloaded")
            session.page.wait_for_timeout(700)
            blocked = [b for b in session.blocked_requests if "/steal" in b["url"]]
        finally:
            session.close()
        scenarios.append(
            {
                "name": "cross_origin_fetch",
                "blocked_records": blocked,
                "forbidden_hits_after": len(forbidden_rec.snapshot()),
            }
        )

        hits_after_fetch = len(forbidden_rec.snapshot())

        # 2) 顶层 302
        obs = PlaywrightObserver(
            manager, capture_screenshot=False, capture_dom=False, capture_aria=False
        )
        obs.setup({"base_url": allowed_base, "run_id": "boundary-redirect", "trace": False})
        try:
            record = obs.observe(
                TestStep(step_id="s1", action="navigate", value=f"{allowed_base}/redirect"), {}
            )
            redirects = list(obs.session.redirect_checks)
        finally:
            obs.teardown()
        scenarios.append(
            {
                "name": "top_level_302",
                "step_error": record.error,
                "redirect_checks": redirects,
                "blocked_records": (record.observation or {}).get("blocked_requests", []),
                "forbidden_hits_after": len(forbidden_rec.snapshot()),
                "forbidden_new_hits": len(forbidden_rec.snapshot()) - hits_after_fetch,
            }
        )

        hits_after_redirect = len(forbidden_rec.snapshot())

        # 3) 点击跳转
        obs = PlaywrightObserver(
            manager, capture_screenshot=False, capture_dom=False, capture_aria=False
        )
        obs.setup({"base_url": allowed_base, "run_id": "boundary-click", "trace": False})
        try:
            obs.observe(TestStep(step_id="s1", action="navigate", value=f"{allowed_base}/page"), {})
            click_record = obs.observe(
                TestStep(
                    step_id="s2", action="click", target={"strategy": "test_id", "value": "out"}
                ),
                {},
            )
        finally:
            obs.teardown()
        scenarios.append(
            {
                "name": "click_navigation",
                "step_error": click_record.error,
                "blocked_records": (click_record.observation or {}).get("blocked_requests", []),
                "forbidden_new_hits": len(forbidden_rec.snapshot()) - hits_after_redirect,
            }
        )
    finally:
        manager.close()
        for srv in (allowed, forbidden):
            srv.shutdown()
            srv.server_close()

    # 4) DNS rebinding（策略层，确定性验证）
    answers: dict[str, list[str]] = {"teamflow.example": ["203.0.113.10"]}
    dns_policy = TargetPolicy.from_origins(
        ["http://teamflow.example:8080"], resolver=lambda h: answers.get(h, []), pin_dns=True
    )
    before = dns_policy.check("http://teamflow.example:8080/projects", resolve_dns=True)
    answers["teamflow.example"] = ["10.0.0.5"]
    dns_policy._dns_cache.clear()  # noqa: SLF001 - 跳过缓存以模拟下一次解析
    after = dns_policy.check("http://teamflow.example:8080/projects", resolve_dns=True)
    scenarios.append(
        {
            "name": "dns_rebinding",
            "pinned": {k: sorted(v) for k, v in dns_policy.pinned_dns.items()},
            "before": {"allowed": before.allowed, "reason": before.reason},
            "after": {"allowed": after.allowed, "reason": after.reason},
        }
    )

    total_forbidden_hits = len(forbidden_rec.snapshot())
    checks = {
        "cross_origin_fetch_blocked": scenarios[0]["blocked_records"] != [],
        "top_level_302_blocked": bool(scenarios[1]["step_error"])
        and scenarios[1]["forbidden_new_hits"] == 0,
        "click_navigation_blocked": bool(scenarios[2]["step_error"])
        and scenarios[2]["forbidden_new_hits"] == 0,
        "dns_rebinding_detected": scenarios[3]["after"]["reason"] == "DNS_REBINDING_DETECTED",
        "unauthorized_server_zero_requests": total_forbidden_hits == 0,
    }
    report = {
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "allowed_base": allowed_base,
        "forbidden_port": forbidden_port,
        "allowed_hits": allowed_rec.snapshot(),
        "forbidden_hits": forbidden_rec.snapshot(),
        "scenarios": scenarios,
        "checks": checks,
        "passed": all(checks.values()),
        "limitation": (
            "本检查证明：跨域 fetch / 顶层 302 / 点击跳转都不再产生到达未授权目标的出站请求；"
            "DNS 部分由策略层注入 resolver 确定性验证。用户态无法接管系统 DNS，"
            "生产环境应叠加受控出站代理（见 docs/RUN-007-SECURITY-RELIABILITY.md）。"
        ),
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(checks, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
