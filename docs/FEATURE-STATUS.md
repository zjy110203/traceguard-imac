# 功能状态矩阵（更新至 2026-11-23 RUN-004-FE）

> 历史口径：RUN-002 基线状态保留在本文件原有条目中；RUN-003-G 真实 LLM 接入与 RUN-004-FE 前端产品化升级按下列条目更新。历史 RUN-002/RUN-003 报告不改写。

状态口径：

- **已运行验证**：有真实执行证据（测试日志 / Run 记录 / 截图），可复核；
- **仅实现未验证**：代码完整但缺少运行时验证；
- **降级**：存在但不是目标形态（明确标注）；
- **阻塞**：受外部条件限制，未产生结果；
- **待决策**：需项目负责人批准，不擅自冻结。

## 1. 确定性执行内核

| 能力 | 状态 | 证据 / 说明 |
|---|---|---|
| Playwright 真实浏览器执行 | **已运行验证** | Chromium 159；4 场景真实跑通；E2E 3 项 |
| 浏览器进程/上下文隔离与释放 | **已运行验证** | 每 Run 独立 Context；连续执行无泄漏报错 |
| 定位策略（role/test_id/label/placeholder/css/xpath） | **已运行验证** | TeamFlow 真实点击/填表 |
| 截图 / DOM / ARIA / trace 采集 | **已运行验证** | 每 Run 12–25 份证据，PNG 人工核验 |
| 网络与控制台信号采集 | **已运行验证** | 500 场景捕获 `POST /api/projects → 500` |
| HTTP 降级模式 | **已运行验证** | 明确标记 `http-fallback`，不冒充浏览器 |
| Oracle 独立状态查询 | **已运行验证** | `/api/state/projects` 结果登记为 TRUSTED 证据 |
| 断言评估与 Verdict 聚合 | **已运行验证** | 13 项单测 + 真实场景四态 |
| 执行失败不静默降级 | **已运行验证** | 浏览器不可用 → Run FAILED，不转 HTTP |

## 2. 任务、队列与 Worker

| 能力 | 状态 | 说明 |
|---|---|---|
| Task/Run/Step 状态机 | **已运行验证** | 9 项单测 + API |
| 重试产生新 Run | **已运行验证** | attempt 递增，历史保留 |
| 租约 + fencing token | **已运行验证** | 单测 + 独立 Worker |
| 过期租约回收 | **已运行验证** | `/queue/reap`；Worker 启动执行 |
| 独立 Worker 进程 | **已运行验证** | `--once` 执行真实 Run（`run_1795325544…`） |
| 多 Worker 并发 | **仅实现未验证** | 依赖 DB 原子 claim；未做双进程压测 |
| 心跳续租线程 | **已实现（单测验证）** | 每 ttl/3 续租；被接管后 lost，步骤边界停止执行 |

## 3. 持久化

| 能力 | 状态 | 说明 |
|---|---|---|
| 统一 Schema + 迁移 | **已运行验证** | 迁移 v4 修复 Step 主键；全新库/旧库均可建 |
| 事件幂等/序号/冲突 | **已运行验证** | 9 项持久化测试（含并发 12 线程） |
| 证据不可变与派生链 | **已运行验证** | 单测 + API |
| 产物内容寻址与篡改检测 | **已运行验证** | 篡改后下载 404 |
| 重启恢复 | **已运行验证** | 关闭数据库重开后事件/证据仍可读 |
| PostgreSQL | **仅实现未验证** | 方言兼容，本机无实例 |
| Redis | **未采用** | 队列由 DB 实现，无需 Redis |
| MinIO / S3 | **未实现** | 本地存储；S3 适配器缺失 |
| 事件归档/分区 | **未实现** | 当前单库 |

## 4. 资产与审核

