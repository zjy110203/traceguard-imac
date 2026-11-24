"""RUN-009 WP3：本机受控出站代理（HTTP 绝对形式 + CONNECT 隧道）。

目的
----
把「请求发出前拦截」从浏览器进程外移到网络出口，使未授权目标**在 TCP 层**
就不可达。代理是两个端点之间的唯一路径：

* 明文 HTTP / ``ws://``：浏览器发绝对形式请求，代理校验后连接到
  **已校验的 IP**（不重新解析），逐跳重定向都会再次经过代理并再次校验；
* HTTPS / ``wss://``：浏览器发 ``CONNECT host:port``，代理校验 host/port、
  解析并校验 IP，然后连接到**已校验的 IP** 并转发原始 TLS 字节。

关键安全性质
------------
1. **DNS pinning 落在连接上**：先解析 → 校验全部 A/AAAA → 用校验过的 IP
   建连，连接阶段不再调用解析器。即使 DNS 在校验与 connect 之间变化
   （rebinding TOCTOU），连接仍然指向被校验过的地址。
2. **未授权私网拒绝**：私网 / 回环 / 链路本地 / 组播 / 保留地址只允许在
   调用方把它们**显式列为授权 IP 字面量 origin** 时通过；授权域名若解析到
   私网地址一律拒绝。
3. **逐跳重定向**：明文 HTTP 的每一跳都是一条新的代理请求，因此每跳都会
   重新执行 `TargetPolicy.check`。CONNECT 内的跳转位于 TLS 里，代理看不到
   （见局限）。
4. **不泄露认证头**：代理自身使用的 ``Proxy-*`` 逐跳头在转发前被剥离；
   审计只记录方法 / host / port / **不含 query 的 path** / 解析结果 / 判定，
   绝不记录任何请求头的值（包括 ``Authorization`` / ``Cookie``）。

已披露局限（不得据此宣称完全安全）
------------------------------------
* CONNECT 是字节隧道，不解密 TLS，因此无法检查 TLS 内部的 HTTP 方法、
  路径、Header、重定向链；HTTPS 页面内的跳转只受「目标 host:port 白名单」
  约束。要做内容级检查必须引入 TLS 中间人（会破坏证书信任模型），本实现
  刻意不做。
* 浏览器自身的 DNS 缓存 / Happy Eyeballs 在「配置了代理」时由代理承担解析；
  但若应用绕过代理（直连 socket、扩展、非浏览器组件）则仍不受本代理约束。
* ``TargetPolicy`` 的 DNS pinning 是进程内快照；代理重启后需重新 pin。

本模块只依赖标准库与既有 `TargetPolicy`，不修改任何浏览器代码。
"""

from __future__ import annotations

import asyncio
import ipaddress
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from traceguard_core.security.target_policy import TargetPolicy

#: 代理自己的逐跳头：绝不转发给目标（防止代理凭据泄露到目标服务器）。
#: 注意刻意 **不** 剥离 Connection / Upgrade，否则 ``ws://`` 升级会失败。
_PROXY_HOP_HEADERS = frozenset(
    {
        "proxy-authorization",
        "proxy-authenticate",
        "proxy-connection",
        "proxy-host",
        "proxy-agent",
    }
)

_WS_SCHEME_MAP = {"ws": "http", "wss": "https"}
_ALLOWED_SCHEMES = {"http", "https", "ws", "wss"}
_DEFAULT_PORT = {"http": 80, "ws": 80, "https": 443, "wss": 443}

_READ_TIMEOUT_S = 15.0
_MAX_HEADER_LINE = 32_768
_MAX_HEADER_TOTAL = 262_144


