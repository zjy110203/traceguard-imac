"""RUN-018 WP3：探针清理接口的真实 TeamFlow 集成测试。

与 tests/unit/test_probe_cleanup_security_run018.py（19 项纯校验/语义测试）互补，
本文件启动**真实 TeamFlow 进程**，通过 HTTP 走完整的创建 → 清理 → 复核链路：

1. 探针数据（bp:<token>: 前缀，probe workspace）真实创建后可被清理；
2. 非 probe workspace 未显式授权 → 400 拒绝（不删除任何数据）；
3. default 未授权 → 400；default + allow_default=true + 短 token → 400；
4. 非法字符 token（LIKE 通配符）→ 400，且**未删除任何数据**；
5. 清理只删本探针行：用户业务项目（default workspace）不受影响；
6. 重复清理幂等（deleted=0, ok=true），不伪报成功；
7. 返回值包含清理前后计数（before/deleted/after/remaining_matched）。

`/api/test/*` 端点只存在于 TeamFlow（被测目标应用），
TraceGuard 生产 API（apps/api）不挂载任何清理入口——由
`test_cleanup_endpoint_not_in_traceguard_api` 静态验证。
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PROBE_WORKSPACE = "tg-benchmark-probe"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _http(method: str, url: str, body: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"} if data is not None else {},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310 - 本地测试目标
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode() or "{}")
        except Exception:  # noqa: BLE001
            detail = {}
        return exc.code, detail


def _wait_health(url: str, timeout: float = 40.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:  # noqa: S310
                if resp.status == 200:
                    return True
        except Exception:  # noqa: BLE001
            time.sleep(0.3)
    return False


@pytest.fixture(scope="module")
def teamflow(tmp_path_factory):
    work = tmp_path_factory.mktemp("run018-wp3")
    port = _free_port()
    pythonpath = ":".join(
        str(ROOT / p)
        for p in (
            "packages/core",
            "packages/contracts",
            "packages/execution",
            "packages/agents",
            "packages/evidence",
            "packages/persistence",
            "apps/api",
            "apps/teamflow",
        )
    )
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", str(work)),
        "TMPDIR": os.environ.get("TMPDIR", "/tmp"),
        "PYTHONPATH": pythonpath,
        "TG_TEAMFLOW_DB": f"sqlite:///{work / 'teamflow.db'}",
    }
    proc = subprocess.Popen(  # noqa: S603 - 受控本地子进程
        [
            sys.executable,
            "-m",
            "uvicorn",
            "traceguard_teamflow.main:app",
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
    if not _wait_health(base + "/health"):
        proc.terminate()
        pytest.skip("TeamFlow 未能在本机启动")
    yield base
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:  # pragma: no cover
        proc.kill()


def _create_project(base: str, name: str, workspace: str) -> dict:
    status, data = _http(
        "POST",
        base + "/api/projects",
        {"name": name, "description": "wp3-cleanup-test", "workspace_slug": workspace},
    )
    assert status in (200, 201), (status, data)
    return data


def _cleanup(
    base: str, token: str, workspace: str, allow_default: bool = False
) -> tuple[int, dict]:
    return _http(
        "POST",
        base + "/api/test/probe-cleanup",
        {"token": token, "workspace": workspace, "allow_default": allow_default},
    )


def test_probe_data_created_and_cleaned(teamflow) -> None:  # type: ignore[no-untyped-def]
    base = teamflow
    token = "a1b2c3d4e5f6"
    _create_project(base, f"bp:{token}:scenario-A", PROBE_WORKSPACE)
    _create_project(base, f"bp:{token}:scenario-B", PROBE_WORKSPACE)
    status, result = _cleanup(base, token, PROBE_WORKSPACE)
    assert status == 200, result
    assert result["ok"] is True
    assert result["deleted"] == 2
    assert result["remaining_matched"] == 0
    assert result["before"] >= 2 and result["after"] == result["before"] - 2
    # 复核：探针项目确实消失
    _, state = _http("GET", base + f"/api/state/projects?workspace={PROBE_WORKSPACE}")
    names = [str(p.get("name")) for p in state.get("projects", [])]
    assert not any(n.startswith(f"bp:{token}:") for n in names)


def test_cleanup_is_idempotent(teamflow) -> None:  # type: ignore[no-untyped-def]
    base = teamflow
    token = "feedface1234"
    _create_project(base, f"bp:{token}:once", PROBE_WORKSPACE)
    status1, r1 = _cleanup(base, token, PROBE_WORKSPACE)
    assert status1 == 200 and r1["ok"] is True and r1["deleted"] == 1
    status2, r2 = _cleanup(base, token, PROBE_WORKSPACE)
    assert status2 == 200 and r2["ok"] is True and r2["deleted"] == 0
    assert r2["remaining_matched"] == 0


def test_cleanup_rejects_unauthorized_workspace(teamflow) -> None:  # type: ignore[no-untyped-def]
    base = teamflow
    _create_project(base, "bp:0ddba1122334:x", "default")
    status, detail = _cleanup(base, "0ddba1122334", "default", allow_default=False)
    assert status == 400, detail
    # 数据未被删除（拒绝时不做部分删除）
    _, state = _http("GET", base + "/api/state/projects")
    names = [str(p.get("name")) for p in state.get("projects", [])]
    assert any("bp:0ddba1122334" in n for n in names), f"拒绝清理后数据应保留: {names}"


def test_cleanup_default_requires_long_token(teamflow) -> None:  # type: ignore[no-untyped-def]
    base = teamflow
    _create_project(base, "bp:abc1234def5:x", "default")
    # <12 位 token 即使显式授权也拒绝
    status, detail = _cleanup(base, "abc1234def5", "default", allow_default=True)
    assert status == 400, detail
    _, state = _http("GET", base + "/api/state/projects")
    names = [str(p.get("name")) for p in state.get("projects", [])]
    assert any("bp:abc1234def5" in n for n in names)


def test_cleanup_rejects_like_wildcard_token(teamflow) -> None:  # type: ignore[no-untyped-def]
    base = teamflow
    _create_project(base, "bp:cafe11111111:x", PROBE_WORKSPACE)
    _create_project(base, "bp:cafe22222222:x", PROBE_WORKSPACE)
    # `%` 通配符意图匹配全部探针行 → 必须被 400 拒绝且不删除任何数据
    status, detail = _cleanup(base, "deadbeef%", PROBE_WORKSPACE)
    assert status == 400, detail
    status, detail = _cleanup(base, "deadbeef_", PROBE_WORKSPACE)
    assert status == 400, detail
    _, state = _http("GET", base + f"/api/state/projects?workspace={PROBE_WORKSPACE}")
    names = [str(p.get("name")) for p in state.get("projects", [])]
    assert sum("bp:cafe" in n for n in names) == 2, "通配符 token 被拒绝后数据应完整保留"


def test_cleanup_scoped_to_token_prefix(teamflow) -> None:  # type: ignore[no-untyped-def]
    base = teamflow
    token_a = "aaaa11111111"
    token_b = "bbbb22222222"
    _create_project(base, f"bp:{token_a}:mine", PROBE_WORKSPACE)
    _create_project(base, f"bp:{token_b}:other", PROBE_WORKSPACE)
    status, result = _cleanup(base, token_a, PROBE_WORKSPACE)
    assert status == 200 and result["deleted"] == 1
    _, state = _http("GET", base + f"/api/state/projects?workspace={PROBE_WORKSPACE}")
    names = [str(p.get("name")) for p in state.get("projects", [])]
    assert any(f"bp:{token_b}:" in n for n in names), "其他探针（不同 token）的数据不能被误删"


def test_user_business_data_survives_probe_cleanup(teamflow) -> None:  # type: ignore[no-untyped-def]
    """探针清理（probe workspace）绝不触及 default workspace 的业务数据。"""
    base = teamflow
    user_name = f"wp3-user-project-{time.time_ns()}"
    _create_project(base, user_name, "default")
    token = "fedcba987654"
    _create_project(base, f"bp:{token}:probe-row", PROBE_WORKSPACE)
    status, result = _cleanup(base, token, PROBE_WORKSPACE)
    assert status == 200 and result["ok"] is True
    _, state = _http("GET", base + "/api/state/projects")
    names = [str(p.get("name")) for p in state.get("projects", [])]
    assert user_name in names, "业务数据必须完好无损"


def test_cleanup_endpoint_not_in_traceguard_api() -> None:
    """静态验证：清理入口只存在于 TeamFlow（被测目标），TraceGuard 生产 API 无此路由。"""
    api_dir = ROOT / "apps" / "api"
    hits = []
    for path in api_dir.rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="replace")
        if "probe-cleanup" in text or "probe_cleanup" in text:
            hits.append(str(path))
    assert not hits, f"TraceGuard 生产 API 不应挂载探针清理入口: {hits}"
