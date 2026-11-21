"""安全策略网关（阶段12）：SSRF / DNS Rebinding / Redirect / 凭证 / 出站 / 资源配额。

本地可确定性验证；外部 Web 应用访问需明确授权（未授权保持 BLOCKED）。
SLO/并发/资源数字不预先编造，配额以配置形式给出，实测后调整。
"""

from __future__ import annotations

import ipaddress
import re
import socket
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from traceguard_core.logging import redact


@dataclass
class SecurityPolicy:
    allowed_hosts: set[str] = field(default_factory=lambda: {"127.0.0.1", "localhost"})
    allowed_ports: set[int] = field(default_factory=lambda: {8011})
    max_redirects: int = 3
    blocked_private_networks: bool = True
    credential_patterns: list[str] = field(
        default_factory=lambda: [
            r"password\s*[=:]",
            r"api[_-]?key\s*[=:]",
            r"token\s*[=:]",
            r"authorization\s*[=:]",
            r"secret\s*[=:]",
        ]
    )

    def check_url(self, url: str, *, resolve_dns: bool = False) -> dict[str, Any]:
        """SSRF + DNS Rebinding 防护：校验域名与解析 IP。"""
        try:
            parsed = urlparse(url)
            host = parsed.hostname or ""
            port = parsed.port or (80 if parsed.scheme == "http" else 443)
        except Exception:  # noqa: BLE001
            return {"allowed": False, "reason": "MALFORMED_URL"}

        if host not in self.allowed_hosts:
            return {"allowed": False, "reason": "HOST_NOT_ALLOWED"}

        # TeamFlow 本地端口约束
        if host in ("127.0.0.1", "localhost") and port not in self.allowed_ports:
            return {"allowed": False, "reason": "PORT_NOT_ALLOWED"}

        # DNS Rebinding：可选解析校验（解析结果必须仍在允许集）
        if resolve_dns and host not in ("127.0.0.1", "localhost"):
            try:
                resolved = socket.gethostbyname(host)
                ip = ipaddress.ip_address(resolved)
                if ip.is_private or ip.is_loopback or ip.is_link_local:
                    return {"allowed": False, "reason": "DNS_REBINDING_TO_PRIVATE"}
            except Exception:  # noqa: BLE001
                return {"allowed": False, "reason": "DNS_RESOLUTION_FAILED"}

        # 内网地址字面量阻断
        try:
            ip = ipaddress.ip_address(host)
            if (
                self.blocked_private_networks
                and (ip.is_private or ip.is_link_local)
                and host not in self.allowed_hosts
            ):
                return {"allowed": False, "reason": "PRIVATE_IP_BLOCKED"}
        except ValueError:
            pass

        return {"allowed": True, "reason": "ALLOWED"}

    def check_redirect(self, chain: list[str]) -> dict[str, Any]:
        """Redirect Security：每次重定向重新校验目标。"""
        if len(chain) > self.max_redirects + 1:
            return {"allowed": False, "reason": "TOO_MANY_REDIRECTS"}
        for url in chain:
            result = self.check_url(url)
            if not result["allowed"]:
                return {"allowed": False, "reason": f"REDIRECT_BLOCKED:{result['reason']}"}
        return {"allowed": True, "reason": "ALLOWED"}

    def scan_credentials(self, text: str) -> dict[str, Any]:
        """凭证保护：检测文本中疑似凭据，输出脱敏后文本。"""
        found = [p for p in self.credential_patterns if re.search(p, text, re.I)]
        return {"has_credential": bool(found), "patterns": found, "redacted": redact(text)}

    def check_external_authorization(self, host: str, authorized_hosts: set[str]) -> dict[str, Any]:
        """外部应用泛化门禁：未明确授权 → BLOCKED。"""
        if host in authorized_hosts:
            return {"allowed": True, "reason": "EXTERNAL_AUTHORIZED"}
        return {"allowed": False, "reason": "EXTERNAL_NOT_AUTHORIZED"}


@dataclass
class ResourceQuota:
    """资源配额（默认值，需实测校准；不声称是最终 SLO）。"""

    max_concurrent_tasks: int = 4
    max_browsers: int = 2
    max_event_write_rate: int = 100  # events/sec
    max_evidence_bytes: int = 10 * 1024 * 1024
    note: str = "默认配额，需按实测调整，非最终SLO"
