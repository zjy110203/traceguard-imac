# RUN-014 WP2：Worker Lease / Fencing 原子性强化

> 分支：`workbuddy/run014-product-closure`
> 结论：租约过期即失效（fail-closed）；Step / Evidence / Event / 最终结果由**同一套**
> 事务内守卫保护，旧 Worker 无法在"检查通过 → 被接管"的窗口里写入任何业务数据。

---

## 1. 审查发现的真实缺陷

| # | 位置 | 缺陷 | 影响 |
|---|---|---|---|
| F1 | `TaskService.lease_valid` | 只比对 `lease_owner` / `fencing_token`，**不检查 `lease_expires_at`** | 租约已过期、尚未被接管时，旧 Worker 仍自认合法；SIGSTOP 恢复后可以继续写 |
| F2 | `TaskService.renew_lease` | 同样不看过期时间，过期租约可被"续租复活" | 与 `claim_next_run` 的接管条件形成竞态：一边复活一边被接管 |
| F3 | `RunPipeline` + `record_step` / Evidence / Event | `_ensure_lease()` 是独立 SELECT，写入是另一个事务 | 典型 TOCTOU：检查成功 → 租约被接管 → 旧 Worker 继续写步骤/证据/事件 |
| F4 | `submit_result` | 只校验 token/owner，不校验租期 | 过期 Worker 的终态提交被接受，可能覆盖接管者的结果 |
| F5 | `submit_result` | **任何**提交（含 RUNNING/FINALIZING 中间态）都会把 `lease_expires_at` 清零 | 仍在执行的 Run 变成"无主"，任何 Worker 都能合法接管 → 双执行风险 |
| F6 | `mark_running` | 先 SELECT 再 ORM 赋值，UPDATE 无代次条件 | 接管后旧的 `mark_running` 仍可能把状态改回 RUNNING |

## 2. 修复方案

### 2.1 租约语义（fail-closed）

```
执行权 = lease_owner == worker AND fencing_token == token
         AND lease_expires_at > now AND status ∉ {COMPLETED, FAILED, TIMED_OUT, CANCELLED, FINALIZING}
```

过期即失效，**不以"尚未有新 Worker 接管"作为合法性依据**。
`renew_lease` / `lease_valid` / `mark_running` / `submit_result` 全部按此判定；
`renew_lease`、`mark_running` 改为单条条件 UPDATE（rowcount 判定），不再"先读后写"。

### 2.2 Fencing 真正约束持久化写入

新增 `packages/persistence/traceguard_persistence/fencing.py`：

```sql
-- 在写入所在的同一事务内执行（自赋值，只为取行锁 + 原子条件判定）
UPDATE tg_run SET fencing_token = fencing_token
 WHERE run_id = :run_id AND lease_owner = :worker AND fencing_token = :token
   AND lease_expires_at > :now AND execution_status NOT IN ('COMPLETED', ...)
```

命中（rowcount=1）→ 本事务持有 run 行锁，接管方必须等本事务提交；
未命中 → 立即 `LEASE_LOST`，整笔写入回滚。

这是"事务内校验 + 原子条件更新"，不是"再补一次 SELECT"：

- PostgreSQL READ COMMITTED 下，等锁的接管语句在锁释放后会**重新求值 WHERE**，
  本事务已提交/已续租 → 接管条件不再成立，不会出现"检查后被接管仍然写入"；
- SQLite 写锁串行化，语义一致。

接线点：

| 写入 | 接口 | 守卫 |
|---|---|---|
| Step（含 api 预置/超时步骤） | `TaskService.record_step(..., fence=(worker, token))` | `fenced_session` + 同事务写 Step |
| Evidence（步骤/状态/信号/附件） | `PersistentEvidenceManager.add_raw(..., fence=...)` | 同事务 `fenced_guard` 后插入 |
| Event（run.started / step.completed / assertion.evaluated / run.failed） | `PersistentEventStore.append(..., fence=...)` | 同事务 `fenced_guard` 后插入 |
| Event（`run.completed` 收尾） | `append(..., allow_own_submission=True)` | 仅接受"同一 worker + 同一代次已提交终态"的回执，代次被推进则拒绝 |
| 最终结果 | `submit_result` | token + owner + 租期 + 终态冻结 |
| 续租 / 开跑 | `renew_lease` / `mark_running` | 单条条件 UPDATE |

