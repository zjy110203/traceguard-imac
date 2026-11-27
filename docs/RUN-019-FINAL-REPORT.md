# RUN-019-R2 最终报告（产品收口、CI 修复与发布准入）

> 分支 `workbuddy/run019-product-completion`。
> RUN-019 起点 `73edc346`；R2 起点 `61ffd4a`；R2 代码提交
> `2b90d6c`（R2-1）→ `87696a6`（R2-2）→ `cbc620f`（R2-3）→ `dfb49b2`（R2-4）→
> `19f527f`（证据/文档）→ `7988210`（CI 看门狗）。本轮不新增架构设计。
> 口径：每条结论都给可回读证据；BLOCKED / NOT_RUN 不含糊。

## 0. 结论摘要

| 工作包 | 结论 | 关键证据 |
|---|---|---|
| R2-1 Nightly UI E2E 修复 | **CLOSED / VERIFIED** | 本地 core 41 PASS / 0 FAIL；CI ui-acceptance **SUCCESS（2m01s）** |
| R2-2 compile_source 来源可信 | **CLOSED / VERIFIED** | 专项 8/8；公共 HTTP 无法声明 compiler/healed；版本继承血缘 |
| R2-3 前端权限门禁完整化 | **CLOSED / VERIFIED** | 8 个视图补齐；四角色真实验收 24/24 |
| R2-4 Workspace 范围 / 版本完整性 | **CLOSED / VERIFIED** | 监控 scope 显式化；`authoring_hash` 篡改 → 409；专项 5/5 |
| R2-5 CI 与 Reliability Gate | **PR/Nightly 通过；Reliability CI NOT_RUN** | PR Gate #113 ✅；Nightly #66 6/6 ✅；Reliability 需 dispatch（无凭据） |
| R2-6 文档与台账 | **DONE** | 本文件 + `docs/RUN-019-R2-GAP-CLOSURE.md` + `.workbuddy-run/` |

## 1. 起点与提交关系（修正 RUN-019 报告的口径）

- RUN-019 实际起点：`73edc346`（**不是** `05a3639`；`05a3639` 是 RUN-018-R2 的 HEAD）。
- RUN-019 最新 HEAD（R2 起点）：`61ffd4a`。
- Nightly #65 @ `61ffd4a`：**FAILURE**（ui-acceptance `#tc-steps` 超时；llm-smoke BLOCKED → gate-verdict 失败）。
- PR Gate #112 @ `61ffd4a`：**SUCCESS**，但只有 backend + frontend 两个 Job，
  **不能替代** Nightly 的真实 UI 验收。
- R2 修复后：PR Gate #113 与 Nightly #66 均在 `19f527f` 上真实通过（详见 §6）。

## 2. R2-1：Nightly UI E2E 失败的根因与修复

**根因**：RUN-019 WP1 把 TestCase 的 JSON 步骤输入框改为可视化 `StepEditor.vue`，
RUN-012 时代的 `#tc-steps` / `#tc-assertions` 已从 DOM 消失；`business_loop_ui_acceptance_run012.py`
仍在 `page.fill("#tc-steps", ...)` → `TimeoutError: Page.fill: Timeout 30000ms exceeded`。

**修复**（不是替换选择器、不是隐藏兼容框、不是跳过 UI）：

1. `StepEditor.vue` 增加稳定 `data-testid`：`step-editor` / `step-row` / `step-add` /
   `step-action` / `step-strategy` / `step-target` / `step-value` / `step-description` /
   `assertion-row` / `assertion-add` / `assertion-kind` / `assertion-description` /
   `assertion-expected` / `step-mode-json` 等。
2. 补齐断言 `expected` 的可视化 JSON 编辑与行内校验（此前只能靠 JSON 高级模式，
   可视化路径无法定义"断言什么"——真实功能缺口）。
3. 新步骤默认无 locator；选择定位策略但未填目标值时，提交前明确报错，
   避免契约层 `LocatorSpec.value(min_length=1)` 的 422。
