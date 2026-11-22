"""Oracle-Protected Self-Healing 服务（持久化版）。

生命周期：DRAFT → VALIDATING → VALIDATED → PENDING_APPROVAL → APPROVED → PROMOTING
          → PROMOTED → ROLLED_BACK（失败分支：VALIDATION_FAILED / PROMOTION_FAILED / REJECTED）

安全约束：
- 只允许修改定位器/等待/数据引用等非业务语义字段（Diff 白名单）；
- Assertion 不得删除或降低严格度；Oracle 绑定不得改变；
- 沙箱验证是**真实再执行**（独立 Run），不是"规则算过"就算过；
- 审批必须是具名人类角色，Agent 禁止自批；
- 晋升使用 CAS：当前正式版本必须等于 base；并发冲突显式失败；
- 回滚保留历史，不删除已发布版本。
"""

from __future__ import annotations

import time
from typing import Any

from traceguard_agents.healing.rules import assertion_preserved, diff_allowed, oracle_integrity_ok
from traceguard_agents.healing.service import REPAIR_TRANSITIONS, RepairStatus
from traceguard_contracts.models import TestCaseVersion
from traceguard_core.errors import AppError, ErrorCode
from traceguard_core.ids import new_id, utc_now_iso
from traceguard_core.logging import get_logger

from traceguard_api.runtime import Runtime

logger = get_logger("traceguard.service.repair")


