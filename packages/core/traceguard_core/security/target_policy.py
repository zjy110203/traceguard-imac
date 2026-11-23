"""统一目标访问策略（RUN-006 WP1-05 / RUN-007 WP1-05）。

同一份策略必须同时约束 Explorer 的 ToolGateway 与正式 Playwright Runner：
- 只允许访问显式授权的 origin（scheme + host + port）；
- 相对路径按 base_url 解析；协议相对（`//host`）与绝对 URL 一律重新校验，不能绕过白名单；
- 导航后、点击跳转后、重定向链每一跳都复核最终 URL；
- 未授权的内网 / 回环 / 链路本地地址默认阻断（已授权 origin 除外）；
- 浏览器层通过 route 拦截在请求发出前阻断，而不是依赖提示词或人工检查。

RUN-007 补充（DNS）：
- 授权主机的 DNS 解析结果在策略构造时"钉住"（pin），后续解析结果与之无交集即判定
  为 DNS Rebinding 并阻断；
- 未授权主机若解析到内网/回环地址，同样显式拒绝（不只看 URL 字面量）。

设计说明（安全边界）：
- 这是网络访问控制，不是身份认证。单机开发环境下的调用者身份仍然不可强认证，
  见 `docs/RUN-007-SECURITY-RELIABILITY.md` 的局限说明；
- 无法在用户态接管系统 DNS，因此"钉住 + 每次复核"只能发现并拒绝**已发生变化的**解析结果，
  不能阻止浏览器内核自身缓存导致的短时不一致；生产部署应改用受控出站代理（见文档）。
"""

from __future__ import annotations

import ipaddress
import socket
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

from traceguard_core.errors import AppError, ErrorCode

#: 无网络副作用的 scheme：允许（不触发任何出站请求）
SAFE_SCHEMES = {"about", "data", "blob"}

#: DNS 解析结果缓存时长（秒）：避免每个请求都做一次 getaddrinfo
DNS_CACHE_TTL_S = 5.0


class TargetAccessDenied(AppError):
    """目标地址被策略拒绝。继承 AppError，便于 API 层统一映射为 403。"""

    def __init__(self, reason: str, url: str = "") -> None:
        super().__init__(ErrorCode.AUTHORIZATION_ERROR, f"TARGET_NOT_ALLOWED: {reason}")
        self.reason = reason
        self.url = url


def default_resolver(host: str) -> list[str]:
    """解析主机的全部 A/AAAA 记录（IPv4 + IPv6）。

    用 `getaddrinfo` 而不是 `gethostbyname`：后者只返回一个 IPv4，
    会把 AAAA 与多记录场景漏掉（正是 DNS Rebinding 常见的绕过方式）。
    """
    infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    out: list[str] = []
    for info in infos:
        addr = info[4][0]
        if addr not in out:
            out.append(addr)
    return out


def _is_internal_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return bool(
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_unspecified
        or ip.is_reserved
    )


def _looks_like_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


@dataclass(frozen=True, order=True)
class Origin:
    scheme: str
    host: str
    port: int

    def __str__(self) -> str:  # pragma: no cover - 便于日志
        return f"{self.scheme}://{self.host}:{self.port}"

    @classmethod
    def parse(cls, value: str) -> Origin:
        raw = (value or "").strip()
        if not raw:
            raise ValueError("EMPTY_ORIGIN")
        if "://" not in raw:
            raw = f"http://{raw}"
        parsed = urlparse(raw)
        scheme = (parsed.scheme or "http").lower()
        host = (parsed.hostname or "").lower()
        if not host:
            raise ValueError(f"INVALID_ORIGIN: {value}")
        default_port = 443 if scheme == "https" else 80
        port = parsed.port or default_port
        return cls(scheme=scheme, host=host, port=int(port))


@dataclass(frozen=True)
class TargetCheck:
    allowed: bool
    reason: str
    url: str = ""
    origin: Origin | None = None

    def raise_if_denied(self) -> None:
        if not self.allowed:
            raise TargetAccessDenied(self.reason, self.url)


