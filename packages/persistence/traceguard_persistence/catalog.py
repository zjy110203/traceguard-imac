"""测试资产目录：Workspace / TestIntent / TestCaseVersion / Oracle。

不变量：
- TestCaseVersion 内容不可变（`immutable=True`），流程状态（review_status）可流转；
- Oracle 版本化，Agent 不能批准（`approved_by_agent` 恒 False），审批人必须是具名角色；
- 只有 APPROVED 的用例版本才允许被正式执行（执行器强制校验）。

RUN-006 WP1-01 增强：
- 审核前必须为全部 required assertion 绑定"存在且已审批、同 Workspace、版本明确"的 Oracle；
- 审核通过时把 `oracle_version` 固定到断言上，执行端不得无条件使用最新版本；
- 审查人身份用主体类型校验（拒绝 agent:/llm:/system: 等非人类主体），并明确
  单机开发环境没有强认证，不宣称生产级 RBAC；
- 并发审核用 `review_revision` 做 CAS，只允许一次成功。
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import select, update
from traceguard_contracts.models import OracleSpec, TestCaseVersion, TestIntent
from traceguard_core.errors import AppError, ErrorCode
from traceguard_core.ids import new_id, utc_now_iso

from . import stability_gate
from .db import Database
from .models import (
    DocRow,
    IntentRow,
    OracleRow,
    PromotionReceiptRow,
    TestCaseRow,
    TestCaseVersionRow,
    WorkspaceRow,
)

DEFAULT_WORKSPACE = "default"

#: 非人类主体标识：审核 / 审批 / 晋升一律拒绝（agent 可以"提案"，但不能"批准"）
NON_HUMAN_TOKENS = {
    "agent",
    "agents",
    "ai",
    "assistant",
    "bot",
    "llm",
    "model",
    "robot",
    "service",
    "system",
    "worker",
}


def _now() -> str:
    return utc_now_iso()


def _subject_tokens(actor: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", (actor or "").lower()) if t]


def is_non_human_actor(actor: str) -> bool:
    """判断主体是否为"非人类"（按命名空间/首段标识，而不是整串精确匹配）。

    这样 `agent:gpt`、`agent-1`、`LLM:deepseek`、`system:promotion` 都会被拒绝，
    不再存在"字符串精确匹配"的绕过空间。
    """
    tokens = _subject_tokens(actor)
    if not tokens:
        return True  # 空主体同样不可信
    return tokens[0] in NON_HUMAN_TOKENS


def require_human_actor(actor: str, *, field: str = "reviewer") -> str:
    value = (actor or "").strip()
    if not value:
        raise AppError(ErrorCode.PRECONDITION_FAILED, f"{field}_REQUIRED")
    if is_non_human_actor(value):
        raise AppError(
            ErrorCode.AUTHORIZATION_ERROR,
            f"{field.upper()}_MUST_BE_HUMAN: '{value}' 不是可用的具名人类主体",
        )
    return value


class CatalogService:
    def __init__(self, db: Database) -> None:
        self.db = db
        self._ensure_default_workspace()

    # ---------------- Workspace ----------------
    def _ensure_default_workspace(self) -> None:
        with self.db.session() as s:
            if not s.get(WorkspaceRow, DEFAULT_WORKSPACE):
                s.add(
                    WorkspaceRow(
                        workspace_id=DEFAULT_WORKSPACE,
                        name="Default Workspace",
                        description="TraceGuard 默认工作区",
                    )
                )

    def create_workspace(
        self, name: str, description: str = "", created_by: str = "system"
    ) -> dict[str, Any]:
        with self.db.session() as s:
            wid = f"ws_{new_id('x').split('_', 1)[1]}"
            s.add(
                WorkspaceRow(
                    workspace_id=wid,
                    name=name,
                    description=description,
                    created_by=created_by,
                )
            )
            return {
                "workspace_id": wid,
                "name": name,
                "description": description,
                "created_from": created_by,
            }

    def list_workspaces(self) -> list[dict[str, Any]]:
        with self.db.session() as s:
            rows = s.scalars(select(WorkspaceRow).order_by(WorkspaceRow.created_at)).all()
            return [
                {
                    "workspace_id": r.workspace_id,
                    "name": r.name,
                    "description": r.description,
                    "created_at": r.created_at,
                }
                for r in rows
            ]

    def get_workspace(self, workspace_id: str) -> dict[str, Any] | None:
        with self.db.session() as s:
            r = s.get(WorkspaceRow, workspace_id)
            if not r:
                return None
            return {
                "workspace_id": r.workspace_id,
                "name": r.name,
                "description": r.description,
                "created_at": r.created_at,
            }

    # ---------------- TestIntent ----------------
    def create_intent(self, payload: dict[str, Any]) -> dict[str, Any]:
        clean = {k: v for k, v in payload.items() if k in TestIntent.model_fields}
        intent = TestIntent(**clean)
        with self.db.session() as s:
            s.add(
                IntentRow(
                    intent_id=intent.intent_id,
                    workspace_id=payload.get("workspace_id", DEFAULT_WORKSPACE),
                    title=intent.title,
                    status=intent.status,
                    version=intent.version,
                    doc=intent.model_dump(),
                )
            )
        out = intent.model_dump()
        out["workspace_id"] = payload.get("workspace_id", DEFAULT_WORKSPACE)
        return out

    def list_intents(
        self, workspace_id: str | None = None, status: str | None = None
    ) -> list[dict[str, Any]]:
        with self.db.session() as s:
            q = select(IntentRow)
            if workspace_id:
                q = q.where(IntentRow.workspace_id == workspace_id)
            if status:
                q = q.where(IntentRow.status == status)
            rows = s.scalars(q.order_by(IntentRow.created_at.desc())).all()
            return [_intent_dict(r) for r in rows]

    def get_intent(self, intent_id: str) -> dict[str, Any] | None:
        with self.db.session() as s:
            r = s.get(IntentRow, intent_id)
            return _intent_dict(r) if r else None

    def update_intent_status(self, intent_id: str, status: str) -> dict[str, Any]:
        allowed = {"DRAFT", "REVIEW", "APPROVED", "DEPRECATED"}
        if status not in allowed:
            raise AppError(ErrorCode.VALIDATION_ERROR, f"invalid status {status}")
        with self.db.session() as s:
            r = s.get(IntentRow, intent_id)
            if not r:
                raise AppError(ErrorCode.NOT_FOUND, "intent not found")
            r.status = status
            r.updated_at = _now()
            doc = dict(r.doc or {})
            doc["status"] = status
            r.doc = doc
            s.flush()
            return _intent_dict(r)

    # ---------------- TestCase ----------------
    def create_testcase(
        self,
        payload: dict[str, Any],
        *,
        title: str = "",
        workspace_id: str = DEFAULT_WORKSPACE,
        review_status: str = "DRAFT",
    ) -> dict[str, Any]:
        """创建用例新版本；已存在则递增版本，历史版本不被覆盖。

        入参允许携带展示用附加字段（review_history/workspace_id 等），
        进入契约模型前按 model_fields 过滤，避免历史字段污染新版本。
        """
        clean = {k: v for k, v in payload.items() if k in TestCaseVersion.model_fields}
        tc = TestCaseVersion(**clean)
        tc.review_status = review_status  # type: ignore[assignment]
        with self.db.session() as s:
            row = s.get(TestCaseRow, tc.testcase_id)
            if row is None:
                row = TestCaseRow(
                    testcase_id=tc.testcase_id,
                    workspace_id=workspace_id,
                    intent_ref=tc.intent_ref,
                    title=title or tc.testcase_id,
                    review_status=review_status,
                    latest_version=0,
                    compile_source=tc.compile_source,
                    doc={},
                )
                s.add(row)
                s.flush()
            next_version = (row.latest_version or 0) + 1
            if tc.version != next_version:
                tc.version = next_version
            version_doc = tc.model_dump()
            version_doc["review_status"] = review_status
            s.add(
                TestCaseVersionRow(
                    id=f"{tc.testcase_id}@{tc.version}",
                    testcase_id=tc.testcase_id,
                    workspace_id=workspace_id,
                    version=tc.version,
                    review_status=review_status,
                    immutable=True,
                    doc=version_doc,
                )
            )
            row.latest_version = tc.version
            row.review_status = review_status
            row.updated_at = _now()
            if title:
                row.title = title
            s.flush()
            return version_doc

    def list_testcases(
        self, workspace_id: str | None = None, review_status: str | None = None
    ) -> list[dict[str, Any]]:
        with self.db.session() as s:
            q = select(TestCaseRow)
            if workspace_id:
                q = q.where(TestCaseRow.workspace_id == workspace_id)
            if review_status:
                q = q.where(TestCaseRow.review_status == review_status)
            rows = s.scalars(q.order_by(TestCaseRow.updated_at.desc())).all()
            out = []
            for r in rows:
                ver = s.get(TestCaseVersionRow, f"{r.testcase_id}@{r.latest_version}")
                out.append(
                    {
                        "testcase_id": r.testcase_id,
                        "title": r.title,
                        "workspace_id": r.workspace_id,
                        "intent_ref": r.intent_ref,
                        "review_status": r.review_status,
                        "latest_version": r.latest_version,
                        "compile_source": r.compile_source,
                        "step_count": len((ver.doc or {}).get("steps", [])) if ver else 0,
                        "assertion_count": len((ver.doc or {}).get("assertions", [])) if ver else 0,
                        "updated_at": r.updated_at,
                    }
                )
            return out

    def get_testcase(self, testcase_id: str) -> dict[str, Any] | None:
        with self.db.session() as s:
            r = s.get(TestCaseRow, testcase_id)
            if not r:
                return None
            ver = s.get(TestCaseVersionRow, f"{testcase_id}@{r.latest_version}")
            return {
                "testcase_id": r.testcase_id,
                "title": r.title,
                "workspace_id": r.workspace_id,
                "intent_ref": r.intent_ref,
                "review_status": r.review_status,
                "latest_version": r.latest_version,
                "compile_source": r.compile_source,
                "latest": dict(ver.doc or {}) if ver else None,
            }

    def list_versions(self, testcase_id: str) -> list[dict[str, Any]]:
        with self.db.session() as s:
            rows = s.scalars(
                select(TestCaseVersionRow)
                .where(TestCaseVersionRow.testcase_id == testcase_id)
                .order_by(TestCaseVersionRow.version.desc())
            ).all()
            return [
                {
                    "testcase_id": r.testcase_id,
                    "version": r.version,
                    "review_status": r.review_status,
                    "immutable": r.immutable,
                    "created_at": r.created_at,
                    "doc": dict(r.doc or {}),
                }
                for r in rows
            ]

    def get_version(self, testcase_id: str, version: int) -> dict[str, Any] | None:
        with self.db.session() as s:
            r = s.get(TestCaseVersionRow, f"{testcase_id}@{version}")
            if not r:
                return None
            out = dict(r.doc or {})
            out["review_status"] = r.review_status
            return out

    def review_testcase(
        self,
        testcase_id: str,
        version: int,
        decision: str,
        reviewer: str,
        note: str = "",
        *,
        require_stability: bool = True,
    ) -> dict[str, Any]:
        """人工审核：APPROVED 前必须通过 Oracle 绑定门禁 **和** 稳定性回放门禁，并用修订号 CAS 防并发。

        RUN-007 WP1-01：Agent 生成的候选（`compile_source in {compiler, healed}`）必须存在
        "同版本、已完成、全样本终态、STABLE_PASS、Oracle 完整且版本固定" 的稳定性回放，
        否则不得 APPROVED。人工编写版本（`compile_source == "human"`）显式记录豁免事实，
        不做静默跳过；豁免会写进 `review_history`，可审计。
        """
        if decision not in {"APPROVED", "REJECTED", "PENDING"}:
            raise AppError(ErrorCode.VALIDATION_ERROR, "decision must be APPROVED/REJECTED/PENDING")
        actor = require_human_actor(reviewer)
        with self.db.session() as s:
            v = s.get(TestCaseVersionRow, f"{testcase_id}@{version}")
            if not v:
                raise AppError(ErrorCode.NOT_FOUND, "testcase version not found")
            base_revision = int(v.review_revision or 0)
            doc = dict(v.doc or {})
            wsid = v.workspace_id or DEFAULT_WORKSPACE
            warnings: list[str] = list(doc.get("oracle_binding_warnings") or [])
            stability_evidence: dict[str, Any] | None = None
            stability_policy = ""

            if decision == "APPROVED":
                problems, warnings = self._oracle_gate(s, doc, workspace_id=wsid)
                if problems:
                    raise AppError(
                        ErrorCode.PRECONDITION_FAILED,
                        "ORACLE_BINDING_INCOMPLETE: " + "; ".join(problems[:6]),
                        details={"problems": problems, "warnings": warnings},
                    )
                doc = self._pin_oracle_versions(s, doc, workspace_id=wsid)
                doc["oracle_binding_warnings"] = warnings
                stability_evidence, stability_policy = self._stability_gate(
                    s,
                    testcase_id,
                    version,
                    doc,
                    workspace_id=wsid,
                    require=require_stability,
                )

            doc["review_status"] = decision
            doc["review_history"] = list(doc.get("review_history") or []) + [
                {
                    "decision": decision,
                    "reviewer": actor,
                    "note": note,
                    "at": _now(),
                    "review_revision": base_revision + 1,
                    "stability_policy": stability_policy,
                    "stability_evidence": stability_evidence,
                }
            ]
            doc["review_revision"] = base_revision + 1

            # 行级 CAS：并发审核只有一个能推进修订号，另一个得到 CONFLICT
            result = s.execute(
                update(TestCaseVersionRow)
                .where(
                    TestCaseVersionRow.id == f"{testcase_id}@{version}",
                    TestCaseVersionRow.review_revision == base_revision,
                )
                .values(review_status=decision, doc=doc, review_revision=base_revision + 1)
            )
            if result.rowcount != 1:
                raise AppError(
                    ErrorCode.CONFLICT,
                    "CONCURRENT_REVIEW_CONFLICT: 该版本已被其他审核操作更新，请重新读取",
                )
            tc = s.get(TestCaseRow, testcase_id)
            if tc:
                tc.review_status = decision
                tc.updated_at = _now()
            s.flush()
            return {
                "testcase_id": testcase_id,
                "version": version,
                "review_status": decision,
                "reviewer": actor,
                "review_revision": base_revision + 1,
                "warnings": warnings,
            }

    # ------------------------------------------------------------ oracle binding
    def bind_oracles(
        self,
        testcase_id: str,
        version: int,
        bindings: dict[str, dict[str, Any]],
        *,
        actor: str,
    ) -> dict[str, Any]:
        """把 Oracle 绑定到具体 Assertion（只提案绑定，不等于审批）。

        `bindings`：`{assertion_id: {"oracle_id": ..., "oracle_version": int|None}}`
        校验：断言存在、Oracle 存在、已审批、同 Workspace、断言与 Oracle 声明一致。
        已 APPROVED 的版本不允许再改绑定（必须新建版本，保持版本不可变语义）。
        """
        actor = require_human_actor(actor, field="actor")
        if not bindings:
            raise AppError(ErrorCode.VALIDATION_ERROR, "bindings 不能为空")
        with self.db.session() as s:
            v = s.get(TestCaseVersionRow, f"{testcase_id}@{version}")
            if not v:
                raise AppError(ErrorCode.NOT_FOUND, "testcase version not found")
            if v.review_status == "APPROVED":
                raise AppError(
                    ErrorCode.PRECONDITION_FAILED,
                    "APPROVED_VERSION_CONTENT_IMMUTABLE: 请创建新版本后再绑定 Oracle",
                )
            doc = dict(v.doc or {})
            wsid = v.workspace_id or DEFAULT_WORKSPACE
            assertions = {a.get("assertion_id"): a for a in doc.get("assertions") or []}
            bound: dict[str, Any] = dict(doc.get("oracle_bindings") or {})
            warnings: list[str] = list(doc.get("oracle_binding_warnings") or [])
            for assertion_id, spec in bindings.items():
                if assertion_id not in assertions:
                    raise AppError(ErrorCode.VALIDATION_ERROR, f"UNKNOWN_ASSERTION: {assertion_id}")
                oracle_id = str(spec.get("oracle_id") or "")
                version_hint = spec.get("oracle_version")
                row = self._resolve_oracle(s, oracle_id, version_hint, workspace_id=wsid)
                mismatch = str(row.assertion_id or "") not in ("", str(assertion_id))
                if mismatch:
                    # 语义复用：记录事实并显式告警，不静默假装"这条 Oracle 就是为它写的"
                    warnings.append(
                        f"ORACLE_REUSED_ACROSS_ASSERTIONS: {oracle_id}"
                        f"(声明用于 {row.assertion_id}) -> {assertion_id}"
                    )
                assertions[assertion_id]["oracle_id"] = oracle_id
                assertions[assertion_id]["oracle_version"] = int(row.version)
                bound[assertion_id] = {
                    "oracle_id": oracle_id,
                    "oracle_version": int(row.version),
                    "workspace_id": row.workspace_id,
                    "approved": bool(row.approved),
                    "approved_by": row.approved_by,
                    "oracle_assertion_id": row.assertion_id,
                    "assertion_id_mismatch": mismatch,
                    "bound_by": actor,
                    "bound_at": _now(),
                }
            doc["assertions"] = list(assertions.values())
            doc["oracle_bindings"] = bound
            doc["oracle_binding_warnings"] = warnings
            doc["binding_history"] = list(doc.get("binding_history") or []) + [
                {"actor": actor, "bindings": bound, "warnings": warnings, "at": _now()}
            ]
            v.doc = doc
            s.flush()
            return {
                "testcase_id": testcase_id,
                "version": version,
                "review_status": v.review_status,
                "oracle_bindings": bound,
                "warnings": warnings,
            }

    def _resolve_oracle(
        self,
        s: Any,
        oracle_id: str,
        version: int | None,
        *,
        workspace_id: str,
    ) -> OracleRow:
        """解析 Oracle 行并做存在性/审批/Workspace 校验。"""
        if not oracle_id:
            raise AppError(ErrorCode.VALIDATION_ERROR, "ORACLE_ID_REQUIRED")
        if version is not None:
            row = s.get(OracleRow, f"{oracle_id}@{int(version)}")
        else:
            row = s.scalar(
                select(OracleRow)
                .where(OracleRow.oracle_id == oracle_id)
                .order_by(OracleRow.version.desc())
                .limit(1)
            )
        if row is None:
            raise AppError(
                ErrorCode.NOT_FOUND, f"ORACLE_NOT_FOUND: {oracle_id}@{version or 'latest'}"
            )
        if (row.workspace_id or DEFAULT_WORKSPACE) != (workspace_id or DEFAULT_WORKSPACE):
            raise AppError(
                ErrorCode.AUTHORIZATION_ERROR,
                f"CROSS_WORKSPACE_ORACLE: {oracle_id} 属于 {row.workspace_id}",
            )
        if not row.approved:
            raise AppError(
                ErrorCode.PRECONDITION_FAILED, f"ORACLE_NOT_APPROVED: {oracle_id}@{row.version}"
            )
        approval = (row.doc or {}).get("approval_history") or []
        approver = row.approved_by or next(
            (a.get("approved_by") for a in reversed(approval) if a.get("approved")), ""
        )
        if not approver or is_non_human_actor(str(approver)):
            raise AppError(
                ErrorCode.PRECONDITION_FAILED,
                f"ORACLE_APPROVER_NOT_HUMAN: {oracle_id}@{row.version}",
            )
        return row

    def _oracle_gate(
        self, s: Any, doc: dict[str, Any], *, workspace_id: str
    ) -> tuple[list[str], list[str]]:
        """审核门禁：全部 required assertion 必须绑定合法、已审批、同 Workspace 的 Oracle。

        返回 (problems, warnings)：problems 非空则不得 APPROVED；
        warnings 记录"跨断言复用 Oracle"等需要人看到但不阻断的事实。
        """
        problems: list[str] = []
        warnings: list[str] = []
        assertions = doc.get("assertions") or []
        if not assertions:
            return ["NO_ASSERTIONS: 用例必须至少有一条断言"], []
        if not any(a.get("required", True) for a in assertions):
            return ["NO_REQUIRED_ASSERTION: 空必需断言集非法"], []
        for a in assertions:
            aid = a.get("assertion_id")
            if not a.get("required", True):
                continue
            oracle_id = a.get("oracle_id")
            if not oracle_id:
                problems.append(f"MISSING_ORACLE: {aid}")
                continue
            try:
                row = self._resolve_oracle(
                    s, str(oracle_id), a.get("oracle_version"), workspace_id=workspace_id
                )
            except AppError as exc:
                problems.append(f"{exc.message} ({aid})")
                continue
            if str(row.assertion_id or "") not in ("", str(aid)):
                warnings.append(
                    f"ORACLE_REUSED_ACROSS_ASSERTIONS: {oracle_id}"
                    f"(声明用于 {row.assertion_id}) -> {aid}"
                )
        return problems, warnings

    def _pin_oracle_versions(
        self, s: Any, doc: dict[str, Any], *, workspace_id: str
    ) -> dict[str, Any]:
        """审核通过时把 Oracle 版本固定写入断言，避免每次执行都用"最新版本"。"""
        pinned: dict[str, Any] = dict(doc.get("oracle_bindings") or {})
        for a in doc.get("assertions") or []:
            if not a.get("oracle_id"):
                continue
            row = self._resolve_oracle(
                s, str(a["oracle_id"]), a.get("oracle_version"), workspace_id=workspace_id
            )
            a["oracle_version"] = int(row.version)
            pinned[a["assertion_id"]] = {
                **(pinned.get(a["assertion_id"]) or {}),
                "oracle_id": a["oracle_id"],
                "oracle_version": int(row.version),
                "approved": True,
                "approved_by": row.approved_by,
                "workspace_id": row.workspace_id,
                "pinned_at": _now(),
            }
        doc["oracle_bindings"] = pinned
        doc["oracle_binding_frozen"] = True
        return doc

    def latest_approved_version(self, testcase_id: str) -> dict[str, Any] | None:
        with self.db.session() as s:
            row = s.scalar(
                select(TestCaseVersionRow)
                .where(
                    TestCaseVersionRow.testcase_id == testcase_id,
                    TestCaseVersionRow.review_status == "APPROVED",
                )
                .order_by(TestCaseVersionRow.version.desc())
                .limit(1)
            )
            if not row:
                return None
            return dict(row.doc or {})

    # ------------------------------------------------------- stability gate (F08)
    def _stability_gate(
        self,
        s: Any,
        testcase_id: str,
        version: int,
        doc: dict[str, Any],
        *,
        workspace_id: str,
        require: bool = True,
    ) -> tuple[dict[str, Any] | None, str]:
        """正式审核的稳定性回放入口。

        返回 `(evidence, policy)`：
        - `policy = "ENFORCED"`：采纳了一条合法回放，`evidence` 是留痕摘要；
        - `policy = "EXEMPT_HUMAN_AUTHORED"`：人工编写版本，记录豁免事实（不是静默跳过）；
        - `policy = "SKIPPED_BY_CALLER"`：调用方显式 `require_stability=False`（仅用于
          历史数据修复/迁移，正常审核路径不得使用）。
        """
        compile_source = str(doc.get("compile_source") or "human")
        if not require:
            return None, "SKIPPED_BY_CALLER"
        if compile_source == "human":
            # 人工编写用例没有"候选晋升"语义；仍然留痕，便于审计追溯
            return None, "EXEMPT_HUMAN_AUTHORED"

        version_ref = f"{testcase_id}@{version}"
        rows = s.scalars(
            select(DocRow)
            .where(DocRow.kind == stability_gate.REPLAY_KIND, DocRow.ref_id == version_ref)
            .order_by(DocRow.created_at.desc())
            .limit(20)
        ).all()
        if not rows:
            raise AppError(
                ErrorCode.PRECONDITION_FAILED,
                f"STABILITY_REPLAY_REQUIRED: {version_ref} 没有任何稳定性回放记录"
                f"（compile_source={compile_source}）",
                details={"testcase_version_ref": version_ref, "compile_source": compile_source},
            )

        best: list[str] | None = None
        for row in rows:
            problems = stability_gate.replay_problems(row.doc or {}, version_ref)
            if not problems:
                return stability_gate.gate_evidence(row.doc or {}), "ENFORCED"
            if best is None or len(problems) < len(best):
                best = problems
        raise AppError(
            ErrorCode.PRECONDITION_FAILED,
            "STABILITY_REPLAY_GATE_FAILED: " + "; ".join((best or [])[:6]),
            details={
                "problems": best or [],
                "testcase_version_ref": version_ref,
                "compile_source": compile_source,
            },
        )

    # --------------------------------------------------- atomic promotion (CAS)
    def approved_version_number(self, testcase_id: str) -> int:
        """当前正式（APPROVED）版本号；0 表示还没有正式版本。"""
        with self.db.session() as s:
            return self._approved_version_in_session(s, testcase_id)

    @staticmethod
    def _approved_version_in_session(s: Any, testcase_id: str) -> int:
        value = s.scalar(
            select(TestCaseVersionRow.version)
            .where(
                TestCaseVersionRow.testcase_id == testcase_id,
                TestCaseVersionRow.review_status == "APPROVED",
            )
            .order_by(TestCaseVersionRow.version.desc())
            .limit(1)
        )
        return int(value or 0)

    def promote_version_cas(
        self,
        testcase_id: str,
        *,
        base_version: int,
        candidate: dict[str, Any],
        workspace_id: str = DEFAULT_WORKSPACE,
        title: str = "",
        idempotency_key: str = "",
        proposal_id: str = "",
    ) -> dict[str, Any]:
        """正式版本晋升：比较 + 写入 + 幂等回执在同一个数据库事务内完成（真正原子 CAS）。

        两个基于同一 base_version 的并发晋升只允许一个成功，另一个得到 VERSION_CONFLICT；
        绝不"先在事务 A 比较，再在事务 B 无条件创建版本"。

        RUN-007 WP1-02：传 `idempotency_key` 时，回执与版本指针在同一事务提交，
        因此"CAS 已提交但进程随后崩溃"的重试会命中回执并返回原结果，
        不会被误判成 VERSION_CONFLICT。
        """
        with self.db.session() as s:
            replay = self._receipt_replay(s, idempotency_key, proposal_id, kind="PROMOTE")
            if replay is not None:
                return replay
            row = s.get(TestCaseRow, testcase_id)
            if row is None:
                raise AppError(ErrorCode.NOT_FOUND, "testcase not found")
            current = self._approved_version_in_session(s, testcase_id)
            if current != base_version:
                raise AppError(
                    ErrorCode.CONFLICT,
                    f"VERSION_CONFLICT: current_approved={current} base={base_version}",
                    details={"current_approved_version": current, "base_version": base_version},
                )
            expected_latest = int(row.latest_version or 0)
            new_version = max(expected_latest, current) + 1
            doc = self._promoted_doc(candidate, new_version=new_version)
            updated = s.execute(
                update(TestCaseRow)
                .where(
                    TestCaseRow.testcase_id == testcase_id,
                    TestCaseRow.latest_version == expected_latest,
                )
                .values(
                    latest_version=new_version,
                    review_status="APPROVED",
                    updated_at=_now(),
                    compile_source=doc.get("compile_source", row.compile_source),
                    title=title or row.title,
                )
            )
            if updated.rowcount != 1:
                raise AppError(
                    ErrorCode.CONFLICT,
                    "VERSION_CONFLICT: latest_version 已被并发修改",
                )
            s.add(
                TestCaseVersionRow(
                    id=f"{testcase_id}@{new_version}",
                    testcase_id=testcase_id,
                    workspace_id=workspace_id,
                    version=new_version,
                    review_status="APPROVED",
                    review_revision=1,
                    immutable=True,
                    doc=doc,
                )
            )
            self._write_receipt(
                s,
                idempotency_key=idempotency_key,
                kind="PROMOTE",
                proposal_id=proposal_id,
                testcase_id=testcase_id,
                workspace_id=workspace_id,
                base_version=base_version,
                target_version=new_version,
                result={"version": new_version, "testcase_id": testcase_id, "kind": "PROMOTE"},
            )
            s.flush()
            return doc

    def rollback_version_cas(
        self,
        testcase_id: str,
        *,
        promoted_version: int,
        restored: dict[str, Any],
        workspace_id: str = DEFAULT_WORKSPACE,
        title: str = "",
        idempotency_key: str = "",
        proposal_id: str = "",
    ) -> dict[str, Any]:
        """回滚：只有"当前正式版本仍是本提案晋升的版本"时才允许回滚。

        与晋升相同：幂等回执与版本指针在同一事务提交，重试可确定性恢复。
        """
        with self.db.session() as s:
            replay = self._receipt_replay(s, idempotency_key, proposal_id, kind="ROLLBACK")
            if replay is not None:
                return replay
            row = s.get(TestCaseRow, testcase_id)
            if row is None:
                raise AppError(ErrorCode.NOT_FOUND, "testcase not found")
            current = self._approved_version_in_session(s, testcase_id)
            if current != promoted_version:
                raise AppError(
                    ErrorCode.CONFLICT,
                    f"ROLLBACK_CONFLICT: current_approved={current} promoted={promoted_version}",
                    details={
                        "current_approved_version": current,
                        "promoted_version": promoted_version,
                    },
                )
            expected_latest = int(row.latest_version or 0)
            new_version = max(expected_latest, current) + 1
            doc = self._promoted_doc(restored, new_version=new_version)
            updated = s.execute(
                update(TestCaseRow)
                .where(
                    TestCaseRow.testcase_id == testcase_id,
                    TestCaseRow.latest_version == expected_latest,
                )
                .values(
                    latest_version=new_version,
                    review_status="APPROVED",
                    updated_at=_now(),
                    title=title or row.title,
                )
            )
            if updated.rowcount != 1:
                raise AppError(ErrorCode.CONFLICT, "ROLLBACK_CONFLICT: latest_version 已被并发修改")
            s.add(
                TestCaseVersionRow(
                    id=f"{testcase_id}@{new_version}",
                    testcase_id=testcase_id,
                    workspace_id=workspace_id,
                    version=new_version,
                    review_status="APPROVED",
                    review_revision=1,
                    immutable=True,
                    doc=doc,
                )
            )
            self._write_receipt(
                s,
                idempotency_key=idempotency_key,
                kind="ROLLBACK",
                proposal_id=proposal_id,
                testcase_id=testcase_id,
                workspace_id=workspace_id,
                base_version=promoted_version,
                target_version=new_version,
                result={"version": new_version, "testcase_id": testcase_id, "kind": "ROLLBACK"},
            )
            s.flush()
            return doc

    # -------------------------------------------------------- promotion receipts
    @staticmethod
    def _receipt_replay(
        s: Any, idempotency_key: str, proposal_id: str, *, kind: str
    ) -> dict[str, Any] | None:
        """命中已提交的回执 → 返回原结果（幂等重放），不再动版本指针。

        两个来源：
        1. 同一幂等键（正常重试）；
        2. 同一 proposal 的同类回执（不同幂等键重试同一动作）——避免重复创建版本。
        """
        if idempotency_key:
            row = s.get(PromotionReceiptRow, idempotency_key)
            if row is not None:
                return {**dict(row.result or {}), "idempotent_hit": True, "matched_by": "key"}
        if proposal_id:
            row = s.scalar(
                select(PromotionReceiptRow)
                .where(
                    PromotionReceiptRow.proposal_id == proposal_id,
                    PromotionReceiptRow.kind == kind,
                )
                .order_by(PromotionReceiptRow.created_at.desc())
                .limit(1)
            )
            if row is not None:
                return {**dict(row.result or {}), "idempotent_hit": True, "matched_by": "proposal"}
        return None

    @staticmethod
    def _write_receipt(
        s: Any,
        *,
        idempotency_key: str,
        kind: str,
        proposal_id: str,
        testcase_id: str,
        workspace_id: str,
        base_version: int,
        target_version: int,
        result: dict[str, Any],
    ) -> None:
        if not idempotency_key:
            return
        existing = s.get(PromotionReceiptRow, idempotency_key)
        if existing is not None:
            return  # 同一事务内重复写回执：保持第一次的结果
        s.add(
            PromotionReceiptRow(
                idempotency_key=idempotency_key,
                kind=kind,
                proposal_id=proposal_id,
                testcase_id=testcase_id,
                workspace_id=workspace_id,
                base_version=base_version,
                target_version=target_version,
                result=result,
                created_at=_now(),
            )
        )

    def get_promotion_receipt(self, idempotency_key: str) -> dict[str, Any] | None:
        with self.db.session() as s:
            row = s.get(PromotionReceiptRow, idempotency_key)
            return _receipt_dict(row) if row else None

    def find_promotion_receipt(
        self, proposal_id: str, *, kind: str | None = None
    ) -> dict[str, Any] | None:
        with self.db.session() as s:
            q = select(PromotionReceiptRow).where(PromotionReceiptRow.proposal_id == proposal_id)
            if kind:
                q = q.where(PromotionReceiptRow.kind == kind)
            row = s.scalar(q.order_by(PromotionReceiptRow.created_at.desc()).limit(1))
            return _receipt_dict(row) if row else None

    @staticmethod
    def _promoted_doc(candidate: dict[str, Any], *, new_version: int) -> dict[str, Any]:
        """构造正式版本文档：断言与 Oracle 绑定原样保留，不因晋升而放宽。"""
        doc = {
            k: (dict(v) if isinstance(v, dict) else list(v) if isinstance(v, list) else v)
            for k, v in candidate.items()
        }
        doc.pop("review_history", None)
        doc["version"] = new_version
        doc["review_status"] = "APPROVED"
        doc["created_at"] = _now()
        return doc

    # ---------------- Oracle ----------------
    def create_oracle(
        self,
        payload: dict[str, Any],
        *,
        approved_by: str = "",
        approval_note: str = "",
        approved: bool = False,
    ) -> dict[str, Any]:
        spec = OracleSpec(**{k: v for k, v in payload.items() if k in OracleSpec.model_fields})
        with self.db.session() as s:
            prev = s.scalar(
                select(OracleRow)
                .where(OracleRow.oracle_id == spec.oracle_id)
                .order_by(OracleRow.version.desc())
                .limit(1)
            )
            version = (prev.version + 1) if prev else spec.version
            spec.version = version
            if approved:
                # 主体类型校验（不接受 agent:*/llm:*/system:* 等非人类主体）
                approved_by = require_human_actor(approved_by, field="approved_by")
            doc = spec.model_dump()
            doc["approval_history"] = [
                {
                    "approved": approved,
                    "approved_by": approved_by,
                    "note": approval_note,
                    "at": _now(),
                }
            ]
            # OracleManager 校验读取 expectation_approval，必须与行级审批状态一致
            doc["expectation_approval"] = {
                "approved": bool(approved),
                "approver": approved_by,
                "note": approval_note,
                "at": doc["approval_history"][0]["at"],
            }
            s.add(
                OracleRow(
                    id=f"{spec.oracle_id}@{version}",
                    oracle_id=spec.oracle_id,
                    workspace_id=payload.get("workspace_id", DEFAULT_WORKSPACE),
                    version=version,
                    assertion_id=spec.assertion_id,
                    approved=approved,
                    approved_by=approved_by,
                    doc=doc,
                )
            )
            s.flush()
            return doc

    def get_oracle(self, oracle_id: str, version: int | None = None) -> dict[str, Any] | None:
        with self.db.session() as s:
            if version is not None:
                r = s.get(OracleRow, f"{oracle_id}@{version}")
            else:
                r = s.scalar(
                    select(OracleRow)
                    .where(OracleRow.oracle_id == oracle_id)
                    .order_by(OracleRow.version.desc())
                    .limit(1)
                )
            if not r:
                return None
            doc = dict(r.doc or {})
            doc["version"] = r.version
            doc["approved"] = r.approved
            doc["approved_by"] = r.approved_by
            doc["workspace_id"] = r.workspace_id
            return doc

    def list_oracles(self, workspace_id: str | None = None) -> list[dict[str, Any]]:
        with self.db.session() as s:
            q = select(OracleRow)
            if workspace_id:
                q = q.where(OracleRow.workspace_id == workspace_id)
            rows = s.scalars(q.order_by(OracleRow.created_at.desc())).all()
            return [
                {
                    "oracle_id": r.oracle_id,
                    "version": r.version,
                    "assertion_id": r.assertion_id,
                    "approved": r.approved,
                    "approved_by": r.approved_by,
                    "expected_behavior": (r.doc or {}).get("expected_behavior", ""),
                    "created_at": r.created_at,
                }
                for r in rows
            ]


def _receipt_dict(r: PromotionReceiptRow) -> dict[str, Any]:
    return {
        "idempotency_key": r.idempotency_key,
        "kind": r.kind,
        "proposal_id": r.proposal_id,
        "testcase_id": r.testcase_id,
        "workspace_id": r.workspace_id,
        "base_version": r.base_version,
        "target_version": r.target_version,
        "result": dict(r.result or {}),
        "created_at": r.created_at,
    }


def _intent_dict(r: IntentRow) -> dict[str, Any]:
    out = dict(r.doc or {})
    out["status"] = r.status
    out["version"] = r.version
    out["workspace_id"] = r.workspace_id
    out["created_at"] = r.created_at
    out["updated_at"] = r.updated_at
    return out
