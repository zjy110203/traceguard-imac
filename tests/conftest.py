"""pytest 全局 fixture。确保测试不依赖真实凭据与生产数据库。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest


def pytest_configure(config: pytest.Config) -> None:
    """RUN-011-R1 WP6（OPEN-RISK-04 根因修复）：并行进程隔离。

    根因 A（错误级联）：`--basetemp=.pytest-tmp/run` 是固定路径；多个 pytest
    进程并行时（真实复现：3 并发全量 → 每进程 7-8 errors），pytest 在会话开始
    会**清空 basetemp**——进程 A 删掉进程 B 正在使用的临时目录 →
    FileNotFoundError / OSError 级联。修复：进程启动时 basetemp 后缀 PID
    （xdist worker 场景用 worker_id），并行进程各自持有独立目录。

    根因 B（用例互扰）：TeamFlow 默认库 `sqlite:///./.traceguard/teamflow.db`
    是固定共享文件（TeamFlow models 在 import 时读 `TG_TEAMFLOW_DB`）——
    并行进程并发 seed 触发 `UNIQUE constraint failed: tf_workspace.slug`，
    ASGI 异常使浏览器断言连锁失败。修复：进程级隔离 TeamFlow 库
    （`.traceguard/test-tmp/teamflow-e2e-<pid>.db`），必须在任何
    `traceguard_teamflow` import 之前设置。
    """
    bt = getattr(config.option, "basetemp", None)
    if bt:
        bt_path = str(bt)
        if bt_path.startswith(".pytest-tmp"):
            worker = os.getenv("PYTEST_XDIST_WORKER", "")
            suffix = worker if worker else f"p{os.getpid()}"
            if not bt_path.rstrip("/").endswith(suffix):
                config.option.basetemp = f"{bt_path.rstrip('/')}-{suffix}"
    # 根因 B：TeamFlow 测试库按进程隔离（import 前设置，models 模块级读取）
    if not os.environ.get("TG_TEAMFLOW_DB"):
        Path(".traceguard/test-tmp").mkdir(parents=True, exist_ok=True)
        os.environ["TG_TEAMFLOW_DB"] = (
            f"sqlite:///./.traceguard/test-tmp/teamflow-e2e-{os.getpid()}.db"
        )


@pytest.fixture(scope="session", autouse=True)
def _ensure_basetemp_parent() -> None:
    """CI 全新 checkout 时 --basetemp 的父目录不存在，pytest 的 tmp_path 会 mkdir 失败。"""
    Path(".pytest-tmp").mkdir(parents=True, exist_ok=True)


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """测试环境强制使用内存/临时 SQLite，绝不连生产库。

    LLM 在测试中默认禁用（防止读取 .env 真实 Key 产生真实 API 调用）；
    LLM 专项测试自行打开开关并使用 FakeProvider / MockTransport。
    同时重置 Settings 缓存，确保任何提前建立的 lru_cache 不泄露真实 .env 配置。
    """
    monkeypatch.setenv("TG_ENVIRONMENT", "test")
    monkeypatch.setenv("TG_DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("TG_LLM_PROVIDER", "")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    from traceguard_core.config import reset_settings_cache

    reset_settings_cache()
    yield  # noqa: PT022
    reset_settings_cache()


@pytest.fixture
def tmp_traceguard_dir(tmp_path):  # type: ignore[no-untyped-def]
    """项目内隔离临时目录，用于证据/附件测试。"""
    d = tmp_path / "traceguard-test"
    d.mkdir()
    return d


@pytest.fixture
def ensure_testcase():  # type: ignore[no-untyped-def]
    """RUN-011-R1.1 WP1：为 Task/Run 血缘校验准备真实 TestCaseVersion 行。

    `create_run()` 现在要求 `testcase_version_id` 指向数据库中真实存在的版本，
    且与 Task 的 testcase_ref / workspace 一致。测试不得再使用假版本字符串。
    """

    def _make(
        rt: Any,
        testcase_id: str,
        *,
        version: int = 1,
        workspace_id: str = "default",
        review_status: str = "DRAFT",
    ) -> str:
        from traceguard_core.ids import utc_now_iso

        catalog = getattr(rt, "catalog", rt)
        payload = {
            "testcase_id": testcase_id,
            "version": 1,
            "workspace_id": workspace_id,
            "created_at": utc_now_iso(),
            "steps": [
                {
                    "step_id": "s1",
                    "action": "navigate",
                    "target": {"strategy": "css", "value": "body"},
                }
            ],
            "assertions": [
                {
                    "assertion_id": "a1",
                    "description": "页面可见",
                    "kind": "ui",
                    "required": True,
                    "expected": {},
                }
            ],
        }
        for current in range(1, int(version) + 1):
            if catalog.get_version(testcase_id, current) is not None:
                continue
            catalog.create_testcase(
                payload,
                title=testcase_id,
                workspace_id=workspace_id,
                review_status=review_status,
            )
        return f"{testcase_id}@{int(version)}"

    return _make


@pytest.fixture(scope="module")
def work_tmp():  # type: ignore[no-untyped-def]
    """项目内临时目录（沙箱内 /var/folders 可能不可写）。"""
    import uuid
    from pathlib import Path

    base = Path(".traceguard/test-tmp") / uuid.uuid4().hex[:8]
    base.mkdir(parents=True, exist_ok=True)
    return base
