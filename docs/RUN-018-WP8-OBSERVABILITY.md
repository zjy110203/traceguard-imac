# RUN-018 WP8：产品运行监控与告警（Observability）

> 状态：已实现并通过测试。**所有告警阈值均为 provisional（临时配置，未实测校准），不构成任何 SLA 承诺。**

## 1. 目标与范围

为 TraceGuard 产品运行期提供可观测能力（WP8 §8.1-8.3）：

1. **运行指标**：API / Worker / Queue / Run / Artifact / Database 六类；
2. **告警**：七类规则 + 状态机处置闭环（OPEN → ACKNOWLEDGED → RESOLVED）；
3. **前端**：独立 Monitoring 页面（`/monitoring`），支持刷新、自动刷新与告警状态流转。

### 约束（指令要求）

- 不引入 Redis / Celery / S3 / 新消息队列 / 外部监控栈；
- 尽量扩展现有能力：指标取自既有持久化表（`tg_run` / `tg_task` / `tg_artifact` /
  `tg_schema_migration` / `tg_worker_heartbeat` / `tg_alert`）与进程内轻量计数器；
- 所有阈值标注 provisional，不得将未测量阈值宣传为正式 SLA；
- 不弱化现有断言。

## 2. 架构总览

```
Worker 主循环（inline / standalone）          API 请求
  ├─ 心跳 upsert（tg_worker_heartbeat）        ├─ 请求指标中间件（进程内计数）
  └─ 节流告警评估（≥30s/次）                   └─ /api/v1/monitoring/*
                                                    ├─ MetricsCollector.collect()  ← 六类指标快照
                                                    └─ AlertEngine.evaluate()      ← 规则评估 + 落库
```

新增文件：

| 文件 | 职责 |
| --- | --- | 
| `packages/persistence/traceguard_persistence/monitoring.py` | 阈值配置、请求指标/队列趋势/DB 失败计数（进程内）、Worker 心跳 upsert、六类指标快照 `MetricsCollector` |
| `packages/persistence/traceguard_persistence/alerts.py` | 告警引擎：规则、状态机、持久化、自动解决 |
| `apps/api/traceguard_api/routers/monitoring.py` | 监控与告警 REST API |
| `apps/web/src/views/MonitoringView.vue` | 前端 Monitoring 页面 |
| `tests/unit/test_alert_engine_run018.py` | 告警引擎单元测试 |
| `tests/integration/test_monitoring_api_run018.py` | 监控 API 集成测试 |

扩展文件：`models.py`（+`WorkerHeartbeatRow`/`AlertRow`）、`main.py`（请求指标中间件 +
路由挂载）、`runtime.py` 与 `apps/workers/.../main.py`（心跳 + 告警评估钩子）、
`router.ts` / `App.vue` / `SystemView.vue` / `api.ts`（前端接入）。

## 3. 运行指标（8.1）

端点：`GET /api/v1/monitoring/metrics?workspace_id=&artifact_hash_limit=`

响应六组指标 + `thresholds`（provisional）+ `thresholds_status` + `scope` 口径声明。
各组统计口径随响应内 `counting` 字段显式声明（沿用 `/system/metrics` 的口径声明惯例）。

### 3.1 API

| 指标 | 口径 |
| --- | --- |
| 健康状态 | `health`：数据库探活通过 = `ok`，否则 `degraded` |
| 请求数量 / 错误率 | 进程内请求计数器（`main.py` 中间件落点，有界窗口 2000 样本，随重启清零） |
| 响应时间 | 窗口内 P50 / P95 / avg / max（毫秒） |
| 数据库连接状态 | `database_ok` + `database_error` |
| 最近错误 | 最近 20 条 4xx/5xx（方法/路径/状态/耗时/时间） |

### 3.2 Worker

数据源：`tg_worker_heartbeat`（Worker 每轮询周期 upsert 一次）。

