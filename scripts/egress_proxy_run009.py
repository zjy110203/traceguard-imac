"""RUN-009 WP3：受控出站代理真实验证（本地服务 + Playwright Chromium）。

本脚本不修改 BrowserSession；它把 Chromium 的**全部** HTTP/HTTPS/WS 流量强制
经 `EgressProxy`，然后统计每个未授权服务器实际收到的请求数。核心断言：
**未授权服务器收到的请求数 == 0**。

运行：
    .venv/bin/python scripts/egress_proxy_run009.py \
        --out docs/assets/run009/wp3/egress-proxy-evidence.json

端口全部使用 0（OS 自动分配），避开 8000/8011/8020/8042/8050/5173/5175。
本脚本不读取 .env 或任何凭据。
"""

from __future__ import annotations

import argparse
import asyncio
import http.server
import json
import socket
import sys
import threading
import time
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
for _p in (
    _ROOT,
    _ROOT / "packages/core",
    _ROOT / "packages/contracts",
    _ROOT / "packages/execution",
    _ROOT / "apps/api",
    _ROOT / "apps/teamflow",
):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from traceguard_core.security.egress_proxy import EgressProxy  # noqa: E402
from traceguard_core.security.target_policy import TargetPolicy  # noqa: E402

# --------------------------------------------------------------------------- 记录器


class Recorder:
    """线程安全的访问记录器。"""

    def __init__(self, name: str) -> None:
        self.name = name
        self.hits: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def add(self, *, method: str, path: str, headers: dict[str, str] | None = None) -> None:
        with self._lock:
            self.hits.append(
                {
                    "at_ms": int(time.time() * 1000),
                    "method": method,
                    "path": path,
                    # 只记录少量非敏感头名，不记录任何值（认证头绝不落盘）
                    "header_names": sorted((headers or {}).keys()),
                }
            )

    def paths(self) -> list[str]:
        with self._lock:
            return [h["path"] for h in self.hits]

    def count(self) -> int:
        with self._lock:
            return len(self.hits)

    def snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self.hits)


# --------------------------------------------------------------------------- HTTP 服务


class _Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _record(self) -> None:
        rec: Recorder = self.server.recorder  # type: ignore[attr-defined]
        rec.add(
            method=self.command,
            path=self.path,
            headers={k.lower(): v for k, v in self.headers.items()},
        )

    def _send(self, status: int, body: bytes, *, content_type: str = "text/plain") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _redirect(self, location: str) -> None:
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        self._record()
        cfg: dict[str, Any] = self.server.cfg  # type: ignore[attr-defined]
        mode = cfg.get("mode", "ok")
        if mode == "redirect_map" and self.path in cfg.get("redirects", {}):
            self._redirect(cfg["redirects"][self.path])
            return
        if mode == "forbidden":
            self._send(200, b"FORBIDDEN-TARGET-REACHED")
            return
        body = (
            b"<!doctype html><html><body><h1 id='t'>allowed</h1>"
            b"<p id='ready'>ready</p></body></html>"
        )
        self._send(200, body, content_type="text/html; charset=utf-8")

    def log_message(self, fmt: str, *args: Any) -> None:
        return  # 静默，避免噪声日志


class _IPv6ThreadingHTTPServer(http.server.ThreadingHTTPServer):
    address_family = socket.AF_INET6


def make_http_server(
    recorder: Recorder,
    *,
    host: str = "127.0.0.1",
    port: int = 0,
    mode: str = "ok",
    redirects: dict[str, str] | None = None,
) -> http.server.ThreadingHTTPServer:
    cls = _IPv6ThreadingHTTPServer if ":" in host else http.server.ThreadingHTTPServer
    server = cls((host, port), _Handler)
    server.recorder = recorder  # type: ignore[attr-defined]
    server.cfg = {"mode": mode, "redirects": redirects or {}}  # type: ignore[attr-defined]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


