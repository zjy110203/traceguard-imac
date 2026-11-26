"""RUN-017 WP6：独立 Worker 部署下的真实 UI 全链路验收。

与 RUN-016 同一条真实链路（Intent → … → Rollback），差异：

- 输出到 `docs/assets/run017/wp5/ui`，标签 `run017-ui-full-loop`；
- 强制 `TG_INLINE_WORKER=false` + 2 个独立 Worker（生产部署形态）；
- 覆盖 RUN-017 新增能力：Regression INCONCLUSIVE 新 Attempt、Attempt 历史查看；
- 失败必须真实失败（退出码非 0），不吞异常。

用法：
    .venv/bin/python scripts/business_loop_ui_acceptance_run017.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from business_loop_ui_acceptance_run012 import main as run_loop  # noqa: E402

if __name__ == "__main__":
    sys.exit(
        run_loop(
            [
                "--out",
                "docs/assets/run017/wp5/ui",
                "--workpackage",
                "RUN-017 WP6",
                "--label",
                "run017-ui-full-loop",
                "--independent-workers",
                "2",
                "--lease-ttl",
                "6",
                "--faults",
                "--extended",
            ]
        )
    )
