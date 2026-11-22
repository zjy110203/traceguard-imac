# TraceGuard 架构说明（v0.2 / RUN-002 实际实现）

> 本文描述**当前代码真实实现**，不是路线图设想。与设计基线的差异见 §8。
> 架构基线：模块化单体 + 独立 Worker；不引入无必要的微服务。

## 1. 系统组成

```
┌──────────────────────────────┐        ┌─────────────────────────────┐
│ Web 工作台 (Vue3 + TS)        │        │ 被测应用 TeamFlow            │
│ router + 真实 REST 调用       │        │ FastAPI + SSR 页面 + 故障注入 │
└──────────────┬───────────────┘        └──────────────▲──────────────┘
               │ /api/v1                                │ 真实 HTTP / 浏览器
┌──────────────▼───────────────────────────────────────┴──────────────┐
│ FastAPI 平台 (模块化单体)                                            │
│  routers: system / catalog / execution / evidence / intelligence /  │
│           benchmark                                                 │
│  services: Runtime(组合根) · Diagnosis · Graph · Probe · Repair ·    │
│            Exploration · BenchmarkRunner                            │
│  execution: RunPipeline · PlaywrightObserver · HttpObserver ·        │
│             AssertionEvaluator · VerdictAggregator · OracleManager  │
│  agents: Explorer · TestCaseCompiler · SelfHealing(rules)            │
│  evidence: EventStore · EvidenceManager · ConsumerInbox ·            │
│            EvidenceGraph · DiagnosisEngine · ProbeEngine            │
│  persistence: Database · Catalog · DocStore · ArtifactStore          │
└──────────────┬──────────────────────────────────────────────────────┘
               │
      ┌────────▼─────────┐        ┌──────────────────────────┐
      │ SQLite / Postgres │        │ 本地内容寻址产物目录       │
      │ 业务/事件/证据/目录 │        │ .traceguard/artifacts/    │
      └──────────────────┘        └──────────────────────────┘
```

### 1.1 部署形态

| 形态 | 说明 |
|---|---|
| 单机（默认） | API 内嵌后台 Worker（`TG_INLINE_WORKER=1`），一个端口同时提供 API + 前端静态资源 |
| 分离 Worker | `TG_INLINE_WORKER=0` + `python -m traceguard_worker.main`，可多实例（租约 + fencing token 保证不重复执行） |
| 演示 | `scripts/run_teamflow.sh` + `scripts/run_api.sh`，浏览器真实启动 |

## 2. 分层职责

| 层 | 包 | 职责 | 不做什么 |
|---|---|---|---|
| 契约 | `traceguard_contracts` | C01–C12 Pydantic 模型、枚举口径、schema 版本 | 不写业务逻辑 |
| 核心 | `traceguard_core` | 配置（TG_ 前缀）、日志（结构化 + 脱敏）、错误码、ID | 不依赖上层的业务对象 |
| 持久化 | `traceguard_persistence` | 统一 Schema、迁移、目录/事件/证据/产物/DocStore | 不做流程判断 |
| 执行 | `traceguard_execution` | 浏览器会话、观察者、断言、Verdict、Task/Run 状态机、Pipeline | 不生成测试意图，不修改 Oracle |
| Agent | `traceguard_agents` | Explorer、Compiler、Tool/Model Gateway、自愈规则 | 不直接改数据库状态；不自批 Oracle/修复 |
| 证据与智能 | `traceguard_evidence` | Event、Evidence、Inbox、Graph、DiagnosisEngine、ProbeEngine | LLM 不能宣布 CONFIRMED |
| 平台 | `traceguard_api` | 组合根、REST、服务编排、Benchmark | 路由层不写业务规则 |

## 3. 核心执行链路（真实）

```
POST /testcases/{id}/execute
  → TaskService.create_task（幂等键；记录 testcase_version_id 与 mode）
  → TaskService.create_run（新 Run，attempt 递增；不覆盖历史）
  → [Worker 或 inline worker] claim_next_run（租约 + fencing_token++）
  → RunPipeline.execute
      1. 加载 TestCaseVersion（只允许 APPROVED；沙箱候选除外）
      2. 加载已审批 Oracle（未审批 → 不参与判定）
      3. cleanup_policy=RESET_DATA → POST /api/test/reset
      4. 预置 api 步骤（故障注入等）
      5. 逐步观察（Playwright：真实点击/填表/断言）
           · 每步落库 StepRow（run 范围主键）
           · 每步事件 step.completed（幂等键 = run:step）
           · 截图 / DOM / ARIA → ArtifactStore（sha256 寻址）→ Evidence(raw)
           · 网络/控制台信号 → Evidence(network)
      6. Trusted Oracle 独立状态查询 → Evidence(api, TRUSTED)
      7. AssertionEvaluator 逐条判定（Oracle 缺失 = NOT_EVALUATED）
      8. Verdict 聚合（四态；执行中断 → NOT_EVALUATED）
      9. submit_result（校验 fencing token；execution_status 与 verdict 独立）
     10. run.completed 事件
```

