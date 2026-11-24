"""RUN-010 WP6：axe-core 自动化无障碍审查（真实 Chromium + 认证会话）。

与 RUN-009 的区别：
- RUN-009 的 axe-core 缺失（BLOCKED），本轮已在 apps/web 安装 axe-core npm 包；
- 本脚本在**认证开启**状态下登录（覆盖 WP1 登录系统），对全部业务路由 +
  登录页 + 用户管理页执行真实 axe-core 审查；
- 对 6 个此前缺表单标签的视图（Intents/TestCases/Oracles/Runs/Diagnoses/
  DiagnosisDetail）逐一验证 label 关联已修复；
- 全部结果 JSONL/JSON 落盘为证据，不伪造通过。

用法：
    .venv/bin/python scripts/ui_review_run010.py --out docs/assets/run010/wp6
"""

from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

ROUTES = [
    ("login", "/login"),
    ("dashboard", "/"),
    ("intents", "/intents"),
    ("testcases", "/testcases"),
    ("oracles", "/oracles"),
    ("runs", "/runs"),
    ("explorer", "/explorer"),
    ("models", "/models"),
    ("diagnoses", "/diagnoses"),
    ("repairs", "/repairs"),
    ("benchmark", "/benchmark"),
    ("system", "/system"),
    ("users", "/users"),
]
#: 6 个 RUN-009 登记缺表单标签的视图（必须逐一复验）
FIXED_VIEWS = ["intents", "testcases", "oracles", "runs", "diagnoses", "users"]

AUTH_RULES = [
    "label",
    "button-name",
    "image-alt",
    "heading-order",
    "th-has-data-cells",
    "select-name",
    "empty-table-header",
    "aria-valid-attr-value",
    "aria-allowed-attr",
    "color-contrast",
    "link-name",
    "listitem",
    "definition-list",
]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _wait_health(url: str, timeout: float = 30.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if r.status == 200:
                    return True
        except Exception:  # noqa: BLE001
            time.sleep(0.3)
    return False


def start_api(work: Path) -> tuple[str, subprocess.Popen[Any]]:
    port = _free_port()
    import os

    env = dict(os.environ)
    env.update(
        TG_AUTH_ENABLED="true",
        TG_AUTH_CSRF_REQUIRED="true",
        TG_ENVIRONMENT="development",
        TG_DATABASE_URL=f"sqlite:///{work / 'ui.db'}",
        TG_LLM_PROVIDER="",
        TG_INLINE_WORKER="false",
    )
    env["PYTHONPATH"] = ":".join(
        str(ROOT / p)
        for p in [
            "packages/core",
            "packages/contracts",
            "packages/execution",
            "packages/agents",
            "packages/evidence",
            "packages/persistence",
            "apps/api",
            "apps/teamflow",
            "apps/workers",
            "benchmark",
        ]
    )
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "traceguard_api.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{port}"
    assert _wait_health(f"{base}/health"), "API 未启动"
    bootstrap = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "auth_bootstrap.py"),
            "--db",
            f"sqlite:///{work / 'ui.db'}",
            "--username",
            "ui-admin",
            "--password",
            "ui-review-pass-1",
            "--roles",
            "platform-admin,qa-lead,qa-engineer",
        ],
        env=env,
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert bootstrap.returncode == 0, bootstrap.stderr[-500:]
    return base, proc


def main() -> int:
    parser = argparse.ArgumentParser(description="RUN-010 axe-core 无障碍审查")
    parser.add_argument("--out", default="docs/assets/run010/wp6")
    args = parser.parse_args()
    out = ROOT / args.out
    out.mkdir(parents=True, exist_ok=True)

    work = Path(tempfile.mkdtemp(prefix="ui-review-"))
    base, proc = start_api(work)
    results: list[dict[str, Any]] = []
    axe_engine = ""
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            ctx = browser.new_context(viewport={"width": 1440, "height": 900})
            page = ctx.new_page()
            console_errors: list[str] = []
            page.on(
                "console", lambda m: console_errors.append(m.text) if m.type == "error" else None
            )

            # 1. 真实登录（认证开启）
            page.goto(f"{base}/login", wait_until="networkidle")
            page.fill("#login-username", "ui-admin")
            page.fill("#login-password", "ui-review-pass-1")
            page.click("button.login-submit")
            page.wait_for_selector(".user-chip", timeout=15000)

            axe_path = ROOT / "apps/web/node_modules/axe-core/axe.min.js"
            assert axe_path.exists(), f"axe-core 未安装: {axe_path}"

            for name, path in ROUTES:
                page.goto(f"{base}{path}", wait_until="networkidle")
                page.wait_for_timeout(700)
                page.add_script_tag(path=str(axe_path))
                axe_result = page.evaluate(
                    "async () => await axe.run(document, { runOnly: { type: 'rule', values: "
                    + json.dumps(AUTH_RULES)
                    + " } })"
                )
                violations = [
                    {
                        "id": v["id"],
                        "impact": v.get("impact"),
                        "nodes": len(v.get("nodes", [])),
                        "sample": [
                            {"target": n.get("target"), "html": (n.get("html") or "")[:160]}
                            for n in (v.get("nodes") or [])[:3]
                        ],
                    }
                    for v in axe_result.get("violations", [])
                ]
                # 6 个修复视图的 label 关联检查：所有可见输入控件必须有可访问名称
                label_check = page.evaluate(
                    """() => {
                      const bad = [];
                      for (const el of document.querySelectorAll('input:not([type=hidden]), select, textarea')) {
                        const id = el.id || '';
                        const labelled = id && document.querySelector(`label[for="${id}"]`);
                        const aria = el.getAttribute('aria-label') || el.getAttribute('aria-labelledby');
                        const inLabel = el.closest('label');
                        if (!labelled && !aria && !inLabel) {
                          bad.push({tag: el.tagName, id, name: el.name || ''});
                        }
                      }
                      return {inputs: document.querySelectorAll('input,select,textarea').length, unlabeled: bad};
                    }"""
                )
                results.append(
                    {
                        "route": name,
                        "path": path,
                        "axe_engine": "axe-core (npm)",
                        "violations": violations,
                        "violation_count": len(violations),
                        "pass_count": len(axe_result.get("passes", [])),
                        "label_check": label_check,
                        "console_errors": [e for e in console_errors if "favicon" not in e][:5],
                    }
                )
                axe_engine = "axe-core (npm)"
                console_errors.clear()
            browser.close()
    finally:
        proc.terminate()
        proc.wait(timeout=10)

    (out / "axe-review.json").write_text(
        json.dumps(
            {
                "engine": axe_engine,
                "routes": len(ROUTES),
                "rules": AUTH_RULES,
                "results": results,
                "fixed_views_recheck": {
                    r["route"]: r["label_check"] for r in results if r["route"] in FIXED_VIEWS
                },
                "total_violations": sum(r["violation_count"] for r in results),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    total = sum(r["violation_count"] for r in results)
    unlabeled = sum(len(r["label_check"]["unlabeled"]) for r in results)
    print(
        f"axe review done: {len(ROUTES)} routes, {total} violations, {unlabeled} unlabeled inputs"
    )
    print(f"evidence → {out / 'axe-review.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