### 2.3 中间态不释放租约

只有终态提交才 `lease_expires_at = 0`。RUNNING/FINALIZING 的中间提交保留租约，
避免"还在执行却被判定无主"。

### 2.4 状态码顺序（保持既有契约）

`submit_result` 的拒绝顺序：幂等回执重放 → owner 不符（`STALE_FENCING_TOKEN`）→
终态冻结（`FINAL_STATE_FROZEN`）→ token/租期不符（`STALE_FENCING_TOKEN`）。
既保留"终态不可覆盖"的既有语义，也不让过期 Worker 蒙混。

## 3. 新增并发故障测试

`tests/integration/test_worker_fencing_run014.py`（真实 SQLite 文件库 + 真实队列语义）：

| 场景 | 用例 | 断言 |
|---|---|---|
| 过期但尚未被接管 | `test_expired_lease_is_invalid_before_takeover` | `lease_valid=False`、`renew=False`、Step 写入 `LEASE_LOST`、submit 拒绝、Run 仍 PREPARING |
| A 通过检查 → 暂停 → 过期 → B 接管 → B 完成 → A 恢复 | `test_paused_worker_cannot_write_after_takeover` | A 的 Step/Evidence/Event/终态提交全部被拒；步骤 1 条、证据 1 条、事件 1 条、verdict 仍为 B 的 PASSED |
| FINALIZING 中间态 | `test_finalizing_blocks_further_writes` | Step/Evidence 写入 `LEASE_LOST` |
| 两个 Worker 同时争抢 | `test_concurrent_claim_single_owner`（4 线程 + Barrier） | 仅 1 个 owner，`fencing_token=1` |
| 续租边界 | `test_renew_keeps_lease_but_never_after_takeover` | 本人可续；他人不可续 |
| API/Worker 重启 | `test_api_restart_keeps_fencing` | 新实例识别旧代次失效；接管后旧 Worker 写入被拒 |
| 幂等提交 | `test_idempotent_submit_still_replays_after_terminal` | 同键重放返回原回执；新写入被拒 |

```bash
python -m pytest tests/integration/test_worker_fencing_run014.py -q
# 7 passed in 1.87s
```

原有 SIGKILL / SIGSTOP / SIGCONT 场景（RUN-013 WP1 真实进程实验）保留未改动；
本轮的全量回归覆盖到 `tests/unit/test_tasks.py`、`test_task_idempotency_run010.py`、
`test_event_concurrency.py`、`test_evidence.py`、`test_full_pipeline.py`。

## 4. 全量回归

```bash
python -m pytest -q
# 791 passed, 16 skipped（skip 全部为未配置 TG_TEST_POSTGRES_URL 的 PG 专项）
```

PostgreSQL 16 专项与多进程故障恢复见 WP6（`docs/RUN-014-FINAL-REPORT.md`）。

## 5. 保留风险 / 边界

1. **Artifact 落盘未纳入守卫**：`ArtifactStore.put` 是内容寻址 + 幂等写入，
   旧 Worker 最坏只会留下一个无人引用的孤儿 artifact（不产生业务结果、不可被证据引用）。
2. **`run.completed` 事件的特例**：必须在 `submit_result` 之后发出，因此用
   `allow_own_submission` 接受"同 worker 同代次的终态回执"。代次被推进后该分支失效。
3. Event 序号在守卫失败时可能"烧号"（分配在独立事务）：表现为合法跳号，不会重复/倒退。
4. 数据库整体断连不在本文件覆盖（RUN-013 已有 `pool_pre_ping` 恢复验证）。