# ------------------------------------------------------------------- 异步运行容器


class AsyncRuntime:
    """单个后台线程 + 单个事件循环，同时运行代理和 WebSocket 回显服务。"""

    def __init__(self, policy: TargetPolicy, *, ws_port: int) -> None:
        self.policy = policy
        self.loop = asyncio.new_event_loop()
        self.proxy = EgressProxy(policy, host="127.0.0.1", port=0)
        self.proxy_port = 0
        self.ws_port = ws_port
        self.ws_server: Any = None
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self._boot())
        self._ready.set()
        self.loop.run_forever()

    async def _boot(self) -> None:
        from websockets.asyncio.server import serve as ws_serve

        self.proxy_port = await self.proxy.start()

        async def echo(ws: Any) -> None:
            async for msg in ws:
                await ws.send(f"echo:{msg}")

        self.ws_server = await ws_serve(echo, "127.0.0.1", self.ws_port)

    def start(self) -> None:
        self._thread.start()
        if not self._ready.wait(timeout=15):
            raise RuntimeError("ASYNC_RUNTIME_TIMEOUT")

    def call(self, coro: Any) -> Any:
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout=20)

    def stop(self) -> None:
        async def _shutdown() -> None:
            await self.proxy.stop()
            self.ws_server.close()
            await self.ws_server.wait_closed()

        try:
            self.call(_shutdown())
        except Exception:  # noqa: BLE001
            pass
        self.loop.call_soon_threadsafe(self.loop.stop)
        self._thread.join(timeout=5)


# ----------------------------------------------------------------------- 原始请求


def raw_proxy_request(
    proxy_port: int,
    request: bytes,
    *,
    timeout: float = 3.0,
) -> bytes:
    """用裸 socket 直接和代理对话，用于协议级/决策级断言。"""
    sock = socket.create_connection(("127.0.0.1", proxy_port), timeout=timeout)
    try:
        sock.sendall(request)
        sock.settimeout(timeout)
        chunks: list[bytes] = []
        total = 0
        while total < 65_536:
            try:
                data = sock.recv(4096)
            except TimeoutError:
                break
            if not data:
                break
            chunks.append(data)
            total += len(data)
        return b"".join(chunks)
    finally:
        sock.close()


def _status(response: bytes) -> int:
    first = response.split(b"\r\n", 1)[0]
    parts = first.split(b" ")
    try:
        return int(parts[1]) if len(parts) >= 2 else 0
    except ValueError:
        return 0


def _reason(response: bytes) -> str:
    text = response.decode("latin-1", errors="replace")
    return text.split("\r\n\r\n", 1)[-1].strip()[:120]


# ------------------------------------------------------------------------- 主流程


