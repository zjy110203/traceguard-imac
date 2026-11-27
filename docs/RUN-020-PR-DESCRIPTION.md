# RUN-019-R2 正式 PR 准备材料

> 用途：授权后在 `zjy110203/traceguard-imac` 创建 PR 时直接作为 `--body-file`。
> 本文件**不创建 PR、不合并、不强推**。基线 `main@ac2876e`，head
> `workbuddy/run019-product-completion@564b21c`。审计见 `docs/RUN-020-PREPARATION-AUDIT.md`。

---

## PR 标题

```
RUN-019-R2: 产品收口、UI E2E 修复与发布准入（含 RUN-008~RUN-019）
```

备选（若评审要求按轮次拆分）：

```
RUN-019-R2: 三个真实缺口收口（UI 验收 / 来源可信 / 权限门禁）+ 发布准入材料
```

## 概览

本 PR 把 `main`（停在 RUN-007 收口点 `ac2876e`）推进到 **RUN-019-R2**：产品功能完整化、
执行内核可靠性、智能诊断升级、权限与来源可信治理、三级 CI 门禁与长稳验收。
**172 个提交、线性历史（0 个 merge 提交）、2967 文件、+313,389/−2,354。**

- head：`workbuddy/run019-product-completion` → 代码 HEAD `564b21c`
  （其后 `f409bf4` 仅追加 RUN-020 准备文档与证据，无代码改动；PR Gate #116 通过）
- 已跑通的 CI（HEAD 上）：**PR Gate #115 SUCCESS（3m40s）**、
  **Nightly Gate #68 SUCCESS（6/6 Job，10m52s）**
- 状态：**HOLD（等待 Reliability Gate + CI 真实 LLM + 人工评审）**
- 不含：main 合并、强推、历史改写、自动合并

## 1. 变更摘要

### 1.1 按轮次（RUN-008 → RUN-019-R2）

| RUN | 主题 | 关键交付 | 权威报告 |
|---|---|---|---|
| 008 | 质量加固 | 长稳 60.14 分钟 / 66 周期 / 23 checks；Benchmark 30 场景×2 轮；前端 15 视图验收 | `RUN-008-FINAL-REPORT.md` |
| 009 | 安全与智能 | 认证/RBAC/审计、受控出站代理三层、诊断 v1 增强、供应链审计 | `RUN-009-FINAL-REPORT.md` |
| 010 | 集成与智能成熟 | 认证产品化、出站代理接入内核、提交幂等、诊断 v2、Benchmark v4、a11y | `RUN-010-FINAL-REPORT.md` |
| 011 | 全链路闭环 | Explorer/Compiler/TestCase/Oracle/Replay/Diagnosis/Probe/Repair 闭环、PG16 | `RUN-011-FINAL-REPORT.md` |
| 012 | 产品完整性 | Workspace UI、全局指标、业务闭环 UI、AI 可靠性 | `RUN-012-FINAL-REPORT.md` |
| 013 | 生产可靠性 | 独立 Worker、Workspace 并发、Replay 稳定性、soak | `RUN-013-FINAL-REPORT.md` |
| 014 | 产品收口 | Regression 闭环、租约 fencing、SSE 安全、全血缘 UI | `RUN-014-FINAL-REPORT.md` |
| 015 | 执行一致性 | fencing 最终一致、Regression 幂等、SSE 间隔上限、PG16 soak | `RUN-015-FINAL-REPORT.md` |
| 016 | 交付定稿 | Artifact 生命周期、Regression 重试、Benchmark 独立观测、CI | `RUN-016-FINAL-REPORT.md` |
| 017 | 产品成熟度 | 故障观测“测得对”、Regression INCONCLUSIVE 授权重试、备份恢复、三级门禁 | `RUN-017-FINAL-REPORT.md` |
| 018 | 诊断闭环 | 双提交观测、Artifact GC 并发、诊断反馈闭环、独立 Holdout、a11y 65/65 | `RUN-018-FINAL-REPORT.md` |
| 019 | 产品完整性收口 | 资产管理 API/UI 完整化、诊断证据链升级、PG16 全血缘 40/40 | `RUN-019-FINAL-REPORT.md` |
| **019-R2** | **真实缺口收口** | **UI 验收对齐可视化编辑器 / 来源可信 / 权限门禁 / 版本完整性** | `RUN-019-FINAL-REPORT.md` + `RUN-019-R2-GAP-CLOSURE.md` |

