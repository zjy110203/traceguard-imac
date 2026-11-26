"""RUN-018 WP2：状态读取三态（PRESENT / ABSENT / UNKNOWN）的测试矩阵。

源码审查发现的真实缺陷：`_probe_state_consistency` 的 `if not direct.get("ok")` 与
`_readback` 的 `bool(payload.get("ok"))` 把**读取不可用**（403 / 5xx / 超时 / 连接失败 /
非 JSON / 缺字段）当作"记录不存在"，会误判 ACCEPTED_NOT_PERSISTED（CONFIRMED）。

本文件用本地 HTTP 服务精确控制状态读取通道，覆盖测试矩阵：

- 正常 200 + 存在 / 正常 200 + 不存在（有效权威读取才允许判定）
- 403 / 500 / 503 / 连接拒绝 / 连接中断 / 超时 / 非 JSON / 缺少 projects / 缺少 ok
- 读取期间服务重启（先不可用后恢复）→ 延迟读取最终恢复
- 清理失败 → UNKNOWN

连接拒绝用真实死端口（connect 层 ECONNREFUSED，无法在同一监听套接字上伪造），
连接中断用"接受连接后直接断开"（RemoteDisconnected）覆盖"读取期间服务重启"的
典型网络表现。真实 TeamFlow 集成测试见 tests/integration/test_observation_unknown_run018.py。
"""

from __future__ import annotations

import json
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "benchmark"))

from traceguard_benchmark import fault_effect as fe  # noqa: E402
from traceguard_benchmark.fault_effect import (  # noqa: E402
    EffectClass,
    EffectStatus,
    ReadVerdict,
    capture_baseline,
    new_probe_token,
    observe_fault_effect,
    _probe_persistence,
    _probe_state_consistency,
    _read_state_project,
    _readback,
    _state_list_contains,
)


class _State:
    def __init__(self) -> None:
        #: healthy / persist_fail / cleanup_fail / stale / state_403 / state_500 /
        #: state_503 / state_non_json / state_missing_ok / state_ok_false /
        #: state_missing_projects / state_malformed_projects / state_timeout /
        #: state_drop / state_flap
        self.mode = "healthy"
        self.projects: dict[str, dict[str, str]] = {}
        self.seq = 0
        self.last_create_ts = 0.0
        self.slow_s = 3.0
        self.flap_drop_first_n = 0
        self.flap_dropped = 0
        self.lock = threading.Lock()


