# RUN-016 WP1-B：Fencing 全链路最终审查

> 分支：`workbuddy/run016-finalization`｜基线：`73e4fe6`
> 方法：逐条读源码 + 真实 PostgreSQL 16 专项测试 + 静态审计（AST）
> 原则：**能证明已有实现正确，就保留实现只补证据；有真实缺陷才改代码。**

---

## 1. 写入链路逐条核对

| 入口 | 实现位置 | 守卫方式 | 结论 |
|---|---|---|---|
| `claim_next_run` | `tasks/service.py` | 条件 `UPDATE ... WHERE lease 空闲/过期` + rowcount CAS | OK：两个进程抢同一 Run 只有一个命中 |
| `renew_lease` | `tasks/service.py` | owner + token + 未终态 | OK：被接管后旧 token 续租失败 |
| `mark_running` | `tasks/service.py` | `fenced_guard` | OK |
| `record_step` | `tasks/service.py::record_step` | 传 fence → `fenced_session`（守卫 UPDATE 与 step 写入同事务） | OK |
| Evidence 写入 | `stores.py::PersistentEvidenceManager._insert` | `fenced_guard`（`fence` 非空时） | OK |
| Event 写入 | `stores.py::PersistentEventStore.append` | `fenced_guard`（`fence` 非空时）+ `allow_own_submission` 仅限同 worker 同代次 | OK |
| `submit_result` | `tasks/service.py` | `fenced_guard(allow_finalizing=True, allow_own_submission=True)` + 取锁后 `refresh` + 二次幂等检查 | OK |
| Pipeline 事件 | `runtime/pipeline.py::_emit` | 所有调用显式传 `fence=(worker_id, token)` | OK（新增静态审计锁死） |
| Artifact 登记 | `stores.py::ArtifactStore.put` | **原实现无守卫** | **缺陷 → 本轮修复**（见 §4） |
| 终态冻结 | `submit_result` | 终态 Run 只允许同键重放，其它一律 `FINAL_STATE_FROZEN/STALE` | OK |
| FINALIZING | `fenced_guard` | 默认阻塞 Step/Evidence/Event；仅 `submit_result` 可推进到终态 | OK |

## 2. 六个重点问题的结论

1. **旧 Worker 恢复后能否写入** → 不能。`fenced_guard` 同时校验 `lease_owner`、`fencing_token`、
   `lease_expires_at > db_now()`；被接管后三者至少一项不成立。
2. **租约过期后能否继续写入** → 不能。租约过期即失效，不以"还没有新 Worker 接管"为由放行
   （RUN-014 已修正，本轮复核确认）。
3. **行锁等待后是否重新读数据库时间** → 是。`db_now()` 先 `SELECT ... FOR UPDATE` 拿行锁，
   再读 `clock_timestamp()`（RUN-015 WP1.3）。PG 实测：锁等待跨过过期点后条件 UPDATE 仍失败。
4. **事件是否存在未受 Fencing 保护的写入入口** → 修复前存在结构性风险（`fence` 缺省 None，
   新调用点容易漏传）。本轮新增 AST 审计测试，把"每个 `_emit` 必须传 fence"变成 CI 约束。
5. **事务提交是否可能部分成功** → 不会。守卫 UPDATE、业务写入、提交在同一 `Session` 事务内；
   `Database.session()` 异常即 rollback。新增 PG 测试：事务中途抛错后 step 数为 0。
6. **进程重启是否影响 token 有效性** → 不影响。token 存在 `tg_run` 行内；
   新 Runtime（等价重启）用旧 token 写入被拒，重新领取得到严格递增的新 token。

## 3. 新增测试与证据

### 3.1 静态审计（无需数据库）

`tests/unit/test_fencing_entrypoint_audit_run016.py`：

- `RunPipeline._emit(...)` 的每一处调用必须显式传 `fence=`；
- `RunPipeline` 内 `artifacts.put(...)` 必须传 `fence=` 与 `run_id=`；
- `_emit` 的 `fence` 必须保持关键字参数且默认 `None`（强制调用侧显式传入）。

