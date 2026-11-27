# RUN-019-R2：收口差距清单与修复台账

> 分支 `workbuddy/run019-product-completion`；R2 起点 `61ffd4a`（RUN-019 `73edc346` 起算的最后一个提交）。
> 本文件是 **R2 起点快照 + 逐项关闭证据**，不改写 `docs/RUN-019-GAP-ANALYSIS.md` 的历史判断。

## 0. 起点状态（Nightly #65 / PR Gate #112 的真实结论）

| 项目 | 起点结论 | 证据 |
|---|---|---|
| Nightly #65（`61ffd4a`） | **FAILURE** | [run 45990858537](https://github.com/zjy110203/traceguard-imac/actions/runs/45990858537) |
| ├ backend-full | Success | 同上 |
| ├ security | Success | 同上 |
| ├ postgres | Success | 同上 |
| ├ ui-acceptance | **Failure**：`TimeoutError: Page.fill: Timeout 30000ms exceeded`（`#tc-steps`） | 同上（annotation） |
| ├ llm-smoke | Job Success，但缺 `DEEPSEEK_API_KEY` → 真实调用 **BLOCKED** | gate-verdict annotation |
| └ gate-verdict | **Failure**（因 ui-acceptance 失败 + LLM BLOCKED） | 同上 |
| PR Gate #112（`61ffd4a`） | 仅 backend + frontend 两个 Job，**不能替代** Nightly 的真实 UI 验收 | [run 45990858545](https://github.com/zjy110203/traceguard-imac/actions/runs/45990858545) |

## 1. R2-1 UI E2E 与可视化编辑器（CLOSED）

**根因**：RUN-019 WP1 把 TestCase 的 JSON 步骤输入框改成 `StepEditor.vue`，RUN-012 时代的
`#tc-steps` / `#tc-assertions` 已不存在；脚本仍在 `fill` 这两个选择器 → 30s 超时。

**修复**：

- `StepEditor.vue` 增加稳定 `data-testid`（`step-editor` / `step-row` / `step-action` /
  `step-strategy` / `step-target` / `step-value` / `step-description` / `step-add` /
  `assertion-row` / `assertion-kind` / `assertion-description` / `assertion-expected` /
  `assertion-add` / `step-mode-json` 等），并补齐断言 `expected` 的可视化 JSON 编辑与校验
  （此前只能靠 JSON 高级模式填写，属真实功能缺口）。
- 新步骤默认无 locator；选择策略但未填目标值时，提交前明确报错，避免契约层 422。
- `business_loop_ui_acceptance_run012.py` 的 TestCase 段重写为真实可视化操作：
  清空预置 5 步/1 断言 → 逐步新增 → 改 action → 选策略 → 填 locator → 填值/描述 →
  新增断言并填写 expected → 切 JSON 模式核对同一份内容 → 切回 → 创建 →
  **从 `/versions/{v}` 回读，逐项核对 action/target/value/expected/assertion_id**。
- 删除对隐藏兼容输入框的任何依赖；不跳过 UI 创建步骤。

**验收**：`--profile core`（真实 API + 真实 TeamFlow + 真实 Chromium + 认证开启 +
2 个独立 Worker + 服务端回读）→ **42 项 / 41 PASS / 0 FAIL / 1 NOTE**；
新增 ②a–②f 六项断言覆盖编辑器交互本身。证据：`docs/assets/run019-r2/local/ui/run017-ui-core.json`。

## 2. R2-2 compile_source 来源可信度（CLOSED）

**漏洞**：`POST /testcases` 与 `POST /testcases/{id}/versions` 直接采信客户端
`compile_source`，而 `review_testcase()` 对 `human` 有稳定性回放豁免 →
可把 Agent/Compiler 产物标成 human 绕过门禁；版本递增还能"洗来源"。

**修复**：

| 入口 | 修复后行为 |
|---|---|
| `POST /testcases`（公共 HTTP） | 服务端固定 `human`，记录 `source_origin=manual_http` + `source_ref=user:<name>`；声明 compiler/healed → **403 `COMPILE_SOURCE_NOT_CLIENT_CONTROLLED`** 并写审计，且不落库 |
| `POST /testcases/{id}/versions` | 继承已落库来源；客户端改标（尤其 compiler→human）→ **403**；`source_ref` 指向父版本 `tcase@N` |
| `CatalogService.create_trusted_candidate()` | 仅接受 `compiler`/`healed` + 非空血缘；拒绝 `human`（防"人工豁免后门"） |
| Explorer Compiler | 走可信入口，`source_origin=compiler_flow`，`source_ref=exploration_id` |
| Repair 沙箱 / 晋升 / 回滚 | `source_origin=repair_sandbox` / `repair_promotion` / `repair_rollback`，`source_ref=proposal_id` |
| 审核留痕 | `review_history[].source_provenance` 记录 compile_source + origin + ref |

**反例测试**（`tests/integration/test_testcase_source_provenance_run019_r2.py`，8/8 PASS）：
客户端声明 compiler → 403 且不落库；声明 healed → 403；人工路径合法记录 human + 血缘；
可信入口拒绝 human / 空血缘；已有 compiler 候选改标 human → 403 且版本数不变；
新版本继承 compiler 血缘；未回放审批 → 412；旧版本与旧 Run 引用不变；agent 主体自批 → 403。

