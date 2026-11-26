# RUN-015 WP1：Worker Fencing 最终一致性

> 分支：`workbuddy/run015-execution-consistency`｜基线：`438c54a`（RUN-014 最终 HEAD）
> 状态：**PASS**（专项与 PG16 锁等待竞态均已真实运行；边界见 §6）

---

## 1. 修复的三个实际缺陷

### 1.1 `submit_result` 的 TOCTOU（最高优先级）

原实现（RUN-014）：同一事务内 `s.get(RunRow)` → Python 层比对 owner / token / 租期 / 状态 → 就地改 ORM → 提交。
SELECT 与写回之间没有行锁，也没有带条件的最终 UPDATE。真实竞态：

```
A 读取旧租约(owner=A, token=1) → 暂停
租约到期 → B 接管(token=2) → B 写 Step/Evidence/Event → B 提交 COMPLETED/PASSED/回执
A 恢复 → 旧 ORM 对象被 flush → B 的 verdict/report 被覆盖
```

修复：`TaskService.submit_result()` 在任何业务写入之前调用 `fenced_guard()`，
在**同一事务**内对 `tg_run` 做条件 UPDATE（owner + token + 租期 + 状态全命中），
未命中立即 `STALE_FENCING_TOKEN` 并回滚；拿锁后 `s.refresh(r)` 重新读取真实行，
并在锁内再次查幂等回执、重新执行终态冻结与状态转换检查。
`RunSubmission` 回执与 Run 状态仍在同一次 commit 中写入。

保留的既有语义：

- 同幂等键重放先于守卫返回原回执，终态后仍可重放；
- 不同键的终态重写 → `FINAL_STATE_FROZEN`（同 owner + 同代次）/ `STALE_FENCING_TOKEN`（旧 owner 或旧代次）；
- `FINALIZING → COMPLETED/FAILED` 仍合法：守卫新增 `allow_finalizing=True`，只在该调用点放宽 FINALIZING，Step/Evidence/Event 的默认守卫仍拒绝 FINALIZING；
- `run.completed` 的 `allow_own_submission` 语义不变。

### 1.2 Pipeline 缺失的 Event Fence

`RunPipeline._emit()` 支持 fence，但两个调用点没有传入，导致 `PersistentEventStore.append()` 的守卫根本没有执行：

- `step.completed`（`pipeline.py:447`）
- `assertion.evaluated`（`pipeline.py:499`）

已补 `fence=(self.worker_id, token)`。全仓审查确认所有 Run 业务事件只从 `RunPipeline._emit()` 发出，
六个调用点（`run.started`、`run.failed` ×2、`run.completed`、`step.completed`、`assertion.evaluated`）
现在均带 fence；`run.completed` 保留 `allow_own_submission=True`。

### 1.3 锁等待期间的租约时间失真

原实现在 UPDATE 前用 Python `time.time()` 生成 SQL 参数。若语句等待行锁，等锁期间租约真实过期，
拿锁后仍可能用调用时的旧时间命中条件，让过期租约"复活"。

**实测发现**：在 PostgreSQL 16.16 上，即使把 `clock_timestamp()` 直接写进条件 UPDATE 的 WHERE，
语句因行锁阻塞后，拿锁时仍按语句开始时的快照求值（实验：调用发生在过期前 3 秒，等锁 3.35 秒后返回 `rowcount=1`）。
因此"换成数据库时钟函数"本身不足以修复。

采用指令允许的第二种方式：**先取锁、后读时钟**。`db_now(session, run_id=...)` 在 PG 上执行
`SELECT lease_expires_at FROM tg_run WHERE run_id=? FOR UPDATE`（阻塞至获得行锁并取得最新版本），
随后读取 `EXTRACT(epoch FROM clock_timestamp())`；`fenced_guard`、`renew_lease`、`mark_running`
统一使用拿锁后的时间。SQLite 为单写者，保留 Python 时间。没有增加"仅 Python 层的 SELECT"来冒充守卫。

---

## 2. 修改文件

