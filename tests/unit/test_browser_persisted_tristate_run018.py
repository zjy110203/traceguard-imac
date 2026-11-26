"""RUN-018 WP2：BrowserEffectObserver 业务回读三态的测试矩阵。

源码审查发现的真实缺陷：`_persisted()` 只看 `projects` 列表、不看 HTTP 状态——
独立状态接口 500 / 403 / 连接失败时 projects 不存在 → persisted=false →
被误判成"业务没有落库" → 分类器进入 MISLEADING_SUCCESS_ON_FAILURE /
SILENT_IGNORE / OPERATION_FAILED_ERROR_SURFACED（假 CONFIRMED）。

本文件覆盖：

- `_persisted()` 三态矩阵：正常 200+存在 / 200+不存在、403、500、503、连接拒绝、
  连接中断、超时、非 JSON、缺 projects、缺 ok；
- `_classify()` 约束：UNKNOWN 时不得进入上述三个误判分类，且必须给出观测失败原因；
- `_request_json()` 对 HTTP 状态码 / 非 JSON / 网络失败的真实处理（本地 HTTP 服务）；
- 既有分类路径的回归保护（PRESENT / ABSENT 语义不变）。
"""

from __future__ import annotations

import json
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "benchmark"))

from traceguard_benchmark.browser_effect import (  # noqa: E402
    BrowserEffectObserver,
    _request_json,
)
from traceguard_benchmark.fault_effect import EffectStatus, ReadVerdict  # noqa: E402

NAME = "bp:token:ui"

FORBIDDEN_WHEN_UNKNOWN = {
    "MISLEADING_SUCCESS_ON_FAILURE",
    "SILENT_IGNORE",
    "OPERATION_FAILED_ERROR_SURFACED",
}


def _ok_state(projects: list[dict]) -> dict:
    return {
        "http_status": 200,
        "payload": {"ok": True, "count": len(projects), "projects": projects},
        "raw": "",
        "error": "",
        "elapsed_ms": 1.0,
    }


def _broken_state(http_status: int, error: str, payload: dict | None = None, raw: str = "") -> dict:
    return {
        "http_status": http_status,
        "payload": payload,
        "raw": raw,
        "error": error,
        "elapsed_ms": 1.0,
    }


# ---------------------------------------------------------------------------
# _persisted() 三态矩阵
# ---------------------------------------------------------------------------
def test_persisted_present() -> None:
    state = _ok_state([{"key": "TF-1", "name": NAME, "status": "ACTIVE"}])
    verdict, reason = BrowserEffectObserver._persisted(state, NAME)
    assert verdict is ReadVerdict.PRESENT
    assert reason == "OK"


def test_persisted_absent() -> None:
    state = _ok_state([{"key": "TF-2", "name": "other", "status": "ACTIVE"}])
    verdict, _ = BrowserEffectObserver._persisted(state, NAME)
    assert verdict is ReadVerdict.ABSENT
    empty = _ok_state([])
    verdict2, _ = BrowserEffectObserver._persisted(empty, NAME)
    assert verdict2 is ReadVerdict.ABSENT


@pytest.mark.parametrize("status", [403, 500, 503])
def test_persisted_http_errors_are_unknown(status) -> None:
    state = _broken_state(status, f"HTTP_{status}")
    verdict, reason = BrowserEffectObserver._persisted(state, NAME)
    assert verdict is ReadVerdict.UNKNOWN
    assert reason == f"STATE_READ_HTTP_{status}:HTTP_{status}"


@pytest.mark.parametrize(
    ("error", "expected_fragment"),
    [
        ("CONNECTION_REFUSED", "CONNECTION_REFUSED"),
        ("TIMEOUT", "TIMEOUT"),
        ("CONNECTION_FAILURE", "CONNECTION_FAILURE"),
    ],
)
def test_persisted_network_failures_are_unknown(error, expected_fragment) -> None:
    state = _broken_state(0, error, raw="")
    verdict, reason = BrowserEffectObserver._persisted(state, NAME)
    assert verdict is ReadVerdict.UNKNOWN
    assert expected_fragment in reason


def test_persisted_non_json_is_unknown() -> None:
    state = _broken_state(200, "NON_JSON_RESPONSE", raw="<html>oops</html>")
    verdict, reason = BrowserEffectObserver._persisted(state, NAME)
    assert verdict is ReadVerdict.UNKNOWN
    assert "NON_JSON_RESPONSE" in reason


@pytest.mark.parametrize(
    ("payload", "expected_fragment"),
    [
        ({}, "MISSING_OK_FIELD"),
        ({"ok": False, "projects": []}, "OK_NOT_TRUE"),
        ({"ok": True}, "MISSING_OR_MALFORMED_PROJECTS"),
        ({"ok": True, "projects": "nope"}, "MISSING_OR_MALFORMED_PROJECTS"),
        ({"ok": True, "projects": ["not-a-dict"]}, "MISSING_OR_MALFORMED_PROJECTS"),
    ],
)
def test_persisted_malformed_payload_is_unknown(payload, expected_fragment) -> None:
    state = _broken_state(200, "", payload=payload)
    verdict, reason = BrowserEffectObserver._persisted(state, NAME)
    assert verdict is ReadVerdict.UNKNOWN
    assert expected_fragment in reason


