# TraceGuard

> **RUN-013（2026-11-25）**：生产形态验证与可靠性强化——独立 Worker 真实 UI 闭环、
> Workspace 并发与 SSE 授权收口、稳定性回放审核闭环、60 分钟 PostgreSQL 16 长稳、
> 真实 LLM Smoke 与 AI 质量量化。状态与限制见 `docs/RUN-013-FINAL-REPORT.md`。
> 历史阶段报告保留在 `docs/`。

Agentic Web UI Testing & Quality Intelligence Platform（智能 Web UI 自动化测试与质量情报平台）

> 真实浏览器执行 · 业务事实校验 · 证据可追溯 · 诊断与修复闭环

## RUN-013 新增能力（2026-11-25）

- **独立 Worker 部署闭环**：`TG_INLINE_WORKER=false` 下由独立进程 claim/执行，
  真实覆盖 SIGTERM/SIGKILL/租约接管/旧 Worker 恢复/API 重启/双 Worker 并发幂等；
  修复“旧 Worker 恢复后重复写证据”，写证据前增加同步租约校验。
- **Workspace 并发与安全**：修复 `/events/stream` 未鉴权与跨空间订阅（含游标绕过）；
  前端修复快速切换竞态、参数覆盖、换用户残留、多标签不同步；授权撤销后下一请求立即 403。
- **稳定性回放→审核闭环**：compiler/healed 两类候选真实回放与 UI 审核；
  审核前重新核验 Oracle 版本/审批状态，杜绝“旧回放批准新 Oracle”；用例详情新增回放状态面板。
- **60 分钟长稳（PostgreSQL 16）**：独立 API + 双 Worker + 真实 Chromium，真实故障注入，
  产出资源/一致性/证据完整性的可回读 JSON。
- **AI 真实能力**：DeepSeek 真实 Smoke 11/11；诊断顾问 6 场景质量量化（Schema/证据支持/重复/成本），
  明确区分规则组、LLM 组、FakeProvider 与真实 Provider。

## RUN-009 新增能力（2026-11-24）

- **真实身份认证与 RBAC**：本地认证（PBKDF2 密码、HttpOnly 会话、CSRF、登录限速）、四角色权限矩阵、
  服务端 Principal 注入审批主体（请求体 `reviewer`/`approved_by` 不再可信）、审计日志哈希链；
  `TG_AUTH_ENABLED=true` 时全站 fail-closed，未认证请求一律 401，不再回退匿名管理员。
- **受控出站代理**（用户态）：HTTP/HTTPS/CONNECT 在请求发出前按 `TargetPolicy` 校验，逐跳重定向复核、
  DNS pinning、WebSocket 守卫、私网/链路本地拒绝；真实验证中未授权服务器收到请求数为 0。
  宿主级网络封锁仍 BLOCKED（无防火墙权限），不把用户态代理写成生产级隔离。
- **14/14 前端业务路由验收**：从 `router.ts` 自动解析路由，154 张真实截图覆盖 15 视图 × 4 断点 × 双主题、
  四态、刷新/直达 URL/前进后退/真实交互；发现 6 个视图的表单控件缺可访问标签（已记录，未在本轮改 UI）。
- **资源监控与性能基线**：基于 `libproc.dylib` 的真实 CPU/RSS 采样（不使用 ps/proc）；
  1/2/4 Worker 并行入队实测吞吐 12.37 → 23.67 → 32.74 runs/min，队列等待 12.4s → 2.2s，内存随 Worker 线性增长。
- **诊断增益研究（WP6/WP7）**：19 场景逐场景误差分析证明 RUN-008 中 D/E=C 的根因是探针证据未回灌、
  图只升置信度不改主因；v4 消融臂实测「真实回灌探针」在当前时序错位下产生**负增益**（覆盖率 24/38→16/38），
  并给出三条明确的增益前提，而非编造提升。