| 指标 | 口径 |
| --- | --- |
| Worker 数量 | 心跳表登记行数 |
| 在线 / 离线 | `last_heartbeat_at` 距今 ≤ provisional 阈值（默认 90s）且 `status != STOPPED` |
| 最近心跳 | `last_heartbeat_at` + `heartbeat_age_s` |
| 当前执行 Run | `current_run_id` / `current_task_id`，页面可跳转 Run 详情 |
| 租约剩余时间 | `tg_run.lease_expires_at - now`（按当前 Run 计） |
| 接管次数 | `takeovers_total`：领取时 `previous_status != QUEUED`（租约过期回收再领取）即计 1 |
| 失败次数 | `failures_total`：执行抛异常或结果 FAILED/TIMED_OUT 即计 1 |

心跳由两类 Worker 写入：API 内嵌 Worker（`runtime.py` inline loop，mode=inline）与
独立 Worker（`apps/workers/traceguard_worker/main.py`，mode=standalone）。
优雅退出写 `status=STOPPED`。心跳失败绝不影响执行主循环。

### 3.3 Queue

数据源：`tg_run` SQL 聚合（与 `/queue/stats` 同口径，不截断）。

| 指标 | 口径 |
| --- | --- |
| QUEUED / RUNNING 数量 | `GROUP BY execution_status`（RUNNING 含 PREPARING） |
| 最老任务等待时间 | `now - min(created_at of QUEUED)` |
| 积压趋势 | 进程内采样（每次指标请求最多 3s 一条，随重启清零）+ 方向（rising/falling/steady）；长期趋势需外部时序库（本期不引入） |
| 超时任务 | `timed_out_total`（TIMED_OUT 状态累计）+ `overdue_running`（QUEUED/PREPARING/RUNNING 且等待/运行超过 provisional 上限，默认 900s 的 Run 列表） |

### 3.4 Run

| 指标 | 口径 |
| --- | --- |
| PASSED / FAILED / INCONCLUSIVE | 全历史 `GROUP BY test_verdict`（含 NOT_EVALUATED/UNKNOWN） |
| 最近 24h 成功率 | `PASSED / (PASSED+FAILED+INCONCLUSIVE)`，按 `ended_at` 落窗，样本上限 2000 行/请求 |
| P50 / P95 执行时间 | `ended_at - started_at`（毫秒），仅统计 24h 内两者齐全的 Run |
| 常见失败类型 | 24h 内 FAILED/TIMED_OUT Run 的错误串前缀（`error` 首段）Top5 |

### 3.5 Artifact

数据源：`tg_artifact` + `ArtifactStore.scan()` + GC 审计文件。

| 指标 | 口径 |
| --- | --- |
| 登记数量 / 总占用空间 | `COUNT(*)` / `SUM(size)` |
| 缺失文件 | `scan()`：登记在册但落盘缺失 |
| 哈希不一致 | 有界重算最近 N 条登记（默认 50，`artifact_hash_limit` 可调，响应声明 `hash_check.truncated`）|
| 孤儿文件 / 孤儿登记 | `scan()`（含 `protected_fresh` 保护期对象） |
| GC 状态 | `_gc-audit.jsonl`：最近执行时间、记录数、error 数、隔离目录数；完整 GC 仍走 `scripts/artifact_gc_run016.py` |

### 3.6 Database

| 指标 | 口径 |
| --- | --- |
| 连接状态 | `SELECT 1` 探活 |
| 迁移版本 | `MAX(version)` 与已应用条数（`tg_schema_migration`） |
| 连接池 | SQLAlchemy pool `status()`（size/checkedin/checkedout/overflow） |
| 关键读写失败 | 监控子系统自身观察到的读/写失败计数（进程内，随重启清零），含最近错误串 |

## 4. 告警引擎（8.2）

### 4.1 告警类型（七类，指令最小集全覆盖）

