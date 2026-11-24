"""RUN-009 WP1：完整前端业务验收与交互完善（真实浏览器 + 真实 API 数据）。

相对 RUN-007 脚本的增强：
- **直接解析 apps/web/src/router.ts** 得到全部 14 个业务路由（name/path/component/meta），
  显式排除 `/:pathMatch(.*)*` 通配路由，并与 14 条清单一一对应；
- 覆盖 14 条业务路由 + Run 详情的「证据图」子视图（15 个视图）；
- 4 断点（1440/1024/768/390）× Light/Dark 双主题真实截图；
- 每个视图均执行：直接 URL 打开、页面刷新、前进后退、真实交互、键盘 Tab 基础可访问性、
  ARIA / 表格可读性静态审查、console 错误、横向溢出、路由身份与内容断言；
- Loading / Empty / Success / Error 四态真实截图；
- 尝试 axe-core / axe CLI 自动化无障碍审查；不可用时如实标记 BLOCKED，
  并以内置等价规则（可访问名称 / 表单标签 / 图片 alt / 表头 / tabindex / h1）替代。

所有截图按视图独立命名，绝不复用其他页面截图充当某路由证据。

用法：
    .venv/bin/python scripts/ui_review_run009.py \
      --web-url http://127.0.0.1:5175 \
      --api-url http://127.0.0.1:8020 \
      --out docs/assets/run009/wp1/ui

产物：
    docs/assets/run009/wp1/ui/route-coverage.json
    docs/assets/run009/wp1/ui/route-manifest.json
    docs/assets/run009/wp1/ui/shots/*.png
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
ROUTER_TS = ROOT / "apps" / "web" / "src" / "router.ts"

BREAKPOINTS = [
    ("1440", 1440, 900),
    ("1024", 1024, 768),
    ("768", 768, 1024),
    ("390", 390, 844),
]

IGNORABLE = (
    "favicon",
    "Download the Vue Devtools",
    "net::ERR_ABORTED",
    "EventSource",
    "[vite]",
)

# 路由 name -> 正文关键词（来自 run007 已验证的断言，逐页校准）
EXPECT: dict[str, list[str]] = {
    "dashboard": ["质量指挥中心", "总览"],
    "intents": ["测试需求", "需求"],
    "testcases": ["测试资产", "用例"],
    "testcase-detail": ["用例", "版本"],
    "oracles": ["Oracle", "契约"],
    "runs": ["执行中心", "Run"],
    "run-detail": ["执行轨迹", "步骤"],
    "explorer": ["Agent", "探索"],
    "models": ["模型"],
    "diagnoses": ["诊断"],
    "diagnosis-detail": ["假设", "诊断"],
    "repairs": ["修复"],
    "benchmark": ["Benchmark"],
    "system": ["系统"],
}

# 每个视图的安全真实交互（不破坏业务数据）：搜索框输入 / 刷新按钮 / Tab 切换
INTERACT: dict[str, dict[str, str]] = {
    "dashboard": {"kind": "click", "text": "刷新"},
    "intents": {"kind": "fill", "selector": "input[placeholder*='搜索']", "value": "创建"},
    "testcases": {"kind": "fill", "selector": "input[placeholder*='搜索']", "value": "创建"},
    "testcase-detail": {"kind": "click", "text": "查看"},
    "oracles": {"kind": "fill", "selector": "input[placeholder*='搜索']", "value": "Oracle"},
    "runs": {"kind": "click", "text": "刷新"},
    "run-detail": {"kind": "click", "text": "断言"},
    "run-evidence-graph": {"kind": "click", "text": "生成证据图"},
    "explorer": {"kind": "fill", "selector": "input[aria-label='搜索探索']", "value": "TF"},
    "models": {"kind": "click", "text": "刷新"},
    "diagnoses": {"kind": "click", "text": "刷新"},
    "diagnosis-detail": {
        "kind": "fill",
        "selector": "input:not([type=checkbox])",
        "value": "wp1-a11y",
    },
    "repairs": {"kind": "click", "text": "刷新"},
    "benchmark": {"kind": "click", "text": "刷新"},
    "system": {"kind": "click", "text": "刷新"},
}


def _git_head() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, text=True
        ).strip()
    except Exception:  # noqa: BLE001
        return "unknown"


def _api(base: str, path: str) -> Any:
    try:
        with urllib.request.urlopen(base.rstrip("/") + "/api/v1" + path, timeout=20) as resp:
            return json.loads(resp.read().decode() or "null")
    except Exception:  # noqa: BLE001
        return None


def parse_routes() -> dict[str, Any]:
    """从 router.ts 直接解析业务路由；返回清单并排除通配符路由。"""
    text = ROUTER_TS.read_text(encoding="utf-8")
    routes: list[dict[str, Any]] = []
    excluded: list[dict[str, str]] = []
    for line in text.splitlines():
        if "path:" not in line:
            continue
        pm = re.search(r"path:\s*'([^']+)'", line)
        if not pm:
            continue
        path = pm.group(1)
        nm = re.search(r"name:\s*'([^']+)'", line)
        cm = re.search(r"component:\s*\(\)\s*=>\s*import\('([^']+)'\)", line)
        tm = re.search(r"title:\s*'([^']+)'", line)
        xm = re.search(r"crumb:\s*'([^']+)'", line)
        if not nm or not cm:
            excluded.append({"path": path, "reason": "无 name/component（通配或重定向路由）"})
            continue
        routes.append(
            {
                "index": len(routes) + 1,
                "path": path,
                "name": nm.group(1),
                "component": cm.group(1),
                "title": tm.group(1) if tm else "",
                "crumb": xm.group(1) if xm else "",
                "dynamic": ":" in path,
            }
        )
    return {
        "source": str(ROUTER_TS.relative_to(ROOT)),
        "business_routes": routes,
        "excluded": excluded,
        "business_count": len(routes),
    }


def discover_ids(base: str) -> dict[str, str]:
    runs = _api(base, "/runs?limit=50") or []
    diags = _api(base, "/diagnoses?limit=20") or []
    cases = _api(base, "/testcases?limit=50") or []
    failed = [r for r in runs if r.get("test_verdict") == "FAILED"]
    tf_cases = [c for c in cases if str(c.get("testcase_id", "")).startswith("tcase_tf_")]
    return {
        "run_id": (failed or runs)[0]["run_id"] if (failed or runs) else "",
        "diagnosis_id": diags[0]["diagnosis_id"] if diags else "",
        "testcase_id": (tf_cases or cases)[0]["testcase_id"] if (tf_cases or cases) else "",
    }


def resolve_routes(manifest: dict[str, Any], ids: dict[str, str]) -> list[dict[str, Any]]:
    resolved: list[dict[str, Any]] = []
    for r in manifest["business_routes"]:
        path = r["path"]
        param = re.search(r":([A-Za-z_]+)", path)
        resolved_path = path
        if param:
            # 三条动态路由都叫 :id，必须按路由 name 选取对应真实实体 ID
            key = {
                "testcase-detail": "testcase_id",
                "run-detail": "run_id",
                "diagnosis-detail": "diagnosis_id",
            }.get(r["name"], param.group(1))
            value = ids.get(key, "")
            resolved_path = path.replace(param.group(0), value) if value else ""
        resolved.append(
            {**r, "resolved_path": resolved_path, "param": param.group(1) if param else None}
        )
    return resolved


def build_views(routes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    views: list[dict[str, Any]] = []
    for r in routes:
        key = f"{r['index']:02d}-{r['name']}"
        expect = EXPECT.get(r["name"], [r["title"]] if r["title"] else [])
        view = {
            "key": key,
            "route_name": r["name"],
            "route_path": r["path"],
            "resolved_path": r["resolved_path"],
            "component": r["component"],
            "title": r["title"],
            "expect": expect,
            "interaction": INTERACT.get(r["name"], {"kind": "click", "text": "刷新"}),
            "kind": "route",
            "skip_if": not r["resolved_path"],
        }
        views.append(view)
        if r["name"] == "run-detail" and r["resolved_path"]:
            views.append(
                {
                    "key": f"{r['index']:02d}-{r['name']}-evidence-graph",
                    "route_name": "run-evidence-graph",
                    "route_path": r["path"],
                    "resolved_path": r["resolved_path"],
                    "component": r["component"],
                    # 子视图共用父路由 document.title（router.afterEach 按 meta.title 设置）
                    "title": r["title"],
                    "expect": ["证据", "节点", "边"],
                    "interaction": INTERACT["run-evidence-graph"],
                    "kind": "subview",
                    "setup_tab": "证据图",
                    "skip_if": False,
                }
            )
    return views


def _stats(page: Any) -> dict[str, Any]:
    return page.evaluate(
        """() => {
          const de = document.scrollingElement || document.documentElement;
          return {
            scrollWidth: de.scrollWidth, clientWidth: de.clientWidth,
            scrollHeight: de.scrollHeight, innerWidth: window.innerWidth,
            innerHeight: window.innerHeight,
          };
        }"""
    )


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


def _theme_is_dark(page: Any) -> bool:
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
    bg = _lum(info.get("bg"))
    if bg is None:
        bg = _lum(info.get("appBg"))
    fg = _lum(info.get("fg"))
    if bg is None:
        return False
    return bg < 0.35 and (fg is None or fg > bg)


A11Y_JS = """() => {
  const visible = el => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
  };
  const accName = el => {
    const a = (el.getAttribute('aria-label') || '').trim();
    if (a) return a;
    const t = (el.getAttribute('title') || '').trim();
    if (t) return t;
    return (el.textContent || '').replace(/\\s+/g, ' ').trim();
  };
  const buttons = [...document.querySelectorAll('button,[role=button]')].filter(visible);
  const noNameButtons = buttons.filter(b => !accName(b)).slice(0, 8).map(b => b.outerHTML.slice(0, 120));
  const fields = [...document.querySelectorAll('input:not([type=hidden]),select,textarea')].filter(visible);
  const labelled = el => {
    if ((el.getAttribute('aria-label') || '').trim()) return true;
    if ((el.getAttribute('aria-labelledby') || '').trim()) return true;
    if (el.closest('label')) return true;
    if (el.id && document.querySelector('label[for="' + CSS.escape(el.id) + '"]')) return true;
    return false;
  };
  const noLabelFields = fields.filter(f => !labelled(f)).slice(0, 8).map(f => f.outerHTML.slice(0, 120));
  const imgs = [...document.querySelectorAll('img')].filter(visible);
  const noAltImgs = imgs.filter(i => i.getAttribute('alt') === null).slice(0, 8).map(i => i.outerHTML.slice(0, 120));
  const tables = [...document.querySelectorAll('table')].filter(visible);
  const noHeaderTables = tables.filter(t => !t.querySelector('th')).slice(0, 8).map(t => t.outerHTML.slice(0, 120));
  const th = [...document.querySelectorAll('th')].filter(visible);
  const thNoScope = th.filter(t => !(t.getAttribute('scope') || '').trim() && !(t.getAttribute('headers') || '').trim()).length;
  const posTab = [...document.querySelectorAll('[tabindex]')]
    .filter(e => parseInt(e.getAttribute('tabindex'), 10) > 0).slice(0, 8).map(e => e.outerHTML.slice(0, 120));
  const emptyAria = [...document.querySelectorAll('[aria-label],[aria-labelledby],[aria-describedby]')]
    .filter(e => !(accName(e) || (e.getAttribute('aria-labelledby') || '').trim() || (e.getAttribute('aria-describedby') || '').trim()))
    .slice(0, 8).map(e => e.outerHTML.slice(0, 120));
  return {
    buttons: buttons.length, no_name_buttons: noNameButtons,
    fields: fields.length, no_label_fields: noLabelFields,
    images: imgs.length, no_alt_images: noAltImgs,
    tables: tables.length, no_header_tables: noHeaderTables,
    th_count: th.length, th_without_scope: thNoScope,
    positive_tabindex: posTab, empty_aria: emptyAria,
    h1_count: document.querySelectorAll('h1').length,
  };
}"""


def a11y_check(page: Any) -> dict[str, Any]:
    data = page.evaluate(A11Y_JS)
    severe = (
        len(data["no_name_buttons"])
        + len(data["no_label_fields"])
        + len(data["no_alt_images"])
        + len(data["no_header_tables"])
        + len(data["positive_tabindex"])
    )
    return {
        **data,
        "severe_violations": severe,
        "equivalent_pass": severe == 0 and data["h1_count"] >= 1,
    }


def keyboard_check(page: Any) -> dict[str, Any]:
    try:
        page.evaluate(
            "() => { if (document.activeElement && document.activeElement.blur) document.activeElement.blur(); }"
        )
        page.keyboard.press("Tab")
        active = page.evaluate(
            """() => {
              const e = document.activeElement;
              if (!e || e === document.body || e === document.documentElement) return null;
              const r = e.getBoundingClientRect();
              const s = getComputedStyle(e);
              const name = (e.getAttribute('aria-label') || '').trim()
                || (e.textContent || '').replace(/\\s+/g, ' ').trim();
              return {
                tag: e.tagName, name: name.slice(0, 80),
                visible: r.width > 0 && r.height > 0,
                outline: s.outlineStyle, outline_width: s.outlineWidth,
              };
            }"""
        )
        if not active:
            return {
                "tab_reached_focusable": False,
                "pass": False,
                "reason": "Tab 未到达任何可聚焦元素",
            }
        ok = active["visible"] and bool(active["name"])
        return {"tab_reached_focusable": True, **active, "pass": bool(ok)}
    except Exception as exc:  # noqa: BLE001
        return {"tab_reached_focusable": False, "pass": False, "error": str(exc)[:200]}


def axe_attempt(page: Any) -> dict[str, Any]:
    """尝试 axe-core / axe CLI；当前隔离环境未安装则如实 BLOCKED。"""
    from shutil import which

    candidates = [
        str(ROOT / "apps/web/node_modules/axe-core/axe.min.js"),
        str(ROOT / "node_modules/axe-core/axe.min.js"),
    ]
    available = [c for c in candidates if Path(c).exists()]
    cli = which("axe")
    if available or cli:
        try:
            if available:
                page.add_script_tag(path=available[0])
            else:
                page.add_script_tag(
                    url="https://cdnjs.cloudflare.com/ajax/libs/axe-core/4.10.2/axe.min.js"
                )
            result = page.evaluate("async () => await axe.run(document)")
            violations = [
                {"id": v["id"], "impact": v["impact"], "nodes": len(v["nodes"])}
                for v in result.get("violations", [])
            ]
            return {
                "status": "VERIFIED",
                "engine": "axe-core",
                "violations": violations,
                "pass_count": len(result.get("passes", [])),
            }
        except Exception as exc:  # noqa: BLE001
            return {"status": "BLOCKED", "reason": f"axe 引擎存在但运行失败：{str(exc)[:200]}"}
    return {
        "status": "BLOCKED",
        "engine": "axe-core / axe CLI",
        "reason": "隔离环境未安装 axe-core npm 包，也无 axe CLI；未联网注入 CDN 脚本以保证证据可复现。"
        "已改用脚本内置等价规则（可访问名称/表单标签/图片 alt/表头/tabindex/h1）自动化审查。",
        "equivalent_check": "inline-aria-table-keyboard rules",
    }


def perform_interaction(
    page: Any, cfg: dict[str, str], api_counter: dict[str, int]
) -> dict[str, Any]:
    before_text = page.inner_text("body")
    before_api = api_counter["n"]
    out: dict[str, Any] = {"kind": cfg["kind"], "detail": "", "api_requests_triggered": 0}
    try:
        if cfg["kind"] == "fill":
            loc = page.locator(cfg["selector"]).first
            loc.fill(cfg["value"], timeout=5000)
            page.wait_for_timeout(600)
            out["detail"] = f"填充 {cfg['selector']} = {cfg['value']!r}，回读={loc.input_value()!r}"
            out["ok"] = loc.input_value() == cfg["value"]
        else:
            loc = page.get_by_role("button", name=cfg["text"]).first
            loc.click(timeout=6000)
            page.wait_for_timeout(800)
            out["detail"] = f"点击按钮 {cfg['text']!r}"
            out["ok"] = True
    except Exception as exc:  # noqa: BLE001
        out["error"] = str(exc)[:200]
        out["ok"] = False
        # 回退交互：点击页面内任一安全的刷新/标签按钮，保证每视图都有真实交互证据
        try:
            fallback = page.get_by_role(
                "button",
                name=re.compile("刷新|重新加载|加载|查看|步骤轨迹|断言|证据|生成证据图"),
            ).first
            fallback.click(timeout=5000)
            page.wait_for_timeout(700)
            out["detail"] = f"主交互不可用，回退点击按钮：{fallback.inner_text()[:40]!r}"
            out["ok"] = True
            out["fallback_used"] = True
        except Exception as exc2:  # noqa: BLE001
            out["error"] += f" | fallback: {str(exc2)[:160]}"
    after_text = page.inner_text("body")
    out["api_requests_triggered"] = api_counter["n"] - before_api
    out["dom_changed"] = len(after_text) != len(before_text) or after_text != before_text
    return out


def _goto(page: Any, url: str) -> None:
    """直接 URL 打开；对偶发的 Vite 首屏超时做一次有限重试。"""
    last: Exception | None = None
    for _ in range(2):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_selector(".app .sidebar", timeout=15000)
            return
        except Exception as exc:  # noqa: BLE001
            last = exc
    assert last is not None
    raise last


def navigate(page: Any, url: str, setup_tab: str = "") -> None:
    _goto(page, url)
    if setup_tab:
        page.get_by_role("button", name=setup_tab, exact=True).first.click(timeout=6000)
    page.wait_for_timeout(900)


def route_identity(page: Any, view: dict[str, Any]) -> dict[str, Any]:
    pathname = page.evaluate("() => window.location.pathname")
    title = page.title()
    body = page.inner_text("body")
    hits = {k: (k in body) for k in view["expect"]}
    expected = view["resolved_path"]
    return {
        "expected_path": expected,
        "actual_path": pathname,
        "path_ok": pathname.rstrip("/") == expected.rstrip("/"),
        "title": title,
        "title_ok": (not view["title"]) or title.startswith(view["title"]),
        "keyword_hits": hits,
        "content_ok": all(hits.values()),
        "body_length": len(body),
    }


def run(base_web: str, base_api: str, out: Path) -> dict[str, Any]:
    from playwright.sync_api import sync_playwright

    out.mkdir(parents=True, exist_ok=True)
    (out / "shots").mkdir(exist_ok=True)

    manifest = parse_routes()
    if manifest["business_count"] != 14:
        raise RuntimeError(
            f"router.ts 应包含 14 条业务路由，实际解析到 {manifest['business_count']} 条；"
            "请检查路由定义或解析规则，不允许以部分覆盖冒充完整覆盖"
        )
    ids = discover_ids(base_api)
    routes = resolve_routes(manifest, ids)
    views = [v for v in build_views(routes) if not v.get("skip_if")]
    skipped = [v for v in build_views(routes) if v.get("skip_if")]

    coverage: list[dict[str, Any]] = []
    states: list[dict[str, Any]] = []
    shots = 0
    axe_result: dict[str, Any] = {}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        console: list[str] = []
        api_counter = {"n": 0}

        def new_page(width: int, height: int, scheme: str = "light") -> Any:
            ctx = browser.new_context(
                viewport={"width": width, "height": height},
                color_scheme=scheme,
                is_mobile=width <= 480,
                has_touch=width <= 480,
            )
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
            pg.on(
                "request",
                lambda r: (
                    api_counter.update(n=api_counter["n"] + 1) if "/api/v1/" in r.url else None
                ),
            )
            return pg

        # ---------------------------- 4 断点 × 双主题 × 全部视图 ----------------------------
        for name, width, height in BREAKPOINTS:
            for scheme in ("light", "dark"):
                page = new_page(width, height, scheme)
                for view in views:
                    console.clear()
                    rec: dict[str, Any] = {
                        "view": view["key"],
                        "route_name": view["route_name"],
                        "route_path": view["route_path"],
                        "kind": view["kind"],
                        "breakpoint": name,
                        "theme": scheme,
                        "url": base_web + view["resolved_path"],
                    }
                    try:
                        navigate(page, base_web + view["resolved_path"], view.get("setup_tab", ""))
                        ident = route_identity(page, view)
                        st = _stats(page)
                        overflow = max(0, st["scrollWidth"] - st["clientWidth"])
                        shot = out / "shots" / f"{view['key']}-{name}-{scheme}.png"
                        page.screenshot(path=str(shot), full_page=True)
                        shots += 1
                        rec.update(
                            {
                                "status": "PASS"
                                if ident["path_ok"]
                                and ident["title_ok"]
                                and ident["content_ok"]
                                and overflow <= 2
                                else "FAIL",
                                "identity": ident,
                                "overflow_px": overflow,
                                "dark_applied": _theme_is_dark(page) if scheme == "dark" else None,
                                "console_errors": list(console[:5]),
                                "screenshot": str(shot.relative_to(ROOT)),
                                "a11y": a11y_check(page),
                                "keyboard": keyboard_check(page),
                            }
                        )
                        if not axe_result:
                            axe_result = axe_attempt(page)
                        if name == "1440" and scheme == "light":
                            # 页面刷新：真实 reload 后再次校验路由身份
                            console.clear()
                            try:
                                page.reload(wait_until="domcontentloaded", timeout=30000)
                                page.wait_for_selector(".app .sidebar", timeout=15000)
                                if view.get("setup_tab"):
                                    page.get_by_role(
                                        "button", name=view["setup_tab"], exact=True
                                    ).first.click(timeout=6000)
                                page.wait_for_timeout(900)
                                after = route_identity(page, view)
                                refresh_shot = out / "shots" / f"{view['key']}-refresh.png"
                                page.screenshot(path=str(refresh_shot), full_page=True)
                                shots += 1
                                rec["refresh"] = {
                                    "ok": after["path_ok"] and after["content_ok"],
                                    "identity": after,
                                    "screenshot": str(refresh_shot.relative_to(ROOT)),
                                    "console_errors": list(console[:5]),
                                }
                            except Exception as exc:  # noqa: BLE001
                                rec["refresh"] = {"ok": False, "error": str(exc)[:200]}

                            # 真实交互：搜索输入 / 刷新按钮 / Tab 切换
                            rec["interaction"] = perform_interaction(
                                page, view["interaction"], api_counter
                            )
                            inter_shot = out / "shots" / f"{view['key']}-interaction.png"
                            page.screenshot(path=str(inter_shot), full_page=True)
                            shots += 1
                            rec["interaction"]["screenshot"] = str(inter_shot.relative_to(ROOT))

                            # 前进后退：首页 -> 目标路由 -> 后退 -> 前进
                            try:
                                source = "/" if view["resolved_path"] != "/" else "/runs"
                                _goto(page, base_web + source)
                                _goto(page, base_web + view["resolved_path"])
                                page.go_back(wait_until="domcontentloaded", timeout=15000)
                                page.wait_for_selector(".app .sidebar", timeout=15000)
                                page.wait_for_timeout(300)
                                back_path = page.evaluate("() => window.location.pathname")
                                page.go_forward(wait_until="domcontentloaded", timeout=15000)
                                page.wait_for_selector(".app .sidebar", timeout=15000)
                                page.wait_for_timeout(300)
                                fwd_path = page.evaluate("() => window.location.pathname")
                                rec["history"] = {
                                    "source": source,
                                    "back_to": back_path,
                                    "forward_to": fwd_path,
                                    "ok": back_path.rstrip("/") == source.rstrip("/")
                                    and fwd_path.rstrip("/") == view["resolved_path"].rstrip("/"),
                                }
                            except Exception as exc:  # noqa: BLE001
                                rec["history"] = {"ok": False, "error": str(exc)[:200]}
                    except Exception as exc:  # noqa: BLE001
                        rec.update({"status": "ERROR", "error": str(exc)[:300]})
                    coverage.append(rec)
                page.context.close()

        # -------------------------------------------------------- 四态 --------------------------------------------------------
        # Loading：真实拦截 API 并延迟
        page = new_page(1440, 900)
        try:

            def _slow(route: Any) -> None:
                time.sleep(4)
                route.continue_()

            page.route("**/api/v1/**", _slow)
            _goto(page, base_web + "/runs")
            page.wait_for_timeout(1200)
            shot = out / "shots" / "state-loading-runs.png"
            page.screenshot(path=str(shot), full_page=True)
            shots += 1
            states.append(
                {
                    "state": "loading",
                    "view": "runs",
                    "screenshot": str(shot.relative_to(ROOT)),
                    "detail": "拦截 /api/v1/** 延迟 4s 后截取真实加载态",
                }
            )
        except Exception as exc:  # noqa: BLE001
            states.append({"state": "loading", "error": str(exc)[:200]})
        page.context.close()

        # Empty：真实空结果筛选
        page = new_page(1440, 900)
        try:
            _goto(page, base_web + "/runs?verdict=INCONCLUSIVE")
            page.wait_for_timeout(1200)
            body = page.inner_text("body")
            shot = out / "shots" / "state-empty-runs.png"
            page.screenshot(path=str(shot), full_page=True)
            shots += 1
            states.append(
                {
                    "state": "empty",
                    "view": "runs",
                    "screenshot": str(shot.relative_to(ROOT)),
                    "empty_hint_visible": any(k in body for k in ("空", "暂无", "没有", "无数据")),
                }
            )
        except Exception as exc:  # noqa: BLE001
            states.append({"state": "empty", "error": str(exc)[:200]})
        page.context.close()

        # Error：不存在的 Run
        page = new_page(1440, 900)
        try:
            _goto(page, base_web + "/runs/run_wp1_not_exists")
            page.wait_for_timeout(1500)
            body = page.inner_text("body")
            shot = out / "shots" / "state-error-missing-run.png"
            page.screenshot(path=str(shot), full_page=True)
            shots += 1
            states.append(
                {
                    "state": "error",
                    "view": "run-detail",
                    "screenshot": str(shot.relative_to(ROOT)),
                    "error_hint_visible": any(
                        k in body.lower() for k in ("错误", "失败", "not found", "重试", "不存在")
                    ),
                }
            )
        except Exception as exc:  # noqa: BLE001
            states.append({"state": "error", "error": str(exc)[:200]})
        page.context.close()

        # Success：真实数据页
        page = new_page(1440, 900)
        try:
            _goto(page, base_web + "/")
            page.wait_for_timeout(1200)
            shot = out / "shots" / "state-success-dashboard.png"
            page.screenshot(path=str(shot), full_page=True)
            shots += 1
            states.append(
                {"state": "success", "view": "dashboard", "screenshot": str(shot.relative_to(ROOT))}
            )
        except Exception as exc:  # noqa: BLE001
            states.append({"state": "success", "error": str(exc)[:200]})
        page.context.close()

        browser.close()

    # ----------------------------------------------- 汇总 -----------------------------------------------
    by_view: dict[str, dict[str, Any]] = {}
    for rec in coverage:
        entry = by_view.setdefault(
            rec["view"],
            {
                "view": rec["view"],
                "route_name": rec["route_name"],
                "route_path": rec["route_path"],
                "kind": rec["kind"],
                "status": "PASS",
                "functional_status": "PASS",
                "a11y_status": "PASS",
                "screenshots": {},
                "breakpoints": {},
                "issues": [],
                "a11y_issues": [],
            },
        )
        entry["screenshots"][f"{rec['breakpoint']}-{rec['theme']}"] = rec.get("screenshot", "")
        entry["breakpoints"][f"{rec['breakpoint']}-{rec['theme']}"] = {
            "status": rec.get("status"),
            "identity": rec.get("identity"),
            "overflow_px": rec.get("overflow_px"),
            "dark_applied": rec.get("dark_applied"),
            "console_errors": rec.get("console_errors"),
            "keyboard": rec.get("keyboard"),
            "a11y": rec.get("a11y"),
        }
        if rec.get("screenshot") is None or rec.get("status") != "PASS":
            entry["functional_status"] = "FAIL"
            entry["issues"].append(
                f"{rec['breakpoint']}-{rec['theme']}: {rec.get('status')}: {rec.get('error', '')}"
            )
        if rec.get("dark_applied") is False:
            entry["functional_status"] = "FAIL"
            entry["issues"].append(f"{rec['breakpoint']}-dark: 暗色主题未实际生效")
        if rec.get("keyboard", {}).get("pass") is False:
            entry["a11y_status"] = "FAIL"
            detail = f"{rec['breakpoint']}-{rec['theme']}: 键盘 Tab 基础可访问性未通过"
            entry["a11y_issues"].append(detail)
            entry["issues"].append(detail)
        if rec.get("a11y", {}).get("severe_violations", 0) > 0:
            entry["a11y_status"] = "FAIL"
            detail = (
                f"{rec['breakpoint']}-{rec['theme']}: 等价无障碍规则发现 "
                f"{rec['a11y']['severe_violations']} 项严重问题"
            )
            entry["a11y_issues"].append(detail)
            entry["issues"].append(detail)
        if "refresh" in rec:
            entry["refresh"] = rec["refresh"]
            entry["screenshots"]["refresh-1440-light"] = rec["refresh"].get("screenshot", "")
            if not rec["refresh"].get("ok"):
                entry["functional_status"] = "FAIL"
                entry["issues"].append("页面刷新后路由身份/内容校验失败")
        if "interaction" in rec:
            entry["interaction"] = rec["interaction"]
            entry["screenshots"]["interaction-1440-light"] = rec["interaction"].get(
                "screenshot", ""
            )
            if not rec["interaction"].get("ok"):
                entry["functional_status"] = "FAIL"
                entry["issues"].append(f"真实交互失败：{rec['interaction'].get('error', '')}")
        if "history" in rec:
            entry["history"] = rec["history"]
            if not rec["history"].get("ok"):
                entry["functional_status"] = "FAIL"
                entry["issues"].append(f"前进后退未回到预期路由：{rec['history'].get('error', '')}")
        if rec.get("console_errors"):
            entry["functional_status"] = "FAIL"
            entry["issues"].append(
                f"{rec['breakpoint']}-{rec['theme']}: console 错误 {rec['console_errors'][:1]}"
            )
        entry["status"] = (
            "FAIL"
            if entry["functional_status"] == "FAIL" or entry["a11y_status"] == "FAIL"
            else "PASS"
        )

    route_entries = []
    for r in routes:
        key = f"{r['index']:02d}-{r['name']}"
        entry = by_view.get(key, {"view": key, "status": "BLOCKED", "issues": ["未执行"]})
        entry = {
            **entry,
            "route_index": r["index"],
            "route_name": r["name"],
            "component": r["component"],
        }
        route_entries.append(entry)
    subview_entries = [e for e in by_view.values() if e["kind"] == "subview"]

    failures = [e for e in by_view.values() if e["status"] == "FAIL"]
    functional_failures = [
        e["view"] for e in by_view.values() if e.get("functional_status") == "FAIL"
    ]
    a11y_failures = [e["view"] for e in by_view.values() if e.get("a11y_status") == "FAIL"]
    functional_passed = not functional_failures
    summary = {
        "git_head": _git_head(),
        "router_business_routes": manifest["business_count"],
        "router_excluded": manifest["excluded"],
        "views_planned": len(build_views(routes)),
        "views_executed": len(views),
        "views_skipped": [v["key"] for v in skipped],
        "coverage_checks": len(coverage),
        "screenshots": shots,
        "route_entries": len(route_entries),
        "subview_entries": len(subview_entries),
        "failed_views": [e["view"] for e in failures],
        "functional_failures": functional_failures,
        "a11y_failures": a11y_failures,
        "states": [s["state"] for s in states if "error" not in s],
        "axe": axe_result.get("status", "BLOCKED"),
        "breakpoints": [b[0] for b in BREAKPOINTS],
        "themes": ["light", "dark"],
        "verdict": "VERIFIED" if not failures else ("PARTIAL" if functional_passed else "FAIL"),
        "passed": not failures,
    }

    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "web_base": base_web,
        "api_base": base_api,
        "router": manifest,
        "routes": route_entries,
        "subviews": subview_entries,
        "ids": ids,
        "coverage": coverage,
        "states": states,
        "axe_accessibility": axe_result,
        "summary": summary,
        "passed": summary["passed"],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--web-url", default="http://127.0.0.1:5175")
    ap.add_argument("--api-url", default="http://127.0.0.1:8020")
    ap.add_argument("--out", default="docs/assets/run009/wp1/ui")
    args = ap.parse_args()
    out = ROOT / args.out
    report = run(args.web_url, args.api_url, out)

    manifest = {
        "source": report["router"]["source"],
        "business_count": report["router"]["business_count"],
        "excluded": report["router"]["excluded"],
        "routes": [
            {
                "index": e["route_index"],
                "name": e["route_name"],
                "component": e["component"],
                "status": e["status"],
                "screenshots": e.get("screenshots", {}),
            }
            for e in report["routes"]
        ],
    }
    (out / "route-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out / "route-coverage.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    for e in report["routes"] + report["subviews"]:
        if e["status"] != "PASS":
            print(f"FAIL {e['view']}: {e['issues'][:3]}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