@dataclass
class TargetPolicy:
    """目标域名 / 端口 / 重定向白名单策略。"""

    origins: frozenset[Origin] = field(default_factory=frozenset)
    max_redirects: int = 3
    #: 明确授权的 origin 中若出现回环/内网地址是允许的（本项目即本地 TeamFlow）
    allow_authorized_private: bool = True
    #: 可注入的 DNS 解析器（测试用；默认 getaddrinfo）
    resolver: Callable[[str], list[str]] | None = None
    #: 策略构造时钉住的 host -> 解析结果；空表示未钉住
    pinned_dns: dict[str, frozenset[str]] = field(default_factory=dict)
    #: host -> (解析时刻, 结果)，短期缓存避免每请求一次 DNS
    _dns_cache: dict[str, tuple[float, frozenset[str]]] = field(default_factory=dict, repr=False)

    # ------------------------------------------------------------------ 构造
    @classmethod
    def from_origins(
        cls,
        origins: Iterable[str],
        *,
        max_redirects: int = 3,
        resolver: Callable[[str], list[str]] | None = None,
        pin_dns: bool = False,
        allow_authorized_private: bool = True,
    ) -> TargetPolicy:
        parsed: set[Origin] = set()
        for item in origins:
            if not item:
                continue
            parsed.add(Origin.parse(str(item)))
        policy = cls(
            origins=frozenset(parsed),
            max_redirects=max_redirects,
            resolver=resolver,
            allow_authorized_private=allow_authorized_private,
        )
        if pin_dns:
            policy.pin_dns()
        return policy

    @classmethod
    def from_base_url(
        cls, base_url: str, *, extra: Iterable[str] = (), **kwargs: object
    ) -> TargetPolicy:
        return cls.from_origins([base_url, *[e for e in extra if e]], **kwargs)  # type: ignore[arg-type]

    @property
    def hosts(self) -> set[str]:
        return {o.host for o in self.origins}

    @property
    def ports(self) -> set[int]:
        return {o.port for o in self.origins}

    @property
    def base_url(self) -> str:
        """首个授权的 origin（用于相对路径解析）。"""
        if not self.origins:
            return ""
        origin = sorted(self.origins)[0]
        return f"{origin.scheme}://{origin.host}:{origin.port}"

    # ------------------------------------------------------------------ DNS
    def resolve_ips(self, host: str) -> frozenset[str]:
        """返回主机的全部解析地址（带 5s 缓存）。解析失败返回空集合。"""
        key = (host or "").lower()
        now = time.monotonic()
        cached = self._dns_cache.get(key)
        if cached and now - cached[0] < DNS_CACHE_TTL_S:
            return cached[1]
        try:
            if key and _looks_like_ip(key):
                ips = frozenset({key})
            else:
                ips = frozenset(self.resolver(key) if self.resolver else default_resolver(key))
        except Exception:  # noqa: BLE001 - 解析失败不阻断（由 URL 层面的白名单兜底）
            ips = frozenset()
        self._dns_cache[key] = (now, ips)
        return ips

    def pin_dns(self) -> dict[str, frozenset[str]]:
        """钉住所有授权 origin 的当前解析结果，用于后续 rebinding 检测。"""
        pinned: dict[str, frozenset[str]] = {}
        for origin in self.origins:
            ips = self.resolve_ips(origin.host)
            if ips:
                pinned[origin.host] = ips
        self.pinned_dns = pinned
        return pinned

    # ------------------------------------------------------------------ 校验
    def check(self, url: str, *, resolve_dns: bool = False) -> TargetCheck:
        raw = (url or "").strip()
        if not raw:
            return TargetCheck(False, "EMPTY_URL", raw)
        try:
            parsed = urlparse(raw)
        except Exception:  # noqa: BLE001
            return TargetCheck(False, "MALFORMED_URL", raw)

        scheme = (parsed.scheme or "").lower()
        if scheme in SAFE_SCHEMES:
            return TargetCheck(True, "NO_NETWORK_SCHEME", raw)

        if scheme not in {"http", "https"}:
            return TargetCheck(False, "SCHEME_NOT_ALLOWED", raw)

        # 凭证内联（http://user:pass@host/）一律拒绝：凭据不得出现在 URL 里
        if parsed.username or parsed.password:
            return TargetCheck(False, "URL_USERINFO_FORBIDDEN", raw)

        host = (parsed.hostname or "").lower()
        if not host:
            return TargetCheck(False, "MALFORMED_URL", raw)

        try:
            port = int(parsed.port or (443 if scheme == "https" else 80))
        except ValueError:
            return TargetCheck(False, "MALFORMED_PORT", raw)

        origin = Origin(scheme=scheme, host=host, port=port)
        if origin in self.origins:
            if resolve_dns:
                rebinding = self._rebinding_check(host)
                if rebinding:
                    return TargetCheck(False, rebinding, raw, origin)
            return TargetCheck(True, "ALLOWED", raw, origin)

        # 未授权的内网/回环/链路本地字面量：显式区分原因，便于审计
        try:
            ip = ipaddress.ip_address(host)
            if _is_internal_ip(ip) and not (self.allow_authorized_private and host in self.hosts):
                return TargetCheck(False, "PRIVATE_NETWORK_BLOCKED", raw, origin)
        except ValueError:
            if host in {"localhost", "localhost.localdomain"} and host not in self.hosts:
                return TargetCheck(False, "LOCALHOST_NOT_AUTHORIZED", raw, origin)

        if host not in self.hosts:
            if resolve_dns:
                for addr in self.resolve_ips(host):
                    try:
                        if _is_internal_ip(ipaddress.ip_address(addr)):
                            # 未授权域名解析到内网：典型的 DNS 指向内网绕过
                            return TargetCheck(
                                False, "DNS_RESOLVES_TO_PRIVATE_NETWORK", raw, origin
                            )
                    except ValueError:
                        continue
            return TargetCheck(False, "HOST_NOT_ALLOWED", raw, origin)
        return TargetCheck(False, "PORT_NOT_ALLOWED", raw, origin)

    def _rebinding_check(self, host: str) -> str:
        """授权主机的解析结果与钉住结果无交集 → 判定为 DNS Rebinding。"""
        pinned = self.pinned_dns.get(host)
        if not pinned:
            return ""
        current = self.resolve_ips(host)
        if not current:
            return ""  # 解析不到就交给网络层自己失败，不假装是 rebinding
        if current & pinned:
            return ""
        return "DNS_REBINDING_DETECTED"

    def resolve(self, value: str, base_url: str = "") -> TargetCheck:
        """把相对路径 / 绝对 URL 解析成绝对 URL 并校验。

        `//evil.example.com/path` 这类协议相对写法会被 urljoin 解析成外部地址，
        因此这里先显式拒绝，避免"看起来是相对路径"的绕过。
        """
        raw = (value or "").strip()
        if not raw:
            raw = "/"
        if raw.startswith("//"):
            return TargetCheck(False, "PROTOCOL_RELATIVE_URL_FORBIDDEN", raw)
        base = base_url or self.base_url
        absolute = raw if "://" in raw else urljoin(f"{base.rstrip('/')}/", raw)
        result = self.check(absolute)
        return TargetCheck(result.allowed, result.reason, absolute, result.origin)

    def check_redirect_chain(
        self, chain: Iterable[str], *, resolve_dns: bool = False
    ) -> TargetCheck:
        urls = [u for u in chain if u]
        if len(urls) > self.max_redirects + 1:
            return TargetCheck(False, "TOO_MANY_REDIRECTS", urls[-1] if urls else "")
        for url in urls:
            result = self.check(url, resolve_dns=resolve_dns)
            if not result.allowed:
                return TargetCheck(False, f"REDIRECT_BLOCKED:{result.reason}", url, result.origin)
        return TargetCheck(True, "ALLOWED", urls[-1] if urls else "")

    def check_landed_url(self, url: str, *, resolve_dns: bool = True) -> TargetCheck:
        """导航/点击后的落地地址复核（跳转后仍须在授权范围、且 DNS 未被改写）。"""
        result = self.check(url, resolve_dns=resolve_dns)
        if not result.allowed:
            return TargetCheck(False, f"LANDED_URL_NOT_ALLOWED:{result.reason}", url, result.origin)
        return result


__all__ = [
    "Origin",
    "TargetAccessDenied",
    "TargetCheck",
    "TargetPolicy",
    "default_resolver",
]