4. 验收脚本改为真实可视化操作：清空预置 5 步/1 断言 → 逐步新增 → 改 action →
   选策略 → 填 locator → 填值/描述 → 新增断言并填 expected → 切 JSON 模式核对 →
   切回 → 创建 → **从 `/versions/{v}` 回读逐项核对**（action/target/value/expected/assertion_id）。
5. Oracle 的 `assertion_id` 改为读取编辑器真实生成值，不再硬编码 `a_wp4_banner`。

**验收**：`business_loop_ui_acceptance_run017.py --profile core`
（真实 API + 真实 TeamFlow + 真实 Chromium + 认证开启 + 2 个独立 Worker + 服务端回读）
→ **42 项 / 41 PASS / 0 FAIL / 1 NOTE**，新增 ②a–②f 六项覆盖编辑器交互。
证据：`docs/assets/run019-r2/local/ui/run017-ui-core.json`（+ 12 张截图）。
CI 侧：Nightly #66 `ui-acceptance` **SUCCESS，2m01s**，不再有 `#tc-steps` 失败。

## 3. R2-2：compile_source 来源可信度

**漏洞**：`POST /testcases` 与 `POST /testcases/{id}/versions` 直接采信客户端
`compile_source`；`review_testcase()` 对 `human` 有稳定性回放豁免 →
Agent/Compiler 产物可被标成 human 绕过回放，已生成候选还能通过"新建版本"洗来源。

**修复后的来源矩阵**：

| 入口 | 服务端认定的来源 | 客户端能否改变 |
|---|---|---|
| `POST /testcases`（公共 HTTP） | `human` + `source_origin=manual_http` + `source_ref=user:<name>` | 否（声明 compiler/healed → 403 `COMPILE_SOURCE_NOT_CLIENT_CONTROLLED`，且不落库） |
| `POST /testcases/{id}/versions` | 继承 TestCaseRow 的来源；`source_ref=父版本 tcase@N` | 否（改标 → 403，版本数不变） |
| `CatalogService.create_trusted_candidate()` | `compiler` → `compiler_flow`；`healed` → `repair_sandbox` | 仅服务端进程内；拒绝 human / 空血缘 |
| Explorer Compiler | `compiler_flow` + `exploration_id` | — |
| Repair 沙箱 / 晋升 / 回滚 | `repair_sandbox` / `repair_promotion` / `repair_rollback` + `proposal_id` | — |
| 审核留痕 | `review_history[].source_provenance` | — |

**反例测试**（`test_testcase_source_provenance_run019_r2.py`，8/8）：
客户端声明 compiler/healed → 403 + 不落库；人工路径记录 human 血缘；
可信入口拒绝 human/空血缘；compiler 候选改标 human → 403 且版本数不变；
新版本继承 compiler 血缘；未回放审批 → 412；旧版本/旧 Run 引用不变；
`agent:llm` 主体自批 → 403。

**剩余风险**：进程内代码仍可直接调用持久层（同一边界内视为可信）；
人工路径与"人用 LLM 生成后手工提交"在内容上不可区分，这一层由 qa-lead 审核把关。

## 4. R2-3：前端权限门禁完整化

源码级审计发现 **8 个视图完全没有 `can()` 判定**（viewer 也能点），本轮全部补齐：
Intents / RunDetail / TestCaseDetail / Repairs（提交与校验）/ OracleDetail /
Users / System / Benchmark / Explorer / DiagnosisDetail。动作映射与后端
`PERMISSIONS` + `action_for_request()` 对齐；认证关闭的开发环境不隐藏按钮。

**四角色真实验收**（`scripts/rbac_role_ui_acceptance_run019_r2.py`）
platform-admin / qa-lead / qa-engineer / viewer 各自真实登录 + 真实 Chromium：
- 按钮"存在且可用 / 存在但禁用"逐项核对（不存在与不可用分开判定）；
- 需要 REVIEW 状态需求才能出现的「通过」按钮，按角色核对可用性；
- 直接绕过前端发请求，确认服务端独立 403：viewer 建用例、qa-engineer 审核、
  qa-engineer 写演示资产、qa-lead 晋升；
- 服务端 `/auth/permissions` 与验收期望逐动作比对（无隐藏放宽）。
**24/24 PASS**，证据 `docs/assets/run019-r2/local/rbac/`。

