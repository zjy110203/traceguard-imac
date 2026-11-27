"""CI pytest 看门狗：超时把"卡住"变成可诊断的失败，而不是拖到 Job 超时。

背景：RUN-019-R2 Nightly #66 的 `Full backend suite (SQLite)` 在 CI 上挂起
（本地 7 分钟、CI 正常 6 分钟），45 分钟 Job 超时前没有任何测试名/栈信息，
无法定位。GitHub 匿名访问拿不到 job log，只能靠可公开读取的 Job Summary。

做法：
- 同进程调用 `pytest.main()`，用 `faulthandler.dump_traceback_later()` 在
  `--timeout` 秒后把所有线程栈写入 `$GITHUB_STEP_SUMMARY` 与 stderr；
- 到达 `--hard-timeout` 仍未结束时强制退出码 124（失败），不伪装通过；
- 测试正常结束时取消定时器，退出码沿用 pytest 的结果。

用法：
    python scripts/ci_pytest_watchdog.py --timeout 900 --hard-timeout 2100 -- -q
退出码：pytest 结果；超时强制 124。
"""

from __future__ import annotations

import argparse
import faulthandler
import os
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _append_summary(text: str) -> None:
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary:
        return
    try:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(text)
    except OSError:
        pass


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeout", type=int, default=900, help="打印栈回溯的秒数")
    ap.add_argument("--hard-timeout", type=int, default=2100, help="强制失败的秒数")
    ap.add_argument("pytest_args", nargs=argparse.REMAINDER)
    args = ap.parse_args(argv)
    pytest_args = [a for a in args.pytest_args if a != "--"] or ["-q"]

    dump_path = ROOT / ".pytest-tmp" / "ci-hang-traceback.txt"
    dump_path.parent.mkdir(parents=True, exist_ok=True)
    dump_file = open(dump_path, "w", encoding="utf-8")  # noqa: SIM115 - 进程生命周期内保持打开
    faulthandler.dump_traceback_later(args.timeout, repeat=False, file=dump_file)

    fired = threading.Event()

    def _hard_stop() -> None:
        fired.set()
        faulthandler.dump_traceback(file=dump_file)
        dump_file.flush()
        stacks = (
            dump_path.read_text(encoding="utf-8", errors="replace") if dump_path.exists() else ""
        )
        _append_summary(
            "\n### pytest 挂起：强制失败（hard timeout）\n\n"
            f"- timeout: {args.timeout}s / hard: {args.hard_timeout}s\n"
            f"- 线程栈（尾部 4000 字符）：\n\n```\n{stacks[-4000:]}\n```\n"
        )
        print(f"[watchdog] pytest exceeded {args.hard_timeout}s, aborting", file=sys.stderr)
        os._exit(124)

    timer = threading.Timer(args.hard_timeout, _hard_stop)
    timer.daemon = True
    timer.start()

    import pytest

    code = int(pytest.main(pytest_args))
    timer.cancel()
    faulthandler.cancel_dump_traceback_later()
    dump_file.close()
    if fired.is_set():  # pragma: no cover - 已被 _hard_stop 结束
        return 124
    return code


if __name__ == "__main__":
    raise SystemExit(main())