结果：`3 passed`。该测试会在未来新增事件写入时立刻失败，防止静默绕过。

### 3.2 PostgreSQL 16 专项

`tests/integration/test_fencing_audit_run016.py`（真实 PG，非 mock）：

| 用例 | 验证点 | 结果 |
|---|---|---|
| `test_partial_transaction_rolls_back_completely` | 守卫 + step 写入中途抛错 → step 数 0 | PASS |
| `test_new_process_with_old_token_cannot_write` | 新 Runtime 用旧 token → `LEASE_LOST`；重新领取后新 token 可写、旧 token 永久失效 | PASS |
| `test_unfenced_event_with_forged_owner_is_rejected` | 伪造 owner+token 写事件 → `LEASE_LOST` | PASS |
| `test_evidence_write_blocked_after_lease_expiry` | 租约过期后写证据 → `LEASE_LOST` | PASS |

原始日志：`docs/assets/run016/wp1/fencing-tests.log`。

## 4. 本轮唯一代码修改：Artifact 登记纳入 Fencing
`ArtifactStore.put()` 原先"先落盘、后登记"，且登记不校验租约。旧 Worker 在失去租约后
仍可登记产物记录（或写出无人引用的文件）。修复：

- 新增 `fence=(worker_id, token)` / `run_id` 参数；登记前在同一事务内执行 `fenced_guard`；
- 守卫失败 → `LEASE_LOST`，并**回滚本次新建的文件**（若库中已有同哈希登记则保留，避免误删胜者文件）；
- 文件写入改为"临时文件 + 原子 rename"，避免半截文件。
- Pipeline 的 artifact sink 传递 `fence`，API 侧（手动上传）保持不传 fence 的合法用法。

## 5. 剩余限制（如实登记）

- SQLite 单写者模式没有等价的"行锁等待后重读时钟"，`db_now()` 直接返回 Python 时间；
  跨进程并发语义由 PG 验证，SQLite 仅保证单进程正确性。
- `claim_next_run` 的候选集 `limit(5)` 在高争用下可能多轮才领取到；不影响正确性，只影响吞吐。
- 静态审计只覆盖 `RunPipeline`；若未来在别处新增 Run 业务写入入口，需要扩展审计范围
  （当前所有业务写入仍集中在 `TaskService` / `PersistentEvidenceManager` / `PersistentEventStore`）。

## 6. 审查中发现的两个语义细节（已写入测试，避免误判）

### 6.1 进程重启 ≠ 接管

- **进程重启**（同 `worker_id`、同 `fencing_token`、租约未过期）→ 允许继续写。
  这是正确行为：否则任何 Worker 进程重启都会把执行中的 Run 变成不可继续。
- **被接管**（token 严格递增）→ 旧 token 立即失效，即使原租约时间窗还没到。

`test_process_restart_keeps_token_then_takeover_invalidates_it` 把这条边界固定下来，
避免后续把"重启"误判成"越权"或反过来放宽。

### 6.2 `fenced_session` 不可重入（同一 Run）

在 `fenced_session(run_id, ...)` 内部再调用 `record_step(..., fence=...)`（它会另开一个
Session 并再次对同一 `tg_run` 行做条件 UPDATE）会**自锁**：外层事务持有行锁，内层等待锁，
在 PostgreSQL 上表现为永久阻塞（不是异常）。

- 当前产品代码不存在这种嵌套调用（Pipeline 直接写 step，不预先持有守卫事务）；
- 本轮测试最初写成嵌套形式，在 PG 上直接挂死，已改为在同一事务内调用 `_record_step_in()`；
- 结论：这是**开发者 API 使用约束**，不是产品缺陷。如需防御，可在 `fenced_session`
  上用 ContextVar 记录已持锁的 run_id 并在重入时直接抛错——本轮未实现，登记为技术债。