| 能力 | 状态 | 说明 |
|---|---|---|
| TestIntent CRUD + 状态流转 | **已运行验证** | UI + API |
| TestCase 不可变版本 | **已运行验证** | 语义升级生成新版本（API 用例升到 v2） |
| 人工审核门禁 | **已运行验证** | 未审核执行 → 412；agent 审核被拒 |
| Oracle 版本化 + 防 Agent 自批 | **已运行验证** | 412 + 契约 `Literal[False]` |
| Explorer 真实探索 | **已运行验证** | 3 个动作 allowed，轨迹落库 |
| Compiler 候选生成 | **已运行验证** | 生成 PENDING 用例 + 诚实 warning |
| 候选 → 正式晋升 | **已运行验证** | 需人工审核（UI 通过） |

## 5. 诊断与证据

| 能力 | 状态 | 说明 |
|---|---|---|
| 事件/证据 API 与下载 | **已运行验证** | 81 端点中的 evidence/artifact 组 |
| Evidence Graph 构建与持久化 | **已运行验证** | 40 节点/39 边；图快照可复现 |
| 规则诊断（PERSISTENCE/UI/API/NETWORK/LOCATOR/JS） | **已运行验证** | Benchmark B：3/3 主因正确 |
| False Pass 识别 | **已运行验证** | 传统基线 1/1 假通过；本平台 0/1 |
| 多假设与 Disposition 分流 | **已运行验证** | API 场景 CONFIRMED→PRODUCT_DEFECT |
| Flaky 特征标记 | **已实现（单测验证）** | 同版本历史结论不一致 → 标记 flaky，不因此判通过 |
| 只读探针（4 类） | **已运行验证** | 状态查询真实执行；越权 BLOCKED |
| 探针对诊断的增益 | **已运行验证（负结果）** | E 组与 D 组指标相同；记录为诚实结论 |
| LLM 辅助诊断 | **已运行验证** | RUN-003-G 真实 DeepSeek：`rule-v1+llm-advisor`，候选 `suggested_by=llm:*`，UI 标记 CANDIDATE |

## 6. 自愈

| 能力 | 状态 | 说明 |
|---|---|---|
| 提案 + Diff 白名单 | **已运行验证** | 越界路径 412 |
| 断言保持 / Oracle 完整性 | **已运行验证** | 单测 + 对照组 |
| 沙箱真实执行 | **已运行验证** | 真实 Run PASSED |
| 人工审批 / 拒绝 | **已运行验证** | agent 被拒；UI 可操作 |
| CAS 晋升 + 幂等 | **已运行验证** | v3 晋升 CAS_OK；重复键重放 |
| 回归验证 | **已运行验证** | 晋升版本执行 PASSED |
| 回滚 | **已运行验证** | 生成恢复版本 v4，历史保留 |
| L2/L3 自动修复策略 | **未实现** | 仅定位器/等待级；无产品代码修复（设计如此） |

## 7. 平台与前端

| 能力 | 状态 | 说明 |
|---|---|---|
| REST API | **已运行验证** | OpenAPI 可查；RUN-004-FE 新增只读 `GET /api/v1/system/llm/summary` |
| Vue 工作台 | **已运行验证** | 14 个视图，真实数据；统一设计系统 + AppShell + 模型运行中心 |
| 前端构建与类型检查 | **已运行验证** | `vue-tsc` + `vite build` 通过（RUN-004-FE 复核） |
| 单端口部署（API 托管前端） | **已运行验证** | `http://127.0.0.1:8000/` 截图核验 |
| 认证 / RBAC | **未实现** | 本地单租户；生产需前置鉴权 |
| SSE 实时推送 | **降级** | 仅保活；前端用轮询，显示真实最后更新时间 |
| 移动端适配 | **已运行验证** | RUN-004-FE：六档断点无横向溢出，390px 抽屉导航可操作 |
| 模型运行监控 | **已运行验证** | RUN-004-FE：`/models` + 真实用量聚合；成本为应用侧估算，配置态与连通态分离 |

## 8. Benchmark

