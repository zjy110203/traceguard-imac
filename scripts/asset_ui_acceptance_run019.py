#!/usr/bin/env python3
"""RUN-019 WP3：新增资产管理前端的真实浏览器业务操作验收。

复用 ui_matrix_run017.Env（真实 API + 真实 TeamFlow + 真实 Chromium），
本脚本**开启认证**：以真实 platform-admin 会话登录，逐项点击真实按钮
并通过同源 Cookie 的 API 回读验证，不把页面渲染当作业务成功。
证据：docs/assets/run019/wp3/ui-asset-loop.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for p in (
    "scripts",
    "packages/core",
    "packages/contracts",
    "packages/execution",
    "packages/evidence",
    "packages/persistence",
    "apps/api",
    "apps/teamflow",
    "apps/workers",
):
    if str(ROOT / p) not in sys.path:
        sys.path.insert(0, str(ROOT / p))

import ui_matrix_run017 as matrix  # noqa: E402

# 认证开启：管理页（/workspaces /users）才可进入；其余子进程环境保持不变。
_orig_base_env = matrix.base_env


def _authed_base_env() -> dict[str, str]:
    return {**_orig_base_env(), "TG_AUTH_ENABLED": "true"}


matrix.base_env = _authed_base_env

from ui_matrix_run017 import Env, seed_and_execute  # noqa: E402

OUT = ROOT / "docs/assets/run019/wp3"
RECORDS: list[dict[str, Any]] = []


def record(name: str, ok: bool, detail: Any) -> None:
    RECORDS.append({"step": name, "result": "PASS" if ok else "FAIL", "detail": detail})
    print(f"[{'PASS' if ok else 'FAIL'}] {name}")


def main() -> int:
    from playwright.sync_api import sync_playwright

    work = OUT / "run-env"
    env = Env(work)
    try:
        ids = seed_and_execute(env)

        # 认证开启后，先在真实平台库里创建管理员（与运行中 API 共享 SQLite 文件）。
        from traceguard_persistence import Database
        from traceguard_persistence.auth import create_user

        db = Database(f"sqlite:///{env.work / 'platform.db'}")
        try:
            create_user(
                db,
                username="ui-admin",
                password="UiAdmin-Run019!",
                roles=["platform-admin"],
            )
            # 独立普通用户：作为 UI 角色变更对象，避免触碰管理员自身。
            create_user(
                db,
                username="ui-viewer",
                password="UiViewer-Run019!",
                roles=["viewer"],
            )
        finally:
            db.dispose()

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            ctx = browser.new_context(viewport={"width": 1440, "height": 900})
            page = ctx.new_page()

            # 0) 真实登录（与业务操作共用 Cookie 会话；API 回读也走该会话）
            page.goto(env.api_base + "/login", wait_until="domcontentloaded")
            page.locator("#login-username").fill("ui-admin")
            page.locator("#login-password").fill("UiAdmin-Run019!")
            page.locator("button.login-submit").click()
            page.wait_for_url(env.api_base + "/", timeout=15000)

            def api(path: str) -> Any:
                resp = ctx.request.get(env.api_base + "/api/v1" + path)
                assert resp.ok, (path, resp.status, resp.text()[:200])
                return resp.json()

            # 1) Workspace 管理页：真实创建并回读
            page.goto(env.api_base + "/workspaces", wait_until="domcontentloaded")
            page.wait_for_timeout(900)
            page.get_by_role("button", name="新建工作区").click()
            page.locator("#new-ws-name").fill("UI 验收工作区")
            page.locator("#new-ws-desc").fill("由真实浏览器创建")
            page.get_by_role("button", name="创建", exact=True).click()
            page.wait_for_timeout(700)
            ws = api("/workspaces")
            record("工作区创建通过 UI 落库", any(w["name"] == "UI 验收工作区" for w in ws), ws)

            # 2) 用户管理：角色变更真实生效（普通用户 → 追加 qa-lead）
            users_before = api("/auth/users")
            target = next((u for u in users_before if u["username"] == "ui-viewer"), None)
            record("存在可变更角色的用户", target is not None, users_before)
            if target:
                page.goto(env.api_base + "/users", wait_until="domcontentloaded")
                page.wait_for_timeout(900)
                row = page.locator("tr", has_text=target["username"]).first
                row.get_by_role("button", name="编辑权限").click()
                page.wait_for_timeout(300)
                page.locator('input[type="checkbox"][value="qa-lead"]').check()
                page.get_by_role("button", name="保存", exact=True).click()
                page.wait_for_timeout(700)
                users_after = api("/auth/users")
                after = next((u for u in users_after if u["user_id"] == target["user_id"]), {})
                record("用户角色通过 UI 修改", "qa-lead" in after.get("roles", []), after)

            # 3) 需求新建 + 编辑：先通过 UI 创建，再编辑（种子需求均被用例引用，服务端按门禁拒绝编辑）
            page.goto(env.api_base + "/intents", wait_until="domcontentloaded")
            page.wait_for_timeout(900)
            page.get_by_role("button", name="新建需求").click()
            page.locator("#intent-title").fill("UI 验收需求")
            page.locator("#intent-desc").fill("由真实浏览器创建的需求")
            page.get_by_role("button", name="创建", exact=True).click()
            page.wait_for_timeout(700)
            intents_after_create = api("/intents")
            created = next(
                (it for it in intents_after_create if it["title"] == "UI 验收需求"), None
            )
            record("需求通过 UI 创建", created is not None, bool(created))
            if created:
                card = page.locator(".card", has_text="UI 验收需求").first
                card.get_by_role("button", name="编辑").click()
                page.wait_for_timeout(300)
                page.locator("#edit-intent-title").fill("UI 编辑后的需求标题")
                page.get_by_role("button", name="保存", exact=True).click()
                page.wait_for_timeout(700)
                intent = api(f"/intents/{created['intent_id']}")
                record(
                    "需求编辑通过 UI 落库且版本递增",
                    intent["title"] == "UI 编辑后的需求标题"
                    and int(intent["version"]) >= 2
                    and intent["status"] == "DRAFT",
                    {
                        "title": intent["title"],
                        "version": intent["version"],
                        "status": intent["status"],
                    },
                )

            # 4) Oracle 详情：审批新版本
            page.goto(env.api_base + "/oracles/oracle_tf_ui_banner", wait_until="domcontentloaded")
            page.wait_for_timeout(900)
            approve = page.get_by_role("button", name="审批并新建版本")
            if approve.count() and approve.first.is_enabled():
                approve.first.click()
                page.wait_for_timeout(700)
                versions = api("/oracles/oracle_tf_ui_banner/versions")
                record("Oracle 审批通过 UI 创建新版本", len(versions) >= 2, len(versions))
            else:
                record("Oracle 审批通过 UI 创建新版本", False, "审批按钮不可用")

            # 5) TestCase 详情：可视化新建版本 + 差异比较
            tcid = ids["testcase_id"]
            page.goto(env.api_base + f"/testcases/{tcid}", wait_until="domcontentloaded")
            page.wait_for_timeout(1000)
            page.get_by_role("button", name="新建版本").click()
            page.locator("#new-version-title").fill("UI 新建版本")
            page.get_by_role("button", name="新增步骤").click()
            # 新增步骤默认目标为空会被服务端 schema 拒绝；补全目标元素后保存
            n_steps = page.get_by_placeholder("目标元素 / locator 值").count()
            if n_steps:
                page.get_by_placeholder("目标元素 / locator 值").last.fill(
                    "[data-test='ui-banner']"
                )
            page.get_by_role("button", name="创建新版本").click()
            page.wait_for_timeout(900)
            versions = api(f"/testcases/{tcid}/versions")
            record("用例新版本通过 UI 落库", len(versions) >= 2, [v["version"] for v in versions])
            page.locator("#diff-from").select_option(str(versions[-1]["version"]))
            page.get_by_label("比较目标版本").select_option(str(versions[0]["version"]))
            page.wait_for_timeout(300)
            page.get_by_role("button", name="比较", exact=True).click(force=True)
            page.wait_for_timeout(500)
            record("版本差异比较可见", page.locator("pre").count() > 0, "diff pre rendered")

            browser.close()
    except Exception as exc:  # noqa: BLE001 - 如实保留失败
        record("执行过程", False, f"{type(exc).__name__}: {exc}")
    finally:
        env.close()

    failed = [r for r in RECORDS if r["result"] != "PASS"]
    summary = {
        "total": len(RECORDS),
        "passed": len(RECORDS) - len(failed),
        "failed": len(failed),
        "records": RECORDS,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "ui-asset-loop.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {"total": summary["total"], "passed": summary["passed"], "failed": summary["failed"]}
        )
    )
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
