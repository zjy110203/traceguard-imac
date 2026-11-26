# RUN-016 决策稿：INCONCLUSIVE 回归的重试策略

> 状态：**NEEDS_DECISION（仅提案，未改状态机）**
> 当前实现保持：`regression_retry_allowed = false`、`regression_retry_decision = "NEEDS_DECISION"`。
> 本文只回答"如果要允许重试，应该怎么做才是安全的"，不代替负责人决策。

---

## 1. 现状（源码复核，非报告转述）

`RepairService.regression_run()` 的当前契约：

- 只有 `PROMOTED` 提案可回归；
- 回归对象是 `promoted_version` 指向的**已批准正式版本**；
- 认领行主键 `repair-regression:{proposal_id}:v{promoted}:attempt1` + 同事务建 Run，
  保证并发请求只产生一个 Run（RUN-015 WP2）；
- 终态判定：`COMPLETED + PASSED + 有证据` → `PASSED`，否则按原因落 `FAILED` / `INCONCLUSIVE`；
- 终态后 `regression_run()` 直接返回原文档（幂等），**不存在任何重跑入口**；
- `regression_status()` 固定输出 `regression_retry_allowed=false`，前端不得暗示可重试。

即：**INCONCLUSIVE 目前是终局**，不是"待重试"状态。

## 2. 四种结论的语义边界

| 状态 | 触发条件 | 业务含义 | 是否应允许重跑 |
|---|---|---|---|
| `PASSED` | Run `COMPLETED` + `PASSED` + 证据非空 | 晋升版本在当前环境复现成功 | 否（重复执行只会制造噪声） |
| `FAILED` | Run `COMPLETED` + `FAILED`/`NOT_EVALUATED` + 证据充分 | 晋升版本真实回归失败，是明确结论 | 否（结论已明确，应走回滚/新提案） |
| `INCONCLUSIVE` | Oracle 缺失/未审批、证据缺失、Run 终态异常、执行超时 | **没有得出结论**，不是"失败" | 视原因而定（本稿讨论对象） |
| `STALE` | 读取时发现正式批准版本已变化，或已回滚 | 结论对当前版本不再有效 | 否（必须先重新晋升，再对新版本回归） |

关键区分：`INCONCLUSIVE` 是"证据不足"，`FAILED` 是"证据充分的反向结论"。
把二者混成一个可重试状态，会让"失败"被反复重跑掩盖。

## 3. INCONCLUSIVE 的失败原因细分

| 原因码 | 典型场景 | 是否可通过重跑解决 | 建议 |
|---|---|---|---|
| `REGRESSION_ORACLE_UNAVAILABLE` | 版本断言未绑定 Oracle / Oracle 未审批 / 固定版本被删 | **否**（配置问题，重跑同样缺） | 要求先修 Oracle 绑定，不允许重试 |
| `EVIDENCE_MISSING` | Run 完成但没有证据（采集失败、浏览器崩溃） | 通常是 | 允许一次人工授权重试 |
| `EXECUTION_TIMEOUT` | Run 超时未终态，或等待超时 | 通常是（环境抖动） | 允许一次人工授权重试 |
| `RUN_ABORTED` | Worker 崩溃/被杀、DB 断连导致 Run 非正常终态 | 通常是 | 允许一次人工授权重试 |
| `ENVIRONMENT_DRIFT` | TeamFlow 不可达、端口冲突 | 需先修环境 | 允许重试，但需记录环境自检结果 |
| `UNKNOWN` | 其他 | 不确定 | 默认不允许，需人工升级 |

## 4. 推荐方案 A：人工授权的新 Attempt（保留血缘）

### 4.1 语义

- **不改写历史**：旧 Run / 旧 Verdict / 旧 Evidence / 旧 history 一律保持不可变；
- 新 Attempt 是**独立 Run**，通过血缘字段显式指向旧 Attempt：
  - `regression_attempt_no`（从 1 递增）
  - `regression_parent_run_id`（上一 Attempt 的 Run）
  - `regression_supersedes_run_id`（同 parent，语义等价，便于查询）
- Repair 文档上的 `regression_run_id` 始终指向**最新一次 Attempt**，
  历史 Attempt 归档在 `regression_attempts[]`；
- 结论判定不变：只有新 Attempt 的 Run `COMPLETED + PASSED + 有证据` 才置 `PASSED`。

### 4.2 状态机（仅回归子状态，不动 REPAIR_TRANSITIONS）

