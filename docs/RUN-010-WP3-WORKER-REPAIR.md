# RUN-010 WP3：Worker / Task / Repair 状态正确性与幂等

> 状态：**IMPLEMENTED / VERIFIED**（幂等契约 8 用例 + 既有 4/4 Worker 故障契约测试；全量回归 588 passed）。
> 提交：见 git log（RUN-010 WP3）。
> Worker 四真实故障场景：**BLOCKED**（B-17 未解除，本轮未绕过）。

## 1. Task 终态提交幂等（§3.1）

### 契约（RUN-009 缺口 → 本轮修复）

RUN-009 已发现：同一 owner、同一 fencing token 的重复终态提交会覆盖 verdict/report。
本轮在 `TaskService.submit_result` 引入**提交回执（submission receipt）**：

| 规则 | 实现 |
|---|---|
| 提交回执与终态记录原子提交 | 回执写入与 Run 状态变更在同一事务（同一 `db.session()`） |
| 同一幂等键重复提交返回原回执 | `RunSubmissionRow` 主键幂等键；命中即返回 `replayed=True` 的原回执，verdict/report 不变 |
| 不同 key 的重复终态提交按冻结语义拒绝 | Run 已在 `FINAL_RUN_STATES` → 任何新键提交抛 `FINAL_STATE_FROZEN` |
| 旧 fencing token 拒绝 | 既有 `STALE_FENCING_TOKEN`（保持） |
| 终态 Run 不允许被旧提交覆盖 | fencing + 冻结双保险：抬升 token 后的新代次提交同样被冻结拒绝（测试覆盖） |
| Report / Verdict 不允许静默变化 | 重放路径不改 Run；新键路径拒绝 |
| 未显式提供幂等键 | 自动确定性键 `{run_id}:{fencing_token}:{worker_id}:{execution_status}` —— 同代次同状态的合法重试天然幂等，不同状态互不冲突，调用方零改动 |

### 调用方兼容性核查（避免只改一个服务）

- `pipeline.py`（正式执行链路）：忽略返回值，自动键生效 —— 崩溃重试安全；
- Worker / Repair / Sandbox / Reconcile 路径：经 `rt.tasks.submit_result`，全部回归通过（36 个既有 Task 相关测试 + 8 个新契约测试）；
- 新表：`tg_run_submission`（迁移 v13，幂等键主键 + run_id 索引）。

## 2. 状态机审查（§3.2）

- `RUN_TRANSITIONS`：QUEUED→PREPARING→RUNNING→FINALIZING→{COMPLETED/FAILED}（含 TIMED_OUT/CANCELLED 并行出口），终态转换集为空（冻结）——本轮新增终态提交冻结语义后，状态机与提交层一致；
- `TASK_TRANSITIONS`：终态（COMPLETED/FAILED/CANCELLED）转换集为空；
- **语义独立性**：`execution_status` 与 `test_verdict` 保持独立字段、独立写入（本轮修复中还纠正了 `ended_at`——原先 RUNNING 提交也会覆盖结束时间，现在只有终态提交写入）；
- Run 提交回执按 (RUNNING, FINALIZING, COMPLETED) 各自记录，`step_event_count` 等报告数据仍由 report 承载，不混入状态字段。

## 3. Repair 状态机审查（§3.3）

审查范围：Proposal 创建 → Assertion Preservation → Sandbox → 审批 → CAS 晋升 → 回归 → 回滚 → Crash Recovery → Reconcile。

| 检查项 | 结论 |
|---|---|
| 重复审批 | APPROVED→APPROVED 非法转换，状态机拒绝（`test_repair_duplicate_approve_rejected`）；审批后 reject 同样被拒 |
| 重复晋升 | 同幂等键 → 回执重放（不重复创建版本，版本集合断言 {1,2}）；不同键 → CAS/回执防护（既有 `test_repair_atomic_cas` 保留） |
| CAS 成功后进程退出 | 崩溃恢复路径既有（`test_repair_crash_recovery` 保留通过） |
| 回执写入 | promotion_record + tg_promotion_receipt（v8）双记录（既有） |
| 用户权限撤销后继续执行 | WP1 认证开启时审批主体来自服务端 Principal；本模块 `require_human_actor` 拒绝 agent:/llm:/system: 等主体（既有 + 保留） |
| 历史版本引用 / 回滚 | rollback 从版本历史恢复指定版本（既有保留） |
| 幂等键跨请求重用 | propose→approve→promote 全链路幂等键测试（新增） |

## 4. Worker 四场景故障矩阵（§3.4）——BLOCKED（B-17 保持）

- AgentDR 对 B-17 的拦截**未解除**：本轮未绕过 AgentDR、未更换工具、未拆分写入、未改写参数、未读取真实凭据、未把契约测试冒充真实进程测试；
- 未获得安全负责人或 AgentDR 管理员的正式批准 → 按指令执行替代路径：
  - 纯契约测试 4/4 保持通过（`test_worker_fault_contract_run009.py`）；
  - 恢复状态机测试：领取→提交→接管（fencing token 单调）→ 旧 Worker 写入被拒（同文件保留 + 本轮新增冻结断言）；
  - 状态记录为 **BLOCKED**，不伪造真实进程故障证据；
- 解除条件不变：受信任执行路径批准后按 RUN-009 WP4 报告 §2.3 执行。

## 5. 验收（§3.5）

| 验收项 | 证据 |
|---|---|
| 同 Run 接管证据 | `test_duplicate_submit_contract_stale_rejected_same_owner_recorded`（worker-B 领取后 token 递增） |
| fencing token 单调增加 | 同上 + `claim_next_run` 实现（token+1 原子递增） |
| 旧 Worker 写入被拒绝 | 同上（STALE_FENCING_TOKEN） |
| 重复提交不会覆盖最终结果 | `test_same_idempotency_key_replays_original_receipt` / `test_auto_idempotency_key_makes_retry_safe` / `test_final_state_frozen_against_new_key_submission` |
| Event 无重复 | 事件层 idem 键（既有 `test_event_concurrency` 保留） |
| Evidence 无孤儿 | 既有 Evidence 持久化测试保留 |
| Task / Run 最终状态一致 | `test_multi_status_progression_with_receipts`（Task COMPLETED 与 Run COMPLETED 同步断言） |
| Repair 晋升回执正确 | `test_repair_promote_requires_idempotency_and_replay` + 既有 CAS 测试 |
| 回滚可以恢复指定版本 | 既有 rollback 测试保留通过 |

## 6. 状态

- Task 幂等/冻结：IMPLEMENTED / VERIFIED；
- Repair 状态机：VERIFIED（既有 + 新增）；
- Worker 真实故障注入：BLOCKED（B-17，未绕过）。