| alert_type | 触发条件（provisional 阈值） | 默认严重级别 |
| --- | --- | --- |
| `WORKER_OFFLINE` | Worker 心跳超时（默认 90s），逐 Worker 一条 | WARNING（无任何在线 Worker 时 CRITICAL） |
| `QUEUE_BACKLOG` | QUEUED 数 > 20 或最老等待 > 900s（任一） | WARNING |
| `RUN_FAILURE_RATE` | 24h 失败率 ≥ 50%（样本 ≥ 5 才评估） | WARNING（≥80% 时 CRITICAL） |
| `DB_UNREACHABLE` | 数据库探活失败 | CRITICAL |
| `ARTIFACT_INTEGRITY` | 产物缺失或哈希不一致（`missing` / `hash` 两个指纹） | CRITICAL |
| `GC_ANOMALY` | GC 审计出现 error 结果 | WARNING |
| `RUN_TIMEOUT` | 单个 Run 等待/运行超 provisional 上限（默认 900s），逐 Run 一条 | WARNING |

### 4.2 状态机

```
OPEN ──acknowledge──▶ ACKNOWLEDGED ──resolve──▶ RESOLVED（终态）
  └────────────resolve─────────────────────────────┘
         （条件消失时引擎自动 RESOLVED）
```

- **处理记录**：`acknowledged_by/at`（处理主体 + 处理时间）、`resolved_by/at`、
  `resolution_reason`（解决原因，人工 resolve 时必填，422 拒绝空原因）；
- **人工处理主体**：服务端 Principal（`user:{username}`），认证关闭时 `operator:dev`，
  不采信请求体声明；ack/resolve 写入审计日志（`alert.acknowledge` / `alert.resolve`）；
- **非法迁移**：重复 ACK / 已解决再处理 → 409 `ILLEGAL_STATE`；
- **自动解决**：条件消失（连续评估不再命中）→ `RESOLVED`，
  `resolved_by=system:alert-engine`，`resolution_reason=condition_cleared`；
- **去重**：同一 `fingerprint` 的未解决告警只保留一条，持续命中只累加 `occurrences`
  与 `last_seen`；RESOLVED 后条件再现 → 新建告警（历史可追溯）。

### 4.3 自动解决的竞态防护（实现要点）

评估入口是"指标快照 + 规则"，存在 TOCTOU 窗口（Worker 线程先采集快照、后评估时，
API 侧可能已新建告警）。引擎做了两道防护：

1. 快照 `generated_at` 之后创建的告警，不因这份旧快照被自动解决；
2. 快照中数据源报错/缺数的告警类型视为"未知"，保持活跃，交由下次健康快照或人工处理。

### 4.4 阈值配置（全部 provisional）

定义于 `traceguard_persistence.monitoring.PROVISIONAL_THRESHOLDS`，可选环境变量覆盖：

| 阈值键 | 默认值 | 环境变量 |
| --- | --- | --- |
| `worker_offline_seconds` | 90 | `TG_ALERT_WORKER_OFFLINE_S` |
| `queue_backlog_count` | 20 | `TG_ALERT_QUEUE_BACKLOG_N` |
| `queue_oldest_wait_seconds` | 900 | `TG_ALERT_QUEUE_WAIT_S` |
| `run_failure_rate` | 0.5 | `TG_ALERT_RUN_FAILURE_RATE` |
| `run_failure_rate_min_samples` | 5 | `TG_ALERT_RUN_FAILURE_MIN_SAMPLES` |
| `run_overrun_seconds` | 900 | `TG_ALERT_RUN_OVERRUN_S` |

**以上全部为 provisional：未经过真实负载实测校准，仅供告警初版触发；
任何界面与文档都不得将其表述为 SLA。**

### 4.5 触发时机（不引入新调度基础设施）

- **API 进程**：`GET /api/v1/monitoring/metrics` 与 `GET /api/v1/monitoring/alerts`
  每次读取前评估一次（读路径顺带评估，无后台调度器）；
