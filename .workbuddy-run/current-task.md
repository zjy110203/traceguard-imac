# 当前任务（RUN-017 收口，2026-11-26 22:40 CST）

> 分支 `workbuddy/run017-product-maturity`，最终 HEAD 待推送。
> 本轮全部可自主完成的工作已做完：
> - WP0 基线（12 项技术债登记）、WP1 CI（真实触发，PR #75 success）、
>   WP2 独立观测重写 + 浏览器观测 + 120 样本实验、WP3 人工授权新 Attempt（15 测试）、
>   WP4.1 重入防护 / WP4.2 GC 分页 + 10 万级实测 / WP4.3 后端边界 /
>   WP4.4 PG16 62.05 分钟长稳（33/33 PASS、0 NOTE）、
>   WP5 备份恢复（SQLite+PG16 各 12 项）+ doctor + 文档同步、
>   WP6 UI 业务闭环 58/0 + 128 格矩阵全过、WP7 诊断评估 + 1 处规则修复（A/B 中性）。
> - 全量：SQLite 932 passed / PG16 专项 28 passed / Ruff / vue-tsc / vite / npm audit 全绿。
> 禁止：改 main、强推、reset --hard、git clean、自动合并 PR、读取凭据。

---

# 当前任务（RUN-017，2026-11-26 21:55 CST）

> 分支 `workbuddy/run017-product-maturity`（起点 `f18e871`，已推送 `1faebec`）。
> 目标：产品成熟度、智能诊断完善、最终交付闭环。
> 已完成：WP0 基线、WP1 CI（真实触发成功）、WP2 观测修复 + 30×2 实验、WP3 重试、
> WP4.1/4.2/4.3、WP5 备份恢复 + doctor、WP6 文档同步（UI 验收待跑）。
> 进行中：WP4.4 PG16 严格 62 分钟长稳（后台）、WP6 UI 全链路、WP7 诊断分析。
> 禁止：改 main、强推、reset --hard、git clean、自动合并 PR、读取凭据。

---

# 当前任务（RUN-016 收口，2026-11-26 19:30）

> **RUN-016 已完成本轮全部可独立完成的工作**，分支 `workbuddy/run016-finalization`，起点 `73e4fe6`。
> - 长稳：PG16 严格 **62.14 分钟**（要求 ≥60，无容差）/ 113 Run 全 PASSED / 31 PASS 0 FAIL 1 NOTE（SIGSTOP 未触发）。
> - Regression：10 个 OS 进程 + PG16，13/13 判定通过（唯一 Claim/Run/History）。
> - Promotion：新增失败恢复（plan + 人工 confirm），7 项测试；INCONCLUSIVE 只出决策稿。
> - Artifact：put 纳入 Fencing + 原子写 + 巡检/孤儿回收；729 文件 ↔ 729 登记一致，孤儿 0。
> - Benchmark：故障效果改独立探针；v2 False Pass 必须有独立证据；GT 路径被 TargetPolicy 阻断；23×2 NOT_RUN。
> - 权限/SSE/前端：/system/config 脱敏；SSE 上限 5s；UI 全链路 58 PASS / 0 FAIL（15 截图）。
> - 质量门禁：895 passed / 0 failed / 0 skipped；Ruff / vue-tsc / vite build 通过。
> - CI：Nightly 汇总拆列 + Release Gate 强制 LLM 证据 PASSED；GitHub Actions 本轮 NOT_RUN（无凭据）。
> 权威文档：`docs/RUN-016-FINAL-REPORT.md`；日志 `docs/RUN-016-WORKLOG.md`；证据 `docs/assets/run016/`。
> 禁止：改 main、自动合并 PR、关闭 PR #1/#2、强推、reset --hard、git clean。
> B-17 / 宿主隔离 / 外部站点 / 人工基线 / LLM 四臂 / Release Gate / 23×2 仍 BLOCKED 或 NOT_RUN。
> 待决策：INCONCLUSIVE 重试、LICENSE、PR 合并、23×2 实验窗口、纯 UI 故障观测投入。

