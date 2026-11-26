"""RUN-016 WP3：Fault Effect 独立观测的专项测试。

用本地 HTTP 服务模拟 TeamFlow 的三种业务响应（正常 / API 500 / 落库失败 / 状态滞后），
验证 `observe_fault_effect()` 的判定**只来自独立探针**：

- 不接收任何 Run verdict 参数；
- 故障激活但业务正常 → NOT_TRIGGERED；
- 纯 UI / 定位器类故障没有独立探针 → UNKNOWN（不允许推断）；
- 探针证据带可追溯的 evidence id 与原始请求/响应摘要。
"""

from __future__ import annotations

import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "benchmark"))

from traceguard_benchmark.fault_effect import (  # noqa: E402
    BENCHMARK_SCHEMA_VERSION,
    EffectStatus,
    diff_business_state,
    observe_fault_effect,
    snapshot_business_state,
)


class _State:
    def __init__(self) -> None:
        self.mode = "healthy"  # healthy / api_500 / persist_fail / lag / timeout
        self.projects: list[str] = []
        self.lag_s = 1.6


def _make_server(state: _State) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args) -> None:  # noqa: D102 - 静默访问日志
            return

        def _send(self, code: int, payload: dict) -> None:
            body = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 约定
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            try:
                body = json.loads(raw or b"{}")
            except json.JSONDecodeError:
                body = {}
            if state.mode == "api_500":
                self._send(500, {"detail": "simulated"})
                return
            if state.mode == "timeout":
                time.sleep(5.0)
            name = str(body.get("name") or "")
            if not name:
                self._send(422, {"detail": "name required"})
                return
            if state.mode != "persist_fail":
                state.projects.append(name)
            self._send(201, {"name": name})

        def do_GET(self) -> None:  # noqa: N802
            if self.path.startswith("/api/state/projects"):
                if state.mode == "lag":
                    time.sleep(state.lag_s)
                self._send(200, {"count": len(state.projects), "projects": state.projects})
                return
            self._send(404, {"detail": "not found"})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


@pytest.fixture()
def teamflow():
    state = _State()
    server = _make_server(state)
    base = f"http://127.0.0.1:{server.server_address[1]}"
    yield base, state
    server.shutdown()
    server.server_close()


def test_api_fault_is_confirmed_by_independent_probe(teamflow) -> None:
    base, state = teamflow
    state.mode = "api_500"
    result = observe_fault_effect(base, ["TF-API-001"], timeout=3)
    assert result["fault_effect_status"] == EffectStatus.CONFIRMED.value
    assert result["fault_effect_source"].startswith("INDEPENDENT_TEAMFLOW_PROBE")
    assert result["fault_effect_evidence_refs"]
    assert result["benchmark_schema_version"] == BENCHMARK_SCHEMA_VERSION
    probe = result["fault_effect_probes"][0]
    assert probe["detail"]["status"] == 500


def test_healthy_business_marks_not_triggered(teamflow) -> None:
    base, state = teamflow
    result = observe_fault_effect(base, ["TF-API-001"], timeout=3)
    assert result["fault_effect_status"] == EffectStatus.NOT_TRIGGERED.value


def test_persistence_fault_detected_by_state_check(teamflow) -> None:
    base, state = teamflow
    state.mode = "persist_fail"
    result = observe_fault_effect(base, ["TF-DB-001"], timeout=3)
    assert result["fault_effect_status"] == EffectStatus.CONFIRMED.value
    probe = result["fault_effect_probes"][0]
    assert probe["detail"]["reason"] == "API_ACCEPTED_BUT_NOT_PERSISTED"
    assert probe["detail"]["side_effect"] is True


def test_state_lag_is_measured_not_inferred(teamflow) -> None:
    base, state = teamflow
    state.mode = "lag"
    result = observe_fault_effect(base, ["TF-DB-004"], timeout=8)
    assert result["fault_effect_status"] == EffectStatus.CONFIRMED.value
    assert result["fault_effect_probes"][0]["elapsed_ms"] >= 1500


def test_ui_only_fault_is_unknown(teamflow) -> None:
    base, _state = teamflow
    result = observe_fault_effect(base, ["TF-LOC-001"], timeout=3)
    assert result["fault_effect_status"] == EffectStatus.UNKNOWN.value
    assert result["fault_effect_source"] == "NO_INDEPENDENT_PROBE"
    assert result["fault_effect_evidence_refs"] == []
    assert "Run verdict" in result["fault_effect_note"]


def test_timeout_is_confirmed_as_unavailable(teamflow) -> None:
    base, state = teamflow
    state.mode = "timeout"
    result = observe_fault_effect(base, ["TF-NET-002"], timeout=3)
    assert result["fault_effect_status"] == EffectStatus.CONFIRMED.value
    assert result["fault_effect_probes"][0]["kind"] in {"api_timeout", "persistence"}


def test_observation_never_accepts_verdict(teamflow) -> None:
    """接口层面就不存在 verdict 入参——效果判定无法被测试结论污染。"""
    base, state = teamflow
    state.mode = "api_500"
    with pytest.raises(TypeError):
        observe_fault_effect(base, ["TF-API-001"], verdict="PASSED")  # type: ignore[call-arg]
    result = observe_fault_effect(base, ["TF-API-001"], timeout=3)
    assert "verdict" not in json.dumps(result["fault_effect_probes"])


def test_business_state_snapshot_and_diff(teamflow) -> None:
    base, state = teamflow
    before = snapshot_business_state(base)
    state.projects.extend(["a", "b"])
    after = snapshot_business_state(base)
    diff = diff_business_state(before, after)
    assert diff["before_count"] == 0
    assert diff["after_count"] == 2
    assert diff["count_changed"] is True
    assert diff["delta"] == 2


def test_ground_truth_path_is_blocked_even_on_authorized_host() -> None:
    """RUN-016 WP3.3：授权主机上的评测标签路径也不允许执行链路访问。"""
    from traceguard_core.security.target_policy import TargetPolicy

    policy = TargetPolicy.from_origins(["http://127.0.0.1:8111"])
    blocked = policy.check("http://127.0.0.1:8111/api/ground-truth/API-500")
    assert blocked.allowed is False
    assert blocked.reason == "GROUND_TRUTH_PATH_BLOCKED"
    # 正常业务路径不受影响
    ok = policy.check("http://127.0.0.1:8111/api/projects")
    assert ok.allowed is True
    # 相对路径同样被拦
    rel = policy.resolve("/api/ground-truth/FALSE-PASS", "http://127.0.0.1:8111")
    assert rel.allowed is False and rel.reason == "GROUND_TRUTH_PATH_BLOCKED"
