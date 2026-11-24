# TraceGuard 自主开发进度

> **RUN-009 WP0 权威状态摘要（2026-11-24 16:05 CST）**：下方 RUN-001 段是历史起点快照，不再代表当前状态。
> RUN-009 已完成：WP0/1/2/5/6/7 通过，WP3/WP4 PARTIAL（宿主级网络封锁与 B-17 四场景如实 BLOCKED），
> WP8 PARTIAL（vue-tsc 升级 NEEDS_DECISION）。最终状态见 `docs/RUN-009-FINAL-REPORT.md`；
> 当前分支 `workbuddy/run008-quality-hardening`，`origin/main` = `ac2876e`，领先 32+ 提交。
> 不得把 `40efee6` 的三段 CI 结果外推到后续提交。以下历史记录保留不覆盖。

> 本目录为长期自主运行的持久化记录。每次中断后先读 `resume.md`，再读本文件与 `phase-status.md`。
> 严禁在此保存密钥、Token、密码或真实凭据。

- 最后更新：2026-11-22 00:12 (GMT+8)
- 运行编号：RUN-001
- 目标：基于路线图 v1.1 与架构基线 v1.1，从当前真实进度交付到完整 TraceGuard
- 目标时长：30h（最长 36h，以平台允许为限）

## 真实起点（只读审查结论）

- 项目根目录：`/Users/dlp--test/Desktop/traceguard`（唯一 TraceGuard 目录，无歧义）
- 根目录内容：仅 `资料/` 与 `资料.zip`（40MB）。**没有任何源代码、没有 Git 仓库。**
- 权威资料：
  - `资料/项目资料/00～10`（11 份设计文档，文档集 v1.1.0，架构基线 v1.1）
  - `资料/实施线路/TraceGuard-完整项目实施路线图-v1.1.md`（1821 行）
  - `资料/实施线路/TraceGuard-v1.1-修订摘要.md`（含 F01～F13、G01～G08 修订）
  - `资料/TraceGuard-开源项目调研/`（Agentic-Test-Explorer、TestZeus-Hercules 调研与借鉴台账）
- Git 状态：非 Git 仓库。工作区无用户未提交修改需要保护。
- 实际开发进度：**阶段 0 尚未开始（NOT_STARTED）**。全 14 阶段均为 NOT_STARTED。

## 路线图冻结状态（重要）

- 路线图 v1.1 原文明确标注为"评审修订稿，通过项目评审后方可标记为正式冻结基线"。
- 未检索到任何"正式批准/冻结"记录。**不声称路线图已正式冻结。**
- 但已冻结的设计语义（G01～G08 / F01～F13 中明确"已冻结"部分）作为实施依据：
  - 阶段 2 的 Test Verdict 四态聚合规则 + 8 条补充规则；
  - Event 至少一次投递、`workspace_id` 必填且服务端可信注入；
  - 三级 CI 门禁职责划分；Repair 沿用原状态机。
- 12 项待确认事项（P01～P12，路线图 7.2）保持待确认，尤其 P03（Oracle 多来源裁决）、P08（Repair 审批角色）。

## 已完成工作

1. ✅ 只读审查：范围、安全边界、资料、代码、Git 状态。
2. ✅ 建立长期运行记录目录与基线文件。
3. ✅ **阶段 0 完成**（ACCEPTED*）：Git 仓库+分支、目录骨架、FastAPI/Vue3 骨架、统一配置/日志脱敏/错误/健康检查、C01-C12 共22个 JSON Schema、契约测试、CI 三级门禁配置、安全基线、依赖锁定（requirements.lock.txt + package-lock.json）。测试 22/22 通过；API 真实 HTTP 启动验证成功。
4. ✅ **阶段 1 完成**（ACCEPTED）：TeamFlow 最小被测应用（登录/Workspace/项目创建列表详情编辑）、独立业务状态查询、测试数据重置/种子、五类故障注入（TF-API/NET/DB/UI/TIME-001）、Ground Truth 隔离（403 验证）、场景元数据。测试 9/9 通过；curl 真实业务闭环验证成功（含 TF-DB-001 假成功被独立状态揭穿）。
5. ✅ **M0 里程碑达成**。全项目累计 31 测试通过，lint/format 全绿。

