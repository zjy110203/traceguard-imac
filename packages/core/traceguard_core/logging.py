"""结构化日志 + 脱敏过滤器（安全基线）。

规则：
- 输出 JSON 行，包含 ts/level/logger/event/... 字段。
- 任何日志消息与参数中的敏感键（password/token/secret/authorization/cookie/key）
  的值会被 ***REDACTED*** 替换，防止凭据入日志。
"""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from typing import Any

_SENSITIVE_KEY = re.compile(
    r"(pass(word|wd)?|secret|token|authorization|cookie|api[_-]?key|access[_-]?key|private[_-]?key|credential)",
    re.IGNORECASE,
)


def redact(value: Any) -> Any:
    """递归脱敏：对 dict 中敏感键遮蔽，对字符串中的 key=value 形态遮蔽。"""
    if isinstance(value, dict):
        return {
            k: ("***REDACTED***" if _SENSITIVE_KEY.search(str(k)) else redact(v))
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    if isinstance(value, str):
        # 遮蔽 query/日志里形如 password=xxx、token: xxx 的片段
        value = re.sub(
            r"(?i)(pass(word|wd)?|secret|token|authorization|api[_-]?key)\s*[=:]\s*[^\s&,\"']+",
            r"\1=***REDACTED***",
            value,
        )
        return value
    return value


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, (dict, list, tuple)):
            record.msg = redact(record.msg)
        else:
            record.msg = redact(str(record.msg))
        if record.args:
            if isinstance(record.args, dict):
                record.args = redact(record.args)
            else:
                record.args = tuple(redact(a) for a in record.args)
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if hasattr(record, "event"):
            payload["event"] = record.event
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        for k, v in record.__dict__.items():
            if k not in {
                "name",
                "msg",
                "args",
                "levelname",
                "levelno",
                "pathname",
                "filename",
                "module",
                "exc_info",
                "exc_text",
                "stack_info",
                "lineno",
                "funcName",
                "created",
                "msecs",
                "relativeCreated",
                "thread",
                "threadName",
                "processName",
                "process",
                "event",
                "taskName",
            }:
                payload[k] = redact(v)
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(level: str = "INFO", json_output: bool = True) -> None:
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level.upper())
    handler = logging.StreamHandler()
    handler.addFilter(RedactingFilter())
    handler.setFormatter(
        JsonFormatter()
        if json_output
        else logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    root.addHandler(handler)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
