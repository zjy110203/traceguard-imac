"""RUN-017 WP2：故障效果（Fault Effect）的**独立观测**（v2 修正版）。

四个概念严格分开（对应指令 §4.1–§4.5）：

1. **Fault Activation**：故障注入器是否真的激活。来源 = TeamFlow `/api/faults` 快照。
2. **Fault Effect**：故障是否真的影响了被测业务。来源 = 本模块的独立探针，
   **先取故障前健康基线，再在故障发生后独立观测**，不读 Run verdict。
3. **Test Verdict**：TraceGuard 自己的判定，只作为被观测对象记录。
4. **Ground Truth**：离线评测标签，评测端读取，不进入诊断/探针。

RUN-016 版本的分类缺陷（本轮修复）：

- `api_error` 把 403 直接当"业务受影响"——权限策略变化会被误判成故障影响；
- `api_timeout` 只有 `status=0` 才算受影响——快速 5xx 反而被判成"没影响"；
- `persistence` 用 name 去 grep `/api/state/projects` 的整段响应文本，而该接口
  只返回 keys → **健康环境也会被判成"未持久化"**，并真实留下 `r16-probe-*` 数据；
- `state_lag` 用单次采样 + 固定 1500ms 阈值，无基线、无分位数；
- 纯 UI / 定位器类没有独立观测 → 只能 UNKNOWN（本轮由 `browser_effect` 补上）。

判定规则（fail-closed）：

- 探针有**故障前健康基线**且观测到业务受影响 → `CONFIRMED`
- 基线健康且观测到业务正常 → `NOT_TRIGGERED`
- 缺基线 / 基线不健康 / 结果不确定 / 清理失败 → `UNKNOWN`

`UNKNOWN` 不构成 False Pass 证据，也不允许用 Run verdict 补洞。
"""

from __future__ import annotations

import hashlib
import json
import statistics
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

#: 样本结构版本：v1 = 历史（效果可由 verdict 推断）；v2 = 独立观测（本模块）
BENCHMARK_SCHEMA_VERSION = "benchmark-fault-v2"

#: 探针专用 Workspace：探针只读写这里，不触碰 `default` 等真实业务空间。
PROBE_WORKSPACE = "tg-benchmark-probe"

#: 探针项目名前缀（配合随机 token 做归属标识，清理只删自己创建的行）
PROBE_NAME_TAG = "bp"

#: 状态滞后探针的工程参考阈值（不是产品 SLA，不得对外宣称）
STATE_LAG_REFERENCE_MS = 1500.0


class EffectStatus(str, Enum):
    CONFIRMED = "CONFIRMED"
    NOT_TRIGGERED = "NOT_TRIGGERED"
    UNKNOWN = "UNKNOWN"


class ReadVerdict(str, Enum):
    """独立状态读取的三态裁决（RUN-018 WP2）。

    只有**有效的权威读取**（HTTP 200 + ok=true + 合法响应结构）才允许给出
    PRESENT / ABSENT；403 / 5xx / 超时 / 连接失败 / 非法 JSON / 缺少必要字段
    一律 UNKNOWN——读取不可用绝不能被当作"记录不存在"的证据。
    """

    PRESENT = "PRESENT"
    ABSENT = "ABSENT"
    UNKNOWN = "UNKNOWN"


class EffectClass(str, Enum):
    """探针结论的机器可读分类（写入样本，便于审计与统计）。"""

    HTTP_5XX = "HTTP_5XX"
    VALIDATION_REJECT = "VALIDATION_REJECT"
    AUTHORIZED_OPERATION_OK = "AUTHORIZED_OPERATION_OK"
    AUTHZ_DENIED_WITH_BASELINE = "AUTHZ_DENIED_WITH_BASELINE"
    AUTHZ_DENIED_NO_BASELINE = "AUTHZ_DENIED_NO_BASELINE"
    TIMEOUT = "TIMEOUT"
    CONNECTION_FAILURE = "CONNECTION_FAILURE"
    API_ACCEPTED_NOT_PERSISTED = "API_ACCEPTED_NOT_PERSISTED"
    DUPLICATE_NOT_PERSISTED = "DUPLICATE_NOT_PERSISTED"
    PERSISTED = "PERSISTED"
    STATE_LAG = "STATE_LAG"
    STALE_READ = "STALE_READ"
    BASELINE_MISSING = "BASELINE_MISSING"
    BASELINE_UNHEALTHY = "BASELINE_UNHEALTHY"
    CLEANUP_FAILED = "CLEANUP_FAILED"
    INSUFFICIENT_SAMPLES = "INSUFFICIENT_SAMPLES"
    CREATE_REJECTED = "CREATE_REJECTED"
    UNEXPECTED_ACCEPT = "UNEXPECTED_ACCEPT"
    NO_INDEPENDENT_PROBE = "NO_INDEPENDENT_PROBE"
    STATE_READ_UNAVAILABLE = "STATE_READ_UNAVAILABLE"
    CONTRADICTORY_STATE_READS = "CONTRADICTORY_STATE_READS"


#: 每类故障用哪些独立探针。缺少映射 = 无独立证据能力 → UNKNOWN。
#: 纯 UI / 定位器类由 `browser_effect.BrowserEffectObserver` 提供独立观测，
#: 这里只标注能力归属（"browser"），不在本模块执行。
FAULT_EFFECT_PROBES: dict[str, tuple[str, ...]] = {
    "TF-API-001": ("api_error",),
    "TF-API-002": ("api_error",),
    # API-003/004 作用于**更新**接口：用创建接口探测会得到假阴性
    "TF-API-003": ("api_update",),
    "TF-API-004": ("api_update",),
    "TF-SEC-001": ("api_error",),
    # SEC-002 作用于**列表读取**：创建接口正常，必须单独观测读路径
    "TF-SEC-002": ("read_authz",),
    "TF-NET-001": ("api_timeout",),
    "TF-NET-002": ("api_timeout",),
    "TF-DB-001": ("persistence",),
    "TF-DB-002": ("persistence",),
    "TF-DB-005": ("persistence",),
    "TF-FLAKY-001": ("persistence",),
    "TF-FLAKY-002": ("api_error",),
    # DB-003/004 的滞后是**读到的值过期**（服务端立即返回 count-1），不是响应变慢：
    # 用延迟阈值永远测不到，必须比对"刚写入的行是否出现在状态读里"。
    "TF-DB-003": ("state_consistency",),
    "TF-DB-004": ("state_consistency",),
    # TIME-001/002 才是真正的响应变慢（列表读取 sleep 1.5s / 5s）
    "TF-TIME-001": ("read_latency",),
    "TF-TIME-002": ("read_latency",),
    # TIME-003 是"300ms 内第二次提交被丢弃"的双提交竞态：单次提交路径不受影响，
    # 没有能覆盖它的独立探针 → 明确 UNKNOWN（不得用单次提交"没影响"冒充结论）
    "TF-TIME-003": (),
    "TF-COMP-001": ("api_error",),
    "TF-COMP-002": ("persistence", "read_latency"),
    # 浏览器级观测（browser_effect）
    "TF-UI-001": ("browser",),
    "TF-UI-002": ("browser",),
    "TF-UI-003": ("browser",),
    "TF-UI-004": ("browser",),
    "TF-LOC-001": ("browser",),
    "TF-LOC-002": ("browser",),
    "TF-COMP-003": ("browser",),
}

#: 需要"故障前基线"才能下结论的探针（其余为自证型）
BASELINE_REQUIRED = {
    "api_error",
    "api_timeout",
    "api_update",
    "persistence",
    "read_authz",
    "read_latency",
    "state_consistency",
}