## 3. R2-3 前端权限门禁完整化（CLOSED）

源码级审计发现 **8 个视图完全没有 `can()` 判定**（此前只有 Repairs/Oracles/TestCases 等少数视图有）。
逐项补齐（动作与后端 `PERMISSIONS` / 中间件映射一致）：

| 视图 | 动作 → 权限 |
|---|---|
| IntentsView | 新建/编辑/提交评审/废弃/探索 → `testcase.create`；通过 → `testcase.review` |
| RunDetailView | 诊断/证据图（含重建）→ `diagnosis.operate`；重试 → `run.execute`；探针执行/刷新建议 → `probe.execute` |
| TestCaseDetailView | 新建版本/稳定性回放 → `testcase.create`；保存绑定 → `oracle.bind` |
| RepairsView | 新建/规则校验/沙箱/提交 → `repair.submit`；批准/驳回 → `repair.approve`；晋升/回滚/回归/回归重试 → 对应动作 |
| OracleDetailView | 审批并新建版本 → `oracle.create` |
| UsersView | 新建/编辑权限/启用停用 → `user.manage` |
| SystemView | 浏览器自检/写入演示资产 → `system.configure` |
| BenchmarkView | 实验 A/B/C → `system.configure` |
| ExplorerView | 发起探索/编译候选 → `testcase.create` |
| DiagnosisDetailView | 探针/主动闭环 → `probe.execute`；分流复核 → `diagnosis.operate` |

前端矩阵与后端一致性由 `/api/v1/auth/permissions` 服务端返回值为准（脚本逐动作核对）。
**四角色真实验收**（`scripts/rbac_role_ui_acceptance_run019_r2.py`，24/24 PASS）：
platform-admin / qa-lead / qa-engineer / viewer 各自真实登录 + 真实浏览器，
核对按钮"存在且可用/存在但禁用"，并直接绕过前端发请求确认服务端 403。

## 4. R2-4 Workspace 范围与版本完整性（CLOSED）

### Workspace

- `MonitoringView` 之前调用 `monitoringMetrics(undefined)`：不传 Workspace 时服务端按
  "全部已授权 Workspace 聚合"统计，页面却没有任何范围标注 → 静默混用。
  修复：默认传当前 Workspace；全局汇总需显式勾选，并展示服务端返回的
  `scope.mode`（当前空间 / 全部已授权 / 全局）。
- `Runs/Diagnoses/Repairs` 的「包含 Benchmark」查询确实走全局端点（不含 `wsPath`），
  语义是"跨全部已授权 Workspace 聚合"：标签与 aria-label 已明确标注，不再让用户误以为是当前空间。
- 全局搜索的 6 个数据源全部使用 `wsPath()`，无需修改。

### TestCaseVersion

- `immutable=True` 只是流程标记，SQLite/PG 的 JSON 列本身可被直接改写。
  修复：新增 `authoring_hash`（步骤/断言/配置/来源的作者内容摘要，排除 Oracle 绑定与审核元数据），
  创建时写入，**审核与 Oracle 绑定写库前复核**；不一致 → 409 `TESTCASE_VERSION_CONTENT_MUTATED`。
- 审核元数据（`review_history` / `review_revision` / `oracle_bindings` / `oracle_binding_frozen`）
  仍可按规则追加；Oracle 版本在审批时固定，审批后禁止改绑定（412）。
- 旧版本内容与旧 Run 的 `testcase_version_id` 不因新版本创建而改变。
- 证据：`tests/integration/test_testcase_version_integrity_run019_r2.py`（5/5 PASS）。

**剩余风险（如实记录）**：R2 之前写入的历史版本没有 `authoring_hash`，首次流转时建立基线，
无法追溯该时间点之前是否被直接改库；数据库级防篡改需要 DBA 权限隔离或行级签名，本轮未做。

## 5. R2-5 CI 与 Reliability Gate

| 门禁 | HEAD | 结果 | 说明 |
|---|---|---|---|
| PR Gate #113 | `19f527f` | **SUCCESS** | backend 3m36s + frontend 26s |
| Nightly #66 | `19f527f` | **SUCCESS（6/6）** | backend-full / security / postgres / ui-acceptance（2m01s）/ llm-smoke（Job 成功，真实调用 BLOCKED）/ gate-verdict |
| PR Gate #114 / Nightly #67 | `7988210` | SUCCESS / SUCCESS | 加入 CI 看门狗后的复验 |
| PR Gate #115 / Nightly #68 | `564b21c`（HEAD） | **SUCCESS / SUCCESS（6/6）** | 收口提交后最终档：PR 3m40s、Nightly 10m52s（ui-acceptance 2m5s） |
| Reliability Gate（CI） | — | **NOT_RUN** | 仅 `workflow_dispatch`；本机无 gh CLI/凭据，未触发；本地 62 分钟 soak 33/33 保留 |

Nightly #65（`61ffd4a`）为对照：ui-acceptance 因 `#tc-steps` 超时失败、gate-verdict 失败；
R2-1 修复后 #66 同一 Job 真实通过（2m01s）。
LLM Secret 未配置时按既有规则 BLOCKED，**不声称真实调用成功**。
