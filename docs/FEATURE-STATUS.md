# 功能状态矩阵（更新至 2026-11-25 RUN-013）

> **RUN-013 状态（2026-11-25，权威）**：`docs/RUN-013-FINAL-REPORT.md` 为准。
> 基线 `90f6f4e` → 本轮 HEAD（见最终报告）；主题：生产形态验证与可靠性强化。
>
> - **独立 Worker 部署**：`TG_INLINE_WORKER=false` + 2 独立 Worker，真实 UI 全链路
>   **56 条记录 = 55 PASS / 0 FAIL / 1 NOTE**；故障注入 22 项通过。修复旧 Worker 恢复后重复写证据。
> - **Workspace 并发与安全**：修复 `/events/stream` 未鉴权/跨空间订阅（含游标绕过）；
>   API 专项 **8 passed**、真实浏览器 **10/10**；授权撤销后下一请求立即 403；多标签同步。
> - **稳定性回放→审核**：compiler + healed 真实回放/审核 **15/15**；
>   修复 Oracle 改版后旧回放仍可批准；UI 新增回放状态/进度/结论/Oracle/审核条件面板。
> - **长稳（PostgreSQL 16）**：见 `docs/RUN-013-WP4-SOAK.md`（真实 60 分钟 / ≥100 Run / 多故障注入）。
> - **AI 真实能力**：DeepSeek Smoke **11/11**；诊断顾问 6 场景质量评估（结构/Schema/证据支持 1.0，
>   Fallback 0，归一化采纳率 0.833，6 次 $0.001116，n=6 不外推）。
> - **系统审查**：见 `docs/RUN-013-WP6-SYSTEM-REVIEW.md`；修复 `/system/health` 全局计数泄露。
>
> 仍 BLOCKED / NOT_RUN：B-17、宿主级网络隔离、外部真实站点、真实人工基线、
> Benchmark 23×2（长稳占用隔离环境，NOT_RUN）、Nightly/Release Gate（无凭据）。
> 以上均不标 PASS。

---

# 功能状态矩阵（更新至 2026-11-25 RUN-012）

> **RUN-012 状态（2026-11-25，权威）**：`docs/RUN-012-FINAL-REPORT.md` 为准。
> WP0~WP6 全部完成，基线 `0ca128f` → HEAD `90f6f4e`（代码交付 `7aa3ba5` + CI 证据提交）：
> - **Benchmark 口径可信**：显式场景契约 + 重定义 False Pass（原始归档哈希不变，以 Errata 追加说明；历史 False Pass 保持 PARTIAL / INFERRED_ONLY）；专项 **39 passed**；
> - **CI 可信**：PR Gate **真实触发并成功**（最终证据 run `45621959909`，head SHA `90f6f4e`，backend/frontend 两 Job 均成功）；
> - **多 Workspace 产品化**：前端统一上下文 + 15 视图接入，真实浏览器 25/25；
> - **全局统计权限**：`/system/metrics` 与 `/queue/stats` 接入 Workspace 过滤，改 SQL 真实聚合（专项 11/11）；
> - **核心业务 UI 闭环**：Oracle 绑定 / 稳定性回放 / LLM 建议入口补齐，真实浏览器 **30 条记录（29 PASS / 0 FAIL / 1 NOTE）**；
> - **AI 可靠性**：诊断顾问补 Schema 校验、重试、fallback 标注与追踪（专项 22/22，此前零覆盖）；
> - 质量门禁：全量 **760 passed** + PG16 **16 passed** + 长稳 **20/20 周期**（22 项检查全绿）+ Ruff/前端构建全绿。
> 仍 BLOCKED / NOT_RUN：B-17、宿主级网络隔离、LLM 真实模型输出路径、真实人工基线、
> Nightly/Release Gate 触发（无凭据）、23×2 Benchmark 重跑（独立部署环境）——**均不标 PASS**。

---

# 功能状态矩阵（更新至 2026-11-25 RUN-011-R1.1）

> **RUN-011-R1.1 状态（2026-11-25）**：`docs/RUN-011-R1.1-FINAL-REPORT.md` 为准。
> Workspace 数据隔离与对象级授权、Repair 服务端血缘与来源可信均为**已运行验证**（隔离/血缘测试 23/23）；
> 两轮完整闭环 API 86/86 ×2 + DB 逐实体 Workspace 24/24 ×2；Benchmark v5 口径已修正（原始数据保留）；
> UI 验收 15/0（+2 NOTE：无 workspace 切换控件、审核成功路径由闭环覆盖）；全量 690 passed + PG16 13 passed + Ruff 全绿。
> 仍 BLOCKED：B-17、宿主级网络隔离、LLM/人工基线、真实 CI 证据（无凭据不标 PASS）。
> **注**：其中的「无 workspace 切换控件」缺口已由 RUN-012 WP2 修复（真实浏览器 25/25）。

