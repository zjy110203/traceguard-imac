"""统一配置：所有环境变量带 TG_ 前缀，避免与系统环境冲突。

安全基线：
- 不读取任何真实凭据文件；密钥只从环境变量注入。
- 敏感字段（secret/password/token/key）在 repr 中自动遮蔽。
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """全局运行配置。默认 SQLite，可平滑切换 PostgreSQL。"""

    model_config = SettingsConfigDict(
        env_prefix="TG_",
        env_file=".env",  # 允许项目内 .env（被 .gitignore 排除）
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # 应用
    app_name: str = "TraceGuard"
    environment: str = "local"  # local / ci / staging（不含 prod 凭据）
    log_level: str = "INFO"
    log_json: bool = True

    # 数据库：默认项目内 SQLite；PG 由 TG_DATABASE_URL 覆盖
    database_url: str = "sqlite:///./.traceguard/traceguard.db"
    db_echo: bool = False

    # API
    api_host: str = "127.0.0.1"
    api_port: int = 8000

    # 安全基线
    allowed_origins: list[str] = Field(default_factory=lambda: ["http://127.0.0.1:5173"])
    max_upload_bytes: int = 10 * 1024 * 1024

    # TeamFlow（阶段1）
    teamflow_base_url: str = "http://127.0.0.1:8001"

    # 浏览器执行
    browser_headless: bool = True
    browser_channel: str = ""  # "" = playwright 自带 chromium；msedge / chrome
    browser_timeout_ms: int = 8000
    run_max_duration_s: int = 180

    # 证据与产物
    artifact_dir: str = ".traceguard/artifacts"
    artifact_backend: str = "local"  # local / s3（S3 需 boto3 与 TG_S3_* 配置）

    # Worker
    worker_id: str = ""  # 空则自动生成 host-pid
    worker_poll_interval_s: float = 1.0
    worker_lease_ttl_s: float = 120.0
    inline_worker: bool = True  # 单机模式：API 内嵌后台执行器（生产可关掉并独立部署 Worker）

    @field_validator("log_level")
    @classmethod
    def _upper_level(cls, v: str) -> str:
        return v.upper()

    @field_validator("database_url")
    @classmethod
    def _reject_embedded_password(cls, v: str) -> str:
        """允许带口令的 DSN，但口令只来自环境变量，不允许日志打印（见安全基线测试）。"""
        return v

    def public_dict(self) -> dict[str, Any]:
        """可安全写入日志/报告的配置（敏感字段遮蔽）。

        除了按字段名遮蔽，还对 DSN 值中形如 scheme://user:password@host 的口令遮蔽，
        防止 PostgreSQL/Redis DSN 里的内联凭据进入日志。
        """
        import re

        sensitive = ("password", "secret", "token", "key", "credential")
        out: dict[str, Any] = {}
        for k, v in self.model_dump().items():
            if any(s in k.lower() for s in sensitive):
                out[k] = "***REDACTED***"
            elif isinstance(v, str):
                # 遮蔽 URL 内联口令：scheme://user:PASSWORD@host
                out[k] = re.sub(r"(?<=://)[^:/@\s]+:[^@\s]+@", "***:***@", v)
            else:
                out[k] = v
        return out


@lru_cache
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    """测试用：清空缓存以重新读取环境变量。"""
    get_settings.cache_clear()


def database_path_from_url(url: str) -> str | None:
    """若为 sqlite DSN，返回本地文件路径；否则返回 None。"""
    if url.startswith("sqlite:///"):
        return url[len("sqlite:///") :]
    return None


def ensure_sqlite_dir(url: str) -> None:
    """确保 SQLite 数据文件所在目录存在（仅本地项目目录内）。"""
    path = database_path_from_url(url)
    if path:
        os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
