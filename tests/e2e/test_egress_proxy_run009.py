"""RUN-009 WP3：受控出口代理端到端验收。

复用 `scripts/egress_proxy_run009.py` 的真实场景（本地服务 + Playwright
Chromium 经代理），本文件只做断言与回归保护，不重写场景实现。

核心断言：未授权服务器实际收到的请求数必须为 0；不能只凭 URL 检查宣称安全。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from egress_proxy_run009 import run_all_scenarios  # noqa: E402
from traceguard_core.security.egress_proxy import EgressProxy  # noqa: E402
from traceguard_core.security.target_policy import TargetPolicy  # noqa: E402


@pytest.fixture(scope="module")
def report(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """运行一次真实场景，并把原始证据写入 pytest 临时目录供失败排查。"""
    out_dir = tmp_path_factory.mktemp("egress-proxy-run009")
    result = run_all_scenarios(out_dir)
    (out_dir / "evidence.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result


@pytest.mark.e2e
def test_all_checks_passed(report: dict[str, Any]) -> None:
    assert report["passed"] is True, report["checks"]
    assert all(report["checks"].values()), report["checks"]


@pytest.mark.e2e
def test_unauthorized_servers_received_zero_requests(report: dict[str, Any]) -> None:
    """本 WP 的关键量化指标：未授权目标实际收到 0 个请求。"""
    assert report["unauthorized_request_count"] == 0
    for name, rec in report["unauthorized_servers"].items():
        assert rec["count"] == 0, f"{name} 收到请求: {rec['hits']}"


@pytest.mark.e2e
def test_http_navigation_and_redirects(report: dict[str, Any]) -> None:
    checks = report["checks"]
    for key in (
        "allowed_target_200",
        "unauthorized_http_status_403",
        "unauthorized_target_zero_requests",
        "cross_origin_fetch_zero_requests",
        "top_level_302_zero_requests",
        "multi_hop_redirect_zero_requests",
    ):
        assert checks[key] is True, key

    scenarios = {s["name"]: s for s in report["scenarios"]}
    chain = scenarios["multi_hop_redirect"]
    # 证明多跳确实发生（不是因为第一跳就被拦而假通过）
    assert "/chain1" in chain["chain_hits"]
    assert "/chain2" in chain["chain_hits"]
    assert chain["unauthorized_new_hits"] == 0


@pytest.mark.e2e
def test_dns_change_and_websocket(report: dict[str, Any]) -> None:
    checks = report["checks"]
    assert checks["dns_change_blocked"] is True
    assert checks["websocket_authorized"] is True
    assert checks["websocket_unauthorized_zero_requests"] is True

    scenarios = {s["name"]: s for s in report["scenarios"]}
    dns = scenarios["dns_change"]
    assert dns["navigation_before"]["status"] == 200
    assert dns["navigation_after"]["status"] == 403
    assert dns["flip_target_hits"] == []
    ws = scenarios["websocket"]
    assert ws["authorized_result"] == "msg:echo:ping"
    assert ws["unauthorized_result"] != "open"
    assert ws["forbidden_hits"] == []


@pytest.mark.e2e
def test_ipv4_ipv6_and_protocol_rejections(report: dict[str, Any]) -> None:
    checks = report["checks"]
    for key in (
        "ipv6_authorized_200",
        "ipv6_unauthorized_403",
        "ipv6_unauthorized_zero_requests",
        "private_network_all_blocked",
        "illegal_schemes_all_blocked",
    ):
        assert checks[key] is True, key

    scenarios = {s["name"]: s for s in report["scenarios"]}
    private = scenarios["private_network"]["cases"]
    # 私网 / 链路本地 / 未授权域名指向内网都必须 403，且没有建立上游连接
    for case in private.values():
        assert case["status"] == 403
    audit_by_reason = {e["reason"] for e in report["proxy_audit"] if not e["connected"]}
    assert "DNS_RESOLVES_TO_PRIVATE_NETWORK" in audit_by_reason


@pytest.mark.e2e
def test_no_authentication_header_leakage(report: dict[str, Any]) -> None:
    """代理凭据不得转发到目标；任何头值不得进入审计/证据。"""
    checks = report["checks"]
    assert checks["proxy_auth_not_forwarded"] is True
    assert checks["audit_no_auth_headers"] is True
    probe = report["raw_evidence"]["auth_header_probe"]
    assert probe["upstream_received_proxy_authorization"] is False
    assert probe["audit_leaked_any_token"] is False


@pytest.mark.e2e
def test_chromium_forced_loopback_through_proxy(report: dict[str, Any]) -> None:
    """证明 -loopback 隐式绕过已关闭，否则 127.0.0.1 场景根本不会经过代理。"""
    argv = report["raw_evidence"]["chromium_proxy_argv"]
    joined = " ".join(argv)
    assert "--proxy-bypass-list=<-loopback>" in joined, argv
    assert any(arg.startswith("--proxy-server=http://127.0.0.1:") for arg in argv), argv

    # 代理审计里必须真的出现 loopback 目标（功能证据，不只依赖命令行）
    loopback_entries = [
        e for e in report["proxy_audit"] if e["host"] in {"127.0.0.1", "::1"}
    ]
    assert loopback_entries, "代理审计中没有 loopback 请求，bypass 可能未生效"


def test_proxy_ipv6_and_keepalive_validation_regression() -> None:
    """快速回归：IPv6 URL 重建 + keep-alive 后续请求不得绕过逐请求校验。"""
    policy = TargetPolicy.from_origins(
        ["http://127.0.0.1:1", "http://[::1]:1"], pin_dns=False
    )
    proxy = EgressProxy(policy)

    # IPv6 字面量必须能重建出可解析的 URL（曾因未加 [] 被误判 MALFORMED_URL）
    d = proxy._evaluate(  # noqa: SLF001 - 直接测决策层，不建立真实连接
        channel="http",
        method="GET",
        raw_scheme="http",
        host="::1",
        port=1,
        path="/x",
    )
    assert d.allowed is True, d  # 曾被错误判成 MALFORMED_URL（IPv6 未加 []）
    assert d.chosen_ip == "::1"

    d2 = proxy._evaluate(  # noqa: SLF001
        channel="http",
        method="GET",
        raw_scheme="http",
        host="10.1.2.3",
        port=80,
        path="/",
    )
    assert d2.allowed is False
    assert d2.reason == "PRIVATE_NETWORK_BLOCKED"
