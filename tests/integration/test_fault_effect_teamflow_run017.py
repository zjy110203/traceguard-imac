"""RUN-017 WP2：探针在**真实 TeamFlow** 上的分类正确性（不是 mock）。

单元测试保证"状态码 → 结论"的映射；本文件保证这些映射**符合真实故障语义**：

- 健康环境不能被判成受影响（RUN-016 的 persistence 探针在健康环境永远 CONFIRMED，
  因为它拿 name 去 grep 只返回 keys 的状态接口）；
- 每个故障机制都能被对应探针独立观测到；
- 慢但最终落库（TF-DB-005）不能被判成未持久化；
- 探针只清理自己的数据。

环境：独立 TeamFlow 子进程 + 独立 SQLite；不触碰平台库。
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "benchmark"))

from traceguard_benchmark.fault_effect import (  # noqa: E402
    EffectStatus,
    capture_baseline,
    new_probe_token,
    observe_fault_activation,
    observe_fault_effect,
)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _http(method: str, url: str, body: dict | None = None, timeout: float = 15.0) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"} if data is not None else {},
        method=method,
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - 本地测试目标
        return json.loads(resp.read().decode() or "{}")


def _wait_health(url: str, timeout: float = 40.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:  # noqa: S310
                if resp.status == 200:
                    return True
        except Exception:  # noqa: BLE001
            time.sleep(0.3)
    return False


@pytest.fixture(scope="module")
def teamflow(tmp_path_factory):
    work = tmp_path_factory.mktemp("run017-probe")
    port = _free_port()
    pythonpath = ":".join(
        str(ROOT / p)
        for p in (
            "packages/core",
            "packages/contracts",
            "packages/execution",
            "packages/agents",
            "packages/evidence",
            "packages/persistence",
            "apps/api",
            "apps/teamflow",
        )
    )
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", str(work)),
        "TMPDIR": os.environ.get("TMPDIR", "/tmp"),
        "PYTHONPATH": pythonpath,
        "TG_TEAMFLOW_DB": f"sqlite:///{work / 'teamflow.db'}",
    }
    proc = subprocess.Popen(  # noqa: S603 - 受控本地子进程
        [
            sys.executable,
            "-m",
            "uvicorn",
            "traceguard_teamflow.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{port}"
    if not _wait_health(base + "/health"):
        proc.terminate()
        pytest.skip("TeamFlow 未能在本机启动")
    yield base
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:  # pragma: no cover
        proc.kill()


@pytest.fixture(autouse=True)
def clean_teamflow(teamflow):  # type: ignore[no-untyped-def]
    def _reset() -> None:
        available = _http("GET", teamflow + "/api/faults").get("available") or []
        for fault in available:
            try:
                _http("POST", f"{teamflow}/api/faults/{fault}/clear", {})
            except Exception:  # noqa: BLE001
                pass
        _http("POST", teamflow + "/api/test/reset", {})

    _reset()
    yield
    _reset()


def _observe_without_fault(base: str, faults: list[str], *, timeout: float = 6.0) -> dict:
    """只采基线 + 观测，不激活故障（用于"健康环境不能判受影响"的回归）。"""
    baseline = capture_baseline(base, faults, timeout=timeout, probe_token=new_probe_token())
    return observe_fault_effect(base, faults, baseline=baseline, timeout=timeout)


def _observe_with_fault(base: str, faults: list[str], *, timeout: float = 6.0) -> tuple[dict, dict]:
    token = new_probe_token()
    baseline = capture_baseline(base, faults, timeout=timeout, probe_token=token)
    for fault in faults:
        _http("POST", f"{base}/api/faults/{fault}/activate", {"sticky": True})
    activation = observe_fault_activation(base, faults)
    result = observe_fault_effect(base, faults, baseline=baseline, timeout=timeout)
    return result, activation


def _bp_rows(base: str) -> list[str]:
    state = _http("GET", base + "/api/state/projects?workspace=default")
    probe = _http("GET", base + "/api/state/projects?workspace=tg-benchmark-probe")
    names = [p["name"] for p in state.get("projects") or []]
    names += [p["name"] for p in probe.get("projects") or []]
    return [n for n in names if str(n).startswith("bp:")]


def test_healthy_environment_is_not_triggered(teamflow) -> None:  # type: ignore[no-untyped-def]
    """最关键回归：健康环境下所有自证型探针都必须判 NOT_TRIGGERED。"""
    for faults in (["TF-DB-001"], ["TF-API-001"], ["TF-DB-003"], ["TF-TIME-002"]):
        available = _http("GET", teamflow + "/api/faults").get("available") or []
        for fault in available:
            _http("POST", f"{teamflow}/api/faults/{fault}/clear", {})
        result = _observe_without_fault(teamflow, faults)
        assert result["fault_effect_status"] == EffectStatus.NOT_TRIGGERED.value, (
            faults,
            result,
        )
    assert _bp_rows(teamflow) == []


@pytest.mark.parametrize(
    ("faults", "expected_class"),
    [
        (["TF-API-001"], "HTTP_5XX"),
        (["TF-API-002"], "HTTP_5XX"),
        (["TF-API-003"], "HTTP_5XX"),
        (["TF-API-004"], "HTTP_5XX"),
        (["TF-SEC-001"], "AUTHZ_DENIED_WITH_BASELINE"),
        (["TF-SEC-002"], "AUTHZ_DENIED_WITH_BASELINE"),
    ],
)
def test_fault_mechanisms_are_independently_observed(
    teamflow,
    faults,
    expected_class,  # type: ignore[no-untyped-def]
) -> None:
    result, _activation = _observe_with_fault(teamflow, faults)
    assert result["fault_effect_status"] == EffectStatus.CONFIRMED.value, result
    assert result["fault_effect_classification"] == expected_class, result


def test_persistence_rollback_is_observed(teamflow) -> None:  # type: ignore[no-untyped-def]
    result, _ = _observe_with_fault(teamflow, ["TF-DB-001"])
    assert result["fault_effect_status"] == EffectStatus.CONFIRMED.value
    assert result["fault_effect_classification"] in {
        "API_ACCEPTED_NOT_PERSISTED",
        "HTTP_5XX",
    }
    assert _bp_rows(teamflow) == []


def test_duplicate_persistence_fault_is_observed(teamflow) -> None:  # type: ignore[no-untyped-def]
    result, _ = _observe_with_fault(teamflow, ["TF-DB-002"])
    assert result["fault_effect_status"] == EffectStatus.CONFIRMED.value
    assert result["fault_effect_classification"] == "DUPLICATE_NOT_PERSISTED"
    assert _bp_rows(teamflow) == []


def test_slow_write_still_persisted_is_not_triggered(teamflow) -> None:  # type: ignore[no-untyped-def]
    """TF-DB-005：写入慢 4s 但最终落库 → 不能被判成"未持久化"。"""
    result, _ = _observe_with_fault(teamflow, ["TF-DB-005"], timeout=10)
    assert result["fault_effect_status"] == EffectStatus.NOT_TRIGGERED.value, result
    assert _bp_rows(teamflow) == []


def test_stale_state_read_is_observed(teamflow) -> None:  # type: ignore[no-untyped-def]
    """TF-DB-003：写入返回成功但状态读暂时看不到该行（值过期，不是响应变慢）。"""
    result, _ = _observe_with_fault(teamflow, ["TF-DB-003"], timeout=12)
    assert result["fault_effect_status"] == EffectStatus.CONFIRMED.value
    assert result["fault_effect_classification"] == "STALE_READ"
    probe = next(p for p in result["fault_effect_probes"] if p["kind"] == "state_consistency")
    assert probe["detail"]["immediate_read"]["present"] is False
    assert probe["detail"]["direct_read"]["ok"] is True
    assert probe["detail"]["eventual_read"]["present"] is True
    assert _bp_rows(teamflow) == []


def test_list_delay_is_observed_on_business_list_path(teamflow) -> None:  # type: ignore[no-untyped-def]
    """TF-TIME-002：列表读取延迟 5s —— 只看状态接口会漏判。"""
    result, _ = _observe_with_fault(teamflow, ["TF-TIME-002"], timeout=10)
    assert result["fault_effect_status"] == EffectStatus.CONFIRMED.value
    probe = next(p for p in result["fault_effect_probes"] if p["kind"] == "read_latency")
    assert probe["detail"]["comparison"]["delta_p95_ms"] >= 800


def test_network_timeout_is_observed(teamflow) -> None:  # type: ignore[no-untyped-def]
    result, _ = _observe_with_fault(teamflow, ["TF-NET-002"], timeout=6)
    assert result["fault_effect_status"] == EffectStatus.CONFIRMED.value
    assert result["fault_effect_classification"] in {"TIMEOUT", "HTTP_5XX"}


def test_activation_snapshot_is_independent(teamflow) -> None:  # type: ignore[no-untyped-def]
    _http("POST", teamflow + "/api/faults/TF-API-001/activate", {"sticky": True})
    activation = observe_fault_activation(teamflow, ["TF-API-001"])
    assert activation["fault_activation_status"] == "ACTIVE"
    assert activation["fault_activation_source"] == "teamflow:/api/faults"
    assert "TF-API-001" in activation["fault_activation_active"]