| 实验 | 状态 | 关键结果 |
|---|---|---|
| B 故障诊断（A/C/D/E） | **已运行验证** | A 33.3% / C,D,E 100%（n=3）；A 假通过 1/1 |
| C 自愈对照 | **已运行验证** | 沙箱 1/1，断言保持 1/1 vs 0/1 |
| A 用例生成 | **部分** | 人工 4/4、编译器 2/2 执行成功；无可评估断言（契约要求） |
| B 组 LLM 基线 | **已运行验证** | RUN-003-G 真实 Flash / Pro / Routed 三组对照，详见 `RUN-003-G-LLM-Benchmark.md`；小样本不宣称显著 |
| 手工修复基线 | **阻塞** | 需真实人力 |
| Macro-F1 / Time to Diagnosis | **未实现** | 样本量不足 / 未埋点 |

## 9. 需要负责人决策（不得擅自冻结）

| 编号 | 事项 | 状态 |
|---|---|---|
| P03 | Oracle 多来源冲突裁决细则 | 待决策，见 `docs/decisions/P03-oracle-arbitration.md` |
| P08 | Repair 审批角色矩阵 | 待决策，见 `docs/decisions/P08-repair-approval-roles.md` |
| — | 路线图 v1.1 正式冻结 | 未冻结（无批准记录） |
| P-EXT | 外部真实站点泛化授权 | 未授权，保持 BLOCKED |

---

# RUN-005 功能状态（2026-11-23，六状态口径）

> 本节使用 RUN-005 指定口径：**IMPLEMENTED / VERIFIED / PARTIAL / BLOCKED / NEEDS_DECISION / NOT_IMPLEMENTED**。
> 上述历史章节保留原有五状态口径，作为历史记录，不改写。

| 能力 | 状态 | 证据 / 说明 |
|---|---|---|
| Probe 预算按 Run/Diagnosis 隔离 | **VERIFIED** | 15 单测 + 6 集成；600s/3600s 服务时长不耗尽新预算 |
| 预算持久化与关闭语义 | **VERIFIED** | `probe_budget` 文档；重启按 scope 恢复；关闭后 `PROBE_BUDGET_CLOSED` |
| Active Planner 真实闭环 | **VERIFIED** | `POST /runs/{id}/probe-plan` 真实执行 2 探针并产出证据，停止原因明确 |
| 真实 SSE 事件流 | **VERIFIED** | 持久化事件、Last-Event-ID、workspace/run 过滤、心跳、轮询降级；7 项测试 |
| 前端 SSE 接入与降级 | **VERIFIED** | `apps/web/src/events.ts`；Run 详情页显示通道状态徽标；30/30 UI 验收 |
| Consumer Inbox 持久化状态机 | **VERIFIED** | PROCESSING/SUCCEEDED/FAILED/UNKNOWN、租约接管、事务性、多连接抢占 |
| Inbox 真实消费者 | **VERIFIED** | `run-timeline-projector` → `/runs/{id}/timeline`（派生视图） |
| Event Store 多进程并发 | **VERIFIED** | DB 原子序号分配器；4 进程 × 25 事件无重复无丢失 |
| LLM TestCase Compiler（三种模式） | **PARTIAL** | 模式与顾问实现 + 真实单次冒烟通过；未做完整探索→编译→回放链路 |
| 候选稳定性回放（F08） | **PARTIAL** | 编排/语义/门槛 VERIFIED（7 测试）；未做真实浏览器多次回放 |
| LLM Active Probe Planner | **VERIFIED** | 真实调用成功；只能选已注册探针；确定性 Planner 仍为决策者 |
| LLM Repair Planner | **VERIFIED** | 真实生成受限变更；禁止断言/Oracle；仍需审批/CAS |
| 三类 Agent 可观测性 | **VERIFIED** | `/system/llm/summary` 增加 compiler/probe/repair 调用与 fallback |
| Benchmark v2 verdict 复验 | **VERIFIED** | 30/30（真实浏览器，隔离库） |
| Benchmark v3 多臂对照 | **BLOCKED** | 成本/时长；未输出任何准确性数字 |
| PostgreSQL 真实验证 | **PARTIAL** | parity 测试 + CI Service Container 已就绪；本机 BLOCKED |
| CI 三级门禁 | **VERIFIED（文件）/ 未在真实 CI 跑** | PR / Nightly / Release 工作流入库；Nightly 因 dev-only high 漏洞会先失败（如实记录） |
| 前端自动化验收 | **VERIFIED** | `scripts/ui_acceptance.py` 30/30 + 18 张截图 |
| 60 分钟长稳 | **BLOCKED** | 未执行 |
| 多 Worker 故障恢复 | **PARTIAL** | 历史证据，本轮未复验 |
| P03 / P08 | **NEEDS_DECISION** | 决策包已产出，未冻结 |
| Redis/Celery、MinIO/S3、专用图数据库 | **NOT_IMPLEMENTED** | 无明确收益证据，按规则不引入 |

