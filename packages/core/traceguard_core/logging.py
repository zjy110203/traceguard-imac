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

#: 字符串形态必须与字典键使用同一套敏感词（RUN-008 WP6）：
#: 旧实现的字符串正则少了 cookie/access_key/private_key/credential，
#: 导致 `Cookie: session=xxx` 这类日志会把真实凭据原文写出。
_SENSITIVE_INLINE = re.compile(
    _SENSITIVE_KEY.pattern + r"\s*[=:]\s*[^\s&,\"']+",
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
        value = re.sub(_SENSITIVE_INLINE, "***REDACTED***", value)
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


_RESERVED_LOG_KEYS = {
    "message",
    "asctime",
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
    "taskName",
    "event",
}


class StructuredLogger:
    """结构化日志门面：`logger.info("event_name", key=value)`。

    标准库 Logger 不接受任意 kwarg，这里统一转成 `extra`，由 JsonFormatter 写入 JSON 行；
    同时保留 name/level/isEnabledFor 等常用属性，避免调用方感知差异。
    """

    __slots__ = ("_logger", "name")

    def __init__(self, name: str) -> None:
        self._logger = logging.getLogger(name)
        self.name = name

    def _emit(self, level: int, event: str, exc_info: bool = False, **fields: Any) -> None:
        if not self._logger.isEnabledFor(level):
            return
        clean: dict[str, Any] = {}
        for k, v in fields.items():
            key = k if k not in _RESERVED_LOG_KEYS else f"ctx_{k}"
            clean[key] = redact(v)
        self._logger.log(level, event, exc_info=exc_info, extra=clean, stacklevel=3)

    def debug(self, event: str, **fields: Any) -> None:
        self._emit(logging.DEBUG, event, **fields)

    def info(self, event: str, **fields: Any) -> None:
        self._emit(logging.INFO, event, **fields)

    def warning(self, event: str, **fields: Any) -> None:
        self._emit(logging.WARNING, event, **fields)

    warn = warning

    def error(self, event: str, **fields: Any) -> None:
        self._emit(logging.ERROR, event, **fields)

    def exception(self, event: str, **fields: Any) -> None:
        self._emit(logging.ERROR, event, exc_info=True, **fields)

    def critical(self, event: str, **fields: Any) -> None:
        self._emit(logging.CRITICAL, event, **fields)

    def isEnabledFor(self, level: int) -> bool:  # noqa: N802 - 兼容 logging API
        return self._logger.isEnabledFor(level)

    def setLevel(self, level: int | str) -> None:  # noqa: N802 - 兼容 logging API
        self._logger.setLevel(level)


def get_logger(name: str) -> StructuredLogger:
    return StructuredLogger(name)