- **Worker 故障契约**：FINALIZING 提前判 Task FAILED 的真实缺陷已修复；四个真实进程故障场景
  仍因 B-17（AgentDR 拦截）保持 BLOCKED，未伪造通过。

## 现在能做什么

- 用 **真实 Chromium** 执行 Web UI 用例，逐步采集截图 / DOM / ARIA / 网络 / 控制台证据；
- 用 **独立状态查询 + 已审批 Oracle** 校验业务事实，识破"UI 显示成功但数据未落库"（False Pass）；
- 对失败 Run 做 **证据驱动诊断**：多假设、确定性确认规则、处置分流，证据不足就是 `INCONCLUSIVE`；
- 用 **只读探针** 补证据（五项校验 + 白名单 + 预算 + 审计）；
- **Oracle-Protected Self-Healing**：只改定位/等待，真实沙箱验证，人工审批，CAS 晋升，可回滚；
- 通过 **Vue 工作台** 完成全部操作：质量总览 / 需求 / 用例 / Oracle / 执行 / Run 详情 / Agent Studio / 模型运行 / 诊断 / 修复 / Benchmark / 系统。

## RUN-005 新增能力（2026-11-23）

- **Active Diagnosis 闭环**：证据缺口 → 候选探针 → 五项校验 → EIG → Planner 选择 → 执行 → 新证据 → 再评估；
  预算按 Run / Diagnosis 隔离并持久化（`provisional` 标注，P06 未冻结）。
- **真实 SSE**：`/events/stream` 推送持久化业务事件，支持 `Last-Event-ID` 续传、workspace/run 过滤、心跳与轮询降级；
  SSE 只做实时提示，状态仍以 API 为准。
- **可靠消费**：Consumer Inbox 统一 PROCESSING/SUCCEEDED/FAILED/UNKNOWN 状态机（租约抢占、崩溃恢复、事务性），
  并由 Run 时间线投影真实使用。
- **LLM 三类扩展**：编译（rule_only / llm_only / llm_plus_rule_validation）、探针规划、修复规划；
  全部只产候选，失败即规则兜底并显式标记。
- **候选稳定性回放（F08）**：独立 Run、sandbox 入口、不稳定不晋升、Oracle 不完整不得宣称业务已验证。
- **工程门禁**：PR / Nightly / Release 三级 GitHub Actions；前端验收脚本（30 项）；PG parity 测试。

## RUN-006 新增能力（2026-11-23）

- **Oracle 闭环**：新增 Oracle 绑定接口；`APPROVED` 前强制校验全部 required assertion 的 Oracle
  （存在 / 已审批 / 同 Workspace / 版本明确），审核通过时把 `oracle_version` 固定到断言，
  历史用例复现不会静默跟随"最新 Oracle"版本。
- **审批主体门禁**：`agent:*` / `llm:*` / `system:*` / `bot:*` / `service:*` 一律不能审核用例、
  审批 Oracle、审批或回滚修复（按主体命名空间判断，不再是字符串精确匹配）。
  注意：这**不是**身份认证，当前仍是单机可信部署（详见 `docs/RUN-006-SECURITY-RELIABILITY.md`）。
- **Active Diagnosis 真正闭环**：探针证据会回灌到假设（支持 / 反驳 / 排除），重新裁决并留痕
  `evaluation_history`；证据冲突 → `STOP_CONFLICT_UNRESOLVED` 并转人工，不再硬编码 `False`；
  探针失败/超时**不会**被当成目标系统故障证据。
- **预算与版本安全**：探针预算读/写失败一律阻断（fail-closed），扣减用文档版本 CAS 支持跨进程；
  修复晋升/回滚改为单事务"比较 + 条件更新 + 插入版本"，并发冲突只允许一个成功。
- **统一网络管控**：Explorer 与正式 Runner 共用同一份 `TargetPolicy`（origin 白名单、拒绝协议相对/
  绝对越权 URL、逐跳重定向校验、内网阻断），浏览器层对未授权请求在发出前 abort。
  已知限制：顶层 302 跳转的出站请求会到达目标，产品保证是"落地拒绝 + 步骤失败"。