class RepairService:
    def __init__(self, rt: Runtime) -> None:
        self.rt = rt

    # --------------------------------------------------------------- create
    def propose(
        self,
        *,
        testcase_id: str,
        base_version: int,
        changes: list[dict[str, Any]],
        reason: str = "",
        created_by: str = "agent:repair-planner",
        workspace_id: str | None = None,
    ) -> dict[str, Any]:
        base = self.rt.catalog.get_version(testcase_id, base_version)
        if not base:
            raise AppError(ErrorCode.NOT_FOUND, "base testcase version not found")
        if not changes:
            raise AppError(ErrorCode.VALIDATION_ERROR, "changes 不能为空")

        candidate = _apply_changes(base, changes)
        diff = _build_diff(changes)
        proposal_id = new_id("repair")

        base_tc = _to_tc(base)
        cand_tc = _to_tc(candidate)
        ok_assert = assertion_preserved(base_tc, cand_tc)
        ok_oracle = oracle_integrity_ok(base_tc, cand_tc)
        ok_diff = diff_allowed(diff)

        doc = {
            "proposal_id": proposal_id,
            "testcase_id": testcase_id,
            "workspace_id": workspace_id or self._workspace_of(base),
            "base_testcase_version": f"{testcase_id}:v{base_version}",
            "base_version": base_version,
            "status": "VALIDATED" if (ok_assert and ok_oracle and ok_diff) else "VALIDATION_FAILED",
            "sandbox_status": "NOT_STARTED",
            "assertion_preservation": "PASS" if ok_assert else "FAIL",
            "oracle_integrity": "PASS" if ok_oracle else "FAIL",
            "diff_allowed": ok_diff,
            "diff": diff,
            "reason": reason,
            "created_by": created_by,
            "created_at": utc_now_iso(),
            "approval_ref": None,
            "sandbox_run_id": None,
            "promotion_record": {},
            "failure_reason": ""
            if (ok_assert and ok_oracle and ok_diff)
            else "RULE_VALIDATION_FAILED",
            "candidate_doc": candidate,
            "base_doc": base,
        }
        self.rt.docs.put(
            doc_id=proposal_id,
            kind="repair",
            doc=doc,
            workspace_id=doc["workspace_id"],
            ref_id=testcase_id,
            status=doc["status"],
        )
        return doc

    def get(self, proposal_id: str) -> dict[str, Any]:
        rec = self.rt.docs.get(proposal_id)
        if not rec or rec.kind != "repair":
            raise AppError(ErrorCode.NOT_FOUND, "repair proposal not found")
        return rec.to_dict()

    def list(
        self, workspace_id: str | None = None, status: str | None = None
    ) -> list[dict[str, Any]]:
        recs = self.rt.docs.list(kind="repair", workspace_id=workspace_id, status=status, limit=200)
        return [_summary(r.to_dict()) for r in recs]

    # ------------------------------------------------------------- lifecycle
    def validate(self, proposal_id: str) -> dict[str, Any]:
        doc = self.get(proposal_id)
        if doc["status"] in {"VALIDATED", "VALIDATION_FAILED"}:
            return doc
        self._transition(doc, RepairStatus.VALIDATING)
        base_tc = _to_tc(doc["base_doc"])
        cand_tc = _to_tc(doc["candidate_doc"])
        ok = (
            assertion_preserved(base_tc, cand_tc)
            and oracle_integrity_ok(base_tc, cand_tc)
            and diff_allowed(doc["diff"])
        )
        doc["assertion_preservation"] = "PASS" if assertion_preserved(base_tc, cand_tc) else "FAIL"
        doc["oracle_integrity"] = "PASS" if oracle_integrity_ok(base_tc, cand_tc) else "FAIL"
        doc["diff_allowed"] = diff_allowed(doc["diff"])
        if ok:
            self._transition(doc, RepairStatus.VALIDATED)
        else:
            doc["failure_reason"] = "RULE_VALIDATION_FAILED"
            self._transition(doc, RepairStatus.VALIDATION_FAILED)
        return self._save(doc)

    def sandbox_run(self, proposal_id: str) -> dict[str, Any]:
        """真实沙箱验证：把候选版本作为 PENDING sandbox 用例再执行一次。"""
        doc = self.get(proposal_id)
        if doc["status"] != "VALIDATED":
            raise AppError(ErrorCode.PRECONDITION_FAILED, "只有 RULE_VALIDATED 的提案可沙箱验证")
        candidate = dict(doc["candidate_doc"])
        candidate["review_status"] = "PENDING"
        rc = dict(candidate.get("run_config") or {})
        rc["sandbox_only"] = True
        candidate["run_config"] = rc
        candidate["compile_source"] = "healed"
        created = self.rt.catalog.create_testcase(
            candidate,
            title=f"sandbox-{doc['testcase_id']}",
            workspace_id=doc["workspace_id"],
            review_status="PENDING",
        )
        version = created["version"]
        task = self.rt.tasks.create_task(
            doc["testcase_id"],
            f"repair-sandbox-{proposal_id}-{time.time_ns()}",
            testcase_version_id=f"{doc['testcase_id']}@{version}",
        )
        run = self.rt.tasks.create_run(task["task_id"], f"{doc['testcase_id']}@{version}")
        outcome = self.rt.execute_run(run["run_id"])
        doc["sandbox_run_id"] = outcome["run_id"]
        doc["sandbox_status"] = "PASSED" if outcome["test_verdict"] == "PASSED" else "FAILED"
        doc["sandbox_verdict"] = outcome["test_verdict"]
        doc["sandbox_observer_mode"] = outcome["observer_mode"]
        if outcome["test_verdict"] != "PASSED":
            doc["failure_reason"] = f"SANDBOX_{outcome['test_verdict']}"
        doc["candidate_version"] = version
        return self._save(doc)

    def submit_for_approval(self, proposal_id: str) -> dict[str, Any]:
        doc = self.get(proposal_id)
        if doc["sandbox_status"] != "PASSED":
            raise AppError(
                ErrorCode.PRECONDITION_FAILED,
                "沙箱验证未通过：不得提交审批（不能用规则校验冒充真实回归）",
            )
        self._transition(doc, RepairStatus.PENDING_APPROVAL)
        return self._save(doc)

    def approve(self, proposal_id: str, approver: str, note: str = "") -> dict[str, Any]:
        if not approver or any(x in approver.lower() for x in ("agent", "llm")):
            raise AppError(ErrorCode.AUTHORIZATION_ERROR, "AGENT_CANNOT_SELF_APPROVE_REPAIR")
        doc = self.get(proposal_id)
        self._transition(doc, RepairStatus.APPROVED)
        doc["approval_ref"] = f"approval:{proposal_id}:{approver}:{utc_now_iso()}:{note}".rstrip(
            ":"
        )
        doc["approved_by"] = approver
        return self._save(doc)

    def reject(
        self, proposal_id: str, reviewer: str = "human:qa", note: str = ""
    ) -> dict[str, Any]:
        doc = self.get(proposal_id)
        self._transition(doc, RepairStatus.REJECTED)
        doc["rejected_by"] = reviewer
        doc["reject_note"] = note
        return self._save(doc)

    def promote(self, proposal_id: str, *, idempotency_key: str) -> dict[str, Any]:
        doc = self.get(proposal_id)
        if (
            doc["status"] == "PROMOTED"
            and doc.get("promotion_record", {}).get("idempotency_key") == idempotency_key
        ):
            return doc  # 幂等重放
        self._transition(doc, RepairStatus.PROMOTING)

        latest = self.rt.catalog.latest_approved_version(doc["testcase_id"])
        current_version = latest["version"] if latest else 0
        if current_version != doc["base_version"]:
            doc["failure_reason"] = "VERSION_CONFLICT"
            doc["promotion_record"] = {
                "idempotency_key": idempotency_key,
                "result": "VERSION_CONFLICT",
                "current_version": current_version,
                "base_version": doc["base_version"],
                "at": utc_now_iso(),
            }
            self._transition(doc, RepairStatus.PROMOTION_FAILED)
            return self._save(doc)

        candidate = dict(doc["candidate_doc"])
        candidate["version"] = current_version + 1
        created = self.rt.catalog.create_testcase(
            candidate,
            title=f"{doc['testcase_id']} (healed)",
            workspace_id=doc["workspace_id"],
            review_status="APPROVED",
        )
        doc["promotion_record"] = {
            "idempotency_key": idempotency_key,
            "target_version": created["version"],
            "operator": "system:promotion",
            "check": "CAS_OK",
            "at": utc_now_iso(),
        }
        doc["promoted_version"] = created["version"]
        self._transition(doc, RepairStatus.PROMOTED)
        return self._save(doc)

    def rollback(self, proposal_id: str, operator: str, reason: str) -> dict[str, Any]:
        doc = self.get(proposal_id)
        if doc["status"] != "PROMOTED":
            raise AppError(ErrorCode.PRECONDITION_FAILED, "只有 PROMOTED 提案可回滚")
        base = doc["base_doc"]
        latest = self.rt.catalog.latest_approved_version(doc["testcase_id"])
        next_version = (latest["version"] if latest else doc["base_version"]) + 1
        restored = dict(base)
        restored["version"] = next_version
        created = self.rt.catalog.create_testcase(
            restored,
            title=f"{doc['testcase_id']} (rolled back)",
            workspace_id=doc["workspace_id"],
            review_status="APPROVED",
        )
        doc["rollback_record"] = {
            "target_version": f"{doc['testcase_id']}:v{next_version}",
            "operator": operator,
            "reason": reason,
            "at": utc_now_iso(),
            "restored_version": created["version"],
        }
        self._transition(doc, RepairStatus.ROLLED_BACK)
        return self._save(doc)

    # --------------------------------------------------------------- helpers
    def _transition(self, doc: dict[str, Any], target: RepairStatus) -> None:
        current = RepairStatus(doc["status"])
        if target not in REPAIR_TRANSITIONS.get(current, set()):
            raise AppError(
                ErrorCode.CONFLICT, f"illegal repair transition {current.value}->{target.value}"
            )
        doc["status"] = target.value

    def _save(self, doc: dict[str, Any]) -> dict[str, Any]:
        return self.rt.docs.put(
            doc_id=doc["proposal_id"],
            kind="repair",
            doc=doc,
            workspace_id=doc["workspace_id"],
            ref_id=doc["testcase_id"],
            status=doc["status"],
        ).to_dict()

    @staticmethod
    def _workspace_of(base_doc: dict[str, Any]) -> str:
        return str(base_doc.get("workspace_id") or "default")


