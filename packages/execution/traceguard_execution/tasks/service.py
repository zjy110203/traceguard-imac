"""Task / Run / Step 服务：持久化 + 状态机 + 租约 fencing + 队列领取。

不变量（来自资料/06 与设计基线）：
- Task 是逻辑任务，Run 是执行尝试；重试产生新 Run，不覆盖历史；
- `execution_status` 与 `test_verdict` 独立；
- Run 有租约（lease_owner / lease_expires_at）与执行代次（fencing_token），旧代次提交被拒绝；
- 租约过期后 Run 可被重新领取，fencing_token 递增，旧 Worker 的心跳与提交都会失败。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session
from traceguard_core.errors import AppError, ErrorCode
from traceguard_core.ids import new_id
from traceguard_persistence.db import Database, get_database
from traceguard_persistence.fencing import WRITE_BLOCKED_STATES, fenced_guard
from traceguard_persistence.models import (
    RunRow,
    RunSubmissionRow,
    StepRow,
    TaskRow,
    TestCaseRow,
    TestCaseVersionRow,
)

TASK_TRANSITIONS: dict[str, set[str]] = {
    "CREATED": {"QUEUED", "CANCELLED"},
    "QUEUED": {"RUNNING", "CANCELLED"},
    "RUNNING": {"WAITING", "COMPLETED", "FAILED", "CANCELLED"},
    "WAITING": {"RUNNING", "CANCELLED"},
    "COMPLETED": set(),
    "FAILED": set(),
    "CANCELLED": set(),
}
RUN_TRANSITIONS: dict[str, set[str]] = {
    "QUEUED": {"PREPARING", "CANCELLED"},
    "PREPARING": {"RUNNING", "FAILED", "TIMED_OUT", "CANCELLED"},
    "RUNNING": {"FINALIZING", "FAILED", "TIMED_OUT", "CANCELLED"},
    "FINALIZING": {"COMPLETED", "FAILED"},
    "COMPLETED": set(),
    "FAILED": set(),
    "TIMED_OUT": set(),
    "CANCELLED": set(),
}
FINAL_RUN_STATES = {"COMPLETED", "FAILED", "TIMED_OUT", "CANCELLED"}


class LeaseHeartbeat:
    """执行期间的租约续期看护。

    - 每 ttl/3 续租一次；续租失败（被新 Worker 接管/任务被取消）即标记 lost；
    - Pipeline 在步骤边界检查 lost，主动停止，避免"僵尸执行"继续写证据；
    - 只续期，不改变 fencing token。
    """

    def __init__(
        self, service: TaskService, run_id: str, worker_id: str, fencing_token: int, ttl_s: float
    ) -> None:
        self.service = service
        self.run_id = run_id
        self.worker_id = worker_id
        self.fencing_token = fencing_token
        self.ttl_s = ttl_s
        self.lost = False
        self.renewals = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="tg-lease-heartbeat", daemon=True)

    def _loop(self) -> None:
        interval = max(0.2, self.ttl_s / 3)
        while not self._stop.wait(interval):
            try:
                ok = self.service.renew_lease(
                    self.run_id, self.worker_id, self.fencing_token, ttl_seconds=self.ttl_s
                )
            except Exception:  # noqa: BLE001
                ok = False
            if not ok:
                self.lost = True
                return
            self.renewals += 1

    def start(self) -> LeaseHeartbeat:
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=2)


class TaskService:
    def __init__(self, db: Database | None = None) -> None:
        self.db = db or get_database()
        self.db.create_schema()

    # ------------------------------------------------------------------ Task
    def create_task(
        self,
        testcase_ref: str,
        idempotency_key: str,
        application: str = "teamflow",
        *,
        testcase_version_id: str = "",
        mode: str = "playwright",
        workspace_id: str | None = None,
    ) -> dict[str, Any]:
        """创建 Task。

        RUN-011-R1.1 WP1：`workspace_id` **必须显式提供**（由服务端从 TestCase /
        TestCaseVersion 解析），不再有 `default` 默认值——旧默认值会让非 default
        用例的 Task 静默落入 default，破坏 Workspace 隔离。
        """
        ws = (workspace_id or "").strip()
        if not ws:
            raise AppError(
                ErrorCode.VALIDATION_ERROR,
                "WORKSPACE_REQUIRED: Task 必须显式指定 workspace_id"
                "（应继承 TestCase 的真实 Workspace，不得使用默认值）",
            )
        with self.db.session() as s:
            existing = s.scalar(select(TaskRow).where(TaskRow.idempotency_key == idempotency_key))
            if existing:
                if existing.workspace_id != ws:
                    raise AppError(
                        ErrorCode.CONFLICT,
                        f"IDEMPOTENCY_KEY_REUSED: 幂等键已绑定 workspace "
                        f"'{existing.workspace_id}'，与请求 '{ws}' 不一致",
                    )
                return {"task_id": existing.task_id, "idempotent_hit": True}
            task_id = new_id("task")
            s.add(
                TaskRow(
                    task_id=task_id,
                    workspace_id=ws,
                    testcase_ref=testcase_ref,
                    testcase_version_id=testcase_version_id,
                    application=application,
                    status="CREATED",
                    idempotency_key=idempotency_key,
                    mode=mode,
                )
            )
            return {"task_id": task_id, "idempotent_hit": False}

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        with self.db.session() as s:
            r = s.get(TaskRow, task_id)
            return self._task_dict(r) if r else None

    def transition_task(self, task_id: str, target: str) -> None:
        with self.db.session() as s:
            r = s.get(TaskRow, task_id)
            if not r:
                raise AppError(ErrorCode.NOT_FOUND, f"task {task_id} not found")
            if target != r.status and target not in TASK_TRANSITIONS.get(r.status, set()):
                raise AppError(ErrorCode.CONFLICT, f"illegal task transition {r.status}->{target}")
            r.status = target
            r.updated_at = time.time()

    def cancel_task(self, task_id: str) -> dict[str, Any]:
        with self.db.session() as s:
            r = s.get(TaskRow, task_id)
            if not r:
                raise AppError(ErrorCode.NOT_FOUND, f"task {task_id} not found")
            if r.status not in {"CREATED", "QUEUED", "RUNNING", "WAITING"}:
                raise AppError(ErrorCode.CONFLICT, f"task in final state {r.status}")
            r.status = "CANCELLED"
            r.updated_at = time.time()
            for run in s.scalars(
                select(RunRow).where(
                    RunRow.task_id == task_id, RunRow.execution_status.notin_(FINAL_RUN_STATES)
                )
            ).all():
                run.execution_status = "CANCELLED"
            return {"task_id": task_id, "status": "CANCELLED"}

    def list_tasks(
        self,
        workspace_id: str | None = None,
        status: str | None = None,
        limit: int = 200,
        *,
        workspace_ids: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        with self.db.session() as s:
            q = select(TaskRow)
            if workspace_id:
                q = q.where(TaskRow.workspace_id == workspace_id)
            if workspace_ids is not None:
                q = q.where(TaskRow.workspace_id.in_(list(workspace_ids)))
            if status:
                q = q.where(TaskRow.status == status)
            rows = s.scalars(q.order_by(TaskRow.created_at.desc()).limit(limit)).all()
            return [self._task_dict(r) for r in rows]

    @staticmethod
    def _task_dict(r: TaskRow) -> dict[str, Any]:
        return {
            "task_id": r.task_id,
            "workspace_id": r.workspace_id,
            "testcase_ref": r.testcase_ref,
            "testcase_version_id": r.testcase_version_id,
            "application": r.application,
            "status": r.status,
            "mode": r.mode,
            "idempotency_key": r.idempotency_key,
            "created_at": r.created_at,
        }

    # ------------------------------------------------------------------- Run
    def create_run(
        self,
        task_id: str,
        testcase_version_id: str,
        *,
        attempt: int | None = None,
        auto_queue: bool = True,
    ) -> dict[str, Any]:
        """每次调用产生新 Run（重试语义），不覆盖历史。

        RUN-011-R1.1 WP1 §1.2：Run 必须继承并**验证** Task 的 Workspace 与版本归属——
        1) `testcase_version_id` 必须指向数据库中真实存在的 TestCaseVersion；
        2) 该版本必须属于 Task.testcase_ref；
        3) 该版本的 Workspace 必须等于 Task.workspace_id（禁止跨 Workspace 创建 Run）。
        只校验字符串格式、不校验数据库归属一律拒绝。
        """
        with self.db.session() as s:
            task = s.get(TaskRow, task_id)
            if not task:
                raise AppError(ErrorCode.NOT_FOUND, f"task {task_id} not found")
            version_ref = (testcase_version_id or task.testcase_version_id or "").strip()
            if not version_ref:
                raise AppError(
                    ErrorCode.VALIDATION_ERROR,
                    "TESTCASE_VERSION_REQUIRED: Run 必须绑定具体 TestCase 版本",
                )
            self._assert_version_lineage(s, task, version_ref)
            prev = s.scalar(
                select(RunRow)
                .where(RunRow.task_id == task_id)
                .order_by(RunRow.attempt.desc())
                .limit(1)
            )
            next_attempt = attempt if attempt is not None else (prev.attempt + 1 if prev else 1)
            run_id = new_id("run")
            s.add(
                RunRow(
                    run_id=run_id,
                    task_id=task_id,
                    workspace_id=task.workspace_id,
                    attempt=next_attempt,
                    testcase_version_id=version_ref,
                    execution_status="QUEUED",
                    test_verdict="NOT_EVALUATED",
                    fencing_token=0,
                )
            )
            if auto_queue and task.status == "CREATED":
                task.status = "QUEUED"
                task.updated_at = time.time()
            return {
                "run_id": run_id,
                "attempt": next_attempt,
                "execution_status": "QUEUED",
                "observer_mode": task.mode,
            }

    @staticmethod
    def _assert_version_lineage(session: Any, task: TaskRow, version_ref: str) -> None:
        """校验 Run 的版本血缘：存在、同用例、同 Workspace。"""
        testcase_id, _, raw_version = version_ref.partition("@")
        testcase_id = testcase_id.strip()
        raw_version = raw_version.strip()
        if not testcase_id or not raw_version.isdigit():
            raise AppError(
                ErrorCode.VALIDATION_ERROR,
                f"INVALID_TESTCASE_VERSION_REF: {version_ref}（期望 形如 tcase_x@1）",
            )
        version = int(raw_version)
        version_row = session.get(TestCaseVersionRow, f"{testcase_id}@{version}")
        if version_row is None:
            raise AppError(
                ErrorCode.NOT_FOUND,
                f"TESTCASE_VERSION_NOT_FOUND: {version_ref}",
            )
        if version_row.testcase_id != task.testcase_ref:
            raise AppError(
                ErrorCode.CONFLICT,
                f"TESTCASE_VERSION_MISMATCH: 版本 {version_ref} 属于用例 "
                f"{version_row.testcase_id}，与 Task 的 {task.testcase_ref} 不一致",
            )
        if version_row.workspace_id != task.workspace_id:
            raise AppError(
                ErrorCode.AUTHORIZATION_ERROR,
                f"CROSS_WORKSPACE_RUN: 版本 {version_ref} 属于 workspace "
                f"'{version_row.workspace_id}'，与 Task 的 '{task.workspace_id}' 不一致",
            )
        tc_row = session.get(TestCaseRow, task.testcase_ref)
        if tc_row is not None and tc_row.workspace_id != task.workspace_id:
            raise AppError(
                ErrorCode.AUTHORIZATION_ERROR,
                f"CROSS_WORKSPACE_RUN: Task workspace '{task.workspace_id}' 与用例 "
                f"'{tc_row.workspace_id}' 不一致",
            )

    def acquire_lease(
        self, run_id: str, worker_id: str, ttl_seconds: float = 60.0
    ) -> dict[str, Any]:
        """获取执行租约：fencing_token 单调递增；仅在非终态且租约不活跃时可获取。"""
        with self.db.session() as s:
            r = s.get(RunRow, run_id)
            if not r:
                raise AppError(ErrorCode.NOT_FOUND, f"run {run_id} not found")
            now = time.time()
            lease_active = bool(r.lease_owner) and r.lease_expires_at > now
            if r.execution_status in FINAL_RUN_STATES or r.execution_status == "FINALIZING":
                raise AppError(ErrorCode.CONFLICT, "run not acquirable (final)")
            if lease_active:
                raise AppError(ErrorCode.CONFLICT, "run not acquirable (lease active)")
            r.fencing_token += 1
            r.lease_owner = worker_id
            r.lease_expires_at = now + ttl_seconds
            r.execution_status = "PREPARING"
            r.started_at = r.started_at or now
            task = s.get(TaskRow, r.task_id)
            if task and task.status in {"CREATED", "QUEUED"}:
                task.status = "RUNNING"
                task.updated_at = now
            return {
                "fencing_token": r.fencing_token,
                "lease_expires_at": r.lease_expires_at,
                "attempt": r.attempt,
                "workspace_id": r.workspace_id,
                "task_id": r.task_id,
                "ttl_s": ttl_seconds,
            }

    def renew_lease(
        self, run_id: str, worker_id: str, fencing_token: int, ttl_seconds: float = 60.0
    ) -> bool:
        """续租：**原子条件更新**，租约已过期则拒绝（fail-closed）。

        RUN-014 WP2：旧实现只比对 owner/token，不看 `lease_expires_at`——
        一个已经过期、但尚未被接管的旧 Worker 仍能续租成功，把"已经失效的执行权"
        复活，与 claim 的接管条件形成竞态。现在过期即不可续；
        续租语句本身也是条件 UPDATE，不存在"读到空闲 → 再写"的窗口。
        """
        now = time.time()
        with self.db.session() as s:
            result = s.execute(
                update(RunRow)
                .where(
                    RunRow.run_id == run_id,
                    RunRow.lease_owner == worker_id,
                    RunRow.fencing_token == int(fencing_token),
                    RunRow.lease_expires_at > now,
                    RunRow.execution_status.notin_(WRITE_BLOCKED_STATES),
                )
                .values(lease_expires_at=now + ttl_seconds)
            )
            return result.rowcount == 1

    def lease_valid(self, run_id: str, worker_id: str, fencing_token: int) -> bool:
        """同步租约校验（RUN-013 WP1 / RUN-014 WP2）。

        异步心跳只能"事后"发现租约丢失；步骤边界/写证据前必须再做一次同步校验，
        否则被 SIGSTOP 后恢复的旧 Worker 可能在被接管后继续写步骤与证据。

        RUN-014 WP2：必须检查 `lease_expires_at > now`。租约只代表"一段时间内的执行权"，
        过期即失效，**不能**以"还没有新 Worker 接管"为由来证明旧 Worker 仍然合法。
        """
        now = time.time()
        with self.db.session() as s:
            r = s.get(RunRow, run_id)
            if not r:
                return False
            return (
                r.lease_owner == worker_id
                and int(r.fencing_token) == int(fencing_token)
                and float(r.lease_expires_at or 0.0) > now
                and r.execution_status not in FINAL_RUN_STATES
                and r.execution_status != "FINALIZING"
            )

    @contextmanager
    def fenced_session(self, run_id: str, worker_id: str, fencing_token: int) -> Iterator[Session]:
        """租约守卫下的事务：**校验与写入在同一个事务内**（RUN-014 WP2）。

        实现方式：事务开始先对 run 行做一次"条件空更新"——
        `UPDATE tg_run SET fencing_token = fencing_token WHERE run_id=? AND
        lease_owner=? AND fencing_token=? AND lease_expires_at > now AND status NOT IN (...)`

        - 命中（rowcount=1）：本事务持有该 run 的行锁，接管方必须等本事务提交；
          在 PostgreSQL READ COMMITTED 下，等锁的接管语句会重新求值 WHERE，
          本事务若已续租/未退出，接管条件不再成立 → 不会发生"检查后写入被接管"。
        - 未命中：代次/属主/租期/状态任一不成立 → 立即 `LEASE_LOST`，不写任何业务数据。

        这样 fencing_token 真正约束了持久化写入，而不是"再补一次 SELECT"。
        """
        with self.db.session() as s:
            if not fenced_guard(s, run_id=run_id, worker_id=worker_id, fencing_token=fencing_token):
                raise AppError(
                    ErrorCode.CONFLICT,
                    "LEASE_LOST: 租约已过期/被接管，写入被拒绝（fencing guard）",
                )
            yield s

    def claim_next_run(self, worker_id: str, ttl_seconds: float = 120.0) -> dict[str, Any] | None:
        """Worker 领取：取最早排队的 Run；租约过期未提交的 Run 也会被回收领取。

        并发安全（RUN-003-E）：领取用单条条件 UPDATE 原子完成——
        只有在租约仍然空闲/过期的那一刻，条件 UPDATE 才会命中（rowcount=1）。
        两个进程同时竞争同一 Run 时，只有一个能成功，另一个换下一个候选，
        杜绝"双方都读到空闲租约 → 双重执行"的竞态（此前可触发 tg_step 双写冲突）。
        """
        now = time.time()
        with self.db.session() as s:
            candidates = s.scalars(
                select(RunRow)
                .where(
                    RunRow.execution_status.in_(["QUEUED", "PREPARING", "RUNNING"]),
                    (RunRow.lease_expires_at < now) | (RunRow.lease_owner.is_(None)),
                )
                .order_by(RunRow.created_at)
                .limit(5)
            ).all()
            for cand in candidates:
                if not (
                    cand.execution_status == "QUEUED"
                    or not cand.lease_owner
                    or cand.lease_expires_at < now
                ):
                    continue
                # 必须在 UPDATE 之前取旧状态：ORM 对象在 UPDATE 后会被 expire，
                # 再读 cand.execution_status 会重新 SELECT 拿到新值（PREPARING），
                # previous_status 就变成"更新后的状态"，契约失真（PostgreSQL 上必现）。
                previous_status = cand.execution_status
                # 原子领取：条件 UPDATE + rowcount 判定，等价于对租约的 CAS
                result = s.execute(
                    update(RunRow)
                    .where(
                        RunRow.run_id == cand.run_id,
                        RunRow.execution_status.in_(["QUEUED", "PREPARING", "RUNNING"]),
                        (RunRow.lease_expires_at < now) | (RunRow.lease_owner.is_(None)),
                    )
                    .values(
                        fencing_token=RunRow.fencing_token + 1,
                        lease_owner=worker_id,
                        lease_expires_at=now + ttl_seconds,
                        execution_status="PREPARING",
                        started_at=func.coalesce(RunRow.started_at, now),
                    )
                )
                if result.rowcount != 1:
                    continue  # 被其他 Worker 抢走，尝试下一个候选
                target = s.get(RunRow, cand.run_id)
                assert target is not None
                task = s.get(TaskRow, target.task_id)
                if task and task.status in {"CREATED", "QUEUED"}:
                    task.status = "RUNNING"
                    task.updated_at = now
                return {
                    "run_id": target.run_id,
                    "task_id": target.task_id,
                    "workspace_id": target.workspace_id,
                    "attempt": target.attempt,
                    "fencing_token": target.fencing_token,
                    "previous_status": previous_status,
                    "lease_expires_at": target.lease_expires_at,
                    "ttl_s": ttl_seconds,
                }
            return None

    def reap_expired_runs(self) -> list[dict[str, Any]]:
        """把租约过期的未完成 Run 标记为可重新领取（不改变执行历史）。"""
        now = time.time()
        reaped: list[dict[str, Any]] = []
        with self.db.session() as s:
            rows = s.scalars(
                select(RunRow).where(
                    RunRow.execution_status.in_(["PREPARING", "RUNNING"]),
                    RunRow.lease_expires_at < now,
                )
            ).all()
            for r in rows:
                reaped.append(
                    {
                        "run_id": r.run_id,
                        "fencing_token": r.fencing_token,
                        "lease_owner": r.lease_owner,
                        "status": r.execution_status,
                    }
                )
                r.lease_owner = None
                r.lease_expires_at = 0.0
                r.execution_status = "QUEUED"
        return reaped

    def submit_result(
        self,
        run_id: str,
        worker_id: str,
        fencing_token: int,
        *,
        execution_status: str,
        test_verdict: str,
        report: dict[str, Any] | None = None,
        observer_mode: str | None = None,
        error: str | None = None,
        idempotency_key: str = "",
    ) -> dict[str, Any]:
        """提交结果：fencing 校验 + 幂等回执（RUN-010 WP3）。

        契约：
        - 回执与终态记录原子提交（同一事务）；
        - 同一幂等键重复提交 → 返回原回执（verdict/report 不被改写）；
        - 未显式提供幂等键时使用确定性键
          ``{run_id}:{fencing_token}:{worker_id}:{execution_status}``，
          使「同代次同状态的合法重试」天然幂等；
        - 终态 Run 的不同键（或无键重算）重复终态提交 → 冻结语义拒绝
          （FINAL_STATE_FROZEN），verdict/report 不发生静默变化；
        - 旧 fencing token / 旧 owner → STALE_FENCING_TOKEN 拒绝（既有语义）。
        """
        key = (idempotency_key or "").strip()[:160] or self._auto_idempotency_key(
            run_id, worker_id, fencing_token, execution_status
        )
        with self.db.session() as s:
            r = s.get(RunRow, run_id)
            if not r:
                raise AppError(ErrorCode.NOT_FOUND, f"run {run_id} not found")
            # 1) 幂等键重放：返回原回执，不改变任何状态
            existing = s.get(RunSubmissionRow, key)
            if existing is not None:
                if existing.run_id != run_id:
                    raise AppError(
                        ErrorCode.CONFLICT,
                        "IDEMPOTENCY_KEY_REUSED: key belongs to another run",
                    )
                return self._submission_receipt(existing, replayed=True)
            # 2) 主体不符（别人持有租约）：无论 Run 是否终态都是代次失效
            if r.lease_owner != worker_id:
                raise AppError(
                    ErrorCode.CONFLICT,
                    "STALE_FENCING_TOKEN: old/expired worker submit rejected",
                )
            # 3) 终态冻结语义：终态 Run 只允许同键重放（上面已处理）。
            #    任何新键/新状态提交都拒绝，不允许覆盖 verdict/report。
            if r.execution_status in FINAL_RUN_STATES:
                raise AppError(
                    ErrorCode.CONFLICT,
                    f"FINAL_STATE_FROZEN: run {run_id} already "
                    f"{r.execution_status}; duplicate final submissions are rejected "
                    "(replay the original idempotency key to obtain the receipt)",
                )
            # 4) fencing + 租期校验（旧 token / 已过期租约一律拒绝）
            if r.fencing_token != fencing_token or float(r.lease_expires_at or 0.0) <= time.time():
                raise AppError(
                    ErrorCode.CONFLICT,
                    "STALE_FENCING_TOKEN: old/expired worker submit rejected",
                )
            # 5) 状态转换检查
            if (
                execution_status != r.execution_status
                and execution_status not in RUN_TRANSITIONS.get(r.execution_status, set())
                and not (execution_status == "COMPLETED" and r.execution_status == "RUNNING")
            ):
                raise AppError(
                    ErrorCode.CONFLICT,
                    f"illegal run transition {r.execution_status}->{execution_status}",
                )
            # 6) 状态变更 + 回执原子写入（同一事务）
            previous_status = r.execution_status
            r.execution_status = execution_status
            r.test_verdict = test_verdict
            r.report_json = report
            r.observer_mode = observer_mode or r.observer_mode
            r.error = error
            r.ended_at = time.time() if execution_status in FINAL_RUN_STATES else r.ended_at
            # RUN-014 WP2：只有终态提交才释放租约。中间态（RUNNING/FINALIZING）仍由
            # 本 Worker 持有执行权，提前清空会让"还在执行"的 Run 变成无主可接管。
            if execution_status in FINAL_RUN_STATES:
                r.lease_expires_at = 0.0
            receipt = RunSubmissionRow(
                idempotency_key=key,
                run_id=run_id,
                fencing_token=fencing_token,
                worker_id=worker_id,
                execution_status=execution_status,
                test_verdict=test_verdict,
                report_json=report,
                error=error,
                observer_mode=observer_mode or r.observer_mode,
                submitted_at=time.time(),
            )
            s.add(receipt)
            task = s.get(TaskRow, r.task_id)
            # RUN-009 WP4：FINALIZING 是中间态，不得提前把 Task 判为 FAILED；
            # 只有 Run 进入终态时才同步 Task 状态。
            if (
                task
                and execution_status in FINAL_RUN_STATES
                and task.status in {"RUNNING", "WAITING"}
            ):
                task.status = "COMPLETED" if execution_status == "COMPLETED" else "FAILED"
                task.updated_at = time.time()
            return self._submission_receipt(
                receipt, replayed=False, previous_status=previous_status
            )

    @staticmethod
    def _auto_idempotency_key(
        run_id: str, worker_id: str, fencing_token: int, execution_status: str
    ) -> str:
        return f"{run_id}:{int(fencing_token)}:{worker_id}:{execution_status}"[:160]

    @staticmethod
    def _submission_receipt(
        row: RunSubmissionRow, *, replayed: bool, previous_status: str = ""
    ) -> dict[str, Any]:
        return {
            "idempotency_key": row.idempotency_key,
            "run_id": row.run_id,
            "fencing_token": row.fencing_token,
            "worker_id": row.worker_id,
            "execution_status": row.execution_status,
            "test_verdict": row.test_verdict,
            "report": row.report_json,
            "error": row.error,
            "observer_mode": row.observer_mode,
            "submitted_at": row.submitted_at,
            "replayed": replayed,
            **({"previous_status": previous_status} if previous_status else {}),
        }

    def mark_running(
        self, run_id: str, worker_id: str, fencing_token: int, observer_mode: str
    ) -> None:
        """PREPARING → RUNNING：条件 UPDATE（代次 + 属主 + 未过期 + 非终态）。"""
        now = time.time()
        with self.db.session() as s:
            r = s.get(RunRow, run_id)
            if not r or r.fencing_token != fencing_token or r.lease_owner != worker_id:
                raise AppError(ErrorCode.CONFLICT, "STALE_FENCING_TOKEN")
            if float(r.lease_expires_at or 0.0) <= now:
                raise AppError(ErrorCode.CONFLICT, "LEASE_LOST: 租约已过期，不能开始执行")
            values: dict[str, Any] = {"observer_mode": observer_mode}
            if "RUNNING" in RUN_TRANSITIONS.get(r.execution_status, set()):
                values["execution_status"] = "RUNNING"
            result = s.execute(
                update(RunRow)
                .where(
                    RunRow.run_id == run_id,
                    RunRow.lease_owner == worker_id,
                    RunRow.fencing_token == int(fencing_token),
                    RunRow.lease_expires_at > now,
                    RunRow.execution_status.notin_(WRITE_BLOCKED_STATES),
                )
                .values(**values)
            )
            if result.rowcount != 1:
                raise AppError(ErrorCode.CONFLICT, "LEASE_LOST: 租约已失效，写入被拒绝")

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self.db.session() as s:
            r = s.get(RunRow, run_id)
            return self._run_dict(r) if r else None

    def list_runs(
        self,
        task_id: str | None = None,
        *,
        workspace_id: str | None = None,
        workspace_ids: list[str] | None = None,
        status: str | None = None,
        verdict: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        with self.db.session() as s:
            q = select(RunRow)
            if task_id:
                q = q.where(RunRow.task_id == task_id)
            if workspace_id:
                q = q.where(RunRow.workspace_id == workspace_id)
            if workspace_ids is not None:
                q = q.where(RunRow.workspace_id.in_(list(workspace_ids)))
            if status:
                q = q.where(RunRow.execution_status == status)
            if verdict:
                q = q.where(RunRow.test_verdict == verdict)
            rows = s.scalars(q.order_by(RunRow.created_at.desc()).limit(limit)).all()
            return [self._run_dict(r) for r in rows]

    @staticmethod
    def _run_dict(r: RunRow) -> dict[str, Any]:
        return {
            "run_id": r.run_id,
            "task_id": r.task_id,
            "workspace_id": r.workspace_id,
            "attempt": r.attempt,
            "testcase_version_id": r.testcase_version_id,
            "execution_status": r.execution_status,
            "test_verdict": r.test_verdict,
            "observer_mode": r.observer_mode,
            "fencing_token": r.fencing_token,
            "lease_owner": r.lease_owner,
            "lease_expires_at": r.lease_expires_at,
            "report": r.report_json,
            "error": r.error,
            "created_at": r.created_at,
            "started_at": r.started_at,
            "ended_at": r.ended_at,
        }

    # ------------------------------------------------------------------ Step
    def record_step(
        self,
        run_id: str,
        step_id: str,
        index: int,
        status: str = "PENDING",
        error: str | None = None,
        *,
        action: str = "",
        duration_ms: int = 0,
        observation: dict[str, Any] | None = None,
        fence: tuple[str, int] | None = None,
    ) -> None:
        """写步骤。传 `fence=(worker_id, fencing_token)` 时走租约守卫事务。

        RUN-014 WP2：守卫 UPDATE 与步骤写入在同一事务内，旧 Worker 无法在
        "校验通过 → 被接管" 的窗口里追加步骤。
        """
        if fence is not None:
            with self.fenced_session(run_id, fence[0], int(fence[1])) as s:
                self._record_step_in(
                    s, run_id, step_id, index, status, error, action, duration_ms, observation
                )
            return
        with self.db.session() as s:
            self._record_step_in(
                s, run_id, step_id, index, status, error, action, duration_ms, observation
            )

    @staticmethod
    def _record_step_in(
        s: Session,
        run_id: str,
        step_id: str,
        index: int,
        status: str,
        error: str | None,
        action: str,
        duration_ms: int,
        observation: dict[str, Any] | None,
    ) -> None:
        row_id = f"{run_id}:{step_id}"
        existing = s.get(StepRow, row_id)
        if existing:
            existing.status = status
            existing.error = error
            existing.action = action or existing.action
            existing.duration_ms = duration_ms or existing.duration_ms
            if observation is not None:
                existing.observation = observation
        else:
            s.add(
                StepRow(
                    id=row_id,
                    step_id=step_id,
                    run_id=run_id,
                    step_index=index,
                    status=status,
                    error=error,
                    action=action,
                    duration_ms=duration_ms,
                    observation=observation or {},
                )
            )

    def list_steps(self, run_id: str) -> list[dict[str, Any]]:
        with self.db.session() as s:
            rows = s.scalars(
                select(StepRow).where(StepRow.run_id == run_id).order_by(StepRow.step_index)
            ).all()
            return [
                {
                    "step_id": r.step_id,
                    "run_id": r.run_id,
                    "index": r.step_index,
                    "action": r.action,
                    "status": r.status,
                    "error": r.error,
                    "duration_ms": r.duration_ms,
                    "observation": dict(r.observation or {}),
                }
                for r in rows
            ]

    # --------------------------------------------------------------- Summary
    def task_summary(self, task_id: str) -> dict[str, Any]:
        """Task 级展示汇总（不得冒充单次 Run 的 Test Verdict）。"""
        runs = self.list_runs(task_id)
        verdicts: dict[str, int] = {}
        for r in runs:
            verdicts[r["test_verdict"]] = verdicts.get(r["test_verdict"], 0) + 1
        latest = runs[0] if runs else None
        return {
            "task_id": task_id,
            "run_count": len(runs),
            "verdicts": verdicts,
            "latest_run": latest,
            "note": "展示汇总，非单次 Run 的 Test Verdict",
        }

    def queue_stats(self) -> dict[str, Any]:
        with self.db.session() as s:
            queued = s.scalar(select(RunRow).where(RunRow.execution_status == "QUEUED").limit(1))
            running = s.scalar(select(RunRow).where(RunRow.execution_status == "RUNNING").limit(1))
            total = len(s.scalars(select(RunRow.run_id)).all())
        return {
            "queued_present": queued is not None,
            "running_present": running is not None,
            "total_runs": total,
        }