### 1.2 RUN-019-R2 的 7 个提交（可直接 review）

| 提交 | 内容 | 为什么必须改 |
|---|---|---|
| `2b90d6c` | `StepEditor.vue` 补 14 个 `data-testid` + 断言 `expected` 可视化编辑；UI 验收脚本改为真实可视化操作 | Nightly #65 真实失败：脚本仍在 `page.fill("#tc-steps")`，而该元素已被可视化编辑器取代 → 30s 超时 |
| `87696a6` | `compile_source` 由服务端可信流程决定（公共 HTTP 固定 `human`；compiler/healed 仅可信入口 + 血缘） | 客户端可自称 compiler/healed 绕过稳定性回放，还能靠新建版本“洗来源” |
| `cbc620f` | 8 个视图补 `can()` 权限门禁，四角色真实验收 24/24 | viewer 在 UI 上能点到不该点的动作（服务端仍 403，但前端误导） |
| `dfb49b2` | Monitoring 统计范围显式化；`TestCaseVersion.authoring_hash` 写入 + 审核/绑定时复核 | 不传 Workspace 时静默按“全部授权空间”统计；`immutable` 只是标记，改库无法发现 |
| `19f527f` | 收口清单 + 本地证据（UI 42 项 / RBAC 24 项）+ 格式化 | 证据与文档落库 |
| `7988210` | `scripts/ci_pytest_watchdog.py`：15 分钟打印线程栈、35 分钟强制失败 | 防真实挂起拖到 45 分钟 Job 超时（不降低断言强度） |
| `564b21c` | 最终报告 + 交付台账 + CI 真实结果回填 | 收口 |

### 1.3 规模与热点目录

| 目录 | 内容 |
|---|---|
| `apps/api` / `packages/*` | API、执行内核、持久层、证据与诊断、Agent 网关 |
| `apps/web` | Vue 3 前端（19 视图，含 StepEditor / Workspaces / OracleDetail） |
| `apps/teamflow` | 被测系统（真实业务闭环） |
| `apps/workers` | 独立 Worker（队列消费 + fencing） |
| `tests/` | unit / contracts / integration / e2e / teamflow |
| `docs/` | 每轮 BASELINE / WORKLOG / WP 专题 / FINAL-REPORT |
| `docs/assets/` | 各轮验收证据（241.7 MB，占入库体积 95%） |
| `.github/workflows/` | `pr-gate` / `nightly-gate` / `release-gate` / `reliability-gate` |

## 2. 测试结果（全部为真实执行）

### 2.1 CI（HEAD `564b21c`）

| 门禁 | 运行 | 结论 | 明细 |
|---|---|---|---|
| PR Gate | **#115** | **SUCCESS** 3m40s | backend（ruff check / ruff format / unit+contract / fast integration）+ frontend（`vue-tsc --noEmit` + `vite build`） |
| PR Gate（文档提交） | **#116** @ `f409bf4` | **SUCCESS** 3m19s | 仅文档 + 证据，无代码改动 |
| Nightly Gate（文档提交） | **#69** @ `f409bf4` | **SUCCESS（6/6）** 10m14s | backend-full 10m7s、ui-acceptance 2m15s、postgres 1m1s、security 15s、llm-smoke 20s（BLOCKED）、gate-verdict 2s |
| Nightly Gate | **#68** | **SUCCESS（6/6）** 10m52s | backend-full 9m33s、ui-acceptance **2m5s**、postgres 1m3s（PG16）、security 20s、llm-smoke 23s、gate-verdict 4s |
| ├ llm-smoke | 同上 | Job SUCCESS，**真实调用 BLOCKED** | 仓库无 `DEEPSEEK_API_KEY`；gate-verdict 注明“不得用于 Release 放行” |
| Reliability Gate | — | **NOT_RUN** | 仅 `workflow_dispatch`；本机无 `gh`/令牌（见 `RUN-020-RELEASE-ADMISSION.md` §1） |

