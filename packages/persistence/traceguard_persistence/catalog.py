"""测试资产目录：Workspace / TestIntent / TestCaseVersion / Oracle。

不变量：
- TestCaseVersion 内容不可变（`immutable=True`），流程状态（review_status）可流转；
- Oracle 版本化，Agent 不能批准（`approved_by_agent` 恒 False），审批人必须是具名角色；
- 只有 APPROVED 的用例版本才允许被正式执行（执行器强制校验）。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from traceguard_contracts.models import OracleSpec, TestCaseVersion, TestIntent
from traceguard_core.errors import AppError, ErrorCode
from traceguard_core.ids import new_id, utc_now_iso

from .db import Database
from .models import IntentRow, OracleRow, TestCaseRow, TestCaseVersionRow, WorkspaceRow

DEFAULT_WORKSPACE = "default"


def _now() -> str:
    return utc_now_iso()


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
        intent = TestIntent(**payload)
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
        """创建用例新版本；已存在则递增版本，历史版本不被覆盖。"""
        tc = TestCaseVersion(**payload)
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
        self, testcase_id: str, version: int, decision: str, reviewer: str, note: str = ""
    ) -> dict[str, Any]:
        if decision not in {"APPROVED", "REJECTED", "PENDING"}:
            raise AppError(ErrorCode.VALIDATION_ERROR, "decision must be APPROVED/REJECTED/PENDING")
        if not reviewer or reviewer.lower() in {"agent", "llm", "system"}:
            raise AppError(ErrorCode.PRECONDITION_FAILED, "review requires a named human reviewer")
        with self.db.session() as s:
            v = s.get(TestCaseVersionRow, f"{testcase_id}@{version}")
            if not v:
                raise AppError(ErrorCode.NOT_FOUND, "testcase version not found")
            v.review_status = decision
            doc = dict(v.doc or {})
            doc["review_status"] = decision
            doc.setdefault("review_history", []).append(
                {"decision": decision, "reviewer": reviewer, "note": note, "at": _now()}
            )
            v.doc = doc
            tc = s.get(TestCaseRow, testcase_id)
            if tc:
                tc.review_status = decision
                tc.updated_at = _now()
            s.flush()
            return {"testcase_id": testcase_id, "version": version, "review_status": decision}

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

    # ---------------- Oracle ----------------
    def create_oracle(
        self,
        payload: dict[str, Any],
        *,
        approved_by: str = "",
        approval_note: str = "",
        approved: bool = False,
    ) -> dict[str, Any]:
        spec = OracleSpec(**payload)
        with self.db.session() as s:
            prev = s.scalar(
                select(OracleRow)
                .where(OracleRow.oracle_id == spec.oracle_id)
                .order_by(OracleRow.version.desc())
                .limit(1)
            )
            version = (prev.version + 1) if prev else spec.version
            spec.version = version
            if approved and (not approved_by or approved_by.lower() in {"agent", "llm"}):
                raise AppError(
                    ErrorCode.PRECONDITION_FAILED,
                    "oracle approval requires a named human approver (agent cannot approve)",
                )
            doc = spec.model_dump()
            doc["approval_history"] = [
                {
                    "approved": approved,
                    "approved_by": approved_by,
                    "note": approval_note,
                    "at": _now(),
                }
            ]
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


def _intent_dict(r: IntentRow) -> dict[str, Any]:
    out = dict(r.doc or {})
    out["status"] = r.status
    out["version"] = r.version
    out["workspace_id"] = r.workspace_id
    out["created_at"] = r.created_at
    out["updated_at"] = r.updated_at
    return out