---

# 当前任务（RUN-015 执行中，2026-11-26 追加；下方历史保留）

> **当前阶段**：RUN-015 `workbuddy/run015-execution-consistency` 收口完成，基线 `438c54a`。
> WP0~WP7 已交付；PG16 长稳复验 59.71 分钟 / 115 Run / 0 FAIL，全链路 40/40；
> 全量 828 passed / 16 skipped，Ruff、vue-tsc、vite build 通过。
> 权威文档：`docs/RUN-015-FINAL-REPORT.md`，专题 `RUN-015-WP{0,1,2,3,4,5,6}-*.md`，
> 工作日志 `RUN-015-WORKLOG.md`，证据 `docs/assets/run015/`。
> 禁止：改 main、自动合并 PR、关闭 PR #1/#2、强推、reset --hard、git clean。
> B-17 / 宿主隔离 / 人工基线 / LLM 四臂 / Release Gate / Benchmark 新一轮仍 BLOCKED 或 NOT_RUN。

---

# 当前任务（RUN-011-R1.1 交付，2026-11-25 追加；下方 R1 历史保留）

> **RUN-011-R1.1 已交付**：Workspace 安全隔离 + Repair 血缘/来源可信 + Benchmark 口径修正 + 两轮闭环重测 + UI 验收。
> P0/P1 修复全绿：隔离与血缘测试 23/23；两轮闭环 API 86/86 ×2、DB 逐实体 Workspace 24/24 ×2；
> UI 验收 15/0（+2 NOTE）；全量 690 passed + PG16 专项 13 passed；Ruff 全绿。
> 权威报告：`docs/RUN-011-R1.1-FINAL-REPORT.md`（含最终 10 问）；UI：`docs/RUN-011-R1.1-WP6-UI-ACCEPTANCE.md`。
> 已知边界（如实登记）：前端无 workspace 切换控件（硬编码 default）；B-17 保持 BLOCKED；
> 无真实 GitHub Actions 证据不标记 CI PASS；未合并 PR、未改 main。

---

# 当前任务（RUN-011-R1 交付，2026-11-25）

> **RUN-011-R1 已完成（B-17 保持 BLOCKED）**：单一 TestCase 血缘 18 步闭环两轮
> （API 86/86 + DB 17/17 ×2）；时间三态 + TF-DB-004 四场景 4/4；假设去重 5 场景；
> Benchmark v5 三场景 Pilot 15/15 + 23 场景×2 轮（37 样本，探针 54/54 真实执行）；
> Egress 误拦根因修复（复验 6/6）；PG 并行修复（680 串行 + 3 进程并行 2/3~3/3 全绿）。
> 权威报告：`docs/RUN-011-R1-FINAL-REPORT.md`（含最终 9 问）；基线：`docs/RUN-011-R1-BASELINE.md`。
> BLOCKED（保持）：B-17（安全批准申请已备）、宿主级隔离、LLM 计费臂、人工基线、CI、增量 PR。
> NOT RUN（保持）：UI 全链路点击、多 API/DB 断连/长事务、生产部署验收。
> 测试终态：**680 passed / 0 failed / 0 skipped**（PG16）+ Ruff 全绿。
> 待负责人决策：PR 创建与合并、LICENSE、B-17 安全批准（材料见 WP7 报告）。

---

# 当前任务（RUN-011 交付，2026-11-25 02:25 CST）

