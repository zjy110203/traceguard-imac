"""RUN-013 WP3：compiler / healed 候选「稳定性回放 → 审核」真实浏览器闭环。

- 独立 API / 独立 Worker / 真实 TeamFlow / 真实 Chromium；
- 通过 TestCase Detail 页面启动回放，读取真实状态/进度/样本/结论/Oracle/审核条件；
- 正向：compiler 与 healed 各一条，回放 STABLE_PASS 后 UI 审核通过；
- 负向：无回放审核被拒、失败回放被拒、回放未完成被拒。

用法：
    .venv/bin/python scripts/stability_replay_acceptance_run013.py
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
    WorkerFleet,
    _child_env,
    _clear_fault,
    _free_port,
    _post_fault,
    _start_api,
    _wait_health,
)

PASSWORD = "Replay-accept-2026!"

STEPS = [
    {"step_id": "s1", "action": "navigate", "value": "/projects", "description": "打开项目列表"},
    {
        "step_id": "s2",
        "action": "click",
        "target": {"strategy": "test_id", "value": "nav-new"},
        "description": "新建",
    },
    {
        "step_id": "s3",
        "action": "fill",
        "target": {"strategy": "test_id", "value": "project-name"},
        "value": "WP3 回放项目",
        "description": "填写名称",
    },
    {
        "step_id": "s4",
        "action": "click",
        "target": {"strategy": "test_id", "value": "create-submit"},
        "description": "提交",
    },
    {
        "step_id": "s5",
        "action": "assert",
        "target": {"strategy": "test_id", "value": "banner-success"},
        "value": "banner-success",
        "description": "读取成功提示",
    },
]


def _wait_replay(api: Http, replay_id: str, timeout_s: float = 120.0) -> dict[str, Any]:
    deadline = time.time() + timeout_s
    last: dict[str, Any] = {}
    while time.time() < deadline:
        last = api.get(f"/api/v1/stability-replays/{replay_id}?refresh=true")
        if last.get("status") in {"COMPLETED", "INCOMPLETE"}:
            return last
        time.sleep(1.0)
    return last


def _create_candidate(
    lead: Http, title: str, *, compile_source: str, oracle_id: str
) -> tuple[str, int]:
    tc = lead.post(
        "/api/v1/testcases",
        {
            "title": title,
            "workspace_id": "default",
            "compile_source": compile_source,
            "steps": STEPS,
            "assertions": [
                {
                    "assertion_id": "a_wp3_banner",
                    "description": "成功提示可见",
                    "kind": "ui",
                    "required": True,
                    "expected": {"test_id": "banner-success"},
                    "oracle_id": oracle_id,
                    "oracle_version": 1,
                }
            ],
            "cleanup_policy": "RESET_DATA",
        },
        expect_status=(200, 201),
    )
    tc_id = str(tc.get("testcase_id") or "")
    version = int(tc.get("version") or 1)
    lead.post(
        f"/api/v1/testcases/{tc_id}/versions/{version}/oracle-bindings",
        {"bindings": {"a_wp3_banner": {"oracle_id": oracle_id}}, "actor": "role:qa-lead"},
    )
    return tc_id, version


def _create_oracle(lead: Http, assertion_id: str, suffix: str) -> str:
    oracle_id = f"oracle_wp3_{suffix}"
    lead.post(
        "/api/v1/oracles",
        {
            "oracle_id": oracle_id,
            "assertion_id": assertion_id,
            "expected_behavior": "创建成功后必须出现成功提示",
            "requirement_ref": "REQ-WP3-REPLAY#UI-1",
            "approved": True,
            "approved_by": "role:qa-lead",
            "approval_note": "WP3 回放验收",
        },
        expect_status=(200, 201),
    )
    return oracle_id


def main() -> int:
    out = (ROOT / "docs/assets/run013/wp3/ui").resolve()
    out.mkdir(parents=True, exist_ok=True)
    work = out / "_runtime"
    work.mkdir(parents=True, exist_ok=True)
    rep = Reporter(out)

    from playwright.sync_api import sync_playwright

    api_port = _free_port()
    teamflow_port = _free_port()
    for name in ("replay.db", "teamflow.db"):
        f = work / name
        if f.exists():
            f.unlink()
    env = _child_env(
        f"sqlite:///{work / 'replay.db'}",
        api_port,
        teamflow_port,
        f"sqlite:///{work / 'teamflow.db'}",
        inline_worker=False,
        lease_ttl_s=30.0,
    )
    env["TG_ARTIFACT_DIR"] = str(work / "artifacts")
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
    api_holder: dict[str, Any] = {"proc": _start_api(env, api_port)}
    base = f"http://127.0.0.1:{api_port}"
    teamflow = f"http://127.0.0.1:{teamflow_port}"
    fleet = WorkerFleet(env, out, 30.0)

    try:
        if not _wait_health(teamflow + "/health", 30) or not _wait_health(base + "/health", 60):
            rep.check("环境：API/TeamFlow 可启动", False, base)
            return 3
        rep.check("环境：API 独立进程 + 内嵌 Worker 关闭", True, base)
        for name, roles in (
            ("r13-replay-lead", "qa-lead"),
            ("r13-replay-admin", "platform-admin"),
        ):
            r = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "auth_bootstrap.py"),
                    "--db",
                    f"sqlite:///{work / 'replay.db'}",
                    "--username",
                    name,
                    "--password",
                    PASSWORD,
                    "--roles",
                    roles,
                    "--workspaces",
                    '["default"]' if roles == "qa-lead" else '["*"]',
                ],
                env=env,
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                timeout=60,
            )
            if r.returncode != 0:
                rep.check(f"环境：初始化 {name}", False, r.stderr[-200:])
                return 3
        lead = Http(base, "lead")
        admin = Http(base, "admin")
        lead.login("r13-replay-lead", PASSWORD)
        admin.login("r13-replay-admin", PASSWORD)
        rep.check("环境：qa-lead/platform-admin 就绪", True, "auth+CSRF")
        fleet.start("r13-replay-w1")
        fleet.start("r13-replay-w2")
        _wait_health(base + "/health", 20)

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            ctx = browser.new_context(viewport={"width": 1440, "height": 1000})
            page = ctx.new_page()

            def login_page() -> None:
                page.goto(base + "/login", wait_until="domcontentloaded")
                page.wait_for_selector("#login-username", timeout=15000)
                page.fill("#login-username", "r13-replay-lead")
                page.fill("#login-password", PASSWORD)
                page.click("button.login-submit")
                page.wait_for_selector(".workspace-picker select", timeout=20000)

            def replay_card_text() -> str:
                card = page.locator(".card").filter(has=page.locator("h2:has-text('稳定性回放')"))
                return card.first.inner_text(timeout=5000)

            def card_field(text: str, name: str) -> str:
                lines = [line.strip() for line in text.splitlines()]
                try:
                    return lines[lines.index(name) + 1]
                except (ValueError, IndexError):
                    return ""

            login_page()
            rep.check("UI：已登录并可进入用例详情", True, base)

            # ------------------------------------------------ negative: no replay
            no_replay_oracle = _create_oracle(lead, "a_wp3_no_replay", "no_replay")
            no_replay_tc, no_replay_v = _create_candidate(
                lead, "WP3 负向：无回放候选", compile_source="compiler", oracle_id=no_replay_oracle
            )
            r = lead.raw(
                "POST",
                f"/api/v1/testcases/{no_replay_tc}/versions/{no_replay_v}/review",
                {"decision": "APPROVED", "reviewer": "role:qa-lead", "note": "无回放"},
            )
            rep.check(
                "负向：无回放 → 审核被服务端拒绝（STABILITY_REPLAY_REQUIRED）",
                r[0] == 412 and "STABILITY_REPLAY_REQUIRED" in r[2],
                f"status={r[0]} body={r[2][:200]}",
                server_verified=True,
            )

            # ---------------------------------------------- positive: compiler
            compiler_oracle = _create_oracle(lead, "a_wp3_banner", "compiler")
            compiler_tc, _ = _create_candidate(
                lead,
                "WP3 compiler 候选：成功提示可见",
                compile_source="compiler",
                oracle_id=compiler_oracle,
            )
            page.goto(base + f"/testcases/{compiler_tc}", wait_until="domcontentloaded")
            page.wait_for_selector("button:has-text('启动稳定性回放')", timeout=20000)
            before = replay_card_text()
            rep.check(
                "UI：无回放时显示真实空状态",
                "尚无稳定性回放" in before,
                before[:80],
                server_verified=True,
            )
            page.click("button:has-text('启动稳定性回放')")
            deadline = time.time() + 150
            card = ""
            while time.time() < deadline:
                # 主动点击「刷新」：既验证按钮真实可用，也不依赖后台轮询时序。
                page.locator(".card").filter(has=page.locator("h2:has-text('稳定性回放')")).locator(
                    "button:has-text('刷新')"
                ).first.click()
                page.wait_for_timeout(1200)
                card = replay_card_text()
                if card_field(card, "稳定性结论") == "STABLE_PASS" and "3 / 3" in card:
                    break
                time.sleep(1)
            rep.check(
                "UI：回放完成后显示 3/3 样本 + STABLE_PASS",
                card_field(card, "稳定性结论") == "STABLE_PASS" and "3 / 3" in card,
                card[:300],
                server_verified=True,
            )
            rep.check(
                "UI：显示成功/失败数量与 Oracle 版本",
                "通过 3" in card and "oracle_wp3_compiler@1" in card,
                card[:300],
                server_verified=True,
            )
            rep.check(
                "UI：显示审核条件满足",
                "满足" in card and "未满足" not in card,
                card[:300],
                server_verified=True,
            )
            page.click("button:has-text('审核通过')")
            page.wait_for_timeout(2000)
            tc_doc = lead.get(f"/api/v1/testcases/{compiler_tc}")
            history = (
                tc_doc.get("latest", {}).get("review_history") or tc_doc.get("review_history") or []
            )
            enforced = any(h.get("stability_policy") == "ENFORCED" for h in history)
            rep.check(
                "compiler：UI 审核通过且留痕 stability_policy=ENFORCED",
                tc_doc.get("review_status") == "APPROVED" and enforced,
                f"status={tc_doc.get('review_status')} enforced={enforced}",
                server_verified=True,
            )

            # ------------------------------------------------ positive: healed
            healed_oracle = _create_oracle(lead, "a_wp3_banner", "healed")
            healed_tc, _ = _create_candidate(
                lead,
                "WP3 healed 候选：修复后成功提示可见",
                compile_source="healed",
                oracle_id=healed_oracle,
            )
            page.goto(base + f"/testcases/{healed_tc}", wait_until="domcontentloaded")
            page.wait_for_selector("button:has-text('启动稳定性回放')", timeout=20000)
            page.click("button:has-text('启动稳定性回放')")
            deadline = time.time() + 150
            card = ""
            while time.time() < deadline:
                # 主动点击「刷新」：既验证按钮真实可用，也不依赖后台轮询时序。
                page.locator(".card").filter(has=page.locator("h2:has-text('稳定性回放')")).locator(
                    "button:has-text('刷新')"
                ).first.click()
                page.wait_for_timeout(1200)
                card = replay_card_text()
                if card_field(card, "稳定性结论") == "STABLE_PASS" and "3 / 3" in card:
                    break
                time.sleep(1)
            rep.check(
                "UI：healed 候选回放 3/3 + STABLE_PASS",
                card_field(card, "稳定性结论") == "STABLE_PASS" and "3 / 3" in card,
                card[:300],
                server_verified=True,
            )
            rep.check(
                "UI：healed 候选审核条件满足",
                "满足" in card and "未满足" not in card,
                card[:300],
                server_verified=True,
            )
            page.click("button:has-text('审核通过')")
            page.wait_for_timeout(2000)
            tc_doc = lead.get(f"/api/v1/testcases/{healed_tc}")
            rep.check(
                "healed：UI 审核通过",
                tc_doc.get("review_status") == "APPROVED",
                f"status={tc_doc.get('review_status')}",
                server_verified=True,
            )

            # ------------------------------- negative: failed replay blocks review
            fail_oracle = _create_oracle(lead, "a_wp3_banner", "failed")
            fail_tc, fail_v = _create_candidate(
                lead, "WP3 负向：失败回放候选", compile_source="compiler", oracle_id=fail_oracle
            )
            _post_fault(teamflow, "TF-UI-002", {"sticky": True})
            replay = lead.post(
                f"/api/v1/testcases/{fail_tc}/versions/{fail_v}/stability-replay?repeats=3",
                {},
                expect_status=(200, 201),
            )
            failed_doc = _wait_replay(lead, str(replay.get("replay_id")))
            _clear_fault(teamflow, "TF-UI-002")
            rep.check(
                "负向：真实故障下回放非 STABLE_PASS",
                failed_doc.get("status") == "COMPLETED"
                and (failed_doc.get("classification") or {}).get("classification") != "STABLE_PASS",
                f"status={failed_doc.get('status')} "
                f"class={(failed_doc.get('classification') or {}).get('classification')}",
                server_verified=True,
            )
            r = lead.raw(
                "POST",
                f"/api/v1/testcases/{fail_tc}/versions/{fail_v}/review",
                {"decision": "APPROVED", "reviewer": "role:qa-lead", "note": "失败回放"},
            )
            rep.check(
                "负向：失败回放 → 审核被拒（STABILITY_NOT_STABLE_PASS）",
                r[0] == 412 and "STABILITY_NOT_STABLE_PASS" in r[2],
                f"status={r[0]} body={r[2][:220]}",
                server_verified=True,
            )

            # --------------------------- negative: unfinished replay blocks review
            pending_oracle = _create_oracle(lead, "a_wp3_banner", "pending")
            pending_tc, pending_v = _create_candidate(
                lead,
                "WP3 负向：未完成回放候选",
                compile_source="compiler",
                oracle_id=pending_oracle,
            )
            fleet.stop("r13-replay-w1", timeout=20)
            fleet.stop("r13-replay-w2", timeout=20)
            lead.post(
                f"/api/v1/testcases/{pending_tc}/versions/{pending_v}/stability-replay?repeats=3",
                {},
                expect_status=(200, 201),
            )
            r = lead.raw(
                "POST",
                f"/api/v1/testcases/{pending_tc}/versions/{pending_v}/review",
                {"decision": "APPROVED", "reviewer": "role:qa-lead", "note": "未完成"},
            )
            rep.check(
                "负向：回放未完成 → 审核被拒（STABILITY_REPLAY_NOT_COMPLETED）",
                r[0] == 412 and "STABILITY_REPLAY_NOT_COMPLETED" in r[2],
                f"status={r[0]} body={r[2][:220]}",
                server_verified=True,
            )
            fleet.start("r13-replay-w1")
            fleet.start("r13-replay-w2")
            browser.close()
    except Exception as exc:  # noqa: BLE001
        rep.check("WP3 验收脚本执行", False, f"{type(exc).__name__}: {exc}")
    finally:
        fleet.close()
        for proc in (api_holder.get("proc"), tf_proc):
            if not proc:
                continue
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except Exception:  # noqa: BLE001
                proc.kill()

    summary = {
        "workpackage": "RUN-013 WP3",
        "engine": "Playwright Chromium + real API + real TeamFlow + independent Workers",
        "total": len(rep.results),
        "passed": sum(1 for r in rep.results if r["status"] == "PASS"),
        "failed": len(rep.failed),
        "results": rep.results,
    }
    (out / "stability-replay-acceptance.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        f"[out] {out / 'stability-replay-acceptance.json'} passed={summary['passed']} failed={summary['failed']}"
    )
    return 1 if rep.failed else 0


if __name__ == "__main__":
    sys.exit(main())
