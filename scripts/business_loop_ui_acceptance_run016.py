"""RUN-016 WP4.3：独立 Worker 部署下的真实 UI 全链路验收。

复用 RUN-012/013 的真实业务闭环脚本，强制：
- `TG_INLINE_WORKER=false`：API 不启动内嵌 Worker；
- 独立 `traceguard_worker.main` 进程消费队列（2 个）；
- 真实租约 / Fencing Token / SIGKILL / SIGSTOP 接管；
- 证据与报告标记为 RUN-016（不再沿用 RUN-013/014 的 workpackage 标签）。

用法：
    .venv/bin/python scripts/business_loop_ui_acceptance_run016.py

退出码：0 全部通过；1 有失败；3 环境不可达。
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
                "docs/assets/run016/wp4/ui",
                "--workpackage",
                "RUN-016 WP4",
                "--label",
                "run016-ui-full-loop",
                "--independent-workers",
                "2",
                "--lease-ttl",
                "6",
                "--faults",
                "--extended",
            ]
        )
    )