@dataclass
class ProbeResult:
    kind: str
    #: True=业务受影响；False=业务正常；None=无法判定
    impacted: bool | None
    detail: dict[str, Any] = field(default_factory=dict)
    elapsed_ms: float = 0.0
    at: float = 0.0
    classification: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "impacted": self.impacted,
            "classification": self.classification,
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
    """最小 HTTP 客户端（不引入 requests 依赖），返回 (status, text, elapsed_ms)。

    `status=0` 表示没拿到 HTTP 响应（DNS/拒连/超时），调用方必须结合耗时区分：
    快速失败 ≠ 超时，二者不能混为一谈。
    """
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"} if data is not None else {}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - 受控内网地址
            return (
                resp.status,
                resp.read(8192).decode("utf-8", "replace"),
                (time.perf_counter() - started) * 1000,
            )
    except urllib.error.HTTPError as exc:
        return (
            exc.code,
            exc.read(8192).decode("utf-8", "replace"),
            (time.perf_counter() - started) * 1000,
        )
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
        return 0, f"{type(exc).__name__}: {exc}", (time.perf_counter() - started) * 1000


def _json(text: str) -> dict[str, Any]:
    try:
        value = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def _parse_json_strict(text: str) -> dict[str, Any] | None:
    """严格 JSON 解析：非法 JSON / 非对象返回 None（区别于"响应合法但缺字段"）。"""
    try:
        value = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def network_error_reason(error_text: str) -> str:
    """把网络层异常文本归类成可审计的失败原因（保留原文由调用方记录）。"""
    low = error_text.lower()
    if "timed out" in low or "timeout" in low:
        return "TIMEOUT"
    if "refused" in low:
        return "CONNECTION_REFUSED"
    return "CONNECTION_FAILURE"


def _percentile(samples: list[float], q: float) -> float:
    """线性插值分位数（样本已按时间序），空样本返回 0.0。"""
    if not samples:
        return 0.0
    ordered = sorted(samples)
    if len(ordered) == 1:
        return float(ordered[0])
    pos = (len(ordered) - 1) * q
    low = int(pos)
    high = min(low + 1, len(ordered) - 1)
    frac = pos - low
    return float(ordered[low] + (ordered[high] - ordered[low]) * frac)


def _probe_prefix(token: str) -> str:
    return f"{PROBE_NAME_TAG}:{token}:"


def new_probe_token() -> str:
    return uuid.uuid4().hex[:12]


def _create_project(
    base_url: str, name: str, timeout: float, *, workspace: str = PROBE_WORKSPACE
) -> tuple[int, dict[str, Any], float, str]:
    status, text, ms = _http(
        "POST",
        base_url.rstrip("/") + "/api/projects",
        body={"name": name, "workspace_slug": workspace},
        timeout=timeout,
    )
    return status, _json(text), ms, text


def _cleanup_probe_data(base_url: str, token: str, timeout: float) -> dict[str, Any]:
    """回收本探针创建的数据；返回结果供调用方判定（失败不得当作成功）。"""
    status, text, ms = _http(
        "POST",
        base_url.rstrip("/") + "/api/test/probe-cleanup",
        body={"token": token, "workspace": PROBE_WORKSPACE},
        timeout=max(timeout, 8.0),
    )
    payload = _json(text)
    ok = (
        status == 200
        and bool(payload.get("ok"))
        and int(payload.get("remaining_matched") or 0) == 0
    )
    return {
        "ok": ok,
        "http_status": status,
        "elapsed_ms": round(ms, 1),
        "response": payload or text[:300],
    }


def _state_projects(base_url: str, timeout: float) -> tuple[int, dict[str, Any], float]:
    status, text, ms = _http(
        "GET",
        base_url.rstrip("/") + f"/api/state/projects?workspace={PROBE_WORKSPACE}",
        timeout=timeout,
    )
    return status, _json(text), ms


# ---------------------------------------------------------------------------
# 故障前基线
# ---------------------------------------------------------------------------
def capture_baseline(
    base_url: str,
    fault_ids: list[str] | tuple[str, ...],
    *,
    timeout: float = 6.0,
    probe_token: str | None = None,
) -> dict[str, Any]:
    """在**故障激活之前**采集健康基线。

    返回结构直接写入样本（`fault_effect_baseline`），便于事后审计
    "当时据以判断的基线是什么"。缺少基线时，需要基线的探针一律 UNKNOWN。
    """
    token = probe_token or new_probe_token()
    kinds = [k for k in probe_kinds_for(fault_ids) if k != "browser"]
    out: dict[str, Any] = {
        "at": time.time(),
        "probe_token": token,
        "probe_workspace": PROBE_WORKSPACE,
        "kinds": {},
    }
    if "api_error" in kinds:
        out["kinds"]["api_error"] = _baseline_api_error(base_url, timeout, token)
    if "api_timeout" in kinds:
        out["kinds"]["api_timeout"] = _baseline_api_timeout(base_url, timeout)
    if "persistence" in kinds:
        out["kinds"]["persistence"] = _baseline_persistence(base_url, timeout, token)
    if "read_latency" in kinds:
        out["kinds"]["read_latency"] = _baseline_read_latency(base_url, timeout)
    if "state_consistency" in kinds:
        out["kinds"]["state_consistency"] = _baseline_state_consistency(base_url, timeout, token)
    if "read_authz" in kinds:
        out["kinds"]["read_authz"] = _baseline_read_authz(base_url, timeout)
    if "api_update" in kinds:
        out["kinds"]["api_update"] = _baseline_api_update(base_url, timeout, token)
    return out


def _baseline_api_error(base_url: str, timeout: float, token: str) -> dict[str, Any]:
    """健康基线：合法载荷必须能创建成功（201），非法载荷必须被 4xx 拒绝。

    RUN-016 的缺陷：探测载荷是空的 `{}`，而 FastAPI 在**进入路由函数之前**就返回 422，
    注入的 500/403 永远不会执行 → 真实环境里探针只能看到 422，故障被判成"没影响"。
    修正后：观测用合法载荷（名字带探针 token，随后清理），空载荷只作为契约信息。
    """
    invalid_statuses: list[int] = []
    for _ in range(2):
        status, _text, _ms = _http(
            "POST", base_url.rstrip("/") + "/api/projects", body={}, timeout=timeout
        )
        invalid_statuses.append(status)
        if status == 0:
            break
    contract_ok = bool(invalid_statuses) and all(s in (400, 409, 422) for s in invalid_statuses)
    canary_name = f"{_probe_prefix(token)}baseline-api"
    authorized_status, _payload, _ms, _text = _create_project(base_url, canary_name, timeout)
    cleanup = _cleanup_probe_data(base_url, token, timeout)
    healthy = contract_ok and authorized_status in (200, 201) and bool(cleanup.get("ok"))
    return {
        "healthy": healthy,
        "invalid_statuses": invalid_statuses,
        "invalid_contract": "VALIDATION_REJECT" if contract_ok else "UNEXPECTED",
        "authorized_status": authorized_status,
        "authorized_ok": authorized_status in (200, 201),
        "cleanup": cleanup,
        "note": (
            ""
            if healthy
            else "健康基线异常：合法创建未成功或非法载荷未被 4xx 拒绝，无法归因后续故障"
        ),
    }


def _baseline_api_timeout(base_url: str, timeout: float) -> dict[str, Any]:
    """健康基线：读接口（无副作用）在短超时窗口内必须快速返回。"""
    limit = max(0.5, min(timeout, 2.5))
    statuses: list[int] = []
    elapsed: list[float] = []
    for _ in range(3):
        status, _text, ms = _http(
            "GET", base_url.rstrip("/") + "/api/projects?workspace=default", timeout=limit
        )
        statuses.append(status)
        elapsed.append(ms)
        if status == 0:
            break
    healthy = bool(statuses) and all(s == 200 for s in statuses)
    return {
        "healthy": healthy,
        "limit_s": limit,
        "statuses": statuses,
        "latency_p50_ms": round(statistics.median(elapsed), 1) if elapsed else 0.0,
        "latency_p95_ms": round(_percentile(elapsed, 0.95), 1),
        "note": "" if healthy else "健康基线不可达：无法区分环境故障与注入故障",
    }


