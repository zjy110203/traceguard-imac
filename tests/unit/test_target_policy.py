"""RUN-006 WP1-05：统一目标访问策略测试（SSRF / 绝对 URL 绕过 / 重定向 / 跨域）。

这些是"策略层"测试；真实浏览器请求级拦截见 `tests/e2e/test_target_policy_browser.py`。
"""

from __future__ import annotations

import pytest
from traceguard_agents.gateway.tool_gateway import ToolCall, ToolGateway
from traceguard_core.security.policy import SecurityPolicy
from traceguard_core.security.target_policy import (
    Origin,
    TargetAccessDenied,
    TargetPolicy,
)

BASE = "http://127.0.0.1:8011"


def _policy() -> TargetPolicy:
    return TargetPolicy.from_origins([BASE])


# ------------------------------------------------------------------ origin 解析
def test_origin_parse_defaults_ports() -> None:
    assert Origin.parse("http://127.0.0.1:8011").port == 8011
    assert Origin.parse("https://example.com").port == 443
    assert Origin.parse("example.com").scheme == "http"
    with pytest.raises(ValueError):
        Origin.parse("")


# ------------------------------------------------------------- 绝对 URL / 相对路径
def test_relative_path_resolves_against_base() -> None:
    d = _policy().resolve("/login", BASE)
    assert d.allowed is True
    assert d.url == f"{BASE}/login"


def test_protocol_relative_url_is_rejected() -> None:
    """`//evil.example.com/x` 看起来像相对路径，实际是外部主机。"""
    d = _policy().resolve("//evil.example.com/x", BASE)
    assert d.allowed is False
    assert d.reason == "PROTOCOL_RELATIVE_URL_FORBIDDEN"


def test_absolute_url_outside_allowlist_rejected() -> None:
    d = _policy().resolve("http://evil.example.com/login", BASE)
    assert d.allowed is False and d.reason == "HOST_NOT_ALLOWED"


def test_absolute_url_same_host_wrong_port_rejected() -> None:
    d = _policy().resolve("http://127.0.0.1:9999/admin", BASE)
    assert d.allowed is False and d.reason == "PORT_NOT_ALLOWED"


# ------------------------------------------------------------------------- SSRF
@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data",
        "http://10.0.0.1/",
        "http://192.168.1.1/",
        "http://172.16.0.1/",
        "http://[::1]/",
        "http://0.0.0.0/",
    ],
)
def test_private_and_link_local_blocked(url: str) -> None:
    d = _policy().check(url)
    assert d.allowed is False
    assert d.reason in {"PRIVATE_NETWORK_BLOCKED", "HOST_NOT_ALLOWED"}


def test_credentials_in_url_forbidden() -> None:
    d = _policy().check("http://user:secret@127.0.0.1:8011/login")
    assert d.allowed is False and d.reason == "URL_USERINFO_FORBIDDEN"


def test_non_http_scheme_blocked() -> None:
    assert _policy().check("file:///etc/passwd").reason == "SCHEME_NOT_ALLOWED"
    assert _policy().check("ftp://127.0.0.1:8011/").reason == "SCHEME_NOT_ALLOWED"


def test_about_blank_allowed_as_no_network() -> None:
    assert _policy().check("about:blank").allowed is True
    assert _policy().check("data:text/html,<p>x</p>").allowed is True


# --------------------------------------------------------------------- redirect
def test_redirect_chain_blocked_on_external_hop() -> None:
    chain = [f"{BASE}/start", "http://evil.example.com/steal"]
    d = _policy().check_redirect_chain(chain)
    assert d.allowed is False
    assert d.reason.startswith("REDIRECT_BLOCKED")


def test_too_many_redirects_blocked() -> None:
    chain = [f"{BASE}/r{i}" for i in range(6)]
    d = TargetPolicy.from_origins([BASE], max_redirects=2).check_redirect_chain(chain)
    assert d.allowed is False and d.reason == "TOO_MANY_REDIRECTS"


def test_landed_url_check_rejects_external_landing() -> None:
    d = _policy().check_landed_url("http://evil.example.com/")
    assert d.allowed is False
    assert d.reason == "LANDED_URL_NOT_ALLOWED:HOST_NOT_ALLOWED"


def test_raise_if_denied_raises_app_error() -> None:
    with pytest.raises(TargetAccessDenied) as exc:
        _policy().check("http://evil.example.com/").raise_if_denied()
    assert exc.value.code.value == "AUTHORIZATION_ERROR"


# ------------------------------------------------------- Explorer 侧共用同一策略
def test_gateway_uses_same_policy_for_protocol_relative() -> None:
    gw = ToolGateway(target_policy=_policy(), base_url=BASE)
    d = gw.check(ToolCall("navigate", {"url": "//evil.example.com/x"}))
    assert d.allowed is False
    assert d.reason == "UNAUTHORIZED_TARGET"
    assert d.detail == "PROTOCOL_RELATIVE_URL_FORBIDDEN"


def test_gateway_allows_relative_path_inside_base() -> None:
    gw = ToolGateway(target_policy=_policy(), base_url=BASE)
    d = gw.check(ToolCall("navigate", {"url": "/login"}))
    assert d.allowed is True


def test_gateway_blocks_cross_origin_absolute_url() -> None:
    gw = ToolGateway(target_policy=_policy(), base_url=BASE)
    d = gw.check(ToolCall("navigate", {"url": "http://127.0.0.1:9999/admin"}))
    assert d.allowed is False and d.detail == "PORT_NOT_ALLOWED"


def test_gateway_landed_url_guard() -> None:
    gw = ToolGateway(target_policy=_policy(), base_url=BASE)
    assert gw.check_landed_url(f"{BASE}/projects")["allowed"] is True
    blocked = gw.check_landed_url("http://evil.example.com/")
    assert blocked["allowed"] is False
    assert any(a.get("tool") == "landed_url" for a in gw.audit_log())


def test_security_policy_and_target_policy_agree_on_local_target() -> None:
    """两个策略层对本地 TeamFlow 的结论必须一致（不能一边严一边松）。"""
    legacy = SecurityPolicy()
    policy = _policy()
    for url in (
        f"{BASE}/login",
        "http://127.0.0.1:9999/",
        "http://example.com/",
        "http://169.254.169.254/",
    ):
        assert legacy.check_url(url)["allowed"] is policy.check(url).allowed, url
