# RUN-014 WP1：Repair → Promotion → Regression → Rollback 真实闭环

> 分支：`workbuddy/run014-product-closure`｜基线：`85f18c1`（RUN-013 HEAD）
> 结论：**Regression 已成为独立的真实执行阶段**；Promotion 失败不再被前端误判为成功；
> Regression 通过只由真实 Run（COMPLETED + PASSED + 有证据）决定。

---

## 1. 审查发现的真实缺陷

| # | 位置 | 缺陷 | 严重度 |
|---|---|---|---|
| D1 | `apps/web/src/views/RepairsView.vue` `lifecycle` | Promotion 与 Regression 使用**同一个** `promoted` 条件，回归永远与晋升同步"完成" | P0（业务语义错误） |
| D2 | 同上 | `!!Object.keys(promotion_record).length` 把 `VERSION_CONFLICT` 失败记录也当作晋升成功 | P0 |
| D3 | `RepairService` | 完全没有 Regression：`PROMOTED` 直接是终点，只能回滚 | P0（功能缺失） |
| D4 | 同上 | 沙箱通过即 `PENDING_APPROVAL`，晋升后无任何"真实版本再验证"手段 | P1 |
| D5 | 同上 | 列表把 `base_version+1` 当作"目标版本"展示，未晋升时也是"v2"，暗示已晋升 | P2 |
| D6 | `auth.py` | 新增加回归动作需要独立 RBAC；否则会落到 `repair.submit`（qa-engineer） | P1 |

## 2. 修复方案（最小改动，复用现有 Run/Worker 机制）

### 2.1 数据模型：Regression 作为独立状态

不新造执行框架。`repair` 文档新增字段（与既有 `sandbox_status` 同构，独立于生命周期）：

```
regression_status      NOT_STARTED | QUEUED | RUNNING | PASSED | FAILED | INCONCLUSIVE | BLOCKED
regression_run_id      / regression_task_id / regression_testcase_version_id
regression_verdict     / regression_started_at / regression_finished_at
regression_failure_reason / regression_evidence_ids / regression_oracle_refs
regression_history[]   / regression_stale
```

生命周期状态机不变（`REPAIR_TRANSITIONS` 未放宽）：晋升仍是 `APPROVED → PROMOTING → PROMOTED`，
回滚仍是 `PROMOTED → ROLLED_BACK`。这样不会为了"多一个阶段"破坏既有 CAS/幂等语义。

### 2.2 真正的 Regression Run

`RepairService.regression_run()`：

1. 仅 `PROMOTED` 可回归（否则 412 `REGRESSION_REQUIRES_PROMOTED`）；
2. 取**实际晋升版本** `promoted_version`，回读版本文档，校验存在 / Workspace 一致 / `APPROVED`；
3. 服务端核验该版本绑定的 Oracle（固定版本必须存在且已审批）——任何缺失/未审批直接
   `INCONCLUSIVE` + `REGRESSION_ORACLE_UNAVAILABLE`，**不创建 Run**（fail-closed）；
4. `Task(mode=playwright, workspace=提案 Workspace)` + `Run(testcase_version_id=tcase@promoted)`，
   由**现有独立 Worker** 消费队列执行（API 线程只等待，不冒充 Worker）；
5. 收敛真实结果：`COMPLETED + PASSED + evidence_ids 非空` → `PASSED`；`verdict=FAILED` → `FAILED`；
   其余（执行失败/证据缺失/INCONCLUSIVE/NOT_EVALUATED）→ `INCONCLUSIVE`；
6. 超时不伪造成功：保持 `RUNNING`，由后续轮询（同一 Run）收敛；
7. 幂等：终态直接返回；在途只收敛**同一个** `regression_run_id`；Task 幂等键为
   `repair-regression:{proposal_id}:v{promoted}`，崩溃恢复会复用已存在的 Run，不重复创建。

判定矩阵：

| Run execution_status | Run test_verdict | Evidence | Regression |
|---|---|---|---|
| COMPLETED | PASSED | ≥1 | **PASSED** |
| COMPLETED | PASSED | 0 | INCONCLUSIVE（`REGRESSION_EVIDENCE_MISSING`） |
| COMPLETED | FAILED | — | **FAILED**（`REGRESSION_ASSERTION_FAILED`） |
| COMPLETED | INCONCLUSIVE / NOT_EVALUATED | — | INCONCLUSIVE |
| FAILED / TIMED_OUT / CANCELLED | 任意 | — | INCONCLUSIVE（`REGRESSION_RUN_*`） |
| 未终态 | — | — | RUNNING（不完成） |
| Oracle 不可用 | 未执行 | — | INCONCLUSIVE（`REGRESSION_ORACLE_UNAVAILABLE`） |