def _baseline_persistence(base_url: str, timeout: float, token: str) -> dict[str, Any]:
    """健康基线：探针自己能真实写入、能按 key 回读、能清理干净。"""
    name = f"{_probe_prefix(token)}baseline-persist"
    status, payload, ms, text = _create_project(base_url, name, timeout)
    key = str((payload.get("project") or {}).get("key") or "")
    readback = _readback(base_url, name, key, timeout)
    cleanup = _cleanup_probe_data(base_url, token, timeout)
    healthy = status in (200, 201) and readback.get("persisted") is True and bool(cleanup.get("ok"))
    return {
        "healthy": healthy,
        "create_status": status,
        "create_body": text[:200],
        "readback": readback,
        "cleanup": cleanup,
        "latency_ms": round(ms, 1),
        "note": "" if healthy else "健康基线写入/回读/清理未全部通过",
    }


def _baseline_read_latency(base_url: str, timeout: float) -> dict[str, Any]:
    """健康基线：业务列表读取 5 次，记录 p50/p95（无副作用）。"""
    elapsed: list[float] = []
    statuses: list[int] = []
    for _ in range(5):
        status, _text, ms = _http(
            "GET", base_url.rstrip("/") + "/api/projects?workspace=default", timeout=timeout
        )
        statuses.append(status)
        elapsed.append(ms)
    healthy = bool(statuses) and all(s == 200 for s in statuses)
    return {
        "healthy": healthy,
        "samples": len(elapsed),
        "statuses": statuses,
        "latency_p50_ms": round(_percentile(elapsed, 0.5), 1),
        "latency_p95_ms": round(_percentile(elapsed, 0.95), 1),
        "raw_ms": [round(x, 1) for x in elapsed],
        "reference_threshold_ms": STATE_LAG_REFERENCE_MS,
        "note": "" if healthy else "健康基线列表读取不可用",
    }


def _baseline_state_consistency(base_url: str, timeout: float, token: str) -> dict[str, Any]:
    """健康基线：刚写入的项目必须立刻出现在状态读里（读写一致，且读取必须有效）。"""
    name = f"{_probe_prefix(token)}baseline-consistency"
    status, payload, ms, text = _create_project(base_url, name, timeout)
    key = str((payload.get("project") or {}).get("key") or "")
    immediate = _state_list_contains(base_url, name, key, timeout)
    direct = _read_state_project(base_url, key, timeout)
    cleanup = _cleanup_probe_data(base_url, token, timeout)
    healthy = (
        status in (200, 201)
        and immediate.get("verdict") == ReadVerdict.PRESENT.value
        and direct.get("verdict") == ReadVerdict.PRESENT.value
        and bool(cleanup.get("ok"))
    )
    return {
        "healthy": healthy,
        "create_status": status,
        "create_body": text[:160],
        "immediate_read": immediate,
        "direct_read": direct,
        "direct_status": direct.get("http_status"),
        "direct_elapsed_ms": direct.get("elapsed_ms"),
        "cleanup": cleanup,
        "create_elapsed_ms": round(ms, 1),
        "note": "" if healthy else "健康基线读写不一致、状态读取不可用或清理失败",
    }


def _baseline_read_authz(base_url: str, timeout: float) -> dict[str, Any]:
    """健康基线：授权读取业务列表必须 200（403 判定的前提）。"""
    statuses: list[int] = []
    elapsed: list[float] = []
    for _ in range(3):
        status, _text, ms = _http(
            "GET", base_url.rstrip("/") + "/api/projects?workspace=default", timeout=timeout
        )
        statuses.append(status)
        elapsed.append(ms)
    healthy = bool(statuses) and all(s == 200 for s in statuses)
    return {
        "healthy": healthy,
        "statuses": statuses,
        "latency_p50_ms": round(statistics.median(elapsed), 1) if elapsed else 0.0,
        "note": "" if healthy else "健康基线读取列表不是 200",
    }


def _baseline_api_update(base_url: str, timeout: float, token: str) -> dict[str, Any]:
    """健康基线：探针自己的项目可以被更新接口成功修改（随后清理）。"""
    name = f"{_probe_prefix(token)}baseline-update"
    status, payload, ms, text = _create_project(base_url, name, timeout)
    key = str((payload.get("project") or {}).get("key") or "")
    detail: dict[str, Any] = {"create_status": status, "create_body": text[:160], "key": key}
    if status not in (200, 201) or not key:
        cleanup = _cleanup_probe_data(base_url, token, timeout)
        return {
            "healthy": False,
            "update_status": 0,
            "cleanup": cleanup,
            "latency_ms": round(ms, 1),
            "note": "健康基线无法创建探针项目",
        }
    update_status, update_text, update_ms = _http(
        "PUT",
        base_url.rstrip("/") + f"/api/projects/{key}",
        body={"name": f"{name}-v2", "status": "ACTIVE"},
        timeout=timeout,
    )
    cleanup = _cleanup_probe_data(base_url, token, timeout)
    detail.update({"update_status": update_status, "update_body": update_text[:160]})
    healthy = update_status == 200 and bool(cleanup.get("ok"))
    return {
        "healthy": healthy,
        "update_status": update_status,
        "detail": detail,
        "cleanup": cleanup,
        "latency_ms": round(update_ms, 1),
        "note": "" if healthy else "健康基线更新接口未成功",
    }


def _read_state_list(base_url: str, timeout: float) -> dict[str, Any]:
    """权威状态列表的严格读取（三态）：只有 HTTP 200 + ok=true + 合法 projects 列表有效。

    RUN-018 WP2：403 / 5xx / 超时 / 连接失败 / 非 JSON / 缺少 ok / 缺少 projects
    一律 UNKNOWN（旧实现把这些当作"记录不存在"，会把读取不可用误判成
    ACCEPTED_NOT_PERSISTED / STALE_READ）。原始状态码与错误原因保留在返回值里。
    """
    status, text, ms = _http(
        "GET",
        base_url.rstrip("/") + f"/api/state/projects?workspace={PROBE_WORKSPACE}",
        timeout=timeout,
    )
    result: dict[str, Any] = {
        "valid": False,
        "reason": "",
        "http_status": status,
        "elapsed_ms": round(ms, 1),
    }
    if status == 0:
        result["reason"] = network_error_reason(text)
        result["error"] = text[:300]
        return result
    if status != 200:
        result["reason"] = f"HTTP_{status}"
        result["body"] = text[:300]
        return result
    payload = _parse_json_strict(text)
    if payload is None:
        result["reason"] = "NON_JSON_RESPONSE"
        result["body"] = text[:300]
        return result
    if "ok" not in payload:
        result["reason"] = "MISSING_OK_FIELD"
        result["body"] = text[:300]
        return result
    if payload.get("ok") is not True:
        result["reason"] = "OK_NOT_TRUE"
        result["body"] = text[:300]
        return result
    projects = payload.get("projects")
    if not isinstance(projects, list) or any(not isinstance(p, dict) for p in projects):
        result["reason"] = "MISSING_OR_MALFORMED_PROJECTS_FIELD"
        result["body"] = text[:300]
        return result
    result["valid"] = True
    result["reason"] = "OK"
    result["count"] = payload.get("count")
    result["projects"] = projects
    return result


def _state_list_contains(base_url: str, name: str, key: str, timeout: float) -> dict[str, Any]:
    """状态列表里是否能看到这条刚写入的记录（三态，按 key 优先，名称回退）。

    `present` 为 True/False/None（None = UNKNOWN）；只有有效权威读取才能给出
    True / False——有效读取明确表示记录不在列表里，才允许判 ABSENT。
    """
    read = _read_state_list(base_url, timeout)
    result: dict[str, Any] = {
        "present": None,
        "verdict": ReadVerdict.UNKNOWN.value,
        "reason": read.get("reason", ""),
        "http_status": read.get("http_status"),
        "elapsed_ms": read.get("elapsed_ms"),
    }
    for extra in ("count", "error", "body"):
        if extra in read:
            result[extra] = read[extra]
    if not read.get("valid"):
        return result
    for item in read["projects"]:
        if key and str(item.get("key")) == key:
            result["present"] = True
            result["verdict"] = ReadVerdict.PRESENT.value
            return result
        if str(item.get("name")) == name:
            result["present"] = True
            result["verdict"] = ReadVerdict.PRESENT.value
            return result
    result["present"] = False
    result["verdict"] = ReadVerdict.ABSENT.value
    return result


