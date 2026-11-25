"""RUN-011 WP1/WP6：直接读 PostgreSQL 复核闭环数据（只读，不做任何写入）。

用途：验收报告不能只引用 HTTP 响应，必须能从数据库独立复核追溯链。
本脚本只用 SELECT，不修改任何数据；不读取凭据（DSN 由命令行传入，不含口令）。

用法：
    .venv/bin/python scripts/verify_db_run011.py \
        --dsn "postgresql+psycopg://traceguard@127.0.0.1:55432/traceguard_run011_l1" \
        --workspace ws-run011-l1 --out docs/assets/run011/full-loop/loop1-db.json

RUN-011-R1 WP1 §1.5（数据库与 API 双向复核）：
    .venv/bin/python scripts/verify_db_run011.py \
        --dsn "postgresql+psycopg://traceguard@127.0.0.1:55432/traceguard_run011_r1_l1" \
        --workspace ws-run011-r1-1 \
        --lineage-json docs/assets/run011-r1/lineage/loop1.json \
        --out docs/assets/run011-r1/lineage/loop1-db.json
    （--lineage-json 提供 API 侧记录的实体 ID，本脚本在 DB 侧独立核对关系。）
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from sqlalchemy import create_engine, text


def _scalar(conn, sql: str, **params):  # type: ignore[no-untyped-def]
    return conn.execute(text(sql), params).scalar()


def _check_lineage(out: dict, c, lineage: dict) -> None:  # type: ignore[no-untyped-def]
    """§1.5：DB 侧独立复核血缘关系（与 API 侧断言双向对账）。"""

    def check(name: str, ok: bool, detail) -> None:  # type: ignore[no-untyped-def]
        out["checks"].append({"name": name, "ok": bool(ok), "detail": detail})

    ids = lineage.get("ids") or {}
    tc_id = ids.get("testcase_id") or ""
    approved_version = int(ids.get("approved_version") or 0)
    failed_run_id = ids.get("failed_run_id") or ""
    diag_id = ids.get("diagnosis_id") or ""
    pid = ids.get("repair_proposal_id") or ""
    promoted_version = int(ids.get("promoted_version") or 0)
    normal_run_id = ids.get("run_id") or ""
    reg_run_id = ids.get("regression_run_id") or ""

    run_rows = {
        r[0]: {
            "testcase_id": str(r[1] or "").split("@")[0],
            "testcase_version_id": r[1],
            "verdict": r[2],
        }
        for r in c.execute(
            text(
                "SELECT run_id, testcase_version_id, test_verdict FROM tg_run "
                "WHERE run_id = ANY(:rids)"
            ),
            {"rids": list(x for x in (normal_run_id, failed_run_id, reg_run_id) if x)},
        )
    }
    out["facts"]["lineage_runs"] = run_rows
    check(
        "DB: 正式/失败/回归 Run 均存在且同一 testcase_id",
        all(r in run_rows for r in (normal_run_id, failed_run_id) if r)
        and len({v["testcase_id"] for v in run_rows.values()}) == 1
        and (not run_rows or next(iter(run_rows.values()))["testcase_id"] == tc_id),
        run_rows,
    )
    check(
        "DB: 正式 Run 与失败 Run 使用批准版本",
        run_rows.get(normal_run_id, {}).get("testcase_version_id") == f"{tc_id}@{approved_version}"
        and run_rows.get(failed_run_id, {}).get("testcase_version_id") == f"{tc_id}@{approved_version}",
        {k: v.get("testcase_version_id") for k, v in run_rows.items()},
    )
    check(
        "DB: 回归 Run 使用精确晋升版本",
        run_rows.get(reg_run_id, {}).get("testcase_version_id") == f"{tc_id}@{promoted_version}",
        run_rows.get(reg_run_id),
    )

    # Diagnosis doc（tg_doc, kind='diagnosis'）
    diag_rows = [
        {"diagnosis_id": r[0], "run_id": (r[1] or {}).get("run_id"), "testcase_id": (r[1] or {}).get("testcase_id")}
        for r in c.execute(
            text("SELECT id, doc FROM tg_doc WHERE id = :did AND kind='diagnosis'"),
            {"did": diag_id},
        )
    ]
    out["facts"]["lineage_diagnosis"] = diag_rows
    check(
        "DB: Diagnosis.run_id 与失败 Run 一致且绑定主 TestCase",
        bool(diag_rows)
        and diag_rows[0]["run_id"] == failed_run_id
        and diag_rows[0]["testcase_id"] == tc_id,
        diag_rows,
    )

    # Repair doc（tg_doc, kind='repair'）
    rep_rows = [
        {
            "proposal_id": r[0],
            "testcase_id": (r[1] or {}).get("testcase_id"),
            "base_version": (r[1] or {}).get("base_version"),
            "diagnosis_id": (r[1] or {}).get("diagnosis_id"),
            "source_run_id": (r[1] or {}).get("source_run_id"),
            "repair_source": (r[1] or {}).get("repair_source"),
            "promoted_version": (r[1] or {}).get("promoted_version"),
        }
        for r in c.execute(
            text("SELECT id, doc FROM tg_doc WHERE id = :pid AND kind='repair'"),
            {"pid": pid},
        )
    ]
    out["facts"]["lineage_repair"] = rep_rows
    check(
        "DB: Repair 绑定主 TestCase + Diagnosis + 失败 Run + base_version",
        bool(rep_rows)
        and rep_rows[0]["testcase_id"] == tc_id
        and rep_rows[0]["diagnosis_id"] == diag_id
        and rep_rows[0]["source_run_id"] == failed_run_id
        and int(rep_rows[0]["base_version"] or 0) == approved_version,
        rep_rows,
    )
    check(
        "DB: Repair.repair_source 如实标注（human:/rule:/llm:）",
        bool(rep_rows) and str(rep_rows[0]["repair_source"] or "").startswith(("human:", "rule:", "llm:")),
        rep_rows[0]["repair_source"] if rep_rows else None,
    )

    # Promotion / rollback receipts
    receipts = [
        {
            "idempotency_key": r[0],
            "kind": r[1],
            "proposal_id": r[2],
            "testcase_id": r[3],
            "base_version": int(r[4]),
            "target_version": int(r[5]),
        }
        for r in c.execute(
            text(
                "SELECT idempotency_key, kind, proposal_id, testcase_id, base_version, target_version "
                "FROM tg_promotion_receipt WHERE proposal_id = :pid ORDER BY created_at"
            ),
            {"pid": pid},
        )
    ]
    out["facts"]["lineage_receipts"] = receipts
    promote_receipts = [r for r in receipts if r["kind"] == "PROMOTE"]
    rollback_receipts = [r for r in receipts if r["kind"] == "ROLLBACK"]
    check(
        "DB: PROMOTE 回执绑定 Proposal/TestCase 且 base=批准版本 → target=晋升版本",
        bool(promote_receipts)
        and all(
            r["proposal_id"] == pid
            and r["testcase_id"] == tc_id
            and r["base_version"] == approved_version
            and r["target_version"] == promoted_version
            for r in promote_receipts
        ),
        promote_receipts,
    )
    check(
        "DB: ROLLBACK 回执绑定 Proposal 且 base=晋升版本",
        bool(rollback_receipts)
        and all(r["proposal_id"] == pid and r["base_version"] == promoted_version for r in rollback_receipts),
        rollback_receipts,
    )

    # Diagnosis 证据引用归属失败 Run（不允许跨 Run 误引）
    ev_rows = [
        {"evidence_id": r[0], "run_id": r[1]}
        for r in c.execute(
            text("SELECT evidence_id, run_id FROM tg_evidence WHERE run_id = :rid"),
            {"rid": failed_run_id},
        )
    ]
    run_ev_ids = {e["evidence_id"] for e in ev_rows}
    diag_ev: set[str] = set()
    for row in c.execute(
        text("SELECT doc FROM tg_doc WHERE id = :did AND kind='diagnosis'"), {"did": diag_id}
    ):
        doc = row[0] or {}
        for h in doc.get("hypotheses") or []:
            for r in (h.get("supporting") or []) + (h.get("contradicting") or []):
                if r.get("id"):
                    diag_ev.add(str(r["id"]))
        for x in doc.get("evidence_refs") or []:
            diag_ev.add(str(x))
        for x in doc.get("probe_evidence_ids") or []:
            diag_ev.add(str(x))
    foreign = sorted(diag_ev - run_ev_ids)
    # 探针证据（run_id 同为失败 Run）已在 run_ev_ids 内；若引用了其它 Run 的证据 → foreign
    out["facts"]["lineage_evidence"] = {
        "run_evidence_count": len(run_ev_ids),
        "diagnosis_evidence_count": len(diag_ev),
        "foreign": foreign[:20],
    }
    check(
        "DB: 诊断证据引用均属于失败 Run（无跨 Run 误引）",
        bool(diag_ev) and not foreign,
        out["facts"]["lineage_evidence"],
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn", required=True)
    ap.add_argument("--workspace", default="")
    ap.add_argument("--lineage-json", default="", help="full_loop_run011.py 输出 JSON（API 侧实体 ID）")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    engine = create_engine(args.dsn, future=True)
    ws = args.workspace
    out: dict = {"dsn_scheme": args.dsn.split("://", 1)[0], "workspace": ws, "checks": [], "facts": {}}

    def check(name: str, ok: bool, detail) -> None:  # type: ignore[no-untyped-def]
        out["checks"].append({"name": name, "ok": bool(ok), "detail": detail})

    with engine.connect() as c:
        out["facts"]["server_version"] = _scalar(c, "SHOW server_version")
        out["facts"]["migration_versions"] = [
            r[0] for r in c.execute(text("SELECT version FROM tg_schema_migration ORDER BY version"))
        ]
        out["facts"]["migration_count"] = len(out["facts"]["migration_versions"])

        counts = {}
        for table in (
            "tg_intent",
            "tg_testcase",
            "tg_testcase_version",
            "tg_oracle",
            "tg_task",
            "tg_run",
            "tg_event",
            "tg_evidence",
            "tg_audit_log",
            "tg_doc",
        ):
            try:
                counts[table] = _scalar(c, f"SELECT count(*) FROM {table}")  # noqa: S608 - 固定表名白名单
            except Exception as exc:  # noqa: BLE001
                counts[table] = f"ERROR: {type(exc).__name__}"
        out["facts"]["row_counts"] = counts

        if ws:
            out["facts"]["workspace_counts"] = {
                "intents": _scalar(c, "SELECT count(*) FROM tg_intent WHERE workspace_id=:ws", ws=ws),
                "testcases": _scalar(
                    c, "SELECT count(*) FROM tg_testcase WHERE workspace_id=:ws", ws=ws
                ),
                "runs": _scalar(c, "SELECT count(*) FROM tg_run WHERE workspace_id=:ws", ws=ws),
            }

        verdicts = {
            str(r[0]): int(r[1])
            for r in c.execute(
                text(
                    "SELECT test_verdict, count(*) FROM tg_run "
                    "WHERE (:ws = '' OR workspace_id = :ws) GROUP BY test_verdict"
                ),
                {"ws": ws},
            )
        }
        out["facts"]["verdicts"] = verdicts

        owners = [
            {"lease_owner": r[0], "n": int(r[1])}
            for r in c.execute(
                text(
                    "SELECT lease_owner, count(*) FROM tg_run "
                    "WHERE (:ws = '' OR workspace_id = :ws) GROUP BY lease_owner ORDER BY 2 DESC"
                ),
                {"ws": ws},
            )
        ]
        out["facts"]["run_lease_owners"] = owners
        api_owners = [o for o in owners if o["lease_owner"] and "api" in str(o["lease_owner"])]
        check("所有 Run 的 lease_owner 均非 API 进程", not api_owners, api_owners)

        # 每个 Run 的证据数（不允许"有 Run 无证据"）
        orphans = [
            {"run_id": r[0], "evidence": int(r[1])}
            for r in c.execute(
                text(
                    "SELECT r.run_id, count(e.evidence_id) FROM tg_run r "
                    "LEFT JOIN tg_evidence e ON e.run_id = r.run_id "
                    "WHERE (:ws = '' OR r.workspace_id = :ws) "
                    "AND r.execution_status IN ('COMPLETED','FAILED','TIMED_OUT') "
                    "GROUP BY r.run_id HAVING count(e.evidence_id) = 0"
                ),
                {"ws": ws},
            )
        ]
        check("终态 Run 均有 Evidence（无孤儿 Run）", not orphans, orphans)

        dup_seq = int(
            _scalar(
                c,
                "SELECT count(*) FROM ("
                " SELECT producer_instance_id, sequence FROM tg_event "
                " GROUP BY producer_instance_id, sequence HAVING count(*) > 1"
                ") x",
            )
            or 0
        )
        null_run_events = int(
            _scalar(c, "SELECT count(*) FROM tg_event WHERE context_refs->>'run' IS NULL") or 0
        )
        out["facts"]["event_seq_duplicates"] = dup_seq
        out["facts"]["events_without_run_ref"] = null_run_events
        check("Event (producer_instance_id, sequence) 全局唯一", dup_seq == 0, dup_seq)
        check("Event 均可追溯到 Run", null_run_events == 0, null_run_events)

        audit = c.execute(
            text("SELECT count(*), min(seq), max(seq), count(DISTINCT seq) FROM tg_audit_log")
        ).first()
        out["facts"]["audit"] = {
            "entries": int(audit[0]),
            "min_seq": audit[1],
            "max_seq": audit[2],
            "distinct_seq": int(audit[3]),
        }
        check(
            "审计 seq 唯一且连续（1..N）",
            int(audit[0]) == int(audit[3]) and int(audit[1] or 0) == 1 and int(audit[2] or 0) == int(audit[0]),
            out["facts"]["audit"],
        )

        # 版本链：晋升版本 + 回滚恢复版本
        lineage: dict = {}
        if args.lineage_json:
            lineage = json.loads(Path(args.lineage_json).read_text(encoding="utf-8"))
        main_tc = (lineage.get("ids") or {}).get("testcase_id") or "tcase_bench_loc001"
        versions = [
            {
                "testcase_id": r[0],
                "version": int(r[1]),
                "review_status": r[2],
                "compile_source": (r[3] or {}).get("compile_source")
                if isinstance(r[3], dict)
                else None,
            }
            for r in c.execute(
                text(
                    "SELECT testcase_id, version, review_status, doc FROM tg_testcase_version "
                    "WHERE testcase_id = :tc ORDER BY version"
                ),
                {"tc": main_tc},
            )
        ]
        out["facts"]["main_testcase_versions"] = versions
        check(
            f"主线用例 {main_tc} 存在多个版本（晋升 + 回滚留痕）",
            len(versions) >= 3,
            versions,
        )

        # 晋升/回滚回执是独立表（不是 doc）：必须按表核对，避免"看起来没有回执"。
        receipt_rows = [
            {
                "idempotency_key": r[0],
                "kind": r[1],
                "proposal_id": r[2],
                "testcase_id": r[3],
                "base_version": int(r[4]),
                "target_version": int(r[5]),
                "created_at": r[6],
            }
            for r in c.execute(
                text(
                    "SELECT idempotency_key, kind, proposal_id, testcase_id, base_version, "
                    "target_version, created_at FROM tg_promotion_receipt ORDER BY created_at"
                )
            )
        ]
        out["facts"]["promotion_receipts"] = receipt_rows
        check(
            "CAS 晋升回执与回滚回执均已落库",
            any(r["kind"] == "PROMOTE" for r in receipt_rows)
            and any(r["kind"] == "ROLLBACK" for r in receipt_rows),
            receipt_rows,
        )
        check(
            "每条回执都绑定 base_version 与 target_version",
            all(r["base_version"] >= 1 and r["target_version"] > r["base_version"] for r in receipt_rows)
            if receipt_rows
            else False,
            receipt_rows,
        )
        probe_plans = _scalar(c, "SELECT count(*) FROM tg_doc WHERE kind='probe_plan'")
        out["facts"]["probe_plans"] = int(probe_plans)
        diagnoses = _scalar(c, "SELECT count(*) FROM tg_doc WHERE kind='diagnosis'")
        out["facts"]["diagnoses"] = int(diagnoses)
        out["facts"]["replay_docs"] = int(_scalar(c, "SELECT count(*) FROM tg_doc WHERE kind='stability_replay'"))

        if lineage:
            _check_lineage(out, c, lineage)

    engine.dispose()
    out["summary"] = {
        "total": len(out["checks"]),
        "passed": sum(1 for x in out["checks"] if x["ok"]),
        "failed": [x["name"] for x in out["checks"] if not x["ok"]],
    }
    print(json.dumps(out["summary"], ensure_ascii=False))
    for x in out["checks"]:
        print(("PASS" if x["ok"] else "FAIL"), x["name"], "" if x["ok"] else x["detail"])
    if args.out:
        p = Path(args.out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"[out] {p}")
    return 0 if not out["summary"]["failed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