---

# 功能状态矩阵（更新至 2026-11-23 RUN-004-FE，历史保留）

> **RUN-010 状态（2026-11-24 22:45 CST，权威）**：`docs/RUN-010-FINAL-REPORT.md` 为准。
> WP0~WP9 全部完成：认证产品化（生产候选）、Egress 接入正式内核、Task 幂等冻结、
> 诊断 v2（时间对齐+图裁决+403 规则）、Benchmark v4 两轮真实执行（C 臂 64.3%）、
> axe 13 路由 0 violations、供应链治理（npm 0 漏洞）、完整闭环验收 3/3。
> BLOCKED：B-17、宿主级网络隔离、LLM/人工基线；NEEDS_DECISION：LICENSE。

> **RUN-009 WP0 状态更正（2026-11-24 16:05 CST）**：下方条目为历史快照。
> 当前 HEAD `cb80591`。RUN-008 三段 CI 在 `40efee6` 上全绿（PR `45335765297` / Nightly `45335777381` / Release `45336007166`），
> 下方将 Release `45336007166` 标为「进行中」的条目已过时；`cb80591` 只改恢复文件，不得外推 CI 结论。
> 前端旧验收仅覆盖 12 视图（原始 JSON 中 run_id/diagnosis_id 为空），RUN-009 WP1 正在补齐 14/14；认证/RBAC NOT_IMPLEMENTED。
> 权威基线见 `docs/RUN-009-BASELINE.md`。历史条目保留不覆盖。

## RUN-009 功能状态（2026-11-24，权威）