def _read_state_project(base_url: str, key: str, timeout: float) -> dict[str, Any]:
    """直接按 key 的权威状态查询（三态）：ok=True → PRESENT，ok=False → ABSENT。

    HTTP 200 + 布尔 `ok` 字段才是有效读取；ok=False 是权威的"记录不存在"。
    其余（HTTP 非 200 / 网络失败 / 非 JSON / 缺少 ok）一律 UNKNOWN，
    不得当作"不存在"的证据（RUN-018 WP2 修正点）。
    """
    result: dict[str, Any] = {
        "present": None,
        "verdict": ReadVerdict.UNKNOWN.value,
        "reason": "",
        "ok": None,
        "http_status": 0,
        "elapsed_ms": 0.0,
    }
    if not key:
        result["reason"] = "NO_PROJECT_KEY"
        return result
    status, text, ms = _http(
        "GET", base_url.rstrip("/") + f"/api/state/project/{key}", timeout=timeout
    )
    result["http_status"] = status
    result["elapsed_ms"] = round(ms, 1)
    if status == 0:
        result["reason"] = network_error_reason(text)
        result["error"] = text[:300]
        return result
    if status != 200:
        result["reason"] = f"HTTP_{status}"
        result["body"] = text[:300]
        return result
    payload = _parse_json_strict(text)
    if payload is None:
        result["reason"] = "NON_JSON_RESPONSE"
        result["body"] = text[:300]
        return result
    if "ok" not in payload:
        result["reason"] = "MISSING_OK_FIELD"
        result["body"] = text[:300]
        return result
    ok = payload.get("ok")
    result["ok"] = ok
    if ok is True:
        result["present"] = True
        result["verdict"] = ReadVerdict.PRESENT.value
        result["reason"] = "OK"
        return result
    if ok is False:
        result["present"] = False
        result["verdict"] = ReadVerdict.ABSENT.value
        result["reason"] = "OK"
        return result
    result["reason"] = "MALFORMED_OK_FIELD"
    return result


def _readback(base_url: str, name: str, key: str, timeout: float) -> dict[str, Any]:
    """按 key 精确回读（优先）或按名称在权威状态里查（回退）。三态：persisted=True/False/None。

    RUN-018 WP2：`persisted` 为 None（UNKNOWN）表示读取不可用——旧实现把它
    当作 False，会把 500/403/超时误判成"未落库"。只有有效权威读取才能证明不存在。
    """
    if key:
        read = _read_state_project(base_url, key, timeout)
        by, identity = "key", {"key": key}
    else:
        read = _state_list_contains(base_url, name, "", timeout)
        by, identity = "name", {"name": name}
    verdict = read.get("verdict")
    persisted = {"PRESENT": True, "ABSENT": False}.get(str(verdict))
    return {
        "by": by,
        **identity,
        "http_status": read.get("http_status"),
        "persisted": persisted,
        "verdict": verdict,
        "reason": read.get("reason"),
        "elapsed_ms": read.get("elapsed_ms"),
        "state_count": read.get("count"),
    }


# ---------------------------------------------------------------------------
# 故障后独立探针
# ---------------------------------------------------------------------------
def _probe_api_error(
    base_url: str, timeout: float, baseline: dict[str, Any] | None, token: str
) -> ProbeResult:
    """用**合法载荷**（探针自己的数据，随后清理）判断创建链路是否受影响。

    分类（严格按指令 §4.1）：
    - 真实 5xx → CONFIRMED；
    - 403 且基线证明"授权操作本来可以成功" → CONFIRMED；否则 UNKNOWN；
    - 2xx → NOT_TRIGGERED（业务操作成功）；
    - 4xx 校验拒绝（合法载荷在基线可用）→ UNKNOWN（无法归因）；
    - 连接失败 → UNKNOWN（不区分目标故障与环境故障就不能认定注入生效）。
    """
    base = (baseline or {}).get("kinds", {}).get("api_error") if baseline else None
    detail: dict[str, Any] = {"request": "POST /api/projects (valid payload)", "baseline": base}
    if not base:
        return ProbeResult(
            "api_error",
            None,
            {**detail, "reason": "BASELINE_MISSING"},
            0.0,
            time.time(),
            EffectClass.BASELINE_MISSING.value,
        )
    if not base.get("healthy"):
        return ProbeResult(
            "api_error",
            None,
            {**detail, "reason": "BASELINE_UNHEALTHY", "baseline_note": base.get("note")},
            0.0,
            time.time(),
            EffectClass.BASELINE_UNHEALTHY.value,
        )

    statuses: list[int] = []
    bodies: list[str] = []
    elapsed: list[float] = []
    total_ms = 0.0
    classification = EffectClass.AUTHORIZED_OPERATION_OK.value
    impacted: bool | None = False
    for idx in range(3):
        name = f"{_probe_prefix(token)}api{idx}-{uuid.uuid4().hex[:6]}"
        status, payload, ms, text = _create_project(base_url, name, timeout)
        statuses.append(status)
        bodies.append(text[:160])
        elapsed.append(ms)
        total_ms += ms
        if status in (500, 502, 503, 504):
            impacted = True
            classification = EffectClass.HTTP_5XX.value
            break
        if status == 403:
            if base.get("authorized_ok"):
                impacted = True
                classification = EffectClass.AUTHZ_DENIED_WITH_BASELINE.value
            else:
                impacted = None
                classification = EffectClass.AUTHZ_DENIED_NO_BASELINE.value
            break
        if status == 0:
            impacted = None
            classification = EffectClass.CONNECTION_FAILURE.value
            break
        if status in (400, 409, 422):
            # 基线里同样的合法载荷是可用的，现在被校验拒绝：无法归因（不是业务成功也不是故障证明）
            impacted = None
            classification = EffectClass.CREATE_REJECTED.value
            break
        if 200 <= status < 300 and payload.get("persisted") is False:
            # API 说成功但内部标记未持久化：这是持久化问题，交给 persistence 探针；
            # 这里只记录，不把它当作 API 链路故障
            bodies[-1] = f"{bodies[-1]} (persisted=false)"

    cleanup = _cleanup_probe_data(base_url, token, timeout)
    detail.update(
        {
            "statuses": statuses,
            "bodies": bodies,
            "elapsed_ms": [round(x, 1) for x in elapsed],
            "cleanup": cleanup,
        }
    )
    if not cleanup.get("ok"):
        return ProbeResult(
            "api_error",
            None,
            {**detail, "reason": "PROBE_CLEANUP_FAILED", "requires_environment_reset": True},
            total_ms,
            time.time(),
            EffectClass.CLEANUP_FAILED.value,
        )
    detail["reason"] = classification
    return ProbeResult("api_error", impacted, detail, total_ms, time.time(), classification)


