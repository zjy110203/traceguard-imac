# RUN-019 WP3：前端产品级完整性（权限门禁 + 无障碍 + 真实业务操作）

> 分支 `workbuddy/run019-product-completion`。执行时间：2026-11-27。

## 1. 权限门禁真实化（不再只靠后端兜底）

逐视图检查按钮/表单的真实业务属性，把"任何人都可点的破坏性按钮"收敛为
前端可见权限初筛 + 服务端权威判定：

- **RepairsView**：批准/驳回（`repair.approve`）、晋升（`repair.promote`）、
  回滚（`repair.rollback`）、运行回归验证（`repair.regression`）按会话角色禁用；
  审批主体改为服务端可信（不采信页面声明）。
- **MonitoringView**：告警确认/解决按 `system.configure` 门禁（与后端路径映射一致；
  `system.observe` 仅用于全局运维观测接口）。
- **DiagnosisDetailView**：诊断结论审批按 `diagnosis.operate`/评审权限门禁。
- **TestCasesView / TestCaseDetailView / OraclesView / OracleDetailView /
  IntentsView / WorkspacesView**：创建/审核/执行/审批按钮按角色禁用；
  审核主体取当前会话用户，不再硬编码 `role:qa-lead`。
- **前后端权限矩阵对齐修复**：前端 `ROLE_PERMISSIONS` 补齐
  `repair.regression`（qa-lead/platform-admin）与 `system.observe`
  （platform-admin），与后端 `PERMISSIONS` 同构。该缺漏在 WP4 full-loop
  中真实暴露（回归按钮被禁），已修复并复跑通过。

## 2. 无障碍验收扩展（RUN-018 65/65 → RUN-019 77/77）

`scripts/ui_a11y_run019.py` 在 RUN-018 的 16 视图基础上补齐：

- `monitoring`（RUN-018 未覆盖的第 16 个业务视图）
- `oracle_detail`（RUN-019 新增）
- `workspaces`（RUN-019 新增）

口径：19 视图 × 2 主题 × 2 断点 = **76 个视图组合**，另加 **1 个弹窗焦点
（users 弹窗 focus trap / Esc 关闭）检查 = **77 格，77/77 PASS**
（真实 Chromium + axe-core + 键盘焦点检查 + 390px 溢出检查）。

### 过程中发现并修复的违规（3 类 8 处）

| 问题 | 修复 |
|---|---|
| MonitoringView 数据库连接池 `pre.json` 滚动块无键盘焦点 | 补 `tabindex="0"` |
| OracleDetailView `.linklike` 使用不存在的 `--link` 变量，暗色主题对比度 3.3 | 改用主题化 `--brand`（暗色 #8aa7ff 对比度 7.28） |
| `.muted`（--text-3 #657183）浅色主题在 #f2f2f2 上对比度 4.42 | 全局 `--text-3` 加深至 #606b7f（≥4.8） |
| OracleDetailView 审批记录/关联用例表格 390px 横向溢出 16px | 包 `scroll-x` 焦点容器 |

证据：`docs/assets/run019/wp3/`（a11y-summary.json、axe-reports.json、截图）。

## 3. 新增管理页的真实业务操作验收（认证开启）

`scripts/asset_ui_acceptance_run019.py`：真实 API + 真实 TeamFlow + 真实 Chromium，
以真实 `platform-admin` 会话登录（Cookie + CSRF），逐项点击真实按钮，
**业务成功以服务端回读为准**：

| 步骤 | 结果 |
|---|---|
| 工作区创建通过 UI 落库（created_by=user:ui-admin） | PASS |
| 用户角色通过 UI 修改（ui-viewer → +qa-lead） | PASS |
| 需求通过 UI 创建 | PASS |
| 需求编辑通过 UI 落库且版本递增、回到 DRAFT | PASS |
| Oracle 审批通过 UI 创建新版本 | PASS |
| 用例新版本通过 UI 落库（可视化步骤编辑器） | PASS |
| 版本差异比较可见 | PASS |

**8/8 PASS**（含前置检查），证据 `docs/assets/run019/wp3/ui-asset-loop.json`。

### 过程中发现并修复的真实缺陷

- `auth_store.list_users` 返回原始行，`roles` 仍是 JSON 字符串——API 消费方
  拿到的是字符串而非数组。已修复（解析 + active 布尔化），并补 RBAC 回归。
- 种子需求均被用例引用时服务端按门禁拒绝编辑（预期保护），验收改为
  UI 先建后编。

## 4. R2 源码级权限审计（2026-11-27）

RUN-019 WP3 的门禁只覆盖了部分视图。R2 逐视图审计发现 **8 个视图完全没有 `can()` 判定**，
按钮对 viewer 也可见可点（仅靠后端 403 兜底），与"前端按真实会话权限显示或禁用"的要求不符。

| 视图 | 补齐的动作 |
|---|---|
| IntentsView | 新建/编辑/提交评审/废弃/探索 → `testcase.create`；通过 → `testcase.review` |
| RunDetailView | 诊断/证据图 → `diagnosis.operate`；重试 → `run.execute`；探针 → `probe.execute` |
| TestCaseDetailView | 新建版本/稳定性回放 → `testcase.create`；保存绑定 → `oracle.bind` |
| RepairsView | 新建/规则校验/沙箱/提交 → `repair.submit`（此前仅审批/晋升/回滚有门禁） |
| OracleDetailView | 审批并新建版本 → `oracle.create` |
| UsersView | 新建/编辑权限/启用停用 → `user.manage` |
| SystemView / BenchmarkView | 自检/seed/实验 A-C → `system.configure` |
| ExplorerView | 发起探索/编译候选 → `testcase.create` |
| DiagnosisDetailView | 探针/主动闭环 → `probe.execute`；分流复核 → `diagnosis.operate` |

**四角色真实验收**：`scripts/rbac_role_ui_acceptance_run019_r2.py`
（真实登录 × 真实 Chromium × 服务端回读），**24/24 PASS**：

- 每个角色核对按钮"存在且可用 / 存在但禁用"，不存在与不可用分开判定；
- 直接绕过前端发请求确认服务端独立 403：viewer 建用例、qa-engineer 审核、
  qa-engineer 写演示资产、qa-lead 晋升；
- 服务端 `/auth/permissions` 矩阵与验收期望逐动作比对，确认无隐藏放宽。

证据：`docs/assets/run019-r2/local/rbac/`（4 张角色截图 + rbac-roles.json）。
