"""RUN-017 WP2：Fault Effect 独立观测的专项测试（修正后契约）。

> 本文件由 RUN-016 版本升级而来：RUN-017 把判定契约从"单次探测 + 状态码"改成
> "故障前健康基线 + 故障后独立观测 + 分类"，因此断言也随之收紧（不是放宽）。
> 原有用例（5xx 确认 / 落库失败 / 状态滞后 / UI-only UNKNOWN）全部保留，
> 并新增 403 授权基线、清理失败、快速 5xx、缺基线等回归点。

用本地 HTTP 服务模拟 TeamFlow 的业务响应，验证：

- 判定**只来自独立探针**（接口层面没有 verdict 入参）；
- 必须**先采故障前基线**，再观测故障后行为；缺基线 = UNKNOWN；
- 403 只有在"授权基线证明授权操作本来可用"时才判 CONFIRMED；
- 5xx / 真超时算业务受影响；快速 4xx 校验拒绝不算；
- persistence 探针按 key 回读、只清理自己的数据、清理失败 → UNKNOWN；
- 重名静默失败（TF-DB-002）可被独立观测；
- 状态滞后用 5+5 采样与 p50/p95 比较；
- 纯 UI / 定位器类交给浏览器观测 → 本模块 UNKNOWN（不推断）。
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
    EffectClass,
    EffectStatus,
    capture_baseline,
    diff_business_state,
    observe_fault_effect,
    snapshot_business_state,
)


class _State:
    def __init__(self) -> None:
        #: healthy / api_500 / api_503 / authz_403 / authz_422 / persist_fail /
        #: dup_fail / lag / timeout / cleanup_fail / canary_500
        self.mode = "healthy"
        self.projects: dict[str, dict[str, str]] = {}
        self.seq = 0
        self.lag_s = 1.7
        self.last_create_ts = 0.0
        self.lock = threading.Lock()


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
            if state.mode == "api_500":
                self._send(500, {"detail": "simulated"})
                return
            if state.mode == "api_503":
                self._send(503, {"detail": "simulated"})
                return
            if state.mode == "timeout":
                time.sleep(5.0)
            name = str(body.get("name") or "")
            if not name:
                if state.mode in ("authz_403", "authz_ambiguous"):
                    self._send(403, {"detail": "workspace quota exceeded"})
                    return
                self._send(422, {"detail": "name required"})
                return
            if state.mode == "canary_500":
                self._send(500, {"detail": "canary broken"})
                return
            if state.mode == "authz_403":
                self._send(403, {"detail": "workspace quota exceeded"})
                return
            if state.mode in ("authz_422", "authz_ambiguous"):
                self._send(422, {"detail": "unexpected validation"})
                return
            with state.lock:
                state.seq += 1
                key = f"TF-{state.seq:06d}"
                if state.mode == "persist_fail":
                    self._send(201, {"ok": True, "persisted": False, "project": {"key": key}})
                    return
                if state.mode == "dup_fail" and any(
                    v["name"] == name for v in state.projects.values()
                ):
                    self._send(201, {"ok": True, "persisted": False, "project": {"key": key}})
                    return
                state.projects[key] = {"name": name, "status": "ACTIVE"}
                state.last_create_ts = time.time()
                self._send(201, {"ok": True, "persisted": True, "project": {"key": key}})

        def do_GET(self) -> None:  # noqa: N802
            if self.path.startswith("/api/projects"):
                if state.mode == "api_500":
                    self._send(500, {"detail": "simulated"})
                    return
                if state.mode == "api_503":
                    self._send(503, {"detail": "simulated"})
                    return
                if state.mode == "timeout":
                    time.sleep(5.0)
                if state.mode == "read_lag":
                    time.sleep(state.lag_s)
                with state.lock:
                    items = [{"key": k, "name": v["name"]} for k, v in state.projects.items()]
                self._send(200, {"ok": True, "items": items})
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
                # "ok": True 对齐真实 TeamFlow /api/state/projects 契约（该接口恒返回 ok）；
                # RUN-018 WP2 起状态读取三态要求 ok=true 才是有效权威读取。
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


def _observe(base: str, state: _State, faults: list[str], *, mode: str, timeout: float = 3):
    """标准两段式：健康基线 → 切换故障 → 独立观测。"""
    token = "testtoken1234"
    baseline = capture_baseline(base, faults, timeout=timeout, probe_token=token)
    assert baseline["kinds"], "基线必须包含至少一个探针"
    state.mode = mode
    return observe_fault_effect(base, faults, baseline=baseline, timeout=timeout)


def test_api_fault_is_confirmed_by_independent_probe(teamflow) -> None:
    base, state = teamflow
    result = _observe(base, state, ["TF-API-001"], mode="api_500")
    assert result["fault_effect_status"] == EffectStatus.CONFIRMED.value
    assert result["fault_effect_classification"] == EffectClass.HTTP_5XX.value
    assert result["fault_effect_evidence_refs"]
    assert result["benchmark_schema_version"] == BENCHMARK_SCHEMA_VERSION
    assert result["fault_effect_probes"][0]["detail"]["statuses"] == [500]


def test_healthy_business_marks_not_triggered(teamflow) -> None:
    base, state = teamflow
    result = _observe(base, state, ["TF-API-001"], mode="healthy")
    assert result["fault_effect_status"] == EffectStatus.NOT_TRIGGERED.value
    assert result["fault_effect_classification"] == EffectClass.AUTHORIZED_OPERATION_OK.value
    assert [p["name"] for p in state.projects.values() if p["name"].startswith("bp:")] == []


def test_403_with_authorization_baseline_is_confirmed(teamflow) -> None:
    """健康基线里授权 canary=201；故障后授权操作被 403 拒绝 → 真实业务影响。"""
    base, state = teamflow
    result = _observe(base, state, ["TF-SEC-001"], mode="authz_403")
    assert result["fault_effect_status"] == EffectStatus.CONFIRMED.value
    assert result["fault_effect_classification"] == EffectClass.AUTHZ_DENIED_WITH_BASELINE.value
    detail = result["fault_effect_probes"][0]["detail"]
    assert detail["baseline"]["authorized_status"] == 201
    assert detail["statuses"] == [403]


def test_403_without_conclusive_authorization_is_unknown(teamflow) -> None:
    """基线可用、故障后合法请求被 422 拒绝：无法归因 → UNKNOWN（不得判受影响）。"""
    base, state = teamflow
    result = _observe(base, state, ["TF-SEC-001"], mode="authz_ambiguous")
    assert result["fault_effect_status"] == EffectStatus.UNKNOWN.value
    assert result["fault_effect_classification"] == EffectClass.CREATE_REJECTED.value


def test_unhealthy_baseline_blocks_attribution(teamflow) -> None:
    """健康基线自己就是 500：后续 500 不能归因于注入故障。"""
    base, state = teamflow
    state.mode = "canary_500"
    baseline = capture_baseline(base, ["TF-API-001"], timeout=3)
    assert baseline["kinds"]["api_error"]["healthy"] is False
    state.mode = "api_500"
    result = observe_fault_effect(base, ["TF-API-001"], baseline=baseline, timeout=3)
    assert result["fault_effect_status"] == EffectStatus.UNKNOWN.value
    assert result["fault_effect_classification"] == EffectClass.BASELINE_UNHEALTHY.value


def test_missing_baseline_is_unknown(teamflow) -> None:
    base, state = teamflow
    state.mode = "api_500"
    result = observe_fault_effect(base, ["TF-API-001"], timeout=3)
    assert result["fault_effect_status"] == EffectStatus.UNKNOWN.value
    assert result["fault_effect_classification"] == EffectClass.BASELINE_MISSING.value


def test_persistence_fault_detected_by_state_check(teamflow) -> None:
    base, state = teamflow
    result = _observe(base, state, ["TF-DB-001"], mode="persist_fail")
    assert result["fault_effect_status"] == EffectStatus.CONFIRMED.value
    probe = result["fault_effect_probes"][0]
    assert probe["detail"]["reason"] == EffectClass.API_ACCEPTED_NOT_PERSISTED.value
    assert probe["detail"]["side_effect"] is True
    assert probe["detail"]["cleanup"]["ok"] is True
    # 探针自己的数据已清理干净，不污染业务表
    leftovers = [v["name"] for v in state.projects.values() if v["name"].startswith("bp:")]
    assert leftovers == []


def test_duplicate_silent_failure_detected(teamflow) -> None:
    base, state = teamflow
    result = _observe(base, state, ["TF-DB-002"], mode="dup_fail")
    assert result["fault_effect_status"] == EffectStatus.CONFIRMED.value
    assert result["fault_effect_classification"] == EffectClass.DUPLICATE_NOT_PERSISTED.value


def test_cleanup_failure_is_unknown_and_requires_reset(teamflow) -> None:
    base, state = teamflow
    result = _observe(base, state, ["TF-DB-001"], mode="cleanup_fail")
    assert result["fault_effect_status"] == EffectStatus.UNKNOWN.value
    assert result["fault_effect_classification"] == EffectClass.CLEANUP_FAILED.value
    assert result["fault_effect_requires_environment_reset"] is True


def test_read_latency_is_measured_not_inferred(teamflow) -> None:
    base, state = teamflow
    result = _observe(base, state, ["TF-TIME-002"], mode="read_lag", timeout=8)
    assert result["fault_effect_status"] == EffectStatus.CONFIRMED.value
    probe = result["fault_effect_probes"][0]
    assert probe["kind"] == "read_latency"
    assert probe["detail"]["samples"] == 5
    assert probe["detail"]["comparison"]["delta_p95_ms"] >= 800


def test_stale_state_read_is_confirmed(teamflow) -> None:
    """TF-DB-003/004 是"读到的值过期"，不是"响应变慢"：必须用一致性探针。"""
    base, state = teamflow
    result = _observe(base, state, ["TF-DB-004"], mode="stale", timeout=8)
    assert result["fault_effect_status"] == EffectStatus.CONFIRMED.value
    probe = result["fault_effect_probes"][0]
    assert probe["kind"] == "state_consistency"
    assert probe["detail"]["reason"] == "STALE_STATE_READ"
    assert probe["detail"]["immediate_read"]["present"] is False
    assert probe["detail"]["direct_read"]["ok"] is True


def test_timeout_is_confirmed_as_unavailable(teamflow) -> None:
    base, state = teamflow
    result = _observe(base, state, ["TF-NET-002"], mode="timeout", timeout=3)
    assert result["fault_effect_status"] == EffectStatus.CONFIRMED.value
    assert result["fault_effect_probes"][0]["kind"] == "api_timeout"
    assert result["fault_effect_classification"] == EffectClass.TIMEOUT.value


def test_fast_5xx_on_timeout_probe_is_also_confirmed(teamflow) -> None:
    """RUN-016 缺陷：快速 503 曾被判成"没影响"。"""
    base, state = teamflow
    result = _observe(base, state, ["TF-NET-001"], mode="api_503", timeout=3)
    assert result["fault_effect_status"] == EffectStatus.CONFIRMED.value
    assert result["fault_effect_probes"][0]["classification"] == EffectClass.HTTP_5XX.value


def test_ui_only_fault_is_unknown(teamflow) -> None:
    base, _state = teamflow
    result = observe_fault_effect(base, ["TF-LOC-001"], timeout=3)
    assert result["fault_effect_status"] == EffectStatus.UNKNOWN.value
    assert result["fault_effect_source"] == "BROWSER_EFFECT_OBSERVER_PENDING"
    assert result["fault_effect_evidence_refs"] == []


def test_observation_never_accepts_verdict(teamflow) -> None:
    """接口层面就不存在 verdict 入参——效果判定无法被测试结论污染。"""
    base, state = teamflow
    state.mode = "api_500"
    with pytest.raises(TypeError):
        observe_fault_effect(base, ["TF-API-001"], verdict="PASSED")  # type: ignore[call-arg]
    baseline = capture_baseline(base, ["TF-API-001"], timeout=3)
    result = observe_fault_effect(base, ["TF-API-001"], baseline=baseline, timeout=3)
    assert "verdict" not in json.dumps(result["fault_effect_probes"])


def test_business_state_snapshot_and_diff(teamflow) -> None:
    base, state = teamflow
    before = snapshot_business_state(base)
    state.projects["TF-1"] = {"name": "a", "status": "ACTIVE"}
    state.projects["TF-2"] = {"name": "b", "status": "ACTIVE"}
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
    ok = policy.check("http://127.0.0.1:8111/api/projects")
    assert ok.allowed is True
    rel = policy.resolve("/api/ground-truth/FALSE-PASS", "http://127.0.0.1:8111")
    assert rel.allowed is False and rel.reason == "GROUND_TRUTH_PATH_BLOCKED"