def _probe_api_timeout(
    base_url: str, timeout: float, baseline: dict[str, Any] | None, token: str
) -> ProbeResult:
    """短超时探测**读接口**（无副作用）：5xx / 真超时都算业务受影响。

    RUN-016 的缺陷：用空载荷 POST 创建接口，FastAPI 在路由函数之前就返回 422，
    `apply_network_delay()` 根本没执行 → 网络故障永远测不到。改成 GET 列表：
    该路径同样经过网络延迟注入，但不写任何数据。
    """
    base = (baseline or {}).get("kinds", {}).get("api_timeout") if baseline else None
    limit = max(0.5, min(timeout, 2.5))
    statuses: list[int] = []
    elapsed: list[float] = []
    errors: list[str] = []
    for _ in range(3):
        status, text, ms = _http(
            "GET", base_url.rstrip("/") + "/api/projects?workspace=default", timeout=limit
        )
        statuses.append(status)
        elapsed.append(ms)
        if status == 0:
            errors.append(text[:160])
        if status in (500, 502, 503, 504):
            break
    detail: dict[str, Any] = {
        "request": "GET /api/projects?workspace=default",
        "limit_s": limit,
        "statuses": statuses,
        "elapsed_ms": [round(x, 1) for x in elapsed],
        "errors": errors,
        "baseline": base,
    }
    ms_total = sum(elapsed)
    if not base:
        return ProbeResult(
            "api_timeout",
            None,
            {**detail, "reason": "BASELINE_MISSING"},
            ms_total,
            time.time(),
            EffectClass.BASELINE_MISSING.value,
        )
    if not base.get("healthy"):
        return ProbeResult(
            "api_timeout",
            None,
            {**detail, "reason": "BASELINE_UNHEALTHY", "baseline_note": base.get("note")},
            ms_total,
            time.time(),
            EffectClass.BASELINE_UNHEALTHY.value,
        )

    if any(s in (500, 502, 503, 504) for s in statuses):
        return ProbeResult(
            "api_timeout",
            True,
            {**detail, "reason": "HTTP_5XX_INJECTED_FAULT"},
            ms_total,
            time.time(),
            EffectClass.HTTP_5XX.value,
        )
    timed_out = any(s == 0 and ms >= limit * 900 for s, ms in zip(statuses, elapsed, strict=False))
    if timed_out:
        base_p95 = float(base.get("latency_p95_ms") or 0.0)
        if base_p95 < limit * 500:
            return ProbeResult(
                "api_timeout",
                True,
                {**detail, "reason": "TIMEOUT_VS_HEALTHY_BASELINE", "baseline_p95_ms": base_p95},
                ms_total,
                time.time(),
                EffectClass.TIMEOUT.value,
            )
        return ProbeResult(
            "api_timeout",
            None,
            {**detail, "reason": "TIMEOUT_BUT_SLOW_BASELINE", "baseline_p95_ms": base_p95},
            ms_total,
            time.time(),
            EffectClass.BASELINE_UNHEALTHY.value,
        )
    if any(s == 0 for s in statuses):
        return ProbeResult(
            "api_timeout",
            None,
            {**detail, "reason": "CONNECTION_FAILURE_UNCLASSIFIED"},
            ms_total,
            time.time(),
            EffectClass.CONNECTION_FAILURE.value,
        )
    if all(s == 200 for s in statuses):
        return ProbeResult(
            "api_timeout",
            False,
            {**detail, "reason": "FAST_RESPONSE_WITHIN_LIMIT"},
            ms_total,
            time.time(),
            EffectClass.AUTHORIZED_OPERATION_OK.value,
        )
    return ProbeResult(
        "api_timeout",
        None,
        {**detail, "reason": "UNCLASSIFIED"},
        ms_total,
        time.time(),
        EffectClass.CONNECTION_FAILURE.value,
    )


def _probe_persistence(
    base_url: str, timeout: float, baseline: dict[str, Any] | None, token: str
) -> ProbeResult:
    """真实写入 → 按 key 独立回读 → 重名写入验证 → **只清理自己的数据**。

    三次尝试用于覆盖 50% 概率的 Flaky 数据源（TF-FLAKY-001）：任一次出现
    "API 说成功但没落库"即确认；三次都正常才判 NOT_TRIGGERED。
    """
    base = (baseline or {}).get("kinds", {}).get("persistence") if baseline else None
    detail: dict[str, Any] = {"baseline": base, "attempts": []}
    if not base or not base.get("healthy"):
        return ProbeResult(
            "persistence",
            None,
            {
                **detail,
                "reason": "BASELINE_MISSING" if not base else "BASELINE_UNHEALTHY",
                "baseline_note": (base or {}).get("note", ""),
            },
            0.0,
            time.time(),
            (
                EffectClass.BASELINE_MISSING.value
                if not base
                else EffectClass.BASELINE_UNHEALTHY.value
            ),
        )

    impacted = False
    classification = EffectClass.PERSISTED.value
    http_5xx = False
    total_ms = 0.0
    for idx in range(3):
        name = f"{_probe_prefix(token)}p{idx}-{uuid.uuid4().hex[:6]}"
        status, payload, ms, text = _create_project(base_url, name, timeout)
        total_ms += ms
        attempt: dict[str, Any] = {
            "attempt": idx,
            "name": name,
            "create_status": status,
            "create_body": text[:200],
            "api_persisted_flag": payload.get("persisted"),
        }
        if status in (500, 502, 503, 504):
            http_5xx = True
            impacted = True
            attempt["outcome"] = "HTTP_5XX"
            detail["attempts"].append(attempt)
            break
        if status not in (200, 201):
            attempt["outcome"] = "CREATE_REJECTED"
            detail["attempts"].append(attempt)
            continue
        key = str((payload.get("project") or {}).get("key") or "")
        readback = _readback(base_url, name, key, timeout)
        attempt["readback"] = readback
        if readback.get("persisted") is None:
            # 读取不可用 ≠ 未落库：该次尝试无法判定，不得当作 ACCEPTED_NOT_PERSISTED
            attempt["outcome"] = "READ_UNAVAILABLE"
            detail["attempts"].append(attempt)
            continue
        if not readback.get("persisted"):
            attempt["outcome"] = "ACCEPTED_NOT_PERSISTED"
            impacted = True
            classification = EffectClass.API_ACCEPTED_NOT_PERSISTED.value
            detail["attempts"].append(attempt)
            break
        # 重名写入（覆盖 TF-DB-002 的"静默失败"路径）
        dup_status, dup_payload, dup_ms, dup_text = _create_project(base_url, name, timeout)
        total_ms += dup_ms
        attempt["duplicate"] = {
            "create_status": dup_status,
            "create_body": dup_text[:200],
            "api_persisted_flag": dup_payload.get("persisted"),
        }
        if dup_status in (500, 502, 503, 504):
            http_5xx = True
            impacted = True
            attempt["outcome"] = "DUPLICATE_HTTP_5XX"
            detail["attempts"].append(attempt)
            break
        if dup_status in (200, 201):
            dup_key = str((dup_payload.get("project") or {}).get("key") or "")
            if dup_key:
                dup_read = _readback(base_url, name, dup_key, timeout)
            else:
                s_read = _read_state_list(base_url, timeout)
                if s_read.get("valid"):
                    dup_count = sum(
                        1 for p in s_read["projects"] if str(p.get("name")) == name
                    )
                    dup_read = {
                        "by": "name",
                        "http_status": s_read.get("http_status"),
                        "persisted": dup_count >= 2,
                        "verdict": (
                            ReadVerdict.PRESENT if dup_count >= 2 else ReadVerdict.ABSENT
                        ).value,
                        "reason": "OK",
                    }
                else:
                    dup_read = {
                        "by": "name",
                        "http_status": s_read.get("http_status"),
                        "persisted": None,
                        "verdict": ReadVerdict.UNKNOWN.value,
                        "reason": s_read.get("reason"),
                    }
            attempt["duplicate"]["readback"] = dup_read
            if dup_read.get("persisted") is None:
                attempt["outcome"] = "DUPLICATE_READ_UNAVAILABLE"
                detail["attempts"].append(attempt)
                continue
            if not dup_read.get("persisted"):
                attempt["outcome"] = "DUPLICATE_NOT_PERSISTED"
                impacted = True
                classification = EffectClass.DUPLICATE_NOT_PERSISTED.value
                detail["attempts"].append(attempt)
                break
        attempt["outcome"] = "PERSISTED"
        detail["attempts"].append(attempt)

    cleanup = _cleanup_probe_data(base_url, token, timeout)
    detail["cleanup"] = cleanup
    detail["side_effect"] = True
    detail["probe_workspace"] = PROBE_WORKSPACE
    detail["probe_token"] = token
    if not cleanup.get("ok"):
        return ProbeResult(
            "persistence",
            None,
            {**detail, "reason": "PROBE_CLEANUP_FAILED", "requires_environment_reset": True},
            total_ms,
            time.time(),
            EffectClass.CLEANUP_FAILED.value,
        )
    if http_5xx:
        return ProbeResult(
            "persistence",
            True,
            {**detail, "reason": "HTTP_5XX_INJECTED_FAULT"},
            total_ms,
            time.time(),
            EffectClass.HTTP_5XX.value,
        )
    if impacted:
        return ProbeResult(
            "persistence",
            True,
            {**detail, "reason": classification},
            total_ms,
            time.time(),
            classification,
        )
    if all(a.get("outcome") == "PERSISTED" for a in detail["attempts"]):
        return ProbeResult(
            "persistence",
            False,
            {**detail, "reason": "ALL_ATTEMPTS_PERSISTED"},
            total_ms,
            time.time(),
            EffectClass.PERSISTED.value,
        )
    read_unavailable = any(
        a.get("outcome") in ("READ_UNAVAILABLE", "DUPLICATE_READ_UNAVAILABLE")
        for a in detail["attempts"]
    )
    return ProbeResult(
        "persistence",
        None,
        {
            **detail,
            "reason": (
                "READ_UNAVAILABLE_NO_CONCLUSIVE_ATTEMPT"
                if read_unavailable
                else "NO_CONCLUSIVE_ATTEMPT"
            ),
        },
        total_ms,
        time.time(),
        (
            EffectClass.STATE_READ_UNAVAILABLE.value
            if read_unavailable
            else EffectClass.CREATE_REJECTED.value
        ),
    )


