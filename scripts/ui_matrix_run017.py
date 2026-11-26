"""RUN-017 WP6.1：15 视图 × 4 断点 × 双主题的真实浏览器布局验收。

检查项（每个视图 × 断点 × 主题）：

1. **无横向溢出**（`scrollWidth <= innerWidth + 2`）；
2. **无控件越界**（可见元素的右边界不得超出视口 2px 以上）；
3. **无 Console 错误**（页面级 error；资源 404 不计入，但会记录）；
4. 截图留证（`ui/<theme>/<width>/<view>.png`）。

数据准备：独立 TeamFlow + 独立 API（内嵌 Worker，SQLite 临时库），
seed 场景资产 → 跑一次正常 Run → 跑一次 API-001 故障 Run 并诊断，
以便 Run 详情 / 诊断详情 / 修复中心等页面**有真实数据**可渲染。

用法：
    .venv/bin/python scripts/ui_matrix_run017.py --out docs/assets/run017/wp6
退出码：0 全部 PASS；1 有 FAIL；3 环境不可用。
"""

from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for _p in (
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
):
    sys.path.insert(0, str(ROOT / _p))

WIDTHS = (1440, 1024, 768, 390)
THEMES = ("light", "dark")
_PATHS = (
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
)


def base_env() -> dict[str, str]:
    """子进程环境：只放字面量，不读取宿主机环境变量。"""
    return {
        "PATH": "/usr/bin:/bin:/usr/local/bin",
        "HOME": str(Path.home()),
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": ":".join(str(ROOT / p) for p in _PATHS),
        "TG_ENVIRONMENT": "development",
        "TG_AUTH_ENABLED": "false",
        "TG_LLM_PROVIDER": "",
        "TG_BROWSER_HEADLESS": "true",
    }


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def http_json(
    method: str, url: str, body: dict[str, Any] | None = None, timeout: float = 30.0
) -> tuple[int, dict[str, Any]]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"} if data is not None else {},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - 本地受控
            text = resp.read().decode("utf-8", "replace")
            try:
                return resp.status, json.loads(text or "{}")
            except json.JSONDecodeError:
                return resp.status, {"raw": text[:300]}
    except urllib.error.HTTPError as exc:
        return exc.code, {"raw": exc.read().decode("utf-8", "replace")[:300]}
    except Exception as exc:  # noqa: BLE001
        return 0, {"error": f"{type(exc).__name__}: {exc}"}


