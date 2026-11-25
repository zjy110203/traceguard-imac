# RUN-012 工作日志

分支：`workbuddy/run012-product-completion`｜基线：`0ca128f`（`workbuddy/run011-r1.2-security-benchmark`）
原则：每个工作包独立提交；不删除旧测试、不弱化断言、不把 NOT_RUN / BLOCKED 写成 VERIFIED。

---

## WP0：R1.2 交付复核与 Benchmark 可信度补修

- **开始时间**：2026-11-25 16:15 CST
- **完成时间**：2026-11-25 16:29 CST

### 修改内容

1. 新增 `scripts/bench_scenario_contract.py`：23 场景显式契约（期望结果 / 控制类别 / 故障触发条件）、五分类 + 正常负样本标签、TriState 触发证据、历史 9 条异常逐条分类表、契约校验器。
2. `scripts/bench_v5_run011_r1.py`：`V5_SCENARIOS` 由契约生成；新增 `enrich_record()` 与 `_exception_record()`，异常路径保留 GT / 故障 / TestCase / run_id / 诊断状态 / 错误细节；统计新增 `record_classification_counts`、`false_pass_evaluation`、`contract_problems` 等字段，严格 False Pass 口径要求故障触发证据，412 不再自动排除。
3. `scripts/bench_pilot_run011_r1.py` + `scripts/full_loop_run011.py`：新增 `TeamFlow.fault_state()`；`run_scenario()` 抓取真实故障 state 快照，并把 412 诊断异常包装为保留上下文的 `ScenarioExecutionError`。
4. 新增 `tests/unit/test_benchmark_scenario_contract_run012.py`（14 项：需求指定的 10 类 + 历史 9 条拆分 + PARTIAL 状态 + 契约完整性 + 上下文保留）。
5. 新增 `docs/RUN-012-WP0-BENCHMARK-ERRATA.md`、`docs/assets/run012/wp0/scenario-contracts.json`（原始归档未修改）。

### 测试结果

- Benchmark 专项（新增 14 + R1.2 原 9 + Evaluator 2）：**25 passed**。
- `ruff check` 改动文件：All checks passed；新增文件 `ruff format` 通过。
- 原始归档 SHA-256 前后一致（`v5-2rounds.json` `d99bc2e…`、`pilot-3scenarios.json` `2e96e24…`）。
- 历史 False Pass 指标如实标记 **PARTIAL**（0/37，证据等级 INFERRED_ONLY），未为维持 0/37 强行归类。
- 当前提交：`44cfb46a580ec33e80e77820d8277e68baf62c2e`。

### 剩余问题 / 下一项

- 未重跑 23×2 真实 Benchmark（成本与独立部署环境约束本轮暂不做；Errata 明确历史状态）。
- 下一项：WP1 — 修复 CI 分支触发规则，检查 PostgreSQL 16 专项，尝试真实 CI 触发并如实记录证据。

---

## WP1：CI 与 PostgreSQL 16 真实验收

- **开始时间**：2026-11-25 16:30 CST
- **完成时间**：2026-11-25 16:38 CST

### 修改内容

1. `.github/workflows/pr-gate.yml`：新增 `workbuddy/run011-*` / `workbuddy/run012-*` push 触发；纳入 R1.2 审核门禁与全局观测 RBAC 两个安全专项。
2. `.github/workflows/nightly-gate.yml`：新增 PG Workspace 隔离 / 权限过滤 / 审计链 / 审核 CAS 专项步骤。
3. 修复 CI 格式门禁基线失败：对 29 个既有文件执行机械格式化（无断言/语义变化）。
4. 新增 `tests/integration/test_postgres_workspace_run012.py`（3 项 PG 专项）与 `docs/RUN-012-WP1-CI-POSTGRES.md`。

### 测试结果

- PG 16.16 隔离库 `traceguard_run012_wp1`：parity 5 + platform 8 + 新增 3 = **16 passed / 0 skipped**。
- PR Gate 后端本地复现：**416 passed**；`ruff check` / `ruff format --check` 全绿。
- GitHub Actions：分支推送后 PR Gate **真实触发并成功** — run `45578101070`，head SHA `57a5ea7c2affe36fb9647506d067f0acc08e8265`，`conclusion=success`（2026-11-25 08:39:45Z）；Nightly 需 `workflow_dispatch`，无凭据仍 NOT_RUN。

### 剩余问题 / 下一项

- Nightly / Release Gate 仍未真实触发，保持 NOT_RUN。
- 下一项：WP2 — 建立前端统一 Workspace 上下文并接入全部视图。

---

## WP2：多 Workspace 产品功能

- **开始时间**：2026-11-25 16:39 CST
- **完成时间**：2026-11-25 16:55 CST

### 修改内容