> **RUN-011 已完成（PARTIAL 明确标注）**：在独立 API + 3 独立 Worker + PG16.16 + 真实 Chromium +
> 强制出站代理 + 认证开启的拓扑下，两轮完整业务闭环各 **62/62** 通过，数据库级复核各 **8/8**。
> 分支 `workbuddy/run011-full-loop`（从 RUN-010 `b470518` 创建）。
> 权威报告：`docs/RUN-011-FINAL-REPORT.md`；基线：`docs/RUN-011-BASELINE.md`。
> 未完成/阻塞：Benchmark v5（NOT RUN，v4 测量无效）、B-17（BLOCKED）、宿主级隔离（BLOCKED）、
> UI 全链路（NOT RUN）、多 API/DB 断连（NOT RUN）、CI（BLOCKED，无 gh/凭据）。
> OPEN-RISK：授权目标 https 偶发 PORT_NOT_ALLOWED、时序错误确认、LLM 候选未去重。
> 已推送：`origin/workbuddy/run011-full-loop`（提交 `f604e5d`）。
> 增量 PR 未创建（无 gh、不读取凭据）；创建入口：https://github.com/zjy110203/traceguard-imac/pull/new/workbuddy/run011-full-loop
> 待负责人决策：PR 创建与合并、LICENSE、B-17 安全批准。

---


# 当前任务（RUN-010 交付，2026-11-24）

> **2026-11-25 00:30 CST 收尾完成（RUN-010-FINAL-CLOSEOUT）**：统计更正、CL-01/CL-02 缺陷修复、
> WP4/WP6 报告补齐、PR #2 标题与描述更新；三级 CI 门禁在 `d4d59ba` 上全部 success。
> 待负责人决策：PR #2 合并时机、LICENSE 选择、B-17 安全批准。
> 权威报告：`docs/RUN-010-CLOSEOUT-REPORT.md`、`docs/RUN-010-FINAL-REPORT.md`。
> 历史内容保留在下方。

---

# 当前任务（RUN-010 交付，2026-11-24 22:45 CST）

> RUN-010 已完成：全部 10 个工作包交付，见 `docs/RUN-010-FINAL-REPORT.md`。
> 待负责人决策：PR #2 合并时机、LICENSE 选择、B-17 安全批准。
> 历史内容保留在下方。

---

# 当前任务（RUN-009 交付，2026-11-24）

> 历史标题「RUN-002 结束点」保留在下方。RUN-009 已完成，最终状态见 `docs/RUN-009-FINAL-REPORT.md`。

## RUN-009 收尾

- WP0/1/2/5/6/7 通过；WP3/WP4/WP8 PARTIAL（宿主级网络封锁、B-17 四场景、vue-tsc 升级如实 BLOCKED/NEEDS_DECISION）。
- 新增认证/RBAC、受控出站代理、14/14 路由验收、资源监控、Benchmark v4、供应链审计与完整交付报告。
- PR #2 保持 open/HOLD，合并决策记录在 `docs/RUN-009-REVIEW-REPORT.md` 与最终报告。

## 历史快照（RUN-002 结束点）

# 当前任务（RUN-002 结束点）

## 已完成

- 真实浏览器执行链路（Chromium + 证据 + Pipeline + Worker）
- 统一持久化层（事件/证据/产物/目录/迁移）
- 完整 REST API（81 端点）+ 9 页 Vue 工作台 + 单端口部署
- Explorer → Compiler → 审核 → 执行 → 诊断 → 图 → 探针 → 修复 → Benchmark 全链路
- 22 个契约 Schema 导出 + 防漂移测试
- 文档体系重写（架构/API/DB/Agent/Playwright/证据/自愈/Benchmark/安全/测试/部署/交付）

## 下一步建议

1. 接入 LLM 适配器，补 Benchmark 缺失基线；
2. 扩数据集（≥30 场景）+ Macro-F1 / Time to Diagnosis 埋点；
3. PG 实测、Worker 心跳续租、多实例压测；
4. P08 RBAC 落地；P03 契约调整（待批准）；
5. 前端 SSE 与图交互增强。

---

## RUN-004-FE 更新（2026-11-23，追加，不改写上方 RUN-002 历史记录）

