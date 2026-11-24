"""RUN-011 WP1/WP6：直接读 PostgreSQL 复核闭环数据（只读，不做任何写入）。

用途：验收报告不能只引用 HTTP 响应，必须能从数据库独立复核追溯链。
本脚本只用 SELECT，不修改任何数据；不读取凭据（DSN 由命令行传入，不含口令）。

用法：
    .venv/bin/python scripts/verify_db_run011.py \
        --dsn "postgresql+psycopg://traceguard@127.0.0.1:55432/traceguard_run011_l1" \
        --workspace ws-run011-l1 --out docs/assets/run011/full-loop/loop1-db.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from sqlalchemy import create_engine, text


def _scalar(conn, sql: str, **params):  # type: ignore[no-untyped-def]
    return conn.execute(text(sql), params).scalar()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn", required=True)
    ap.add_argument("--workspace", default="")
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
                    "WHERE testcase_id = 'tcase_bench_loc001' ORDER BY version"
                )
            )
        ]
        out["facts"]["loc001_versions"] = versions
        check("定位器用例存在多个版本（晋升 + 回滚留痕）", len(versions) >= 3, versions)

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
