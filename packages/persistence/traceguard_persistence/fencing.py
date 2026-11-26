"""Run 租约守卫（Fencing Guard）：把执行代次绑定到持久化写入。

设计要点（RUN-014 WP2）：

- 租约是**有期限的执行权**：`lease_expires_at <= now` 即视为失效，
  不以"尚未有新 Worker 接管"为由来证明旧 Worker 仍然合法；
- 守卫不是"再补一次 SELECT"：它在**写入所在的同一事务内**对 `tg_run` 行做
  条件空更新（CAS 语义）。命中即持有该行的行锁，接管方必须等本事务提交；
  未命中则调用方必须放弃整笔写入（fail-closed）；
- Step / Evidence / Event 的写入都复用同一守卫，保证
  "租约过期后旧 Worker 不能写入任何业务结果"这条不变量只有一处实现。
"""

from __future__ import annotations

import time
from typing import Any

from sqlalchemy import exists, select, update
from sqlalchemy.orm import Session

from .models import RunRow, RunSubmissionRow

#: 不允许再写入业务结果的执行状态（终态 + 收尾中间态）
WRITE_BLOCKED_STATES = ("COMPLETED", "FAILED", "TIMED_OUT", "CANCELLED", "FINALIZING")


def fenced_guard(
    session: Session,
    *,
    run_id: str,
    worker_id: str,
    fencing_token: int,
    now: float | None = None,
    allow_own_submission: bool = False,
) -> bool:
    """在本事务内校验并锁定执行代次；返回 False 表示租约已失效。

    生成的 SQL 等价于：

        UPDATE tg_run SET fencing_token = fencing_token
         WHERE run_id = :run_id AND lease_owner = :worker_id
           AND fencing_token = :token AND lease_expires_at > :now
           AND execution_status NOT IN ('COMPLETED', ...)

    不改变任何业务字段（自赋值），只用于取得行锁 + 原子条件判定。

    `allow_own_submission=True`：允许"同一 worker + 同一代次已经提交过终态"的
    收尾写入（典型场景：`run.completed` 事件必须在 submit_result 之后发出）。
    该分支只认 `tg_run_submission` 里 (run_id, worker_id, fencing_token) 完全一致
    的终态回执——旧 Worker 的代次早于接管者，永远命不中。
    """
    if allow_own_submission:
        own = session.scalar(
            select(
                exists().where(
                    RunSubmissionRow.run_id == run_id,
                    RunSubmissionRow.worker_id == worker_id,
                    RunSubmissionRow.fencing_token == int(fencing_token),
                    RunSubmissionRow.execution_status.in_(
                        ("COMPLETED", "FAILED", "TIMED_OUT", "CANCELLED")
                    ),
                )
            )
        )
        if own:
            # 仍要确认代次没有被别人推进（防止"提交后又被接管"的极端情况）
            current = session.scalar(select(RunRow.fencing_token).where(RunRow.run_id == run_id))
            return bool(current is not None and int(current) == int(fencing_token))
    result = session.execute(
        update(RunRow)
        .where(
            RunRow.run_id == run_id,
            RunRow.lease_owner == worker_id,
            RunRow.fencing_token == int(fencing_token),
            RunRow.lease_expires_at > (time.time() if now is None else now),
            RunRow.execution_status.notin_(WRITE_BLOCKED_STATES),
        )
        .values(fencing_token=RunRow.fencing_token)
    )
    return result.rowcount == 1


def fence_ref(fence: Any) -> tuple[str, int, str]:
    """把外部传入的 fence 归一化为 (run_id, token, worker_id)。

    支持 `(worker_id, fencing_token)` 二元组（TaskService 接口）与
    `(run_id, worker_id, fencing_token)` 三元组。
    """
    if isinstance(fence, dict):
        return (
            str(fence.get("run_id") or ""),
            int(fence.get("fencing_token") or 0),
            str(fence.get("worker_id") or ""),
        )
    seq = tuple(fence)
    if len(seq) == 3:
        return str(seq[0]), int(seq[2]), str(seq[1])
    return "", int(seq[1]), str(seq[0])


__all__ = ["WRITE_BLOCKED_STATES", "fence_ref", "fenced_guard"]