## 最终状态（2026-11-22）

- **阶段 0～13 核心能力全部实现**；178 测试全通过，lint/format 全绿；10/10 演示场景 PASS。
- **M0～M4 达成**；M5 部分达成（阶段12 外部泛化因未授权 BLOCKED，如实记录）。
- 最终交付报告：`docs/FINAL/TraceGuard-Final-Delivery-Report.md`；证据：`tests/reports/`。
- 限制：Playwright 真浏览器 PoC / Vue build / 外部泛化 / Redis-Celery 分布式 待具备环境验证（详见报告第 14/15 节）。

## 当前阶段

- 阶段 0、1、2、3：已完成并提交（Git: c9d3a45、fc1397c、eb99ed2、08eebd8）。
- **M0、M1 里程碑达成**。累计 63 测试通过，lint/format 全绿。

## 已完成（续）

6. ✅ **阶段 2 完成**：确定性执行内核——Verdict 四态聚合（13测试，含全部合法/非法样例+8补充规则）、Oracle Manager（来源/审批校验、冲突检测）、Assertion Evaluator（6测试）、TestCase 加载器 + 14 用例 + 10 Oracle、HttpObserver(降级)/PlaywrightObserver(真浏览器代码就绪)、LocalExecutor、Trusted Oracle 独立时序、E2E 4测试、演示报告。修复了"业务状态查询时序"真实 bug。
7. ✅ **阶段 3 完成**：Task/Run/Step 持久化服务 + 状态机 + 租约 fencing（F06）+ 幂等 Task 创建 + 重试创新 Run + execution_status/test_verdict 独立 + Task 展示汇总 + 平台 API 挂载 + 9 测试 + curl 真实 HTTP 验证。

## 下一步

- 阶段 4：Event 与 Evidence 基础设施。Event Envelope（context_refs/workspace_id 服务端注入）、幂等消费、Consumer Inbox、序号缺口、Evidence 分类/完整性/SHA256/脱敏。
- Playwright 真浏览器 PoC 因本机网络极慢（累计>30分钟未装成）记录为限制，CI 环境验证；本机用 HttpObserver 降级路径并明确标记 mode。

## 环境约束（真实）

- Python 3.13.12、Node 22.22.2、npm 10.9.7、git 2.39.5
- **Docker 不可用、psql 不可用、uv/poetry 不可用**（不改系统环境）
- PyPI 与 npm registry 可联网（已验证 fastapi=0.165.0、vue=3.5.46 可查）
- 决策：阶段 0/1 采用项目内 venv + SQLite（通过 SQLAlchemy 抽象，可平滑切 PostgreSQL）；Docker Compose 配置照常产出但不作为本环境验收硬依赖，记录为限制。

## RUN-002 进展（2026-11-22）

### 里程碑 A：真实浏览器执行打通（PASS，有真实证据）
- 安装 playwright 1.65.2 + Chromium 159（清华/npmmirror 镜像，此前报告的"下载太慢"已解决）。
- 新增 `traceguard_execution/browser/`：BrowserManager（thread-local，多渠道路径 bundled→msedge→chrome）+ BrowserSession（console/pageerror/network/dialog/trace/截图/DOM/ARIA）。
- 重写 PlaywrightObserver：全定位策略、全动作、逐步证据、trace。
- 新增 `runtime/pipeline.py`：Run 领取租约 → 执行 → 逐步落库/事件/证据 → Trusted Oracle 状态查询 → 断言 → Verdict → 提交。
- 真实运行 4 场景（Chromium 159，TeamFlow 真实服务）：
  - 正常创建：PASSED（8 步，24 证据）
  - False Pass（TF-DB-001）：FAILED（业务断言识破 UI 假成功）
  - UI 提示缺失（TF-UI-001）：FAILED（UI 断言失败，业务断言通过）
  - API 500：PASSED（错误提示出现且无脏数据）
- 真实截图已人工核验（1280x800 PNG，页面含"创建失败：simulated API failure (TF-API-001)"）。

