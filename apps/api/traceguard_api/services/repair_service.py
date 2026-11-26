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

from traceguard_api.runtime import TERMINAL_RUN_STATUSES, Runtime

logger = get_logger("traceguard.service.repair")

#: 服务端可信的确定性规则来源（RUN-011-R1.1 WP3 §3.2）。
#: 只有"变更路径符合该规则契约"时才允许自称 `rule:<name>`，杜绝任意伪造。
RULE_SOURCE_CONTRACTS: dict[str, dict[str, Any]] = {
    "locator-fixture": {
        "description": "仅允许修改步骤定位器/等待/动作配置（steps[].target|value|action_config）",
        "path_suffixes": ("target", "value", "action_config"),
        "max_changes": 8,
    },
    "llm-repair-fallback": {
        "description": "LLM Advisor 未成功返回时的确定性回退规则（同样只允许步骤定位/等待/动作配置）",
        "path_suffixes": ("target", "value", "action_config"),
        "max_changes": 12,
    },
}

#: Run 可作为"失败来源"的测试结论（PASSED 一律拒绝）
FAILURE_SOURCE_VERDICTS = {"FAILED", "NOT_EVALUATED"}
TERMINAL_STATUSES = {"COMPLETED", "FAILED", "TIMED_OUT", "CANCELLED"}

#: RUN-014 WP1：Regression 是**独立于 Repair 生命周期**的验证阶段（与 sandbox_status 同构）：
#: NOT_STARTED → QUEUED → RUNNING → PASSED / FAILED / INCONCLUSIVE（BLOCKED 为环境不可用）。
#: 只有"真实 Regression Run 的 execution_status=COMPLETED + verdict=PASSED + 有证据"才算 PASSED。
REGRESSION_STATUSES = frozenset(
    {"NOT_STARTED", "QUEUED", "RUNNING", "PASSED", "FAILED", "INCONCLUSIVE", "BLOCKED"}
)
REGRESSION_TERMINAL = frozenset({"PASSED", "FAILED", "INCONCLUSIVE", "BLOCKED"})
REGRESSION_INFLIGHT = frozenset({"QUEUED", "RUNNING"})