## 5. R2-4：Workspace 范围与版本完整性

### 5.1 Workspace 统计范围

- `MonitoringView` 之前调用 `monitoringMetrics(undefined)`：不传 Workspace 时服务端按
  "全部已授权 Workspace 聚合"统计，页面却没有任何范围标注（静默混用两种口径）。
  修复：默认传当前 Workspace；全局汇总需显式勾选，并在页头展示服务端返回的
  `scope.mode`（当前空间 / 全部已授权 / 全局）。
- `Runs/Diagnoses/Repairs` 的「包含 Benchmark」确实走全局端点（不含 `wsPath`），
  语义是"跨全部已授权 Workspace 聚合"：标签与 aria-label 已明确标注。
- 全局搜索 6 个数据源全部使用 `wsPath()`，无需修改。

### 5.2 TestCaseVersion 完整性

`immutable=True` 只是流程标记，JSON 列本身可被直接改库。R2 增加
`authoring_hash`（步骤/断言/配置/来源摘要，排除 Oracle 绑定与审核元数据）：
创建时写入，**审核与 Oracle 绑定写库前复核**；不一致 → 409
`TESTCASE_VERSION_CONTENT_MUTATED`。审核元数据（review_history / review_revision /
oracle_bindings）仍可追加；Oracle 版本审批时固定，审批后禁止改绑定（412）；
旧版本内容与旧 Run 的 `testcase_version_id` 不因新版本改变。
专项 5/5（`test_testcase_version_integrity_run019_r2.py`）。

**剩余风险**：R2 之前的历史版本没有摘要基线（首次流转时建立，无法追溯更早的改库）；
数据库级防篡改需 DBA 权限隔离或行级签名，本轮未做。

## 6. R2-5：CI 与可靠性门禁（真实结果）

| 门禁 | HEAD | 结果 | 说明 |
|---|---|---|---|
| PR Gate #113 | `19f527f` | **SUCCESS** | backend 3m36s（ruff/format/unit+contract/fast integration）、frontend 26s（typecheck+build） |
| Nightly #66 | `19f527f` | **SUCCESS（6/6 Job）** | 见下表 |
| ├ backend-full | | SUCCESS 9m40s | 全量 SQLite 套件 6m16s + RUN-016/017 专项 2m08s + 并发 9s |
| ├ security | | SUCCESS 17s | npm audit + secret scan |
| ├ postgres | | SUCCESS 1m23s | PG16 parity/platform/workspace/fencing/迁移幂等 |
| ├ ui-acceptance | | **SUCCESS 2m01s** | R2-1 的关键验收，无 `#tc-steps` 失败 |
| ├ llm-smoke | | SUCCESS（Job） | **真实调用 BLOCKED（缺 `DEEPSEEK_API_KEY`）**，不声称成功 |
| └ gate-verdict | | SUCCESS 3s | 按既有规则接受 LLM BLOCKED |
| Nightly #65（对照） | `61ffd4a` | FAILURE | ui-acceptance `TimeoutError: Page.fill #tc-steps`；gate-verdict 失败 |
| PR Gate #112（对照） | `61ffd4a` | SUCCESS | 仅 backend+frontend，覆盖不到 UI E2E |
| PR Gate #114 / Nightly #67 | `7988210` | SUCCESS / SUCCESS（6/6） | 加入 `ci_pytest_watchdog.py` 后的复验 |
| **PR Gate #115 / Nightly #68** | **`564b21c`（分支 HEAD）** | **SUCCESS / SUCCESS（6/6 Job）** | 收口提交后的最终档：PR 3m40s；Nightly 10m52s（backend-full 9m33s、ui-acceptance 2m5s、postgres 1m3s、security 20s、llm-smoke 23s、gate-verdict 4s） |
| Reliability Gate（CI） | — | **NOT_RUN** | 只能 `workflow_dispatch`；本机无 gh CLI/凭据，未触发 |

