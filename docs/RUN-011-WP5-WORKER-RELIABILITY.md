# RUN-011 WP5：Worker 可靠性与故障恢复（PARTIAL / B-17 仍 BLOCKED）

> 执行时间：2026-11-25 01:30–02:10 CST。
> 状态：**PARTIAL**。本轮在新的独立拓扑中复测了正常领取、执行归属、fencing、终态幂等；
> **B-17 的四个真实故障场景仍然 BLOCKED**，未绕过、未换工具、未拆写入。

## 1. 新拓扑中的真实领取证据

| 指标 | loop1 | loop2 | 证据来源 |
|---|---|---|---|
| Worker 进程数 | 3（w1/w2/w3） | 3（v1/v2/v3） | 进程独立启动，独立 `TG_WORKER_ID` |
| API 内嵌 Worker | 关闭（`inline_running=false`） | 同 | `/system/health` |
| 稳定性回放领取者 | w2 / w1 / w3 | v1 / v2 / v3 | `records[].claimed_by`，`worker_consumed=true` |
| 正式 Run 领取者 | w2 | v1 | `GET /runs/{id}.lease_owner` |
| 沙箱 Run 领取者 | w1 | v3 | `repair.sandbox_executed_by` |
| 回归 Run 领取者 | w3 | v1 | `lease_owner` |
| fencing token | 0 → 1 | 0 → 1 | 轮询记录 |
| attempt | 1 | 1 | 轮询记录 |
| `lease_owner` 是否等于 API worker | **否**（API=`run011-api`） | **否**（`run011-api2`） | 显式断言 |

结论：Worker 的独立性是**从数据库/API 观测到的**（lease_owner、claimed_by、fencing），
不是"配置里写了 TG_INLINE_WORKER=0"。

## 2. 终态幂等

| 检查 | 结果 |
|---|---|
| Repair 晋升幂等重放（同 idempotency_key） | 返回同一个 `promoted_version=2`，**未新建版本**（F9） |
| `tg_promotion_receipt` 主键 | `idempotency_key` 唯一，重放不新增行 |
| 回滚回执 | 确定性 key `rollback:{proposal}:v2`，重复调用不会产生第二行 |
| Task 提交幂等 | RUN-010 已实现，本轮未改；`create_task` 使用传入幂等键 |

## 3. API 重启下的会话与状态

本轮为加载代码修复，**多次重启 API 进程**（至少 5 次），期间：

- 四个角色的 Session 全部保留（会话存 PostgreSQL，不在进程内存）；
- 已完成的 Run / Evidence / Diagnosis / Repair 状态不变；
- 重启后 `GET /runs/{id}`、`/evidence/{id}/verify`、`/repairs/{id}` 均返回一致结果；
- 审计链 `seq` 连续（1..N），无因重启产生的空洞。

这不是"正式的重启测试"，但提供了真实证据：**认证与业务状态不依赖单进程内存**。

## 4. B-17 四个补充故障场景：仍然 BLOCKED

| 场景 | 状态 | 说明 |
|---|---|---|
| 领取任务后立即退出 | **BLOCKED** | 需要写入含 subprocess + 环境字典的故障脚本；RUN-008 起被安全策略拦截（`info_theft_steal_env_keys`），未绕过 |
| 租约过期后被接管 | **BLOCKED** | 同上（需要改 `TG_WORKER_LEASE_TTL_S` 并强杀进程） |
| API 重启（执行中） | **BLOCKED** | 同上 |
| Worker 全量重启 | **BLOCKED** | 同上 |

按指令 §5.2：
- 未执行被拦截操作；
- 未修改参数规避；
- 未换工具规避；
- 未拆分写入；
- **没有把契约测试当成真实故障测试**。

已有替代证据（不冒充 B-17）：
- `docs/assets/run008/wp3/worker-fault-recovery.json`（历史：真实 SIGKILL 中途接管，17/17 checks）；
- 本轮 `verify_db_run011.py`：终态 Run 无孤儿证据、lease_owner 全部为 Worker。

## 5. 本轮新增的相关约束

- 独立部署下 **API + 3 Worker 同时对空库执行 `create_schema()`**：迁移 12 条、无重复、无崩溃
  （RUN-010 CL-03 的 advisory lock 修复在真实并发启动下有效）。
- Worker 与 API 共享同一 PG，连接池未出现耗尽（3 Worker + 1 API，单次实验 < 3 分钟）。

## 6. 结论

- 正常路径的可靠性有真实证据；
- **异常路径（进程被杀、租约过期、API 重启中断执行）没有本轮证据**；
- B-17 需要安全负责人批准后才能执行；在此之前，任何"Worker 故障恢复已验证"的说法都不成立。