---

# RUN-006 功能状态（2026-11-23）

> 本轮只做 WP1（核心正确性与安全性）。口径同上：IMPLEMENTED / VERIFIED / PARTIAL / BLOCKED / NEEDS_DECISION / NOT_IMPLEMENTED。
> 测试基线：285 passed → **367 passed / 5 skipped**（新增 82 采集用例）。

| 能力 | 状态 | 证据 / 说明 |
|---|---|---|
| Oracle 绑定 API（`/oracle-bindings`） | **VERIFIED** | 26 项门禁测试 + 真实 API 200/412/403 |
| required assertion 审核门禁 | **VERIFIED** | 缺 Oracle / 未审批 / 跨 Workspace / 不存在 → 412 且列出全部问题 |
| Oracle 版本固定（pinned `oracle_version`） | **VERIFIED** | 审核时写入断言；执行端不回退到最新版本 |
| 审核主体类型校验 | **VERIFIED** | `agent`/`agent:gpt`/`agent-1`/`llm:*`/`system:*`/`bot:*`/`service:*`/空 → 403 |
| 并发审核 CAS（`review_revision`） | **VERIFIED** | 行级条件 UPDATE；迁移 v7 |
| 探针证据回灌假设 | **VERIFIED（语义层）** | 11 项集成测试；真实 `http_state` 探针使假设被反驳并排除 |
| 冲突真实计算 + 停止条件 | **VERIFIED** | `unresolved_conflicts` 落库；冲突时零探针消耗停止 |
| 探针失败 ≠ 目标故障证据 | **VERIFIED** | 失败/超时不进 supporting/contradicting |
| LLM 假设不得 CONFIRMED | **VERIFIED** | 修复 `suggested_by == "llm"` 精确匹配漏洞（真实漏洞） |
| 全 STOP_* 停止原因 | **VERIFIED** | CONFIRMED / 冲突 / 预算关闭 / 预算耗尽 / 预算不可用 / 权限失效 / 边际价值不足 / 迭代上限 |
| Evidence Graph 探针节点 | **VERIFIED** | 真实图 60 节点/81 边，含 `ProbeExecution` 与 `contradicts` |
| ProbeBudget 故障安全 | **VERIFIED** | 读失败/写失败均阻断；回滚内存计数 |
| 预算跨进程一致性 | **PARTIAL** | 多 Registry 共享版本 CAS；未做双 OS 进程压测 |
| 晋升/回滚原子 CAS | **VERIFIED（SQLite）** | 单事务比较+写入；PG 行锁未实测 |
| 统一目标域名策略 | **VERIFIED** | 18 项策略测试；Explorer 与 Runner 同一 `TargetPolicy` |
| 请求级网络管控 | **VERIFIED（子资源/XHR）** | 真实 Chromium：未授权服务端 0 次请求 |
| 顶层 302 越权落地 | **PARTIAL** | 落地复核 → `TargetAccessDenied` → 步骤失败；出站请求已到达（限制已披露） |
| 身份认证 / RBAC | **NOT_IMPLEMENTED** | 仍为单机可信；主体类型门禁 ≠ 身份认证（NEEDS_DECISION R1） |
| 完整端到端全链（含修复晋升） | **PARTIAL** | 正常/故障场景真跑；修复全链未走完 |
| 稳定性回放 F08 | **PARTIAL（继承）** | 本轮未改 |
| 前端诊断视图（冲突/回灌/闭环入口） | **VERIFIED** | `DiagnosisDetailView` 显示冲突、`probe_feedback`、`excluded_by`、主动闭环停止原因；30/30 UI 验收 |
| 前端其余 13 视图体验审查 | **NOT_IMPLEMENTED（本轮）** | 未审查，不声称改善 |
| PostgreSQL / 4 Worker / 60 分钟长稳 | **BLOCKED** | 环境/时间窗限制 |
| CI 三级门禁真实运行 | **BLOCKED** | 未触发真实 Actions |
| Benchmark v3 | **NOT_IMPLEMENTED（本轮）** | 不引用历史数字 |