前端：诊断详情页新增 **冲突提示 / 探针回灌记录 / 主动诊断闭环入口**，390px 表格优化；`ui_acceptance.py` **30/30** 通过，
1440/1024/768/390 无横向溢出，深色主题可读（截图见 `docs/assets/run006/`）。

本轮测试：**367 passed / 5 skipped**（起点 285）；真实 Chromium E2E **11 passed**。
交付文档：`docs/RUN-006-BASELINE.md`、`RUN-006-IMPLEMENTATION-REPORT.md`、`RUN-006-TEST-REPORT.md`、
`RUN-006-SECURITY-RELIABILITY.md`、`RUN-006-DECISION-REQUESTS.md`；证据：`docs/assets/run006/`。

## RUN-007 续跑新增能力（2026-11-23）

- **执行归属可见**：Run 详情新增「执行归属」条（`Worker` / `attempt` / `fencing token` / 队列状态 / 租约），
  质量总览明确区分「独立 Worker / 内嵌 Worker」并显示 API Worker ID、数据库状态与 dialect。
- **E2E 改为真正消费队列**：`TG_INLINE_WORKER=0` + 4 个独立 Worker + `sync:false` 轮询，
  四场景（正常 / 假成功 / 探针闭环 / 修复全链）**55 步 0 失败**；证据页改为
  `GET /runs/{id}/evidence` 并校验证据图节点与关系（不再依赖空的 `report.evidence_ids`）。
- **两个真实缺陷修复**：① 常驻 Worker 复用 pipeline 导致业务状态缓存**跨 Run 泄漏**（假通过漏检）；
  ② 沙箱曾在 API 进程内执行（改为入队 + 无 Worker 时 fail-closed）；
  另外修复 API + 多 Worker **并发启动建表非原子**导致的随机崩溃。
- **前端 15 视图 × 4 断点 + 深色 + 四态真实截图验收**：75 项检查全通过，
  并修掉证据图裁切、390px 筛选栏错位、暗色截图实际是浅色三处问题（见 `docs/RUN-007-UI-REVIEW.md`）。
- **CI 三级门禁去掩盖**：PR Gate 分支过滤修正；Nightly 删除 `|| true` / `|| echo`，
  LLM 缺 Secret 记 BLOCKED、有 Secret 失败即失败，npm audit 区分工具错误与漏洞；
  Release Gate 去除 RUN-005 依赖，改用 Actions API 校验 Nightly 的 `head_sha` 严格等于当前 commit。
- **可靠性实验**：Worker 被杀接管（真实计时 + 同 Run 代次递增 + 陈旧代次拒绝）、
  60 分钟长稳（独立 API + 4 Worker + 真实 SSE 续传 + 19 项完整性检查）、
  UI-FaultBench v3 四臂（A/C/D/E）30 场景与 LLM 对照臂。

> 交付状态：分支已推送、PR #1 已创建（→ `main@ac2876e`）；同一 commit `cc3339d` 上
> **PR Gate / Nightly Gate / Release Gate 三级门禁全部真实通过**（Nightly 使用 `postgres:16`，
> Release 用 Actions API 校验 `head_sha` 严格相等）。
> 仍存在的限制：本机无 Docker/PostgreSQL（本地 PG 用例 skip，CI 已真跑）、自愈闭环未完成、
> 证据图/探针臂无增益。详见 `docs/RUN-007-TEST-REPORT.md` §9。

交付文档：`docs/RUN-007-BASELINE.md`、`RUN-007-IMPLEMENTATION-REPORT.md`、`RUN-007-TEST-REPORT.md`、
`RUN-007-SECURITY-RELIABILITY.md`、`RUN-007-UI-REVIEW.md`、`RUN-007-BENCHMARK-REPORT.md`、
`RUN-007-DECISION-REQUESTS.md`；证据：`docs/assets/run007/`。

## 快速开始