- 已完成：前端产品化重构（统一设计系统 / AppShell / 14 视图）、Explorer 真实 LLM 信息展示、新增 `/models` 与只读 `GET /api/v1/system/llm/summary`。
- 验证：224 passed、ruff 全绿、typecheck/build 通过、真实 Chromium 53/53、六档断点无溢出。
- 证据：`docs/RUN-004-FE-IMPLEMENTATION-REPORT.md`、`docs/RUN-004-FE-UI-ACCEPTANCE.md`、`docs/assets/run004-fe/`。
- 上方“下一步建议”第 1 项（LLM 适配器 / Benchmark 基线）已由 RUN-003-G 完成；本轮不再重复。其余项状态未变。

---

## RUN-005-CORE 更新（2026-11-23，追加）

- 已完成（VERIFIED）：探针预算按 Run/Diagnosis 隔离与持久化；Active Diagnosis 真实闭环；
  真实 SSE（Last-Event-ID/过滤/心跳/轮询降级）；Inbox 持久化状态机 + 时间线投影真实消费者；
  Event Store 多进程序号原子分配。
- 已完成（实现 + 真实冒烟）：LLM 编译器（三模式）、LLM 探针规划、LLM 修复规划、三类可观测性。
- 已完成（编排/语义）：候选稳定性回放 F08（独立 Run + sandbox + Oracle 门槛 + 人工审核）。
- 已完成（工程）：CI 三级门禁工作流、前端自动化验收脚本（30/30）、PG parity 测试（本地 skip）。
- 未完成：Benchmark v3 多臂（BLOCKED）、60 分钟长稳（BLOCKED）、4 Worker 复验（PARTIAL）、
  PG 本地实测（BLOCKED）、P03/P08（NEEDS_DECISION）。

---

## RUN-006 更新（2026-11-23，追加）

- 已完成（VERIFIED）：Oracle 绑定/审核门禁/版本固定/主体类型校验/并发审核 CAS；
  探针证据回灌 + 动态重诊断 + 冲突真实计算 + 全 STOP_* 原因；预算故障安全与跨进程 CAS；
  晋升/回滚单事务原子 CAS；统一 TargetPolicy（Explorer + Runner + 请求级拦截）。
- 关键漏洞修复：`suggested_by == "llm"` 精确匹配导致 `llm:*` 假设可保留 CONFIRMED（已改为前缀 + 状态后降级）。
- 测试：285 → **367 passed / 5 skipped**；真实 Chromium E2E 11 项通过。
- 真实环境：正常/故障 Run、诊断、probe-plan、真实 http_state 回灌、图节点均已实测（`docs/assets/run006/`）。
- 未完成：WP2 全链 E2E（PARTIAL）、WP3 前端（NOT_IMPLEMENTED 本轮）、WP4 PG/Worker/长稳（BLOCKED）、
  WP5 CI 真实运行（BLOCKED）、WP6 Benchmark v3（NOT_IMPLEMENTED 本轮）。

---

## RUN-007-RESUME 更新（2026-11-23，追加）

- 分支：`workbuddy/run007-resume-20261123`（基于 `origin/main` `ac2876e`）
- 已完成（VERIFIED）：WP1 复核（163 passed）、迁移 v8 幂等/旧库升级、E2E 独立 Worker 队列（55 步 0 失败）、
  证据来源修正与图绑定校验、沙箱走队列（fail-closed）、Run 级状态缓存隔离、前端 15 视图验收（75/75）、
  Worker 故障恢复（17/17）、60 分钟长稳（65 周期/19 checks）、Benchmark 四臂 30 场景 + LLM 两臂。
- 已完成（IMPLEMENTED，未运行）：CI 三级门禁修复（PR 分支过滤、Nightly 去掩盖、Release 去 RUN-005 依赖 +
  Actions API 校验 Nightly `head_sha`）。**无 GitHub 凭据 → 未触发**。
- 阻塞：分支推送/PR（无 https 凭据、无 SSH key、无 token）；本机 PG16 运行时（无 Docker/psql）；
  人工诊断基线（无人力）；Routed 臂（未触发 reasoning 升级）。
- 未完成（如实登记）：Benchmark 中定位器场景的自愈"沙箱通过 → 晋升"断点未定位（回归 0/1）。