| 文件 | 变更 |
|---|---|
| `packages/persistence/traceguard_persistence/fencing.py` | 新增 `db_now()`（锁后 DB 时钟）、`fenced_guard(allow_finalizing=...)` |
| `packages/execution/traceguard_execution/tasks/service.py` | `submit_result()` 原子守卫 + 锁内刷新/回执复查；`renew_lease()` / `mark_running()` 使用锁后时钟 |
| `packages/execution/traceguard_execution/runtime/pipeline.py` | `step.completed` / `assertion.evaluated` 补 fence |
| `tests/integration/test_worker_fencing_run015.py` | 新增 3 项确定性测试（SQLite） |
| `tests/integration/test_fencing_pg_run015.py` | 新增 6 项 PG16 测试 |
| `docs/assets/run015/wp1/*.txt` | 原始 pytest 输出 |

---

## 3. 测试与原始证据

| 命令 | 结果 | 原始输出 |
|---|---|---|
| `pytest tests/integration/test_worker_fencing_run015.py tests/integration/test_worker_fencing_run014.py -q` | **10 passed** | `docs/assets/run015/wp1/fencing-sqlite-raw.txt` |
| `pytest tests/integration/test_fencing_pg_run015.py -v`（PG16.16） | **6 passed** | `docs/assets/run015/wp1/fencing-pg16-raw.txt` |
| `pytest -q`（全量最终复跑） | **828 passed / 16 skipped** | `docs/assets/run015/final/full-pytest-raw.txt` |

新增测试内容：

1. `test_submit_result_atomic_guard_prevents_old_worker_override`：在守卫入口同步完成 B 的接管与终态提交，A 恢复后提交必须 `STALE_FENCING_TOKEN`；B 的 Verdict / Report / Receipt 逐字段不变，A 的回执不落库。
2. `test_pipeline_run_events_are_fenced[step.completed|assertion.evaluated]`：真实 `RunPipeline.execute()` 路径，A 完成 Step/Evidence 后在 Event 写入前被接管；A 不得追加对应事件，B 的终态与结果完整。测试只替换"观测来源"（StubObserver），不替换 Worker、EventStore、EvidenceManager 或 TaskService（不使用 Mock 冒充真实 Worker）。
3. PG16 锁等待：`renew` / `mark_running` / `step` / `submit` 四种操作在租约有效时发起并阻塞，等锁至租约过期后释放，全部被拒绝；过期 Worker 不得复活租约、不得写 Step、不得落终态回执。
4. PG16 `submit_result` 接管竞态与四 Worker 并发领取只有一个 owner。

---

## 4. 验收矩阵

| 不变量 | 证据 |
|---|---|
| 过期 Worker 无法续租 | RUN-014 `test_expired_lease_is_invalid_before_takeover`；PG16 `[renew]` |
| 过期 Worker 无法标记 RUNNING | PG16 `[mark_running]` |
| 过期 Worker 无法写 Step / Evidence / Event | RUN-014 专项 + PG16 `[step]` + Pipeline 两项 |
| 过期 Worker 无法提交终态 | PG16 `[submit]` + SQLite 原子守卫测试 |
| 并发接管只有一个合法 owner | RUN-014 4 线程 + PG16 4 线程 |
| 最终提交回执不被旧 Worker 覆盖 | 两项 takeover 测试逐字段断言 |

---

## 5. 与 RUN-014 的兼容性

- `FINALIZING` 状态机、幂等键重放、终态冻结、自动幂等键格式均未改变；
- RUN-014 七项 fencing 并发测试原样通过；`test_task_idempotency_run010` 与 `test_worker_fault_contract_run009` 原样通过；
- 事件序号在守卫失败时仍可能跳号（不重复/不倒退），与 RUN-014 边界一致。

## 6. 边界

- Artifact 内容寻址落盘仍未纳入守卫（最坏留下无人引用的孤儿对象），与 RUN-014 相同；
- PG16 专项需本机 55432 或 `TG_TEST_POSTGRES_URL`；无环境时按 skip 处理，不标 PASS；
- 本轮未修改 B-17 相关安全边界，未使用被禁止的故障注入。