> 口径说明：上表 `#113/#66` 对应 `19f527f`，**保留为历史事实不改写**；
> 分支最终 HEAD `564b21c` 的真实结果是 **PR Gate #115 / Nightly Gate #68（6/6 SUCCESS）**。
> LLM 侧：CI 的 `llm-smoke` Job 成功但真实调用 **BLOCKED**（仓库未配 Secret）；
> 本地 `scripts/llm_smoke.py` 在 `564b21c` 上 **11/11 REAL_PROVIDER PASS**
> （证据 `docs/assets/run020-prep/llm/llm-smoke-20261127.json`），两者不互相替代。

**Reliability 本地证据（RUN-019 保留）**：`scripts/soak_run013.py --minutes 62` @ PG16，
墙钟 62.16 分钟、122 个真实 Run 全终态、33/33 checks PASS
（`docs/assets/run019/wp5/soak/soak-62min.json`）。该证据不替代 CI 实跑。

**R2 追加的 CI 可诊断性**：Nightly #66 的 `Full backend suite` 曾出现一次
"整步长时间无输出"（后确认 #66 实际 6m16s 正常完成，早前读数来自页面缓存）。
为防真实挂起再次拖到 45 分钟 Job 超时，新增 `scripts/ci_pytest_watchdog.py`：
15 分钟把全部线程栈写入 Job Summary、35 分钟强制失败（退出码 124），
不降低任何断言强度。

## 7. 本轮验证汇总（本地）

| 门禁 | 结果 |
|---|---|
| `ruff check apps packages scripts tests` | All checks passed |
| `ruff format --check apps/api packages scripts tests` | 280 files already formatted |
| `pytest -q`（SQLite 全量） | **1144 passed / 16 skipped**（skipped 均为未配置 `TG_TEST_POSTGRES_URL` 的 PG 专项；419s） |
| `npm run typecheck` / `npm run build` | 通过 |
| UI 全链路 core | 42 项 / 41 PASS / 0 FAIL / 1 NOTE |
| a11y（R2 后复跑，`docs/assets/run019-r2/local/a11y`） | **77/77**（19 视图×2 主题×2 断点 = 76 组合 + 1 弹窗焦点检查） |
| 四角色权限 | 24/24 |

## 8. 未决事项（如实记录）

| 编号 | 事项 | 状态 |
|---|---|---|
| B-25 | Reliability Gate CI 实跑 | **NOT_RUN**：需 `workflow_dispatch`，本机无 gh CLI/凭据；本地 62 分钟 soak 保留 |
| B-26 | PR 创建 | **NOT_RUN**：无 gh CLI/凭据，未授权不创建；分支已推送 |
| B-27 | CI 日志/API 导出 | **BLOCKED**：匿名 API 无 job log 权限（403），只能读 runs/jobs/annotations 与 Job Summary |
| B-24 | 真实 LLM 证据 | **BLOCKED**：缺 `DEEPSEEK_API_KEY`，未寻找凭据 |
| — | main 合并 | 未授权，不执行 |

## 9. 交付索引

- 收口差距清单：`docs/RUN-019-R2-GAP-CLOSURE.md`
- 专题：`docs/RUN-019-{WP1-ASSET-MANAGEMENT,WP2-DIAGNOSIS,WP3-FRONTEND,WP4-BUSINESS-LOOP,WP5-QUALITY-GATES}.md`
- 证据：
  - `docs/assets/run019-r2/local/ui/`（报告 + 12 截图）
  - `docs/assets/run019-r2/local/rbac/`（报告 + 4 角色截图）
  - `docs/assets/run019-r2/local/a11y/`（77/77）
  - `docs/assets/run019/wp{1..5}/`（RUN-019 原始证据，未改写）
- 台账：`.workbuddy-run/{current-task,progress,changes,test-results,blockers,phase-status}.md`

## 10. 发布建议

代码与 CI 已达到"可进入正式 PR 评审"的状态：Nightly 6/6 通过、PR Gate 通过、
UI 业务闭环真实通过、三个安全/完整性缺口（来源可信、权限门禁、版本完整性）已关闭。
**不建议直接合并**：Reliability Gate 尚未在 CI 实跑、真实 LLM 仍 BLOCKED、
PR 尚未创建；这三项需有凭据的执行者完成后再放行。