- **Worker 进程**：主循环节流评估（每引擎实例 ≥30s 一次，评估失败绝不影响执行）。

## 5. REST API

| 方法/路径 | 权限 | 说明 |
| --- | --- | --- |
| `GET /api/v1/monitoring/metrics` | `read` | 六类指标快照 + 本次评估摘要；Run/Queue 按调用方授权 Workspace 过滤，响应 `scope` 声明口径 |
| `GET /api/v1/monitoring/alerts` | `read` | 告警列表（`status` / `alert_type` / `limit` 过滤）；普通用户可见系统级 + 授权 Workspace 告警 |
| `GET /api/v1/monitoring/alerts/{id}` | `read` | 单条告警；跨 Workspace 访问 403 `WORKSPACE_FORBIDDEN` |
| `POST .../alerts/{id}/acknowledge` | `system.configure`（platform-admin） | OPEN → ACKNOWLEDGED |
| `POST .../alerts/{id}/resolve` | `system.configure`（platform-admin） | → RESOLVED，body `{reason}` 必填 |

权限沿用既有 `auth.action_for_request` 映射（GET → `read`；未知写操作 fail-closed →
`system.configure`），未新增权限面。Workspace 口径与 `/system/metrics`、`/queue/stats`
一致：显式 `workspace_id` 非授权 403；省略时普通用户只统计授权并集，platform-admin 全局。

## 6. 前端（8.3）

- **入口**：左侧导航「运行 / 运行监控」（`/monitoring`），System 页头部亦有跳转；
- **概览卡**：API 状态/错误率/P95、数据库、Worker 在线、队列积压、活跃告警、产物存储；
- **Worker 表**：在线状态、最近心跳、当前 Run（跳转 Run 详情）、租约剩余、领取/接管/失败计数；
- **队列卡**：QUEUED/RUNNING/超时计数、积压趋势 Sparkline、逾期 Run 列表（跳转）；
- **最近错误**：API 4xx/5xx 与最近失败 Run（跳转 Run 详情）合并展示；
- **告警**：活跃告警表（确认 / 解决操作，解决原因弹窗必填）、历史告警表（解决原因 +
  处理主体/时间）；类型/关联 Run/Worker 均可跳转；
- **Artifact / 数据库**：存储状态、GC 状态、连接池、迁移版本；
- **刷新**：手动刷新 + 30s 自动刷新开关；阈值区显式标注 provisional 非 SLA。

## 7. 测试

- `tests/unit/test_alert_engine_run018.py`：状态机迁移（含非法迁移拒绝）、七类规则
  触发/不触发边界（最小样本量保护、等待时间触发）、去重与 occurrences、
  自动解决与再现新建、阈值 provisional 声明与环境变量覆盖；
- `tests/integration/test_monitoring_api_run018.py`：六类指标组齐全 + 阈值声明、
  请求指标真实计数与错误记录、心跳反映到 Worker 指标、告警生命周期（ACK/RESOLVE/
  原因必填/409/404）、metrics 评估联动、Workspace 作用域声明、
  RBAC（POST 仅 platform-admin、跨 Workspace 告警 403）。

运行：`.venv/bin/python -m pytest tests/ -q -x --timeout=120`（全量回归通过）。

## 8. 已知边界（诚实声明）

1. 进程内计数器（请求指标 / 队列趋势 / DB 失败计数）**随进程重启清零**，响应中
   以 `since` / `note` 声明；跨进程/长期趋势需外部时序库（本期按约束不引入）；
2. 告警评估为**拉取触发**（API 读取 / Worker 节流评估），不是常驻推送；无人访问
   且无 Worker 运行时不会产生新告警记录；
3. Artifact 哈希校验为**有界抽样**（默认最近 50 条），完整校验走 GC 脚本；
4. `DB_UNREACHABLE` 告警本身需要写库，数据库完全不可达时该记录写不进去——
   此时以 `/system/health` 与 API 错误率作为兜底观测面。
