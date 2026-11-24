# RUN-009 WP4：Worker 故障矩阵与安全阻塞处理

> 状态：PARTIAL / BLOCKED（四场景真实进程故障注入保持 BLOCKED；契约级工作已 VERIFIED）。
> 生成时间：2026-11-24。权威起点 `cb80591`。本文件不把旧 SIGKILL 证据当作四个新场景的证据。

## 1. B-17 安全边界复核

- 已复核 `.workbuddy-run/blockers.md` B-17 及下方根因分析：RUN-008 中 BuddySentinel 以 `info_theft_steal_env_keys` 两次拦截了 `scripts/worker_fault_matrix_run008.py` 的写入，第一次使用 `**os.environ` 整体复制父进程环境，第二次改为显式最小白名单后仍被拦截。
- 原文结论明确：不重试、不换工具、不拆写入、不改参数规避；文件未创建，未读取/打印/外传任何真实凭据。本轮 RUN-009 严格遵守，**没有尝试重新写入该脚本或执行四个故障场景**。
- 正式解除条件：需要 AgentDR 管理员或安全负责人批准并给出受信任执行流程。当前没有批准，因此以下四个场景维持 **BLOCKED**：
  1. Worker 领取任务后退出（post_claim_exit）；
  2. 租约过期（lease_expiry）；
  3. API 重启（api_restart）；
  4. Worker 全量重启（fleet_restart）。

## 2. 本轮可独立完成且已完成的工作

### 2.1 契约级状态机与数据库一致性测试

新增 `tests/unit/test_worker_fault_contract_run009.py`，4 个测试全部通过（连同既有 `test_tasks.py` 共 14 passed）：

| 测试 | 覆盖的契约 | 结果 |
|---|---|---|
| `test_post_claim_exit_contract_reap_and_reassign` | 领取后 PREPARING → 租约过期回收 → QUEUED → 新 worker 接管；fencing 1→2；旧 worker 提交被拒 | VERIFIED |
| `test_lease_expiry_contract_monotonic_token_and_stale_renew` | 租约过期不改变 attempt；fencing 单调递增；旧 owner 续租失败 | VERIFIED |
| `test_api_restart_and_fleet_restart_contract_claim_is_idempotent_boundary` | 新建 TaskService（模拟进程重启重连）后 owner/token/状态仍持久化；旧 owner 提交被拒 | VERIFIED |
| `test_duplicate_submit_contract_stale_rejected_same_owner_recorded` | 正常状态机到 COMPLETED；旧 owner/旧 token 重复提交被拒 | VERIFIED |

这些是**契约与数据库断言**，不是真实 OS 进程故障注入，不能替代四个 BLOCKED 场景。

### 2.2 源码审计发现并修复的真实缺陷

**缺陷**：`TaskService.submit_result()` 在 Run 进入中间态 `FINALIZING` 时，将 Task 同步为 `FAILED`；后续 `COMPLETED` 提交因 Task 状态已不在 `{"RUNNING","WAITING"}` 而无法修正。正常 Pipeline 只提交终态，因此线上主链路未被触发，但该 API 契约对其它调用方不安全。

**修复**：`packages/execution/traceguard_execution/tasks/service.py` 中仅在 `execution_status in FINAL_RUN_STATES` 时同步 Task 终态；中间态保持 RUNNING/WAITING。修复后契约测试 4/4 + 既有任务测试 10/10 全绿。

**仍需注意（NEEDS_DECISION）**：`submit_result()` 对「同一 owner、同一 token、同一 execution_status」的重复终态提交未设幂等键，仍可能覆盖 verdict/report。建议后续引入提交回执 idempotency key 或终态不可变守卫；本轮未改该语义，避免影响既有 Repair/回执契约。

### 2.3 故障恢复契约清单（供安全批准后执行）

每个真实场景必须采集：Run 状态、attempt、fencing token、owner、lease、Event、Evidence、报告、重复提交结果、最终一致性。必须使用新证据目录 `docs/assets/run009/wp4/`，且每次实验保留原始样本，不得引用 RUN-008 的 SIGKILL 证据。

推荐的受信任执行路径（待批准）：由安全负责人确认环境白名单变量传递方式后，使用显式最小环境变量集合启动子进程，不读取、不打印真实凭据；执行前后核对进程 PID 与退出原因。

## 3. 状态汇总

| 项目 | 状态 |
|---|---|
| B-17 复核、保持 BLOCKED、未绕过 | VERIFIED |
| 状态机/租约/重复提交契约测试 | VERIFIED（4/4） |
| FINALIZING 提前判 Task FAILED 缺陷修复 | VERIFIED（回归 14/14） |
| 同 owner 同 token 重复终态提交幂等性 | NEEDS_DECISION |
| 四个真实进程故障场景 | **BLOCKED**（缺少安全批准的执行路径） |
