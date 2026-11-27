"""探针证据契约（RUN-018 WP5 §5.2）：探针执行 → 证据的统一字段契约。

一次探针执行必须能用同一份结构表达（写入 `probe_execution` 文档 **与**
`source_type=probe` 的 raw evidence content），至少覆盖：

    probe_id, probe_execution_id, run_id, diagnosis_id, workspace_id, probe_status,
    observed_at, captured_at, source_type, source_trust, time_alignment,
    evidence_refs, raw_result_ref, failure_reason, duration_ms, cost, idempotency_key

设计约束：
- 优先复用既有结构（probe_execution 文档 + probe raw evidence），本模块只定义
  字段装配/校验/幂等键，不新增存储层、不改公共 Schema 的既有字段；
- 时间对齐三态（ALIGNED / POST_HOC / UNKNOWN）沿用 `probe.feedback` 的同一口径，
  同一证据不允许出现两套时间语义；
- `idempotency_key`：
  - 调用方显式提供 → 同一执行请求的幂等重放（返回已有执行记录，不重复扣预算）；
  - 未提供 → 每次执行生成唯一 key（延迟复测是**新请求**，绝不与首查共享 key）。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from traceguard_evidence.probe.feedback import ProbeSignal, resolve_alignment

#: 契约必填字段（新增内部模型的最小表达集）
PROBE_CONTRACT_FIELDS: tuple[str, ...] = (
    "probe_id",
    "probe_execution_id",
    "run_id",
    "diagnosis_id",
    "workspace_id",
    "probe_status",
    "observed_at",
    "captured_at",
    "source_type",
    "source_trust",
    "time_alignment",
    "evidence_refs",
    "raw_result_ref",
    "failure_reason",
    "duration_ms",
    "cost",
    "idempotency_key",
)

PROBE_SOURCE_TYPE = "probe"
PROBE_SOURCE_TRUST = "TRUSTED"


def derive_idempotency_key(
    probe_id: str,
    *,
    run_id: str | None = None,
    diagnosis_id: str | None = None,
    params: dict[str, Any] | None = None,
    explicit: str | None = None,
    execution_id: str = "",
) -> str:
    """幂等键：显式提供则原样使用；否则每次执行唯一（auto:<execution_id>）。

    显式键用于"同一执行请求重放"（客户端重试/消息重复投递）：
    同 (probe_id, run_id, diagnosis_id, idempotency_key) 只执行一次。
    自动键绝不复用：延迟复测与首查参数可能完全相同，但它们是**两次独立观测**。
    """
    if explicit and str(explicit).strip():
        return str(explicit).strip()
    basis = f"{probe_id}|{run_id or ''}|{diagnosis_id or ''}|{execution_id}"
    digest = hashlib.sha256(basis.encode()).hexdigest()[:16]
    return f"auto:{probe_id}:{digest}"


def build_probe_evidence_contract(
    *,
    probe_id: str,
    probe_execution_id: str,
    run_id: str | None,
    diagnosis_id: str | None,
    workspace_id: str,
    probe_status: str,
    observed_at: float,
    captured_at: float,
    evidence_refs: list[str],
    failure_reason: str = "",
    duration_ms: int = 0,
    cost: float = 0.0,
    idempotency_key: str = "",
    run_scope: bool = False,
    run_ended_at: float | None = None,
    source_trust: str = PROBE_SOURCE_TRUST,
) -> dict[str, Any]:
    """装配探针证据契约载荷（探针证据 content 与 probe_execution 文档共用）。

    - `time_alignment` 与 `probe.feedback.resolve_alignment` 同口径：
      run 作用域 → ALIGNED；晚于 run 结束 → POST_HOC；时间未知 → UNKNOWN。
    - `raw_result_ref` 指向承载原始结果的 probe_execution 文档（可回放/审计）。
    """
    alignment, alignment_note = resolve_alignment(
        ProbeSignal(
            probe_id=probe_id,
            execution_id=probe_execution_id,
            status=probe_status,
            observed_at=observed_at,
            run_scope=run_scope,
        ),
        run_ended_at,
    )
    payload: dict[str, Any] = {
        "probe_id": probe_id,
        "probe_execution_id": probe_execution_id,
        "run_id": run_id or "",
        "diagnosis_id": diagnosis_id or "",
        "workspace_id": workspace_id,
        "probe_status": probe_status,
        "observed_at": float(observed_at or 0.0),
        "captured_at": float(captured_at or 0.0),
        "source_type": PROBE_SOURCE_TYPE,
        "source_trust": source_trust if probe_status == "SUCCEEDED" else "UNKNOWN",
        "time_alignment": alignment,
        "evidence_refs": list(evidence_refs),
        "raw_result_ref": f"probe_execution:{probe_execution_id}",
        "failure_reason": failure_reason or "",
        "duration_ms": int(duration_ms or 0),
        "cost": float(cost or 0.0),
        "idempotency_key": idempotency_key
        or derive_idempotency_key(
            probe_id,
            run_id=run_id,
            diagnosis_id=diagnosis_id,
            execution_id=probe_execution_id,
        ),
    }
    if alignment_note:
        payload["time_alignment_note"] = alignment_note
    return payload


def validate_probe_evidence_contract(payload: dict[str, Any]) -> list[str]:
    """返回契约缺失/非法字段列表（空列表 = 契约完整）。"""
    missing = [f for f in PROBE_CONTRACT_FIELDS if f not in payload]
    problems = list(missing)
    if "probe_status" in payload and payload["probe_status"] not in {
        "SUCCEEDED",
        "FAILED",
        "TIMED_OUT",
        "BLOCKED",
        "UNKNOWN",
    }:
        problems.append(f"probe_status invalid: {payload['probe_status']}")
    if "time_alignment" in payload and payload["time_alignment"] not in {
        "ALIGNED",
        "POST_HOC",
        "UNKNOWN",
    }:
        problems.append(f"time_alignment invalid: {payload['time_alignment']}")
    return problems


def canonical_params_hash(params: dict[str, Any] | None) -> str:
    """参数规范化摘要（审计用；不参与幂等键——延迟复测参数可完全相同）。"""
    canonical = json.dumps(params or {}, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


__all__ = [
    "PROBE_CONTRACT_FIELDS",
    "PROBE_SOURCE_TRUST",
    "PROBE_SOURCE_TYPE",
    "build_probe_evidence_contract",
    "canonical_params_hash",
    "derive_idempotency_key",
    "validate_probe_evidence_contract",
]
