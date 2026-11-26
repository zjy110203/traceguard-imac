# RUN-016 WP2：Regression 并发与异常恢复完善

> 分支：`workbuddy/run016-finalization`
> 目标：把 RUN-015 的"线程级 10 并发"升级为**真实操作系统多进程 + PostgreSQL 16**
> 验收，并补齐 Promotion Failed 的恢复能力；INCONCLUSIVE 重试只出决策稿。

---

## 1. 现状复核（源码，不引用报告结论）

`RepairService.regression_run()` 的关键不变量：

| 不变量 | 实现 |
|---|---|
| 唯一 Claim | `create_run_once(task, version_ref, claim="repair-regression:{pid}:v{ver}:attempt1")`：认领行主键 + 同事务插入 Run |
| 单 Run | 同上；`REGRESSION_RUN_CONFLICT` 防止提案绑定到另一个 Run |
| CAS 收敛 | `_save(fresh, expected_version=version)`，冲突时回到赢家的 Run |
| Stale 动态判断 | `regression_status()` 按"当前正式版本 vs 回归版本"实时重算，不写回历史 |
| 独立 Worker 消费 | 回归只创建 Task/Run，由 Worker 领取；API 线程不冒充执行 |

结论：并发收敛的**唯一收敛点**在数据库（认领行主键），不依赖 Python 锁；
因此正确的验收方式必须是多进程 + 独立连接，而不是线程。

## 2. 新增：PostgreSQL 16 多进程专项

脚本：`scripts/regression_multiprocess_run016.py`

```
10 个请求进程（spawn，独立 Runtime / 独立连接）
  └─ multiprocessing.Barrier(11) 同步（含 1 个 Worker 进程）
       └─ 同一时刻共同请求同一个 PROMOTED 提案的 regression_run()
1 个独立 Worker 进程：claim_next_run → mark_running → record_step → submit_result(PASSED)
```

判定项（全部为硬条件）：

1. 只有一个有效 Regression Claim（`tg_regression_claim` 行数 = 1）；
2. 只有一个 Regression Run（该 Task 下 Run 数 = 1）；
3. 10 个请求返回的 `regression_run_id` 完全一致；
4. 只有一条 Regression History；
5. 每个 Run 至多一份 `tg_run_submission` 回执；
6. 无重复执行（Run 数 = 1）；
7. 新建 Runtime（等价 API 重启）仍恢复到原 Run；
8. 重复请求不创建新 Run；
9. 失败请求（不存在的提案 ID）不破坏并发赢家的状态；
10. Worker 进程确实消费了 Run。

命令：

```bash
TG_REGRESSION_PG_DB=traceguard_run016_regression \
  .venv/bin/python scripts/regression_multiprocess_run016.py \
  --procs 10 --out docs/assets/run016/wp2/regression-multiprocess.json
```

## 3. 额外覆盖（任务要求 §2.1 的补充场景）

| 要求场景 | 覆盖方式 | 结论 |
|---|---|---|
| Claim 创建成功后进程异常退出 | 请求进程超时/退出后，恢复请求重新进入 `regression_run()`：`existing_run` 命中 → 收敛到同一 Run，不新建 | PASS（脚本判定项 7/8） |
| Run 已创建但 Proposal 尚未绑定 Run ID | 并发赢家写文档前崩溃 → 其它请求 `create_run_once` 命中同一认领行，拿到同一 Run 后继续绑定 | PASS（单 Run 判定） |
| 另一个请求重新进入时的恢复 | 同上 + 新 Runtime 读取 | PASS |
| CAS 冲突 | `_save(expected_version)` 冲突 → 读取赢家文档，不覆盖状态 | PASS（历史条数 = 1） |
| Worker 执行期间 API 重启 | 新 Runtime 只读 `regression_status()` 与 `regression_run()`，Run 由独立 Worker 继续执行 | PASS |

**注意**：以上 5 项在脚本中以"最终不变量"形式断言（单一 Run / 单一 Claim / 单一历史 /
一致 Run ID），而不是伪造故障时刻；进程级 SIGKILL 的接管语义已由 WP1 长稳与
`tests/integration/test_fencing_audit_run016.py` 覆盖。

## 4. Promotion Failed 安全恢复（WP2.3）

### 4.1 修复前的真实缺口

`promote()` 只接受 `APPROVED` / `PROMOTING`；`PROMOTION_FAILED` 直接抛
`illegal repair transition`。也就是说**晋升一旦进入失败态，产品内没有任何恢复入口**，
只能手工改库——这违反"失败后必须可安全恢复"的要求。

### 4.2 新增能力

