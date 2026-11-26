"""RUN-016 WP3：故障效果（Fault Effect）的**独立观测**。

问题（RUN-015 及之前）：

    fault_effect_status = CONFIRMED if activation and verdict in {FAILED, NOT_EVALUATED}

这等于把"测试自己的判断结果"当成"故障是否影响业务"的证据，形成循环论证：
测试判 FAILED 就说故障生效 → 再拿"故障生效 + 测试 PASSED"去算 False Pass。

本模块把四个概念彻底分开（对应指令 §3.1）：

1. **Fault Activation**：故障注入器是否真的激活。来源 = TeamFlow `/api/faults` 快照。
2. **Fault Effect**：故障是否真的影响了被测业务。来源 = 本模块的独立探针
   （直接打 TeamFlow 业务 API / 读业务状态），**不读 Run 的 verdict**。
3. **Test Verdict**：TraceGuard 自己的判定，只作为"被观测对象"记录。
4. **Ground Truth**：离线评测标签，评测端使用，不进入诊断 Prompt。

判定规则（fail-closed）：

- 探针确认业务受影响 → `CONFIRMED`
- 探针确认业务**未**受影响（故障激活但业务正常）→ `NOT_TRIGGERED`
- 没有可用的独立探针 / 探针结果不确定 → `UNKNOWN`

`UNKNOWN` 不构成 False Pass 证据。纯 UI / 定位器类故障在无人为观测（如独立浏览器
录屏）时只能给 `UNKNOWN`——这是有意为之，不允许用 Run verdict 补这个洞。
"""

from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

#: 样本结构版本：v1 = 历史（效果可由 verdict 推断）；v2 = 独立观测（本模块）
BENCHMARK_SCHEMA_VERSION = "benchmark-fault-v2"


class EffectStatus(str, Enum):
    CONFIRMED = "CONFIRMED"
    NOT_TRIGGERED = "NOT_TRIGGERED"
    UNKNOWN = "UNKNOWN"


#: 每类故障用哪些独立探针。缺少映射 = 无独立证据能力 → UNKNOWN。
FAULT_EFFECT_PROBES: dict[str, tuple[str, ...]] = {
    # API 异常：用非法载荷触发 500/503/403，不会写入业务数据
    "TF-API-001": ("api_error",),
    "TF-API-002": ("api_error",),
    "TF-API-003": ("api_error",),
    "TF-API-004": ("api_error",),
    "TF-SEC-001": ("api_error",),
    # 网络超时：短超时探测业务 API 是否可用
    "TF-NET-001": ("api_timeout",),
    "TF-NET-002": ("api_timeout",),
    # 持久化异常：真实写入后独立查询业务状态（读的是 TeamFlow 自己的状态接口）
    "TF-DB-001": ("persistence",),
    "TF-DB-002": ("persistence",),
    "TF-DB-005": ("persistence",),
    "TF-FLAKY-001": ("persistence",),
    "TF-FLAKY-002": ("api_error",),
    # 最终一致性滞后：直接测状态查询耗时
    "TF-DB-003": ("state_lag",),
    "TF-DB-004": ("state_lag",),
    "TF-TIME-001": ("state_lag",),
    "TF-TIME-002": ("state_lag",),
    "TF-TIME-003": ("persistence",),
    # 复合故障：任一底层机制被独立观测到即确认
    "TF-COMP-001": ("api_error",),
    "TF-COMP-002": ("persistence", "state_lag"),
    # 纯 UI / 定位器类：没有独立业务探针 → 只能是 UNKNOWN（如实标注）
    "TF-UI-001": (),
    "TF-UI-002": (),
    "TF-UI-003": (),
    "TF-UI-004": (),
    "TF-LOC-001": (),
    "TF-LOC-002": (),
    "TF-COMP-003": (),
}


@dataclass
class ProbeResult:
    kind: str
    #: True=业务受影响；False=业务正常；None=无法判定
    impacted: bool | None
    detail: dict[str, Any] = field(default_factory=dict)
    elapsed_ms: float = 0.0
    at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "impacted": self.impacted,
            "detail": self.detail,
            "elapsed_ms": round(self.elapsed_ms, 1),
            "at": self.at,
        }


