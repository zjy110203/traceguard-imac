"""候选用例稳定性回放（原路线 F08）。

约束：
- 每次回放产生**独立 Run**，不覆盖历史；
- 走独立 sandbox 入口（Task.mode="sandbox"），不放开正式执行接口绕过审核；
- 记录环境、版本、每次 Verdict；不稳定候选不得晋升；
- Oracle 不完整时不得宣称业务预期已验证（verdict 只能是 NOT_EVALUATED / INCONCLUSIVE）；
- 回放次数与稳定率阈值按风险场景可配置，本模块不设全项目统一阈值；
- 最终晋升仍必须人工审核。
"""

from __future__ import annotations

from typing import Any

from traceguard_agents.compiler.compiler import TestCaseCompiler
from traceguard_core.errors import AppError, ErrorCode
from traceguard_core.ids import new_id, utc_now_iso

REPLAY_KIND = "stability_replay"

#: 按风险场景的默认回放次数（仅默认建议，调用方必须可覆盖；不是全项目统一冻结值）
DEFAULT_REPEATS_BY_RISK = {"low": 2, "default": 3, "high": 5}


class StabilityReplayService:
    def __init__(self, rt: Any) -> None:
        self.rt = rt

    def start(
        self,
        testcase_id: str,
        version: int,
        *,
        repeats: int | None = None,
        risk_profile: str = "default",
        sync: bool = False,
        execute_fn: Any = None,
    ) -> dict[str, Any]:
        base = self.rt.catalog.get_version(testcase_id, version)
        if not base:
            raise AppError(ErrorCode.NOT_FOUND, "testcase version not found")
        if base.get("review_status") == "APPROVED":
            # 正式版本不需要"候选回放"；仍需可以回放，但语义要标注清楚
            pass
        risk = (risk_profile or "default").lower()
        n = int(repeats) if repeats is not None else DEFAULT_REPEATS_BY_RISK.get(risk, 3)
        if not 1 <= n <= 10:
            raise AppError(ErrorCode.VALIDATION_ERROR, "repeats 必须在 1..10 之间")

        replay_id = new_id("replay")
        records: list[dict[str, Any]] = []
        for idx in range(n):
            task = self.rt.tasks.create_task(
                testcase_id,
                f"replay-{replay_id}-{idx}",
                testcase_version_id=f"{testcase_id}@{version}",
                mode="sandbox",
            )
            run = self.rt.tasks.create_run(task["task_id"], f"{testcase_id}@{version}")
            record = {
                "index": idx,
                "task_id": task["task_id"],
                "run_id": run["run_id"],
                "execution_status": run.get("execution_status", "QUEUED"),
                "test_verdict": run.get("test_verdict", "NOT_EVALUATED"),
                "failure_class": "",
            }
            if sync and execute_fn is not None:
                outcome = execute_fn(run["run_id"])
                record["execution_status"] = outcome.get("execution_status", "")
                record["test_verdict"] = outcome.get("test_verdict", "")
                record["failure_class"] = _classify_failure(outcome.get("report") or {})
            records.append(record)

        classification = self._classify(records)
        oracle_complete = _oracle_complete(base)
        doc = {
            "replay_id": replay_id,
            "testcase_id": testcase_id,
            "version": version,
            "risk_profile": risk,
            "repeats": n,
            "sandbox_only": True,
            "environment": {
                "teamflow_base_url": self.rt.settings.teamflow_base_url,
                "browser_headless": self.rt.settings.browser_headless,
                "browser_channel": self.rt.settings.browser_channel or "bundled-chromium",
                "database_dialect": self.rt.db.url.split(":")[0],
                "worker_id": self.rt.worker_id,
            },
            "records": records,
            "classification": classification,
            "oracle_complete": oracle_complete,
            "promotable": bool(
                classification["stable"]
                and classification["classification"] == "STABLE_PASS"
                and oracle_complete
            ),
            "promotion_note": (
                "不稳定或 Oracle 不完整的候选不得晋升；即使稳定也必须人工审核（Agent 不能自批）"
            ),
            "oracle_note": (
                "Oracle 不完整时不得宣称业务预期已验证"
                if not oracle_complete
                else ""
            ),
            "status": "COMPLETED" if sync else "RUNNING",
            "created_at": utc_now_iso(),
            "completed_at": utc_now_iso() if sync else None,
            "requires_human_review": True,
        }
        self.rt.docs.put(
            doc_id=replay_id,
            kind=REPLAY_KIND,
            doc=doc,
            workspace_id=base.get("workspace_id", "default"),
            ref_id=f"{testcase_id}@{version}",
            status=doc["status"],
        )
        return doc

    def refresh(self, replay_id: str) -> dict[str, Any]:
        doc = self.get(replay_id)
        changed = False
        for record in doc["records"]:
            run = self.rt.tasks.get_run(record["run_id"])
            if not run:
                continue
            if (
                record["test_verdict"] != run["test_verdict"]
                or record["execution_status"] != run["execution_status"]
            ):
                record["test_verdict"] = run["test_verdict"]
                record["execution_status"] = run["execution_status"]
                record["failure_class"] = _classify_failure(run.get("report") or {})
                changed = True
        if not changed:
            return doc
        doc["classification"] = self._classify(doc["records"])
        doc["promotable"] = bool(
            doc["classification"]["stable"]
            and doc["classification"]["classification"] == "STABLE_PASS"
            and doc["oracle_complete"]
        )
        if all(r["execution_status"] in {"COMPLETED", "FAILED", "CANCELLED"} for r in doc["records"]):
            doc["status"] = "COMPLETED"
            doc["completed_at"] = utc_now_iso()
        self.rt.docs.put(
            doc_id=replay_id,
            kind=REPLAY_KIND,
            doc=doc,
            workspace_id=doc.get("workspace_id", "default"),
            ref_id=f"{doc['testcase_id']}@{doc['version']}",
            status=doc["status"],
        )
        return doc

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
                    "classification": rec.doc.get("classification", {}),
                    "promotable": rec.doc.get("promotable", False),
                    "created_at": rec.doc.get("created_at"),
                }
            )
        return out

    def _classify(self, records: list[dict[str, Any]]) -> dict[str, Any]:
        verdicts = [r["test_verdict"] for r in records if r["test_verdict"] != "PENDING"]
        result = TestCaseCompiler().validate_stability(verdicts)
        failure_classes: dict[str, int] = {}
        for r in records:
            key = r.get("failure_class") or ("NONE" if r["test_verdict"] == "PASSED" else "UNKNOWN")
            failure_classes[key] = failure_classes.get(key, 0) + 1
        result["failure_classes"] = failure_classes
        result["verdicts"] = verdicts
        return result


def _oracle_complete(version_doc: dict[str, Any]) -> bool:
    assertions = version_doc.get("assertions") or []
    required = [a for a in assertions if a.get("required", True)]
    if not required:
        return False
    return all(a.get("oracle_id") for a in required)


def _classify_failure(report: dict[str, Any]) -> str:
    """把失败归到更具体的原因类别，而不是一律 Flaky。"""
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
                return "BUSINESS"
            if kind == "network":
                return "NETWORK"
            return "UI"
    for o in outcomes:
        if o.get("status") == "INCONCLUSIVE":
            return "EVIDENCE_INSUFFICIENT"
    if report.get("errors"):
        return "EXECUTION"
    return ""


__all__ = ["DEFAULT_REPEATS_BY_RISK", "REPLAY_KIND", "StabilityReplayService"]