### 2.2 本地（HEAD `564b21c`，本轮复审）

| 项 | 结果 |
|---|---|
| 后端全量（SQLite） | **1144 passed / 16 skipped / 0 failed**，382.40s（16 skip 均为本地无 PG，逐条打印原因） |
| ruff check / format | All checks passed / 281 files already formatted |
| UI 全链路（core） | 42 项 / **41 PASS / 0 FAIL / 1 NOTE**（真实 API + TeamFlow + Chromium + 认证 + 2 独立 Worker + 服务端回读） |
| RBAC 四角色 | **24/24**（真实登录 + 真实浏览器 + 绕过前端直连验证 403） |
| a11y | **77/77**（19 视图 × 2 主题 × 2 断点 = 76 + 1 弹窗焦点） |
| 版本完整性 / 来源可信 | 5/5、8/8（R2 专项集成测试） |
| 真实 LLM（本地） | **11/11 REAL_PROVIDER PASS**（Flash 700ms / Pro 4789ms / 5 calls / $0.000639；含超时、截断、脱敏检查） |
| PG16 | CI `postgres` Job（PG 16 容器）覆盖 parity/platform/workspace/fencing/迁移幂等；本地专项 16 skip |

> 口径：CI 通过 ≠ 生产就绪；本地通过 ≠ CI 通过。两类证据都在上表分列，不混用。

## 3. 风险清单

| # | 风险 | 等级 | 现状 / 缓解 |
|---|---|---|---|
| 1 | Reliability Gate 未在 CI 实跑 | **高（发布）** | 本地 62.16 分钟 soak / 122 Run / 33-33 通过；需有 Actions 权限者 dispatch（步骤已成文） |
| 2 | CI 无真实 LLM Secret | **高（能力声明）** | 本地 smoke 11/11 证明接入可用；CI 侧如实 BLOCKED；不得把 FAKE/RULE 结果当 AI 能力 |
| 3 | LICENSE 缺失 | **高（法律）** | `NEEDS_DECISION`，未擅自选择；合并前需负责人决策 |
| 4 | open PR #1 / #2 与本分支同源 | 中 | 本分支包含二者全部提交；**不可分别合并**，以本 PR 为唯一入口，合并后关闭 #1/#2 |
| 5 | 单 PR 体量过大（2967 文件 / 31 万行 / 241.7 MB 证据） | 中 | 建议评审按 RUN 分段；后续证据改“摘要入库 + artifact” |
| 6 | 历史 TestCaseVersion 无 `authoring_hash` 基线 | 中 | R2 后新建版本才校验；更早版本首次流转时建基线，无法追溯更早改库 |
| 7 | 数据库级防篡改未做 | 中 | 需 DBA 权限隔离或行级签名，超出应用层边界 |
| 8 | 3 张 full-loop 截图字节相同（03/04/05） | 低 | 同轮 JSON 断言与血缘 ID 为主证据；RUN-020 起要求步骤级可区分截图 |
| 9 | 匿名 GitHub API 403 / 无 job log 导出 | 低（流程） | 有令牌后 `gh run view --log`；不影响代码质量 |
| 10 | a11y 证据内部标签仍为 “RUN-018 WP8/R1” | 低（追溯） | 数量与时间戳可核；后续脚本加 `--label` |

## 4. 历史分支与 PR 整合说明

| 分支 / PR | 关系 | 处理 |
|---|---|---|
| `workbuddy/run007-e2e-reliability`（= `origin/main`） | 本分支的祖先 | 已包含，无需单独合并 |
| PR **#1** `workbuddy/run007-resume-20261123` | 祖先链上（`merge-base = origin/main`） | **不得单独合并**；本 PR 合并后关闭 |
| PR **#2** `workbuddy/run008-quality-hardening`（65 提交） | 祖先链上（本分支包含其全部提交） | **不得单独合并**；本 PR 合并后关闭 |
| `run009`…`run018` 各分支 | 线性历史中的先后节点，均被本分支包含 | 仅作历史追溯，不合并 |
| `workbuddy/vue-tsc3-eval`、`traceguard-autonomous` | 侧支实验分支 | 不进入 main；保留或按需删除 |

