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
from traceguard_persistence.catalog import require_human_actor

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

    # --------------------------------------------------------- LLM 候选生成
    def llm_suggest(
        self,
        *,
        testcase_id: str,
        base_version: int,
        run_id: str | None = None,
        diagnosis_id: str | None = None,
        propose: bool = False,
    ) -> dict[str, Any]:
        """用 LLM 生成受限修复候选（不自动审批、不自动晋升）。

        模型输出先经过本地白名单过滤，才交给 `propose()`；`propose()` 仍会执行
        Diff Allowlist / Assertion Preservation / Oracle Integrity 校验。
        """
        base = self.rt.catalog.get_version(testcase_id, base_version)
        if not base:
            raise AppError(ErrorCode.NOT_FOUND, "base testcase version not found")
        if not getattr(self.rt.settings, "llm_repair_planner_enabled", False):
            return {"called": False, "reason": "disabled", "changes": []}
        from traceguard_agents.gateway.llm_gateway import build_llm_stack
        from traceguard_agents.gateway.llm_planners import LLMRepairAdvisor

        stack = build_llm_stack(self.rt.settings, root_dir=".")
        if not stack:
            return {"called": False, "reason": "not_configured", "changes": []}

        symptom = ""
        if diagnosis_id:
            rec = self.rt.docs.get(diagnosis_id)
            symptom = str(((rec.doc if rec else {}) or {}).get("symptom", ""))
        elif run_id:
            run = self.rt.tasks.get_run(run_id) or {}
            errors = ((run.get("report") or {}).get("errors") or [])[:3]
            symptom = " | ".join(str(e)[:300] for e in errors)
        advisor = LLMRepairAdvisor(
            provider=stack["provider"],
            router=stack["router"],
            prompts=stack["prompts"],
            tracker=stack.get("tracker"),
            run_id=run_id or "",
        )
        advice = advisor.suggest(
            symptom=symptom,
            steps=base.get("steps") or [],
            assertions=base.get("assertions") or [],
            step_count=len(base.get("steps") or []),
        )
        out: dict[str, Any] = {
            "called": advice.called,
            "fallback": advice.fallback,
            "error": advice.error,
            "model": advice.model,
            "prompt_version": "repair_planner@v1",
            "changes": advice.changes,
            "warnings": advice.warnings,
            "note": "LLM 仅生成候选；必须经校验/沙箱/人工审批/CAS 才可能晋升",
        }
        if propose and advice.changes:
            out["proposal"] = self.propose(
                testcase_id=testcase_id,
                base_version=base_version,
                changes=advice.changes,
                reason=f"LLM 修复候选（{advice.model}）",
                created_by="agent:llm-repair-planner",
            )
        return out

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
        """真实沙箱验证：把候选版本作为 PENDING sandbox 用例再执行一次。

        RUN-007：沙箱副本使用**独立 testcase_id**，不在正式用例上追加 PENDING 版本——
        否则会污染正式用例的版本线（seed/审核门禁都会被这个"影子版本"干扰）。
        """
        doc = self.get(proposal_id)
        if doc["status"] != "VALIDATED":
            raise AppError(ErrorCode.PRECONDITION_FAILED, "只有 RULE_VALIDATED 的提案可沙箱验证")
        candidate = dict(doc["candidate_doc"])
        candidate["testcase_id"] = new_id("tcase")
        candidate["version"] = 1
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
        sandbox_case_id = created["testcase_id"]
        version = created["version"]
        task = self.rt.tasks.create_task(
            sandbox_case_id,
            f"repair-sandbox-{proposal_id}-{time.time_ns()}",
            testcase_version_id=f"{sandbox_case_id}@{version}",
            mode="sandbox",
        )
        run = self.rt.tasks.create_run(task["task_id"], f"{sandbox_case_id}@{version}")
        outcome = self.rt.execute_run(run["run_id"])
        doc["sandbox_run_id"] = outcome["run_id"]
        doc["sandbox_testcase_id"] = sandbox_case_id
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
        # 主体类型校验（拒绝 agent:*/llm:*/system:*），而不是"包含 agent 字样"的模糊匹配
        actor = require_human_actor(approver, field="approver")
        doc = self.get(proposal_id)
        self._transition(doc, RepairStatus.APPROVED)
        doc["approval_ref"] = f"approval:{proposal_id}:{actor}:{utc_now_iso()}:{note}".rstrip(":")
        doc["approved_by"] = actor
        return self._save(doc)

    def reject(self, proposal_id: str, reviewer: str, note: str = "") -> dict[str, Any]:
        """拒绝也必须由人类主体发起（RUN-007 WP1-03：统一主体类型校验）。"""
        actor = require_human_actor(reviewer, field="rejected_by")
        doc = self.get(proposal_id)
        self._transition(doc, RepairStatus.REJECTED)
        doc["rejected_by"] = actor
        doc["reject_note"] = note
        doc["reject_history"] = list(doc.get("reject_history") or []) + [
            {"reviewer": actor, "note": note, "at": utc_now_iso()}
        ]
        return self._save(doc)

    # ------------------------------------------------------------- promotion
    def promote(self, proposal_id: str, *, idempotency_key: str) -> dict[str, Any]:
        """原子晋升 + 幂等回执 + 崩溃恢复。

        崩溃窗口与处理方式：
        - CAS 提交前崩溃 → 无回执、提案停在 PROMOTING → 重试直接重做 CAS（允许 PROMOTING 重入）；
        - CAS 提交后、提案保存前崩溃 → 有回执、提案停在 PROMOTING → 重试命中回执，
          用回执里的 target_version 确定性恢复为 PROMOTED，**不会**误报 VERSION_CONFLICT。
        """
        if not idempotency_key:
            raise AppError(ErrorCode.VALIDATION_ERROR, "idempotency_key 不能为空")
        doc = self.get(proposal_id)

        # 1) 幂等回执命中（含"上一进程 CAS 已提交但没来得及保存提案"的崩溃恢复）
        receipt = self._match_receipt(proposal_id, idempotency_key, kind="PROMOTE")
        if receipt is not None:
            return self._settle_promoted(doc, receipt, idempotency_key, recovered=True)

        status = RepairStatus(doc["status"])
        if status is RepairStatus.PROMOTED:
            # 已晋升但没有回执（历史数据）：补一条回执，保持幂等语义
            receipt = {
                "target_version": int(doc.get("promoted_version") or 0),
                "base_version": int(doc["base_version"]),
            }
            return self._settle_promoted(doc, receipt, idempotency_key, recovered=True)
        if status is not RepairStatus.APPROVED and status is not RepairStatus.PROMOTING:
            raise AppError(
                ErrorCode.CONFLICT,
                f"illegal repair transition {status.value}->PROMOTING",
            )
        if status is RepairStatus.APPROVED:
            self._transition(doc, RepairStatus.PROMOTING)
        else:
            doc["promotion_retry"] = int(doc.get("promotion_retry") or 0) + 1
        self._save(doc)  # 先把 PROMOTING 落库，崩溃后从这里恢复

        # 2) 比较 + 写入 + 回执 在同一个数据库事务内完成
        try:
            created = self.rt.catalog.promote_version_cas(
                doc["testcase_id"],
                base_version=int(doc["base_version"]),
                candidate=doc["candidate_doc"],
                workspace_id=doc["workspace_id"],
                title=f"{doc['testcase_id']} (healed)",
                idempotency_key=idempotency_key,
                proposal_id=proposal_id,
            )
        except AppError as exc:
            if exc.code is not ErrorCode.CONFLICT:
                raise
            current = self.rt.catalog.approved_version_number(doc["testcase_id"])
            doc["failure_reason"] = "VERSION_CONFLICT"
            doc["promotion_record"] = {
                **(doc.get("promotion_record") or {}),
                "idempotency_key": idempotency_key,
                "result": "VERSION_CONFLICT",
                "current_version": current,
                "base_version": doc["base_version"],
                "detail": exc.message,
                "at": utc_now_iso(),
            }
            self._transition(doc, RepairStatus.PROMOTION_FAILED)
            return self._save(doc)

        receipt = {
            "target_version": created.get("version"),
            "base_version": int(doc["base_version"]),
        }
        return self._settle_promoted(doc, receipt, idempotency_key, recovered=False)

    def _match_receipt(
        self, proposal_id: str, idempotency_key: str, *, kind: str
    ) -> dict[str, Any] | None:
        """幂等键命中，或同一提案的同类回执命中（不同幂等键也不重复创建版本）。"""
        receipt = self.rt.catalog.get_promotion_receipt(idempotency_key)
        if receipt is None:
            receipt = self.rt.catalog.find_promotion_receipt(proposal_id, kind=kind)
        if receipt is None or receipt.get("kind") != kind:
            return None
        return receipt

    def _settle_promoted(
        self,
        doc: dict[str, Any],
        receipt: dict[str, Any],
        idempotency_key: str,
        *,
        recovered: bool,
    ) -> dict[str, Any]:
        target = int(receipt.get("target_version") or 0)
        if not target:
            raise AppError(ErrorCode.CONFLICT, "晋升回执缺少 target_version，无法恢复")
        doc["promotion_record"] = {
            **(doc.get("promotion_record") or {}),
            "idempotency_key": idempotency_key,
            "target_version": target,
            "operator": "system:promotion",
            "check": "CAS_OK",
            "atomic": True,
            "receipt_recovered": recovered,
            "at": (doc.get("promotion_record") or {}).get("at") or utc_now_iso(),
        }
        doc["promoted_version"] = target
        if RepairStatus(doc["status"]) is RepairStatus.PROMOTING:
            self._transition(doc, RepairStatus.PROMOTED)
        return self._save(doc)

    def reconcile(self, proposal_id: str) -> dict[str, Any]:
        """确定性恢复：把卡在 PROMOTING 的提案按已提交回执修复到 PROMOTED。"""
        doc = self.get(proposal_id)
        if doc["status"] not in {"PROMOTING", "PROMOTED"}:
            return doc
        receipt = self.rt.catalog.find_promotion_receipt(proposal_id, kind="PROMOTE")
        if receipt is None:
            # 没有回执说明 CAS 没提交：回到 APPROVED，允许重新晋升
            if doc["status"] == "PROMOTING":
                doc["status"] = RepairStatus.APPROVED.value
                doc["failure_reason"] = "PROMOTION_NOT_COMMITTED"
                return self._save(doc)
            return doc
        return self._settle_promoted(doc, receipt, receipt["idempotency_key"], recovered=True)

    def rollback(
        self,
        proposal_id: str,
        operator: str,
        reason: str,
        *,
        idempotency_key: str = "",
    ) -> dict[str, Any]:
        """回滚：同样具备幂等回执与崩溃恢复（RUN-007 WP1-02）。"""
        doc = self.get(proposal_id)
        # 主体校验前置：任何回滚请求都必须先证明"是具名人类"，再谈状态是否允许
        actor = require_human_actor(operator, field="operator")
        promoted_version = int(doc.get("promoted_version") or 0)
        if not promoted_version:
            raise AppError(ErrorCode.PRECONDITION_FAILED, "提案缺少 promoted_version，无法回滚")
        key = idempotency_key or f"rollback:{proposal_id}:v{promoted_version}"
        receipt = self._match_receipt(proposal_id, key, kind="ROLLBACK")
        if receipt is not None:
            return self._settle_rolled_back(doc, receipt, operator, reason, recovered=True)

        if doc["status"] != "PROMOTED":
            raise AppError(ErrorCode.PRECONDITION_FAILED, "只有 PROMOTED 提案可回滚")
        base = doc["base_doc"]
        try:
            created = self.rt.catalog.rollback_version_cas(
                doc["testcase_id"],
                promoted_version=promoted_version,
                restored=base,
                workspace_id=doc["workspace_id"],
                title=f"{doc['testcase_id']} (rolled back)",
                idempotency_key=key,
                proposal_id=proposal_id,
            )
        except AppError as exc:
            if exc.code is not ErrorCode.CONFLICT:
                raise
            # 当前正式版本已被他人推进：不允许用旧提案覆盖
            raise AppError(
                ErrorCode.CONFLICT,
                f"ROLLBACK_CONFLICT: 当前正式版本已不是本提案晋升的 v{promoted_version}",
            ) from exc
        receipt = {
            "target_version": created.get("version"),
            "base_version": promoted_version,
        }
        return self._settle_rolled_back(doc, receipt, actor, reason, recovered=False)

    def _settle_rolled_back(
        self,
        doc: dict[str, Any],
        receipt: dict[str, Any],
        operator: str,
        reason: str,
        *,
        recovered: bool,
    ) -> dict[str, Any]:
        actor = require_human_actor(operator, field="operator")
        restored_version = int(receipt.get("target_version") or 0)
        doc["rollback_record"] = {
            "target_version": f"{doc['testcase_id']}:v{receipt.get('base_version')}",
            "restored_version": restored_version,
            "operator": actor,
            "reason": reason,
            "receipt_recovered": recovered,
            "at": (doc.get("rollback_record") or {}).get("at") or utc_now_iso(),
        }
        doc["promotion_record"] = {
            **(doc.get("promotion_record") or {}),
            "rollback": doc["rollback_record"],
        }
        if RepairStatus(doc["status"]) is RepairStatus.PROMOTED:
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
