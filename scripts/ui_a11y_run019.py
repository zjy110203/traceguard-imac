#!/usr/bin/env python3
"""RUN-019 WP3：在 RUN-018 无障碍脚本基础上补齐 Monitoring 与新增管理视图。

复用 ui_a11y_run018 的真实 Chromium + axe-core 验收流程，只扩展 VIEWS：
- monitoring（RUN-018 未覆盖的第 16 个业务视图）
- oracle_detail（RUN-019 新增）
- workspaces（RUN-019 新增，认证关闭时可能被路由守卫重定向，仍如实记录）

证据：docs/assets/run019/wp3/
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import ui_a11y_run018 as a11y  # noqa: E402
from ui_matrix_run017 import VIEWS as BASE_VIEWS  # noqa: E402

RUN019_VIEWS = BASE_VIEWS + (
    ("monitoring", "/monitoring"),
    ("oracle_detail", "/oracles/oracle_tf_ui_banner"),
    ("workspaces", "/workspaces"),
)


def main() -> int:
    a11y.VIEWS = RUN019_VIEWS
    argv = sys.argv[1:]
    if "--out" not in argv:
        argv = ["--out", "docs/assets/run019/wp3", *argv]
    sys.argv = [sys.argv[0], *argv]
    return a11y.main()


if __name__ == "__main__":
    raise SystemExit(main())
