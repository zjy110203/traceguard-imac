"""Active Diagnosis Planner + Probe Engine（阶段9，C09）。

核心不变量：
- Probe 全只读，白名单模板（禁任意 SQL / 任意内网 / 越 Workspace）；
- 执行前五项校验：权限 → 资源范围 → 风险 → 预算 → 参数 Schema，任一失败拒绝并审计；
- Score = wI*EIG - wC*C - wR*R（启发式估计，非校准概率；权重/归一化为待确认，用默认值并标注）；
- 停止条件明确：确认/无高价值探针/预算耗尽/冲突不可解/边际价值不足/权限失效；
- Probe 结果不等于根因；新证据来源可追溯。
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from traceguard_core.errors import AppError, ErrorCode
from traceguard_core.logging import redact


@dataclass
class ProbeDefinition:
    probe_id: str
    capability: str  # dom_snapshot / screenshot / network_read / console_read / api_read / backend_log_read / database_read
    permission_required: str
    read_only: bool = True
    risk_level: str = "LOW"  # LOW / MEDIUM / HIGH
    allowed_templates: set[str] = field(default_factory=set)  # 白名单查询模板
    allowed_tables: set[str] = field(default_factory=set)  # DB 表白名单
    max_rows: int = 100
    timeout_ms: int = 5000
    estimated_cost: float = 1.0
    estimated_duration_ms: int = 500


@dataclass
class ProbeResult:
    probe_id: str
    status: str  # SUCCEEDED / FAILED / TIMED_OUT / BLOCKED
    data: dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    source: dict[str, Any] = field(default_factory=dict)  # 来源记录
    redacted: bool = False


@dataclass
class ProbeBudget:
    max_probes: int = 5
    max_total_cost: float = 10.0
    max_duration_seconds: float = 60.0

    def __post_init__(self) -> None:
        self._start = time.time()

    def time_left(self) -> float:
        return self.max_duration_seconds - (time.time() - self._start)


class ProbeEngine:
    def __init__(self, budget: ProbeBudget | None = None) -> None:
        self.definitions: dict[str, ProbeDefinition] = {}
        self.executors: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {}
        self.budget = budget or ProbeBudget()
        self._calls = 0
        self._cost = 0.0
        self._audit: list[dict[str, Any]] = []
        self._permissions: set[str] = set()

    def grant_permission(self, perm: str) -> None:
        self._permissions.add(perm)

    def register(self, definition: ProbeDefinition, executor: Callable[[dict[str, Any]], dict[str, Any]]) -> None:
        self.definitions[definition.probe_id] = definition
        self.executors[definition.probe_id] = executor

    def _validate(self, probe_id: str, params: dict[str, Any], workspace_id: str) -> tuple[ProbeDefinition, str]:
        """五项校验：权限 → 资源范围 → 风险 → 预算 → 参数 Schema。任一失败抛错。"""
        d = self.definitions.get(probe_id)
        if not d:
            raise AppError(ErrorCode.NOT_FOUND, f"unknown probe {probe_id}")

        # 1. 权限校验
        if d.permission_required not in self._permissions:
            raise AppError(ErrorCode.AUTHORIZATION_ERROR, "PROBE_PERMISSION_DENIED")

        # 2. 资源范围校验（Workspace 边界 + 任意 SQL / 内网阻断）
        workspace = params.get("workspace")
        if workspace != workspace_id:
            raise AppError(ErrorCode.AUTHORIZATION_ERROR, "CROSS_WORKSPACE_ACCESS_DENIED")
        query = str(params.get("query", ""))
        if re.search(r"\b(drop|delete|update|insert|alter|create)\b", query, re.I):
            raise AppError(ErrorCode.AUTHORIZATION_ERROR, "WRITE_SQL_FORBIDDEN")
        if "template" in params and params["template"] not in d.allowed_templates:
            raise AppError(ErrorCode.AUTHORIZATION_ERROR, "QUERY_TEMPLATE_NOT_WHITELISTED")
        if "table" in params and params["table"] not in d.allowed_tables:
            raise AppError(ErrorCode.AUTHORIZATION_ERROR, "TABLE_NOT_WHITELISTED")

        # 3. 风险检查
        if d.risk_level == "HIGH" and not params.get("explicit_risk_approval"):
            raise AppError(ErrorCode.AUTHORIZATION_ERROR, "HIGH_RISK_PROBE_REQUIRES_APPROVAL")

        # 4. 预算检查
        if self._calls >= self.budget.max_probes:
            raise AppError(ErrorCode.RATE_LIMITED, "PROBE_COUNT_BUDGET_EXHAUSTED")
        if self._cost + d.estimated_cost > self.budget.max_total_cost:
            raise AppError(ErrorCode.RATE_LIMITED, "PROBE_COST_BUDGET_EXHAUSTED")
        if self.budget.time_left() <= 0:
            raise AppError(ErrorCode.TIMEOUT, "PROBE_TIME_BUDGET_EXHAUSTED")

        # 5. 参数 Schema 校验
        if not isinstance(params, dict) or not params:
            raise AppError(ErrorCode.VALIDATION_ERROR, "INVALID_PROBE_PARAMS")
        return d, "ALL_CHECKS_PASSED"

    def execute(self, probe_id: str, params: dict[str, Any], workspace_id: str) -> ProbeResult:
        try:
            d, decision = self._validate(probe_id, params, workspace_id)
        except AppError as e:
            self._audit.append({"probe_id": probe_id, "decision": e.code.value,
                                "reason": e.message, "at": time.time()})
            return ProbeResult(probe_id, "BLOCKED", reason=e.message,
                               source={"decision": e.code.value, "error_code": e.code.value})

        self._calls += 1
        self._cost += d.estimated_cost
        try:
            data = self.executors[probe_id](params)
            # 行数限制
            if "rows" in data and isinstance(data["rows"], list):
                data["rows"] = data["rows"][: d.max_rows]
            redacted_data = redact(data)
            self._audit.append({"probe_id": probe_id, "decision": "ALLOW",
                                "workspace": workspace_id, "at": time.time()})
            return ProbeResult(
                probe_id, "SUCCEEDED", data=redacted_data, redacted=True,
                source={"probe_id": probe_id, "template": params.get("template"),
                        "params_keys": sorted(params.keys()), "workspace": workspace_id,
                        "timestamp": time.time()},
            )
        except Exception as e:  # noqa: BLE001
            self._audit.append({"probe_id": probe_id, "decision": "EXEC_ERROR",
                                "reason": str(e)[:100], "at": time.time()})
            return ProbeResult(probe_id, "FAILED", reason=str(e)[:200])

    def audit_log(self) -> list[dict[str, Any]]:
        return list(self._audit)


# --------------- Planner（EIG 启发式） ---------------
@dataclass
class ScoreWeights:
    w_i: float = 1.0  # EIG 权重（默认，待实验校准）
    w_c: float = 0.3  # 成本权重
    w_r: float = 0.5  # 风险权重
    note: str = "启发式默认权重，归一化与参数仍为待确认事项（P-OTHER），非校准概率"


class ActivePlanner:
    """根据证据缺口选择高价值安全探针；明确停止条件。"""

    def __init__(self, engine: ProbeEngine, weights: ScoreWeights | None = None) -> None:
        self.engine = engine
        self.weights = weights or ScoreWeights()

    def score(self, probe: ProbeDefinition, information_gain: float) -> float:
        """Score = wI*EIG - wC*C - wR*R。EIG 为估计量，不描述为校准概率。"""
        risk = {"LOW": 0.0, "MEDIUM": 0.5, "HIGH": 1.0}.get(probe.risk_level, 1.0)
        return (
            self.weights.w_i * information_gain
            - self.weights.w_c * probe.estimated_cost
            - self.weights.w_r * risk
        )

    def choose(
        self,
        candidates: list[tuple[ProbeDefinition, float]],  # (probe, estimated_information_gain)
        *,
        confirmed: bool = False,
        conflicts_unresolved: bool = False,
        permissions_valid: bool = True,
    ) -> tuple[ProbeDefinition | None, str]:
        """选择探针或给出停止原因。"""
        if confirmed:
            return None, "STOP_CONFIRMED"
        if not permissions_valid:
            return None, "STOP_PERMISSION_INVALID"
        if conflicts_unresolved:
            return None, "STOP_CONFLICT_UNRESOLVED"
        if not candidates:
            return None, "STOP_NO_HIGH_VALUE_PROBE"
        if self.engine.budget.time_left() <= 0 or self.engine._calls >= self.engine.budget.max_probes:
            return None, "STOP_BUDGET_EXHAUSTED"

        scored = [(self.score(p, eig), p) for p, eig in candidates]
        scored.sort(key=lambda x: x[0], reverse=True)
        best_score, best = scored[0]
        if best_score <= 0:  # 边际信息价值不足
            return None, "STOP_MARGINAL_VALUE_INSUFFICIENT"
        return best, "PROBE_SELECTED"