## RUN-007 续跑（2026-11-23，追加；不改写上面历史条目）

> 口径：VERIFIED = 有本轮真实运行证据；IMPLEMENTED = 代码完成但缺本轮运行证据；
> PARTIAL = 部分完成/证据不完整；BLOCKED = 外部条件阻塞；NEEDS_DECISION = 待批准。

| 能力 | 状态 | 证据 / 说明 |
|---|---|---|
| E2E 由独立 Worker 消费队列执行 | **VERIFIED** | `e2e-worker-run007.json`：55 步 0 失败，5 个 Run 均由 w1–w4 领取，`api_inline_worker=false` |
| 执行归属可见（Worker/attempt/fencing/租约） | **VERIFIED** | Run 详情「执行归属」条 + 质量总览 Worker 面板（截图 `ui/07-run-detail-1440.png`） |
| Evidence 取真实来源 + 证据图绑定校验 | **VERIFIED** | A 场景 18 条、B 场景 28 条；图 54 节点/70 边含 `Evidence`+来源关系 |
| 沙箱走队列（无 Worker 时 fail-closed） | **VERIFIED** | `test_sandbox_worker_queue.py`(2) + E2E D 场景 `sandbox_executed_by=run007-w1` |
| Run 级状态缓存隔离 | **VERIFIED** | `test_pipeline_state_cache.py`(2) + E2E B 场景假成功判 FAILED |
| 迁移 v8 幂等 / v7 旧库升级 / 并发启动 | **VERIFIED（SQLite）** | `test_migration_v8.py`(4)；修复前并发用例失败 |
| PostgreSQL 16 平台级契约 | **IMPLEMENTED / 运行时 BLOCKED** | `test_postgres_platform.py`(6) 本地 skip；Nightly PG16 作业就绪但未触发 |
| Worker 被杀 → 同 Run 接管 + 代次隔离 | **VERIFIED** | `worker-fault-recovery-verified.json`（真实 kill、`killed_after_s`>0、`fencing_token` 递增） |
| 60 分钟长稳（独立 API + 4 Worker） | **VERIFIED** | `soak-60min.json` + `soak-samples.jsonl`，19 项 checks 全真 |
| 前端 15 视图 × 4 断点 + 深色 + 四态 | **VERIFIED（视觉人工复核）** | `ui/ui-review.json` 75/75；79 张截图 |
| 证据图完整可见 / 窄屏筛选栏 | **VERIFIED（已修）** | 修复裁切与 390px 错位，见 `RUN-007-UI-REVIEW.md` §3 |
| CI 门禁去掩盖（PR/Nightly/Release） | **IMPLEMENTED（未运行）** | 三份 workflow 已改并通过 YAML 校验；**无 GitHub 凭据 → 未触发** |
| UI-FaultBench v3 四臂 30 场景 | **见 Benchmark 报告** | `docs/RUN-007-BENCHMARK-REPORT.md` |
| LLM 对照臂（受预算子集） | **见 Benchmark 报告** | `llm-arm-*.json`，模型由服务端路由决定，按实际模型记录 |
| 身份认证 / RBAC | **NOT_IMPLEMENTED（NEEDS_DECISION）** | D-01 未批准；主体类型门禁 ≠ 身份认证 |
| 外部站点泛化 | **BLOCKED** | P03 未批准授权清单，本轮未访问未授权站点 |
