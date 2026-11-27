"""RUN-018 WP4：DoubleSubmitObserver 真实浏览器独立观测（Playwright Chromium）。

实验 A：同一 BrowserContext 快速双击（真实用户双击语义）：
  - 用户产生 2 次点击 → 捕获前端真实发出的 POST /api/projects 请求数
  - 权威状态回读得出业务实际创建数量
实验 B：两个独立 BrowserContext（独立 Playwright 实例 + 独立会话）并发提交：
  - 检查服务端幂等行为与重复数据

分类必须区分（不合并）：
  FRONTEND_DEBOUNCE_EFFECTIVE / SERVER_IDEMPOTENCY_EFFECTIVE / DUPLICATE_CREATED /
  ONE_REQUEST_FAILED / BUSINESS_STATE_UNKNOWN / OBSERVER_FAILED

HTTP 双线程观测（test_double_submit_run018.py 的 9 项）全部保留；
本文件补上浏览器层证据。TF-TIME-003 激活时按 Ground Truth 预期
FRONTEND_DEBOUNCE_EFFECTIVE（前端 300ms 去重窗口丢弃第二次提交）。
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


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _http_json(method: str, url: str, body: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"} if data is not None else {},
        method=method,
    )
    with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310 - 本地受控目标
        return resp.status, json.loads(resp.read().decode() or "{}")


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
    work = tmp_path_factory.mktemp("run018-wp6")
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


@pytest.fixture(scope="module")
def browser_out(tmp_path_factory) -> Path:
    return tmp_path_factory.mktemp("run018-wp6-browser")


def _set_fault(base: str, fault_id: str, activate: bool) -> None:
    path = f"/api/faults/{fault_id}/{'activate' if activate else 'clear'}"
    _http_json("POST", base + path, {})


def _assert_cleaned(base: str, token: str) -> None:
    _, state = _http_json("GET", base + "/api/state/projects?workspace=default")
    names = [str(p.get("name")) for p in state.get("projects", [])]
    assert not any(n.startswith(f"bp:{token}:") for n in names), (
        f"探针数据必须被清理: {[n for n in names if n.startswith('bp:')]}"
    )


def test_experiment_a_same_context_double_click(teamflow, browser_out) -> None:  # type: ignore[no-untyped-def]
    """实验 A：TF-TIME-003 激活 + 同 context 双击。

    Ground Truth：前端 JS 在 300ms 内丢弃第二次提交 → 预期
    FRONTEND_DEBOUNCE_EFFECTIVE（用户双击 2 次，前端只发 1 次 POST）。
    """
    from traceguard_benchmark.double_submit import (
        FRONTEND_DEBOUNCE_EFFECTIVE,
        BrowserDoubleSubmitObserver,
    )

    base = teamflow
    _set_fault(base, "TF-TIME-003", True)
    try:
        observer = BrowserDoubleSubmitObserver(base, out_dir=browser_out, headless=True)
        result = observer.observe(fault_active=True)
        assert result["cleanup"]["ok"], result["cleanup"]
        a = result["experiment_a"]
        assert a.get("clicks_delivered") == 2, a
        # 请求捕获真实可信：每条请求带状态码与耗时
        for r in a.get("http_requests", []):
            assert r["status"] is not None
            assert r["elapsed_ms"] is not None
        if result["classification"] == FRONTEND_DEBOUNCE_EFFECTIVE:
            assert a["request_count"] == 1, "去重生效时前端应只发 1 次请求"
            assert result["state_readback"]["expA_count"] == 1
            assert result["status"] == "CONFIRMED"
            assert "去重" in result["ground_truth_interpretation"]
        else:
            # 非 debounce 结果必须如实分类（不允许把 UNKNOWN 改成正常）
            assert result["classification"] in (
                "SERVER_IDEMPOTENCY_EFFECTIVE",
                "DUPLICATE_CREATED",
                "ONE_REQUEST_FAILED",
                "BUSINESS_STATE_UNKNOWN",
                "OBSERVER_FAILED",
            ), result["classification"]
        # 证据已落盘
        assert any(p.name.startswith("exp-a-") for p in browser_out.glob("*.png")), (
            "实验 A 必须保存截图"
        )
    finally:
        _set_fault(base, "TF-TIME-003", False)


def test_experiment_b_two_independent_contexts(teamflow, browser_out) -> None:  # type: ignore[no-untyped-def]
    """实验 B：两个独立 BrowserContext 并发提交（无故障，健康环境）。

    独立观测服务端幂等行为：两上下文提交同名前缀项目，权威回读检查重复。
    """
    from traceguard_benchmark.double_submit import BrowserDoubleSubmitObserver

    base = teamflow
    observer = BrowserDoubleSubmitObserver(base, out_dir=browser_out, headless=True)
    result = observer.observe(fault_active=False)
    b = result["experiment_b"]
    ctxs = b.get("contexts", [])
    assert len(ctxs) == 2, (
        f"experiment_b={b} | note={result.get('note')} | cls={result.get('classification')}"
    )
    for c in ctxs:
        assert c.get("error", "") == "", f"上下文异常: {c}"
        assert c.get("request_count", 0) >= 1, f"每个上下文必须发出业务请求: {c}"
    # 分类结果必须落盘为 JSON 证据
    assert any(p.name.startswith("double-submit-browser-") for p in browser_out.glob("*.json"))
    # 清理必须真实成功（不得伪报成功）
    assert result["cleanup"]["ok"], result["cleanup"]


def test_classification_mapping_invariants(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """分类映射不变量（不启动浏览器，直接驱动 _classify）：

    - FRONTEND_DEBOUNCE_EFFECTIVE + fault_active → CONFIRMED；
    - SERVER_IDEMPOTENCY_EFFECTIVE → NOT_TRIGGERED（幂等挡住 ≠ 故障）；
    - DUPLICATE_CREATED → CONFIRMED（真实重复数据）；
    - BUSINESS_STATE_UNKNOWN → UNKNOWN（不得改成正常）；
    - 状态回读失败 → BUSINESS_STATE_UNKNOWN。
    """
    from traceguard_benchmark.double_submit import (
        BUSINESS_STATE_UNKNOWN,
        DUPLICATE_CREATED,
        FRONTEND_DEBOUNCE_EFFECTIVE,
        SERVER_IDEMPOTENCY_EFFECTIVE,
        BrowserDoubleSubmitObserver,
        EffectStatus,
    )

    observer = BrowserDoubleSubmitObserver("http://127.0.0.1:1")

    def _case(a_reqs, a_ok, created, fault_active):
        result = {
            "experiment_a": {
                "clicks_delivered": 2,
                "request_count": a_reqs,
                "ok_responses": a_ok,
            },
            "experiment_b": {"contexts": []},
            "state_readback": {"http_status": 200, "count": created, "expA_count": created},
            "fault_active": fault_active,
        }
        observer._classify(result)
        return result

    # 前端去重生效（双击 2 次 → 1 请求 → 1 条）
    r = _case(1, 1, 1, fault_active=True)
    assert r["classification"] == FRONTEND_DEBOUNCE_EFFECTIVE
    assert observer._finalize(r, time.perf_counter(), "tokentoken1234")["status"] == "CONFIRMED"

    # 服务端幂等（2 请求全成功 → 1 条）
    r = _case(2, 2, 1, fault_active=False)
    assert r["classification"] == SERVER_IDEMPOTENCY_EFFECTIVE
    assert observer._finalize(r, time.perf_counter(), "tokentoken5678")["status"] == (
        EffectStatus.NOT_TRIGGERED.value
    )

    # 重复数据（2 请求 → 2 条）
    r = _case(2, 2, 2, fault_active=False)
    assert r["classification"] == DUPLICATE_CREATED
    assert observer._finalize(r, time.perf_counter(), "tokentoken9012")["status"] == "CONFIRMED"
    assert r["duplicate_detected"] is True

    # 状态回读失败 → UNKNOWN
    result = {
        "experiment_a": {"clicks_delivered": 2, "request_count": 1, "ok_responses": 1},
        "experiment_b": {"contexts": []},
        "state_readback": {"http_status": 500},
        "fault_active": True,
    }
    observer._classify(result)
    assert result["classification"] == BUSINESS_STATE_UNKNOWN
    assert observer._finalize(result, time.perf_counter(), "tokentoken3456")["status"] == (
        EffectStatus.UNKNOWN.value
    )