```bash
bash scripts/bootstrap.sh          # venv + 依赖 + Chromium + 前端构建
bash scripts/run_teamflow.sh &     # 被测应用 :8001
bash scripts/run_api.sh &          # 平台 :8000（内嵌 Worker）
open http://127.0.0.1:8000/
```

端到端演示（真实执行 19 步，含自愈与 Benchmark）：

```bash
.venv/bin/python scripts/final_demo.py
```

详见 [`docs/quickstart.md`](docs/quickstart.md) 与 [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md)。

## 真实结果（2026-11-22）

| 场景 | Verdict | 说明 |
|---|---|---|
| 正常创建项目 | PASSED | 8 步，24 份证据 |
| False Pass（TF-DB-001） | FAILED | UI 成功但业务 count=0 |
| UI 提示缺失（TF-UI-001） | FAILED | 业务成功但界面无反馈 |
| 接口 500（TF-API-001） | FAILED | 错误提示正确，功能不可用 |

- 测试：**222 passed**；ruff 全绿；前端类型检查 + 构建通过。
- RUN-003：专业前端升级（含新增 Agent 探索与编译中心）、DeepSeek 双模型真实接入
  （Explorer/Diagnosis，规则兜底诚实标记）、Benchmark 数据集 v2（30 场景 30/30 验证）、
  多 Worker 并发竞态修复（详见 docs/RUN-003-IMPLEMENTATION-REPORT.md）。
- Benchmark B（n=3）：传统报告基线首选根因 1/3、假通过 1/1；规则诊断 3/3、假通过 0/1。
- Benchmark C：沙箱 1/1、断言保持 1/1；无保护对照断言保持 0/1。

完整数值与阻塞项见 [`docs/BENCHMARK.md`](docs/BENCHMARK.md)、
功能状态见 [`docs/FEATURE-STATUS.md`](docs/FEATURE-STATUS.md)。

## 架构原则（不可改变）

1. Agent 是能力层，不是事实层。
2. Playwright 负责正式测试的确定性执行。
3. Oracle 独立于 Agent；正式 Oracle 必须来源可追溯并经具名人类审批。
4. Task 与 Run 分离；重试产生新 Run，不覆盖历史。
5. Execution Status / Test Verdict / Diagnosis / Disposition 四者分离。
6. Event 与 Evidence 分离；原始 Evidence 不得被派生结果冒充。
7. 相关性 ≠ 因果；证据不足允许 INCONCLUSIVE，且不等同 PASSED。
8. Self-Healing 不得削弱必需 Assertion、不得修改正式 Oracle。

## 目录

```
apps/api          FastAPI 平台（routers + services + Runtime 组合根）
apps/web          Vue3 + TS 工作台（14 个视图，真实 API；RUN-004-FE 产品化设计系统）
apps/teamflow     受控被测应用（页面 + 业务 API + 故障注入 + GT 隔离）
apps/workers      独立 Worker（租约 + fencing + 优雅退出）
packages/contracts    C01–C12 模型与枚举
packages/core         配置 / 日志脱敏 / 错误码 / ID / 安全策略
packages/persistence  统一 Schema / 迁移 / 事件 / 证据 / 产物 / 目录
packages/execution    浏览器会话 / 观察者 / 断言 / Verdict / 任务服务 / Pipeline
packages/agents       Explorer / Compiler / Gateway / 自愈规则
packages/evidence     Event Store / Evidence / Graph / Diagnosis / Probe
benchmark             UI-FaultBench 数据集（GT 私有）与实验执行器
tests                 unit / contracts / integration / e2e / teamflow
docs                  架构 / API / 数据库 / Agent / Playwright / 安全 / 交付报告
.workbuddy-run/       长期运行记录（恢复点、决策、测试结果）
资料/                 权威设计资料（只读）
```

## 文档

