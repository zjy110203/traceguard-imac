"""RUN-017 WP5.3：环境诊断入口（`scripts/doctor.py`）。

原则：

- **默认只读**：不写配置、不改数据库、不启动/停止任何服务；
- 每项检查输出 `PASS` / `WARN` / `FAIL` / `BLOCKED`，BLOCKED 表示依赖不可用（不是通过）；
- 永不打印密码 / API Key / Cookie / DSN 中的口令（输出前统一脱敏）；
- 退出码：0 = 没有 FAIL（可含 WARN/BLOCKED）；1 = 有 FAIL。

用法：
    .venv/bin/python scripts/doctor.py
    .venv/bin/python scripts/doctor.py --json docs/assets/run017/wp5/doctor.json
    .venv/bin/python scripts/doctor.py --api-url http://127.0.0.1:8000 --teamflow-url http://127.0.0.1:8001
"""

from __future__ import annotations

import argparse
import json
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
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
):
    sys.path.insert(0, str(ROOT / _p))

PASS, WARN, FAIL, BLOCKED = "PASS", "WARN", "FAIL", "BLOCKED"


def redact(text: str) -> str:
    """把 DSN 口令、常见密钥形态替换为 ***（诊断输出也必须可安全粘贴）。"""
    import re

    out = re.sub(r"(://[^:/@\s]+:)([^@/\s]+)(@)", r"\1***\3", text)
    out = re.sub(r"(?i)(api[_-]?key|token|password|secret)([=:]\s*)([^\s,;\"']+)", r"\2***", out)
    return out


def _row(name: str, status: str, detail: str = "", hint: str = "") -> dict[str, Any]:
    return {"check": name, "status": status, "detail": redact(detail), "hint": redact(hint)}


def check_python() -> dict[str, Any]:
    v = sys.version_info
    if (v.major, v.minor) < (3, 11):
        return _row("python", FAIL, f"{sys.version.split()[0]}", "需要 >= 3.11,<3.14")
    if (v.major, v.minor) >= (3, 14):
        return _row("python", WARN, f"{sys.version.split()[0]}", "项目声明 <3.14，请用 3.11–3.13")
    return _row("python", PASS, sys.version.split()[0])


def check_dependencies() -> dict[str, Any]:
    missing = []
    for module in ("fastapi", "sqlalchemy", "pydantic", "uvicorn"):
        try:
            __import__(module)
        except Exception:  # noqa: BLE001
            missing.append(module)
    if missing:
        return _row(
            "dependencies", FAIL, "缺少: " + ", ".join(missing), "pip install -r requirements.txt"
        )
    return _row("dependencies", PASS, "fastapi / sqlalchemy / pydantic / uvicorn 可用")


def check_playwright() -> dict[str, Any]:
    try:
        import playwright  # noqa: F401
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # noqa: BLE001
        return _row("playwright", FAIL, str(exc)[:200], "pip install playwright")
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            version = browser.version
            browser.close()
    except Exception as exc:  # noqa: BLE001
        return _row(
            "playwright_chromium",
            BLOCKED,
            f"{type(exc).__name__}: {exc}"[:300],
            "python -m playwright install chromium",
        )
    return _row("playwright_chromium", PASS, f"chromium {version}")


def check_node() -> dict[str, Any]:
    node = shutil.which("node")
    npm = shutil.which("npm")
    if not node or not npm:
        return _row("node", BLOCKED, "node/npm 不在 PATH", "安装 Node.js 20+")
    try:
        version = subprocess.run(  # noqa: S603 - 只读版本查询
            [node, "--version"], capture_output=True, text=True, timeout=5
        ).stdout.strip()
    except Exception as exc:  # noqa: BLE001
        return _row("node", WARN, str(exc)[:150])
    return _row("node", PASS, f"{version} / npm {shutil.os.path.basename(npm)}")


def check_frontend_build() -> dict[str, Any]:
    dist = ROOT / "apps" / "web" / "dist"
    index = dist / "index.html"
    if not index.is_file():
        return _row("frontend_dist", WARN, "apps/web/dist 不存在", "cd apps/web && npm run build")
    size = sum(p.stat().st_size for p in dist.rglob("*") if p.is_file())
    return _row("frontend_dist", PASS, f"index.html 存在，共 {size // 1024} KiB")


def check_config() -> dict[str, Any]:
    env_file = ROOT / ".env"
    example = ROOT / ".env.example"
    if env_file.is_file():
        # 只判断存在与可读，绝不打印内容（可能含密钥）
        return _row(
            "config", PASS, f".env 存在（不打印内容）；示例 {example.name} 存在={example.is_file()}"
        )
    return _row(
        "config",
        WARN,
        "未找到 .env，将使用默认配置（SQLite + 本地产物目录）",
        "cp .env.example .env 并按需修改",
    )


def check_database() -> dict[str, Any]:
    from traceguard_core.config import get_settings

    settings = get_settings()
    url = settings.database_url
    try:
        from traceguard_persistence import Database

        db = Database(url)
        with db.session() as s:
            from sqlalchemy import text

            s.execute(text("SELECT 1"))
            try:
                rows = s.execute(text("SELECT version FROM tg_schema_migration")).all()
                versions = sorted(int(r[0]) for r in rows)
            except Exception:  # noqa: BLE001 - 空库没有迁移表
                versions = []
        db.dispose()
    except Exception as exc:  # noqa: BLE001
        return _row("database", FAIL, f"{type(exc).__name__}: {exc}"[:300], "检查 TG_DATABASE_URL")
    from traceguard_persistence.db import MIGRATIONS

    expected = sorted(v for v, _n, _m in MIGRATIONS)
    if not versions:
        return _row(
            "database_migrations", WARN, "数据库可连接但未初始化", "首次启动 API 会自动建表"
        )
    if versions != expected:
        return _row(
            "database_migrations",
            WARN,
            f"已应用 {versions}，代码期望 {expected}",
            "启动一次 API 触发迁移（迁移需幂等）",
        )
    return _row("database_migrations", PASS, f"{len(versions)} 个迁移已应用")