def run_all_scenarios(out_dir: Path) -> dict[str, Any]:  # noqa: C901, PLR0915
    from playwright.sync_api import sync_playwright

    scenarios: list[dict[str, Any]] = []
    unauthorized: dict[str, Recorder] = {}
    authorized: dict[str, Recorder] = {}

    def unauthorized_server(name: str, **kwargs: Any):
        rec = Recorder(name)
        unauthorized[name] = rec
        srv = make_http_server(rec, mode="forbidden", **kwargs)
        port = srv.server_address[1]
        return srv, port, f"http://127.0.0.1:{port}"

    allowed_rec = Recorder("allowed")
    authorized["allowed"] = allowed_rec
    allowed_srv = make_http_server(allowed_rec, mode="ok")
    allowed_port = allowed_srv.server_address[1]
    allowed_base = f"http://127.0.0.1:{allowed_port}"

    forbidden_srv, forbidden_port, forbidden_base = unauthorized_server("forbidden")
    forbidden_rec = unauthorized["forbidden"]

    # 多跳链：/chain1 -> /chain2（均授权）-> 未授权 /steal
    chain_rec = Recorder("chain-allowed")
    authorized["chain"] = chain_rec
    chain_srv = make_http_server(chain_rec, mode="redirect_map")
    chain_port = chain_srv.server_address[1]
    chain_base = f"http://127.0.0.1:{chain_port}"
    chain_srv.cfg["redirects"].update(  # type: ignore[attr-defined]
        {
            "/chain1": f"{chain_base}/chain2",
            "/chain2": f"{forbidden_base}/steal",
            "/redirect_ext": f"{forbidden_base}/steal",
            "/redirect_ok": f"{chain_base}/ok",
        }
    )

    # IPv6：一个授权，一个未授权（同一地址族不同端口）
    v6_rec = Recorder("ipv6-authorized")
    authorized["ipv6"] = v6_rec
    v6_srv = make_http_server(v6_rec, host="::1", mode="ok")
    v6_port = v6_srv.server_address[1]
    v6_base = f"http://[::1]:{v6_port}"

    v6_bad_rec = Recorder("ipv6-forbidden")
    unauthorized["ipv6-forbidden"] = v6_bad_rec
    v6_bad_srv = make_http_server(v6_bad_rec, host="::1", mode="forbidden")
    v6_bad_port = v6_bad_srv.server_address[1]
    v6_bad_base = f"http://[::1]:{v6_bad_port}"

    # DNS rebinding 的「变化后」落点：绑定在 ::1 的同端口（见下方 resolver）。
    dns_flip_rec = Recorder("dns-flip-target")
    unauthorized["dns-flip-target"] = dns_flip_rec

    # 注入 resolver：safe.example 初始钉住 127.0.0.1；翻转后返回 ::1。
    dns_state = {"answer": ["127.0.0.1"]}

    def resolver(host: str) -> list[str]:
        if host == "safe.example":
            return list(dns_state["answer"])
        if host == "evil.example":
            return ["127.0.0.1"]  # 未授权域名解析到回环：必须被拒绝
        return []

    # 预分配 WS 端口（避免 bootstrap 进程；端口 0 由 OS 分配）。
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    ws_port = int(probe.getsockname()[1])
    probe.close()

    # DNS 翻转演示：safe.example 初始钉住 127.0.0.1:<port>，翻转后解析到 ::1:<同 port>。
    dns_v4_rec = Recorder("dns-v4-before-flip")
    authorized["dns-v4"] = dns_v4_rec
    dns_v4_srv = make_http_server(dns_v4_rec, host="127.0.0.1", mode="ok")
    dns_flip_port = dns_v4_srv.server_address[1]
    try:
        dns_flip_srv = make_http_server(
            dns_flip_rec, host="::1", port=dns_flip_port, mode="forbidden"
        )
    except OSError:
        # 同端口双栈绑定失败：翻转断言退化为「连接尝试为 0」（audit connected=False）
        dns_flip_srv = None

    origins = [
        allowed_base,
        chain_base,
        v6_base,
        f"http://safe.example:{dns_flip_port}",
        f"ws://127.0.0.1:{ws_port}",
        f"https://127.0.0.1:{ws_port}",  # 兼容 Chromium 对 ws:// 使用 CONNECT 的情况
    ]
    policy = TargetPolicy.from_origins(
        origins,
        resolver=resolver,
        pin_dns=False,
    )
    # 显式钉住 safe.example 的初始答案（本地服务，便于端到端验证）
    policy.pinned_dns["safe.example"] = frozenset({"127.0.0.1"})

    runtime = AsyncRuntime(policy, ws_port=ws_port)
    runtime.start()
    proxy_port = runtime.proxy_port
    proxy_url = f"http://127.0.0.1:{proxy_port}"

    checks: dict[str, Any] = {}
    raw_evidence: dict[str, Any] = {}

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=True,
                # --enable-automation 让 CDP Browser.getBrowserCommandLine 返回真实命令行
                args=["--enable-automation"],
                proxy={
                    "server": proxy_url,
                    # Chromium 默认对 loopback 绕过代理；<-loopback> 取消该隐式绕过，
                    # 强制 127.0.0.1 / ::1 也走代理，否则整个验证不成立。
                    "bypass": "<-loopback>",
                },
            )
            try:
                # 记录真实 Chromium 命令行，证明 --proxy-bypass-list=<-loopback> 已生效。
                # 用 CDP Browser.getBrowserCommandLine 而不是 ps（沙箱下 ps 全量列举被拒）。
                argv_lines: list[str] = []
                context = browser.new_context()
                page = context.new_page()
                try:
                    cdp = context.new_cdp_session(page)
                    argv_lines = list(
                        cdp.send("Browser.getBrowserCommandLine").get("arguments", [])
                    )
                except Exception as exc:  # noqa: BLE001
                    argv_lines = [f"ARGV_CAPTURE_UNAVAILABLE: {exc}"]
                raw_evidence["chromium_proxy_argv"] = [
                    arg
                    for arg in argv_lines
                    if "--proxy" in arg or "--host-resolver" in arg or "ARGV_CAPTURE" in arg
                ]
                raw_evidence["playwright_proxy_config"] = {
                    "server": proxy_url,
                    "bypass": "<-loopback>",
                    "note": (
                        "Playwright 把 bypass 映射为 Chromium 的 "
                        "--proxy-bypass-list=<-loopback>；功能证据是 127.0.0.1/::1 "
                        "请求确实出现在代理审计中且未授权目标 0 命中。"
                    ),
                }

                def nav(url: str) -> dict[str, Any]:
                    try:
                        resp = page.goto(url, wait_until="domcontentloaded", timeout=8_000)
                        page.wait_for_timeout(120)
                        return {"status": resp.status if resp else None, "url": page.url}
                    except Exception as exc:  # noqa: BLE001
                        return {"status": None, "url": page.url, "error": str(exc)[:200]}

                def js(expr: str) -> Any:
                    return page.evaluate(expr)

                # ---- 1. 合法目标
                r = nav(f"{allowed_base}/page")
                fetch_status = js("fetch('/ok').then(r=>r.status)")
                scenarios.append(
                    {
                        "name": "allowed_target",
                        "navigation": r,
                        "fetch_status": fetch_status,
                        "allowed_hits": allowed_rec.paths(),
                    }
                )
                checks["allowed_target_200"] = r["status"] == 200 and fetch_status == 200

                # ---- 2. 未授权目标（直接导航）
                r = nav(f"{forbidden_base}/steal")
                scenarios.append(
                    {
                        "name": "unauthorized_target",
                        "navigation": r,
                        "forbidden_hits": forbidden_rec.paths(),
                    }
                )
                checks["unauthorized_http_status_403"] = r["status"] == 403
                checks["unauthorized_target_zero_requests"] = forbidden_rec.count() == 0

                # ---- 3. 跨域 fetch
                nav(f"{allowed_base}/page")
                fetch_result = js(
                    f"""async () => {{
                        try {{
                          const r = await fetch('{forbidden_base}/steal');
                          return 'status:' + r.status;
                        }} catch (e) {{ return 'rejected:' + e.name; }}
                    }}"""
                )
                scenarios.append(
                    {
                        "name": "cross_origin_fetch",
                        "fetch_result": fetch_result,
                        "forbidden_hits": forbidden_rec.paths(),
                    }
                )
                checks["cross_origin_fetch_zero_requests"] = forbidden_rec.count() == 0

                # ---- 4. 顶层 302
                before = forbidden_rec.count()
                r = nav(f"{chain_base}/redirect_ext")
                scenarios.append(
                    {
                        "name": "top_level_302",
                        "navigation": r,
                        "final_url": page.url,
                        "unauthorized_new_hits": forbidden_rec.count() - before,
                    }
                )
                checks["top_level_302_zero_requests"] = forbidden_rec.count() - before == 0

                # ---- 5. 多跳重定向（末跳未授权）
                before = forbidden_rec.count()
                r = nav(f"{chain_base}/chain1")
                scenarios.append(
                    {
                        "name": "multi_hop_redirect",
                        "navigation": r,
                        "chain_hits": chain_rec.paths(),
                        "unauthorized_new_hits": forbidden_rec.count() - before,
                    }
                )
                checks["multi_hop_redirect_zero_requests"] = (
                    forbidden_rec.count() - before == 0
                    and "/chain1" in chain_rec.paths()
                    and "/chain2" in chain_rec.paths()
                )

                # ---- 6. DNS 变化（rebinding）：用导航而非 fetch，避免 CORS 干扰判定
                dns_before = nav(f"http://safe.example:{dns_flip_port}/before-flip")
                dns_state["answer"] = ["::1"]
                policy._dns_cache.clear()  # noqa: SLF001 - 模拟下一次解析
                dns_after = nav(f"http://safe.example:{dns_flip_port}/steal2")
                scenarios.append(
                    {
                        "name": "dns_change",
                        "pinned": sorted(policy.pinned_dns.get("safe.example", ())),
                        "answer_after": dns_state["answer"],
                        "navigation_before": dns_before,
                        "navigation_after": dns_after,
                        "dns_v4_hits": dns_v4_rec.paths(),
                        "flip_target_hits": dns_flip_rec.paths(),
                    }
                )
                checks["dns_change_blocked"] = (
                    dns_before["status"] == 200
                    and dns_after["status"] == 403
                    and dns_flip_rec.count() == 0
                )

                # ---- 7. WebSocket（授权 + 未授权）
                nav(f"{allowed_base}/page")
                ws_ok = js(
                    f"""async () => new Promise(resolve => {{
                        let done=false; const fin=v=>{{if(!done){{done=true;resolve(v)}}}};
                        try {{
                          const ws=new WebSocket('ws://127.0.0.1:{ws_port}/echo');
                          ws.onopen=()=>ws.send('ping');
                          ws.onmessage=e=>fin('msg:'+e.data);
                          ws.onerror=()=>fin('error');
                          ws.onclose=()=>fin('close');
                          setTimeout(()=>fin('timeout'), 4000);
                        }} catch(e) {{ fin('throw:'+e.message); }}
                    }})"""
                )
                ws_bad = js(
                    f"""async () => new Promise(resolve => {{
                        let done=false; const fin=v=>{{if(!done){{done=true;resolve(v)}}}};
                        try {{
                          const ws=new WebSocket('ws://127.0.0.1:{forbidden_port}/steal');
                          ws.onopen=()=>fin('open');
                          ws.onerror=()=>fin('error');
                          ws.onclose=()=>fin('close');
                          setTimeout(()=>fin('timeout'), 4000);
                        }} catch(e) {{ fin('throw:'+e.message); }}
                    }})"""
                )
                scenarios.append(
                    {
                        "name": "websocket",
                        "authorized_result": ws_ok,
                        "unauthorized_result": ws_bad,
                        "forbidden_hits": forbidden_rec.paths(),
                    }
                )
                checks["websocket_authorized"] = str(ws_ok) == "msg:echo:ping"
                checks["websocket_unauthorized_zero_requests"] = (
                    forbidden_rec.count() == 0 and str(ws_bad) != "open"
                )

                # ---- 8. IPv4 / IPv6
                r6 = nav(f"{v6_base}/page")
                before = v6_bad_rec.count()
                r6bad = nav(f"{v6_bad_base}/steal")
                scenarios.append(
                    {
                        "name": "ipv4_ipv6",
                        "ipv6_authorized": r6,
                        "ipv6_unauthorized": r6bad,
                        "ipv6_unauthorized_hits": v6_bad_rec.paths(),
                    }
                )
                checks["ipv6_authorized_200"] = r6["status"] == 200
                checks["ipv6_unauthorized_403"] = r6bad["status"] == 403
                checks["ipv6_unauthorized_zero_requests"] = v6_bad_rec.count() == 0

                context.close()
            finally:
                browser.close()

        # ---- 9. 私网 / 链路本地 / 未授权域名指向内网（裸 socket 决策级）
        private_cases = {
            "private_10": b"GET http://10.0.0.5/ HTTP/1.1\r\nHost: 10.0.0.5\r\n\r\n",
            "link_local_metadata": (
                b"GET http://169.254.169.254/latest/meta-data/ HTTP/1.1\r\n"
                b"Host: 169.254.169.254\r\n\r\n"
            ),
            "unauth_host_resolves_private": (
                b"GET http://evil.example/steal HTTP/1.1\r\nHost: evil.example\r\n\r\n"
            ),
        }
        private_results = {}
        for name, req in private_cases.items():
            resp = raw_proxy_request(proxy_port, req)
            private_results[name] = {"status": _status(resp), "reason": _reason(resp)}
        scenarios.append({"name": "private_network", "cases": private_results})
        checks["private_network_all_blocked"] = all(
            v["status"] == 403 for v in private_results.values()
        )

        # ---- 10. 非法协议 / 内联凭据 / CONNECT 端口与协议错误
        illegal_cases = {
            "ftp": b"GET ftp://127.0.0.1:21/ HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n",
            "file": b"GET file:///etc/passwd HTTP/1.1\r\nHost: localhost\r\n\r\n",
            "gopher": b"GET gopher://127.0.0.1:70/ HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n",
            "userinfo": (
                f"GET http://user:pass@127.0.0.1:{allowed_port}/ HTTP/1.1\r\n"
                f"Host: 127.0.0.1\r\n\r\n"
            ).encode(),
            "connect_wrong_port": (
                f"CONNECT 127.0.0.1:{allowed_port} HTTP/1.1\r\n"
                f"Host: 127.0.0.1\r\n\r\n"
            ).encode(),
            "connect_forbidden": (
                f"CONNECT 127.0.0.1:{forbidden_port} HTTP/1.1\r\n"
                f"Host: 127.0.0.1\r\n\r\n"
            ).encode(),
        }
        illegal_results = {}
        for name, req in illegal_cases.items():
            resp = raw_proxy_request(proxy_port, req)
            illegal_results[name] = {"status": _status(resp), "reason": _reason(resp)}
        scenarios.append({"name": "illegal_schemes", "cases": illegal_results})
        checks["illegal_schemes_all_blocked"] = all(
            v["status"] == 403 for v in illegal_results.values()
        )

        # ---- 11. 不泄露认证头：代理凭据不得转发到目标，且任何头值不得落盘
        probe_token = "SENSITIVE_PROXY_TOKEN"
        before_hits = allowed_rec.count()
        raw_proxy_request(
            proxy_port,
            (
                f"GET http://127.0.0.1:{allowed_port}/auth-probe HTTP/1.1\r\n"
                f"Host: 127.0.0.1:{allowed_port}\r\n"
                f"Proxy-Authorization: Bearer {probe_token}\r\n"
                "Authorization: Bearer SENSITIVE_END_TO_END_TOKEN\r\n"
                "Cookie: session=SENSITIVE_COOKIE\r\n\r\n"
            ).encode(),
        )
        upstream_hits = allowed_rec.snapshot()[before_hits:]
        upstream_header_names = (
            upstream_hits[-1]["header_names"] if upstream_hits else ["NO_HIT"]
        )
        audit = runtime.proxy.audit_json()
        audit_text = json.dumps(audit, ensure_ascii=False)
        raw_evidence["auth_header_probe"] = {
            "upstream_header_names": upstream_header_names,
            "upstream_received_proxy_authorization": "proxy-authorization"
            in upstream_header_names,
            "audit_entry_count": len(audit),
            "audit_leaked_any_token": any(
                token in audit_text
                for token in (
                    probe_token,
                    "SENSITIVE_END_TO_END_TOKEN",
                    "SENSITIVE_COOKIE",
                )
            ),
            "audit_sample": audit[:3],
        }
        checks["proxy_auth_not_forwarded"] = (
            "proxy-authorization" not in upstream_header_names
        )
        checks["audit_no_auth_headers"] = not raw_evidence["auth_header_probe"][
            "audit_leaked_any_token"
        ]

        total_unauthorized_requests = sum(r.count() for r in unauthorized.values())
        checks["all_unauthorized_servers_zero_requests"] = total_unauthorized_requests == 0

        blocked_items = [
            {
                "item": "浏览器内核 DNS / 应用绕过代理",
                "status": "BLOCKED",
                "detail": (
                    "未取得宿主级网络命名空间控制（无防火墙/iptables/网络策略）；"
                    "本代理只在浏览器被正确配置且流量经过它时有效。直连 socket、"
                    "扩展、非浏览器组件或代理配置丢失都不受约束。"
                ),
            },
            {
                "item": "CONNECT/TLS 内部内容检查",
                "status": "BLOCKED",
                "detail": (
                    "CONNECT 是字节隧道，代理不解密 TLS，看不到 HTTPS 内的路径、"
                    "Header 与重定向链；仅强制目标 host:port 白名单与 IP 校验。"
                    "内容级检查需 TLS 中间人，会破坏证书信任模型，刻意不实现。"
                ),
            },
            {
                "item": "宿主级出口封锁验证",
                "status": "BLOCKED",
                "detail": (
                    "未验证「即使绕过代理，目标也不可达」；这要求主机防火墙或容器网络策略，"
                    "超出本机用户态代理能力。"
                ),
            },
        ]

        report = {
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "proxy_url": proxy_url,
            "policy_origins": sorted(str(o) for o in policy.origins),
            "policy_pinned_dns": {
                k: sorted(v) for k, v in policy.pinned_dns.items()
            },
            "ports": {
                "proxy": proxy_port,
                "allowed": allowed_port,
                "forbidden": forbidden_port,
                "chain": chain_port,
                "ipv6_allowed": v6_port,
                "ipv6_forbidden": v6_bad_port,
                "dns_flip": dns_flip_port,
                "ws_echo": ws_port,
            },
            "scenarios": scenarios,
            "unauthorized_servers": {
                name: {"count": rec.count(), "hits": rec.snapshot()}
                for name, rec in unauthorized.items()
            },
            "authorized_servers": {
                name: {"count": rec.count(), "hits": rec.snapshot()}
                for name, rec in authorized.items()
            },
            "proxy_audit": audit,
            "raw_evidence": raw_evidence,
            "checks": checks,
            "unauthorized_request_count": total_unauthorized_requests,
            "passed": bool(checks.values()) and all(checks.values()),
            "blocked_items": blocked_items,
            "limitations": (
                "未授权请求计数基于本地记录器的实际命中，不等于「互联网上不可达」。"
                "DNS rebinding 的跨 TCP 连接窗口由连接已校验 IP 关闭；"
                "但浏览器内核 DNS 缓存、TLS 内部跳转与应用绕过代理不在本代理覆盖范围。"
            ),
        }
        return report
    finally:
        runtime.stop()
        for srv in (
            allowed_srv,
            chain_srv,
            forbidden_srv,
            v6_srv,
            v6_bad_srv,
            dns_v4_srv,
            dns_flip_srv,
        ):
            if srv is None:
                continue
            srv.shutdown()
            srv.server_close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--out",
        default="docs/assets/run009/wp3/egress-proxy-evidence.json",
    )
    args = ap.parse_args(argv)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    report = run_all_scenarios(out)
    out.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "passed": report["passed"],
                "unauthorized_request_count": report["unauthorized_request_count"],
                "checks": report["checks"],
                "evidence": str(out),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