def _to_tc(doc: dict[str, Any]) -> TestCaseVersion:
    allowed = set(TestCaseVersion.model_fields)
    return TestCaseVersion(**{k: v for k, v in doc.items() if k in allowed})


def _step_index(path: str, change: dict[str, Any]) -> int | None:
    if "step_index" in change:
        return int(change["step_index"])
    if path.startswith("steps[") and "]" in path:
        try:
            return int(path[6 : path.index("]")])
        except ValueError:
            return None
    return None


def _apply_changes(base: dict[str, Any], changes: list[dict[str, Any]]) -> dict[str, Any]:
    """只允许白名单字段变更；任何越界字段直接拒绝（防止偷偷改断言）。"""
    candidate = {
        k: (v.copy() if isinstance(v, dict) else list(v) if isinstance(v, list) else v)
        for k, v in base.items()
    }
    candidate["steps"] = [dict(s) for s in base.get("steps", [])]
    candidate["assertions"] = [dict(a) for a in base.get("assertions", [])]
    candidate["run_config"] = dict(base.get("run_config") or {})

    for change in changes:
        path = str(change.get("path", ""))
        if not diff_allowed([change]):
            raise AppError(
                ErrorCode.PRECONDITION_FAILED,
                f"FORBIDDEN_CHANGE_PATH: {path}（禁止修改断言/Oracle/业务语义）",
            )
        if path.startswith("steps["):
            idx = _step_index(path, change)
            if idx is None or idx >= len(candidate["steps"]):
                raise AppError(ErrorCode.VALIDATION_ERROR, f"step index out of range: {path}")
            if path.endswith("target"):
                candidate["steps"][idx]["target"] = change.get("value")
            elif path.endswith("value"):
                candidate["steps"][idx]["value"] = change.get("value")
            elif path.endswith("action_config"):
                step = candidate["steps"][idx]
                cfg = dict(step.get("action_config") or {})
                cfg.update(change.get("value") or {})
                step["action_config"] = cfg
        elif path == "run_config.wait_timeout":
            candidate["run_config"]["wait_timeout"] = change.get("value")
        elif path == "test_data_refs":
            candidate["test_data_refs"] = list(change.get("value") or [])
        else:
            raise AppError(ErrorCode.PRECONDITION_FAILED, f"UNSUPPORTED_CHANGE_PATH: {path}")
    candidate["compile_source"] = "healed"
    return candidate


def _build_diff(changes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for c in changes:
        out.append(
            {
                "path": c.get("path"),
                "from": c.get("from"),
                "to": c.get("value"),
                "step_index": _step_index(str(c.get("path", "")), c),
                "reason": c.get("reason", ""),
            }
        )
    return out


def _summary(doc: dict[str, Any]) -> dict[str, Any]:
    return {
        "proposal_id": doc["proposal_id"],
        "testcase_id": doc["testcase_id"],
        "base_version": doc.get("base_version"),
        "status": doc["status"],
        "sandbox_status": doc.get("sandbox_status"),
        "assertion_preservation": doc.get("assertion_preservation"),
        "oracle_integrity": doc.get("oracle_integrity"),
        "promoted_version": doc.get("promoted_version"),
        "created_at": doc.get("created_at"),
        "reason": doc.get("reason", ""),
    }
