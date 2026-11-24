"""Active Diagnosis Planner + Probe Engine（阶段9，C09）。

核心不变量：
- Probe 全只读，白名单模板（禁任意 SQL / 任意内网 / 越 Workspace）；
- 执行前五项校验：权限 → 资源范围 → 风险 → 预算 → 参数 Schema，任一失败拒绝并审计；
- Score = wI*EIG - wC*C - wR*R（启发式估计，非校准概率；权重/归一化为待确认，用默认值并标注）；
- 停止条件明确：确认/无高价值探针/预算耗尽/冲突不可解/边际价值不足/权限失效；
- Probe 结果不等于根因；新证据来源可追溯。

RUN-005 修复（P0）：预算不再是"服务实例级"全局对象。
- 每个 Run / Diagnosis 拥有独立 `ProbeBudget`（独立 scope_key、独立起始时间、独立计数）；
- 应用进程运行多久都不会让新诊断的 60 秒窗口提前失效（窗口从该预算首次使用时开始计）；
- 预算可关闭并记录关闭原因；关闭后不可再扣费（明确的停止原因，而非静默失败）；
- 预算使用量可序列化持久化，进程重启后按 scope 恢复，不会把不同诊断的预算混为一谈。

RUN-006 修复（WP1-03）：预算持久化改为"故障安全 + 跨进程原子"。
- 读取预算失败（数据库异常）不再静默新建预算继续跑，而是明确返回 UNAVAILABLE（fail closed）；
- 扣减预算采用"先写库、后计入"：持久化失败则回滚内存计数并拒绝执行探针；
- 持久化使用文档版本 CAS：多进程同时扣减同一 scope 时，冲突方重新加载最新值再重试，
  因此同一 Run / Diagnosis 不会因为多个 API 进程而各得一份独立预算。
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from traceguard_core.errors import AppError, ErrorCode
from traceguard_core.logging import redact

#: P06 尚未由项目负责人确认；以下数值一律标记 provisional，不得写成正式冻结决策。
PROVISIONAL_NOTE = (
    "P06 待负责人确认：以下预算与风险阈值为 provisional 配置，仅用于本轮实现与测试，"
    "不得视为正式冻结的工程决策。"
)


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
    budget_scope: str = ""
    duration_ms: int = 0
    #: RUN-011 WP2：实际观测发生时刻（epoch 秒）。探针在 Run 结束后执行时，
    #: 该时刻 > run_ended_at，属于"事后观测"，不能冒充 Run 当时的历史状态。
    observed_at: float = 0.0


def _fake_clock() -> float:  # pragma: no cover - 仅作为 dataclass 默认占位
    return time.time()


class BudgetUnavailable(AppError):
    """预算无法可靠读写（数据库异常 / CAS 无法收敛）：必须拒绝执行探针。"""

    def __init__(self, message: str = "PROBE_BUDGET_UNAVAILABLE", *, detail: str = "") -> None:
        super().__init__(ErrorCode.DEPENDENCY_ERROR, message)
        self.detail = detail


class _BudgetVersionConflict(Exception):
    """内部信号：持久化文档版本 CAS 冲突，需要重新加载后重试。"""


def budget_doc_id(scope_key: str) -> str:
    """预算文档 ID：同一 scope 稳定，不同 Run/Diagnosis 必然不同。"""
    safe = re.sub(r"[^0-9A-Za-z_.-]+", "-", scope_key)
    return f"pbudget_{safe}"


@dataclass
class ProbeBudget:
    """单个 Run / Diagnosis 的独立探针预算。

    `started_at` 在预算首次创建时确定（不是进程启动时间），因此进程长期运行不会
    让后续诊断的 60 秒窗口提前耗尽。
    """

    scope_key: str = "service"  # run:<run_id> / diagnosis:<diag_id> / manual:<ws>
    max_probes: int = 50
    max_total_cost: float = 100.0
    max_duration_seconds: float = 60.0
    provisional: bool = True
    note: str = PROVISIONAL_NOTE
    started_at: float = 0.0
    probes_used: int = 0
    cost_used: float = 0.0
    closed_at: float | None = None
    close_reason: str = ""
    owner_run_id: str = ""
    owner_diagnosis_id: str = ""
    workspace_id: str = "default"
    version: int = 1
    #: 持久化文档版本（用于跨进程 CAS；0 表示尚未落库）
    doc_version: int = 0
    clock: Callable[[], float] = field(default=_fake_clock, repr=False, compare=False)
    _lock: Any = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._lock is None:
            import threading

            self._lock = threading.RLock()
        if not self.started_at:
            self.started_at = self.clock()

    # ------------------------------------------------------------------ state
    @property
    def budget_id(self) -> str:
        return budget_doc_id(self.scope_key)

    @property
    def is_closed(self) -> bool:
        return self.closed_at is not None

    def elapsed_seconds(self) -> float:
        end = self.closed_at if self.closed_at is not None else self.clock()
        return max(0.0, end - self.started_at)

    def time_left(self) -> float:
        if self.is_closed:
            return 0.0
        return max(0.0, self.max_duration_seconds - (self.clock() - self.started_at))

    def probes_left(self) -> int:
        return max(0, self.max_probes - self.probes_used)

    def cost_left(self) -> float:
        return max(0.0, self.max_total_cost - self.cost_used)

    # ------------------------------------------------------------- transition
    def charge(self, cost: float) -> None:
        with self._lock:
            self.probes_used += 1
            self.cost_used += float(cost)
            self.version += 1

    def close(self, reason: str) -> None:
        with self._lock:
            if self.closed_at is None:
                self.closed_at = self.clock()
                self.close_reason = reason
                self.version += 1

    def reserve(self, cost: float) -> None:
        """执行前先占位计数（并发下保证不超发），执行失败也不退还（计入尝试）。"""
        self.charge(cost)

    def try_reserve(self, cost: float) -> str:
        """原子占位：返回空串表示成功，否则返回明确的拒绝原因。"""
        with self._lock:
            reason = self.exhausted_reason()
            if reason:
                return reason
            if self.cost_used + cost > self.max_total_cost:
                return "PROBE_COST_BUDGET_EXHAUSTED"
            self.probes_used += 1
            self.cost_used += float(cost)
            self.version += 1
            return ""

    def exhausted_reason(self) -> str:
        """返回明确的耗尽原因（空串表示仍有预算）。"""
        if self.is_closed:
            return "PROBE_BUDGET_CLOSED"
        if self.probes_used >= self.max_probes:
            return "PROBE_COUNT_BUDGET_EXHAUSTED"
        if self.cost_used >= self.max_total_cost:
            return "PROBE_COST_BUDGET_EXHAUSTED"
        if self.time_left() <= 0:
            return "PROBE_TIME_BUDGET_EXHAUSTED"
        return ""

    def snapshot(self) -> dict[str, Any]:
        return {
            "budget_id": self.budget_id,
            "scope_key": self.scope_key,
            "owner_run_id": self.owner_run_id,
            "owner_diagnosis_id": self.owner_diagnosis_id,
            "workspace_id": self.workspace_id,
            "max_probes": self.max_probes,
            "probes_used": self.probes_used,
            "probes_left": self.probes_left(),
            "max_total_cost": self.max_total_cost,
            "cost_used": round(self.cost_used, 4),
            "cost_left": round(self.cost_left(), 4),
            "max_duration_seconds": self.max_duration_seconds,
            "elapsed_seconds": round(self.elapsed_seconds(), 3),
            "time_left": round(self.time_left(), 3),
            "closed": self.is_closed,
            "close_reason": self.close_reason,
            "provisional": self.provisional,
            "note": self.note,
            "started_at": self.started_at,
            "closed_at": self.closed_at,
            "version": self.version,
            "doc_version": self.doc_version,
        }

    def to_dict(self) -> dict[str, Any]:
        return self.snapshot()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProbeBudget:
        """从持久化文档恢复（clock 必须重新注入为当前时钟）。"""
        return cls(
            scope_key=str(data.get("scope_key") or data.get("budget_id") or "service"),
            max_probes=int(data.get("max_probes", 50)),
            max_total_cost=float(data.get("max_total_cost", 100.0)),
            max_duration_seconds=float(data.get("max_duration_seconds", 60.0)),
            provisional=bool(data.get("provisional", True)),
            note=str(data.get("note", PROVISIONAL_NOTE)),
            started_at=float(data.get("started_at") or 0.0),
            probes_used=int(data.get("probes_used", 0)),
            cost_used=float(data.get("cost_used", 0.0)),
            closed_at=(float(data["closed_at"]) if data.get("closed_at") else None),
            close_reason=str(data.get("close_reason", "")),
            owner_run_id=str(data.get("owner_run_id", "")),
            owner_diagnosis_id=str(data.get("owner_diagnosis_id", "")),
            workspace_id=str(data.get("workspace_id", "default")),
            version=int(data.get("version", 1)),
            doc_version=int(data.get("doc_version", 0)),
        )

    def apply_doc(self, data: dict[str, Any]) -> None:
        """用持久化文档刷新内存预算（CAS 冲突后重新加载，避免超发）。"""
        with self._lock:
            self.max_probes = int(data.get("max_probes", self.max_probes))
            self.max_total_cost = float(data.get("max_total_cost", self.max_total_cost))
            self.max_duration_seconds = float(
                data.get("max_duration_seconds", self.max_duration_seconds)
            )
            self.probes_used = int(data.get("probes_used", self.probes_used))
            self.cost_used = float(data.get("cost_used", self.cost_used))
            self.started_at = float(data.get("started_at") or self.started_at)
            closed_at = data.get("closed_at")
            self.closed_at = float(closed_at) if closed_at else self.closed_at
            self.close_reason = str(data.get("close_reason", self.close_reason))
            self.version = int(data.get("version", self.version))
            self.doc_version = int(data.get("doc_version", self.doc_version))


class ProbeBudgetRegistry:
    """按 Run / Diagnosis 隔离的预算注册表（线程安全）。

    `loader` / `saver` 由调用方注入（API 层用 DocStore 持久化）；未注入时为纯内存。
    恢复规则：只接受 scope_key 完全一致的预算文档，且已关闭的预算不再复用。
    """

    def __init__(
        self,
        *,
        max_probes: int = 50,
        max_total_cost: float = 100.0,
        max_duration_seconds: float = 60.0,
        provisional: bool = True,
        clock: Callable[[], float] = time.time,
        loader: Callable[[str], dict[str, Any] | None] | None = None,
        saver: Callable[[ProbeBudget], None] | None = None,
        strict_persistence: bool = True,
        cas_retries: int = 5,
    ) -> None:
        import threading

        self._budgets: dict[str, ProbeBudget] = {}
        self._lock = threading.RLock()
        self._defaults = {
            "max_probes": max_probes,
            "max_total_cost": max_total_cost,
            "max_duration_seconds": max_duration_seconds,
            "provisional": provisional,
        }
        self.clock = clock
        self.loader = loader
        self.saver = saver
        #: True = 持久化故障必须阻断探针（生产路径）；False 仅用于纯内存单测
        self.strict_persistence = strict_persistence
        self.cas_retries = max(1, cas_retries)

    def get_or_create(
        self,
        scope_key: str,
        *,
        run_id: str = "",
        diagnosis_id: str = "",
        workspace_id: str = "default",
        overrides: dict[str, Any] | None = None,
    ) -> ProbeBudget:
        with self._lock:
            existing = self._budgets.get(scope_key)
            if existing is not None:
                # 已关闭的预算继续返回：关闭是显式状态，不能被"再次调用"静默绕过
                return existing
            restored = self._load(scope_key, run_id=run_id, diagnosis_id=diagnosis_id)
            if restored is not None:
                self._budgets[scope_key] = restored
                return restored
            params = dict(self._defaults)
            if overrides:
                params.update({k: v for k, v in overrides.items() if v is not None})
            budget = ProbeBudget(
                scope_key=scope_key,
                owner_run_id=run_id,
                owner_diagnosis_id=diagnosis_id,
                workspace_id=workspace_id,
                clock=self.clock,
                **params,
            )
            self._budgets[scope_key] = budget
            self._persist(budget)  # 写不进去就不要假装有预算
            return budget

    def _load(self, scope_key: str, *, run_id: str, diagnosis_id: str) -> ProbeBudget | None:
        if self.loader is None:
            return None
        try:
            doc = self.loader(scope_key)
        except Exception as exc:  # noqa: BLE001
            # WP1-03：读取失败不能"新建一份预算继续跑"，否则等于凭空多出配额
            if self.strict_persistence:
                raise BudgetUnavailable(
                    "PROBE_BUDGET_UNAVAILABLE", detail=f"load failed: {type(exc).__name__}"
                ) from exc
            return None
        if not doc:
            return None
        if str(doc.get("scope_key", "")) != scope_key:
            return None  # 不同诊断/不同 Run 的预算绝不混用
        budget = ProbeBudget.from_dict(doc)
        budget.clock = self.clock
        # 归属校验：持久化文档声明的 owner 必须与本次调用一致（防止 scope 复用错配）
        if run_id and budget.owner_run_id and budget.owner_run_id != run_id:
            return None
        if (
            diagnosis_id
            and budget.owner_diagnosis_id
            and budget.owner_diagnosis_id != (diagnosis_id)
        ):
            return None
        return budget

    def get(self, scope_key: str) -> ProbeBudget | None:
        with self._lock:
            return self._budgets.get(scope_key)

    def close(self, scope_key: str, reason: str = "DIAGNOSIS_COMPLETED") -> ProbeBudget | None:
        with self._lock:
            budget = self._budgets.get(scope_key)
            if budget is None:
                return None
            budget.close(reason)
            # 关闭是"停止动作"：写失败不回滚关闭状态，但必须留下可观测告警
            self._persist_quiet(budget)
            return budget

    def save(self, budget: ProbeBudget) -> None:
        self._persist(budget)

    # --------------------------------------------------------------- persistence
    def _persist(self, budget: ProbeBudget) -> None:
        """把预算写入持久化层；失败必须抛出（调用方据此拒绝执行）。"""
        if self.saver is None:
            return
        try:
            self.saver(budget)
        except AppError as exc:
            if exc.code is ErrorCode.CONFLICT:
                raise _BudgetVersionConflict() from exc
            if self.strict_persistence:
                raise BudgetUnavailable(
                    "PROBE_BUDGET_UNAVAILABLE", detail=f"save failed: {exc.message[:80]}"
                ) from exc
        except Exception as exc:  # noqa: BLE001
            if self.strict_persistence:
                raise BudgetUnavailable(
                    "PROBE_BUDGET_UNAVAILABLE", detail=f"save failed: {type(exc).__name__}"
                ) from exc

    def _persist_quiet(self, budget: ProbeBudget) -> None:
        try:
            self._persist(budget)
        except Exception:  # noqa: BLE001 - 关闭路径不阻塞，但状态仍为 CLOSED
            import logging

            logging.getLogger("traceguard.probe.budget").warning(
                "probe_budget_close_persist_failed", extra={"scope": budget.scope_key}
            )

    def reserve(self, budget: ProbeBudget, cost: float) -> str:
        """跨进程原子占位。返回空串=成功；否则返回明确拒绝原因。

        - 先持久化、后计入内存：写库失败 → 回滚计数并抛 BudgetUnavailable；
        - CAS 冲突 → 重新加载最新持久化值后重试，保证同一 scope 不多发配额。
        """
        with self._lock:
            for _ in range(self.cas_retries):
                reason = budget.exhausted_reason()
                if reason:
                    return reason
                if budget.cost_used + cost > budget.max_total_cost:
                    return "PROBE_COST_BUDGET_EXHAUSTED"
                if self.saver is None:
                    budget.charge(cost)
                    return ""
                budget.probes_used += 1
                budget.cost_used += float(cost)
                budget.version += 1
                try:
                    self._persist(budget)
                    return ""
                except _BudgetVersionConflict:
                    self._rollback_charge(budget, cost)
                    if not self._reload(budget):
                        return "PROBE_BUDGET_UNAVAILABLE"
                except BudgetUnavailable:
                    self._rollback_charge(budget, cost)
                    raise
            return "PROBE_BUDGET_UNAVAILABLE"

    @staticmethod
    def _rollback_charge(budget: ProbeBudget, cost: float) -> None:
        budget.probes_used = max(0, budget.probes_used - 1)
        budget.cost_used = max(0.0, budget.cost_used - float(cost))
        budget.version = max(1, budget.version - 1)

    def _reload(self, budget: ProbeBudget) -> bool:
        if self.loader is None:
            return False
        try:
            doc = self.loader(budget.scope_key)
        except Exception as exc:  # noqa: BLE001
            raise BudgetUnavailable(
                "PROBE_BUDGET_UNAVAILABLE", detail=f"reload failed: {type(exc).__name__}"
            ) from exc
        if not doc:
            return False
        doc.setdefault("scope_key", budget.scope_key)
        budget.apply_doc(doc)
        return True

    def snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            return [b.snapshot() for b in self._budgets.values()]

    def for_run(
        self, run_id: str, *, diagnosis_id: str = "", workspace_id: str = "default"
    ) -> ProbeBudget:
        scope = f"run:{run_id}" if not diagnosis_id else f"diagnosis:{diagnosis_id}"
        return self.get_or_create(
            scope, run_id=run_id, diagnosis_id=diagnosis_id, workspace_id=workspace_id
        )

    def replace_for_tests(self, budget: ProbeBudget) -> None:
        """测试辅助：注入自定义时钟/预算，不影响生产路径。"""
        with self._lock:
            self._budgets[budget.scope_key] = budget


class ProbeEngine:
    def __init__(self, budget: ProbeBudget | None = None) -> None:
        self.definitions: dict[str, ProbeDefinition] = {}
        self.executors: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {}
        self.budget = budget or ProbeBudget()
        self._audit: list[dict[str, Any]] = []
        self._permissions: set[str] = set()

    #: 兼容旧调用：默认预算的使用量
    @property
    def _calls(self) -> int:
        return self.budget.probes_used

    @property
    def _cost(self) -> float:
        return self.budget.cost_used

    def grant_permission(self, perm: str) -> None:
        self._permissions.add(perm)

    def revoke_permission(self, perm: str) -> None:
        self._permissions.discard(perm)

    def register(
        self, definition: ProbeDefinition, executor: Callable[[dict[str, Any]], dict[str, Any]]
    ) -> None:
        self.definitions[definition.probe_id] = definition
        self.executors[definition.probe_id] = executor

    def _validate(
        self,
        probe_id: str,
        params: dict[str, Any],
        workspace_id: str,
        budget: ProbeBudget,
    ) -> tuple[ProbeDefinition, str]:
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

        # 4. 预算检查（预算按 Run/Diagnosis 实例化，不再是全局计数）
        reason = budget.exhausted_reason()
        if reason:
            code = {
                "PROBE_BUDGET_CLOSED": ErrorCode.PRECONDITION_FAILED,
                "PROBE_TIME_BUDGET_EXHAUSTED": ErrorCode.TIMEOUT,
            }.get(reason, ErrorCode.RATE_LIMITED)
            raise AppError(code, reason)
        if budget.cost_used + d.estimated_cost > budget.max_total_cost:
            raise AppError(ErrorCode.RATE_LIMITED, "PROBE_COST_BUDGET_EXHAUSTED")

        # 5. 参数 Schema 校验
        if not isinstance(params, dict) or not params:
            raise AppError(ErrorCode.VALIDATION_ERROR, "INVALID_PROBE_PARAMS")
        return d, "ALL_CHECKS_PASSED"

    def execute(
        self,
        probe_id: str,
        params: dict[str, Any],
        workspace_id: str,
        *,
        budget: ProbeBudget | None = None,
        reserve: Callable[[ProbeBudget, float], str] | None = None,
    ) -> ProbeResult:
        b = budget or self.budget
        started = time.time()
        try:
            d, decision = self._validate(probe_id, params, workspace_id, b)
        except AppError as e:
            self._audit.append(
                {
                    "probe_id": probe_id,
                    "budget_scope": b.scope_key,
                    "decision": e.code.value,
                    "reason": e.message,
                    "at": time.time(),
                }
            )
            return ProbeResult(
                probe_id,
                "BLOCKED",
                reason=e.message,
                source={"decision": e.code.value, "error_code": e.code.value},
                budget_scope=b.scope_key,
                duration_ms=int((time.time() - started) * 1000),
            )

        try:
            # 有持久化预算时走"跨进程原子占位"；失败必须阻断，不能先跑后记账
            denied = (
                reserve(b, d.estimated_cost)
                if reserve is not None
                else b.try_reserve(d.estimated_cost)
            )
        except BudgetUnavailable as exc:
            self._audit.append(
                {
                    "probe_id": probe_id,
                    "budget_scope": b.scope_key,
                    "decision": "BUDGET_UNAVAILABLE",
                    "reason": exc.detail or exc.message,
                    "at": time.time(),
                }
            )
            return ProbeResult(
                probe_id,
                "BLOCKED",
                reason="PROBE_BUDGET_UNAVAILABLE",
                source={"decision": "BUDGET_UNAVAILABLE", "error_code": exc.message},
                budget_scope=b.scope_key,
                duration_ms=int((time.time() - started) * 1000),
            )
        if denied:  # 并发下仍可能被其他诊断线程抢走预算
            self._audit.append(
                {
                    "probe_id": probe_id,
                    "budget_scope": b.scope_key,
                    "decision": "RATE_LIMITED",
                    "reason": denied,
                    "at": time.time(),
                }
            )
            return ProbeResult(
                probe_id,
                "BLOCKED",
                reason=denied,
                source={"decision": "RATE_LIMITED", "error_code": denied},
                budget_scope=b.scope_key,
            )
        try:
            data = self.executors[probe_id](params)
            # 行数限制
            if "rows" in data and isinstance(data["rows"], list):
                data["rows"] = data["rows"][: d.max_rows]
            redacted_data = redact(data)
            self._audit.append(
                {
                    "probe_id": probe_id,
                    "budget_scope": b.scope_key,
                    "decision": "ALLOW",
                    "workspace": workspace_id,
                    "at": time.time(),
                }
            )
            observed = time.time()
            return ProbeResult(
                probe_id,
                "SUCCEEDED",
                data=redacted_data,
                redacted=True,
                source={
                    "probe_id": probe_id,
                    "template": params.get("template"),
                    "params_keys": sorted(params.keys()),
                    "workspace": workspace_id,
                    "timestamp": time.time(),
                },
                budget_scope=b.scope_key,
                duration_ms=int((time.time() - started) * 1000),
                observed_at=observed,
            )
        except TimeoutError as e:
            self._audit.append(
                {
                    "probe_id": probe_id,
                    "budget_scope": b.scope_key,
                    "decision": "TIMEOUT",
                    "reason": str(e)[:100],
                    "at": time.time(),
                }
            )
            return ProbeResult(
                probe_id,
                "TIMED_OUT",
                reason=str(e)[:200] or "PROBE_TIMEOUT",
                budget_scope=b.scope_key,
                duration_ms=int((time.time() - started) * 1000),
            )
        except Exception as e:  # noqa: BLE001
            self._audit.append(
                {
                    "probe_id": probe_id,
                    "budget_scope": b.scope_key,
                    "decision": "EXEC_ERROR",
                    "reason": str(e)[:100],
                    "at": time.time(),
                }
            )
            return ProbeResult(
                probe_id,
                "FAILED",
                reason=str(e)[:200],
                budget_scope=b.scope_key,
                duration_ms=int((time.time() - started) * 1000),
            )

    def audit_log(self) -> list[dict[str, Any]]:
        return list(self._audit)


# --------------- Planner（EIG 启发式） ---------------
@dataclass
class ScoreWeights:
    w_i: float = 1.0  # EIG 权重（默认，待实验校准）
    w_c: float = 0.3  # 成本权重
    w_r: float = 0.5  # 风险权重
    note: str = "启发式默认权重，归一化与参数仍为待确认事项（P05），非校准概率"


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
        budget: ProbeBudget | None = None,
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
        b = budget or self.engine.budget
        exhausted = b.exhausted_reason()
        if exhausted:
            return None, (
                "STOP_BUDGET_EXHAUSTED" if "BUDGET_EXHAUSTED" in exhausted else "STOP_BUDGET_CLOSED"
            )

        scored = [(self.score(p, eig), p) for p, eig in candidates]
        scored.sort(key=lambda x: x[0], reverse=True)
        best_score, best = scored[0]
        if best_score <= 0:  # 边际信息价值不足
            return None, "STOP_MARGINAL_VALUE_INSUFFICIENT"
        return best, "PROBE_SELECTED"

    def rank(
        self, candidates: list[tuple[ProbeDefinition, float]]
    ) -> list[tuple[float, ProbeDefinition]]:
        return sorted(
            ((self.score(p, eig), p) for p, eig in candidates), key=lambda x: x[0], reverse=True
        )


__all__ = [
    "PROVISIONAL_NOTE",
    "ActivePlanner",
    "BudgetUnavailable",
    "ProbeBudget",
    "ProbeBudgetRegistry",
    "ProbeDefinition",
    "ProbeEngine",
    "ProbeResult",
    "ScoreWeights",
]