class RepairService:
    #: 沙箱 Run 的等待上限（秒）：必须由独立 Worker 真实执行完，超时即失败而非伪造结论
    _sandbox_timeout_s: float = 300.0
    #: Regression Run 的等待上限（秒）：超时不伪造结论，保持 RUNNING 由后续轮询收敛
    _regression_timeout_s: float = 300.0

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
        diagnosis_id: str | None = None,
        source_run_id: str | None = None,
        repair_source: str = "",
        actor: str | None = None,
        trusted_source: bool = False,
    ) -> dict[str, Any]:
        """创建修复提案（RUN-011-R1.1 WP3：服务端血缘与来源真实性校验）。

        血缘校验（写库前完成，任一不满足即拒绝）：
        - Diagnosis 必须真实存在、kind=diagnosis、testcase_id/run_id/workspace 一致；
        - Run 必须真实存在、属于同一 TestCase、版本对应 base_version、Workspace 一致；
        - Run 必须具备真实失败或 NOT_EVALUATED 的有效执行证据；PASSED Run 不得伪装为失败来源；
        - Diagnosis 与 Run 不匹配 → 拒绝。

        来源真实性：
        - `human:*` / 空 → 服务端改写为 `human:{actor}`（actor 来自已认证 Principal）；
        - `rule:<name>` → 必须命中服务端规则契约（`RULE_SOURCE_CONTRACTS`）且变更结构吻合；
        - `llm:<model>` → 只允许内部 LLM Advisor 成功路径写入（`trusted_source=True`），
          普通 API 调用方伪造一律拒绝；
        - `created_by` 不再信任客户端声明，由 `actor` 决定（内部调用可显式传入）。
        """
        actor = (actor or created_by or "").strip()
        if not actor:
            raise AppError(ErrorCode.VALIDATION_ERROR, "created_by/actor 不能为空")

        base = self.rt.catalog.get_version(testcase_id, base_version)
        if not base:
            raise AppError(ErrorCode.NOT_FOUND, "base testcase version not found")
        if not changes:
            raise AppError(ErrorCode.VALIDATION_ERROR, "changes 不能为空")

        # ---- 血缘：Workspace 一律取自正式 TestCase 版本（服务端权威） ----
        ws = self._workspace_of(base)
        if workspace_id and str(workspace_id).strip() != ws:
            raise AppError(
                ErrorCode.AUTHORIZATION_ERROR,
                f"CROSS_WORKSPACE_REPAIR: 提案 workspace '{workspace_id}' 与用例版本 '{ws}' 不一致",
            )

        diagnosis_doc = (
            self._load_diagnosis(diagnosis_id, testcase_id=testcase_id, workspace=ws)
            if diagnosis_id
            else None
        )
        run_doc = (
            self._load_failure_run(
                source_run_id, testcase_id=testcase_id, base_version=base_version, workspace=ws
            )
            if source_run_id
            else None
        )

        if diagnosis_doc is not None and run_doc is not None:
            diag_run = str(diagnosis_doc.get("run_id") or "")
            if diag_run and diag_run != str(run_doc.get("run_id")):
                raise AppError(
                    ErrorCode.CONFLICT,
                    f"DIAGNOSIS_RUN_MISMATCH: Diagnosis.run_id={diag_run} 与 "
                    f"source_run_id={run_doc.get('run_id')} 不一致",
                )
        if diagnosis_doc is not None and run_doc is None:
            # 只给 Diagnosis 时自动回填其真实失败 Run，血缘不悬空
            derived = str(diagnosis_doc.get("run_id") or "")
            if derived:
                run_doc = self._load_failure_run(
                    derived, testcase_id=testcase_id, base_version=base_version, workspace=ws
                )
                source_run_id = derived
        if diagnosis_doc is None and run_doc is not None and source_run_id is None:
            source_run_id = str(run_doc.get("run_id") or "")

        source, source_verified = self._resolve_source(
            repair_source, actor=actor, changes=changes, trusted_source=trusted_source
        )

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
            "workspace_id": ws,
            "base_testcase_version": f"{testcase_id}:v{base_version}",
            "base_version": base_version,
            "status": "VALIDATED" if (ok_assert and ok_oracle and ok_diff) else "VALIDATION_FAILED",
            "sandbox_status": "NOT_STARTED",
            "assertion_preservation": "PASS" if ok_assert else "FAIL",
            "oracle_integrity": "PASS" if ok_oracle else "FAIL",
            "diff_allowed": ok_diff,
            "diff": diff,
            "reason": reason,
            "created_by": actor,
            "created_by_source": "principal" if not trusted_source else "internal",
            # RUN-011-R1 WP1 §1.3 + R1.1 WP3：Diagnosis→Repair 血缘（服务端验证）
            "diagnosis_id": diagnosis_id or (diagnosis_doc or {}).get("diagnosis_id") or None,
            "source_run_id": source_run_id or (run_doc or {}).get("run_id") or None,
            "repair_source": source,
            "repair_source_verified": source_verified,
            "lineage": {
                "diagnosis_run_id": (diagnosis_doc or {}).get("run_id"),
                "diagnosis_testcase_id": (diagnosis_doc or {}).get("testcase_id"),
                "run_testcase_version_id": (run_doc or {}).get("testcase_version_id"),
                "run_test_verdict": (run_doc or {}).get("test_verdict"),
                "run_execution_status": (run_doc or {}).get("execution_status"),
                "verified_at": utc_now_iso(),
            },
            "created_at": utc_now_iso(),
            "approval_ref": None,
            "sandbox_run_id": None,
            "promotion_record": {},
            # RUN-014 WP1：Regression 独立状态（不得由 promotion_record 推导）
            "regression_status": "NOT_STARTED",
            "regression_run_id": None,
            "regression_task_id": None,
            "regression_testcase_version_id": None,
            "regression_verdict": "",
            "regression_started_at": None,
            "regression_finished_at": None,
            "regression_failure_reason": "",
            "regression_evidence_ids": [],
            "regression_oracle_refs": [],
            "regression_history": [],
            "regression_stale": False,
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

    # ------------------------------------------------------- lineage helpers
    def _load_diagnosis(
        self, diagnosis_id: str, *, testcase_id: str, workspace: str
    ) -> dict[str, Any]:
        rec = self.rt.docs.get(diagnosis_id)
        if rec is None or rec.kind != "diagnosis":
            raise AppError(ErrorCode.NOT_FOUND, f"DIAGNOSIS_NOT_FOUND: {diagnosis_id}")
        doc = dict(rec.doc or {})
        if str(doc.get("testcase_id") or "") != testcase_id:
            raise AppError(
                ErrorCode.CONFLICT,
                f"DIAGNOSIS_TESTCASE_MISMATCH: Diagnosis 属于用例 "
                f"'{doc.get('testcase_id')}'，与请求 '{testcase_id}' 不一致",
            )
        diag_ws = str(doc.get("workspace_id") or rec.workspace_id or "default")
        if diag_ws != workspace:
            raise AppError(
                ErrorCode.AUTHORIZATION_ERROR,
                f"CROSS_WORKSPACE_DIAGNOSIS: Diagnosis 属于 workspace '{diag_ws}'，"
                f"与用例版本 '{workspace}' 不一致",
            )
        doc.setdefault("diagnosis_id", diagnosis_id)
        return doc

    def _load_failure_run(
        self, run_id: str, *, testcase_id: str, base_version: int, workspace: str
    ) -> dict[str, Any]:
        run = self.rt.tasks.get_run(run_id)
        if not run:
            raise AppError(ErrorCode.NOT_FOUND, f"RUN_NOT_FOUND: {run_id}")
        run_ws = str(run.get("workspace_id") or "default")
        if run_ws != workspace:
            raise AppError(
                ErrorCode.AUTHORIZATION_ERROR,
                f"CROSS_WORKSPACE_RUN: Run 属于 workspace '{run_ws}'，与用例版本 "
                f"'{workspace}' 不一致",
            )
        ref = str(run.get("testcase_version_id") or "")
        tc_id, _, raw_version = ref.partition("@")
        if tc_id != testcase_id:
            raise AppError(
                ErrorCode.CONFLICT,
                f"RUN_TESTCASE_MISMATCH: Run 使用版本 {ref}，与请求用例 {testcase_id} 不一致",
            )
        if raw_version.isdigit() and int(raw_version) != int(base_version):
            raise AppError(
                ErrorCode.CONFLICT,
                f"RUN_VERSION_MISMATCH: Run 使用 v{raw_version}，与 base_version "
                f"v{base_version} 不一致",
            )
        verdict = str(run.get("test_verdict") or "")
        status = str(run.get("execution_status") or "")
        if status not in TERMINAL_STATUSES:
            raise AppError(
                ErrorCode.PRECONDITION_FAILED,
                f"RUN_NOT_TERMINAL: Run 处于 {status or 'UNKNOWN'}，不能作为失败来源",
            )
        if verdict == "PASSED":
            raise AppError(
                ErrorCode.PRECONDITION_FAILED,
                "PASSED_RUN_NOT_A_FAILURE_SOURCE: PASSED Run 不得伪装为失败来源",
            )
        if verdict not in FAILURE_SOURCE_VERDICTS:
            raise AppError(
                ErrorCode.PRECONDITION_FAILED,
                f"RUN_VERDICT_NOT_A_FAILURE_SOURCE: {verdict or 'UNKNOWN'} 不能作为失败来源",
            )
        report = run.get("report") or {}
        has_failure_evidence = bool(
            report.get("errors")
            or [
                s
                for s in report.get("steps") or []
                if s.get("status") in {"FAILED", "TIMED_OUT", "NOT_EVALUATED"}
            ]
            or [o for o in report.get("assertion_outcomes") or [] if o.get("status") != "PASSED"]
            or run.get("error")
        )
        if not has_failure_evidence:
            raise AppError(
                ErrorCode.PRECONDITION_FAILED,
                "RUN_NO_FAILURE_EVIDENCE: Run 缺少真实失败/未评估执行证据，不能作为失败来源",
            )
        return run

    def _resolve_source(
        self,
        repair_source: str,
        *,
        actor: str,
        changes: list[dict[str, Any]],
        trusted_source: bool,
    ) -> tuple[str, bool]:
        """返回 (服务端可信来源, 是否已验证)。

        - `human:*` / 空 → `human:{actor}`；
        - `rule:<name>` → 命中服务端规则契约才允许（trusted_source=True 的内部路径同样校验）；
        - `llm:<model>` → 仅 `trusted_source=True`（真实 LLM Advisor 成功返回）允许。
        """
        raw = (repair_source or "").strip()
        if not raw:
            return f"human:{actor}", False
        if raw.startswith("human:"):
            return f"human:{actor}", False
        if raw.startswith("rule:"):
            rule_id = raw.split(":", 1)[1].strip()
            contract = RULE_SOURCE_CONTRACTS.get(rule_id)
            if contract is None:
                raise AppError(
                    ErrorCode.VALIDATION_ERROR,
                    f"UNTRUSTED_REPAIR_SOURCE: 未知规则来源 rule:{rule_id}"
                    "（只能由服务端登记的确定性规则写入）",
                )
            if len(changes) > int(contract.get("max_changes", 8)):
                raise AppError(
                    ErrorCode.VALIDATION_ERROR,
                    f"RULE_SOURCE_CONTRACT_VIOLATED: rule:{rule_id} 变更数量超出契约",
                )
            for change in changes:
                path = str(change.get("path") or "")
                if not path.startswith("steps[") or not path.endswith(
                    tuple(contract.get("path_suffixes", ()))
                ):
                    raise AppError(
                        ErrorCode.VALIDATION_ERROR,
                        f"RULE_SOURCE_CONTRACT_VIOLATED: rule:{rule_id} 不允许变更路径 {path}",
                    )
            return f"rule:{rule_id}", True
        if raw.startswith("llm:"):
            if not trusted_source:
                raise AppError(
                    ErrorCode.AUTHORIZATION_ERROR,
                    "UNTRUSTED_REPAIR_SOURCE: llm:* 来源只能由服务端 LLM Advisor 成功路径写入",
                )
            return raw, True
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            "repair_source 必须以 human:/rule:/llm: 开头（如实标注修复方案来源）",
        )

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
            # 来源真实性（RUN-011-R1.1 WP3）：只有真实 LLM 调用成功返回才可标 llm:<model>；
            # Fallback 走服务端登记的规则契约，绝不冒充 LLM。
            if advice.called and not advice.fallback:
                source = f"llm:{advice.model or 'unknown'}"
                trusted = True
            else:
                source = "rule:llm-repair-fallback"
                trusted = True
            out["proposal"] = self.propose(
                testcase_id=testcase_id,
                base_version=base_version,
                changes=advice.changes,
                reason=f"LLM 修复候选（{advice.model or 'fallback'}）",
                created_by="agent:llm-repair-planner",
                diagnosis_id=diagnosis_id,
                source_run_id=run_id,
                repair_source=source,
                trusted_source=trusted,
            )
        return out

    def get(self, proposal_id: str) -> dict[str, Any]:
        rec = self.rt.docs.get(proposal_id)
        if not rec or rec.kind != "repair":
            raise AppError(ErrorCode.NOT_FOUND, "repair proposal not found")
        return rec.to_dict()

    def list(
        self,
        workspace_id: str | None = None,
        status: str | None = None,
        *,
        workspace_ids: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        recs = self.rt.docs.list(
            kind="repair",
            workspace_id=workspace_id,
            status=status,
            limit=200,
            workspace_ids=workspace_ids,
        )
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
        # RUN-011-R1.1 WP1 §1.3：Sandbox Task/Run 必须显式继承 Repair 的 Workspace，
        # 不得依赖 create_task 默认值（旧默认值会把沙箱 Task 落入 default）。
        task = self.rt.tasks.create_task(
            sandbox_case_id,
            f"repair-sandbox-{proposal_id}-{time.time_ns()}",
            testcase_version_id=f"{sandbox_case_id}@{version}",
            mode="sandbox",
            workspace_id=doc["workspace_id"],
        )
        run = self.rt.tasks.create_run(task["task_id"], f"{sandbox_case_id}@{version}")
        # RUN-007-RESUME：沙箱属于正式执行，必须由独立 Worker 消费队列，
        # 不在 API 线程里直接 execute_run（否则等于"API 线程池冒充 Worker"）。
        final = self.rt.wait_for_run(run["run_id"], timeout_s=self._sandbox_timeout_s)
        status = str((final or {}).get("execution_status") or "")
        if status not in TERMINAL_RUN_STATUSES:
            raise AppError(
                ErrorCode.PRECONDITION_FAILED,
                f"沙箱 Run 在 {self._sandbox_timeout_s:.0f}s 内未被 Worker 执行完成"
                f"（当前状态 {status or 'UNKNOWN'}）：请确认独立 Worker 已启动",
            )
        doc["sandbox_run_id"] = final["run_id"]
        doc["sandbox_testcase_id"] = sandbox_case_id
        doc["sandbox_status"] = "PASSED" if final["test_verdict"] == "PASSED" else "FAILED"
        doc["sandbox_verdict"] = final["test_verdict"]
        doc["sandbox_observer_mode"] = final.get("observer_mode", "")
        doc["sandbox_executed_by"] = final.get("lease_owner") or ""
        doc["sandbox_fencing_token"] = final.get("fencing_token")
        if final["test_verdict"] != "PASSED":
            doc["failure_reason"] = f"SANDBOX_{final['test_verdict']}"
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
        receipt = self._match_receipt(
            doc, idempotency_key, kind="PROMOTE", base_version=int(doc["base_version"])
        )
        if receipt is not None:
            return self._settle_promoted(doc, receipt, idempotency_key, recovered=True)

        status = RepairStatus(doc["status"])
        if status is RepairStatus.PROMOTED:
            # 已晋升但没有回执（历史数据）：**真正补写一条持久化回执**，
            # 而不是在内存里造一个临时字典就宣称幂等。
            target_version = int(doc.get("promoted_version") or 0)
            if target_version <= 0:
                raise AppError(
                    ErrorCode.CONFLICT,
                    "PROMOTED proposal 缺少有效 promoted_version，无法补回执",
                )
            self.rt.catalog.record_receipt(
                idempotency_key=idempotency_key,
                kind="PROMOTE",
                proposal_id=proposal_id,
                testcase_id=doc["testcase_id"],
                workspace_id=doc["workspace_id"],
                base_version=int(doc["base_version"]),
                target_version=target_version,
                result={
                    "version": target_version,
                    "testcase_id": doc["testcase_id"],
                    "kind": "PROMOTE",
                    "backfilled_from": "repair_doc",
                },
            )
            receipt = self._match_receipt(
                doc, idempotency_key, kind="PROMOTE", base_version=int(doc["base_version"])
            )
            if receipt is None:  # pragma: no cover - 写入后必然可读
                raise AppError(ErrorCode.CONFLICT, "回执补写失败，无法恢复幂等语义")
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
        # Fail-closed：晋升结果必须是一个有效的新版本号；否则不能宣告 PROMOTED
        if not isinstance(receipt["target_version"], int) or receipt["target_version"] <= 0:
            doc["failure_reason"] = "PROMOTION_RESULT_INVALID"
            doc["promotion_record"] = {
                **(doc.get("promotion_record") or {}),
                "idempotency_key": idempotency_key,
                "result": "PROMOTION_RESULT_INVALID",
                "response": created,
                "at": utc_now_iso(),
            }
            self._transition(doc, RepairStatus.PROMOTION_FAILED)
            return self._save(doc)
        return self._settle_promoted(doc, receipt, idempotency_key, recovered=False)

    def _match_receipt(
        self,
        doc: dict[str, Any],
        idempotency_key: str,
        *,
        kind: str,
        base_version: int,
    ) -> dict[str, Any] | None:
        """幂等键命中，或同一提案的同类回执命中（不同幂等键也不重复创建版本）。

        RUN-007-FINALIZATION-2（WP1-B）：回执必须与本次动作身份完全一致
        （kind / proposal_id / testcase_id / workspace_id / base_version）。
        不一致时显式返回冲突——绝不能把另一个 Proposal 的 promoted_version
        当成自己的结果，也不能让 PROMOTE 与 ROLLBACK 共用一条回执。
        """
        receipt = (
            self.rt.catalog.get_promotion_receipt(idempotency_key) if idempotency_key else None
        )
        if receipt is None:
            receipt = self.rt.catalog.find_promotion_receipt(doc["proposal_id"], kind=kind)
        if receipt is None:
            return None
        self._assert_receipt_identity(
            receipt, doc, kind=kind, base_version=base_version, idempotency_key=idempotency_key
        )
        return receipt

    @staticmethod
    def _assert_receipt_identity(
        receipt: dict[str, Any],
        doc: dict[str, Any],
        *,
        kind: str,
        base_version: int,
        idempotency_key: str,
    ) -> None:
        expected = {
            "kind": kind,
            "proposal_id": doc["proposal_id"],
            "testcase_id": doc["testcase_id"],
            "workspace_id": doc["workspace_id"],
        }
        mismatched = [
            {"field": f, "receipt": receipt.get(f), "request": v}
            for f, v in expected.items()
            if str(receipt.get(f) or "") != str(v or "")
        ]
        if int(receipt.get("base_version") or 0) != int(base_version or 0):
            mismatched.append(
                {
                    "field": "base_version",
                    "receipt": receipt.get("base_version"),
                    "request": base_version,
                }
            )
        if mismatched:
            raise AppError(
                ErrorCode.CONFLICT,
                "IDEMPOTENCY_KEY_REUSED: 幂等键/回执已绑定到其它提案、用例、工作区或操作类型",
                details={
                    "idempotency_key": idempotency_key or receipt.get("idempotency_key"),
                    "mismatched": mismatched,
                    "receipt": receipt,
                },
            )

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
        # RUN-014 WP1：晋升成功只代表"版本已生效"，Regression 仍应是独立未开始状态；
        # 已存在的回归结果不得被幂等重放/恢复覆盖。
        doc.setdefault("regression_status", "NOT_STARTED")
        doc.setdefault("regression_run_id", None)
        doc.setdefault("regression_verdict", "")
        doc.setdefault("regression_failure_reason", "")
        doc.setdefault("regression_evidence_ids", [])
        doc.setdefault("regression_history", [])
        doc.setdefault("regression_stale", False)
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

    # ------------------------------------------------- RUN-016 WP2.3 失败恢复
    def promotion_recovery_plan(self, proposal_id: str) -> dict[str, Any]:
        """只读恢复评估：晋升失败后到底该重试、还是已经成功、还是必须重开提案。

        设计原则（fail-closed）：
        - 先读**当前真实 TestCase 版本**与本次提案的回执，判断实际晋升结果，
          不以前端/上一次请求的返回值为准；
        - 重新执行必要前置检查（候选文档 Oracle 绑定、人工审批主体）；
        - 任何判断不确定 → `BLOCKED_*`，不给出"可以重试"的结论。
        """
        doc = self.get(proposal_id)
        status = str(doc["status"])
        testcase_id = doc["testcase_id"]
        base_version = int(doc.get("base_version") or 0)
        current_approved = self.rt.catalog.approved_version_number(testcase_id)
        receipt = self.rt.catalog.find_promotion_receipt(proposal_id, kind="PROMOTE")
        candidate = doc.get("candidate_doc") or {}
        oracle_refs, oracle_problems = self._regression_oracles(candidate)
        approved_by = str(doc.get("approved_by") or "")
        approval_ref = str(doc.get("approval_ref") or "")

        plan: dict[str, Any] = {
            "proposal_id": proposal_id,
            "status": status,
            "testcase_id": testcase_id,
            "base_version": base_version,
            "current_approved_version": current_approved,
            "promoted_version": int(doc.get("promoted_version") or 0),
            "receipt": receipt,
            "oracle_refs": oracle_refs,
            "oracle_problems": oracle_problems,
            "approved_by": approved_by,
            "approval_ref": approval_ref,
            "promotion_record": doc.get("promotion_record") or {},
            "checked_at": utc_now_iso(),
        }

        if status == "PROMOTED":
            plan.update(
                action="NOOP_ALREADY_PROMOTED",
                reason="提案已处于 PROMOTED，无需恢复",
                requires_confirmation=False,
            )
            return plan
        if status not in {"PROMOTION_FAILED", "PROMOTING"}:
            plan.update(
                action="BLOCKED_ILLEGAL_STATE",
                reason=f"状态 {status} 不在可恢复集合（PROMOTION_FAILED / PROMOTING）",
                requires_confirmation=False,
            )
            return plan
        if receipt is not None:
            # CAS 已提交但提案没落库：实际晋升已完成，只能收敛，不能重复晋升
            plan.update(
                action="NOOP_ALREADY_PROMOTED",
                reason="存在本次提案的晋升回执：实际晋升已完成，应收敛状态而不是重试",
                requires_confirmation=False,
            )
            return plan
        if not approved_by or not approval_ref:
            plan.update(
                action="BLOCKED_NEEDS_APPROVAL",
                reason="缺少人类审批记录：不允许绕过 Human Approval 恢复晋升",
                requires_confirmation=False,
            )
            return plan
        if oracle_problems:
            plan.update(
                action="BLOCKED_ORACLE",
                reason="候选版本 Oracle 不满足要求：" + "; ".join(oracle_problems),
                requires_confirmation=False,
            )
            return plan
        if current_approved != base_version:
            plan.update(
                action="BLOCKED_VERSION_DRIFT",
                reason=(
                    f"当前正式版本 v{current_approved} 与提案基线 v{base_version} 不一致："
                    "重试只会再次 CAS 冲突，必须基于最新版本重开提案"
                ),
                requires_confirmation=False,
            )
            return plan

        plan.update(
            action="RETRY_PROMOTE",
            reason="前置条件全部满足，可在人工确认后以新的幂等键重试 CAS 晋升",
            requires_confirmation=True,
            previous_failure=self._last_promotion_failure(doc),
        )
        return plan

    @staticmethod
    def _last_promotion_failure(doc: dict[str, Any]) -> dict[str, Any] | None:
        record = doc.get("promotion_record") or {}
        if not record:
            return None
        return {
            "result": record.get("result"),
            "current_version": record.get("current_version"),
            "base_version": record.get("base_version"),
            "detail": record.get("detail"),
            "at": record.get("at"),
        }

    def promote_recover(
        self,
        proposal_id: str,
        *,
        idempotency_key: str,
        confirm: bool,
        actor: str,
    ) -> dict[str, Any]:
        """人工确认后的安全恢复。

        - `confirm != True` 一律拒绝（人工确认是显式动作，不能默认执行）；
        - 已有回执 / 已 PROMOTED → 只收敛，不重复创建版本；
        - 前置条件不满足 → 抛前置失败，不改任何状态；
        - 允许重试时：PROMOTION_FAILED → APPROVED（审计留痕）→ 用**新的**幂等键走原 CAS 晋升。
        """
        if confirm is not True:
            raise AppError(
                ErrorCode.PRECONDITION_FAILED,
                "PROMOTION_RECOVERY_REQUIRES_HUMAN_CONFIRMATION: 必须显式 confirm=true",
            )
        if not idempotency_key:
            raise AppError(ErrorCode.VALIDATION_ERROR, "idempotency_key 不能为空")
        operator = require_human_actor(actor, field="operator")
        plan = self.promotion_recovery_plan(proposal_id)
        action = plan["action"]
        if action == "NOOP_ALREADY_PROMOTED":
            doc = self.reconcile(proposal_id)
            return {**doc, "recovery": {**plan, "executed": "RECONCILED", "operator": operator}}
        if action != "RETRY_PROMOTE":
            raise AppError(
                ErrorCode.PRECONDITION_FAILED,
                f"PROMOTION_RECOVERY_BLOCKED: {action}: {plan['reason']}",
            )
        doc = self.get(proposal_id)
        if doc["status"] == "PROMOTION_FAILED":
            # 状态机允许 PROMOTION_FAILED -> APPROVED；每一步都留痕
            self._transition(doc, RepairStatus.APPROVED)
            doc["promotion_recovery_history"] = list(
                doc.get("promotion_recovery_history") or []
            ) + [
                {
                    "at": utc_now_iso(),
                    "operator": operator,
                    "from": "PROMOTION_FAILED",
                    "to": "APPROVED",
                    "idempotency_key": idempotency_key,
                    "plan_action": action,
                }
            ]
            doc["failure_reason"] = ""
            self._save(doc)
        promoted = self.promote(proposal_id, idempotency_key=idempotency_key)
        promoted["recovery"] = {
            **plan,
            "executed": "RETRY_PROMOTE",
            "operator": operator,
            "idempotency_key": idempotency_key,
            "result_status": promoted.get("status"),
        }
        return promoted

    # ------------------------------------------------------------- regression
    def regression_run(self, proposal_id: str, *, timeout_s: float | None = None) -> dict[str, Any]:
        """晋升后的真实回归验证（RUN-014 WP1）。

        契约（fail-closed）：
        - 只有 PROMOTED 提案可回归；回归对象是**实际晋升版本**（promoted_version，APPROVED）；
        - 使用该版本绑定的 Oracle（raise 前先在服务端核验，缺失/未审批 → INCONCLUSIVE）；
        - 由现有独立 Worker 消费队列执行真实 Run，不由 API 线程冒充 Worker；
        - 只有 execution_status=COMPLETED + verdict=PASSED + 有证据 才置 PASSED；
        - 幂等：已终态直接返回；在途只收敛同一个 Run，绝不重复创建；
        - 超时/未终态不伪造结论，保持 RUNNING 等后续轮询收敛。
        """
        doc = self.get(proposal_id)
        if doc["status"] != "PROMOTED":
            raise AppError(
                ErrorCode.PRECONDITION_FAILED,
                f"REGRESSION_REQUIRES_PROMOTED: 当前状态 {doc['status']}"
                "（只有已晋升的修复才需要回归验证）",
            )
        current = str(doc.get("regression_status") or "NOT_STARTED")
        if current not in REGRESSION_STATUSES:
            raise AppError(ErrorCode.CONFLICT, f"REGRESSION_STATUS_UNKNOWN: {current}")
        if current in REGRESSION_TERMINAL:
            # 幂等：终态结果只能由新的 Run 改写，重复点击不创建新 Run
            return doc
        existing_run = str(doc.get("regression_run_id") or "")
        if existing_run or current in REGRESSION_INFLIGHT:
            if not existing_run:
                raise AppError(
                    ErrorCode.CONFLICT,
                    "REGRESSION_STATE_INCONSISTENT: 状态为进行中但缺少 regression_run_id",
                )
            return self._settle_regression(doc, self._wait_regression(existing_run, timeout_s))

        promoted = int(doc.get("promoted_version") or 0)
        if promoted <= 0:
            raise AppError(
                ErrorCode.CONFLICT,
                "PROMOTED_WITHOUT_VERSION: 提案缺少有效 promoted_version，无法回归",
            )
        version_ref = f"{doc['testcase_id']}@{promoted}"
        version_doc = self.rt.catalog.get_version(doc["testcase_id"], promoted)
        if not version_doc:
            raise AppError(
                ErrorCode.PRECONDITION_FAILED,
                f"PROMOTED_VERSION_NOT_FOUND: {version_ref}",
            )
        version_ws = str(version_doc.get("workspace_id") or "default")
        if version_ws != doc["workspace_id"]:
            raise AppError(
                ErrorCode.AUTHORIZATION_ERROR,
                f"CROSS_WORKSPACE_REGRESSION: 版本 {version_ref} 属于 workspace "
                f"'{version_ws}'，与提案 '{doc['workspace_id']}' 不一致",
            )
        if str(version_doc.get("review_status") or "") != "APPROVED":
            raise AppError(
                ErrorCode.PRECONDITION_FAILED,
                f"REGRESSION_VERSION_NOT_APPROVED: {version_ref} "
                f"review_status={version_doc.get('review_status')}",
            )

        oracle_refs, oracle_problems = self._regression_oracles(version_doc)
        doc["regression_oracle_refs"] = oracle_refs
        if oracle_problems:
            # fail-closed：没有可信 Oracle 就不能宣称"回归通过"
            return self._mark_regression_inconclusive(
                doc, "REGRESSION_ORACLE_UNAVAILABLE", "; ".join(oracle_problems)
            )

        task = self.rt.tasks.create_task(
            doc["testcase_id"],
            f"repair-regression:{proposal_id}:v{promoted}",
            testcase_version_id=version_ref,
            mode="playwright",
            workspace_id=doc["workspace_id"],
        )
        # RUN-015 WP2：Task 幂等键只能保证"一个 Task"，不能保证"一个 Run"。
        # 认领行主键 + Run 同事务插入，保证并发请求只有一个有效 Regression Run。
        claim = f"repair-regression:{proposal_id}:v{promoted}:attempt1"
        claimed = self.rt.tasks.create_run_once(
            task["task_id"], version_ref, claim=claim, proposal_id=proposal_id
        )
        run_id = str(claimed["run_id"])

        fresh, version = self._fresh_doc(proposal_id)
        already = str(fresh.get("regression_run_id") or "")
        if already and already != run_id:
            raise AppError(
                ErrorCode.CONFLICT,
                "REGRESSION_RUN_CONFLICT: 提案已绑定另一个回归 Run",
            )
        if already == run_id and fresh.get("regression_status") not in {
            "NOT_STARTED",
            "",
            None,
        }:
            # 并发赢家已经写入并可能在途：直接收敛到同一个 Run
            return self._settle_regression(fresh, self._wait_regression(run_id, timeout_s))

        fresh["regression_task_id"] = task["task_id"]
        fresh["regression_run_id"] = run_id
        fresh["regression_testcase_version_id"] = version_ref
        fresh["regression_status"] = "QUEUED"
        fresh["regression_verdict"] = ""
        fresh["regression_failure_reason"] = ""
        fresh["regression_evidence_ids"] = []
        fresh["regression_started_at"] = utc_now_iso()
        fresh["regression_finished_at"] = None
        fresh["regression_stale"] = False
        try:
            saved = self._save(fresh, expected_version=version)
        except AppError as exc:
            if exc.code != ErrorCode.CONFLICT:
                raise
            # 另一个并发请求先写了文档：回到它的 Run，不覆盖其状态
            saved = self.get(proposal_id)
        return self._settle_regression(saved, self._wait_regression(run_id, timeout_s))

    def regression_status(self, proposal_id: str) -> dict[str, Any]:
        """只读回归状态 + 实时 Run 快照（供 UI 刷新；不做任何写入）。"""
        doc = self.get(proposal_id)
        run_id = str(doc.get("regression_run_id") or "")
        run = self.rt.tasks.get_run(run_id) if run_id else None
        # RUN-015 WP2：stale 必须在读取时按"当前正式批准版本 vs 回归版本"重算。
        # 旧实现只在收敛/回滚时落库，别人晋升 v3 后 v2 的旧结论仍显示新鲜。
        # 只改展示字段，不改原始 Run 的 Verdict，也不写回 Repair 文档。
        regression_ref = str(doc.get("regression_testcase_version_id") or "")
        regression_version = 0
        if "@" in regression_ref:
            raw_version = regression_ref.rsplit("@", 1)[1]
            regression_version = int(raw_version) if raw_version.isdigit() else 0
        approved_version = int(self.rt.catalog.approved_version_number(doc["testcase_id"]) or 0)
        stale_reason = ""
        stale = bool(doc.get("regression_stale"))
        if stale:
            stale_reason = "ROLLED_BACK_OR_MARKED_STALE"
        elif regression_version and approved_version and regression_version != approved_version:
            stale = True
            stale_reason = f"APPROVED_VERSION_MOVED:{regression_version}->{approved_version}"
        return {
            "proposal_id": proposal_id,
            "regression_status": doc.get("regression_status") or "NOT_STARTED",
            "regression_verdict": doc.get("regression_verdict") or "",
            "regression_run_id": run_id or None,
            "regression_testcase_version_id": doc.get("regression_testcase_version_id"),
            "regression_started_at": doc.get("regression_started_at"),
            "regression_finished_at": doc.get("regression_finished_at"),
            "regression_failure_reason": doc.get("regression_failure_reason") or "",
            "regression_evidence_ids": list(doc.get("regression_evidence_ids") or []),
            "regression_oracle_refs": list(doc.get("regression_oracle_refs") or []),
            "regression_stale": stale,
            "regression_stale_reason": stale_reason,
            "regression_approved_version": approved_version or None,
            # NEEDS_DECISION：Oracle 缺失/证据缺失后的 INCONCLUSIVE 是否允许重新执行，
            # 涉及状态机与人工授权，本轮不擅自改变；前端不得暗示可重试。
            "regression_retry_allowed": False,
            "regression_retry_decision": "NEEDS_DECISION",
            "regression_history": list(doc.get("regression_history") or []),
            "run": (
                {
                    "run_id": run.get("run_id"),
                    "execution_status": run.get("execution_status"),
                    "test_verdict": run.get("test_verdict"),
                    "lease_owner": run.get("lease_owner"),
                    "fencing_token": run.get("fencing_token"),
                    "started_at": run.get("started_at"),
                    "ended_at": run.get("ended_at"),
                }
                if run
                else None
            ),
        }

    def _regression_oracles(
        self, version_doc: dict[str, Any]
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """核验晋升版本绑定的 Oracle：必须存在且已审批，且版本/Workspace 一致。"""
        refs: list[dict[str, Any]] = []
        problems: list[str] = []
        for spec in version_doc.get("assertions") or []:
            oracle_id = str(spec.get("oracle_id") or "")
            if not oracle_id:
                continue
            pinned = spec.get("oracle_version")
            ref = {
                "assertion_id": spec.get("assertion_id"),
                "oracle_id": oracle_id,
                "oracle_version": pinned,
            }
            doc = self.rt.catalog.get_oracle(oracle_id, int(pinned) if pinned else None)
            if not doc:
                ref["status"] = "MISSING"
                problems.append(f"{oracle_id}@{pinned or 'latest'} 不存在")
            elif not doc.get("approved", False):
                ref["status"] = "UNAPPROVED"
                problems.append(f"{oracle_id}@{doc.get('version')} 未审批")
            elif pinned and int(doc.get("version") or 0) != int(pinned):
                ref["status"] = "VERSION_MISMATCH"
                problems.append(f"{oracle_id} 固定版本 {pinned} 与读取版本不一致")
            else:
                ref["status"] = "OK"
                ref["oracle_version"] = doc.get("version")
            refs.append(ref)
        if not refs:
            problems.append("用例版本未绑定任何 Oracle")
        return refs, problems

    def _wait_regression(self, run_id: str, timeout_s: float | None) -> dict[str, Any]:
        timeout = self._regression_timeout_s if timeout_s is None else max(1.0, timeout_s)
        return self.rt.wait_for_run(run_id, timeout_s=timeout)

    def _mark_regression_inconclusive(
        self, doc: dict[str, Any], reason: str, detail: str
    ) -> dict[str, Any]:
        doc["regression_status"] = "INCONCLUSIVE"
        doc["regression_failure_reason"] = reason
        doc["regression_verdict"] = doc.get("regression_verdict") or "NOT_EVALUATED"
        doc["regression_finished_at"] = utc_now_iso()
        doc["regression_history"] = list(doc.get("regression_history") or []) + [
            {
                "at": doc["regression_finished_at"],
                "run_id": doc.get("regression_run_id"),
                "status": "INCONCLUSIVE",
                "verdict": doc.get("regression_verdict"),
                "reason": reason,
                "detail": detail,
            }
        ]
        return self._save(doc)

    def _settle_regression(self, doc: dict[str, Any], run: dict[str, Any] | None) -> dict[str, Any]:
        """把真实 Run 结果收敛为 Regression 状态（绝不从 promotion_record 推导）。

        RUN-015 WP2：并发请求都只能对同一个 Run 收敛；以文档 CAS 选出一个赢家，
        并对同一 (run_id, status) 的历史项去重，避免重复请求产生多个相互竞争的结论。
        """
        doc, _version = self._fresh_doc(doc["proposal_id"])
        if not run:
            doc["regression_status"] = "RUNNING"
            return self._save_cas(doc)
        status = str(run.get("execution_status") or "")
        verdict = str(run.get("test_verdict") or "NOT_EVALUATED")
        report = run.get("report") or {}
        evidence = [str(x) for x in (report.get("evidence_ids") or [])]
        doc["regression_verdict"] = verdict
        if status not in TERMINAL_STATUSES:
            # RUN-017：在途 Run 没有"判定结论"。旧实现把空 verdict 归一成 NOT_EVALUATED
            # 并写进文档，UI 会显示"已评估但未出结论"，与事实不符；并发请求也会因此
            # 看到 NOT_EVALUATED 而不是"尚无结论"。终态才允许落 NOT_EVALUATED。
            doc["regression_status"] = "RUNNING"
            doc["regression_verdict"] = str(run.get("test_verdict") or "")
            return self._save_cas(doc)

        reason = ""
        if verdict == "FAILED":
            new_status = "FAILED"
            reason = "REGRESSION_ASSERTION_FAILED"
        elif status == "COMPLETED" and verdict == "PASSED":
            if evidence:
                new_status = "PASSED"
            else:
                new_status = "INCONCLUSIVE"
                reason = "REGRESSION_EVIDENCE_MISSING"
        elif status == "COMPLETED":
            new_status = "INCONCLUSIVE"
            reason = f"REGRESSION_VERDICT_{verdict}"
        else:
            new_status = "INCONCLUSIVE"
            reason = f"REGRESSION_RUN_{status}"

        doc["regression_status"] = new_status
        doc["regression_failure_reason"] = reason
        doc["regression_evidence_ids"] = evidence[:100]
        doc["regression_finished_at"] = utc_now_iso()
        # 结果只对"当时晋升的版本"有效：之后被别人推进了新版本 → 明确标记 stale
        current = self.rt.catalog.approved_version_number(doc["testcase_id"])
        doc["regression_stale"] = bool(current) and current != int(doc.get("promoted_version") or 0)
        history = list(doc.get("regression_history") or [])
        if any(
            item.get("run_id") == run.get("run_id") and item.get("status") == new_status
            for item in history
        ):
            # 另一个并发请求已经写入同一结论：不重复追加历史、不改写其时间
            return doc
        history.append(
            {
                "at": doc["regression_finished_at"],
                "run_id": run.get("run_id"),
                "status": new_status,
                "verdict": verdict,
                "execution_status": status,
                "reason": reason,
                "error": (report.get("errors") or [None])[0],
                "evidence_count": len(evidence),
            }
        )
        doc["regression_history"] = history
        return self._save_cas(doc)

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
        receipt = self._match_receipt(doc, key, kind="ROLLBACK", base_version=promoted_version)
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
        if not isinstance(receipt["target_version"], int) or receipt["target_version"] <= 0:
            raise AppError(
                ErrorCode.CONFLICT,
                "ROLLBACK_RESULT_INVALID: 回滚未返回有效的新版本号，不视为成功",
            )
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
        # RUN-014 WP1：回滚后原回归结论不再适用于当前正式版本，必须显式失效
        doc["regression_stale"] = True
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

    def _save(self, doc: dict[str, Any], *, expected_version: int | None = None) -> dict[str, Any]:
        return self.rt.docs.put(
            doc_id=doc["proposal_id"],
            kind="repair",
            doc=doc,
            workspace_id=doc["workspace_id"],
            ref_id=doc["testcase_id"],
            status=doc["status"],
            version=expected_version,
        ).to_dict()

    def _fresh_doc(self, proposal_id: str) -> tuple[dict[str, Any], int]:
        """读取权威文档与版本号（用于 Regression 并发 CAS）。"""
        rec = self.rt.docs.get(proposal_id)
        if not rec or rec.kind != "repair":
            raise AppError(ErrorCode.NOT_FOUND, "repair proposal not found")
        return rec.to_dict(), int(rec.version)

    def _save_cas(self, doc: dict[str, Any]) -> dict[str, Any]:
        """基于当前版本做一次 CAS 写入；冲突时返回赢家版本，不覆盖并发结果。"""
        fresh, version = self._fresh_doc(doc["proposal_id"])
        try:
            return self._save(doc, expected_version=version)
        except AppError as exc:
            if exc.code == ErrorCode.CONFLICT:
                return fresh
            raise

    @staticmethod
    def _workspace_of(base_doc: dict[str, Any]) -> str:
        return str(base_doc.get("workspace_id") or "default")


def _to_tc(doc: dict[str, Any]) -> TestCaseVersion:
    allowed = set(TestCaseVersion.model_fields)
    return TestCaseVersion(**{k: v for k, v in doc.items() if k in allowed})


def _step_index(path: str, change: dict[str, Any]) -> int | None:
    # RUN-014 WP4：HTTP 层 `ChangeReq.model_dump()` 总会带上 step_index=None，
    # 旧写法用 `"step_index" in change` 判断会执行 int(None) → TypeError → 500。
    raw = change.get("step_index")
    if raw is not None:
        return int(raw)
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
        "diagnosis_id": doc.get("diagnosis_id"),
        "source_run_id": doc.get("source_run_id"),
        "repair_source": doc.get("repair_source", ""),
        # RUN-014 WP1：列表也要能区分"已晋升"与"已回归通过"
        "regression_status": doc.get("regression_status") or "NOT_STARTED",
        "regression_run_id": doc.get("regression_run_id"),
        "regression_verdict": doc.get("regression_verdict") or "",
    }
