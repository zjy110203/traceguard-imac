"""统一 ID 生成：前缀 + 时间戳 + 随机，便于追溯与可读性。

格式：<prefix>_<ulid-like>，前缀如 intent/tcase/oracle/task/run/step/evt/evd/run_g 等。
"""

from __future__ import annotations

import secrets
import time
from datetime import UTC, datetime


def new_id(prefix: str) -> str:
    ts = int(time.time() * 1000)
    rand = secrets.token_hex(6)
    return f"{prefix}_{ts:013d}{rand}"


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