### 3.1 为什么执行/结论分离

`execution_status=COMPLETED` 只代表"跑完了"，`test_verdict` 才代表"业务是否通过"。二者是独立字段，
聚合逻辑见 `traceguard_execution/verdict.py`：任一必需断言 FAILED → FAILED；有 NOT_EVALUATED 或
INCONCLUSIVE 且无 FAILED → 不判 PASSED。

## 4. 数据与一致性

- **事件**：只追加；`(producer_instance_id, sequence)` 唯一，`(producer_id, idempotency_key)` 唯一；
  同键异载荷写入冲突表并拒绝（`tg_event_conflict`）。
- **证据**：不可变；派生证据必须 `raw_ref` + `derivation_rule`；入库前脱敏；SHA-256 可校验。
- **产物**：内容寻址（`artifacts/<sha2>/<sha256>`），读取时重新计算哈希，磁盘被篡改即报错。
- **用例版本**：不可变，审核状态可流转；执行器强制 APPROVED。
- **修复**：CAS 晋升（当前正式版本必须等于 base），回滚生成新版本而不是删除历史。

## 5. Agent 参与方式（可审计）

| Agent | 入口 | 产出 | 安全约束 |
|---|---|---|---|
| Explorer | `POST /intents/{id}/explore` | Exploration Trace（动作序列 + 网关审计） | 工具白名单、域名/端口白名单、步数预算；页面内容不提升权限 |
| Compiler | `POST /explorations/{id}/compile` | 候选用例（PENDING） | Locator 优先级；无 Oracle 不自动绑定；必须人工审核 |
| Diagnosis | `POST /runs/{id}/diagnose` | Hypothesis + Disposition | 规则引擎裁决；CONFIRMED 需 TRUSTED 且 confirming 证据 |
| Active Diagnosis | `GET /runs/{id}/probe-suggestions` + `POST /probes/execute` | 只读探针结果 → Evidence | 五项校验、白名单模板、预算、越权拒绝 |
| Self-Healing | `POST /repairs` 等 | RepairProposal + 沙箱 Run | Diff 白名单、断言保护、Oracle 完整性、人工审批、CAS |

## 6. 前端

- Vue 3 + TypeScript + vue-router，无 UI 框架依赖，自建设计系统（`style.css`）。
- 页面：总览 / 测试需求 / 测试资产 / Oracle 契约 / 执行中心 / Run 详情（步骤·断言·证据·图·诊断·事件）/ 诊断中心 /
  修复中心 / Benchmark / 系统。
- 全部数据来自 `/api/v1`，含 loading / error / empty 状态；执行中自动轮询。
- 生产构建由 API 直接托管（单端口），开发用 Vite 代理。

## 7. 可靠性机制

| 机制 | 实现 | 验证 |
|---|---|---|
| 租约 | `lease_owner` + `lease_expires_at`，领取时 fencing_token++ | 单测 + 独立 Worker 实测 |
| 旧 Worker 提交拒绝 | `submit_result` 校验 token | 单测 |
| 过期回收 | `reap_expired_runs` → QUEUED | API `/queue/reap` + Worker 启动时执行 |
| 重启恢复 | 全部状态入库；证据/Schema 迁移版本化 | 持久化重启测试 |
| 幂等 | Task 幂等键；Event 幂等键；Repair 晋升幂等键 | 单测 / 集成测试 |
| 浏览器资源释放 | 每 Run 独立 Context；teardown + trace 落盘；模块退出关闭 Playwright | E2E 连续多次执行 |

## 8. 与设计基线的差异（如实记录）

1. **Redis / Celery 未启用**：队列由数据库实现（claim + lease），本机无 Docker/Redis。
   Celery 不是当前架构必需项，避免"为用而用"。
2. **MinIO / S3 未启用**：产物使用本地内容寻址存储（完整性与篡改检测已实测）。
   `ArtifactStore` 留有 backend 参数，S3 适配器尚未实现。
3. **PostgreSQL 未运行时验证**：Schema/迁移使用 SQLAlchemy 通用类型与方言分支，
   但本机无 PG 服务，未做运行时验证（`infra/docker-compose.yml` 提供部署配置）。
4. **LLM 未接入**：ModelGateway 默认规则 planner（`rule-based-v1`），无 API Key；
   Benchmark 的 LLM 基线标记 BLOCKED，不产生数值。
5. **外部站点未验证**：仅 TeamFlow 本地受控目标（P-EXT 未授权）。
6. **P03 / P08 待决策**：见 `docs/decisions/`，未擅自冻结。