| 能力 | 状态 | 证据 / 说明 |
|---|---|---|
| 真实身份认证 / RBAC / CSRF / 审计 | 已运行验证 | `docs/RUN-009-WP2-AUTH-RBAC.md`；9 个安全用例 + 全量回归；前端登录页未接入 |
| 受控出站代理（用户态） | 已运行验证（部分） | `docs/RUN-009-WP3-NETWORK-SECURITY.md`；未授权命中 0；宿主级封锁 BLOCKED |
| 14/14 前端业务路由验收 | 已运行验证 | `docs/RUN-009-WP1-FRONTEND-REPORT.md`；154 截图；axe 自动化 BLOCKED |
| 资源监控 / 多 Worker 性能基线 | 已运行验证 | `docs/RUN-009-WP5-PERFORMANCE.md`；吞吐 12.37→32.74 runs/min |
| 诊断增益（EG/AP）研究 | 已运行验证（含负结果） | `docs/RUN-009-WP6-AGENT-DIAGNOSIS.md` |
| Benchmark v4 | 已运行验证（LLM/人工臂 BLOCKED） | `docs/RUN-009-WP7-BENCHMARK.md` |
| Worker 四场景故障注入 | 阻塞 | B-17 未解除，契约级测试已补 |
| 依赖升级（vue-tsc 3.x） | 待决策 | `docs/RUN-009-WP8-SUPPLY-CHAIN.md` |

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
| PostgreSQL 16 平台级契约 | **VERIFIED（CI）** | `test_postgres_platform.py`(6) 在 Nightly 的 `postgres:16`（实测 16.16）上全过；本机无 PG 仍 skip |
| Worker 被杀 → 同 Run 接管 + 代次隔离 | **VERIFIED** | `worker-fault-recovery-verified.json`（真实 kill、`killed_after_s`>0、`fencing_token` 递增） |
| 60 分钟长稳（独立 API + 4 Worker） | **VERIFIED** | `soak-60min.json` + `soak-samples.jsonl`，19 项 checks 全真 |
| 前端 15 视图 × 4 断点 + 深色 + 四态 | **VERIFIED（视觉人工复核）** | `ui/ui-review.json` 75/75；79 张截图 |
| 证据图完整可见 / 窄屏筛选栏 | **VERIFIED（已修）** | 修复裁切与 390px 错位，见 `RUN-007-UI-REVIEW.md` §3 |
| CI 门禁去掩盖（PR/Nightly/Release） | **VERIFIED** | 同 commit `cc3339d` 三级全绿：PR Gate [#45201761239](https://github.com/zjy110203/traceguard-imac/actions/runs/45201761239) / Nightly [#45201774473](https://github.com/zjy110203/traceguard-imac/actions/runs/45201774473) / Release [#45202185410](https://github.com/zjy110203/traceguard-imac/actions/runs/45202185410)；首跑暴露 8 个缺陷已修 |
| UI-FaultBench v3 四臂 30 场景 | **见 Benchmark 报告** | `docs/RUN-007-BENCHMARK-REPORT.md` |
| LLM 对照臂（受预算子集） | **见 Benchmark 报告** | `llm-arm-*.json`，模型由服务端路由决定，按实际模型记录 |
| 身份认证 / RBAC | **NOT_IMPLEMENTED（NEEDS_DECISION）** | D-01 未批准；主体类型门禁 ≠ 身份认证 |
| 外部站点泛化 | **BLOCKED** | P03 未批准授权清单，本轮未访问未授权站点 |

---

# RUN-008 功能状态（2026-11-24，追加；不改写上面历史条目）

> 口径：VERIFIED = 本轮真实执行证据；IMPLEMENTED = 代码实现但缺本轮运行证据；
> PARTIAL = 部分证据；BLOCKED = 外部条件或安全策略阻塞；NOT_IMPLEMENTED = 本轮未做。

| 能力 | 状态 | 证据 / 说明 |
|---|---|---|
| P0 缺陷复核（多进程预算 + 长稳真实健康检查） | **VERIFIED** | 16/16 集成回归；`docs/assets/run008/wp1/` 3 组真实 SIGKILL/正常证据 |
| 长稳脚本恒真判断修复 + 守卫回归 | **VERIFIED** | `test_soak_run007_guards.py`；冒烟 22 checks |
| 本机全量回归 | **VERIFIED** | 567 passed / 13 skipped（WP5/WP6 后）；E2E 12/12 |
| 三级 CI 门禁（最终 commit `40efee6`） | **VERIFIED（PR/Nightly）** | PR Gate `45335765297` success；Nightly `45335777381` success（含 PG16 5+8 passed）；Release `45336007166` 进行中 |
| LLM smoke | **BLOCKED（缺 Secret）** | CI 明确标注不得用于 Release 放行，不写为通过 |
| 4 Worker 并发 | **VERIFIED** | `worker-concurrency.json`：峰值 4、单 Run 单 owner、14/14 |
| 执行中途 SIGKILL 故障恢复 | **VERIFIED** | `worker-fault-recovery.json`：真实 kill、17/17、fencing 递增 |
| 补充故障矩阵 4 场景 | **BLOCKED** | AgentDR 两次拦截（含白名单重写），见 `blockers.md` B-17 |
| 前端 14 视图审查 | **VERIFIED** | 60 checks 全过（48 light + 12 dark）、64 截图；修复 5 个真实缺陷 |
| 前端全局搜索意图死链 | **VERIFIED（已修）** | `fix-verification.json` 4/4 |
| 深色验收假证据 | **VERIFIED（已修）** | 旧脚本改为真主题 + 亮度断言，30/0 |
| Repair 主体门禁一致性 | **VERIFIED（已修）** | `test_healing.py` 17/17；`system:*`/`llm:*`/`bot:*`/`worker:*` 全拒 |
| 日志字符串脱敏词表 | **VERIFIED（已修）** | `test_core.py` 36/36 合计 |
| 身份认证 / RBAC | **NOT_IMPLEMENTED** | 仍只有主体类型门禁，不是真实认证（B-09） |
| 依赖 moderate 漏洞（dev-only） | **PARTIAL（已登记）** | high=0 / critical=0 / moderate=3；semver-major 升级未实施 |
| 60 分钟长稳 | **进行中** | `BZ1Hp8`，每周期 real PASSED；报告未生成前不标通过 |
| Benchmark v3 本轮实验 | **进行中** | 隔离 API/Worker/TeamFlow，2 次重复；完成前不引用历史数字冒充本轮 |

---

# RUN-011-R1.2 功能状态（2026-11-25，追加；不改写上面历史条目）

> 口径：VERIFIED = 本轮真实执行证据；BLOCKED = 外部条件阻塞；NEEDS_DECISION = 待批准。
> 详细证据见 `docs/RUN-011-R1.2-SECURITY-BENCHMARK-CLOSEOUT.md`。

| 能力 | 状态 | 证据 / 说明 |
|---|---|---|
| HTTP 审核稳定性门禁不可绕过 | **VERIFIED** | `ReviewReq.require_stability=Literal[True]` + 路由固定 True；6/6 专项（422/412/200/EXEMPT_HUMAN_AUTHORED，无 HTTP `SKIPPED_BY_CALLER`） |
| 受信任服务端内部迁移豁免 | **VERIFIED** | 服务层 `require_stability=False` 仍可用；HTTP 不暴露；RUN-011-R1.1 夹具未删除 |
| 三个全局观测接口权限收口 | **VERIFIED** | `system.observe` 仅 platform-admin；未登录 401 / viewer·qa-engineer·qa-lead 403 / admin 200；拒绝有 DENIED 审计 |
| 普通 Workspace 业务读取不变 | **VERIFIED** | 三角色 default 读取 200，范围外 403；RBAC 矩阵同步 |
| Benchmark v5 场景覆盖修正 | **VERIFIED** | 46 执行 / 23 场景 / 37 诊断 / 19 失败场景 / 9 正向控制；覆盖率 19/23=0.8261 |
| Benchmark v5 False Pass 修正 | **VERIFIED** | evaluator 级真实 Verdict 判定，0/37=0；F 臂预测机制不再参与；F/G 别名兼容 |
| 原始归档证据保护 | **VERIFIED** | 两个 JSON SHA-256 修复前后一致，并写入离线测试断言 |
| 全量回归 | **VERIFIED** | `pytest -q` 713 passed / 13 skipped（基线 690，新增 23 项，无新增 skip）；`ruff check .`、`git diff --check` 均通过 |
| PostgreSQL 16 专项 | **VERIFIED（RUN-012 WP1）** | 本机本地 PostgreSQL 16.16 上真实跑通：平台 + 一致性 + Workspace 专项 **16/16**（此前因无 PG 环境显式 skip） |
| GitHub Actions 门禁 | **VERIFIED（RUN-012 WP1）** | PR Gate 分支推送后**真实触发并成功**：run `45578101070`（head SHA `57a5ea7`）；代码交付 HEAD run `45621143973`（`7aa3ba5`）；最终证据 HEAD run `45621959909`（`90f6f4e`，backend/frontend 两 Job 均成功） |
| `/api/v1/system/metrics` 是否纳入全局观测权限 | **VERIFIED（RUN-012 WP3）** | 已接入 Workspace 过滤：显式 workspace_id 越权 403 `WORKSPACE_FORBIDDEN`；不传时普通用户只统计授权空间并集，仅 platform-admin 得全局汇总；计数改 SQL `COUNT(*)`/`GROUP BY`（旧 `limit=500` 长度推断已移除，522 条回归验证）；`/queue/stats` 同批修复。专项 11/11 |
| RUN-012 准入 | **具备（附条件）** | 安全与 Benchmark 验收完成；进入前需补 PG16 与真实 Actions 结果 |

---

# RUN-012 功能状态（2026-11-25，追加；不改写上面历史条目）

> 口径：VERIFIED = 本轮真实执行证据；BLOCKED = 外部条件阻塞；NOT_RUN = 未执行（不冒充通过）。
> 详细证据见 `docs/RUN-012-FINAL-REPORT.md`。

| 能力 | 状态 | 证据 / 说明 |
|---|---|---|
| Benchmark 场景契约显式化 | **VERIFIED** | 新增 `scripts/bench_scenario_contract.py`（场景 → 期望结果 → 记录分类）；专项 14/14；原始归档哈希不变，以 Errata 追加说明 |
| Benchmark False Pass 口径重定义 | **VERIFIED** | Evaluator 级真实 Verdict 判定；不再用 `limit=500` 列表长度推断；统计含 `record_classification_counts` / `false_pass_evaluation` / `contract_problems` |
| PR Gate 分支触发覆盖 | **VERIFIED** | 推送后 CI **真实触发并成功**：run `45578101070`（`57a5ea7`）；最终证据 run `45621959909`（`90f6f4e`，两 Job 均成功） |
| CI 格式门禁修复 | **VERIFIED** | `ruff format --check` 223 files already formatted；此前大量未格式化文件会导致 CI 必红 |
| PostgreSQL 16 专项验收 | **VERIFIED** | 真实 PG 16.16 独立库：平台 + 一致性 + Workspace 专项 **16/16**（此前因无 PG 环境显式 skip） |
| 多 Workspace 前端上下文 | **VERIFIED** | 新增 `apps/web/src/workspace.ts`；15 视图接入；真实浏览器 **25/25**（2 空间 × 3 角色 + admin + 伪造切换/localStorage 篡改/伪造请求 + 4 分辨率深色） |
| 前端 Workspace 硬编码清零 | **VERIFIED** | 14 处 `workspace_id=default` 硬编码全部移除，改走当前上下文 |
| 全局统计 Workspace 过滤 | **VERIFIED** | `/system/metrics`：显式越权 403 `WORKSPACE_FORBIDDEN`；普通用户仅授权空间并集；仅 admin 全局；专项 11/11 |
| 统计计数真实聚合 | **VERIFIED** | 改 SQL `COUNT(*)`/`GROUP BY`；`/queue/stats` 同批；**522 条 >500 截断阈值回归**通过 |
| Oracle 绑定 UI 入口 | **VERIFIED** | 用例详情页新增绑定控件 + 保存（此前只有展示，UI 上无法完成合法审核）；真实浏览器回读 `oracle_bindings` 落库 |
| 用例详情断言/步骤渲染 | **VERIFIED** | 修复版本列表 `doc` 未展开导致断言表格与绑定控件整体不渲染的缺陷 |
| 被测目标地址配置化 | **VERIFIED** | 前端不再硬编码 `127.0.0.1:8001`；后端默认取 `TG_TEAMFLOW_BASE_URL`（原按 application 硬编码使配置失效） |
| 稳定性回放 UI 入口 | **VERIFIED（入口）** | 用例详情页审核卡新增「启动稳定性回放」；compiler/healed 用例审核前置条件在 UI 上可触发 |
| LLM 修复建议 UI 入口 | **VERIFIED** | 修复中心提案弹窗内新增「LLM 建议」（未配凭据时如实提示，不伪造成功） |
| 核心业务 UI 全链闭环 | **VERIFIED** | 真实浏览器 **30 条记录 = 29 PASS / 0 FAIL / 1 NOTE**（NOTE 为诊断 ID 记录项）：Intent→TestCase→Oracle→绑定→审核→执行→故障注入→诊断→修复→沙箱→审批→晋升→回滚；真实 Worker + 真实 TeamFlow + `observer_mode=playwright` + 真实故障 TF-UI-002 |
| 诊断顾问 Schema 校验 | **VERIFIED** | 顶层对象 / hypotheses 列表 / 域白名单 / 描述非空；越界条目丢弃并计数（专项 17/17） |
| 诊断顾问重试与 Fallback 标注 | **VERIFIED** | 仅 `invalid_json` 重试 1 次（不与 provider 层重复计费）；无候选必标 `fallback=true` + `error` |
| 诊断顾问防虚构证据 | **VERIFIED** | 虚构 `evidence_id` 剔除留痕；**无真实证据支撑的假设整条丢弃** |
| 诊断顾问可回读追踪 | **VERIFIED** | `called/fallback/error/model/dropped/attempts/warnings` 落库；服务层 5/5 |
| LLM 失败不影响确定性诊断 | **VERIFIED** | 服务调用层测试：LLM 失败时规则假设与结论照常产出 |
| 全量回归 | **VERIFIED** | `pytest -q` **760 passed / 16 skipped**（16 skip 均为 PG 专项，已在真实 PG16 单独跑通）；`ruff check`/`format` 通过 |
| 前端类型检查与构建 | **VERIFIED** | `npm run build`（vue-tsc + vite）通过 |
| 长稳（soak） | **VERIFIED** | 6.14 分钟 / **20/20 周期** / 2 独立 Worker；20 Run 全 PASSED；0 API 错误 / 0 孤儿步骤 / 0 重复事件 / 0 未回收租约；DB `quick_check=ok`；SSE 20 周期全有事件 |
| Nightly / Release Gate 真实触发 | **NOT_RUN** | 需 `workflow_dispatch` 与凭据，本机无；不作 PASS 声明（PR Gate 已在最终证据 HEAD 真实成功：run `45621959909`，head `90f6f4e`） |
| 23×2 真实 Benchmark 重跑 | **NOT_RUN** | 独立部署环境与成本约束；口径已修正并以 Errata 记录历史状态 |
| 真实 LLM 模型输出路径 | **NOT_RUN** | 本环境无凭据；契约与边界由离线 FakeProvider 覆盖 22 例 |
| compiler/healed「回放→审核」成功路径 | **PARTIAL** | 入口已接入且可点击；完整多轮回放成功路径未纳入本轮 |
