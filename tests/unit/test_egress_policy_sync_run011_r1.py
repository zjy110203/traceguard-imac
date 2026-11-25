"""RUN-011-R1 WP5：Egress 代理策略同步回归（OPEN-RISK-01 根因）。

根因：`set_egress_policy` 只更新 controller.policy，EgressProxy 实例
（dataclass 按值持有）仍用 start() 时的旧策略评估——共享 Worker 上
先跑 Run 的策略持续生效，后跑 Run 的授权 origin（含 https 变体）被误拦
PORT_NOT_ALLOWED（RUN-011 WP7 实测 7 次中 2 次）。

覆盖：
1. set_policy 同步更新 controller 与 proxy 两个引用；
2. scheme_dual_policy 仅做同 host:port 的 scheme 对偶，不扩大端口面；
3. 授权 origin 的 https 变体放行、未授权端口仍拦截（不放宽白名单）。
"""

from __future__ import annotations

from traceguard_core.security.target_policy import TargetPolicy
from traceguard_execution.browser.egress import EgressProxyController, scheme_dual_policy


def test_set_policy_updates_proxy_reference() -> None:
    """OPEN-RISK-01 根因：策略更新必须到达 EgressProxy 实例。"""
    p1 = TargetPolicy.from_origins(["http://127.0.0.1:8112"])
    controller = EgressProxyController(scheme_dual_policy(p1), worker_id="w-test")
    controller.start()
    try:
        assert controller._proxy is not None  # noqa: SLF001 - 同包协作验证
        old = controller._proxy.policy  # noqa: SLF001
        assert any(o.scheme == "https" and o.port == 8112 for o in old.origins)

        # 换一个授权 origin（新 Run 的 base_url 不同端口）
        p2 = TargetPolicy.from_origins(["http://127.0.0.1:9112"])
        controller.set_policy(scheme_dual_policy(p2))
        # 修复点：proxy.policy 必须与 controller.policy 同步（此前是旧引用）
        assert controller._proxy.policy is controller.policy  # noqa: SLF001
        assert any(o.scheme == "https" and o.port == 9112 for o in controller._proxy.policy.origins)  # noqa: SLF001
        assert not any(o.port == 8112 for o in controller._proxy.policy.origins)  # noqa: SLF001

        # 评估路径走 proxy.policy：新 origin 的 https 变体放行
        check_ok = controller._proxy.policy.check("https://127.0.0.1:9112/", resolve_dns=False)  # noqa: SLF001
        assert check_ok.allowed
        # 旧 origin 的端口不再授权（不扩大端口白名单）
        check_old = controller._proxy.policy.check("https://127.0.0.1:8112/", resolve_dns=False)  # noqa: SLF001
        assert not check_old.allowed
        assert check_old.reason == "PORT_NOT_ALLOWED"
    finally:
        controller.stop()


def test_scheme_dual_policy_keeps_port_surface() -> None:
    """对偶只翻转 scheme：host/port 检查全部保持，不扩大可访问面。"""
    p = TargetPolicy.from_origins(["http://127.0.0.1:8112"])
    dual = scheme_dual_policy(p)
    assert {o.port for o in dual.origins} == {8112}
    assert {o.scheme for o in dual.origins} == {"http", "https"}
    # 未授权端口仍然拒绝（PORT_NOT_ALLOWED），验证「不扩大端口白名单」
    r = dual.check("https://127.0.0.1:9555/", resolve_dns=False)
    assert not r.allowed and r.reason == "PORT_NOT_ALLOWED"


def test_audit_summary_exposes_policy_origins() -> None:
    """审计快照必须可对账当前 policy_origins（建议 1）。"""
    p = TargetPolicy.from_origins(["http://127.0.0.1:8112"])
    controller = EgressProxyController(scheme_dual_policy(p), worker_id="w-test")
    controller.start()
    try:
        summary = controller.audit_summary()
        origins = set(summary.get("policy_origins") or [])
        assert "http://127.0.0.1:8112" in origins
        assert "https://127.0.0.1:8112" in origins
    finally:
        controller.stop()