### 里程碑 B：统一持久化层（PASS）
- 新增 `packages/persistence`：单一 Database（SQLite/PG 同构）、迁移表、事件/证据/目录/产物/文档聚合表。
- Event：`(producer_instance_id, sequence)` 与 `(producer_id, idempotency_key)` 双唯一约束，冲突审计落库，重启可读。
- Evidence：不可变 + 派生链 + 完整性校验 + 产物内容寻址存储（sha256，篡改即报错）。
- Catalog：用例版本不可变、审核需具名人类、Oracle Agent 不能自批（版本递增保留历史）。
- TaskService 全量迁移到统一库（保留原契约）；新增 claim_next_run / reap_expired_runs / 队列统计。

### 修复的真实 Bug
1. `assertions._apply_rule`：`observation["json"]` 为 dict 且字段为 int 时调用 `.get()` → AttributeError（新增 `_resolve_field`，支持嵌套字段）。
2. `logging.get_logger` 返回标准 Logger，但全项目按 `logger.info("event", key=val)` 调用 → TypeError；新增 StructuredLogger（extra 透传 + 保留字规避）。
3. Playwright sync API 在同线程反复 start/stop → "Sync API inside asyncio loop"；改为 thread-local 复用 + 模块结束释放。
4. 前端此前 45 行仅显示 health JSON（报告与实现不符，已记录）。

### 测试
- 全量 190 passed（新增 9 持久化单测 + 3 真实浏览器 E2E）。
- ruff check / format 全绿。

### 里程碑 C：完整 API（PASS）
- 81 个端点（system/catalog/execution/evidence/intelligence/benchmark），OpenAPI 可查。
- 任务一键执行（入队/同步）、队列统计与过期回收；执行走线程池避免事件循环冲突。
- 统一错误码映射（404/409/412/422/403），集成测试 8 项覆盖门禁与越权。

### 里程碑 D：前端工作台（PASS）
- Vue3 + TS + vue-router，9 页：总览/需求/资产/执行/Run详情/诊断/修复/Benchmark/系统。
- 真实 API、loading/error/empty、执行中轮询、证据图 SVG、修复生命周期按钮。
- 单端口部署（API 托管 dist），浏览器截图人工核验；`vue-tsc + vite build` 通过。

### 里程碑 E：业务闭环（PASS）
- Explorer 真实探索（3 动作 allowed）→ Compiler 候选（PENDING + warning）→ 人工审核。
- 诊断→图→探针→处置→修复（沙箱真实执行/审批/CAS 晋升/回归/回滚）全链路实测。

### 里程碑 F：Benchmark 真实实验（PASS，部分基线 BLOCKED）
- B：A 0.333 / C,D,E 1.000（n=3）；假通过 A 1/1、C/D/E 0/1。
- C：沙箱 1/1、断言保持 1/1、无保护对照 0/1、回归失败 0/1。
- A：人工 4/4、编译器 2/2 执行成功；无可评估断言（Oracle 未绑定）。
- LLM / 手工基线 BLOCKED，不产生数值。

### 里程碑 G/H：RUN-003-G LLM 接入 + RUN-004-FE 前端产品化（PASS）
- RUN-003-G：DeepSeek Flash/Pro 真实接入 Explorer/Diagnosis；Benchmark 三组真实对照（详见 RUN-003-G 报告）。
- RUN-004-FE：14 视图产品化升级、Explorer 展示真实 engine/llm_stats、新增 `/models` 与只读汇总 API。
- 验收：224 passed（含新增 2 项）、ruff 全绿、typecheck/build 通过、真实 Chromium 53/53、截图 15 张。

---

## RUN-005-CORE（2026-11-23）

