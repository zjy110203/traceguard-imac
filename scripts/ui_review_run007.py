"""RUN-007 WP3：前端 14 个核心视图的真实浏览器审查（4 断点 + Light/Dark + 四态）。

与旧版 ui_acceptance.py 的差别：
- 覆盖 14 个核心视图（含用例详情 / 诊断详情 / 证据图 / 探针等需要真实 ID 的页面）；
- 4 个断点：1440 / 1024 / 768 / 390，逐页检测**横向溢出**（scrollWidth > viewport）；
- Light / Dark 两套主题真实截图（emulate prefers-color-scheme）；
- Loading / Empty / Error / Success 四态真实截图（Loading 用真实 API 延迟模拟，
  Empty 用真实空结果筛选，Error 用不存在的 Run，Success 用真实数据页）；
- 每页记录 console 错误、关键内容命中、截图路径。

产出：
    docs/assets/run007/ui/*.png
    docs/assets/run007/ui/ui-review.json

用法：
    .venv/bin/python scripts/ui_review_run007.py --base-url http://127.0.0.1:8020
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent

BREAKPOINTS = [
    ("1440", 1440, 900),
    ("1024", 1024, 768),
    ("768", 768, 1024),
    ("390", 390, 844),
]

IGNORABLE = ("favicon", "Download the Vue Devtools", "net::ERR_ABORTED", "EventSource")


def _api(base: str, path: str) -> Any:
    try:
        with urllib.request.urlopen(base.rstrip("/") + "/api/v1" + path, timeout=20) as resp:
            return json.loads(resp.read().decode() or "null")
    except Exception:  # noqa: BLE001
        return None


def _discover(base: str) -> dict[str, str]:
    runs = _api(base, "/runs?limit=50") or []
    diags = _api(base, "/diagnoses?limit=10") or []
    cases = _api(base, "/testcases?limit=50") or []
    failed = [r for r in runs if r.get("test_verdict") == "FAILED"] or runs
    return {
        "run_id": (runs[0]["run_id"] if runs else ""),
        "failed_run_id": (failed[0]["run_id"] if failed else ""),
        "diagnosis_id": (diags[0]["diagnosis_id"] if diags else ""),
        "testcase_id": (cases[0]["testcase_id"] if cases else ""),
    }


def _views(ids: dict[str, str]) -> list[dict[str, Any]]:
    """14 个核心视图（探针与证据图作为 Run/诊断详情的真实子视图纳入审查）。"""

    def run_detail(path: str, tab: str = "") -> dict[str, Any]:
        return {"path": path, "click": tab}

    return [
        {"key": "01-dashboard", "path": "/", "expect": ["质量指挥中心"], "group": "Dashboard"},
        {"key": "02-intents", "path": "/intents", "expect": ["测试需求"], "group": "Intents"},
        {"key": "03-testcases", "path": "/testcases", "expect": ["测试资产"], "group": "TestCases"},
        {
            "key": "04-testcase-detail",
            "path": f"/testcases/{ids['testcase_id']}",
            "expect": ["用例"],
            "group": "TestCases",
            "skip_if": not ids["testcase_id"],
        },
        {"key": "05-oracles", "path": "/oracles", "expect": ["Oracle"], "group": "Oracles"},
        {"key": "06-runs", "path": "/runs", "expect": ["执行中心"], "group": "Runs"},
        {
            "key": "07-run-detail",
            "path": f"/runs/{ids['run_id']}",
            "expect": ["执行轨迹"],
            "group": "RunDetail",
            "skip_if": not ids["run_id"],
        },
        {
            "key": "08-run-evidence-graph",
            **run_detail(f"/runs/{ids['run_id']}", "证据图"),
            "expect": ["证据"],
            "group": "EvidenceGraph",
            "skip_if": not ids["run_id"],
        },
        {"key": "09-explorer", "path": "/explorer", "expect": ["Agent"], "group": "Explorer"},
        {"key": "10-models", "path": "/models", "expect": ["模型"], "group": "Models"},
        {"key": "11-diagnoses", "path": "/diagnoses", "expect": ["诊断"], "group": "Diagnoses"},
        {
            "key": "12-diagnosis-detail",
            "path": f"/diagnoses/{ids['diagnosis_id']}",
            "expect": ["假设"],
            "group": "Diagnosis",
            "skip_if": not ids["diagnosis_id"],
        },
        {"key": "13-repairs", "path": "/repairs", "expect": ["修复"], "group": "Repairs"},
        {
            "key": "14-benchmark",
            "path": "/benchmark",
            "expect": ["Benchmark"],
            "group": "Benchmark",
        },
        {"key": "15-system", "path": "/system", "expect": ["系统"], "group": "System"},
    ]


def _stats(page: Any) -> dict[str, Any]:
    return page.evaluate(
        """() => {
          const de = document.scrollingElement || document.documentElement;
          return {
            scrollWidth: de.scrollWidth,
            clientWidth: de.clientWidth,
            scrollHeight: de.scrollHeight,
            innerWidth: window.innerWidth,
            innerHeight: window.innerHeight,
          };
        }"""
    )


def _theme_is_dark(page: Any) -> bool:
    """暗色模式必须真的生效：body 背景亮度低、文字亮度高（不是只切换了 class）。

    只看 `prefers-color-scheme` 不算——主题必须落到真实渲染颜色上。
    """
    try:
        info = page.evaluate(
            """() => {
              const cs = getComputedStyle(document.body);
              const app = document.querySelector('.app');
              const acs = app ? getComputedStyle(app) : cs;
              return {bg: cs.backgroundColor, appBg: acs.backgroundColor, fg: cs.color};
            }"""
        )
    except Exception:  # noqa: BLE001
        return False

    def _lum(value: str) -> float | None:
        raw = str(value or "").strip()
        if not raw.startswith("rgb"):
            return None
        nums = raw[raw.find("(") + 1 : raw.find(")")].split(",")
        try:
            r, g, b = (float(x.strip()) for x in nums[:3])
        except ValueError:
            return None
        return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255.0

    bg = _lum(info.get("bg"))
    if bg is None:
        bg = _lum(info.get("appBg"))
    fg = _lum(info.get("fg"))
    if bg is None:
        return False
    return bg < 0.35 and (fg is None or fg > bg)


def run(base: str, out: Path) -> dict[str, Any]:
    from playwright.sync_api import sync_playwright

    out.mkdir(parents=True, exist_ok=True)
    ids = _discover(base)
    views = [v for v in _views(ids) if not v.get("skip_if")]
    results: list[dict[str, Any]] = []
    shots = 0

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        console: list[str] = []

        def new_page(width: int, height: int, scheme: str = "light") -> Any:
            ctx = browser.new_context(
                viewport={"width": width, "height": height},
                color_scheme=scheme,
                is_mobile=width <= 480,
                has_touch=width <= 480,
            )
            # 前端主题由 localStorage['tg-theme'] 决定（不是 prefers-color-scheme）。
            # 只设 color_scheme 会得到"浅色页面但被标成 dark"的假证据，必须先写入偏好。
            ctx.add_init_script(f"try{{localStorage.setItem('tg-theme', '{scheme}');}}catch(e){{}}")
            pg = ctx.new_page()
            pg.on(
                "console",
                lambda m: (
                    console.append(m.text)
                    if m.type == "error" and not any(k in m.text for k in IGNORABLE)
                    else None
                ),
            )
            return pg

        # ------------------------------------------------ 4 断点 × Light + Dark 首屏 1440
        for name, width, height in BREAKPOINTS:
            page = new_page(width, height)
            for view in views:
                console.clear()
                url = base + view["path"]
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=30000)
                    page.wait_for_selector(".app .sidebar", timeout=15000)
                    if view.get("click"):
                        try:
                            page.get_by_role("button", name=view["click"]).first.click(timeout=5000)
                        except Exception:  # noqa: BLE001 - tab 不存在时仍记录页面
                            console.append(f"tab-not-found:{view['click']}")
                    page.wait_for_timeout(900)
                    body = page.inner_text("body")
                    st = _stats(page)
                    overflow = st["scrollWidth"] - st["clientWidth"]
                    hit = all(x in body for x in view["expect"])
                    path = out / f"{view['key']}-{name}.png"
                    page.screenshot(path=str(path), full_page=True)
                    shots += 1
                    results.append(
                        {
                            "view": view["key"],
                            "group": view["group"],
                            "breakpoint": name,
                            "theme": "light",
                            "path": view["path"],
                            "content_ok": hit,
                            "overflow_px": max(0, overflow),
                            "console_errors": list(console[:5]),
                            "screenshot": str(path.relative_to(ROOT)),
                        }
                    )
                except Exception as exc:  # noqa: BLE001
                    results.append(
                        {
                            "view": view["key"],
                            "breakpoint": name,
                            "theme": "light",
                            "path": view["path"],
                            "error": str(exc)[:200],
                        }
                    )
            page.context.close()

        # --------------------------------------------------------- Dark @1440
        dark = new_page(1440, 900, scheme="dark")
        for view in views:
            console.clear()
            try:
                dark.goto(base + view["path"], wait_until="domcontentloaded", timeout=30000)
                dark.wait_for_selector(".app .sidebar", timeout=15000)
                if view.get("click"):
                    try:
                        dark.get_by_role("button", name=view["click"]).first.click(timeout=5000)
                    except Exception:  # noqa: BLE001
                        console.append(f"tab-not-found:{view['click']}")
                dark.wait_for_timeout(800)
                body = dark.inner_text("body")
                st = _stats(dark)
                overflow = st["scrollWidth"] - st["clientWidth"]
                hit = all(x in body for x in view["expect"])
                theme_ok = _theme_is_dark(dark)
                path = out / f"{view['key']}-dark.png"
                dark.screenshot(path=str(path), full_page=True)
                shots += 1
                results.append(
                    {
                        "view": view["key"],
                        "group": view["group"],
                        "breakpoint": "1440",
                        "theme": "dark",
                        "path": view["path"],
                        "content_ok": hit,
                        "overflow_px": max(0, overflow),
                        "dark_applied": theme_ok,
                        "console_errors": list(console[:5]),
                        "screenshot": str(path.relative_to(ROOT)),
                    }
                )
            except Exception as exc:  # noqa: BLE001
                results.append(
                    {
                        "view": view["key"],
                        "group": view["group"],
                        "breakpoint": "1440",
                        "theme": "dark",
                        "path": view["path"],
                        "content_ok": False,
                        "error": str(exc)[:200],
                    }
                )
        dark.context.close()

        # ------------------------------------------------------------- 四态
        states: list[dict[str, Any]] = []
        # Loading：真实拦截 API 请求使其延迟 4s，捕捉骨架/加载态
        page = new_page(1440, 900)
        try:

            def _slow(route: Any) -> None:
                time.sleep(4)
                route.continue_()

            page.route("**/api/v1/**", _slow)
            page.goto(base + "/runs", wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(1200)
            path = out / "state-loading-runs.png"
            page.screenshot(path=str(path), full_page=True)
            shots += 1
            states.append(
                {
                    "state": "loading",
                    "view": "runs",
                    "screenshot": str(path.relative_to(ROOT)),
                    "detail": "API 延迟 4s 时截取（真实加载态）",
                }
            )
        except Exception as exc:  # noqa: BLE001
            states.append({"state": "loading", "error": str(exc)[:200]})
        page.context.close()

        # Empty：真实空结果（筛选一个不存在的结论）
        page = new_page(1440, 900)
        try:
            page.goto(base + "/runs?verdict=INCONCLUSIVE", wait_until="domcontentloaded")
            page.wait_for_selector(".app .sidebar", timeout=15000)
            page.wait_for_timeout(1200)
            body = page.inner_text("body")
            path = out / "state-empty-runs.png"
            page.screenshot(path=str(path), full_page=True)
            shots += 1
            states.append(
                {
                    "state": "empty",
                    "view": "runs",
                    "screenshot": str(path.relative_to(ROOT)),
                    "empty_hint_visible": ("空" in body or "暂无" in body or "没有" in body),
                }
            )
        except Exception as exc:  # noqa: BLE001
            states.append({"state": "empty", "error": str(exc)[:200]})
        page.context.close()

        # Error：不存在的 Run
        page = new_page(1440, 900)
        try:
            page.goto(base + "/runs/run_ui_review_not_exists", wait_until="domcontentloaded")
            page.wait_for_selector(".app .sidebar", timeout=15000)
            page.wait_for_timeout(1500)
            body = page.inner_text("body")
            path = out / "state-error-missing-run.png"
            page.screenshot(path=str(path), full_page=True)
            shots += 1
            states.append(
                {
                    "state": "error",
                    "view": "run-detail",
                    "screenshot": str(path.relative_to(ROOT)),
                    "error_hint_visible": any(
                        k in body.lower() for k in ("错误", "失败", "not found", "重试")
                    ),
                }
            )
        except Exception as exc:  # noqa: BLE001
            states.append({"state": "error", "error": str(exc)[:200]})
        page.context.close()

        # Success：真实数据页（Dashboard）
        page = new_page(1440, 900)
        try:
            page.goto(base + "/", wait_until="domcontentloaded")
            page.wait_for_selector(".app .sidebar", timeout=15000)
            page.wait_for_timeout(1200)
            path = out / "state-success-dashboard.png"
            page.screenshot(path=str(path), full_page=True)
            shots += 1
            states.append(
                {
                    "state": "success",
                    "view": "dashboard",
                    "screenshot": str(path.relative_to(ROOT)),
                }
            )
        except Exception as exc:  # noqa: BLE001
            states.append({"state": "success", "error": str(exc)[:200]})
        page.context.close()

        browser.close()

    overflow_fail = [
        r for r in results if r.get("overflow_px") is not None and r["overflow_px"] > 2
    ]
    content_fail = [r for r in results if r.get("content_ok") is False]
    error_fail = [r for r in results if r.get("error")]
    console_fail = [r for r in results if r.get("console_errors")]
    theme_fail = [r for r in results if r.get("dark_applied") is False]

    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "base_url": base,
        "ids": ids,
        "views_covered": sorted({v["key"] for v in views}),
        "groups_covered": sorted({str(v["group"]) for v in views}),
        "breakpoints": [b[0] for b in BREAKPOINTS],
        "themes": ["light", "dark"],
        "screenshots": shots,
        "results": results,
        "states": states,
        "summary": {
            "checks": len(results),
            "content_fail": len(content_fail),
            "overflow_fail": len(overflow_fail),
            "console_fail": len(console_fail),
            "page_errors": len(error_fail),
            "dark_theme_fail": len(theme_fail),
            "light_checks": len([r for r in results if r.get("theme") == "light"]),
            "dark_checks": len([r for r in results if r.get("theme") == "dark"]),
        },
        "passed": not (content_fail or overflow_fail or error_fail or console_fail or theme_fail),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://127.0.0.1:8020")
    ap.add_argument("--out", default="docs/assets/run007/ui")
    args = ap.parse_args()
    out = ROOT / args.out
    report = run(args.base_url, out)
    (out / "ui-review.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report["summary"], ensure_ascii=False))
    for r in report["results"]:
        if r.get("overflow_px", 0) > 2:
            print(f"OVERFLOW {r['view']} @{r['breakpoint']}: +{r['overflow_px']}px")
        if r.get("content_ok") is False:
            print(f"CONTENT  {r['view']} @{r['breakpoint']}: 关键内容缺失")
        if r.get("console_errors"):
            print(f"CONSOLE  {r['view']} @{r['breakpoint']}: {r['console_errors'][:2]}")
    print(f"screenshots={report['screenshots']} passed={report['passed']}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