# ---------------------------------------------------------------------------
# _classify()：UNKNOWN 不允许进入误判分类
# ---------------------------------------------------------------------------
UI_SIGNALS = [
    {"success_visible": True, "error_visible": False},
    {"success_visible": False, "error_visible": True},
    {"success_visible": False, "error_visible": False},
    {"success_visible": True, "error_visible": True},
]


@pytest.mark.parametrize("signals", UI_SIGNALS)
@pytest.mark.parametrize("navigated", [True, False])
def test_classify_unknown_persisted_never_misjudges(signals, navigated) -> None:
    status, classification, note = BrowserEffectObserver._classify(
        persisted=ReadVerdict.UNKNOWN,
        persisted_reason="STATE_READ_HTTP_500:HTTP_500",
        navigated=navigated,
        settle_ms=10.0,
        testid_presence={"project-name": True, "create-submit": True},
        **signals,
    )
    assert status == EffectStatus.UNKNOWN.value
    assert classification == "BUSINESS_STATE_READ_UNAVAILABLE"
    assert classification not in FORBIDDEN_WHEN_UNKNOWN
    assert "STATE_READ_HTTP_500" in note, "必须返回明确的观测失败原因"


def test_classify_unknown_persisted_note_names_failure() -> None:
    _status, _classification, note = BrowserEffectObserver._classify(
        persisted=ReadVerdict.UNKNOWN,
        persisted_reason="STATE_READ_HTTP_403:HTTP_403",
        success_visible=True,
        error_visible=False,
        navigated=True,
        settle_ms=5.0,
        testid_presence={"project-name": True, "create-submit": True},
    )
    assert "STATE_READ_HTTP_403" in note
    assert "读取不可用" in note or "回读不可用" in note


# ---------------------------------------------------------------------------
# 既有分类路径回归（PRESENT / ABSENT 语义保持不变）
# ---------------------------------------------------------------------------
def test_classify_present_success_is_not_triggered() -> None:
    status, classification, _ = BrowserEffectObserver._classify(
        persisted=ReadVerdict.PRESENT,
        persisted_reason="OK",
        success_visible=True,
        error_visible=False,
        navigated=True,
        settle_ms=10.0,
        testid_presence={"project-name": True, "create-submit": True},
    )
    assert status == EffectStatus.NOT_TRIGGERED.value
    assert classification == "OPERATION_OK"


def test_classify_absent_success_is_misleading() -> None:
    status, classification, _ = BrowserEffectObserver._classify(
        persisted=ReadVerdict.ABSENT,
        persisted_reason="OK",
        success_visible=True,
        error_visible=False,
        navigated=True,
        settle_ms=10.0,
        testid_presence={"project-name": True, "create-submit": True},
    )
    assert status == EffectStatus.CONFIRMED.value
    assert classification == "MISLEADING_SUCCESS_ON_FAILURE"


def test_classify_absent_silent_is_silent_ignore() -> None:
    status, classification, _ = BrowserEffectObserver._classify(
        persisted=ReadVerdict.ABSENT,
        persisted_reason="OK",
        success_visible=False,
        error_visible=False,
        navigated=False,
        settle_ms=10.0,
        testid_presence={"project-name": True, "create-submit": True},
    )
    assert status == EffectStatus.CONFIRMED.value
    assert classification == "SILENT_IGNORE"


def test_classify_absent_error_is_operation_failed() -> None:
    status, classification, _ = BrowserEffectObserver._classify(
        persisted=ReadVerdict.ABSENT,
        persisted_reason="OK",
        success_visible=False,
        error_visible=True,
        navigated=True,
        settle_ms=10.0,
        testid_presence={"project-name": True, "create-submit": True},
    )
    assert status == EffectStatus.CONFIRMED.value
    assert classification == "OPERATION_FAILED_ERROR_SURFACED"


# ---------------------------------------------------------------------------
# _request_json：真实 HTTP 行为（状态码 / 非 JSON / 网络失败不被吞掉）
# ---------------------------------------------------------------------------
def _make_server() -> tuple[ThreadingHTTPServer, threading.Thread]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args) -> None:  # noqa: D102
            return

        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/ok":
                body = json.dumps({"ok": True, "projects": [{"name": NAME}]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if self.path == "/forbidden":
                self.send_response(403)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if self.path == "/broken":
                self.send_response(500)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if self.path == "/html":
                body = b"<html>not json</html>"
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


@pytest.fixture(scope="module")
def http_server():
    server, thread = _make_server()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


def test_request_json_keeps_http_status(http_server) -> None:
    ok = _request_json("GET", http_server + "/ok")
    assert ok["http_status"] == 200
    assert ok["payload"]["ok"] is True
    assert ok["error"] == ""

    forbidden = _request_json("GET", http_server + "/forbidden")
    assert forbidden["http_status"] == 403, "HTTP 错误码不得被吞成 0"
    assert forbidden["error"] == "HTTP_403"

    broken = _request_json("GET", http_server + "/broken")
    assert broken["http_status"] == 500
    assert broken["error"] == "HTTP_500"

    html = _request_json("GET", http_server + "/html")
    assert html["http_status"] == 200
    assert html["error"] == "NON_JSON_RESPONSE"
    assert "not json" in html["raw"]


def test_request_json_connection_refused() -> None:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    read = _request_json("GET", f"http://127.0.0.1:{port}/ok", timeout=1.0)
    assert read["http_status"] == 0
    assert read["error"] == "CONNECTION_REFUSED"
    assert read["error_detail"], "必须保留原始错误原因"