def _is_internal(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """与 TargetPolicy._is_internal_ip 同口径的内部地址判定（本地副本，避免私有 API 依赖）。"""
    return bool(
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_unspecified
        or ip.is_reserved
    )


def _format_host(host: str) -> str:
    """把 IPv6 字面量包成 URL 里的 ``[::1]``，否则重建出的 URL 无法被解析。"""
    if ":" not in host:
        return host
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return host
    return f"[{host}]"


@dataclass(frozen=True)
class Decision:
    """一次目标评估的结果；只含可安全落盘/上墙的字段。"""

    allowed: bool
    reason: str
    scheme: str
    host: str
    port: int
    path: str
    resolved_ips: tuple[str, ...] = ()
    chosen_ip: str = ""

    @property
    def verdict(self) -> str:
        return "ALLOW" if self.allowed else "BLOCK"


@dataclass
class AuditEntry:
    ts: str
    channel: str
    method: str
    scheme: str
    host: str
    port: int
    path: str
    resolved_ips: list[str]
    chosen_ip: str
    decision: str
    reason: str
    connected: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "ts": self.ts,
            "channel": self.channel,
            "method": self.method,
            "scheme": self.scheme,
            "host": self.host,
            "port": self.port,
            "path": self.path,  # 仅 path，刻意不含 query（避免把 token 写进审计）
            "resolved_ips": list(self.resolved_ips),
            "chosen_ip": self.chosen_ip,
            "decision": self.decision,
            "reason": self.reason,
            "connected": self.connected,
        }