def _make_server(state: _State) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args) -> None:  # noqa: D102 - 静默访问日志
            return

        def _send(self, code: int, payload: dict | str) -> None:
            body = (
                payload.encode()
                if isinstance(payload, str)
                else json.dumps(payload).encode()
            )
            self.send_response(code)
            self.send_header(
                "Content-Type",
                "text/html; charset=utf-8" if isinstance(payload, str) else "application/json",
            )
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _drop(self) -> None:
            """不发响应直接断开：客户端得到 RemoteDisconnected（模拟服务重启/中断）。"""
            self.close_connection = True
            try:
                self.connection.close()
            except OSError:
                pass

        def _state_fault(self) -> bool:
            """状态读取通道故障注入；返回 True 表示已处理（不再转发真实响应）。"""
            mode = state.mode
            if mode == "state_403":
                self._send(403, {"detail": "state read denied"})
                return True
            if mode == "state_500":
                self._send(500, {"detail": "state read failed"})
                return True
            if mode == "state_503":
                self._send(503, {"detail": "state unavailable"})
                return True
            if mode == "state_non_json":
                self._send(200, "<html>oops not json</html>")
                return True
            if mode == "state_missing_ok":
                with state.lock:
                    items = [{"key": k, "name": v["name"]} for k, v in state.projects.items()]
                self._send(200, {"count": len(items), "keys": [i["key"] for i in items], "projects": items})
                return True
            if mode == "state_ok_false":
                self._send(200, {"ok": False, "count": 0, "projects": []})
                return True
            if mode == "state_missing_projects":
                self._send(200, {"ok": True, "count": 0})
                return True
            if mode == "state_malformed_projects":
                self._send(200, {"ok": True, "count": 1, "projects": ["not-a-dict"]})
                return True
            if mode == "state_timeout":
                time.sleep(state.slow_s)
                return False
            if mode == "state_drop":
                self._drop()
                return True
            if mode == "state_flap":
                with state.lock:
                    if state.flap_dropped < state.flap_drop_first_n:
                        state.flap_dropped += 1
                        self._drop()
                        return True
                return False
            return False

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            try:
                body = json.loads(raw or b"{}")
            except json.JSONDecodeError:
                body = {}
            if self.path == "/api/test/probe-cleanup":
                if state.mode == "cleanup_fail":
                    self._send(500, {"detail": "cleanup unavailable"})
                    return
                token = str(body.get("token") or "")
                prefix = f"bp:{token}:"
                with state.lock:
                    doomed = [k for k, v in state.projects.items() if v["name"].startswith(prefix)]
                    for k in doomed:
                        state.projects.pop(k, None)
                self._send(
                    200,
                    {
                        "ok": True,
                        "deleted": len(doomed),
                        "remaining_matched": 0,
                        "after": len(state.projects),
                    },
                )
                return
            if self.path != "/api/projects":
                self._send(404, {"detail": "not found"})
                return
            name = str(body.get("name") or "")
            if not name:
                self._send(422, {"detail": "name required"})
                return
            with state.lock:
                state.seq += 1
                key = f"TF-{state.seq:06d}"
                if state.mode == "persist_fail":
                    self._send(201, {"ok": True, "persisted": False, "project": {"key": key}})
                    return
                state.projects[key] = {"name": name, "status": "ACTIVE"}
                state.last_create_ts = time.time()
                self._send(201, {"ok": True, "persisted": True, "project": {"key": key}})

        def do_GET(self) -> None:  # noqa: N802
            if self.path.startswith("/api/state/"):
                if self._state_fault():
                    return
            if self.path.startswith("/api/state/projects"):
                with state.lock:
                    items = [
                        {"key": k, "name": v["name"], "status": v["status"]}
                        for k, v in state.projects.items()
                    ]
                    stale = state.mode == "stale" and (time.time() - state.last_create_ts < 2.5)
                if stale and items:
                    items = items[:-1]
                self._send(
                    200,
                    {
                        "ok": True,
                        "count": len(items),
                        "keys": [i["key"] for i in items],
                        "projects": items,
                    },
                )
                return
            if self.path.startswith("/api/state/project/"):
                key = self.path.rsplit("/", 1)[1]
                with state.lock:
                    row = state.projects.get(key)
                self._send(200, {"ok": row is not None, "project": row})
                return
            if self.path == "/api/faults":
                self._send(200, {"available": [], "state": {"active": [], "params": {}}})
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


@pytest.fixture()
def dead_port() -> str:
    """真实连接拒绝：先拿一个空闲端口再释放，connect 必然 ECONNREFUSED。"""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    return f"http://127.0.0.1:{port}"


@pytest.fixture(autouse=True)
def _fast_retest_window(monkeypatch):
    """把延迟复测窗口缩短，保持测试节奏；生产常量默认 12s（TF-DB-004 滞后 9s）。"""
    monkeypatch.setattr(fe, "STATE_CONSISTENCY_RETEST_S", 4.0)


# ---------------------------------------------------------------------------
# _state_list_contains / _read_state_project：三态读取矩阵
# ---------------------------------------------------------------------------
UNAVAILABLE_MODES = [
    ("state_403", "HTTP_403"),
    ("state_500", "HTTP_500"),
    ("state_503", "HTTP_503"),
    ("state_non_json", "NON_JSON_RESPONSE"),
    ("state_missing_ok", "MISSING_OK_FIELD"),
    ("state_missing_projects", "MISSING_OR_MALFORMED_PROJECTS_FIELD"),
    ("state_malformed_projects", "MISSING_OR_MALFORMED_PROJECTS_FIELD"),
    ("state_timeout", "TIMEOUT"),
    ("state_drop", "CONNECTION_FAILURE"),
]