**合并策略**：以本 PR 为唯一入口合并到 `main`；**禁止** squash（会丢失 172 个可审计提交与
每轮的证据对应关系），使用 merge commit 或 fast-forward（当前 main 无分叉，可快进）。

## 5. 数据迁移与回滚

### 5.1 迁移

迁移在 API 启动时按 `tg_schema_migration` 版本表顺序执行，**全部为增量、幂等**
（`CREATE TABLE/INDEX IF NOT EXISTS` + callable 步骤内部判重）。`main` → 本分支新增 v2–v14：

| 版本 | 名称 | 影响 |
|---|---|---|
| 2–3 | 事件/证据/Doc 索引 | 仅索引 |
| 4 | `tg_step` 主键按 Run 作用域 | 结构调整（callable，重建主键） |
| 5–6 | 消费收件箱状态 / 生产者序列分配 | 新表 |
| 7 | TestCase 审核修订号 | 新列 |
| 8 | 晋升回执 | 新表 |
| 9 | 证据摘要语义（`stored_content_sha256` / `attachment_sha256`） | 新列，历史行记 `NOT_RUN` |
| 10 | `intent_ref` 加宽（修 PG varchar(64) 截断） | 列类型变更 |
| 11–12 | 认证 / RBAC / 审计 / 登录限速 / 审计序号 | 新表 |
| 13 | Run 提交回执（幂等键主键） | 新表 |
| 14 | `tg_admin_guard`（最后一个管理员保护） | 新表 + 种子行 |

**前置检查**：`python -m traceguard_api...` 启动即迁移；生产建议先 `pg_dump` 全库备份
（`scripts/backup_restore_verify_run017.py` 已验证 SQLite + PG16 的真实 dump/restore）。

### 5.2 回滚

| 场景 | 动作 | 说明 |
|---|---|---|
| 应用回滚 | 部署上一版本镜像/提交 | 迁移是**只增不改**：新表/新列对旧代码无副作用（旧代码不读新表） |
| v4 主键调整回滚 | **不支持自动回滚** | 需从备份恢复 `tg_step`；升级前必须备份 |
| 数据修复 | 用备份恢复 + 重放 Run | 不做原地降级 DDL |
| 灰度期 | 保留 `main` 旧实例读同一库（只读） | 写操作必须单版本，禁止双写 |

## 6. 合并前检查表

- [ ] `Reliability Gate` 在 HEAD 上真实 SUCCESS，且 soak 证据满足 §1.4 的 9 条硬条件
- [ ] CI Nightly `llm-smoke` 不再是 BLOCKED（仓库 Secret 已配置并产生真实 usage）
- [ ] PR Gate / Nightly 在本 PR 的 merge commit 上复跑全绿（不得沿用旧 SHA 结论）
- [ ] LICENSE 决策完成并入库
- [ ] PR #1 / #2 已标注“随本 PR 合并后关闭”
- [ ] 生产库已备份；迁移在预发库演练一次
- [ ] 无凭据入库（`security` Job 的 secret scan 通过）
- [ ] 人工评审通过（至少 1 名非作者 reviewer）

## 7. 证据索引

| 类别 | 路径 |
|---|---|
| R2 收口清单 | `docs/RUN-019-R2-GAP-CLOSURE.md` |
| R2 最终报告 | `docs/RUN-019-FINAL-REPORT.md` |
| 本轮审计 | `docs/RUN-020-PREPARATION-AUDIT.md` |
| 发布准入 | `docs/RUN-020-RELEASE-ADMISSION.md` |
| UI / RBAC / a11y 证据 | `docs/assets/run019-r2/local/{ui,rbac,a11y}/` |
| 真实 LLM smoke | `docs/assets/run020-prep/llm/llm-smoke-20261127.json` |
| RUN-019 长稳 | `docs/assets/run019/wp5/soak/soak-62min.json` |
| 历史各轮证据 | `docs/assets/run014/` … `docs/assets/run019/` |

---

> 本 PR 描述由 RUN-020-PREPARATION 生成：**未创建 PR、未合并、未改写历史、未强推**。
> 所有“通过/阻塞”表述均可按上表路径回读。
