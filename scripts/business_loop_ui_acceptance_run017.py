"""RUN-017 WP6：独立 Worker 部署下的真实 UI 全链路验收。

与 RUN-016 同一条真实链路（Intent → … → Rollback），差异：

- 输出到 `docs/assets/run017/wp5/ui`，标签 `run017-ui-full-loop`；
- 强制 `TG_INLINE_WORKER=false` + 独立 Worker 进程（生产部署形态）；
- 覆盖 RUN-017 新增能力：Regression INCONCLUSIVE 新 Attempt、Attempt 历史查看；
- 失败必须真实失败（退出码非 0），不吞异常。

两种 profile：

- `full`（默认）：业务闭环 + **独立 Worker 故障注入段**（SIGTERM/SIGKILL/租约接管/
  旧 Worker 恢复/API 重启/双 Worker 并发）。耗时较长且对 CPU 敏感，适合
  人工触发的 Reliability Gate 与本地验收。
- `core`：只跑业务闭环（含证据图/探针/回归验证三段扩展校验），去掉最耗时的故障段，
  适合每日 Nightly 的 CI runner（2–4 vCPU 上故障段容易触发等待超时）。

RUN-018 WP1：CI 通过环境变量指定本次运行专属输出目录与 run-id，
Job Summary 只读取本次报告，不回退历史报告：

    TG_UI_ACCEPTANCE_OUT=docs/assets/run018/ci/<run_id>/ui \
    TG_UI_ACCEPTANCE_RUN_ID=<run_id> \
    .venv/bin/python scripts/business_loop_ui_acceptance_run017.py --profile core

用法：
    .venv/bin/python scripts/business_loop_ui_acceptance_run017.py --profile full
    .venv/bin/python scripts/business_loop_ui_acceptance_run017.py --profile core
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from business_loop_ui_acceptance_run012 import main as run_loop  # noqa: E402

PROFILE = "full"
if "--profile" in sys.argv:
    index = sys.argv.index("--profile")
    if index + 1 < len(sys.argv):
        PROFILE = sys.argv[index + 1]
        del sys.argv[index : index + 2]

# RUN-018 WP1：输出目录与 run-id 支持环境变量覆盖（CI 每次 run 使用唯一目录）。
OUT_DIR = os.environ.get("TG_UI_ACCEPTANCE_OUT", "docs/assets/run017/wp5/ui")
RUN_ID = os.environ.get("TG_UI_ACCEPTANCE_RUN_ID", "")

if __name__ == "__main__":
    args = [
        "--out",
        OUT_DIR,
        "--workpackage",
        "RUN-017 WP6",
        "--label",
        f"run017-ui-{PROFILE}",
        "--independent-workers",
        "2",
        "--lease-ttl",
        "6",
        "--extended",
    ]
    if RUN_ID:
        args += ["--run-id", RUN_ID]
    args += ["--profile", PROFILE]
    if PROFILE == "full":
        args.append("--faults")
    sys.exit(run_loop(args))
