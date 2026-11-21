"""阶段12 安全与可靠性测试（本地可验证部分）。

外部 Web 应用泛化因未获授权保持 BLOCKED（见 decisions.md P-EXT），本测试只验证门禁逻辑。
"""

from __future__ import annotations

import concurrent.futures

from traceguard_core.security.policy import ResourceQuota, SecurityPolicy
from traceguard_evidence.event_store import EventStore


def test_ssrf_private_ip_blocked() -> None:
    p = SecurityPolicy()
    for url in ("http://169.254.169.254/latest/meta-data", "http://10.0.0.5/", "http://192.168.0.1/"):
        r = p.check_url(url)
        assert r["allowed"] is False, url


def test_host_not_whitelisted_blocked() -> None:
    p = SecurityPolicy()
    r = p.check_url("http://example.com/")
    assert r["allowed"] is False and r["reason"] == "HOST_NOT_ALLOWED"


def test_teamflow_local_host_allowed() -> None:
    p = SecurityPolicy()
    assert p.check_url("http://127.0.0.1:8011/login")["allowed"] is True


def test_wrong_local_port_blocked() -> None:
    p = SecurityPolicy()
    r = p.check_url("http://127.0.0.1:9999/")
    assert r["allowed"] is False and r["reason"] == "PORT_NOT_ALLOWED"


def test_dns_rebinding_to_private_blocked(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    p = SecurityPolicy(allowed_hosts={"trusted.example.com"})
    monkeypatch.setattr("socket.gethostbyname", lambda host: "10.0.0.1")
    r = p.check_url("http://trusted.example.com/", resolve_dns=True)
    assert r["allowed"] is False and r["reason"] == "DNS_REBINDING_TO_PRIVATE"


def test_redirect_chain_rechecked() -> None:
    p = SecurityPolicy()
    chain = ["http://127.0.0.1:8011/start", "http://169.254.169.254/steal"]
    r = p.check_redirect(chain)
    assert r["allowed"] is False and "REDIRECT_BLOCKED" in r["reason"]


def test_too_many_redirects_blocked() -> None:
    p = SecurityPolicy(max_redirects=2)
    chain = ["http://127.0.0.1:8011/" + str(i) for i in range(5)]
    r = p.check_redirect(chain)
    assert r["allowed"] is False and r["reason"] == "TOO_MANY_REDIRECTS"


def test_credential_detection_and_redaction() -> None:
    p = SecurityPolicy()
    r = p.scan_credentials("GET /?token=abc123&q=1")
    assert r["has_credential"] is True
    assert "abc123" not in r["redacted"]


def test_external_access_requires_authorization() -> None:
    p = SecurityPolicy()
    # 未授权外部应用 → BLOCKED（阶段12 外部泛化的真实边界）
    r = p.check_external_authorization("external.example.com", set())
    assert r["allowed"] is False and r["reason"] == "EXTERNAL_NOT_AUTHORIZED"
    # 明确授权后允许
    r2 = p.check_external_authorization("external.example.com", {"external.example.com"})
    assert r2["allowed"] is True


def test_resource_quota_documented_not_fabricated_slo() -> None:
    q = ResourceQuota()
    assert "需按实测调整" in q.note  # 明确不是编造的 SLO


# ---------- 可靠性：并发事件写入序号安全 ----------
def test_concurrent_event_append_sequence_unique() -> None:
    store = EventStore()
    iid = store.new_producer_instance("runner")

    def append(i: int) -> int:
        ev = store.append(
            event_type="test", server_workspace_id="ws",
            producer_id="runner", producer_instance_id=iid,
            idempotency_key=f"k-{i}", payload={"i": i},
        )
        return ev.sequence

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        seqs = list(ex.map(append, range(50)))
    assert len(seqs) == 50
    assert len(set(seqs)) == 50  # 并发安全，无重复序号
    assert sorted(seqs) == list(range(1, 51))


def test_event_idempotent_under_concurrency() -> None:
    """并发重复投递同一幂等键 → 只产生一条事件。"""
    store = EventStore()
    iid = store.new_producer_instance("runner")

    def append_same(_: int):
        return store.append(
            event_type="test", server_workspace_id="ws",
            producer_id="runner", producer_instance_id=iid,
            idempotency_key="same-key", payload={"v": 1},
        ).event_id

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        ids = list(ex.map(append_same, range(20)))
    assert len(set(ids)) == 1
    assert len(store.list_events()) == 1