1. 新增 `apps/web/src/workspace.ts`：服务端授权列表、偏好校验、失效回落、空状态、generation 缓存清理。
2. `App.vue`：工作空间切换控件、全局搜索/导航统计随空间刷新、`:key` 强制视图重挂载、390px 顶栏溢出修复。
3. 8 个视图接入 `wsPath()` / 当前空间写入；清除全部 `workspace_id=default` 硬编码。
4. 新增 `scripts/workspace_ui_acceptance_run012.py` 与 `docs/RUN-012-WP2-WORKSPACE-UI.md`。

### 测试结果

- 真实 Chromium + 真实 API + 真实 SQLite：**25 passed / 0 failed**（两空间 × 三角色 + admin、伪造切换、localStorage 篡改、伪造请求、四分辨率深色截图）。
- `npm run build`（vue-tsc + vite）通过；390px 横向溢出 182px 缺陷已修复并复验为 0。

### 剩余问题 / 下一项

- 实体详情类视图依赖服务端对象级授权（已由 R1.1 覆盖），本轮未重复改造。
- 下一项：WP3 — `/system/metrics` 全局统计的 Workspace 过滤与真实聚合计数。

---

## WP3：全局数据权限与统计口径

- **开始时间**：2026-11-25 17:55 CST
- **完成时间**：2026-11-25 18:06 CST

### 修改内容

1. `apps/api/traceguard_api/routers/system.py` `/system/metrics`：接入 `principal_of` / `scope_filter` / `visible_workspaces`，显式越权空间 403、普通用户只统计授权空间并集、仅 platform-admin 得全局汇总；计数改 SQL `COUNT(*)` / `GROUP BY`，移除 `limit=500` 列表长度推断；response 声明 `scope` 与 `counting` 口径。
2. `apps/api/traceguard_api/routers/execution.py` `/queue/stats`：同样改 SQL 分组聚合并声明 `counting.list_limit=None`，修复 >500 条截断失真。
3. `apps/web/src/views/SystemView.vue`：类型化 `SystemMetrics`，"数据统计"卡片展示统计范围与计数口径。
4. 新增 `tests/integration/test_system_metrics_workspace_run012.py`（11 例）与 `docs/RUN-012-WP3-GLOBAL-METRICS.md`。

### 测试结果

- WP3 专项：**11 passed**（含 522 条 intents 突破旧 500 截断阈值的回归）。
- 全量：**738 passed, 16 skipped**（PG 专项需 `TG_TEST_POSTGRES_URL`，WP1 已真实验收）。
- `ruff check` 通过；`npm run build` 通过。

### 剩余问题 / 下一项

- `/inbox/stats`、`/probes/audit`、`/events/conflicts` 维持 RUN-011-R1.2 的 `system.observe` admin 专属口径；`/benchmark/*` 无 Workspace 归属实体，未改。
- 下一项：WP4 — 核心业务 UI 闭环（Intent→TestCase→Oracle→Run→Diagnosis→Repair→Rollback 全链操作入口与状态反馈）。

---

## WP4：核心业务 UI 闭环

- **开始时间**：2026-11-25 18:10 CST
- **完成时间**：2026-11-25 20:44 CST

### 修改内容

1. `apps/web/src/views/TestCaseDetailView.vue`：新增 Oracle 绑定控件与保存绑定（补齐审核门禁的前置 UI 入口）；修正版本列表 `doc` 未展开导致断言表格整体不渲染的缺陷；新增「启动稳定性回放」入口。
2. `apps/web/src/views/TestCasesView.vue`：创建用例不再硬编码 `base_url`，改从 `/system/config` 读取服务端配置的目标地址。
3. `apps/web/src/views/RepairsView.vue`：提案弹窗内新增「LLM 建议」入口（未配凭据时如实提示，不伪造成功）。
4. `packages/execution/traceguard_execution/runtime/pipeline.py` 与 `playwright_observer.py`：默认目标地址统一取 `Settings.teamflow_base_url`，不再按 application/端口硬编码。
5. 新增 `scripts/business_loop_ui_acceptance_run012.py` 与 `docs/RUN-012-WP4-BUSINESS-LOOP-UI.md`。

### 测试结果

- **真实浏览器闭环验收：29 passed / 0 failed**（真实 Chromium + 真实 API + 真实 TeamFlow + 真实 Worker + 真实浏览器执行），
  覆盖 Intent→TestCase→Oracle→绑定→审核→执行→故障注入→诊断→修复→沙箱→审批→晋升→回滚全链，每步均服务端回读。
- 全量：**738 passed, 16 skipped**；`ruff check`/`format` 通过；`npm run build` 通过。
- 一次 `test_migration_v8` 并发迁移用例偶发失败（Barrier 时序敏感），单独复跑与后续两次全量均通过，判定为基线时序敏感。

### 剩余问题 / 下一项

- compiler/healed 用例的「稳定性回放→审核」完整成功路径需多轮真实浏览器执行，本轮只验证入口可用；真实 LLM 模型输出路径待 WP5 覆盖。
- 下一项：WP5 — AI 可靠性升级（Diagnosis Advisor JSON 解析、Schema、重试/Fallback、追踪与离线测试）。