| 文档 | 说明 |
|---|---|
| [架构](docs/ARCHITECTURE.md) | 分层、链路、部署形态、与设计差异 |
| [API](docs/API.md) | API 端点与语义约定（含 RUN-004-FE 只读 LLM 摘要） |
| [数据库](docs/DATABASE.md) | 表结构、约束、迁移 |
| [Agent 与自愈](docs/AGENTS.md) | 探索/编译/诊断/修复机制 |
| [Playwright](docs/PLAYWRIGHT.md) | 浏览器执行、采集、已修缺陷 |
| [证据与诊断](docs/EVIDENCE-AND-DIAGNOSIS.md) | 事件/证据/图/诊断规则 |
| [Benchmark](docs/BENCHMARK.md) | 真实实验与阻塞项 |
| [安全](docs/SECURITY.md) | 威胁模型与测试 |
| [性能与可靠性](docs/PERFORMANCE.md) | 实测延迟、吞吐、可靠性验证 |
| [测试报告](docs/TEST-REPORT.md) | 测试与缺陷回归（RUN-004-FE 全量 224 passed） |
| [RUN-004-FE 设计决策](docs/RUN-004-FE-DESIGN-DECISIONS.md) | 前端重构选型、设计系统与统计口径 |
| [RUN-004-FE 实施报告](docs/RUN-004-FE-IMPLEMENTATION-REPORT.md) | 架构图、页面/API 变更、测试结果 |
| [RUN-004-FE UI 验收](docs/RUN-004-FE-UI-ACCEPTANCE.md) | 真实浏览器截图与交互验收 |
| [部署](docs/DEPLOYMENT.md) | 安装、配置、排错 |
| [功能状态](docs/FEATURE-STATUS.md) | 已运行 / 未验证 / 阻塞 / 待决策 |
| [最终交付报告](docs/FINAL/TraceGuard-Final-Delivery-Report.md) | 一页结论与交付索引 |

## 明确未完成

- LLM：DeepSeek 双模型已接入（Explorer/Diagnosis）；Compiler/Repair 的 LLM 辅助未接入；
- PostgreSQL 运行时未验证（BLOCKED：本机无实例）；Redis 未采用；MinIO/S3 未实现（本地内容寻址存储已实现完整性与篡改检测）；
- 外部站点泛化未授权（P-EXT）；
- 认证/RBAC 未实现（本地单租户）；
- P03 / P08 / 路线图冻结需负责人决策（见 `docs/decisions/`）。

## 开发规范

- 分支：`workbuddy/traceguard-autonomous`（本地开发，不推送远程）
- 提交：阶段性本地提交；禁止密钥入库；禁止强制推送/改写历史
- 测试：验收数字必须来自真实测试日志；禁止 Mock 冒充真实运行

## RUN-008 更新（2026-11-24）

- 开发分支：`workbuddy/run008-quality-hardening`（已推送远程）；基线 HEAD `ce1acea`。
- 本轮交付索引：`docs/RUN-008-FINAL-REPORT.md`（完成后）、`docs/RUN-008-BASELINE.md`、
  `docs/RUN-008-WP1-IMPLEMENTATION.md`、`docs/RUN-008-WP2-REGRESSION-CI.md`、
  `docs/RUN-008-WP3-WORKER.md`、`docs/RUN-008-WP5-FRONTEND-AUDIT.md`、
  `docs/RUN-008-SECURITY-AUDIT.md`、`docs/RUN-008-DECISIONS.md`。
- 回归基线：567 passed / 13 skipped；E2E 12/12；Ruff 全绿；前端 typecheck/build 通过。
- CI（commit `40efee6`）：PR Gate `45335765297` 与 Nightly `45335777381` 真实全绿；
  LLM smoke 因缺 Secret 真实标记 BLOCKED，不冒充通过。
- 安全边界：两次 AgentDR `info_theft_steal_env_keys` 拦截均按策略保留 BLOCKED，
  未绕过、未读取或传递真实凭据；详见 `.workbuddy-run/blockers.md` B-17。
- 功能状态以 `docs/FEATURE-STATUS.md` 的 RUN-008 段为准；旧段落是历史快照。
