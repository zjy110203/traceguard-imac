"""RUN-019-R2：四角色真实权限验收（platform-admin / qa-lead / qa-engineer / viewer）。

每个角色使用**真实登录会话 + 真实 Chromium**，逐项核对：

1. 按钮可见且可用（能执行该动作的角色）；
2. 按钮可见但禁用（无权限角色，前端如实呈现而不是假装成功）；
3. 服务端独立 403（即使绕过前端直接发请求，也必须被拒绝）。

不修改权限矩阵、不放宽门禁：期望值直接来自服务端 `/api/v1/auth/permissions`。
用法：
    .venv/bin/python scripts/rbac_role_ui_acceptance_run019_r2.py
退出码：0 全部通过；1 有失败；3 环境不可达。
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from business_loop_ui_acceptance_run012 import (  # noqa: E402
    Http,
    Reporter,
    _child_env,
    _free_port,
    _start_api,
    _wait_health,
)

PASSWORD = "Rbac-Accept-2026!"
OUT = ROOT / "docs/assets/run019-r2/local/rbac"

ROLES: list[tuple[str, str, str]] = [
    ("r19r-admin", "platform-admin", '["*"]'),
    ("r19r-lead", "qa-lead", '["default"]'),
    ("r19r-eng", "qa-engineer", '["default"]'),
    ("r19r-viewer", "viewer", '["default"]'),
]

#: 前端按钮 → 需要的动作。与后端 PERMISSIONS / 前端 ROLE_PERMISSIONS 同构。
EXPECT: dict[str, dict[str, bool]] = {
    "testcase.create": {"platform-admin": True, "qa-lead": True, "qa-engineer": True, "viewer": False},
    "testcase.review": {"platform-admin": True, "qa-lead": True, "qa-engineer": False, "viewer": False},
    "run.execute": {"platform-admin": True, "qa-lead": True, "qa-engineer": True, "viewer": False},
    "repair.submit": {"platform-admin": True, "qa-lead": True, "qa-engineer": True, "viewer": False},
    "system.configure": {"platform-admin": True, "qa-lead": False, "qa-engineer": False, "viewer": False},
    "user.manage": {"platform-admin": True, "qa-lead": False, "qa-engineer": False, "viewer": False},
}

#: 页面按钮选择器 → 动作（按钮必须真实存在于 DOM，禁用态由前端权限决定）。
UI_PROBES: list[tuple[str, str, str]] = [
    ("/testcases", '[data-testid="testcase-new"]', "testcase.create"),
    ("/repairs", 'button.primary:has-text("新建修复提案")', "repair.submit"),
]


def _seed_users(db_url: str, env: dict[str, str]) -> None:
    for username, role, workspaces in ROLES:
        r = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "auth_bootstrap.py"),
                "--db",
                db_url,
                "--username",
                username,
                "--password",
                PASSWORD,
                "--roles",
                role,
                "--workspaces",
                workspaces,
            ],
            env=env,
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=60,
        )
        if r.returncode != 0:
            raise RuntimeError(f"auth_bootstrap {username} failed: {r.stderr[-300:]}")


def _button_state(page: Any, selector: str) -> tuple[bool, bool]:
    """返回 (存在, 可用)。不存在 ≠ 可用，必须区分。"""
    loc = page.locator(selector).first
    count = page.locator(selector).count()
    if count == 0:
        return False, False
    return True, bool(loc.is_enabled())


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    rep = Reporter(OUT)
    from playwright.sync_api import sync_playwright

    api_port, teamflow_port = _free_port(), _free_port()
    work = OUT / "_runtime"
    work.mkdir(parents=True, exist_ok=True)
    db_url = f"sqlite:///{work / 'rbac.db'}"
    for f in work.glob("*.db"):
        f.unlink()
    env = _child_env(
        db_url, api_port, teamflow_port, f"sqlite:///{work / 'teamflow.db'}", inline_worker=True
    )
    tf_proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "traceguard_teamflow.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(teamflow_port),
        ],
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    api_proc = _start_api(env, api_port)
    base = f"http://127.0.0.1:{api_port}"
    try:
        if not _wait_health(f"http://127.0.0.1:{teamflow_port}/health"):
            rep.check("环境：TeamFlow 可启动", False, "TeamFlow 未就绪")
            return 3
        if not _wait_health(base + "/health"):
            rep.check("环境：API 可启动", False, "API 未就绪")
            return 3
        _seed_users(db_url, env)
        rep.check("环境：四个角色已初始化", True, ", ".join(r[0] for r in ROLES))

        sessions: dict[str, Http] = {}
        for username, role, _ in ROLES:
            api = Http(base, role)
            api.login(username, PASSWORD)
            sessions[role] = api

        # 服务端权限矩阵是唯一权威：前端期望值必须与它一致。
        admin = sessions["platform-admin"]
        matrix = admin.get("/api/v1/auth/permissions")["matrix"]
        matrix_ok = all(
            all((role in matrix and action in matrix[role]) == expected
                for role, expected in per_role.items())
            for action, per_role in EXPECT.items()
        )
        rep.check(
            "服务端权限矩阵与验收期望一致（无隐藏放宽）",
            matrix_ok,
            json.dumps({k: sorted(v) for k, v in matrix.items()}, ensure_ascii=False)[:300],
            server_verified=True,
        )

        # 真实资产：seed 演示用例 + 一条 REVIEW 中的需求（用于审核按钮态）
        seeded = admin.post("/api/v1/system/seed", {})
        # 启动时已自动 seed：created=0 / skipped>0 属正常幂等命中，不是失败。
        seeded_total = int(seeded.get("created") or 0) + int(seeded.get("skipped") or 0)
        rep.check("环境：演示资产已就绪（seed 幂等）", seeded_total > 0, str(seeded)[:120])
        testcases = admin.get("/api/v1/testcases?workspace_id=default")
        tc_rows = testcases if isinstance(testcases, list) else testcases.get("items", [])
        approved = [tc for tc in tc_rows if tc.get("review_status") == "APPROVED"]
        rep.check("环境：存在 APPROVED 用例（执行按钮可判定）", len(approved) >= 1, f"{len(approved)} 条")
        tc_id = str(approved[0]["testcase_id"]) if approved else ""

        eng_api = sessions["qa-engineer"]
        intent = eng_api.post(
            "/api/v1/intents",
            {
                "title": "RBAC 验收需求（待审核）",
                "description": "用于验证 testcase.review 按钮在不同角色下的可用性",
                "owner": "qa-team",
                "workspace_id": "default",
            },
            expect_status=(200, 201),
        )
        intent_id = str(intent.get("intent_id") or "")
        if intent_id:
            eng_api.post(f"/api/v1/intents/{intent_id}/submit", {})

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)

            def login_page(username: str) -> Any:
                ctx = browser.new_context(viewport={"width": 1440, "height": 900})
                page = ctx.new_page()
                page.goto(base + "/login", wait_until="domcontentloaded")
                page.wait_for_selector("#login-username", timeout=15000)
                page.fill("#login-username", username)
                page.fill("#login-password", PASSWORD)
                page.click("button.login-submit")
                page.wait_for_selector(".workspace-picker select", timeout=20000)
                return page

            try:
                for username, role, _ in ROLES:
                    page = login_page(username)
                    try:
                        for path, selector, action in UI_PROBES:
                            page.goto(base + path, wait_until="domcontentloaded")
                            page.wait_for_timeout(900)
                            exists, enabled = _button_state(page, selector)
                            expected = EXPECT[action][role]
                            ok = exists and enabled == expected and (enabled or expected is False)
                            rep.check(
                                f"{role}：{path} 的 {action} 按钮"
                                f"{'可用' if expected else '禁用'}",
                                ok,
                                f"exists={exists} enabled={enabled} expected={expected}",
                                server_verified=True,
                            )

                        # 审核按钮：REVIEW 中的需求对 lead 可用、对 engineer/viewer 禁用。
                        page.goto(base + "/intents", wait_until="domcontentloaded")
                        page.wait_for_selector("text=RBAC 验收需求", timeout=20000)
                        # 只有 REVIEW 状态的需求才渲染「通过」；seed 需求均为 APPROVED，
                        # 因此页面上的「通过」按钮必定属于本次创建的待审核需求。
                        review_btn = page.locator('button:has-text("通过")').first
                        exists = review_btn.count() >= 1
                        enabled = bool(review_btn.is_enabled()) if exists else False
                        expected = EXPECT["testcase.review"][role]
                        rep.check(
                            f"{role}：需求审核按钮{'可用' if expected else '禁用'}",
                            exists and enabled == expected,
                            f"exists={exists} enabled={enabled} expected={expected}",
                            server_verified=True,
                        )

                        # 执行按钮：APPROVED 用例在 TestCases 列表上的可执行性。
                        page.goto(base + "/testcases", wait_until="domcontentloaded")
                        page.wait_for_timeout(900)
                        exec_btn = page.locator(
                            'tr:has-text("' + tc_id + '") button:has-text("执行")'
                        ).first if tc_id else None
                        if exec_btn is not None and exec_btn.count() >= 1:
                            enabled = bool(exec_btn.is_enabled())
                            expected = EXPECT["run.execute"][role]
                            rep.check(
                                f"{role}：APPROVED 用例执行按钮{'可用' if expected else '禁用'}",
                                enabled == expected,
                                f"enabled={enabled} expected={expected}",
                                server_verified=True,
                            )
                        rep.screenshot(page, f"rbac-{role}")
                    finally:
                        page.context.close()

                # ------------------------------ 服务端独立 403（前端隐藏不构成安全保证）
                def status_of(role: str, path: str, body: dict | None) -> int:
                    return int(sessions[role].raw("POST", path, body or {})[0])

                viewer_code = status_of("viewer", "/api/v1/testcases", {"title": "viewer 不应创建"})
                rep.check(
                    "服务端：viewer 直接创建用例被 403",
                    viewer_code == 403,
                    f"status={viewer_code}",
                    server_verified=True,
                )

                eng_code = status_of(
                    "qa-engineer",
                    f"/api/v1/testcases/{tc_id}/versions/1/review",
                    {"decision": "APPROVED", "reviewer": "role:qa-engineer"},
                )
                rep.check(
                    "服务端：qa-engineer 直接审核被 403（不能自批）",
                    eng_code == 403,
                    f"status={eng_code}",
                    server_verified=True,
                )

                seed_code = status_of("qa-engineer", "/api/v1/system/seed", {})
                rep.check(
                    "服务端：qa-engineer 直接写演示资产被 403",
                    seed_code == 403,
                    f"status={seed_code}",
                    server_verified=True,
                )

                promote_code = status_of(
                    "qa-lead",
                    "/api/v1/repairs/rep_not_exists/promote",
                    {"idempotency_key": "rbac-probe"},
                )
                rep.check(
                    "服务端：qa-lead 直接晋升被 403（repair.promote 仅平台管理员）",
                    promote_code == 403,
                    f"status={promote_code}",
                    server_verified=True,
                )
            finally:
                browser.close()
    except Exception as exc:  # noqa: BLE001
        rep.check("权限验收脚本执行", False, f"{type(exc).__name__}: {exc}")
    finally:
        for proc in (api_proc, tf_proc):
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()

    summary = {
        "workpackage": "RUN-019-R2 R2-3",
        "profile": "rbac-roles",
        "total": len(rep.results),
        "passed": sum(1 for r in rep.results if r["status"] == "PASS"),
        "failed": len(rep.failed),
        "results": rep.results,
    }
    (OUT / "rbac-roles.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"total={summary['total']} passed={summary['passed']} failed={summary['failed']}")
    return 1 if rep.failed else 0


if __name__ == "__main__":
    sys.exit(main())
