# RUN-019 WP3：前端产品级完整性（权限门禁 + 无障碍 + 真实业务操作）

> 分支 `workbuddy/run019-product-completion`。执行时间：2026-11-27。

## 1. 权限门禁真实化（不再只靠后端兜底）

逐视图检查按钮/表单的真实业务属性，把"任何人都可点的破坏性按钮"收敛为
前端可见权限初筛 + 服务端权威判定：

- **RepairsView**：批准/驳回（`repair.approve`）、晋升（`repair.promote`）、
  回滚（`repair.rollback`）、运行回归验证（`repair.regression`）按会话角色禁用；
  审批主体改为服务端可信（不采信页面声明）。
- **MonitoringView**：告警确认/解决按 `system.observe` 门禁。
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

口径：19 视图 × 2 主题 × 2 断点 = **77 格，77/77 PASS**
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