def _probe_read_latency(
    base_url: str, timeout: float, baseline: dict[str, Any] | None, token: str
) -> ProbeResult:
    """故障前 5 次 + 故障后 5 次采样业务列表读取，比较 p50/p95（绝对 + 相对变化）。

    TF-TIME-001/002 的真实现象是"列表刷新变慢"（服务端 sleep 1.5s / 5s），
    与"读到的值过期"（TF-DB-003/004）是两种不同机制，不能共用一个探针。
    """
    base = (baseline or {}).get("kinds", {}).get("read_latency") if baseline else None
    elapsed: list[float] = []
    statuses: list[int] = []
    for _ in range(5):
        status, _text, ms = _http(
            "GET", base_url.rstrip("/") + "/api/projects?workspace=default", timeout=timeout
        )
        statuses.append(status)
        elapsed.append(ms)
    detail: dict[str, Any] = {
        "request": "GET /api/projects?workspace=default",
        "samples": len(elapsed),
        "statuses": statuses,
        "raw_ms": [round(x, 1) for x in elapsed],
        "latency_p50_ms": round(_percentile(elapsed, 0.5), 1),
        "latency_p95_ms": round(_percentile(elapsed, 0.95), 1),
        "reference_threshold_ms": STATE_LAG_REFERENCE_MS,
        "baseline": base,
    }
    total_ms = sum(elapsed)
    if not base:
        return ProbeResult(
            "read_latency",
            None,
            {**detail, "reason": "BASELINE_MISSING"},
            total_ms,
            time.time(),
            EffectClass.BASELINE_MISSING.value,
        )
    if not base.get("healthy") or int(base.get("samples") or 0) < 5:
        return ProbeResult(
            "read_latency",
            None,
            {**detail, "reason": "BASELINE_UNHEALTHY", "baseline_note": base.get("note")},
            total_ms,
            time.time(),
            EffectClass.BASELINE_UNHEALTHY.value,
        )
    if any(s in (500, 502, 503, 504) for s in statuses):
        return ProbeResult(
            "read_latency",
            True,
            {**detail, "reason": "HTTP_5XX_ON_READ"},
            total_ms,
            time.time(),
            EffectClass.HTTP_5XX.value,
        )
    if len(elapsed) < 5 or any(s != 200 for s in statuses):
        return ProbeResult(
            "read_latency",
            None,
            {**detail, "reason": "INSUFFICIENT_SAMPLES"},
            total_ms,
            time.time(),
            EffectClass.INSUFFICIENT_SAMPLES.value,
        )
    after_p95 = _percentile(elapsed, 0.95)
    before_p95 = float(base.get("latency_p95_ms") or 0.0)
    lagged = (
        after_p95 >= max(STATE_LAG_REFERENCE_MS, before_p95 * 3) and (after_p95 - before_p95) >= 800
    )
    detail["comparison"] = {
        "before_p50_ms": base.get("latency_p50_ms"),
        "before_p95_ms": before_p95,
        "after_p50_ms": detail["latency_p50_ms"],
        "after_p95_ms": detail["latency_p95_ms"],
        "delta_p95_ms": round(after_p95 - before_p95, 1),
        "rule": "after_p95 >= max(1500, before_p95*3) 且 delta >= 800ms",
    }
    return ProbeResult(
        "read_latency",
        bool(lagged),
        {**detail, "reason": "READ_LATENCY" if lagged else "WITHIN_BASELINE_RANGE"},
        total_ms,
        time.time(),
        EffectClass.STATE_LAG.value if lagged else EffectClass.PERSISTED.value,
    )