| 阶段 | 工作包 | 结果 |
|---|---|---|
| WP0 | 基线审计 | `docs/RUN-005-BASELINE.md`；16 项模块状态重新核定；测试基线 224 |
| WP1.1 | ProbeBudget 生命周期 | 预算注册表 + 持久化 + 关闭语义；15 单测 + 6 集成 |
| WP1.2 | Active Planner 闭环 | `plan_and_probe()` 真实执行；真实 Run 2 探针 + 证据 |
| WP1.3 | 真实 SSE | `/events/stream` 重写；7 测试；前端订阅 + 降级 |
| WP1.4 | Consumer Inbox | 状态机 + 租约 + 事务 + 真实消费者投影；9 单测 + 3 集成 |
| WP1.5 | Event Store 并发 | DB 原子序号分配器；4 进程实测 100 事件无冲突 |
| WP2.1 | LLM 编译器 | 三模式 + 顾问 + 稳定性回放；真实冒烟 2 步 1 断言 |
| WP2.2 | LLM 探针规划 | 真实选择 + 白名单过滤；闭环中 `called=true` |
| WP2.3 | LLM 修复规划 | 真实 3 条受限变更；首次 JSON 失败如实 fallback |
| WP2.4 | 可观测性 | `/system/llm/summary` 增加三类调用/兜底 |
| WP3 | Benchmark | v2 30/30 复验（真实浏览器）；v3 多臂 BLOCKED |
| WP4 | CI/PG/前端验收 | 三级门禁工作流；PG parity 测试；UI 30/30 + 18 截图 |
| WP5 | 安全与可靠性 | 13 项复查；npm audit 1 high(dev-only)+4 moderate 记录 |
| WP6 | 集成验收 | 隔离库真实执行 3 个场景（PASSED/FAILED/FAILED）+ 诊断 + 探针闭环 |

累计回归：285 passed / 5 skipped / 0 failed。

---

## RUN-006（2026-11-23）核心正确性与安全性

- 起点：285 passed / 5 skipped（指令基线 `dadb4ae`）。
- 终点：**367 passed / 5 skipped / 0 failed**；`ruff check` 全绿；真实 Chromium E2E 11 passed。
- 交付：`docs/RUN-006-BASELINE.md`、`RUN-006-IMPLEMENTATION-REPORT.md`、`RUN-006-TEST-REPORT.md`、
  `RUN-006-SECURITY-RELIABILITY.md`、`RUN-006-DECISION-REQUESTS.md`；证据 `docs/assets/run006/`。
- WP1 五项均落地并有可复核证据；WP2–WP6 按实际状态标 PARTIAL/BLOCKED/NOT_IMPLEMENTED，未虚报。

## RUN-007-RESUME 进度（2026-11-23）

| WP | 内容 | 状态 |
|---|---|---|
| WP0 | 基线审计 + 分支创建 + `docs/RUN-007-BASELINE.md` | ✅ 完成（origin/main 校验一致） |
| WP1 | 五项修复复核 + 迁移 v8 幂等/旧库升级 | ✅ 完成（163 passed；4 迁移用例；修 3 个真实缺陷） |
| WP2 | E2E 改独立 Worker 消费队列 | ✅ 完成（55 步 0 失败，证据重取） |
| WP3 | 前端 15 视图 × 4 断点 + 深色 + 四态 | ✅ 完成（75/75，人工复核 + 修 3 处缺陷） |
| WP4-01 | PG16 Service Container 扩展验证 | ⚠️ 用例与 CI 就绪；本机无 PG → BLOCKED |
| WP4-02 | Worker 故障恢复 | ✅ 完成（17/17 checks） |
| WP4-03 | 60 分钟长稳 | ✅ 完成（65 周期全 PASSED） |
| WP5 | CI 三级门禁修复 | ⚠️ 逻辑已修 + YAML 校验；无凭据 → 未真实触发（IMPLEMENTED） |
| WP6 | UI-FaultBench v3 四臂 + LLM 臂 | ✅ 完成（含 D/E 无增益的负结论） |
| WP7 | 七份报告 + README/FEATURE-STATUS/台账 + 状态文件 | ✅ 完成 |
| — | 分支推送 + PR | ❌ BLOCKED（无 Git 凭据） |

## RUN-007-RESUME 收尾（2026-11-23 深夜）

| WP | 变更后状态 |
|---|---|
| WP4-01 PostgreSQL 16 | ⚠️ → ✅ **PASS（CI 真跑 16.16，6 用例）**；本机仍无 PG |
| WP5 CI 三级门禁 | ⚠️（未运行）→ ✅ **PASS**（同 commit 三级全绿） |
| 分支推送 + PR | ❌ BLOCKED → ✅ **PASS**（PR #1） |
| CI 首跑暴露缺陷 | 8 个（2 个产品缺陷 + 6 个测试/CI 缺陷），4 轮修完 |