def _http(
    method: str,
    url: str,
    *,
    body: dict[str, Any] | None = None,
    timeout: float = 5.0,
) -> tuple[int, str, float]:
    """最小 HTTP 客户端（不引入 requests 依赖），返回 (status, text, elapsed_ms)。"""
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"} if data is not None else {}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - 受控内网地址
            return resp.status, resp.read(4096).decode("utf-8", "replace"), (
                time.perf_counter() - started
            ) * 1000
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(4096).decode("utf-8", "replace"), (
            time.perf_counter() - started
        ) * 1000
    except (urllib.error.URLError, TimeoutError) as exc:
        return 0, f"{type(exc).__name__}: {exc}", (time.perf_counter() - started) * 1000


def _probe_api_error(base_url: str, timeout: float) -> ProbeResult:
    """用**不会写入数据**的非法载荷探测创建接口是否仍在正常拒绝。

    - 故障激活（500/503/403）→ impacted=True
    - 正常校验拒绝（400/422）→ impacted=False
    - 201/200 → 服务端接受了非法载荷，属异常，记 impacted=None
    """
    url = base_url.rstrip("/") + "/api/projects"
    status, text, ms = _http("POST", url, body={}, timeout=timeout)
    detail = {"request": "POST /api/projects {}", "status": status, "body": text[:200]}
    if status in (500, 502, 503, 504, 403):
        return ProbeResult("api_error", True, detail, ms, time.time())
    if status in (400, 409, 422):
        return ProbeResult("api_error", False, detail, ms, time.time())
    return ProbeResult("api_error", None, detail, ms, time.time())


def _probe_api_timeout(base_url: str, timeout: float) -> ProbeResult:
    """短超时探测创建接口：超时即业务写入链路不可用。

    用空载荷（健康时会被 4xx 快速拒绝，不写数据）把"网络延迟"与"业务写入"分开测量。
    """
    url = base_url.rstrip("/") + "/api/projects"
    limit = max(0.5, min(timeout, 2.5))
    status, text, ms = _http("POST", url, body={}, timeout=limit)
    detail = {"request": "POST /api/projects {}", "status": status, "body": text[:200]}
    if status == 0:
        return ProbeResult("api_timeout", True, detail, ms, time.time())
    return ProbeResult("api_timeout", False, detail, ms, time.time())


def _probe_persistence(base_url: str, timeout: float) -> ProbeResult:
    """真实创建 → 独立查询业务状态是否落库。

    副作用：无故障时会真实创建一个探针项目（名称带唯一后缀）。该请求发生在被测 Run
    结束之后，不影响该 Run 的判定；副作用在样本里显式记录 `side_effect=true`。
    """
    name = f"r16-probe-{uuid.uuid4().hex[:10]}"
    create_url = base_url.rstrip("/") + "/api/projects"
    status, text, ms = _http(
        "POST", create_url, body={"name": name, "workspace": "default"}, timeout=timeout
    )
    detail: dict[str, Any] = {
        "request": "POST /api/projects",
        "status": status,
        "name": name,
        "side_effect": status in (200, 201),
    }
    if status in (500, 502, 503, 504):
        return ProbeResult("persistence", True, detail, ms, time.time())
    state_url = base_url.rstrip("/") + "/api/state/projects?workspace=default"
    s_status, s_text, s_ms = _http("GET", state_url, timeout=timeout)
    detail["state_status"] = s_status
    detail["state_body"] = s_text[:400]
    if s_status != 200:
        detail["reason"] = "STATE_QUERY_UNAVAILABLE"
        return ProbeResult("persistence", None, detail, ms + s_ms, time.time())
    present = name in s_text
    if status in (200, 201) and not present:
        detail["reason"] = "API_ACCEPTED_BUT_NOT_PERSISTED"
        return ProbeResult("persistence", True, detail, ms + s_ms, time.time())
    if status in (200, 201) and present:
        return ProbeResult("persistence", False, detail, ms + s_ms, time.time())
    detail["reason"] = "CREATE_REJECTED"
    return ProbeResult("persistence", None, detail, ms + s_ms, time.time())


def _probe_state_lag(base_url: str, timeout: float) -> ProbeResult:
    """直接测独立状态查询耗时；>=1500ms 视为业务读取受显著影响。"""
    url = base_url.rstrip("/") + "/api/state/projects?workspace=default"
    status, text, ms = _http("GET", url, timeout=max(timeout, 12.0))
    detail = {
        "request": "GET /api/state/projects",
        "status": status,
        "elapsed_ms": round(ms, 1),
        "threshold_ms": 1500,
    }
    impacted = ms >= 1500 if status == 200 else None
    return ProbeResult("state_lag", impacted, detail, ms, time.time())


