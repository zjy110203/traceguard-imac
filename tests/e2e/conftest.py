"""E2E fixture：启动真实 TeamFlow HTTP 服务（子进程）。

- 不 Mock：所有请求打到真实 uvicorn 服务与 SQLite。
- RUN-011-R1 WP6：端口改为**动态空闲端口**（原固定 8011 在并行 pytest 进程下
  互相抢绑/串话，是 OPEN-RISK-04 用例互扰的来源之一）；TeamFlow 库由根
  conftest 按进程隔离（TG_TEAMFLOW_DB）。
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.request
from collections.abc import Generator

import pytest


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _wait_health(url: str, timeout: float = 20.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if r.status == 200:
                    return True
        except Exception:  # noqa: BLE001
            time.sleep(0.3)
    return False


@pytest.fixture(scope="session")
def teamflow_server() -> Generator[str, None, None]:
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [
            os.path.abspath("apps/api"),
            os.path.abspath("packages/core"),
            os.path.abspath("apps/teamflow"),
        ]
    )
    port = _free_port()
    proc = subprocess.Popen(
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
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        base = f"http://127.0.0.1:{port}"
        if not _wait_health(f"{base}/health"):
            pytest.fail("TeamFlow 服务未能启动")
        yield base
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:  # noqa: BLE001
            proc.kill()
