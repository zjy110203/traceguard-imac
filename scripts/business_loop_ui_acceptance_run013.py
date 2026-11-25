"""RUN-013 WP1：独立 Worker 部署形态下的真实 UI 全链路验收。

复用 RUN-012 的真实业务闭环脚本，但强制：
- `TG_INLINE_WORKER=false`：API 不启动内嵌 Worker；
- 独立 `traceguard_worker.main` 进程消费队列；
- 真实租约 / Fencing Token / SIGKILL / SIGSTOP 接管；
- API 重启与双 Worker 并发幂等。

用法：
    .venv/bin/python scripts/business_loop_ui_acceptance_run013.py

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
                "docs/assets/run013/wp1/ui",
                "--workpackage",
                "RUN-013 WP1",
                "--label",
                "independent-worker-acceptance",
                "--independent-workers",
                "2",
                "--lease-ttl",
                "6",
                "--faults",
            ]
        )
    )
