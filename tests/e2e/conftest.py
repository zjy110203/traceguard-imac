"""E2E fixture：启动真实 TeamFlow HTTP 服务（子进程）。

- 不 Mock：所有请求打到真实 uvicorn 服务与 SQLite。
- 端口 8011；会话结束自动终止。
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import urllib.request
from collections.abc import Generator

import pytest


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
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "traceguard_teamflow.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            "8011",
        ],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        if not _wait_health("http://127.0.0.1:8011/health"):
            pytest.fail("TeamFlow 服务未能启动")
        yield "http://127.0.0.1:8011"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:  # noqa: BLE001
            proc.kill()
