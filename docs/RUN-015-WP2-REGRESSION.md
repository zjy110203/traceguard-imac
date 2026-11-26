# RUN-015 WP2：Regression 并发幂等与状态时效

> 分支：`workbuddy/run015-execution-consistency`｜状态：**PASS（重试策略为 NEEDS_DECISION）**

---

## 1. 修复的实际问题

### 1.1 并发创建重复 Run

原流程：读 Repair 文档 → `create_task`（Task 幂等键可复用）→ `list_runs` → 必要时 `create_run` → 保存 `regression_run_id`。
两个请求同时看到 `NOT_STARTED` + 空 run_id + 已存在 Task + Task 尚无 Run 时，两边都会 `create_run`。

修复（不使用 Python 锁，支持多进程）：

1. `models.py` 新增 `RegressionClaimRow`（`tg_regression_claim`），主键为确定性 claim key
   `repair-regression:{proposal_id}:v{promoted}:attempt1`；
2. `TaskService.create_run_once()` 在**同一事务**内插入 Claim 行与 Run 行。
   唯一主键保证只有一个事务提交；PG 下等锁后唯一冲突、SQLite 下唯一约束冲突，
   落败事务整体回滚（其 Run 插入一并回滚），不会留下孤儿 Run；
3. `TaskService.create_task()` 捕获 `IntegrityError`，并发插入时复用赢家 Task 并校验 workspace；
4. Repair 文档写入使用 `DocStore.put(version=...)` CAS：
   - 注册 Regression Run 时按版本 CAS，冲突方回到赢家文档并收敛到同一 Run；
   - `_settle_regression()` 重新读取权威文档后 CAS，且对同一 `(run_id, status)` 历史项去重，
     保证同一 Run 只有一个结论、重复请求不追加多条历史。

### 1.2 Regression Stale 只在收敛/回滚时落库

原实现：`regression_stale` 只在 `_settle_regression()` / 回滚时写入；
别人晋升新版本后，v2 的旧结论仍显示"新鲜"。

修复：`RepairService.regression_status()` 在读取时按
`regression_testcase_version_id` 与 `catalog.approved_version_number()` 重算：

- 版本不一致 → `regression_stale=true`，`regression_stale_reason=APPROVED_VERSION_MOVED:{old}->{new}`；
- 回滚/已落库标记 → `ROLLED_BACK_OR_MARKED_STALE`；
- 只改展示字段，**不写回 Repair 文档、不改原始 Regression Run 的 Verdict**；
- 前端 `RepairsView.vue` 已有 `selected.regression_stale` 徽标，无需改 UI。

### 1.3 INCONCLUSIVE 重试语义

Oracle 缺失与证据缺失都会进入 INCONCLUSIVE；终态重复请求直接返回旧结果。
是否允许"Oracle 后补批准 / 证据补齐后重新执行"、是否需要新的 Regression Attempt、
旧 Run 与证据是否保留，涉及状态机与人工授权。

**结论：NEEDS_DECISION，本轮不擅自改变状态机。** 只读状态接口新增：

- `regression_retry_allowed: false`
- `regression_retry_decision: "NEEDS_DECISION"`

前端不得暗示可重试；任何重试入口需负责人决定后另开工作包。

---

## 2. 修改文件

| 文件 | 变更 |
|---|---|
| `packages/persistence/traceguard_persistence/models.py` | 新增 `RegressionClaimRow` |
| `packages/execution/traceguard_execution/tasks/service.py` | `create_task` 并发幂等；`create_run` 抽出事务内 helper；新增 `create_run_once` |
| `apps/api/traceguard_api/services/repair_service.py` | 唯一认领 + 文档 CAS + 读取时 stale 重算 + NEEDS_DECISION 字段 |
| `tests/integration/test_repair_regression_run015.py` | 5 项专项测试 |
| `docs/assets/run015/wp2/regression-run015-raw.txt` | 原始 pytest 输出 |

---

## 3. 测试证据

原始输出：`docs/assets/run015/wp2/regression-run015-raw.txt`（5 passed）。

| 用例 | 验证点 |
|---|---|
| `test_ten_concurrent_regression_requests_create_one_run` | 10 线程同时请求 → 同一 Run ID、同一 PASSED 结论；Claim 表仅 1 行；Run 表仅 1 行；历史仅 1 条；新建 Runtime 重启后仍指向同一 Run |
| `test_repeated_request_returns_same_result` | 重复请求返回同一 Run 与状态 |
| `test_regression_failure_and_inconclusive_are_distinct` | FAILED 与证据缺失 INCONCLUSIVE 区分；重试字段为 NEEDS_DECISION |
| `test_stale_recomputed_when_another_user_promotes_new_version` | v2 回归 PASSED → 他人晋升 v3 → 读取显示 `APPROVED_VERSION_MOVED:2->3`，原 Verdict 不变 |
| `test_rollback_marks_stale_and_keeps_history` | 回滚后 stale，历史保留 |

影响面：`pytest tests/unit tests/integration/test_repair_regression_run014.py tests/integration/test_repair_regression_run015.py tests/integration/test_repair_lineage_run011_r1_1.py tests/integration/test_sandbox_worker_queue.py tests/integration/test_worker_fencing_run015.py tests/integration/test_fencing_pg_run015.py -q` → **407 passed**（无新增 skip）。

---

## 4. 验收对照

- 并发请求不产生重复 Run：**PASS**（10 并发，DB 级唯一认领）
- 正常回归通过 / 回归失败 / INCONCLUSIVE：**PASS**
- 回归过期 / 回滚后结论过期：**PASS**（读取时重算，不改原始证据）
- 重复请求：**PASS**
- API 重启恢复：**PASS**（同 DB 新建 Runtime）
- 多用户并发操作：**PASS**（10 线程各自构造 Service 实例，CAS 收敛）
- INCONCLUSIVE 重试策略：**NEEDS_DECISION**（如实记录，未改状态机）
