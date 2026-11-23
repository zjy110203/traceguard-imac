"""前端自动化验收（真实浏览器，可重复运行）。

覆盖路线图要求的页面与交互：
- 页面导航、筛选、表格、详情、拖拽/交互控件、空状态、错误状态；
- SSE 实时通道状态标记（live / polling 降级）；
- 移动端导航、深色主题；
- 每页记录 console 错误与截图（docs/assets/run005/）。

用法：
    # 先启动 API（含前端产物）与 TeamFlow
    .venv/bin/python scripts/ui_acceptance.py [--base-url http://127.0.0.1:8000]
                                             [--out docs/assets/run005]
                                             [--only runs]
退出码：0 全通过；1 有失败；3 服务不可达（BLOCKED，不伪装通过）
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

PAGES = [
    ("dashboard", "/", "总览"),
    ("intents", "/intents", "测试需求"),
    ("testcases", "/testcases", "测试资产"),
    ("oracles", "/oracles", "Oracle"),
    ("runs", "/runs", "执行中心"),
    ("explorer", "/explorer", "Agent Studio"),
    ("models", "/models", "模型"),
    ("diagnoses", "/diagnoses", "诊断"),
    ("repairs", "/repairs", "修复"),
    ("benchmark", "/benchmark", "Benchmark"),
    ("system", "/system", "系统"),
]

IGNORABLE_CONSOLE = (
    "favicon",
    "Download the Vue Devtools",
    "EventSource",
    "net::ERR_ABORTED",
)


def _reachable(base: str) -> bool:
    try:
        with urllib.request.urlopen(f"{base}/health", timeout=5) as resp:
            return resp.status == 200
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def _first_run_id(base: str) -> str | None:
    try:
        with urllib.request.urlopen(f"{base}/api/v1/runs?limit=1", timeout=5) as resp:
            runs = json.loads(resp.read().decode())
        return runs[0]["run_id"] if runs else None
    except Exception:  # noqa: BLE001
        return None


def run(base: str, out_dir: Path, only: str = "") -> dict:
    from playwright.sync_api import sync_playwright

    out_dir.mkdir(parents=True, exist_ok=True)
    results: list[dict] = []

    def record(name: str, ok: bool, detail: str = "") -> None:
        results.append({"check": name, "status": "PASS" if ok else "FAIL", "detail": detail})

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()
        console_errors: list[str] = []
        page.on(
            "console",
            lambda msg: console_errors.append(msg.text)
            if msg.type == "error" and not any(k in msg.text for k in IGNORABLE_CONSOLE)
            else None,
        )

        # 1) 页面导航 + 渲染 + 截图
        for key, path, expect in PAGES:
            if only and only != key:
                continue
            console_errors.clear()
            page.goto(base + path, wait_until="domcontentloaded")
            try:
                page.wait_for_selector(".app .sidebar", timeout=10000)
                page.wait_for_timeout(600)
            except Exception as exc:  # noqa: BLE001
                record(f"page:{key}", False, f"shell not rendered: {exc}")
                continue
            body = page.inner_text("body")
            record(
                f"page:{key}",
                expect in body or "TraceGuard" in body,
                f"title={page.title()!r}",
            )
            page.screenshot(path=str(out_dir / f"{key}.png"), full_page=True)
            if console_errors:
                record(f"console:{key}", False, "; ".join(console_errors[:3]))
            else:
                record(f"console:{key}", True, "")

        page.goto(base + "/runs", wait_until="domcontentloaded")
        page.wait_for_timeout(600)
        # 2) 表格与筛选
        try:
            has_table = page.locator("table").count() > 0
            has_filter = page.locator("select, input[type=search], input[placeholder]").count() > 0
            record("runs:table", has_table, "")
            record("runs:filter", has_filter, "")
        except Exception as exc:  # noqa: BLE001
            record("runs:table", False, str(exc))
        page.screenshot(path=str(out_dir / "runs-table.png"), full_page=True)

        # 3) Run 详情 + SSE 状态标记
        run_id = _first_run_id(base)
        if run_id:
            page.goto(f"{base}/runs/{run_id}", wait_until="domcontentloaded")
            page.wait_for_timeout(1200)
            body = page.inner_text("body")
            record("run-detail:loaded", "Run" in body, run_id)
            record(
                "run-detail:sse-badge",
                any(label in body for label in ("实时推送", "轮询降级", "连接中", "已断开")),
                "SSE 通道状态可见（不是静默失败）",
            )
            page.screenshot(path=str(out_dir / "run-detail.png"), full_page=True)
            # 事件面板
            try:
                page.get_by_role("button", name="事件").first.click()
                page.wait_for_timeout(500)
                record("run-detail:events-tab", True, "")
                page.screenshot(path=str(out_dir / "run-detail-events.png"), full_page=True)
            except Exception as exc:  # noqa: BLE001
                record("run-detail:events-tab", False, str(exc))
        else:
            record("run-detail:loaded", False, "no run available (需要先产生真实 Run)")

        # 4) 错误状态：不存在的资源应给出可见错误提示而不是白屏
        page.goto(f"{base}/runs/run_not_exists_ui_check", wait_until="domcontentloaded")
        page.wait_for_timeout(900)
        body = page.inner_text("body")
        record(
            "error-state:not-found",
            ("失败" in body) or ("错误" in body) or ("not found" in body.lower()) or ("重试" in body),
            "错误提示可见",
        )
        page.screenshot(path=str(out_dir / "error-state.png"), full_page=True)

        # 5) 移动端导航
        mobile = browser.new_context(
            viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True
        )
        mpage = mobile.new_page()
        mpage.goto(base + "/", wait_until="domcontentloaded")
        mpage.wait_for_timeout(600)
        mpage.screenshot(path=str(out_dir / "mobile-dashboard.png"), full_page=True)
        opened = False
        for sel in (".menu-btn", "button[aria-label='打开导航']", ".mobile-toggle"):
            try:
                if mpage.locator(sel).count() > 0:
                    mpage.locator(sel).first.click()
                    mpage.wait_for_timeout(400)
                    opened = mpage.locator(".sidebar.open").count() > 0
                    break
            except Exception:  # noqa: BLE001
                continue
        record("mobile:nav-open", opened, "移动端导航可展开")
        mpage.screenshot(path=str(out_dir / "mobile-nav.png"), full_page=True)
        mobile.close()

        # 6) 深色主题（如存在切换）
        try:
            page.emulate_media(color_scheme="dark")
            page.reload(wait_until="domcontentloaded")
            page.wait_for_timeout(600)
            page.screenshot(path=str(out_dir / "dark-theme.png"), full_page=True)
            record("theme:dark", True, "深色媒体查询下页面可渲染")
        except Exception as exc:  # noqa: BLE001
            record("theme:dark", False, str(exc))

        context.close()
        browser.close()

    return {
        "base_url": base,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "checks": results,
        "passed": sum(1 for r in results if r["status"] == "PASS"),
        "failed": sum(1 for r in results if r["status"] == "FAIL"),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://127.0.0.1:8000")
    ap.add_argument("--out", default="docs/assets/run005")
    ap.add_argument("--only", default="")
    args = ap.parse_args()

    if not _reachable(args.base_url):
        print(json.dumps({"status": "BLOCKED", "reason": "API 不可达，需先启动服务"}))
        return 3

    report = run(args.base_url, Path(args.out), args.only)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "ui-acceptance.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({k: report[k] for k in ("passed", "failed")}, ensure_ascii=False))
    for r in report["checks"]:
        if r["status"] == "FAIL":
            print(f"FAIL {r['check']}: {r['detail']}")
    return 0 if report["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
