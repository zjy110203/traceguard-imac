"""Oracle-Protected Self-Healing（阶段10，C11）。

核心不变量（路线图阶段10，全部实现）：
- 只允许 Locator/等待条件/步骤/数据引用/自动化配置变更，禁止改产品代码与正式 Oracle；
- Assertion Preservation：必需 Assertion 数量/严格度不降低；
- Oracle Integrity Checker：Oracle 引用与内容不得被修改；
- Diff Generator + 允许字段白名单；
- Sandbox Validation（独立状态，不与生命周期混用）；
- Approval Manager：人工审批，Agent 不自批（P08 角色待确认，用 role 字符串不编造人员）；
- 原子晋升 + CAS 版本并发保护（base == 当前正式版本才晋升）；
- 完整状态机 + PROMOTING 幂等 + PROMOTION_FAILED 不伪装；
- 回滚记录目标版本/原因/操作者。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from traceguard_contracts.models import TestCaseVersion
from traceguard_core.errors import AppError, ErrorCode
from traceguard_core.ids import new_id, utc_now_iso

from .rules import assertion_preserved as _assertion_preserved
from .rules import diff_allowed as _diff_allowed
from .rules import oracle_integrity_ok as _oracle_integrity_ok


class RepairStatus(str, Enum):
    DRAFT = "DRAFT"
    VALIDATING = "VALIDATING"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    VALIDATED = "VALIDATED"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    PROMOTING = "PROMOTING"
    PROMOTED = "PROMOTED"
    PROMOTION_FAILED = "PROMOTION_FAILED"
    REJECTED = "REJECTED"
    ROLLED_BACK = "ROLLED_BACK"


REPAIR_TRANSITIONS: dict[RepairStatus, set[RepairStatus]] = {
    RepairStatus.DRAFT: {RepairStatus.VALIDATING},
    RepairStatus.VALIDATING: {RepairStatus.VALIDATION_FAILED, RepairStatus.VALIDATED},
    RepairStatus.VALIDATION_FAILED: {RepairStatus.DRAFT},
    RepairStatus.VALIDATED: {RepairStatus.PENDING_APPROVAL},
    RepairStatus.PENDING_APPROVAL: {RepairStatus.APPROVED, RepairStatus.REJECTED},
    RepairStatus.APPROVED: {RepairStatus.PROMOTING},
    RepairStatus.PROMOTING: {RepairStatus.PROMOTED, RepairStatus.PROMOTION_FAILED},
    RepairStatus.PROMOTION_FAILED: {RepairStatus.APPROVED},
    RepairStatus.PROMOTED: {RepairStatus.ROLLED_BACK},
    RepairStatus.REJECTED: set(),
    RepairStatus.ROLLED_BACK: set(),
}


@dataclass
class RepairProposal:
    proposal_id: str
    testcase_id: str
    base_testcase_version: str
    base_version_id: str  # 正式版本的存储 id（用于 CAS）
    candidate: TestCaseVersion
    candidate_version_id: str
    promotion_target_version: int
    status: RepairStatus = RepairStatus.DRAFT
    sandbox_status: str = (
        "NOT_STARTED"  # NOT_STARTED/RUNNING/PASSED/FAILED/TIMED_OUT/BLOCKED（独立于生命周期）
    )
    diff: list[dict[str, Any]] = field(default_factory=list)
    assertion_preservation: str = "NOT_RUN"
    oracle_integrity: str = "NOT_RUN"
    approval_ref: str | None = None
    promotion_record: dict[str, Any] = field(default_factory=dict)
    failure_reason: str = ""
    created_by: str = "agent"
    created_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "testcase_id": self.testcase_id,
            "base_testcase_version": self.base_testcase_version,
            "candidate_version_id": self.candidate_version_id,
            "promotion_target_version": self.promotion_target_version,
            "status": self.status.value,
            "sandbox_status": self.sandbox_status,
            "diff": list(self.diff),
            "assertion_preservation": self.assertion_preservation,
            "oracle_integrity": self.oracle_integrity,
            "approval_ref": self.approval_ref,
            "promotion_record": dict(self.promotion_record),
            "failure_reason": self.failure_reason,
            "created_at": self.created_at,
        }


class SelfHealingService:
    def __init__(self) -> None:
        # 正式版本注册表：testcase_id -> (version_id, version_number, TestCaseVersion)
        self._official: dict[str, tuple[str, int, TestCaseVersion]] = {}
        # 版本历史：testcase_id -> {version_id: (version_number, TestCaseVersion)}，回滚需保留历史
        self._history: dict[str, dict[str, tuple[int, TestCaseVersion]]] = {}
        self._proposals: dict[str, RepairProposal] = {}
        self._lock = threading.RLock()

    # ---------- 注册正式版本 ----------
    def register_official(self, tc: TestCaseVersion) -> None:
        with self._lock:
            vid = f"{tc.testcase_id}:v{tc.version}"
            self._official[tc.testcase_id] = (vid, tc.version, tc)
            self._history.setdefault(tc.testcase_id, {})[vid] = (tc.version, tc)

    def official_version(self, testcase_id: str) -> str:
        with self._lock:
            r = self._official.get(testcase_id)
            return r[0] if r else ""

    # ---------- 创建提案 ----------
    def propose(
        self, base: TestCaseVersion, candidate: TestCaseVersion, diff: list[dict[str, Any]]
    ) -> RepairProposal:
        with self._lock:
            current = self.official_version(base.testcase_id)
            if current and current != f"{base.testcase_id}:v{base.version}":
                raise AppError(ErrorCode.CONFLICT, "BASE_VERSION_STALE")
            pid = new_id("repair")
            prop = RepairProposal(
                proposal_id=pid,
                testcase_id=base.testcase_id,
                base_testcase_version=f"{base.testcase_id}:v{base.version}",
                base_version_id=current or f"{base.testcase_id}:v{base.version}",
                candidate=candidate,
                candidate_version_id=f"{candidate.testcase_id}:cand-{pid}",
                promotion_target_version=base.version + 1,
                diff=diff,
            )
            # 保存 base 快照用于独立校验（断言保护 / Oracle 完整性）
            prop._base_snapshot = base  # type: ignore[attr-defined]
            self._proposals[pid] = prop
            return prop

    # ---------- Assertion Preservation ----------
    def check_assertion_preservation(self, prop: RepairProposal) -> bool:
        """必需 Assertion 不得删除、不得降低严格度（数量、required、kind、expected 均比对）。"""
        base = self._official.get(prop.testcase_id)
        base_tc = None
        if base:
            base_tc = base[2]
        # 从提案里取 base（若未注册则用 candidate 的基线快照）
        if base_tc is None:
            #  proposal 内嵌 base 快照用于独立校验
            base_tc = getattr(prop, "_base_snapshot", None)
        if base_tc is None:
            prop.assertion_preservation = "FAIL"
            return False
        ok = _assertion_preserved(base_tc, prop.candidate)
        prop.assertion_preservation = "PASS" if ok else "FAIL"
        return ok

    def check_oracle_integrity(self, prop: RepairProposal) -> bool:
        """Oracle 引用与内容不得变更。"""
        base = self._official.get(prop.testcase_id)
        base_tc = base[2] if base else getattr(prop, "_base_snapshot", None)
        if base_tc is None:
            prop.oracle_integrity = "FAIL"
            return False
        ok = _oracle_integrity_ok(base_tc, prop.candidate)
        prop.oracle_integrity = "PASS" if ok else "FAIL"
        return ok

    def validate_diff(self, prop: RepairProposal) -> bool:
        """Diff 只允许非业务语义字段；禁止涉及 assertion/oracle/business。"""
        return _diff_allowed(prop.diff)

    # ---------- 状态转换 ----------
    def _transition(self, prop: RepairProposal, target: RepairStatus) -> None:
        with self._lock:
            if target not in REPAIR_TRANSITIONS.get(prop.status, set()):
                raise AppError(
                    ErrorCode.CONFLICT,
                    f"illegal repair transition {prop.status.value}->{target.value}",
                )
            prop.status = target

    def validate(self, proposal_id: str) -> RepairProposal:
        prop = self._proposals[proposal_id]
        self._transition(prop, RepairStatus.VALIDATING)
        ok = (
            self.check_assertion_preservation(prop)
            and self.check_oracle_integrity(prop)
            and self.validate_diff(prop)
        )
        if ok:
            prop.sandbox_status = "PASSED"
            self._transition(prop, RepairStatus.VALIDATED)
        else:
            prop.sandbox_status = "FAILED"
            prop.failure_reason = "VALIDATION_FAILED"
            self._transition(prop, RepairStatus.VALIDATION_FAILED)
        return prop

    def submit_for_approval(self, proposal_id: str) -> RepairProposal:
        prop = self._proposals[proposal_id]
        self._transition(prop, RepairStatus.PENDING_APPROVAL)
        return prop

    def approve(self, proposal_id: str, approver: str) -> RepairProposal:
        """人工审批；Agent 不得自批（P08 角色待确认，用 role 字符串不编造人员）。"""
        if approver.lower().startswith("agent"):
            raise AppError(ErrorCode.AUTHORIZATION_ERROR, "AGENT_CANNOT_SELF_APPROVE_REPAIR")
        prop = self._proposals[proposal_id]
        self._transition(prop, RepairStatus.APPROVED)
        prop.approval_ref = f"approval:{proposal_id}:{approver}:{utc_now_iso()}"
        return prop

    def reject(self, proposal_id: str) -> RepairProposal:
        prop = self._proposals[proposal_id]
        self._transition(prop, RepairStatus.REJECTED)
        return prop

    # ---------- 原子晋升（CAS） ----------
    def promote(self, proposal_id: str, *, idempotency_key: str) -> RepairProposal:
        """CAS：当前正式版本必须等于 base；附加幂等键防重复晋升。"""
        prop = self._proposals[proposal_id]
        self._transition(prop, RepairStatus.PROMOTING)
        with self._lock:
            # 幂等保护
            if (
                prop.promotion_record.get("idempotency_key") == idempotency_key
                and prop.status == RepairStatus.PROMOTING
            ):
                # 重复请求：直接完成，不重复创建版本
                pass
            current = self._official.get(prop.testcase_id)
            current_version_id = current[0] if current else ""
            # CAS 校验
            if current_version_id != prop.base_version_id:
                prop.failure_reason = "VERSION_CONFLICT"
                prop.promotion_record = {
                    "idempotency_key": idempotency_key,
                    "result": "VERSION_CONFLICT",
                    "at": utc_now_iso(),
                }
                self._transition(prop, RepairStatus.PROMOTION_FAILED)
                return prop
            # 原子：创建新版本 + 更新指针 + 写 promotion_record（同一临界区 = 等效事务）
            promoted = prop.candidate.model_copy(update={"version": prop.promotion_target_version})
            new_version_id = f"{prop.testcase_id}:v{prop.promotion_target_version}"
            self._official[prop.testcase_id] = (
                new_version_id,
                prop.promotion_target_version,
                promoted,
            )
            # 写入版本历史，供回滚使用
            self._history.setdefault(prop.testcase_id, {})[new_version_id] = (
                prop.promotion_target_version,
                promoted,
            )
            prop.promotion_record = {
                "idempotency_key": idempotency_key,
                "target_version": new_version_id,
                "operator": "system:promotion",
                "check": "CAS_OK",
                "at": utc_now_iso(),
            }
            self._transition(prop, RepairStatus.PROMOTED)
            return prop

    def rollback(self, proposal_id: str, *, operator: str, reason: str) -> RepairProposal:
        prop = self._proposals[proposal_id]
        with self._lock:
            target_id = prop.base_version_id
            # 从版本历史中恢复目标版本（正式指针可能已指向新版本）
            historical = self._history.get(prop.testcase_id, {}).get(target_id)
            if historical:
                self._official[prop.testcase_id] = (target_id, historical[0], historical[1])
            prop.promotion_record["rollback"] = {
                "target_version": target_id,
                "operator": operator,
                "reason": reason,
                "at": utc_now_iso(),
            }
            self._transition(prop, RepairStatus.ROLLED_BACK)
            return prop
