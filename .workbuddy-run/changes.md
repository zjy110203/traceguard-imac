# 代码修改记录

- 更新：2026-11-21 23:45

格式：`时间 | 文件/范围 | 动作 | 目的`

## RUN-001

- 23:45 | `.workbuddy-run/*` | 新增 | 建立八份长期运行记录基线文件
- 23:45 | 项目根目录 | 只读审查 | 确认真实起点、安全边界、资料完整性、Git 状态与环境约束

## RUN-001 阶段0
- 23:45 | 项目根 | git init + 分支 workbuddy/traceguard-autonomous | 安全基线
- 23:50 | packages/core/* | 新增 config/logging/errors/ids/database | 统一基础设施（含日志脱敏、DSN 口令遮蔽）
- 23:52 | packages/contracts/* | 新增 C01-C12 模型 + 导出脚本 + 22 Schema | 契约单一事实来源
- 23:55 | apps/api/* | 新增 FastAPI 入口 + 健康检查 + SSE 占位 | 平台入口
- 23:58 | apps/web/* | 新增 Vue3+TS 骨架 | 工作台前端
- 00:05 | infra/ + .github/ | Docker Compose + PR Gate CI | 部署与门禁
- 00:10 | docs/* | quickstart/安全基线/兼容矩阵/契约治理 | 文档
- 修复：`Settings.public_dict()` DSN 口令遮蔽；`OracleSpec.approved_by_agent` 改 Literal[False]
- 提交 c9d3a45

## RUN-001 阶段1
- 00:15 | apps/teamflow/* | 新增 models/api/faults/ground_truth/web/main | TeamFlow 被测应用
- 00:20 | benchmark/scenarios/ | 场景元数据 | C12 基础积累
- 00:22 | tests/teamflow/* | 新增 9 测试 | 阶段1 验收
- 修复：`create_project` 直接调用时 Header 对象归一为 str
- 提交 fc1397c

（后续每次重要修改追加）

## RUN-006（2026-11-23）

- `packages/core/.../security/target_policy.py` | 新增 | 统一目标访问策略（origin 白名单/重定向/内网阻断）
- `packages/execution/.../browser/session.py` | 修改 | context route guard、blocked_requests、落地复核入口
- `packages/execution/.../runtime/playwright_observer.py` | 修改 | navigate/click 目标校验 + 落地复核 + blocked_requests 入证据
- `packages/agents/.../gateway/tool_gateway.py` | 修改 | 复用 TargetPolicy、`detail` 字段、落地复核审计
- `apps/api/.../services/exploration_service.py` | 修改 | Explorer 与 Runner 共用同一策略
- `packages/evidence/.../probe/feedback.py` | 新增 | 探针证据 → 支持/反驳/排除的确定性映射
- `packages/evidence/.../probe/planner.py` | 修改 | 预算 fail-closed、CAS reserve、BudgetUnavailable
- `packages/evidence/.../diagnosis/engine.py` | 修改 | EXCLUDED、unresolved_conflicts、LLM 前缀降级修复
- `apps/api/.../services/probe_service.py` | 修改 | 探针回灌、冲突/停止原因映射、图刷新、LLM 诚实标注
- `apps/api/.../services/diagnosis_service.py` | 修改 | reassess()、Disposition 刷新、冲突落库
- `apps/api/.../services/graph_service.py` | 修改 | ProbeExecution 节点/边
- `packages/persistence/.../catalog.py` | 修改 | bind_oracles、审核门禁、版本固定、原子晋升/回滚、主体校验
- `packages/persistence/.../db.py` + `models.py` | 修改 | 迁移 v7（review_revision）
- `packages/contracts/.../models.py` + schemas | 修改 | `AssertionSpec.oracle_version` + 重新导出
- `apps/api/.../services/repair_service.py` | 修改 | 原子 CAS 晋升/回滚、主体校验
- `apps/api/.../routers/catalog.py` | 修改 | `/oracle-bindings` 端点、AssertionReq.oracle_version
- `apps/api/.../seed.py` | 修改 | 新增 2 个 Oracle，消除跨断言隐式复用
- `packages/execution/.../runtime/pipeline.py` | 修改 | 按 pinned Oracle 版本加载、按模型字段过滤文档
- 测试 | 新增 6 文件 | 62 个测试函数 / 82 采集用例
- `docs/RUN-006-*.md` + `docs/assets/run006/` | 新增 | 交付文档与证据
- `apps/web/src/views/DiagnosisDetailView.vue` | 修改 | 冲突提示、探针回灌展示、主动闭环入口、预算语义提示、390px 表格优化
- `apps/web/src/types.ts` | 修改 | `probe_feedback`/`excluded_by`/`unresolved_conflicts`/`evaluation_history`/`ProbePlan` 类型

## RUN-007-RESUME（2026-11-23，追加）

| 文件 | 变更 | 说明 |
|---|---|---|
| `packages/execution/.../runtime/pipeline.py` | 修改 | `execute()` 开头清空 `_state_cache`（修复跨 Run 业务状态泄漏导致的假通过漏检） |
| `apps/api/.../runtime.py` | 修改 | 新增 `TERMINAL_RUN_STATUSES` 与 `wait_for_run()`（只等待，不执行） |
| `apps/api/.../services/repair_service.py` | 修改 | 沙箱改队列 + `wait_for_run`；无 Worker 时 fail-closed；记录 `sandbox_executed_by`/`sandbox_fencing_token` |
| `apps/api/.../routers/intelligence.py` | 修改 | `diagnose` 增加 `use_llm` 参数（Benchmark 规则臂口径纯净） |
| `packages/persistence/.../db.py` | 修改 | `create_schema` 重试 + 容忍重复 DDL；迁移每步包 `SAVEPOINT`；迁移记录容忍主键冲突（并发启动安全） |
| `scripts/e2e_run007.py` | 重写 | 独立 Worker 队列执行器、`assert_worker_executed`、`/runs/{id}/evidence` 校验、图节点/边校验 |
| `scripts/worker_fault_recovery.py` | 重写 | 真实计时/被 Run ID/同 Run 接管/代次严格递增/陈旧代次拒绝/账实核对 |
| `scripts/soak_run007.py` | 重写 | 自启独立 API + 4 Worker + 真实 SSE 续传 + TeamFlow 真实健康 + 19 项完整性 checks |
| `scripts/ui_review_run007.py` | 新增 | 15 视图 × 4 断点 + Dark + 四态截图与断言（暗色必须真的生效） |
| `scripts/bench_run007.py` | 新增 | UI-FaultBench v3 四臂 + Flaky×5 + 自愈三指标 |
| `scripts/bench_llm_arm.py` | 新增 | 受预算 LLM 对照臂（真实调用，按实际模型记录，不外推） |
| `apps/web/src/views/DashboardView.vue` | 修改 | Worker/数据库/服务健康面板（独立 vs 内嵌 Worker） |
| `apps/web/src/views/RunDetailView.vue` | 修改 | 「执行归属」条（Worker/attempt/fencing/租约） |
| `apps/web/src/views/RunsView.vue` | 修改 | 筛选栏结构（`.filter-check`），修复 390px 错位 |
| `apps/web/src/components/GraphView.vue` | 修改 | 自适应 `fit()` + 「适应画布」+ 缩放下限，修复节点裁切 |
| `apps/web/src/style.css` | 修改 | 证据图画布高度、窄屏筛选栏布局、复选框不被拉伸 |
| `apps/web/src/types.ts` | 修改 | `Run.lease_expires_at` |
| `.github/workflows/pr-gate.yml` | 修改 | push 分支过滤改新分支；快速集成加新用例 |
| `.github/workflows/nightly-gate.yml` | 修改 | 去 `\|\| true` / `\|\| echo`；LLM BLOCKED 结构化；npm audit 区分工具错误；PG16 版本与平台用例；`gate-verdict` 汇总 |
| `.github/workflows/release-gate.yml` | 修改 | 去 RUN-005 依赖；RUN-007 文档/证据校验；Actions API 校验 Nightly `head_sha == github.sha`；benchmark 声明 fail-closed |
| `tests/unit/test_pipeline_state_cache.py` | 新增 | 2 用例 |
| `tests/integration/test_sandbox_worker_queue.py` | 新增 | 2 用例 |
| `tests/integration/test_migration_v8.py` | 新增 | 4 用例 |
| `tests/integration/test_postgres_platform.py` | 新增 | 6 用例（本地 skip，CI PG16 执行） |
| `docs/RUN-007-*.md`（7 份） | 新增 | 交付文档 |
| `docs/assets/run007/*` | 新增 | 证据（E2E/故障恢复/长稳/UI/benchmark） |