@pytest.mark.parametrize(("mode", "expected_reason"), UNAVAILABLE_MODES)
def test_state_list_read_unavailable_is_unknown(teamflow, mode, expected_reason) -> None:
    base, state = teamflow
    state.mode = mode
    read = _state_list_contains(base, "bp:x:name", "KEY1", timeout=1.0)
    assert read["verdict"] == ReadVerdict.UNKNOWN.value
    assert read["present"] is None
    assert read["reason"] == expected_reason
    assert "http_status" in read, "必须保留原始状态码"


def test_state_list_read_ok_false_is_unknown(teamflow) -> None:
    """列表接口 ok 不为 true：不是权威的"记录不存在"表示 → UNKNOWN。"""
    base, state = teamflow
    state.mode = "state_ok_false"
    read = _state_list_contains(base, "bp:x:name", "KEY1", timeout=2.0)
    assert read["verdict"] == ReadVerdict.UNKNOWN.value
    assert read["reason"] == "OK_NOT_TRUE"


def test_state_list_read_present_and_absent(teamflow) -> None:
    base, state = teamflow
    state.projects["K1"] = {"name": "bp:x:name", "status": "ACTIVE"}
    hit = _state_list_contains(base, "bp:x:name", "K1", timeout=2.0)
    assert hit["verdict"] == ReadVerdict.PRESENT.value
    assert hit["present"] is True
    miss = _state_list_contains(base, "bp:x:other", "K9", timeout=2.0)
    assert miss["verdict"] == ReadVerdict.ABSENT.value
    assert miss["present"] is False


@pytest.mark.parametrize(("mode", "expected_reason"), UNAVAILABLE_MODES)
def test_direct_state_read_unavailable_is_unknown(teamflow, mode, expected_reason) -> None:
    base, state = teamflow
    state.projects["K1"] = {"name": "bp:x:name", "status": "ACTIVE"}
    state.mode = mode
    read = _read_state_project(base, "K1", timeout=1.0)
    assert read["verdict"] == ReadVerdict.UNKNOWN.value
    assert read["present"] is None
    assert read["reason"] == expected_reason
    assert "http_status" in read, "必须保留原始状态码"


def test_direct_state_read_present_and_absent(teamflow) -> None:
    base, state = teamflow
    state.projects["K1"] = {"name": "bp:x:name", "status": "ACTIVE"}
    hit = _read_state_project(base, "K1", timeout=2.0)
    assert hit["verdict"] == ReadVerdict.PRESENT.value
    assert hit["ok"] is True
    miss = _read_state_project(base, "K-missing", timeout=2.0)
    assert miss["verdict"] == ReadVerdict.ABSENT.value
    assert miss["ok"] is False, "ok=false 是权威的不存在表示（HTTP 200）"


def test_connection_refused_is_unknown(dead_port) -> None:
    listed = _state_list_contains(dead_port, "bp:x:name", "K1", timeout=1.0)
    assert listed["verdict"] == ReadVerdict.UNKNOWN.value
    assert listed["reason"] == "CONNECTION_REFUSED"
    assert listed["http_status"] == 0
    direct = _read_state_project(dead_port, "K1", timeout=1.0)
    assert direct["verdict"] == ReadVerdict.UNKNOWN.value
    assert direct["reason"] == "CONNECTION_REFUSED"


# ---------------------------------------------------------------------------
# _probe_state_consistency：判定矩阵
# ---------------------------------------------------------------------------
def test_state_consistency_present_is_not_triggered(teamflow) -> None:
    base, state = teamflow
    token = new_probe_token()
    baseline = capture_baseline(base, ["TF-DB-003"], timeout=3, probe_token=token)
    result = _probe_state_consistency(base, 3, baseline, token)
    assert result.impacted is False
    assert result.classification == EffectClass.PERSISTED.value
    assert result.detail["reason"] == "READ_AFTER_WRITE_CONSISTENT"
    assert result.detail["immediate_read"]["verdict"] == ReadVerdict.PRESENT.value


