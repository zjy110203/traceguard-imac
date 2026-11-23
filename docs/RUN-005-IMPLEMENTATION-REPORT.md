# RUN-005 实施报告（Implementation Report）

- 分支：`workbuddy/traceguard-autonomous`（本轮不推送远程，本地检查点提交）
- 基线：`9d751fe` RUN-004-FE
- 环境：macOS / Python 3.13.12 / SQLite（隔离库 `.traceguard/run005/api.db`）
- 真实服务：TeamFlow `127.0.0.1:8011`、TraceGuard API `127.0.0.1:8010`
- 本轮回归：**285 passed / 5 skipped / 0 failed**

## 1. 架构变化概览

```
                         ┌──────────────────────────────┐
  Run/Diagnosis ─────────►│ ProbeBudgetRegistry           │  每个 Run/Diagnosis 独立预算
                         │  scope=run:… / diagnosis:…    │  started_at=首次使用
                         └──────────────┬───────────────┘
                                        │ budget
Evidence Gap ─► Candidate ─► 五项校验 ─► EIG ─► Planner.choose ─► ProbeEngine.execute
     ▲                                                              │
     └──────────────── 新证据（关联 run/diagnosis） ◄───────────────┘
                                        │
                              DocStore: probe_execution / probe_plan / probe_budget

Event Store ──► SSE(/events/stream, Last-Event-ID) ──► 前端 subscribeEvents(SSE→轮询降级)
     │
     └──► Consumer Inbox(原子抢占/租约/UNKNOWN) ──► Run 时间线投影(派生视图)
```

## 2. 数据库变化

| 变更 | 说明 |
|---|---|
| 迁移 v5 `consumer_inbox_states` | `tg_consumer_inbox` 增加 `attempts / claim_expires_at / indeterminate / last_error / updated_at`；旧 `PROCESSED` → `SUCCEEDED`（幂等，SQLite/PG 通用） |
| 迁移 v6 `producer_sequence_allocator` | 新表 `tg_producer_sequence(producer_instance_id PK, last_sequence)`，事件序号改为数据库原子自增（`ON CONFLICT DO UPDATE … RETURNING`） |
| 新文档类型（复用 `tg_doc`） | `probe_budget` / `probe_plan` / `run_timeline` / `stability_replay` |
| SQLite 并发设置 | 文件库启用 `journal_mode=WAL`、`busy_timeout=30000`、`synchronous=NORMAL`，`:memory:` 不受影响 |

**没有**删除任何历史表/数据；迁移均为 `IF NOT EXISTS` 或列存在性检查，可在旧库上重复执行。

