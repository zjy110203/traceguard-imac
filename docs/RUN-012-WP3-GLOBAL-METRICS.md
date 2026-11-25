# RUN-012 WP3：全局数据权限与统计口径

**日期**：2026-11-25 18:06 CST
**分支**：`workbuddy/run012-product-completion`

## 1. 问题定位

| 问题 | 位置 | 旧行为 | 风险 |
|---|---|---|---|
| `/system/metrics` 无 Workspace 过滤 | `apps/api/traceguard_api/routers/system.py` | 任意登录用户拿到**全局** runs/evidence/events/intents/testcases 计数 | 越权观测：普通用户可推断其他 Workspace 的业务规模与 Run verdict 分布 |
| 计数靠列表长度推断 | 同上 | `list_runs(limit=500)` / `docs.list(limit=500)` 后 `len()` | 超过 500 条即截断失真，计数静默偏低 |
| `/queue/stats` 同样截断 | `apps/api/traceguard_api/routers/execution.py` | `list_runs(limit=500)` 后按状态计数 | 队列规模 >500 时 by_status/total 低估 |

## 2. 修复内容

### 2.1 `/system/metrics`（system.py）

- **权限口径**：
  - 显式 `workspace_id` → 统一走 `scope_filter()`，非授权空间 **403 WORKSPACE_FORBIDDEN**；
  - 不传 `workspace_id`：普通用户只统计**授权空间并集**，`platform-admin` 才得到全局汇总；
  - 与 RUN-011-R1.1 的对象级授权出口一致，路由不自行发明授权方式。
- **计数口径**：全部 `SELECT COUNT(*)` / `GROUP BY test_verdict` 直接查 `tg_run` / `tg_evidence` / `tg_event` / `tg_doc` / `tg_testcase` / `tg_intent`，彻底移除 `limit=500` 列表长度推断。
- **透明度**：response 新增
  - `scope`：`mode`（`workspace` / `authorized_workspaces` / `global`）、`workspace_id`、`authorized_workspaces`、`global`；
  - `counting`：`source`、`list_limit: null`、`time_range: "ALL_TIME"`、`verdict_definition`。

### 2.2 `/queue/stats`（execution.py）

- 改为 `SELECT execution_status, COUNT(*) FROM tg_run [WHERE workspace_id IN :scope_ids] GROUP BY execution_status`；
- 保留 `scope_filter()` 作用域（授权空间并集 / admin 全局）；
- response 新增 `counting`（`list_limit: null`）。

### 2.3 前端 `SystemView.vue`

- 类型化 `SystemMetrics`（含 `scope` / `counting`）；
- "数据统计"卡片底部展示统计范围与计数口径，用户可直接回读"我看到的是哪个空间、用什么口径数的"。

## 3. 测试

新增 `tests/integration/test_system_metrics_workspace_run012.py`（11 例）：

| 用例 | 断言 |
|---|---|
| `test_metrics_requires_auth` | 匿名 401（显式清 Cookie，防会话串扰） |
| `test_metrics_forbidden_for_unauthorized_explicit_workspace` | 越权 `workspace_id` → 403 `WORKSPACE_FORBIDDEN` |
| `test_metrics_explicit_workspace_counts_only_that_workspace` | 指定空间只计该空间（2 intents / 3 testcases） |
| `test_metrics_authorized_union_for_normal_user` | 双空间授权用户 = 并集（7 / 4），`mode=authorized_workspaces` |
| `test_metrics_single_scope_user_sees_only_own_workspace` | 单空间用户只见自己空间 |
| `test_metrics_global_only_for_platform_admin` | 仅 admin `mode=global`，`authorized_workspaces=None` |
| `test_metrics_counting_metadata_declares_sql_aggregation` | `list_limit=None`、source 含 COUNT、verdicts 为字典 |
| `test_metrics_exceeds_legacy_500_truncation_threshold` | 造 522 条 intents，计数 522（>500 证明非截断） |
| `test_queue_stats_workspace_scoped_aggregation` | 队列计数按授权空间分组（3 = 2 QUEUED + 1 RUNNING） |
| `test_queue_stats_global_for_admin` | admin 全局 5 条、三状态分布正确 |
| `test_queue_stats_multi_workspace_union` | 双空间授权用户并集 5 条 |

夹具造真实 `tg_run` 行（WS_A 3 条 / WS_B 2 条）并清除启动种子（default 空间）避免污染全局断言。

**结果**：
- 专项：`11 passed`；
- 全量：`738 passed, 16 skipped`（PG 专项需 `TG_TEST_POSTGRES_URL`，WP1 已在真实 PG16 跑过）；
- `ruff check` 通过；`npm run build`（vue-tsc + vite）通过。

## 4. 残留与边界

- `/api/v1/inbox/stats`、`/api/v1/probes/audit`、`/api/v1/events/conflicts` 属 RUN-011-R1.2 的 `system.observe` 全局观测面，仅 platform-admin 可读，本轮不改；
- `/api/v1/benchmark/*` 为基准实验元信息（无 Workspace 归属实体），维持现状；
- `/api/v1/queue/reap` 为写操作，映射 `system.configure`（fail-closed 给 admin），未纳入本轮统计改造。