def test_state_consistency_absent_proven_by_valid_direct_read(teamflow) -> None:
    """正常 200 + 不存在：创建谎报成功但未落库，有效 ABSENT 读取才允许判未落库。"""
    base, state = teamflow
    token = new_probe_token()
    baseline = capture_baseline(base, ["TF-DB-003"], timeout=3, probe_token=token)
    state.mode = "persist_fail"
    result = _probe_state_consistency(base, 3, baseline, token)
    assert result.impacted is True
    assert result.detail["reason"] == "ACCEPTED_NOT_PERSISTED"
    assert result.classification == EffectClass.API_ACCEPTED_NOT_PERSISTED.value
    assert result.detail["direct_read"]["verdict"] == ReadVerdict.ABSENT.value
    assert result.detail["direct_read"]["http_status"] == 200


def test_state_consistency_stale_then_recovered(teamflow) -> None:
    """延迟读取最终恢复：立即列表有效缺席、延迟复测有效 PRESENT → STALE_READ。"""
    base, state = teamflow
    token = new_probe_token()
    baseline = capture_baseline(base, ["TF-DB-003"], timeout=3, probe_token=token)
    state.mode = "stale"
    result = _probe_state_consistency(base, 3, baseline, token)
    assert result.impacted is True
    assert result.detail["reason"] == "STALE_STATE_READ"
    assert result.detail["eventually_consistent"] is True
    assert result.detail["eventual_read"]["present"] is True
    attempts = result.detail["eventual_read"]["attempts"]
    assert attempts, "延迟复测必须留下有效/不可用读取的审计记录"
    assert any(a["verdict"] == ReadVerdict.PRESENT.value for a in attempts)


@pytest.mark.parametrize(
    ("mode", "expected_reason"),
    [
        ("state_403", "STATE_READS_UNAVAILABLE"),
        ("state_500", "STATE_READS_UNAVAILABLE"),
        ("state_503", "STATE_READS_UNAVAILABLE"),
        ("state_non_json", "STATE_READS_UNAVAILABLE"),
        ("state_missing_ok", "STATE_READS_UNAVAILABLE"),
        ("state_missing_projects", "STATE_READS_UNAVAILABLE"),
        ("state_timeout", "STATE_READS_UNAVAILABLE"),
        ("state_drop", "STATE_READS_UNAVAILABLE"),
    ],
)
def test_state_consistency_unavailable_reads_are_unknown(teamflow, mode, expected_reason) -> None:
    """核心回归：读取不可用不得判 ACCEPTED_NOT_PERSISTED / STALE_STATE_READ。"""
    base, state = teamflow
    token = new_probe_token()
    baseline = capture_baseline(base, ["TF-DB-003"], timeout=3, probe_token=token)
    state.mode = mode
    timeout = 1.0 if mode == "state_timeout" else 3.0
    result = _probe_state_consistency(base, timeout, baseline, token)
    assert result.impacted is None, "读取不可用不是业务受影响的证据"
    assert result.detail["reason"] == expected_reason
    assert result.classification == EffectClass.STATE_READ_UNAVAILABLE.value
    assert "observation_failure" in result.detail, "必须返回明确的观测失败原因"
    assert result.detail["immediate_read"]["reason"] not in ("OK", "")
    assert result.detail["direct_read"]["reason"] not in ("OK", "")


def test_state_consistency_restart_recovery_is_unknown(teamflow, monkeypatch) -> None:
    """读取期间服务重启后恢复：延迟读取最终可见，但立即可见性未测到 → UNKNOWN。"""
    base, state = teamflow
    monkeypatch.setattr(fe, "STATE_CONSISTENCY_RETEST_S", 6.0)
    token = new_probe_token()
    baseline = capture_baseline(base, ["TF-DB-003"], timeout=3, probe_token=token)
    state.mode = "state_flap"
    state.flap_drop_first_n = 4  # 立即读 + 直接读 + 前两次复测不可用，随后恢复
    result = _probe_state_consistency(base, 3, baseline, token)
    assert result.impacted is None
    assert result.detail["reason"] == "STATE_READS_UNAVAILABLE"
    assert result.detail["eventual_read"]["present"] is True, "恢复后的有效读取必须被记录为最终可见"
    assert result.detail["eventual_read"]["attempts"], "不可用读取与有效读取都要留痕"


