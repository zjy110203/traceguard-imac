"""pytest 全局 fixture。确保测试不依赖真实凭据与生产数据库。"""

from __future__ import annotations

from pathlib import Path

import pytest


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


@pytest.fixture(scope="module")
def work_tmp():  # type: ignore[no-untyped-def]
    """项目内临时目录（沙箱内 /var/folders 可能不可写）。"""
    import uuid
    from pathlib import Path

    base = Path(".traceguard/test-tmp") / uuid.uuid4().hex[:8]
    base.mkdir(parents=True, exist_ok=True)
    return base