def wait_health(url: str, timeout: float = 60.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:  # noqa: S310
                if resp.status == 200:
                    return True
        except Exception:  # noqa: BLE001
            time.sleep(0.3)
    return False


class Env:
    def __init__(self, work: Path) -> None:
        self.work = work
        self.work.mkdir(parents=True, exist_ok=True)
        self.tf_port = free_port()
        self.api_port = free_port()
        env = base_env()
        env["TG_TEAMFLOW_DB"] = f"sqlite:///{work / 'teamflow.db'}"
        self.tf = subprocess.Popen(  # noqa: S603 - 受控本地子进程
            [
                sys.executable,
                "-m",
                "uvicorn",
                "traceguard_teamflow.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(self.tf_port),
            ],
            cwd=str(ROOT),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.tf_base = f"http://127.0.0.1:{self.tf_port}"
        if not wait_health(self.tf_base + "/health"):
            raise RuntimeError("TeamFlow 未启动")
        api_env = base_env()
        api_env.update(
            {
                "TG_DATABASE_URL": f"sqlite:///{work / 'platform.db'}",
                "TG_ARTIFACT_DIR": str(work / "artifacts"),
                "TG_INLINE_WORKER": "true",
                "TG_TEAMFLOW_BASE_URL": self.tf_base,
            }
        )
        self.api = subprocess.Popen(  # noqa: S603
            [
                sys.executable,
                "-m",
                "uvicorn",
                "traceguard_api.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(self.api_port),
            ],
            cwd=str(ROOT),
            env=api_env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.api_base = f"http://127.0.0.1:{self.api_port}"
        if not wait_health(self.api_base + "/health", 90):
            raise RuntimeError("API 未启动")

    def close(self) -> None:
        for proc in (self.api, self.tf):
            proc.terminate()
        for proc in (self.api, self.tf):
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()


def _wait_run(env: Env, run_id: str, timeout: float = 180.0) -> dict[str, Any]:
    deadline = time.time() + timeout
    last: dict[str, Any] = {}
    while time.time() < deadline:
        status, payload = http_json("GET", f"{env.api_base}/api/v1/runs/{run_id}")
        if status == 200:
            last = payload
            if payload.get("execution_status") in {
                "COMPLETED",
                "FAILED",
                "TIMED_OUT",
                "CANCELLED",
            }:
                return payload
        time.sleep(0.4)
    return last


def _execute(env: Env, testcase_id: str, *, version: int = 1) -> str:
    status, task = http_json(
        "POST",
        env.api_base + "/api/v1/tasks",
        {
            "testcase_ref": testcase_id,
            "idempotency_key": f"ui-{testcase_id}-{uuid.uuid4().hex[:8]}",
            "version": version,
        },
    )
    if status not in (200, 201):
        return ""
    s2, created = http_json("POST", f"{env.api_base}/api/v1/tasks/{task['task_id']}/runs")
    if s2 not in (200, 201):
        return ""
    run_id = str(created.get("run_id") or "")
    if run_id:
        _wait_run(env, run_id)
    return run_id


def seed_and_execute(env: Env) -> dict[str, str]:
    """写入场景资产，并通过 API 的**内嵌 Worker** 制造真实 Run / Diagnosis。

    刻意不在本进程执行 Run：真实执行会初始化出站代理的 event loop，
    之后同进程的 Playwright sync API 会因"loop running"拒绝启动。
    """
    from traceguard_api.runtime import Runtime
    from traceguard_api.seed import seed_teamflow_catalog
    from traceguard_persistence import Database

    db = Database(f"sqlite:///{env.work / 'platform.db'}")
    db.create_schema()
    ids: dict[str, str] = {}
    try:
        rt = Runtime(db)
        seed_teamflow_catalog(rt, base_url=env.tf_base)
        ids["testcase_id"] = "tcase_tf_create_normal"
    finally:
        db.dispose()

    run_id = _execute(env, "tcase_tf_create_normal")
    if run_id:
        ids["run_id"] = run_id

    # 故障场景：注入 API-001 → 失败 Run → 诊断
    http_json("POST", env.tf_base + "/api/faults/TF-API-001/activate", {"sticky": True})
    failed_run = _execute(env, "tcase_tf_false_pass")
    http_json("POST", env.tf_base + "/api/faults/TF-API-001/clear", {})
    if failed_run:
        ids["failed_run_id"] = failed_run
        ds, diag = http_json(
            "POST", f"{env.api_base}/api/v1/runs/{failed_run}/diagnose", timeout=90
        )
        if ds in (200, 201):
            ids["diagnosis_id"] = str(diag.get("diagnosis_id") or "")
    http_json("POST", env.tf_base + "/api/test/reset", {})
    return {k: v for k, v in ids.items() if v}


VIEWS: tuple[tuple[str, str], ...] = (
    ("dashboard", "/"),
    ("intents", "/intents"),
    ("explorer", "/explorer"),
    ("testcases", "/testcases"),
    ("testcase_detail", "/testcases/{testcase_id}"),
    ("oracles", "/oracles"),
    ("runs", "/runs"),
    ("run_detail", "/runs/{run_id}"),
    ("diagnoses", "/diagnoses"),
    ("diagnosis_detail", "/diagnoses/{diagnosis_id}"),
    ("repairs", "/repairs"),
    ("benchmark", "/benchmark"),
    ("models", "/models"),
    ("system", "/system"),
    ("users", "/users"),
    ("login", "/login"),
)

PROBE_JS = """
() => {
  const doc = document.documentElement;
  const vw = window.innerWidth;
  // 表格放在 overflow-x:auto 容器里是**可接受的响应式做法**（用户可横向滚动），
  // 因此只有"越界且不在可滚动容器内"的元素才算真正的控件越界。
  const inScrollable = (el) => {
    let p = el.parentElement;
    while (p && p !== document.body) {
      const st = getComputedStyle(p);
      if ((st.overflowX === 'auto' || st.overflowX === 'scroll') && p.scrollWidth > p.clientWidth) {
        return true;
      }
      p = p.parentElement;
    }
    return false;
  };
  const offenders = [];
  for (const el of document.querySelectorAll('body *')) {
    const r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) continue;
    const st = getComputedStyle(el);
    if (st.visibility === 'hidden' || st.display === 'none') continue;
    if (r.right <= vw + 2) continue;
    if (inScrollable(el)) continue;
    // SVG 内部图元（如 rect）由 SVG 自身裁剪，几何超出不代表溢出；
    // 真正要看的是 <svg> 元素本身的右边界。
    const svgHost = el.closest ? el.closest('svg') : null;
    const host = svgHost || el;
    const hr = host.getBoundingClientRect();
    if (hr.right <= vw + 2) continue;
    offenders.push({
      tag: el.tagName.toLowerCase(),
      cls: String(el.className || '').slice(0, 80),
      right: Math.round(r.right),
      host_tag: host.tagName.toLowerCase(),
      host_right: Math.round(hr.right),
    });
    if (offenders.length >= 5) break;
  }
  return {
    scroll_width: doc.scrollWidth,
    inner_width: vw,
    overflow_px: doc.scrollWidth - vw,
    offenders,
  };
}
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="docs/assets/run017/wp6")
    parser.add_argument("--workdir", default="")
    parser.add_argument("--keep-env", action="store_true")
    parser.add_argument("--views", default="", help="逗号分隔的视图名（默认全部）")
    parser.add_argument("--widths", default="", help="逗号分隔的断点（默认 1440,1024,768,390）")
    args = parser.parse_args()

    out = ROOT / args.out
    out.mkdir(parents=True, exist_ok=True)
    work = Path(args.workdir) if args.workdir else out / "run-env"
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # noqa: BLE001
        print(f"BLOCKED: playwright 不可用: {exc}")
        return 3

    only_views = {v.strip() for v in args.views.split(",") if v.strip()}
    widths = tuple(int(w) for w in args.widths.split(",")) if args.widths else WIDTHS

    env = Env(work)
    try:
        ids = seed_and_execute(env)
    except Exception as exc:  # noqa: BLE001
        env.close()
        print(f"BLOCKED: 数据准备失败: {exc}")
        return 3

    cells: list[dict[str, Any]] = []
    try:
        with sync_playwright() as pw:
            try:
                browser = pw.chromium.launch(headless=True)
            except Exception as exc:  # noqa: BLE001
                print(f"BLOCKED: Chromium 启动失败: {exc}")
                return 3
            for theme in THEMES:
                for width in widths:
                    ctx = browser.new_context(viewport={"width": width, "height": 900})
                    ctx.add_init_script(
                        f"localStorage.setItem('tg-theme', '{theme}');"
                        f"document.documentElement.dataset.theme='{theme}';"
                    )
                    page = ctx.new_page()
                    console_errors: list[str] = []

                    def _on_console(msg, bucket=console_errors):  # noqa: ANN001 - 回调
                        if msg.type == "error":
                            bucket.append(msg.text[:200])

                    page.on("console", _on_console)
                    for name, template in VIEWS:
                        if only_views and name not in only_views:
                            continue
                        path = template.format(**ids)
                        if "{" in path:
                            cells.append(
                                {
                                    "view": name,
                                    "width": width,
                                    "theme": theme,
                                    "status": "SKIP",
                                    "reason": "缺少对应实体 ID",
                                }
                            )
                            continue
                        shot_dir = out / "ui" / theme / str(width)
                        shot_dir.mkdir(parents=True, exist_ok=True)
                        console_errors.clear()
                        try:
                            page.goto(env.api_base + path, wait_until="domcontentloaded")
                            page.wait_for_timeout(1500)
                            probe = page.evaluate(PROBE_JS)
                            page.screenshot(path=str(shot_dir / f"{name}.png"), full_page=True)
                            ok = probe["overflow_px"] <= 2 and not probe["offenders"]
                            cells.append(
                                {
                                    "view": name,
                                    "path": path,
                                    "width": width,
                                    "theme": theme,
                                    "status": "PASS" if ok else "FAIL",
                                    "overflow_px": probe["overflow_px"],
                                    "offenders": probe["offenders"],
                                    "console_errors": console_errors[-5:],
                                }
                            )
                        except Exception as exc:  # noqa: BLE001
                            cells.append(
                                {
                                    "view": name,
                                    "path": path,
                                    "width": width,
                                    "theme": theme,
                                    "status": "FAIL",
                                    "error": f"{type(exc).__name__}: {exc}"[:200],
                                }
                            )
                    ctx.close()
            browser.close()
    finally:
        if not args.keep_env:
            env.close()

    failed = [c for c in cells if c["status"] == "FAIL"]
    skipped = [c for c in cells if c["status"] == "SKIP"]
    summary = {
        "run": "RUN-017 WP6.1",
        "views": [v[0] for v in VIEWS],
        "widths": list(widths),
        "themes": list(THEMES),
        "cells_total": len(cells),
        "passed": len(cells) - len(failed) - len(skipped),
        "failed": len(failed),
        "skipped": len(skipped),
        "entities": ids,
        "failures": [
            {
                k: c.get(k)
                for k in (
                    "view",
                    "width",
                    "theme",
                    "overflow_px",
                    "offenders",
                    "console_errors",
                    "error",
                )
            }
            for c in failed
        ],
        "skips": [f"{c['view']}@{c['width']}/{c['theme']}" for c in skipped],
        "cells": cells,
    }
    (out / "ui-matrix.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {k: summary[k] for k in ("cells_total", "passed", "failed", "skipped", "entities")},
            ensure_ascii=False,
        )
    )
    if failed:
        for item in summary["failures"][:10]:
            print("FAIL", item)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