@dataclass
class EgressProxy:
    """受控出站代理。用 ``asyncio.run`` 或直接 ``await proxy.serve()`` 运行。"""

    policy: TargetPolicy
    host: str = "127.0.0.1"
    port: int = 0  # 0 = 由 OS 分配临时端口，避开固定端口冲突
    audit: list[AuditEntry] = field(default_factory=list, repr=False)
    _server: asyncio.AbstractServer | None = field(default=None, repr=False)

    # ---------------------------------------------------------------- 生命周期
    async def start(self) -> int:
        """启动监听，返回实际端口。"""
        self._server = await asyncio.start_server(self._handle, self.host, self.port)
        socks = self._server.sockets or []
        if not socks:  # pragma: no cover - start_server 成功必有 socket
            raise RuntimeError("PROXY_NO_SOCKET")
        return int(socks[0].getsockname()[1])

    async def stop(self) -> None:
        if self._server is None:
            return
        self._server.close()
        await self._server.wait_closed()
        self._server = None

    async def serve(self) -> None:
        """便捷入口：启动后永久服务（用于子进程模式）。

        RUN-010 WP2 真实缺陷修复：本方法原先无条件再调一次 start()。当调用方
        已经手动 start()（EgressProxyController 场景）时，会再绑一个新临时端口
        并覆盖 _server —— 先前的监听 socket 泄漏，浏览器拿到的是第二个端口，
        而审计/存活检查仍指向第一个。现在幂等：已启动则直接服务现有 server。
        """
        if self._server is None:
            await self.start()
        if self._server is None:  # pragma: no cover
            return
        async with self._server:
            await self._server.serve_forever()

    @property
    def url(self) -> str:
        if self._server is None:
            raise RuntimeError("PROXY_NOT_STARTED")
        socks = self._server.sockets or []
        port = int(socks[0].getsockname()[1])
        return f"http://{self.host}:{port}"

    def audit_json(self) -> list[dict[str, Any]]:
        return [e.to_dict() for e in self.audit]

    # ------------------------------------------------------------------ 评估
    def _authorized_ip_literals(self) -> frozenset[str]:
        """仅把显式写成 IP 字面量的授权 origin 视为「允许私网」。"""
        out: set[str] = set()
        for host in self.policy.hosts:
            try:
                ipaddress.ip_address(host)
            except ValueError:
                continue
            out.add(host)
        return frozenset(out)

    def _evaluate(
        self,
        *,
        channel: str,
        method: str,
        raw_scheme: str,
        host: str,
        port: int,
        path: str,
    ) -> Decision:
        """统一目标评估：URL 白名单 → DNS → IP 合法性 → 选连接 IP。"""
        scheme = _WS_SCHEME_MAP.get(raw_scheme, raw_scheme)

        if scheme not in {"http", "https"}:
            return Decision(False, "SCHEME_NOT_ALLOWED", scheme, host, port, path)
        if not host or "@" in host or "/" in host:
            return Decision(False, "MALFORMED_HOST", scheme, host, port, path)
        if not (1 <= port <= 65535):
            return Decision(False, "MALFORMED_PORT", scheme, host, port, path)

        # 第一层：既有 TargetPolicy（origin 白名单 / userinfo / 协议 / rebinding）
        url = f"{scheme}://{_format_host(host)}:{port}{path or '/'}"
        check = self.policy.check(url, resolve_dns=True)
        if not check.allowed:
            return Decision(False, check.reason, scheme, host, port, path)

        # 第二层：解析结果中的每一个 IP 都必须合法（混合应答里有一个私网也拒绝）
        ips = sorted(self.policy.resolve_ips(host))
        if not ips:
            return Decision(False, "DNS_RESOLUTION_FAILED", scheme, host, port, path)
        allowed_literals = self._authorized_ip_literals()
        pinned = self.policy.pinned_dns.get(host, frozenset())
        for ip_text in ips:
            try:
                ip = ipaddress.ip_address(ip_text)
            except ValueError:
                return Decision(False, "INVALID_DNS_ANSWER", scheme, host, port, path, tuple(ips))
            if not _is_internal(ip):
                continue
            # 私网地址只允许两种显式授权：IP 字面量本身在白名单，或它是授权主机
            # 在策略构造时钉住的精确答案（此时 DNS 一旦变化会先被 rebinding 检查拒绝）。
            if ip_text in allowed_literals or ip_text in pinned:
                continue
            return Decision(
                False,
                "DNS_RESOLVES_TO_PRIVATE_NETWORK",
                scheme,
                host,
                port,
                path,
                tuple(ips),
            )

        # 连接阶段使用这里选出的 IP，绝不重新解析 → 关闭 rebinding TOCTOU。
        return Decision(True, "ALLOWED", scheme, host, port, path, tuple(ips), ips[0])

    def _record(
        self,
        decision: Decision,
        *,
        channel: str,
        method: str,
        connected: bool,
    ) -> None:
        self.audit.append(
            AuditEntry(
                ts=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                channel=channel,
                method=method,
                scheme=decision.scheme,
                host=decision.host,
                port=decision.port,
                path=decision.path,
                resolved_ips=list(decision.resolved_ips),
                chosen_ip=decision.chosen_ip,
                decision=decision.verdict,
                reason=decision.reason,
                connected=connected,
            )
        )

    # ------------------------------------------------------------- HTTP 解析
    @staticmethod
    async def _read_headers(reader: asyncio.StreamReader) -> dict[str, str]:
        headers: dict[str, str] = {}
        total = 0
        while True:
            line = await asyncio.wait_for(reader.readline(), timeout=_READ_TIMEOUT_S)
            if line in (b"\r\n", b"\n", b""):
                break
            total += len(line)
            if len(line) > _MAX_HEADER_LINE or total > _MAX_HEADER_TOTAL:
                raise ValueError("HEADERS_TOO_LARGE")
            name, sep, value = line.decode("latin-1").partition(":")
            if not sep:
                raise ValueError("MALFORMED_HEADER")
            headers[name.strip().lower()] = value.strip()
        return headers

    @staticmethod
    async def _read_body(reader: asyncio.StreamReader, headers: dict[str, str]) -> bytes:
        try:
            length = int(headers.get("content-length", "0") or "0")
        except ValueError:
            raise ValueError("MALFORMED_CONTENT_LENGTH") from None
        if length <= 0:
            return b""
        if length > 8 * 1024 * 1024:
            raise ValueError("BODY_TOO_LARGE")
        return await asyncio.wait_for(reader.readexactly(length), timeout=_READ_TIMEOUT_S)

    # ------------------------------------------------------------------ 连接处理
    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        method = ""
        channel = "unknown"
        try:
            raw_line = await asyncio.wait_for(reader.readline(), timeout=_READ_TIMEOUT_S)
            if not raw_line:
                return
            parts = raw_line.decode("latin-1").rstrip("\r\n").split()
            if len(parts) < 3:
                await self._error_response(writer, 400, "MALFORMED_REQUEST")
                return
            method, target, version = parts[0], parts[1], parts[2]
            headers = await self._read_headers(reader)
            body = await self._read_body(reader, headers)
            if method.upper() == "CONNECT":
                channel = "connect"
                await self._handle_connect(reader, writer, target, version, method, channel)
            else:
                channel = "http"
                await self._handle_http(
                    reader, writer, method, target, version, headers, body, channel
                )
        except (ValueError, TimeoutError, OSError, UnicodeDecodeError) as exc:
            # 解析/读取失败：只回通用错误，不回显任何头或目标内容
            try:
                await self._error_response(writer, 400, str(exc)[:80])
            except OSError:
                pass
        except Exception:  # noqa: BLE001 - 代理不可因单个请求异常退出
            try:
                await self._error_response(writer, 502, "PROXY_INTERNAL_ERROR")
            except OSError:
                pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except (OSError, ConnectionError):
                pass

    async def _handle_http(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        method: str,
        target: str,
        version: str,
        headers: dict[str, str],
        body: bytes,
        channel: str,
    ) -> None:
        try:
            parsed = urlsplit(target)
        except ValueError:
            await self._error_response(writer, 400, "MALFORMED_URL")
            return
        raw_scheme = (parsed.scheme or "").lower()
        # 代理请求必须是绝对形式；origin-form 说明客户端配置有误，拒绝而不是猜测 host。
        if raw_scheme not in _ALLOWED_SCHEMES:
            decision = Decision(False, "SCHEME_NOT_ALLOWED", raw_scheme, "", 0, "")
            self._record(decision, channel=channel, method=method, connected=False)
            await self._error_response(writer, 403, "SCHEME_NOT_ALLOWED")
            return
        if parsed.username or parsed.password:
            decision = Decision(False, "URL_USERINFO_FORBIDDEN", raw_scheme, "", 0, "")
            self._record(decision, channel=channel, method=method, connected=False)
            await self._error_response(writer, 403, "URL_USERINFO_FORBIDDEN")
            return
        host = (parsed.hostname or "").lower()
        try:
            port = int(parsed.port or _DEFAULT_PORT[raw_scheme])
        except ValueError:
            await self._error_response(writer, 400, "MALFORMED_PORT")
            return
        path = parsed.path or "/"

        decision = self._evaluate(
            channel=channel,
            method=method,
            raw_scheme=raw_scheme,
            host=host,
            port=port,
            path=path,
        )
        if not decision.allowed:
            self._record(decision, channel=channel, method=method, connected=False)
            await self._error_response(writer, 403, decision.reason)
            return

        is_ws_upgrade = (
            headers.get("upgrade", "").lower() == "websocket"
            and "upgrade" in headers.get("connection", "").lower()
        )

        origin_form = path + (f"?{parsed.query}" if parsed.query else "")
        forwarded = [f"{method} {origin_form} {version}\r\n"]
        # 剥离代理自己的逐跳头；Connection/Keep-Alive 由下方按场景重写。
        for name, value in headers.items():
            if name in _PROXY_HOP_HEADERS or name in {"host", "connection", "keep-alive"}:
                continue
            forwarded.append(f"{name}: {value}\r\n")
        forwarded.append(f"Host: {parsed.netloc}\r\n")
        # 关键修复（RUN-009 WP3 实测发现）：明文 HTTP 必须让 upstream 关闭连接，
        # 且不再把客户端后续字节盲目转发。否则同一条 keep-alive 连接上的后续
        # 请求会绕过逐请求解析/校验——这是真实可绕过缺口，不能只修 URL 检查。
        forwarded.append("Connection: Upgrade\r\n" if is_ws_upgrade else "Connection: close\r\n")
        head = "".join(forwarded).encode("latin-1") + b"\r\n"

        upstream_reader, upstream_writer = await asyncio.open_connection(decision.chosen_ip, port)
        self._record(decision, channel=channel, method=method, connected=True)
        try:
            upstream_writer.write(head + body)
            await upstream_writer.drain()
            # 只有 WebSocket 升级后才需要双向原始转发；普通 HTTP 单问单答。
            await self._relay(
                reader,
                writer,
                upstream_reader,
                upstream_writer,
                forward_client=is_ws_upgrade,
            )
        finally:
            try:
                upstream_writer.close()
                await upstream_writer.wait_closed()
            except (OSError, ConnectionError):
                pass

    async def _handle_connect(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        target: str,
        version: str,
        method: str,
        channel: str,
    ) -> None:
        # CONNECT 的 authority 形如 host:port，IPv6 为 [::1]:port。
        authority = target.strip()
        if authority.count(":") > 1 and not (authority.startswith("[") and "]" in authority):
            decision = Decision(False, "MALFORMED_HOST", "https", "", 0, "")
            self._record(decision, channel=channel, method=method, connected=False)
            await self._error_response(writer, 403, "MALFORMED_HOST")
            return
        if authority.startswith("["):
            end = authority.find("]")
            if end < 0 or ":" not in authority[end + 1 :]:
                decision = Decision(False, "MALFORMED_HOST", "https", "", 0, "")
                self._record(decision, channel=channel, method=method, connected=False)
                await self._error_response(writer, 403, "MALFORMED_HOST")
                return
            host = authority[1:end].lower()
            port_text = authority[end + 2 :]
        else:
            host, sep, port_text = authority.rpartition(":")
            if not sep:
                decision = Decision(False, "MALFORMED_HOST", "https", "", 0, "")
                self._record(decision, channel=channel, method=method, connected=False)
                await self._error_response(writer, 403, "MALFORMED_HOST")
                return
            host = host.lower()
        try:
            port = int(port_text)
        except ValueError:
            await self._error_response(writer, 400, "MALFORMED_PORT")
            return

        # CONNECT 同时覆盖 HTTPS 与 WSS；两者在代理层都是「host:port 白名单 + TCP 隧道」。
        decision = self._evaluate(
            channel=channel,
            method=method,
            raw_scheme="https",
            host=host,
            port=port,
            path="",
        )
        if not decision.allowed:
            self._record(decision, channel=channel, method=method, connected=False)
            await self._error_response(writer, 403, decision.reason)
            return

        upstream_reader, upstream_writer = await asyncio.open_connection(decision.chosen_ip, port)
        writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        await writer.drain()
        self._record(decision, channel=channel, method=method, connected=True)
        try:
            await self._relay(reader, writer, upstream_reader, upstream_writer)
        finally:
            try:
                upstream_writer.close()
                await upstream_writer.wait_closed()
            except (OSError, ConnectionError):
                pass

    @staticmethod
    async def _relay(
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
        upstream_reader: asyncio.StreamReader,
        upstream_writer: asyncio.StreamWriter,
        *,
        forward_client: bool = True,
    ) -> None:
        async def pipe(src: asyncio.StreamReader, dst: asyncio.StreamWriter) -> None:
            try:
                while True:
                    data = await src.read(65_536)
                    if not data:
                        break
                    dst.write(data)
                    await dst.drain()
            except (OSError, ConnectionError, asyncio.IncompleteReadError):
                pass
            finally:
                try:
                    dst.close()
                except (OSError, ConnectionError):
                    pass

        tasks = [pipe(upstream_reader, client_writer)]
        if forward_client:
            tasks.append(pipe(client_reader, upstream_writer))
        await asyncio.gather(*tasks, return_exceptions=True)

    @staticmethod
    async def _error_response(writer: asyncio.StreamWriter, status: int, reason: str) -> None:
        """最小错误响应：不回显 URL、Header 或任何目标内容。"""
        body = f"{status} {reason}".encode("ascii", errors="replace")
        writer.write(
            (
                f"HTTP/1.1 {status} Blocked\r\n"
                "Content-Type: text/plain; charset=utf-8\r\n"
                # RUN-011 WP7：明确标记"这是代理阻断，不是目标应用返回的 403"。
                # 浏览器执行层据此把"导航看似成功"纠正为步骤失败，避免
                # 未授权目标被阻断却记录成 navigate PASSED。
                "X-TraceGuard-Egress: BLOCK\r\n"
                f"Content-Length: {len(body)}\r\n"
                "Connection: close\r\n\r\n"
            ).encode("ascii")
            + body
        )
        await writer.drain()


__all__ = ["AuditEntry", "Decision", "EgressProxy"]