### 2.3 结论时效与回滚

- `regression_stale`：收敛时若当前 `APPROVED` 版本已不是本提案晋升的版本 → `true`；
  回滚后也置 `true`（原结论不再适用于当前正式版本）。
- 回滚仍走既有 `rollback_version_cas`（CAS + 幂等回执），失败后的人工回滚入口保留；
  回归失败/未执行都不能自动回滚，必须具名人类操作。

### 2.4 前端

- 生命周期 7 段全部来自服务端字段，支持 `done / current / fail / todo` 四态：
  - Promotion：`promoted_version>0 && status∈{PROMOTED,ROLLED_BACK} && promotion_record.check=CAS_OK`；
  - Regression：仅 `regression_status===PASSED`；`FAILED/INCONCLUSIVE` 显示红色 fail；
  - Rollback：仅 `rollback_record.restored_version>0`。
- 新增「回归验证」面板：回归 Run 链接、版本、Verdict、耗时、证据数、Oracle 明细、
  失败原因、结论时效（STALE）、是否允许回滚、历史记录。
- 列表新增「回归」列；未晋升时不再展示虚构的 `v2`。
- 不再只靠 Toast：所有状态变化后重新从服务端拉取整份提案。

### 2.5 RBAC

`repair.regression`（先于 `repair.submit` 匹配）：`POST /repairs/{id}/regression-run` →
仅 `qa-lead` / `platform-admin`；`GET /repairs/{id}/regression` 是只读（`read`），四角色可读。

## 3. 测试与证据

命令：

```bash
python -m pytest tests/integration/test_repair_regression_run014.py -q
# 13 passed in 4.74s
```

| 验收项 | 用例 | 结果 |
|---|---|---|
| 1 正常 Sandbox→Approval→Promotion | `test_promotion_success_does_not_imply_regression` | PASS |
| 2 晋升成功但未回归 | 同上（`regression_status=NOT_STARTED`，无 run_id） | PASS |
| 3 回归成功 | `test_regression_passed_uses_real_run` | PASS |
| 4 回归失败 | `test_regression_failed_then_manual_rollback` | PASS |
| 5 回归 INCONCLUSIVE | `test_regression_inconclusive_is_not_passed` | PASS |
| 6 执行中刷新 | `test_regression_inflight_refresh_then_settle_without_duplicate_run` | PASS |
| 7 重复提交幂等 | `test_regression_repeat_submit_is_idempotent`（3 次调用 1 个 Run） | PASS |
| 8 Promotion CAS 冲突 | `test_promotion_cas_conflict_never_reports_success` | PASS |
| 9 PROMOTION_FAILED 不显示成功 | 同上（`promoted_version` 缺失 + 前端同构断言） | PASS |
| 10 未执行不显示完成 | 用例 2 + 前端 `lifecycle` 同构条件 | PASS |
| 11 失败后人工回滚 | `test_regression_failed_then_manual_rollback` | PASS |
| 12 回滚版本与回执 | 同上（restored_version=3，target=v2） | PASS |
| 补充 Oracle 不可用 fail-closed | `test_regression_oracle_missing_fail_closed` | PASS |
| 补充 证据缺失 fail-closed | `test_regression_without_evidence_is_fail_closed` | PASS |
| 补充 Run 执行失败不通过 | `test_regression_run_failure_is_not_passed` | PASS |
| 补充 RBAC 映射 | `test_regression_endpoint_permission_mapping` | PASS |

"Worker" 用真实 `TaskService`（`claim_next_run → mark_running → submit_result`）驱动，
不是直接改库：fencing、幂等回执、终态冻结都走生产代码路径。
真实浏览器 + 真实独立 Worker 的正向/失败回归链路在 WP4 验收（`docs/RUN-014-WP4-FULL-UI-LOOP.md`）。

回归影响面：`test_healing / test_repair_atomic_cas / test_repair_crash_recovery /
test_repair_lineage / test_repair_receipt_identity / test_auth_rbac_run010 /
test_global_observation_rbac_run011_r1_2` + 本文件 = **101 passed**。

前端：`npm run build`（vue-tsc + vite）通过；Ruff check/format 通过。

## 4. 未覆盖/保留风险

1. `PROMOTION_FAILED` 后没有"重新发起晋升"的 API（状态机允许 `PROMOTION_FAILED → APPROVED`，
   但无入口）：UI 如实显示失败，不伪造成功。属产品决策项。
2. 回归只验证"晋升版本可被真实执行且 Oracle 仍成立"，不做与基线版本的 A/B 差异对比。
3. `regression_stale` 在读取时不重算（只在收敛/回滚时落库），如需实时展示应改为派生字段。
