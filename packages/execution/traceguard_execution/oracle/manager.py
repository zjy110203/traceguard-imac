"""Oracle Manager（阶段2）。

职责：管理 Assertion 级 OracleSpec 的加载、来源与审批校验、版本、可用性检查。
约束（架构原则）：Oracle 独立于 Agent；正式 Oracle 必须有审批记录；Agent 不得批准/修改。
P03（多来源优先级/冲突裁决细则）仍待确认：本实现只做"冲突即 unresolved → INCONCLUSIVE"的兜底，
不擅自设定 Primary 覆盖 Supporting 或可信等级。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from traceguard_contracts.models import OracleSpec


@dataclass
class OracleValidation:
    oracle_id: str
    available: bool
    approved: bool
    has_source: bool
    version: int
    issues: list[str]


class OracleManager:
    """从字典/持久化加载 OracleSpec，执行来源与审批校验。"""

    def __init__(self, specs: dict[str, OracleSpec] | None = None) -> None:
        self._specs = dict(specs or {})

    def register(self, spec: OracleSpec) -> None:
        self._specs[spec.oracle_id] = spec

    def get(self, oracle_id: str) -> OracleSpec | None:
        return self._specs.get(oracle_id)

    def validate(self, oracle_id: str) -> OracleValidation:
        spec = self._specs.get(oracle_id)
        if not spec:
            return OracleValidation(
                oracle_id=oracle_id,
                available=False,
                approved=False,
                has_source=False,
                version=0,
                issues=["ORACLE_NOT_FOUND"],
            )
        issues: list[str] = []
        has_source = bool(spec.requirement_ref) and bool(spec.expected_behavior)
        if not has_source:
            issues.append("NO_REQUIREMENT_SOURCE")
        approval = spec.expectation_approval or {}
        approved = bool(approval.get("approved")) and approval.get("approver") not in (
            None,
            "",
            "agent",
        )
        if not approved:
            issues.append("NOT_APPROVED_BY_HUMAN")
        if spec.approved_by_agent:  # 契约层已禁止 True，这里双保险
            issues.append("AGENT_APPROVED_ORACLE_FORBIDDEN")
        available = approved and has_source
        return OracleValidation(
            oracle_id=oracle_id,
            available=available,
            approved=approved,
            has_source=has_source,
            version=spec.version,
            issues=issues,
        )

    def check_conflict(self, oracle_id: str, observed: dict[str, Any]) -> bool:
        """F03/P03 兜底：多来源观测冲突且无正式裁决规则 → 视为 unresolved。
        本方法只检测冲突，不做裁决；裁决细则待确认后实现。"""
        support = observed.get("supporting_oracles", [])
        primary = observed.get("primary_oracle")
        if isinstance(support, list) and primary is not None:
            vals = {str(primary)} | {str(s) for s in support}
            # 存在多个不同结论且未指定裁决规则 → unresolved
            rule = observed.get("conflict_resolution_rule")
            return len(vals) > 1 and not rule
        return False
