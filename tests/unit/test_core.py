"""核心基础单元测试：配置、脱敏、错误、ID。"""

from __future__ import annotations

import logging

from traceguard_core.config import Settings, database_path_from_url
from traceguard_core.errors import AppError, ErrorCode
from traceguard_core.ids import new_id, utc_now_iso
from traceguard_core.logging import redact


def test_settings_defaults_and_public_redaction() -> None:
    s = Settings(_env_file=None)
    assert s.app_name == "TraceGuard"
    assert s.database_url.startswith("sqlite:")
    pub = s.public_dict()
    assert "database_url" in pub


def test_settings_public_dict_redacts_secrets() -> None:
    s = Settings(_env_file=None, database_url="postgresql://u:supersecret@localhost/db")
    pub = s.public_dict()
    assert "supersecret" not in str(pub)


def test_database_path_from_url() -> None:
    assert database_path_from_url("sqlite:///./.traceguard/t.db") == "./.traceguard/t.db"
    assert database_path_from_url("postgresql://u@localhost/db") is None


def test_redact_dict_and_string() -> None:
    out = redact({"password": "hunter2", "token": "abc", "safe": "ok", "nested": {"api_key": "k"}})
    assert out["password"] == "***REDACTED***"
    assert out["token"] == "***REDACTED***"
    assert out["nested"]["api_key"] == "***REDACTED***"
    assert out["safe"] == "ok"
    s = redact("GET /x?token=abcdef&q=1")
    assert "abcdef" not in s
    # RUN-008 WP6：字符串脱敏必须与字典键覆盖同一组敏感词。
    for line in (
        "Cookie: session=supersecretcookie",
        "access_key=AKIAEXAMPLE123",
        "private_key=pem_material_here",
        "credential=bearer_value_here",
    ):
        red = redact(line)
        assert "supersecretcookie" not in red
        assert "AKIAEXAMPLE123" not in red
        assert "pem_material_here" not in red
        assert "bearer_value_here" not in red


def test_redacting_log_filter() -> None:
    from traceguard_core.logging import RedactingFilter

    rec = logging.LogRecord("t", logging.INFO, __file__, 1, "login password=secret123", (), None)
    RedactingFilter().filter(rec)
    assert "secret123" not in rec.msg


def test_error_status_mapping() -> None:
    e = AppError(ErrorCode.CONFLICT, "dup")
    assert e.http_status == 409
    assert AppError(ErrorCode.RESOURCE_UNAVAILABLE, "x").http_status == 503


def test_ids_and_utc_iso() -> None:
    i = new_id("run")
    assert i.startswith("run_")
    assert utc_now_iso().endswith("Z")
