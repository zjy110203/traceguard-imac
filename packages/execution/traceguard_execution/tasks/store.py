"""执行域表结构导出（表定义已统一到 `traceguard_persistence.models`）。

保留该模块是为了兼容既有导入路径；所有表都挂在同一个 Database 上。
"""

from __future__ import annotations

from traceguard_persistence.db import get_database
from traceguard_persistence.models import RunRow, StepRow, TaskRow

__all__ = ["RunRow", "StepRow", "TaskRow", "init_exec_db"]


def init_exec_db() -> None:
    get_database().create_schema()