def _probe_state_consistency(
    base_url: str, timeout: float, baseline: dict[str, Any] | None, token: str
) -> ProbeResult:
    """写入后立刻读状态：刚创建的记录是否**立即可见**；不可见则观察最终是否可见。

    TF-DB-003/004 的滞后语义是"状态查询返回滞后快照（少一行）"，服务端并不变慢，
    所以任何基于耗时的探针都测不到它。这里用"写入 → 立刻读 → 直接按 key 读 →
    等待窗口内重读"回答三个问题：值是否过期、多久后一致、数据本身是否落库。
    """
    base = (baseline or {}).get("kinds", {}).get("state_consistency") if baseline else None
    detail: dict[str, Any] = {"baseline": base}
    if not base:
        return ProbeResult(
            "state_consistency",
            None,
            {**detail, "reason": "BASELINE_MISSING"},
            0.0,
            time.time(),
            EffectClass.BASELINE_MISSING.value,
        )
    if not base.get("healthy"):
        return ProbeResult(
            "state_consistency",
            None,
            {**detail, "reason": "BASELINE_UNHEALTHY", "baseline_note": base.get("note")},
            0.0,
            time.time(),
            EffectClass.BASELINE_UNHEALTHY.value,
        )
    name = f"{_probe_prefix(token)}consistency"
    status, payload, ms, text = _create_project(base_url, name, timeout)
    total_ms = ms
    detail.update({"create_status": status, "create_body": text[:160]})
    if status in (500, 502, 503, 504):
        cleanup = _cleanup_probe_data(base_url, token, timeout)
        detail["cleanup"] = cleanup
        if not cleanup.get("ok"):
            return ProbeResult(
                "state_consistency",
                None,
                {**detail, "reason": "PROBE_CLEANUP_FAILED"},
                total_ms,
                time.time(),
                EffectClass.CLEANUP_FAILED.value,
            )
        return ProbeResult(
            "state_consistency",
            True,
            {**detail, "reason": "HTTP_5XX_ON_CREATE"},
            total_ms,
            time.time(),
            EffectClass.HTTP_5XX.value,
        )
    key = str((payload.get("project") or {}).get("key") or "")
    if status not in (200, 201) or not key:
        cleanup = _cleanup_probe_data(base_url, token, timeout)
        detail["cleanup"] = cleanup
        if not cleanup.get("ok"):
            return ProbeResult(
                "state_consistency",
                None,
                {**detail, "reason": "PROBE_CLEANUP_FAILED", "requires_environment_reset": True},
                total_ms,
                time.time(),
                EffectClass.CLEANUP_FAILED.value,
            )
        return ProbeResult(
            "state_consistency",
            None,
            {**detail, "reason": "PROBE_CREATE_FAILED"},
            total_ms,
            time.time(),
            EffectClass.CREATE_REJECTED.value,
        )

    immediate = _state_list_contains(base_url, name, key, timeout)
    total_ms += float(immediate.get("elapsed_ms") or 0.0)
    direct = _read_state_project(base_url, key, timeout)
    total_ms += float(direct.get("elapsed_ms") or 0.0)
    detail.update({"immediate_read": immediate, "direct_read": direct})

    # 延迟复测：区分"有效读取"与"不可用读取"，只有有效读取才改变结论
    eventual_present: bool | None = (
        True if immediate.get("verdict") == ReadVerdict.PRESENT.value else None
    )
    retest_attempts: list[dict[str, Any]] = []
    waited_s = 0.0
    if eventual_present is None:
        started_wait = time.time()
        deadline = started_wait + 12.0
        saw_valid_absent = False
        while time.time() < deadline:
            time.sleep(0.5)
            again = _state_list_contains(base_url, name, key, timeout)
            total_ms += float(again.get("elapsed_ms") or 0.0)
            retest_attempts.append(
                {
                    "t_s": round(time.time() - started_wait, 1),
                    "verdict": again.get("verdict"),
                    "http_status": again.get("http_status"),
                    "reason": again.get("reason"),
                }
            )
            if again.get("verdict") == ReadVerdict.PRESENT.value:
                eventual_present = True
                break
            if again.get("verdict") == ReadVerdict.ABSENT.value:
                saw_valid_absent = True
        waited_s = round(time.time() - started_wait, 1)
        if eventual_present is None and saw_valid_absent:
            # 窗口内只有有效读取且全部说"不在" → 有效证据；不可用读取不算
            eventual_present = False
    detail["eventual_read"] = {
        "present": eventual_present,
        "waited_s": waited_s,
        "attempts": retest_attempts[-20:],
    }
    cleanup = _cleanup_probe_data(base_url, token, timeout)
    detail["cleanup"] = cleanup
    detail["side_effect"] = True
    if not cleanup.get("ok"):
        return ProbeResult(
            "state_consistency",
            None,
            {**detail, "reason": "PROBE_CLEANUP_FAILED", "requires_environment_reset": True},
            total_ms,
            time.time(),
            EffectClass.CLEANUP_FAILED.value,
        )

    # ---- 三态判定（RUN-018 WP2）：只有有效权威读取才能证明"存在/不存在" ----
    verdict_d = direct.get("verdict")
    verdict_i = immediate.get("verdict")
    if verdict_d == ReadVerdict.ABSENT.value and verdict_i == ReadVerdict.PRESENT.value:
        return ProbeResult(
            "state_consistency",
            None,
            {
                **detail,
                "reason": "CONTRADICTORY_STATE_READS",
                "observation_failure": (
                    f"直接读取判 ABSENT（{direct.get('reason')}）但列表读取判 PRESENT："
                    "权威读取互相矛盾，不下结论"
                ),
            },
            total_ms,
            time.time(),
            EffectClass.CONTRADICTORY_STATE_READS.value,
        )
    if verdict_d == ReadVerdict.ABSENT.value:
        # 有效权威直接读取明确"不存在"（ok=false）→ 才允许判未落库
        return ProbeResult(
            "state_consistency",
            True,
            {**detail, "reason": "ACCEPTED_NOT_PERSISTED"},
            total_ms,
            time.time(),
            EffectClass.API_ACCEPTED_NOT_PERSISTED.value,
        )
    if verdict_i == ReadVerdict.PRESENT.value:
        return ProbeResult(
            "state_consistency",
            False,
            {**detail, "reason": "READ_AFTER_WRITE_CONSISTENT"},
            total_ms,
            time.time(),
            EffectClass.PERSISTED.value,
        )
    if verdict_i == ReadVerdict.ABSENT.value and (
        verdict_d == ReadVerdict.PRESENT.value or eventual_present is True
    ):
        # 列表有效缺席而记录确实存在（直接读或延迟复测证实）→ 快照过期
        return ProbeResult(
            "state_consistency",
            True,
            {
                **detail,
                "reason": "STALE_STATE_READ",
                "eventually_consistent": eventual_present,
            },
            total_ms,
            time.time(),
            EffectClass.STALE_READ.value,
        )
    if verdict_i == ReadVerdict.ABSENT.value:
        # 列表有效缺席但无法证明记录存在或不存在（直接读不可用且延迟未证实）
        return ProbeResult(
            "state_consistency",
            None,
            {
                **detail,
                "reason": "ABSENCE_UNPROVEN",
                "observation_failure": (
                    f"列表读取有效但记录缺席，直接读取不可用（{direct.get('reason')}）"
                    "且延迟复测未证实存在：无法证明数据不存在"
                ),
            },
            total_ms,
            time.time(),
            EffectClass.STATE_READ_UNAVAILABLE.value,
        )
    if verdict_d == ReadVerdict.PRESENT.value:
        return ProbeResult(
            "state_consistency",
            None,
            {
                **detail,
                "reason": "IMMEDIATE_LIST_READ_UNAVAILABLE",
                "observation_failure": (
                    f"落库已被直接读取证实，但立即列表读取不可用（{immediate.get('reason')}）"
                    "：读写一致性未测到，不判受影响也不判正常"
                ),
            },
            total_ms,
            time.time(),
            EffectClass.STATE_READ_UNAVAILABLE.value,
        )
    return ProbeResult(
        "state_consistency",
        None,
        {
            **detail,
            "reason": "STATE_READS_UNAVAILABLE",
            "observation_failure": (
                f"直接读取（{direct.get('reason')}）与立即列表读取（{immediate.get('reason')}）"
                "均不可用：读取失败不得当作数据不存在"
            ),
        },
        total_ms,
        time.time(),
        EffectClass.STATE_READ_UNAVAILABLE.value,
    )


def _probe_read_authz(
    base_url: str, timeout: float, baseline: dict[str, Any] | None, token: str
) -> ProbeResult:
    """授权读取探针：健康基线 200，故障后 403 → 读取权限被真实影响。"""
    base = (baseline or {}).get("kinds", {}).get("read_authz") if baseline else None
    statuses: list[int] = []
    elapsed: list[float] = []
    for _ in range(3):
        status, text, ms = _http(
            "GET", base_url.rstrip("/") + "/api/projects?workspace=default", timeout=timeout
        )
        statuses.append(status)
        elapsed.append(ms)
        if status in (403, 500, 502, 503, 504) or status == 0:
            break
    detail: dict[str, Any] = {
        "request": "GET /api/projects?workspace=default",
        "statuses": statuses,
        "baseline": base,
    }
    ms_total = sum(elapsed)
    if not base:
        return ProbeResult(
            "read_authz",
            None,
            {**detail, "reason": "BASELINE_MISSING"},
            ms_total,
            time.time(),
            EffectClass.BASELINE_MISSING.value,
        )
    if not base.get("healthy"):
        return ProbeResult(
            "read_authz",
            None,
            {**detail, "reason": "BASELINE_UNHEALTHY", "baseline_note": base.get("note")},
            ms_total,
            time.time(),
            EffectClass.BASELINE_UNHEALTHY.value,
        )
    if 403 in statuses:
        return ProbeResult(
            "read_authz",
            True,
            {**detail, "reason": "AUTHORIZED_READ_DENIED"},
            ms_total,
            time.time(),
            EffectClass.AUTHZ_DENIED_WITH_BASELINE.value,
        )
    if any(s in (500, 502, 503, 504) for s in statuses):
        return ProbeResult(
            "read_authz",
            True,
            {**detail, "reason": "HTTP_5XX_INJECTED_FAULT"},
            ms_total,
            time.time(),
            EffectClass.HTTP_5XX.value,
        )
    if all(s == 200 for s in statuses):
        return ProbeResult(
            "read_authz",
            False,
            {**detail, "reason": "READ_STILL_OK"},
            ms_total,
            time.time(),
            EffectClass.AUTHORIZED_OPERATION_OK.value,
        )
    return ProbeResult(
        "read_authz",
        None,
        {**detail, "reason": "UNCLASSIFIED"},
        ms_total,
        time.time(),
        EffectClass.CONNECTION_FAILURE.value,
    )