`RepairService.promotion_recovery_plan(proposal_id)`（只读）：

1. 读**当前真实正式版本**（`approved_version_number`），不信任上次请求的返回值；
2. 查本次提案的晋升回执（`find_promotion_receipt`）；
3. 重新校验人工审批（`approved_by` / `approval_ref`）与 Oracle 绑定（`_regression_oracles`）；
4. 给出唯一结论：

| action | 含义 |
|---|---|
| `NOOP_ALREADY_PROMOTED` | 已有回执或已 PROMOTED：只收敛，不重复晋升 |
| `RETRY_PROMOTE` | 前置条件全部满足，可人工确认后重试 |
| `BLOCKED_VERSION_DRIFT` | 正式版本已变（别人先晋升），重试只会再冲突 → 必须重开提案 |
| `BLOCKED_NEEDS_APPROVAL` | 缺人类审批记录，不允许绕过 |
| `BLOCKED_ORACLE` | 候选版本 Oracle 不满足，不允许绕过 |
| `BLOCKED_ILLEGAL_STATE` | 状态不在可恢复集合 |

`promote_recover(proposal_id, idempotency_key, confirm, actor)`：

- `confirm != True` → 拒绝；`actor` 必须是人类主体（`require_human_actor`）；
- `NOOP_ALREADY_PROMOTED` → `reconcile()`，不新增版本；
- `RETRY_PROMOTE` → `PROMOTION_FAILED → APPROVED`（状态机允许的边），写
  `promotion_recovery_history`，再用**新的幂等键**走原 CAS 晋升；
- 其它 action → 抛前置失败，**不改任何状态**。

API：

- `GET  /api/v1/repairs/{id}/promotion-recovery`（只读，`read` 权限）
- `POST /api/v1/repairs/{id}/promote-recover`（`repair.promote`，platform-admin）

RBAC 修正：`action_for_request()` 里 `/repairs/**/promote-recover` 原先会落到
`repair.submit`（qa-engineer 可调），已显式映射到 `repair.promote`。

### 4.3 测试

`tests/integration/test_promotion_recovery_run016.py`（7 项全部通过）：

| 用例 | 关键断言 |
|---|---|
| 版本漂移 | `BLOCKED_VERSION_DRIFT`，强行恢复被拒 |
| 显式确认/人类主体 | 缺 confirm 拒绝；`agent:bot` 拒绝 |
| 可恢复场景 | 恢复成功、只新增 1 个版本、历史记录 operator |
| 已实际晋升 | `NOOP_ALREADY_PROMOTED` → 收敛，不新增版本 |
| 缺审批 | `BLOCKED_NEEDS_APPROVAL` |
| Oracle 不满足 | `BLOCKED_ORACLE` |
| RBAC 映射 | `promote-recover` → `repair.promote` |

## 5. INCONCLUSIVE 重试

只产出决策稿：`docs/RUN-016-REGRESSION-RETRY-DECISION.md`。
本轮**未改**：`regression_retry_allowed` 仍为 `false`、`regression_retry_decision` 仍为
`NEEDS_DECISION`、未新增状态、未放开 API。

## 6. 结果与证据

- `docs/assets/run016/wp2/regression-multiprocess.json`：10 进程实测原始结果（见 §7 摘要）；
- 测试原始日志：`docs/assets/run016/wp2/promotion-recovery-tests.log`。

## 7. 实测摘要

`docs/assets/run016/wp2/regression-multiprocess.json`（2026-11-26，PostgreSQL 16.16）：

| 指标 | 实测 |
|---|---|
| 请求进程数 | 10（10 个不同 PID：89856–89865） |
| 同步屏障 | `multiprocessing.Barrier(11)`（10 请求 + 1 Worker） |
| 全部请求成功 | 是（`ok=true` ×10，进程退出码全 0） |
| 每请求耗时 | 0.333–0.428s（几乎同时返回） |
| 返回的 Run ID | 唯一：`run_179568936146587c8f2d470cd` |
| 数据库中的 Run 数 | 1 |
| `tg_regression_claim` 行数 | 1 |
| `tg_run_submission` 回执数 | 1 |
| Regression History 条数 | 1 |
| Worker 处理数 | 1（`r16-mp-w1`） |
| 新 Runtime 恢复 | 指向同一 Run |
| 重复请求 | 不创建新 Run |
| 失败请求（不存在提案） | 被拒，赢家状态不变 |
| **判定** | **13/13 PASS** |

结论：Regression 的"唯一 Claim / 唯一 Run / 唯一历史"不依赖 Python 锁，
在**真实多进程 + PostgreSQL 16** 下成立。