_PROBES = {
    "api_error": _probe_api_error,
    "api_timeout": _probe_api_timeout,
    "persistence": _probe_persistence,
    "state_lag": _probe_state_lag,
}


def _evidence_id(result: ProbeResult) -> str:
    payload = json.dumps(result.to_dict(), sort_keys=True, ensure_ascii=False)
    return "tfp_" + hashlib.sha256(payload.encode()).hexdigest()[:16]


def probe_kinds_for(fault_ids: list[str] | tuple[str, ...]) -> list[str]:
    kinds: list[str] = []
    for fault in fault_ids:
        for kind in FAULT_EFFECT_PROBES.get(str(fault), ()):
            if kind not in kinds:
                kinds.append(kind)
    return kinds


def observe_fault_effect(
    base_url: str,
    fault_ids: list[str] | tuple[str, ...],
    *,
    timeout: float = 6.0,
    observed_at: float | None = None,
) -> dict[str, Any]:
    """独立观测故障对业务的影响；返回可直接写入样本的字段集合。"""
    kinds = probe_kinds_for(fault_ids)
    at = time.time() if observed_at is None else observed_at
    if not kinds:
        return {
            "fault_effect_status": EffectStatus.UNKNOWN.value,
            "fault_effect_source": "NO_INDEPENDENT_PROBE",
            "fault_effect_evidence_refs": [],
            "fault_effect_timestamp": at,
            "fault_effect_probes": [],
            "fault_effect_note": (
                "纯 UI/定位器类故障缺少独立业务探针；不得用 Run verdict 代替效果证据"
            ),
            "benchmark_schema_version": BENCHMARK_SCHEMA_VERSION,
        }
    results = [_PROBES[kind](base_url, timeout) for kind in kinds]
    impacted = [r.impacted for r in results]
    if any(x is True for x in impacted):
        status = EffectStatus.CONFIRMED
    elif impacted and all(x is False for x in impacted):
        status = EffectStatus.NOT_TRIGGERED
    else:
        status = EffectStatus.UNKNOWN
    return {
        "fault_effect_status": status.value,
        "fault_effect_source": "INDEPENDENT_TEAMFLOW_PROBE:" + ",".join(kinds),
        "fault_effect_evidence_refs": [_evidence_id(r) for r in results],
        "fault_effect_timestamp": at,
        "fault_effect_probes": [r.to_dict() for r in results],
        "fault_effect_note": "",
        "benchmark_schema_version": BENCHMARK_SCHEMA_VERSION,
    }


def snapshot_business_state(base_url: str, *, timeout: float = 5.0) -> dict[str, Any]:
    """业务状态快照（独立于 TraceGuard 的 Run/Evidence），可用于前后差异对比。"""
    url = base_url.rstrip("/") + "/api/state/projects?workspace=default"
    status, text, ms = _http("GET", url, timeout=timeout)
    try:
        state = json.loads(text) if status == 200 else {}
    except json.JSONDecodeError:
        state = {}
    return {
        "source": "teamflow:/api/state/projects",
        "http_status": status,
        "state": state,
        "elapsed_ms": round(ms, 1),
        "at": time.time(),
    }


def diff_business_state(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """比较两次业务状态快照，输出可审计的差异（不做业务语义假设）。"""
    b = before.get("state") or {}
    a = after.get("state") or {}

    def _count(state: dict[str, Any]) -> int | None:
        value = state.get("count")
        return int(value) if isinstance(value, int) else None

    before_count, after_count = _count(b), _count(a)
    changed = before_count != after_count and before_count is not None and after_count is not None
    return {
        "before_count": before_count,
        "after_count": after_count,
        "count_changed": changed,
        "delta": (after_count - before_count) if changed else 0,
        "before_http": before.get("http_status"),
        "after_http": after.get("http_status"),
        "at": time.time(),
    }



__all__ = [
    "BENCHMARK_SCHEMA_VERSION",
    "EffectStatus",
    "FAULT_EFFECT_PROBES",
    "diff_business_state",
    "observe_fault_effect",
    "probe_kinds_for",
    "snapshot_business_state",
]