def _probe_api_update(
    base_url: str, timeout: float, baseline: dict[str, Any] | None, token: str
) -> ProbeResult:
    """更新接口探针：创建探针项目 → PUT 改名 → 回读 → 清理。

    TF-API-003/004 只作用于更新接口，用创建接口探测会得到假阴性。
    """
    base = (baseline or {}).get("kinds", {}).get("api_update") if baseline else None
    detail: dict[str, Any] = {"baseline": base}
    if not base:
        return ProbeResult(
            "api_update",
            None,
            {**detail, "reason": "BASELINE_MISSING"},
            0.0,
            time.time(),
            EffectClass.BASELINE_MISSING.value,
        )
    if not base.get("healthy"):
        return ProbeResult(
            "api_update",
            None,
            {**detail, "reason": "BASELINE_UNHEALTHY", "baseline_note": base.get("note")},
            0.0,
            time.time(),
            EffectClass.BASELINE_UNHEALTHY.value,
        )
    name = f"{_probe_prefix(token)}update"
    status, payload, ms, text = _create_project(base_url, name, timeout)
    total_ms = ms
    detail["create_status"] = status
    detail["create_body"] = text[:160]
    if status in (500, 502, 503, 504):
        cleanup = _cleanup_probe_data(base_url, token, timeout)
        detail["cleanup"] = cleanup
        if not cleanup.get("ok"):
            return ProbeResult(
                "api_update",
                None,
                {**detail, "reason": "PROBE_CLEANUP_FAILED"},
                total_ms,
                time.time(),
                EffectClass.CLEANUP_FAILED.value,
            )
        return ProbeResult(
            "api_update",
            True,
            {**detail, "reason": "CREATE_ALSO_5XX"},
            total_ms,
            time.time(),
            EffectClass.HTTP_5XX.value,
        )
    key = str((payload.get("project") or {}).get("key") or "")
    if status not in (200, 201) or not key:
        _cleanup_probe_data(base_url, token, timeout)
        return ProbeResult(
            "api_update",
            None,
            {**detail, "reason": "PROBE_CREATE_FAILED"},
            total_ms,
            time.time(),
            EffectClass.CREATE_REJECTED.value,
        )
    update_status, update_text, update_ms = _http(
        "PUT",
        base_url.rstrip("/") + f"/api/projects/{key}",
        body={"name": f"{name}-v2", "status": "ACTIVE"},
        timeout=timeout,
    )
    total_ms += update_ms
    detail.update({"update_status": update_status, "update_body": update_text[:160]})
    readback = _readback(base_url, f"{name}-v2", key, timeout)
    detail["readback"] = readback
    cleanup = _cleanup_probe_data(base_url, token, timeout)
    detail["cleanup"] = cleanup
    detail["side_effect"] = True
    if not cleanup.get("ok"):
        return ProbeResult(
            "api_update",
            None,
            {**detail, "reason": "PROBE_CLEANUP_FAILED", "requires_environment_reset": True},
            total_ms,
            time.time(),
            EffectClass.CLEANUP_FAILED.value,
        )
    if update_status in (500, 502, 503, 504):
        return ProbeResult(
            "api_update",
            True,
            {**detail, "reason": "HTTP_5XX_ON_UPDATE"},
            total_ms,
            time.time(),
            EffectClass.HTTP_5XX.value,
        )
    if update_status == 200:
        return ProbeResult(
            "api_update",
            False,
            {**detail, "reason": "UPDATE_APPLIED"},
            total_ms,
            time.time(),
            EffectClass.AUTHORIZED_OPERATION_OK.value,
        )
    return ProbeResult(
        "api_update",
        None,
        {**detail, "reason": "UPDATE_UNCLASSIFIED"},
        total_ms,
        time.time(),
        EffectClass.UNEXPECTED_ACCEPT.value,
    )


_PROBES = {
    "api_error": _probe_api_error,
    "api_timeout": _probe_api_timeout,
    "api_update": _probe_api_update,
    "persistence": _probe_persistence,
    "read_authz": _probe_read_authz,
    "read_latency": _probe_read_latency,
    "state_consistency": _probe_state_consistency,
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
    baseline: dict[str, Any] | None = None,
    timeout: float = 6.0,
    observed_at: float | None = None,
    probe_token: str | None = None,
) -> dict[str, Any]:
    """独立观测故障对业务的影响；返回可直接写入样本的字段集合。

    `baseline` 必须是**故障激活之前**由 `capture_baseline()` 采集的结果。
    没有基线时，需要基线的探针（api_error / api_timeout / read_latency / state_consistency）一律 UNKNOWN：
    在故障已生效的环境里"补采基线"无法证明因果关系。
    """
    kinds = [k for k in probe_kinds_for(fault_ids) if k != "browser"]
    at = time.time() if observed_at is None else observed_at
    token = (baseline or {}).get("probe_token") or probe_token or new_probe_token()
    if not kinds:
        browser_only = "browser" in probe_kinds_for(fault_ids)
        return {
            "fault_effect_status": EffectStatus.UNKNOWN.value,
            "fault_effect_source": (
                "BROWSER_EFFECT_OBSERVER_PENDING" if browser_only else "NO_INDEPENDENT_PROBE"
            ),
            "fault_effect_evidence_refs": [],
            "fault_effect_timestamp": at,
            "fault_effect_probes": [],
            "fault_effect_classification": EffectClass.NO_INDEPENDENT_PROBE.value,
            "fault_effect_note": (
                "纯 UI/定位器类故障需要浏览器级观测；不得用 Run verdict 代替效果证据"
                if browser_only
                else "该故障没有独立探针，效果只能 UNKNOWN"
            ),
            "fault_effect_requires_environment_reset": False,
            "benchmark_schema_version": BENCHMARK_SCHEMA_VERSION,
        }
    results = [_PROBES[kind](base_url, timeout, baseline, token) for kind in kinds]
    impacted = [r.impacted for r in results]
    cleanup_failed = any(r.classification == EffectClass.CLEANUP_FAILED.value for r in results)
    if cleanup_failed:
        status = EffectStatus.UNKNOWN
    elif any(x is True for x in impacted):
        status = EffectStatus.CONFIRMED
    elif impacted and all(x is False for x in impacted):
        status = EffectStatus.NOT_TRIGGERED
    else:
        status = EffectStatus.UNKNOWN
    primary = next((r for r in results if r.impacted is True), results[0])
    return {
        "fault_effect_status": status.value,
        "fault_effect_source": "INDEPENDENT_TEAMFLOW_PROBE:" + ",".join(kinds),
        "fault_effect_evidence_refs": [_evidence_id(r) for r in results],
        "fault_effect_timestamp": at,
        "fault_effect_probes": [r.to_dict() for r in results],
        "fault_effect_classification": primary.classification,
        "fault_effect_note": (
            "" if status != EffectStatus.UNKNOWN else primary.detail.get("reason", "")
        ),
        "fault_effect_requires_environment_reset": bool(cleanup_failed),
        "benchmark_schema_version": BENCHMARK_SCHEMA_VERSION,
    }


def observe_fault_activation(
    base_url: str,
    fault_ids: list[str] | tuple[str, ...],
    *,
    timeout: float = 5.0,
) -> dict[str, Any]:
    """故障激活状态（独立来源：TeamFlow 控制面），不参与效果判定。"""
    at = time.time()
    status, text, ms = _http("GET", base_url.rstrip("/") + "/api/faults", timeout=timeout)
    payload = _json(text)
    state = payload.get("state") if isinstance(payload.get("state"), dict) else {}
    active = {str(x) for x in (state.get("active") or [])}
    wanted = {str(x) for x in fault_ids}
    if status != 200:
        activation = "UNKNOWN"
    elif not wanted:
        activation = "NOT_APPLICABLE"
    elif wanted <= active:
        activation = "ACTIVE"
    elif wanted & active:
        activation = "PARTIAL"
    else:
        activation = "NOT_ACTIVE"
    return {
        "fault_activation_status": activation,
        "fault_activation_source": "teamflow:/api/faults",
        "fault_activation_timestamp": at,
        "fault_activation_http_status": status,
        "fault_activation_active": sorted(active),
        "fault_activation_expected": sorted(wanted),
        "fault_activation_elapsed_ms": round(ms, 1),
    }


def snapshot_business_state(base_url: str, *, timeout: float = 5.0) -> dict[str, Any]:
    """业务状态快照（独立于 TraceGuard 的 Run/Evidence），可用于前后差异对比。"""
    status, payload, ms = _state_projects(base_url, timeout)
    return {
        "source": "teamflow:/api/state/projects",
        "workspace": PROBE_WORKSPACE,
        "http_status": status,
        "state": payload,
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
    "BASELINE_REQUIRED",
    "BENCHMARK_SCHEMA_VERSION",
    "EffectClass",
    "EffectStatus",
    "FAULT_EFFECT_PROBES",
    "PROBE_WORKSPACE",
    "ProbeResult",
    "ReadVerdict",
    "capture_baseline",
    "diff_business_state",
    "network_error_reason",
    "new_probe_token",
    "observe_fault_activation",
    "observe_fault_effect",
    "probe_kinds_for",
    "snapshot_business_state",
]
