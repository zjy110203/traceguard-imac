"""RUN-007 WP1-05：DNS 层面的目标策略（rebinding / 私网解析）。

真实浏览器验收在 `tests/e2e/test_target_policy_browser.py`（两个本地服务 + Chromium），
本文件用可注入 resolver 验证"解析结果变化"这一类只能在策略层确定性验证的规则。
"""

from __future__ import annotations

import pytest
from traceguard_core.security.target_policy import TargetPolicy


def _policy(answ: dict[str, list[str]], origins: list[str], *, pin: bool = False) -> TargetPolicy:
    return TargetPolicy.from_origins(origins, resolver=lambda h: answ.get(h, []), pin_dns=pin)


def test_authorized_host_with_rebinding_is_blocked() -> None:
    answers: dict[str, list[str]] = {"teamflow.example": ["203.0.113.10"]}
    policy = _policy(answers, ["http://teamflow.example:8080"], pin=True)
    assert policy.pinned_dns["teamflow.example"] == frozenset({"203.0.113.10"})

    # 正常解析：允许
    assert policy.check("http://teamflow.example:8080/projects", resolve_dns=True).allowed

    # 攻击者把 DNS 改到内网地址 → 必须判定 rebinding
    answers["teamflow.example"] = ["10.0.0.5"]
    policy._dns_cache.clear()  # 跳过 5s 缓存，模拟"缓存过期后的下一次解析"
    result = policy.check("http://teamflow.example:8080/projects", resolve_dns=True)
    assert result.allowed is False
    assert result.reason == "DNS_REBINDING_DETECTED"


def test_rebinding_with_partial_overlap_is_still_allowed() -> None:
    """多记录场景：只要仍有一条钉住过的地址，就还指向同一服务，不误判。"""
    answers: dict[str, list[str]] = {"tf.example": ["203.0.113.10"]}
    policy = _policy(answers, ["http://tf.example:8080"], pin=True)
    answers["tf.example"] = ["203.0.113.10", "10.0.0.5"]
    policy._dns_cache.clear()
    assert policy.check("http://tf.example:8080/", resolve_dns=True).allowed


def test_unauthorized_host_resolving_to_private_network_is_blocked() -> None:
    answers = {"evil.example": ["192.168.1.10"]}
    policy = _policy(answers, ["http://teamflow.local:8080"])
    result = policy.check("http://evil.example/", resolve_dns=True)
    assert result.allowed is False
    assert result.reason == "DNS_RESOLVES_TO_PRIVATE_NETWORK"


def test_unauthorized_public_host_is_blocked_by_whitelist() -> None:
    answers = {"cdn.example": ["93.184.216.34"]}
    policy = _policy(answers, ["http://teamflow.local:8080"])
    result = policy.check("http://cdn.example/", resolve_dns=True)
    assert result.allowed is False
    assert result.reason == "HOST_NOT_ALLOWED"


def test_ipv6_aaaa_records_are_checked() -> None:
    """AAAA 也要检查：只查 A 记录是常见绕过点。"""
    answers = {"v6.evil.example": ["fd00::1"]}
    policy = _policy(answers, ["http://teamflow.local:8080"])
    result = policy.check("http://v6.evil.example/", resolve_dns=True)
    assert result.allowed is False
    assert result.reason == "DNS_RESOLVES_TO_PRIVATE_NETWORK"


def test_resolution_failure_does_not_allow_unauthorized_host() -> None:
    policy = _policy({}, ["http://teamflow.local:8080"])
    result = policy.check("http://unknown.example/", resolve_dns=True)
    assert result.allowed is False
    assert result.reason == "HOST_NOT_ALLOWED"


def test_local_teamflow_still_works() -> None:
    """策略不能把本地被测应用一起挡掉。"""
    policy = TargetPolicy.from_origins(["http://127.0.0.1:8011"], pin_dns=True)
    assert policy.check("http://127.0.0.1:8011/projects", resolve_dns=True).allowed
    assert policy.check("http://127.0.0.1:8012/projects").reason == "PORT_NOT_ALLOWED"


def test_redirect_chain_checks_every_hop() -> None:
    policy = _policy({}, ["http://teamflow.local:8080"])
    ok = policy.check_redirect_chain(
        ["http://teamflow.local:8080/a", "http://teamflow.local:8080/b"]
    )
    assert ok.allowed
    bad = policy.check_redirect_chain(["http://teamflow.local:8080/a", "http://evil.example/b"])
    assert bad.allowed is False
    assert bad.reason.startswith("REDIRECT_BLOCKED")


@pytest.mark.parametrize(
    "url,reason",
    [
        ("http://user:pass@teamflow.local:8080/", "URL_USERINFO_FORBIDDEN"),
        ("//evil.example/x", "PROTOCOL_RELATIVE_URL_FORBIDDEN"),
        ("ftp://teamflow.local/", "SCHEME_NOT_ALLOWED"),
        ("http://localhost:8080/", "LOCALHOST_NOT_AUTHORIZED"),
    ],
)
def test_url_level_guards(url: str, reason: str) -> None:
    policy = _policy({}, ["http://teamflow.local:8080"])
    result = policy.resolve(url) if url.startswith("//") else policy.check(url)
    assert result.allowed is False
    assert result.reason == reason
