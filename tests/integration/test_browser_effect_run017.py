"""RUN-017 WP2.5：浏览器级 UI / Locator 故障效果观测（真实 Chromium + 真实 TeamFlow）。

验证 Browser Effect Observer 能独立回答"故障是否真的影响用户可见业务"：

- 正常流程：用户可见操作成功 + 权威状态确认落库 → NOT_TRIGGERED；
- TF-UI-001：业务成功但页面显示失败 → CONFIRMED（用户无法判断结果）；
- TF-UI-002：提交被静默忽略、无请求无反馈、业务未落库 → CONFIRMED；
- TF-UI-003：业务成功但提示被误导 → CONFIRMED；
- TF-UI-004：成功提示延迟 3s → NOT_TRIGGERED（时序观察，不是失效）；
- TF-LOC-001：test_id 变化但用户可见按钮仍可用 → NOT_TRIGGERED（LOTATOR_ONLY）；
- TF-COMP-003：定位器变化 + 静默忽略 → CONFIRMED（业务真的失败）。

隔离：独立 TeamFlow 子进程 + 独立 SQLite + 独立 BrowserContext；
探针只清理自己创建的数据（名称前缀带随机 token）。
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

from traceguard_benchmark.browser_effect import BrowserEffectObserver  # noqa: E402
from traceguard_benchmark.fault_effect import EffectStatus  # noqa: E402

pytest.importorskip("playwright.sync_api")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _http(method: str, url: str, body: dict | None = None, timeout: float = 10.0) -> dict:
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
    work = tmp_path_factory.mktemp("run017-browser")
    port = _free_port()
    # 显式白名单环境（不读取宿主机环境）：独立 DB + 独立进程
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
        # reset 只清瞬时故障；sticky（复合故障会传递 sticky）必须逐个显式清除
        available = _http("GET", teamflow + "/api/faults").get("available") or []
        for fault in available:
            try:
                _http("POST", f"{teamflow}/api/faults/{fault}/clear", {})
            except Exception:  # noqa: BLE001 - 清理失败不掩盖主断言
                pass
        _http("POST", teamflow + "/api/test/reset", {})

    _reset()
    yield
    _reset()


def _activate(teamflow: str, faults: list[str]) -> None:
    for fault in faults:
        _http("POST", f"{teamflow}/api/faults/{fault}/activate", {"sticky": True})


@pytest.fixture()
def observer(teamflow, tmp_path):  # type: ignore[no-untyped-def]
    return BrowserEffectObserver(
        teamflow,
        out_dir=tmp_path / "browser-evidence",
        headless=True,
        action_timeout_ms=10000,
        settle_timeout_ms=6000,
    )


def _bp_rows(teamflow: str) -> list[str]:
    state = _http("GET", teamflow + "/api/state/projects?workspace=default")
    return [p["name"] for p in state.get("projects") or [] if str(p.get("name")).startswith("bp:")]


def test_normal_flow_is_not_triggered(observer, teamflow) -> None:  # type: ignore[no-untyped-def]
    result = observer.observe(["TF-NORMAL-001"])
    assert result["fault_effect_status"] == EffectStatus.NOT_TRIGGERED.value
    assert result["fault_effect_classification"] == "OPERATION_OK"
    detail = result["fault_effect_probes"][0]["detail"]
    assert detail["business_state"]["persisted"] is True
    assert detail["ui_feedback"]["success_visible"] is True
    assert result["fault_effect_artifacts"], "必须留下 DOM/ARIA/截图证据"
    assert _bp_rows(teamflow) == [], "探针数据必须清理干净"


def test_ui_success_feedback_missing_is_confirmed(observer, teamflow) -> None:  # type: ignore[no-untyped-def]
    _activate(teamflow, ["TF-UI-001"])
    result = observer.observe(["TF-UI-001"])
    assert result["fault_effect_status"] == EffectStatus.CONFIRMED.value
    assert result["fault_effect_classification"] == "UI_FEEDBACK_CONTRADICTS_BUSINESS"
    detail = result["fault_effect_probes"][0]["detail"]
    assert detail["business_state"]["persisted"] is True
    assert detail["ui_feedback"]["error_visible"] is True


def test_silent_ignore_is_confirmed(observer, teamflow) -> None:  # type: ignore[no-untyped-def]
    _activate(teamflow, ["TF-UI-002"])
    result = observer.observe(["TF-UI-002"])
    assert result["fault_effect_status"] == EffectStatus.CONFIRMED.value
    assert result["fault_effect_classification"] == "SILENT_IGNORE"
    detail = result["fault_effect_probes"][0]["detail"]
    assert detail["business_state"]["persisted"] is False
    assert detail["ui_feedback"]["success_visible"] is False
    assert detail["ui_feedback"]["error_visible"] is False


def test_misleading_feedback_is_confirmed(observer, teamflow) -> None:  # type: ignore[no-untyped-def]
    _activate(teamflow, ["TF-UI-003"])
    result = observer.observe(["TF-UI-003"])
    assert result["fault_effect_status"] == EffectStatus.CONFIRMED.value
    detail = result["fault_effect_probes"][0]["detail"]
    assert detail["business_state"]["persisted"] is True
    assert detail["ui_feedback"]["error_visible"] is True  # 成功页却显示失败文案


def test_locator_change_does_not_break_user_flow(observer, teamflow) -> None:  # type: ignore[no-untyped-def]
    """test_id 变了，但用户用可见名称仍能完成业务 → 不能判业务失效。"""
    _activate(teamflow, ["TF-LOC-001"])
    result = observer.observe(["TF-LOC-001"])
    assert result["fault_effect_status"] == EffectStatus.NOT_TRIGGERED.value
    assert result["fault_effect_classification"] == "LOCATOR_ONLY_BREAKAGE"
    detail = result["fault_effect_probes"][0]["detail"]
    assert detail["expected_testids"]["create-submit"] is False
    assert detail["visible_controls"]["submit_by_role_name"] is True
    assert detail["business_state"]["persisted"] is True


def test_delayed_feedback_is_timing_not_failure(observer, teamflow) -> None:  # type: ignore[no-untyped-def]
    _activate(teamflow, ["TF-UI-004"])
    result = observer.observe(["TF-UI-004"])
    assert result["fault_effect_status"] == EffectStatus.NOT_TRIGGERED.value
    assert result["fault_effect_classification"] == "OPERATION_OK_FEEDBACK_DELAYED"
    detail = result["fault_effect_probes"][0]["detail"]
    assert detail["feedback_ms"] >= 2000


def test_composite_locator_and_silent_ignore_is_confirmed(observer, teamflow) -> None:  # type: ignore[no-untyped-def]
    _activate(teamflow, ["TF-COMP-003"])
    result = observer.observe(["TF-COMP-003"])
    assert result["fault_effect_status"] == EffectStatus.CONFIRMED.value
    assert result["fault_effect_classification"] == "SILENT_IGNORE"
    detail = result["fault_effect_probes"][0]["detail"]
    assert detail["business_state"]["persisted"] is False


def test_observer_does_not_accept_verdict_or_ground_truth(observer) -> None:  # type: ignore[no-untyped-def]
    """隔离约束：构造函数不接受 Run verdict / Ground Truth / 诊断结果。"""
    with pytest.raises(TypeError):
        BrowserEffectObserver(  # type: ignore[call-arg]
            "http://127.0.0.1:1", verdict="PASSED", ground_truth={}
        )
