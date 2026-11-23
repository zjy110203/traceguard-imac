"""稳定性回放门禁的**唯一判定规则**（RUN-007 WP1-01）。

放在 persistence 层的原因：回放结论本身就是一条 `DocRow(kind="stability_replay")`，
审核事务需要在自己的 session 里直接读它；如果规则写在 API 服务里，Catalog 就无法
在同一个事务内校验，只能"先查再信"，会引入 TOCTOU 窗口。

这里的函数是纯函数：输入回放文档 + 待审核版本引用，输出 problems 列表。
**任何新增/修改门禁条件都必须改这个文件**，不要在两处各写一遍。
"""

from __future__ import annotations

from typing import Any

REPLAY_KIND = "stability_replay"

#: Run 终态（只有终态才允许参与结论）
TERMINAL_EXECUTION_STATUS = frozenset({"COMPLETED", "FAILED", "TIMED_OUT", "CANCELLED"})

#: 有效终态 verdict（PENDING / NOT_EVALUATED 不得计入稳定性样本）
TERMINAL_VERDICTS = frozenset({"PASSED", "FAILED", "INCONCLUSIVE"})

REPLAY_COMPLETED = "COMPLETED"


def replay_problems(doc: dict[str, Any], version_ref: str) -> list[str]:
    """校验一条回放文档能否作为 `version_ref` 的正式审核依据。"""
    problems: list[str] = []

    if not doc:
        return ["STABILITY_REPLAY_EMPTY"]

    doc_ref = doc.get("testcase_version_ref")
    if doc_ref != version_ref:
        # 旧版本回放不得自动适用于新版本
        problems.append(
            f"STABILITY_REPLAY_VERSION_MISMATCH: 回放属于 {doc_ref}，不能用于 {version_ref}"
        )

    if doc.get("status") != REPLAY_COMPLETED:
        problems.append(f"STABILITY_REPLAY_NOT_COMPLETED: 当前状态 {doc.get('status')}")

    records = doc.get("records") or []
    repeats = int(doc.get("repeats") or 0)
    if repeats <= 0:
        problems.append("STABILITY_REPLAY_NO_REPEATS: repeats 非法")
    if len(records) < repeats:
        problems.append(f"STABILITY_REPLAY_MISSING_SAMPLES: {len(records)}/{repeats}")

    pending = [r for r in records if r.get("execution_status") not in TERMINAL_EXECUTION_STATUS]
    if pending:
        problems.append(
            "STABILITY_REPLAY_HAS_PENDING_RUN: "
            + ",".join(str(r.get("run_id")) for r in pending[:4])
        )

    bad_verdicts = sorted(
        {
            str(r.get("test_verdict"))
            for r in records
            if r.get("test_verdict") not in TERMINAL_VERDICTS
        }
    )
    if bad_verdicts:
        problems.append("STABILITY_REPLAY_NON_TERMINAL_VERDICT: " + ",".join(bad_verdicts))

    classification = (doc.get("classification") or {}).get("classification")
    if classification != "STABLE_PASS":
        problems.append(f"STABILITY_NOT_STABLE_PASS: 实际为 {classification}")

    if not doc.get("oracle_complete"):
        detail = "; ".join((doc.get("oracle_problems") or [])[:4])
        problems.append(f"STABILITY_ORACLE_INCOMPLETE: {detail}".strip())

    if not doc.get("promotable"):
        problems.append("STABILITY_REPLAY_NOT_PROMOTABLE")

    return problems


def gate_evidence(doc: dict[str, Any]) -> dict[str, Any]:
    """审核留痕：把被采纳的回放关键事实写进 review_history。"""
    return {
        "replay_id": doc.get("replay_id"),
        "testcase_version_ref": doc.get("testcase_version_ref"),
        "testcase_fingerprint": doc.get("testcase_fingerprint"),
        "repeats": doc.get("repeats"),
        "classification": (doc.get("classification") or {}).get("classification"),
        "oracle_versions": doc.get("oracle_versions"),
        "browser_versions": doc.get("browser_versions"),
        "promotable": doc.get("promotable"),
        "completed_at": doc.get("completed_at"),
    }


__all__ = [
    "REPLAY_COMPLETED",
    "REPLAY_KIND",
    "TERMINAL_EXECUTION_STATUS",
    "TERMINAL_VERDICTS",
    "gate_evidence",
    "replay_problems",
]