## 3. API 变化

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/events/stream` | 真实 SSE：`workspace_id / run_id / event_type / last_event_id(或 Last-Event-ID 头) / heartbeat_seconds / poll_interval / limit` |
| POST | `/api/v1/runs/{run_id}/probe-plan` | 执行 Active Diagnosis 闭环，返回 steps/stop_reason/budget/evidence |
| GET | `/api/v1/probe-plans`、`/probe-plans/{id}` | 计划查询（含 LLM 规划记录） |
| GET | `/api/v1/probes/budgets` | 预算快照（按 Run/Diagnosis，含 provisional 标记） |
| POST | `/api/v1/probes/budgets/close` | 关闭指定预算并记录原因 |
| GET | `/api/v1/runs/{run_id}/timeline` | 由 Inbox 驱动的真实消费者投影（`authority=derived_view`） |
| GET | `/api/v1/inbox/stats` | 消费状态观测（PROCESSING/SUCCEEDED/FAILED/UNKNOWN） |
| POST | `/api/v1/explorations/{id}/compile?mode=` | 编译模式：`rule_only / llm_only / llm_plus_rule_validation` |
| POST | `/api/v1/testcases/{id}/versions/{v}/stability-replay` | 候选稳定性回放（独立 Run、sandbox Task 模式、可配置次数） |
| GET | `/api/v1/stability-replays…` | 回放结果（`refresh=true` 汇总最新 verdict） |
| POST | `/api/v1/repairs/llm-suggest` | LLM 修复候选（可 `propose=true` 生成提案，仍不审批/不晋升） |
| GET | `/api/v1/system/llm/summary` | 增加 compiler/probe/repair 调用与 fallback 统计 |

兼容性：原有接口签名保持不变；`/probes/execute` 新增可选 `run_id / diagnosis_id`。

## 4. 新增 Agent 能力（统一复用 ModelGateway）

| 模块 | 类 | 边界 |
|---|---|---|
| LLM 编译器 | `LLMCompilerAdvisor`（`gateway/llm_planners.py`） | 步骤定位值必须**出现在探索轨迹**中，否则丢弃；业务断言必须有真实状态接口；Oracle 一律不绑定 |
| LLM 探针规划 | `LLMProbePlannerAdvisor` | 只能从已注册 `ProbeDefinition` 与白名单模板选择；参数仅保留 6 个安全键；`info_gain` 上限 +0.15 |
| LLM 修复规划 | `LLMRepairAdvisor` | 只允许 `steps[i].target/value/action_config`、`run_config.wait_timeout`、`test_data_refs`；触及 assertion/oracle/expected/business 的变更直接丢弃 |
| 稳定性回放 | `StabilityReplayService` | 独立 Run、sandbox 入口、不稳定不晋升、Oracle 不完整不得宣称业务已验证 |

默认开关（保守）：`TG_LLM_COMPILER_MODE=rule_only`、`TG_LLM_PROBE_PLANNER_ENABLED=0`、`TG_LLM_REPAIR_PLANNER_ENABLED=0`；本轮验收时显式打开并记录。

## 5. 修改文件清单

**新增（后端/前端）**
- `apps/api/traceguard_api/sse.py`（SSE 游标与帧格式）
- `apps/api/traceguard_api/services/timeline_service.py`（Inbox 真实消费者）
- `apps/api/traceguard_api/services/stability_service.py`（F08 回放）
- `apps/web/src/events.ts`（SSE 订阅 + 轮询降级 + event_id 去重）
- `packages/agents/traceguard_agents/gateway/llm_planners.py`（三类顾问 + Prompt）
- `requirements-postgres.txt`
- `.github/workflows/nightly-gate.yml`、`release-gate.yml`
- `scripts/ui_acceptance.py`、`scripts/llm_advisor_smoke.py`

**修改**
- `packages/evidence/traceguard_evidence/probe/planner.py`（预算生命周期 + 注册表 + 原子占位）
- `packages/evidence/traceguard_evidence/inbox.py`（内存/持久化统一状态机）
- `packages/persistence/traceguard_persistence/{stores,models,db}.py`（Inbox/Event/迁移/SQLite 并发）
- `apps/api/traceguard_api/services/{probe_service,exploration_service,repair_service,__init__}.py`
- `apps/api/traceguard_api/routers/{intelligence,execution,system}.py`、`main.py`
- `packages/agents/traceguard_agents/compiler/compiler.py`（模式化编译 + llm_meta）
- `packages/execution/traceguard_execution/runtime/pipeline.py`（sandbox Task 模式放行候选版本）
- `packages/core/traceguard_core/config.py`（三类开关 + 模式校验）
- `apps/teamflow/traceguard_teamflow/models.py`（`TG_TEAMFLOW_DB` 隔离库支持）
- `apps/web/src/views/RunDetailView.vue`（SSE 徽标 + 事件驱动刷新）
- `scripts/bench_verify.py`（`TG_API_URL` 可配置）
- `.github/workflows/pr-gate.yml`（扩充快速门禁范围）

**测试新增（9 个文件，61 通过 + 5 PG 跳过）**
`tests/unit/test_probe_budget.py`、`test_inbox_persistence.py`、`test_event_concurrency.py`、`test_llm_advisors.py`；
`tests/integration/test_probe_service.py`、`test_sse.py`、`test_timeline_projection.py`、`test_stability_replay.py`、`test_postgres_parity.py`。

## 6. 未实现 / 未验证（如实记录）

1. **Benchmark v3 多臂对照**（A/C/D/E + Flash/Pro/Routed）：BLOCKED（成本/时长），见 `RUN-005-BENCHMARK-REPORT.md`。
2. **60 分钟长稳 + 4 Worker 故障恢复重跑**：BLOCKED（本轮时间），不引用旧结论冒充本轮验收。
3. **PostgreSQL 本地真实验证**：BLOCKED（无 Docker/psql）；CI Nightly 已配置 Service Container，尚未在真实仓库跑过。
4. **LLM 编译 + 稳定性回放的完整浏览器多次回放**：编排与语义已验证（单测/集成），真实多次浏览器回放未执行。
5. **P03 / P08**：NEEDS_DECISION，未改 Oracle 冲突契约与正式审批模型。
