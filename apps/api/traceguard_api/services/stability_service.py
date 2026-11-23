"""候选用例稳定性回放（原路线 F08）。

RUN-007 WP1-01 强化后的不变量：

- 每次回放产生**独立 Run**，不覆盖历史；
- 走独立 sandbox 入口（Task.mode="sandbox"），不放开正式执行接口绕过审核；
- Run 级状态区分 `PENDING / NOT_EVALUATED / INCONCLUSIVE / PASSED / FAILED`，
  **未完成的 Run 不参与最终结论**，`NOT_EVALUATED` 永远不会被当成"确定性失败"；
- 只有在"全部 Run 到达终态 + 全部必需 Oracle 完整且版本固定"时才形成 `conclusion`，
  否则回放状态保持 `RUNNING`（或 `INCOMPLETE`），结论为 `None`；
- 记录浏览器版本、执行环境、用例版本指纹、Oracle 版本；
- 不稳定候选不得晋升；稳定候选仍必须人工审核（Agent 不能自批）；
- `review_testcase(APPROVED)` 通过 `stability_service.gate_problems()` 强制消费本模块结论。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from traceguard_agents.compiler.compiler import TestCaseCompiler
from traceguard_core.errors import AppError, ErrorCode
from traceguard_core.ids import new_id, utc_now_iso
from traceguard_persistence.stability_gate import (
    REPLAY_COMPLETED,
    REPLAY_KIND,
    TERMINAL_EXECUTION_STATUS,
    replay_problems,
)

#: 按风险场景的默认回放次数（仅默认建议，调用方必须可覆盖；不是全项目统一冻结值）
DEFAULT_REPEATS_BY_RISK = {"low": 2, "default": 3, "high": 5}

#: 回放状态
REPLAY_PENDING = "PENDING"
REPLAY_RUNNING = "RUNNING"
REPLAY_INCOMPLETE = "INCOMPLETE"


class StabilityReplayService:
    def __init__(self, rt: Any) -> None:
        self.rt = rt

    # ------------------------------------------------------------------ create
    def start(
        self,
        testcase_id: str,
        version: int,
        *,
        repeats: int | None = None,
        risk_profile: str = "default",
        sync: bool = False,
        execute_fn: Any = None,
        requested_by: str = "role:qa-lead",
        wait_timeout_s: float | None = None,
    ) -> dict[str, Any]:
        base = self.rt.catalog.get_version(testcase_id, version)
        if not base:
            raise AppError(ErrorCode.NOT_FOUND, "testcase version not found")
        risk = (risk_profile or "default").lower()
        n = int(repeats) if repeats is not None else DEFAULT_REPEATS_BY_RISK.get(risk, 3)
        if not 1 <= n <= 10:
            raise AppError(ErrorCode.VALIDATION_ERROR, "repeats 必须在 1..10 之间")

        version_ref = f"{testcase_id}@{version}"
        replay_id = new_id("replay")
        oracle_versions = _pinned_oracle_versions(base)

        records: list[dict[str, Any]] = []
        for idx in range(n):
            task = self.rt.tasks.create_task(
                testcase_id,
                f"replay-{replay_id}-{idx}",
                testcase_version_id=version_ref,
                mode="sandbox",
                workspace_id=base.get("workspace_id", "default"),
            )
            run = self.rt.tasks.create_run(task["task_id"], version_ref)
            records.append(
                {
                    "index": idx,
                    "task_id": task["task_id"],
                    "run_id": run["run_id"],
                    "execution_status": run.get("execution_status") or "QUEUED",
                    "test_verdict": run.get("test_verdict") or "PENDING",
                    "failure_class": "",
                    "claimed_by": "",
                    "browser_version": "",
                    "started_at": None,
                    "ended_at": None,
                }
            )

        if sync and execute_fn is not None:
            for record in records:
                outcome = execute_fn(record["run_id"])
                _absorb_run_into_record(record, outcome, self.rt)

        oracle_ok, oracle_problems = self._oracle_integrity(
            base, base.get("workspace_id", "default")
        )
        doc: dict[str, Any] = {
            "replay_id": replay_id,
            "workspace_id": base.get("workspace_id", "default"),
            "testcase_id": testcase_id,
            "version": version,
            "testcase_version_ref": version_ref,
            "testcase_fingerprint": _fingerprint(base),
            "risk_profile": risk,
            "repeats": n,
            "sandbox_only": True,
            "requested_by": requested_by,
            "environment": _environment(self.rt),
            "browser_versions": sorted(
                {r["browser_version"] for r in records if r["browser_version"]}
            ),
            "oracle_versions": oracle_versions,
            "oracle_complete": oracle_ok,
            "oracle_problems": oracle_problems,
            "oracle_note": ("Oracle 不完整时不得宣称业务预期已验证" if not oracle_ok else ""),
            "records": records,
            "conclusion": None,
            "classification": None,
            "promotable": False,
            "status": REPLAY_RUNNING,
            "created_at": utc_now_iso(),
            "completed_at": None,
            "requires_human_review": True,
            "promotion_note": (
                "不稳定或 Oracle 不完整的候选不得晋升；即使稳定也必须人工审核（Agent 不能自批）"
            ),
        }
        self._settle(doc)
        self._persist(doc, base.get("workspace_id", "default"))

        if sync and wait_timeout_s:
            doc = self.wait(replay_id, timeout_s=wait_timeout_s)
        return doc

    # ------------------------------------------------------------------ read
    def get(self, replay_id: str) -> dict[str, Any]:
        rec = self.rt.docs.get(replay_id)
        if not rec or rec.kind != REPLAY_KIND:
            raise AppError(ErrorCode.NOT_FOUND, "stability replay not found")
        return rec.to_dict()

    def list(self, testcase_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        ref = f"{testcase_id}@" if testcase_id else None
        recs = self.rt.docs.list(kind=REPLAY_KIND, limit=limit)
        out = []
        for rec in recs:
            if ref and not str(rec.ref_id or "").startswith(ref):
                continue
            out.append(
                {
                    "replay_id": rec.id,
                    "testcase_ref": rec.ref_id,
                    "status": rec.status,
                    "classification": (rec.doc.get("classification") or {}).get("classification"),
                    "promotable": rec.doc.get("promotable", False),
                    "created_at": rec.doc.get("created_at"),
                }
            )
        return out

    # ------------------------------------------------------------------ refresh
    def refresh(self, replay_id: str) -> dict[str, Any]:
        """把 Run 的最新状态吸收进回放文档，并在条件满足时才形成最终结论。"""
        doc = self.get(replay_id)
        changed = False
        for record in doc["records"]:
            run = self.rt.tasks.get_run(record["run_id"])
            if not run:
                continue
            if (
                record["test_verdict"] != run["test_verdict"]
                or record["execution_status"] != run["execution_status"]
                or record.get("claimed_by") != (run.get("lease_owner") or "")
            ):
                _absorb_run_into_record(record, run, self.rt)
                changed = True
        if changed or doc.get("conclusion") is None:
            self._settle(doc)
        self._persist(doc, doc.get("workspace_id", "default"))
        return doc

    def wait(
        self, replay_id: str, *, timeout_s: float = 60.0, interval_s: float = 0.2
    ) -> dict[str, Any]:
        """等待所有 Run 到达终态（异步 Worker 消费路径用）。超时返回当前快照，状态仍 RUNNING。"""
        import time

        deadline = time.time() + max(0.0, timeout_s)
        doc = self.refresh(replay_id)
        while doc["status"] == REPLAY_RUNNING and time.time() < deadline:
            time.sleep(interval_s)
            doc = self.refresh(replay_id)
        if doc["status"] == REPLAY_RUNNING:
            # 未完成：保留真实状态，不生成结论（也不降级成任何"通过"）
            doc["blocked_reason"] = "REPLAY_NOT_CONCLUDED_WITHIN_TIMEOUT"
            self._persist(doc, doc.get("workspace_id", "default"))
        return doc

    # ------------------------------------------------------------------ settle
    def _settle(self, doc: dict[str, Any]) -> None:
        """根据记录计算结论。未完成 → 结论为 None，绝不用未执行样本凑数。"""
        records = doc["records"]
        pending = [r for r in records if r["execution_status"] not in TERMINAL_EXECUTION_STATUS]
        verdicts = [r["test_verdict"] for r in records]
        expected = int(doc.get("repeats", len(records)))

        doc["samples_expected"] = expected
        doc["samples_recorded"] = len(records)
        doc["pending_runs"] = [r["run_id"] for r in pending]
        doc["worker_consumed"] = (
            all(bool(r.get("claimed_by")) for r in records) if records else False
        )

        if pending:
            doc["status"] = REPLAY_RUNNING
            doc["conclusion"] = None
            doc["classification"] = None
            doc["promotable"] = False
            doc["completed_at"] = None
            return

        result = TestCaseCompiler().validate_stability(verdicts, expected_samples=expected)
        result["failure_classes"] = _failure_class_histogram(records)
        result["execution_failures"] = sorted(
            {r["execution_status"] for r in records if r["execution_status"] != "COMPLETED"}
        )
        doc["classification"] = result
        doc["conclusion"] = result["classification"]
        if result["classification"] == "INCOMPLETE":
            doc["status"] = REPLAY_INCOMPLETE
            doc["completed_at"] = None
        else:
            doc["status"] = REPLAY_COMPLETED
            doc["completed_at"] = doc.get("completed_at") or utc_now_iso()
        doc["promotable"] = bool(
            doc["status"] == REPLAY_COMPLETED
            and result["classification"] == "STABLE_PASS"
            and doc.get("oracle_complete")
            and not pending
        )

    def _persist(self, doc: dict[str, Any], workspace_id: str) -> None:
        self.rt.docs.put(
            doc_id=doc["replay_id"],
            kind=REPLAY_KIND,
            doc=doc,
            workspace_id=workspace_id,
            ref_id=doc["testcase_version_ref"],
            status=doc["status"],
        )

    # ------------------------------------------------------------------ oracle
    def _oracle_integrity(
        self, version_doc: dict[str, Any], workspace_id: str
    ) -> tuple[bool, list[str]]:
        """Oracle 完整性：不只看 oracle_id 是否存在，还要看具体版本是否已审批、是否跨 Workspace。"""
        assertions = version_doc.get("assertions") or []
        required = [a for a in assertions if a.get("required", True)]
        if not required:
            return False, ["NO_REQUIRED_ASSERTION: 用例没有任何必需断言"]
        problems: list[str] = []
        for a in required:
            aid = a.get("assertion_id")
            oracle_id = a.get("oracle_id")
            if not oracle_id:
                problems.append(f"MISSING_ORACLE: {aid}")
                continue
            pinned = a.get("oracle_version")
            if pinned is None:
                problems.append(f"ORACLE_VERSION_NOT_PINNED: {aid}")
                continue
            row = self.rt.catalog.get_oracle(str(oracle_id), int(pinned))
            if not row:
                problems.append(f"ORACLE_VERSION_NOT_FOUND: {oracle_id}@{pinned} ({aid})")
                continue
            if not row.get("approved"):
                problems.append(f"ORACLE_NOT_APPROVED: {oracle_id}@{pinned} ({aid})")
            if str(row.get("workspace_id") or "") != str(workspace_id or ""):
                problems.append(f"ORACLE_WORKSPACE_MISMATCH: {oracle_id}@{pinned} ({aid})")
        return (not problems), problems

    # ------------------------------------------------------------------- gate
    def gate_problems(
        self, testcase_id: str, version: int, *, workspace_id: str | None = None
    ) -> tuple[list[str], dict[str, Any] | None]:
        """正式审核门禁的**只读预览**：返回 (problems, evidence)。

        权威实现是 `traceguard_persistence.stability_gate.replay_problems()`，
        由 `Catalog.review_testcase()` 在同一事务内调用；本方法只用于前端/CLI 预检，
        不参与放行判定（避免出现两套口径）。
        """
        version_ref = f"{testcase_id}@{version}"
        recs = self.rt.docs.list(kind=REPLAY_KIND, ref_id=version_ref, limit=50)
        if not recs:
            return [f"STABILITY_REPLAY_REQUIRED: {version_ref} 没有任何稳定性回放"], None

        recs.sort(key=lambda r: str(r.doc.get("created_at") or ""), reverse=True)
        best_problems: list[str] | None = None
        best_doc: dict[str, Any] | None = None
        for rec in recs:
            doc = rec.to_dict()
            problems = replay_problems(doc, version_ref)
            if not problems:
                return [], doc
            if best_problems is None or len(problems) < len(best_problems):
                best_problems, best_doc = problems, doc
        return best_problems or ["STABILITY_REPLAY_INVALID"], best_doc


# --------------------------------------------------------------------- helpers
def _absorb_run_into_record(record: dict[str, Any], run: dict[str, Any], rt: Any) -> None:
    report = run.get("report") or {}
    record["execution_status"] = run.get("execution_status") or record["execution_status"]
    record["test_verdict"] = run.get("test_verdict") or record["test_verdict"]
    record["failure_class"] = _classify_failure(report)
    record["claimed_by"] = run.get("lease_owner") or record.get("claimed_by") or ""
    browser = (report or {}).get("browser") or {}
    if browser.get("version"):
        record["browser_version"] = str(browser["version"])
    record["started_at"] = run.get("started_at") or record.get("started_at")
    record["ended_at"] = run.get("ended_at") or record.get("ended_at")
    record["observer_mode"] = run.get("observer_mode") or record.get("observer_mode", "")
    if report.get("error") and not record.get("error"):
        record["error"] = report.get("error")


def _failure_class_histogram(records: list[dict[str, Any]]) -> dict[str, int]:
    hist: dict[str, int] = {}
    for r in records:
        key = r.get("failure_class") or ("NONE" if r.get("test_verdict") == "PASSED" else "UNKNOWN")
        hist[key] = hist.get(key, 0) + 1
    return hist


def _pinned_oracle_versions(version_doc: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for a in version_doc.get("assertions") or []:
        if a.get("oracle_id"):
            out[str(a["assertion_id"])] = f"{a['oracle_id']}@{a.get('oracle_version')}"
    return out


def _fingerprint(version_doc: dict[str, Any]) -> str:
    payload = {
        "testcase_id": version_doc.get("testcase_id"),
        "version": version_doc.get("version"),
        "assertions": [
            {
                "assertion_id": a.get("assertion_id"),
                "kind": a.get("kind"),
                "expected": a.get("expected"),
                "oracle_id": a.get("oracle_id"),
                "oracle_version": a.get("oracle_version"),
            }
            for a in (version_doc.get("assertions") or [])
        ],
        "steps": version_doc.get("steps"),
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _environment(rt: Any) -> dict[str, Any]:
    settings = rt.settings
    try:
        import playwright  # noqa: F401

        pw_version = getattr(playwright, "__version__", "unknown")
    except Exception:  # noqa: BLE001
        pw_version = "unavailable"
    return {
        "teamflow_base_url": settings.teamflow_base_url,
        "browser_headless": settings.browser_headless,
        "browser_channel": settings.browser_channel or "bundled-chromium",
        "playwright_version": pw_version,
        "database_dialect": str(rt.db.url).split(":")[0],
        "worker_id": rt.worker_id,
        "recorded_at": utc_now_iso(),
    }


def _classify_failure(report: dict[str, Any]) -> str:
    """把失败归到更具体的原因类别，而不是一律 Flaky。

    类别：`LOCATOR` / `TIMING` / `ENVIRONMENT` / `NETWORK` / `BUSINESS` / `UI` /
    `EVIDENCE_INSUFFICIENT` / `EXECUTION` / `PRODUCT`（业务断言失败）。
    """
    steps = report.get("steps") or []
    for s in steps:
        err = str(s.get("error", "")).lower()
        if "locator" in err or "waiting for" in err:
            return "LOCATOR"
        if "timeout" in err or "timed out" in err:
            return "TIMING"
        if "net::" in err or "econnrefused" in err:
            return "ENVIRONMENT"
    outcomes = report.get("assertion_outcomes") or []
    for o in outcomes:
        if o.get("status") == "FAILED":
            kind = o.get("kind", "ui")
            if kind in {"business_state", "api"}:
                return "PRODUCT"
            if kind == "network":
                return "NETWORK"
            return "UI"
    for o in outcomes:
        if o.get("status") == "INCONCLUSIVE":
            return "EVIDENCE_INSUFFICIENT"
    if report.get("errors"):
        return "EXECUTION"
    if report.get("test_verdict") == "NOT_EVALUATED":
        return "NOT_EVALUATED"
    return ""


__all__ = [
    "DEFAULT_REPEATS_BY_RISK",
    "REPLAY_COMPLETED",
    "REPLAY_INCOMPLETE",
    "REPLAY_KIND",
    "REPLAY_PENDING",
    "REPLAY_RUNNING",
    "StabilityReplayService",
]