def test_state_consistency_cleanup_failure_is_unknown(teamflow) -> None:
    base, state = teamflow
    token = new_probe_token()
    baseline = capture_baseline(base, ["TF-DB-003"], timeout=3, probe_token=token)
    state.mode = "cleanup_fail"
    result = _probe_state_consistency(base, 3, baseline, token)
    assert result.impacted is None
    assert result.classification == EffectClass.CLEANUP_FAILED.value
    assert result.detail["reason"] == "PROBE_CLEANUP_FAILED"
    assert result.detail["requires_environment_reset"] is True


def test_state_consistency_connection_refused_is_unknown(teamflow, dead_port) -> None:
    """连接拒绝：探针无法完成读写与清理 → UNKNOWN（不得判业务受影响/未受影响）。"""
    base, _state = teamflow
    token = new_probe_token()
    baseline = capture_baseline(base, ["TF-DB-003"], timeout=3, probe_token=token)
    result = _probe_state_consistency(dead_port, 2, baseline, token)
    assert result.impacted is None
    assert result.detail["reason"] in {
        "PROBE_CLEANUP_FAILED",
        "PROBE_CREATE_FAILED",
        "STATE_READS_UNAVAILABLE",
    }


# ---------------------------------------------------------------------------
# _readback 三态：persistence 探针同样不得把读取失败当未落库
# ---------------------------------------------------------------------------
def test_readback_tristate(teamflow) -> None:
    base, state = teamflow
    state.projects["K1"] = {"name": "bp:x:name", "status": "ACTIVE"}
    ok_read = _readback(base, "bp:x:name", "K1", timeout=2)
    assert ok_read["persisted"] is True
    absent_read = _readback(base, "bp:x:name", "K-missing", timeout=2)
    assert absent_read["persisted"] is False
    state.mode = "state_500"
    unknown_read = _readback(base, "bp:x:name", "K1", timeout=2)
    assert unknown_read["persisted"] is None
    assert unknown_read["verdict"] == ReadVerdict.UNKNOWN.value
    assert unknown_read["http_status"] == 500


def test_persistence_probe_read_unavailable_is_unknown(teamflow) -> None:
    """回归：状态读取 500 时 persistence 探针不得判 ACCEPTED_NOT_PERSISTED。"""
    base, state = teamflow
    token = new_probe_token()
    baseline = capture_baseline(base, ["TF-DB-001"], timeout=3, probe_token=token)
    assert baseline["kinds"]["persistence"]["healthy"] is True
    state.mode = "state_500"
    result = _probe_persistence(base, 3, baseline, token)
    assert result.impacted is None
    assert result.classification == EffectClass.STATE_READ_UNAVAILABLE.value
    assert result.detail["reason"] == "READ_UNAVAILABLE_NO_CONCLUSIVE_ATTEMPT"
    outcomes = [a["outcome"] for a in result.detail["attempts"]]
    assert outcomes and all(o == "READ_UNAVAILABLE" for o in outcomes)


def test_observe_effect_keeps_raw_status_codes(teamflow) -> None:
    """完整原始状态码与错误原因必须进入样本（审计要求）。"""
    base, state = teamflow
    token = new_probe_token()
    baseline = capture_baseline(base, ["TF-DB-003"], timeout=3, probe_token=token)
    state.mode = "state_500"
    result = observe_fault_effect(base, ["TF-DB-003"], baseline=baseline, timeout=3)
    assert result["fault_effect_status"] == EffectStatus.UNKNOWN.value
    probe = result["fault_effect_probes"][0]
    assert probe["detail"]["immediate_read"]["http_status"] == 500
    assert probe["detail"]["direct_read"]["http_status"] == 500
    assert probe["detail"]["immediate_read"]["reason"] == "HTTP_500"
    assert probe["detail"]["observation_failure"]