def check_artifact_dir() -> dict[str, Any]:
    from traceguard_core.config import get_settings

    root = Path(get_settings().artifact_dir)
    try:
        root.mkdir(parents=True, exist_ok=True)
        probe = root / f".doctor-{int(time.time())}.tmp"
        probe.write_bytes(b"ok")
        writable = probe.read_bytes() == b"ok"
        probe.unlink(missing_ok=True)
    except Exception as exc:  # noqa: BLE001
        return _row("artifact_dir", FAIL, f"{type(exc).__name__}: {exc}"[:200], str(root))
    return _row("artifact_dir", PASS if writable else FAIL, f"{root} 可读写")


def _http_health(url: str, timeout: float = 3.0) -> tuple[bool, str]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 - 本机受控
            return resp.status == 200, f"HTTP {resp.status}"
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"[:160]


def check_services(api_url: str, teamflow_url: str) -> list[dict[str, Any]]:
    rows = []
    ok, detail = _http_health(api_url.rstrip("/") + "/health")
    rows.append(
        _row(
            "api_health",
            PASS if ok else WARN,
            detail,
            "" if ok else "API 未启动（本地演示可稍后启动）",
        )
    )
    ok, detail = _http_health(teamflow_url.rstrip("/") + "/health")
    rows.append(
        _row(
            "teamflow_health",
            PASS if ok else WARN,
            detail,
            "" if ok else "被测目标 TeamFlow 未启动",
        )
    )
    return rows


def check_queue(api_url: str) -> dict[str, Any]:
    """Worker 状态：只读 API 的队列统计（不需要数据库写权限）。"""
    url = api_url.rstrip("/") + "/api/v1/queue/stats"
    try:
        with urllib.request.urlopen(url, timeout=3) as resp:  # noqa: S310
            payload = json.loads(resp.read().decode() or "{}")
    except Exception as exc:  # noqa: BLE001
        return _row(
            "worker_queue", WARN, f"{type(exc).__name__}: {exc}"[:160], "API 未启动或未授权"
        )
    queued = payload.get("queued")
    inflight = payload.get("inflight") or payload.get("running")
    return _row("worker_queue", PASS, f"queued={queued} inflight={inflight}")


def check_egress_policy() -> dict[str, Any]:
    """出站策略：只检查默认配置是否收敛（不访问外网）。"""
    from traceguard_core.config import get_settings

    settings = get_settings()
    allowed = list(settings.target_extra_origins or [])
    if len(allowed) > 20:
        return _row(
            "egress_policy",
            WARN,
            f"显式放行 {len(allowed)} 个 origin，请复核",
            "收紧 target_extra_origins",
        )
    return _row(
        "egress_policy",
        PASS,
        f"默认目标 {settings.teamflow_base_url}；额外放行 {len(allowed)} 个 origin",
    )


def check_ports(api_url: str, teamflow_url: str) -> list[dict[str, Any]]:
    rows = []
    for label, url in (("api", api_url), ("teamflow", teamflow_url)):
        try:
            host = urllib.request.urlparse(url).hostname or "127.0.0.1"
            port = urllib.request.urlparse(url).port or 80
        except Exception:  # noqa: BLE001
            continue
        with socket.socket() as s:
            s.settimeout(0.5)
            busy = s.connect_ex((host, int(port))) == 0
        rows.append(
            _row(
                f"port_{label}",
                PASS if busy else WARN,
                f"{host}:{port} {'in use' if busy else 'free'}",
                "" if busy else "服务未监听（本地演示可接受）",
            )
        )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-url", default="http://127.0.0.1:8000")
    parser.add_argument("--teamflow-url", default="")
    parser.add_argument("--json", default="")
    parser.add_argument("--skip-browser", action="store_true")
    args = parser.parse_args()

    from traceguard_core.config import get_settings

    settings = get_settings()
    teamflow_url = args.teamflow_url or settings.teamflow_base_url

    rows: list[dict[str, Any]] = [
        check_python(),
        check_dependencies(),
        check_config(),
    ]
    if not args.skip_browser:
        rows.append(check_playwright())
    rows.extend([check_node(), check_frontend_build(), check_database(), check_artifact_dir()])
    rows.extend(check_services(args.api_url, teamflow_url))
    rows.append(check_queue(args.api_url))
    rows.append(check_egress_policy())
    rows.extend(check_ports(args.api_url, teamflow_url))

    counts = {
        status: sum(1 for r in rows if r["status"] == status)
        for status in (PASS, WARN, FAIL, BLOCKED)
    }
    for row in rows:
        print(f"[{row['status']:<7}] {row['check']:<22} {row['detail']}")
        if row["hint"]:
            print(f"{'':9}→ {row['hint']}")
    print(
        f"\nsummary: PASS={counts[PASS]} WARN={counts[WARN]} FAIL={counts[FAIL]} BLOCKED={counts[BLOCKED]}"
    )
    payload = {
        "run": "RUN-017 WP5.3",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "read_only": True,
        "checks": rows,
        "summary": counts,
        "exit_code": 1 if counts[FAIL] else 0,
    }
    if args.json:
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return 1 if counts[FAIL] else 0


if __name__ == "__main__":
    raise SystemExit(main())
