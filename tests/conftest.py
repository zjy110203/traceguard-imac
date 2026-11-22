"""pytest 全局 fixture。确保测试不依赖真实凭据与生产数据库。"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """测试环境强制使用内存/临时 SQLite，绝不连生产库。"""
    monkeypatch.setenv("TG_ENVIRONMENT", "test")
    monkeypatch.setenv("TG_DATABASE_URL", "sqlite:///:memory:")


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