```
INCONCLUSIVE ──(人工授权)──> RETRY_AUTHORIZED ──> QUEUED(attempt=N+1) ──> PASSED / FAILED / INCONCLUSIVE
     │
     └──(未授权 / 超过上限 / 原因不可重试)──> 保持 INCONCLUSIVE（终局）
```

- 不新增 Repair 主状态，只在 `regression_status` 子状态机内增加 `RETRY_AUTHORIZED`；
- `regression_retry_allowed` 由服务端按"原因码 + 已用次数 + 当前版本是否仍为正式版本"计算，
  不再是常量 `false`，但仍默认 `false`（未授权时）。

### 4.3 授权与边界

| 约束 | 设计 |
|---|---|
| 谁可以授权 | `repair.regression`（qa-lead / platform-admin）+ **必须人类主体**（`require_human_actor`） |
| 显式确认 | 请求体必须 `confirm: true`，缺省拒绝 |
| 次数上限 | 每个 `(proposal, promoted_version)` 最多 **2 次** Attempt（含首次），超出需新提案 |
| 原因门禁 | 只有 §3 中"可重跑"的原因码允许；`ORACLE_UNAVAILABLE` 一律拒绝 |
| 版本门禁 | 正式批准版本必须仍等于 `promoted_version`；否则要求重新晋升（返回 `BLOCKED_VERSION_DRIFT`） |
| 幂等 | 幂等键固定为 `repair-regression:{proposal_id}:v{version}:attempt{N}`，重复点击命中同一 Run |
| 审计 | 每次授权写 `regression_retry_authorizations[]`：actor、原因、旧 Run、新 Run、时间、授权前快照 |
| 前端 | 只有 `regression_retry_allowed=true` 时才渲染"重新验证"按钮；点击需二次确认 + 填写原因 |

### 4.4 数据字段（建议）

```jsonc
{
  "regression_attempt_no": 2,
  "regression_attempts": [
    {"attempt": 1, "run_id": "run_...", "status": "INCONCLUSIVE",
     "reason": "EXECUTION_TIMEOUT", "finished_at": "..."}
  ],
  "regression_parent_run_id": "run_attempt1",
  "regression_retry_allowed": true,
  "regression_retry_reason_code": "EXECUTION_TIMEOUT",
  "regression_retry_decision": "ALLOWED_WITH_AUTHORIZATION",
  "regression_retry_authorizations": [
    {"actor": "human:lead", "at": "...", "reason": "环境抖动",
     "from_run_id": "run_attempt1", "to_run_id": "run_attempt2"}
  ]
}
```

历史文档缺少这些字段时按 `attempt_no=1`、`retry_allowed=false` 解释，**不回写历史**。

## 5. 备选方案与放弃理由

| 方案 | 做法 | 放弃理由 |
|---|---|---|
| B：自动重试 | INCONCLUSIVE 自动重跑，最多 N 次 | INCONCLUSIVE 常由配置缺陷（Oracle 缺失）导致，自动重跑会掩盖真实问题并放大成本；且与"人工审批"产品语义冲突 |
| C：原地重置 | 把同一 Run 重置为 QUEUED 重跑 | 破坏 Event/Evidence 的不可变性与"每 Run 至多一份回执"不变量；审计链断裂 |
| D：新建提案 | 失败后强制重开 Repair 提案 | 成本过高（需重新沙箱 + 审批），且丢失"同一修复的验证历史"血缘 |

## 6. 需要负责人决策的问题

1. 是否采纳方案 A？若采纳，次数上限取 2 还是 3？
2. `ENVIRONMENT_DRIFT` 是否算"可重跑"（涉及是否允许在被测系统自检失败时重试）？
3. 授权是否需要**双人复核**（qa-lead 授权 + platform-admin 复核）？
4. 是否允许 `platform-admin` 单方面授权（当前 `repair.regression` 已包含）？
5. 前端"重新验证"入口是否需要在授权后仍展示旧 Attempt 的完整证据链？

## 7. 本轮已做 / 未做

- 已做：完成状态机与原因码复核；确认 `regression_retry_allowed=false` 为服务端硬编码；
  在 `docs/RUN-016-WP2-REGRESSION.md` 中记录现状与风险。
- 未做（有意）：未新增 `RETRY_AUTHORIZED` 状态、未改 `REGRESSION_TERMINAL`、
  未放开任何 API、未改前端按钮可见性。
  原因：涉及产品语义与人工授权边界，必须先由负责人决策。
