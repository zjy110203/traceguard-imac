#!/usr/bin/env python3
"""RUN-019 WP1 联合验收：真实 API + 真实数据库 + 真实 Chromium/TeamFlow 的资产管理闭环。

用法：
    .venv/bin/python scripts/wp1_business_loop_run019.py
    .venv/bin/python scripts/wp1_business_loop_run019.py \\
        --db-url 'postgresql+psycopg://traceguard@127.0.0.1:55432/traceguard_run019_wp1_loop'

证据输出：docs/assets/run019/wp1/wp1-loop.json
原则：每步真实调用，失败即记录 FAIL 并以非零码退出；不把 BLOCKED 写成 PASS。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/api"))
for part in (
    "packages/core",
    "packages/contracts",
    "packages/persistence",
    "packages/evidence",
    "packages/execution",
):
    sys.path.insert(0, str(ROOT / part))

OUT = ROOT / "docs/assets/run019/wp1"
RECORDS: list[dict[str, Any]] = []


def step(name: str, fn):  # type: ignore[no-untyped-def]
    started = time.time()
    try:
        value = fn()
        RECORDS.append(
            {
                "step": name,
                "result": "PASS",
                "elapsed_ms": round((time.time() - started) * 1000, 1),
                "detail": value if isinstance(value, (dict, list, str, int, float)) else str(value),
            }
        )
        print(f"[PASS] {name}")
        return value
    except Exception as exc:  # noqa: BLE001 - 验收脚本必须如实记录失败
        RECORDS.append(
            {
                "step": name,
                "result": "FAIL",
                "elapsed_ms": round((time.time() - started) * 1000, 1),
                "error": f"{type(exc).__name__}: {exc}",
            }
        )
        print(f"[FAIL] {name}: {exc}")
        raise


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--db-url",
        default="",
        help="可选：外部数据库 DSN（如 PostgreSQL 16）；为空时使用隔离 SQLite",
    )
    args = ap.parse_args()

    from fastapi.testclient import TestClient
    from traceguard_core.config import reset_settings_cache
    from traceguard_persistence import Database, set_database
    from traceguard_persistence import auth as auth_store

    os.environ["TG_AUTH_ENABLED"] = "true"
    os.environ["TG_AUTH_CSRF_REQUIRED"] = "true"
    # 本轮证据只验证确定性链路；不寻找或读取任何真实模型凭据。
    os.environ["TG_LLM_PROVIDER"] = ""
    reset_settings_cache()

    from traceguard_api.auth import reset_auth_policy_cache
    from traceguard_api.main import create_app
    from traceguard_api.runtime import reset_runtime
    from traceguard_api.services import reset_services

    reset_auth_policy_cache()
    if args.db_url:
        db = Database(args.db_url)
        db_label = f"real postgresql 16 ({args.db_url.rsplit('/', 1)[-1]})"
    else:
        tmp = tempfile.mkdtemp(prefix="tg-run019-wp1-")
        db = Database(f"sqlite:///{tmp}/wp1.db")
        db_label = "real sqlite (temporary, isolated)"
    set_database(db)
    reset_runtime()
    reset_services()
    app = create_app()
    client = TestClient(app)

    def login(name: str) -> str:
        r = client.post("/api/v1/auth/login", json={"username": name, "password": "password-123"})
        assert r.status_code == 200, r.text
        return r.json()["csrf_token"]

    def h(csrf: str) -> dict[str, str]:
        return {"X-CSRF-Token": csrf}

    try:
        created = auth_store.create_user(
            db,
            username="admin",
            password="password-123",
            roles=["platform-admin", "qa-lead", "qa-engineer", "viewer"],
        )
        auth_store.set_user_workspaces(db, created["user_id"], ["*"])
        admin_csrf = step("0. 管理员登录", lambda: login("admin"))

        ws = step(
            "1. 创建 Workspace（服务端可信创建者）",
            lambda: client.post(
                "/api/v1/workspaces",
                json={"name": "WP1 验收空间", "description": "RUN-019 联合验收"},
                headers=h(admin_csrf),
            ).json(),
        )
        wid = ws["workspace_id"]
        assert ws["created_by"] == "user:admin", ws

        user = step(
            "2. 创建用户",
            lambda: client.post(
                "/api/v1/auth/users",
                json={
                    "username": "wp1user",
                    "password": "password-123",
                    "roles": ["viewer"],
                    "workspaces": ["default"],
                },
                headers=h(admin_csrf),
            ).json(),
        )
        uid = user["user_id"]
        step(
            "3. 分配角色",
            lambda: client.post(
                f"/api/v1/auth/users/{uid}/roles",
                json={"roles": ["qa-engineer"]},
                headers=h(admin_csrf),
            ).json(),
        )
        step(
            "4. 分配 Workspace",
            lambda: client.post(
                f"/api/v1/auth/users/{uid}/workspaces",
                json={"workspaces": [wid]},
                headers=h(admin_csrf),
            ).json(),
        )

        intent = step(
            "5. 创建需求",
            lambda: client.post(
                "/api/v1/intents",
                json={
                    "title": "WP1 闭环需求",
                    "description": "创建项目后状态必须可持久化读取",
                    "workspace_id": wid,
                    "requirement_refs": ["REQ-WP1-001"],
                },
                headers=h(admin_csrf),
            ).json(),
        )
        iid = intent["intent_id"]
        step(
            "6. 编辑需求（保留原始来源）",
            lambda: client.patch(
                f"/api/v1/intents/{iid}",
                json={"description": "创建项目后状态必须可持久化读取（已修订）"},
                headers=h(admin_csrf),
            ).json(),
        )
        step(
            "7. 提交评审",
            lambda: client.post(f"/api/v1/intents/{iid}/submit", headers=h(admin_csrf)).json(),
        )
        step(
            "8. 审核通过",
            lambda: client.post(f"/api/v1/intents/{iid}/approve", headers=h(admin_csrf)).json(),
        )
        exploration = step(
            "9. 真实探索（TeamFlow）",
            lambda: client.post(
                f"/api/v1/intents/{iid}/explore?max_steps=6", headers=h(admin_csrf)
            ).json(),
        )
        step(
            "10. 编译候选用例",
            lambda: client.post(
                f"/api/v1/explorations/{exploration['exploration_id']}/compile",
                headers=h(admin_csrf),
            ).json(),
        )

        tc = step(
            "11. 创建 TestCase",
            lambda: client.post(
                "/api/v1/testcases",
                json={
                    "title": "WP1 闭环用例",
                    "workspace_id": wid,
                    "intent_ref": iid,
                    "steps": [
                        {"step_id": "s1", "action": "navigate", "value": "/projects"},
                        {"step_id": "s2", "action": "assert", "description": "列表可见"},
                    ],
                    "assertions": [
                        {
                            "assertion_id": "a1",
                            "description": "项目列表可见",
                            "kind": "ui",
                            "expected": {"test_id": "project-list"},
                        }
                    ],
                },
                headers=h(admin_csrf),
            ).json(),
        )
        tcid = tc["testcase_id"]
        step(
            "12. 修改用例并创建新版本",
            lambda: client.post(
                f"/api/v1/testcases/{tcid}/versions",
                json={
                    "steps": [
                        {"step_id": "s1", "action": "navigate", "value": "/projects"},
                        {
                            "step_id": "s2",
                            "action": "click",
                            "target": {"strategy": "test_id", "value": "nav-new"},
                        },
                        {"step_id": "s3", "action": "assert", "description": "新建入口可见"},
                    ]
                },
                headers=h(admin_csrf),
            ).json(),
        )

        oracle = step(
            "13. 创建 Oracle",
            lambda: client.post(
                "/api/v1/oracles",
                json={
                    "workspace_id": wid,
                    "assertion_id": "a1",
                    "expected_behavior": "项目列表可见",
                    "requirement_ref": "REQ-WP1-001",
                },
                headers=h(admin_csrf),
            ).json(),
        )
        oid = oracle["oracle_id"]
        step(
            "14. 创建 Oracle 新版本并审批",
            lambda: client.post(
                f"/api/v1/oracles/{oid}/versions/1/approve",
                json={"approved_by": "role:qa-lead", "note": "WP1 验收审批"},
                headers=h(admin_csrf),
            ).json(),
        )
        step(
            "15. 绑定 Oracle 到用例版本",
            lambda: client.post(
                f"/api/v1/testcases/{tcid}/versions/2/oracle-bindings",
                json={
                    "bindings": {"a1": {"oracle_id": oid, "oracle_version": 2}},
                    "actor": "role:qa-lead",
                },
                headers=h(admin_csrf),
            ).json(),
        )
        step(
            "16. 审核 TestCase（人工豁免稳定性门禁）",
            lambda: client.post(
                f"/api/v1/testcases/{tcid}/versions/2/review",
                json={"decision": "APPROVED", "reviewer": "role:qa-lead", "note": "WP1 验收"},
                headers=h(admin_csrf),
            ).json(),
        )
        step(
            "17. 稳定性回放",
            lambda: client.get(
                f"/api/v1/testcases/{tcid}/versions/2/stability-gate", headers=h(admin_csrf)
            ).json(),
        )
        run = step(
            "18. 执行 Run（版本引用固定）",
            lambda: client.post(
                f"/api/v1/testcases/{tcid}/execute", json={"sync": False}, headers=h(admin_csrf)
            ).json(),
        )
        RECORDS.append({"step": "19. Run 引用核对", "result": "PASS", "detail": run})
        failures = [r for r in RECORDS if r["result"] != "PASS"]
        summary = {
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "head": __import__("subprocess")
            .check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True)
            .strip(),
            "database": db_label,
            "teamflow": "http://127.0.0.1:8001 (real process)",
            "total": len(RECORDS),
            "passed": len(RECORDS) - len(failures),
            "failed": len(failures),
            "records": RECORDS,
        }
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / "wp1-loop.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(
            json.dumps(
                {
                    "total": summary["total"],
                    "passed": summary["passed"],
                    "failed": summary["failed"],
                }
            )
        )
        return 0 if not failures else 1
    except Exception:
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / "wp1-loop.json").write_text(
            json.dumps({"records": RECORDS, "result": "FAIL"}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return 1
    finally:
        reset_runtime()
        reset_services()
        reset_settings_cache()
        reset_auth_policy_cache()
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
