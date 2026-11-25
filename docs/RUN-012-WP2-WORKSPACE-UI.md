# RUN-012 WP2：多 Workspace 产品功能实施与验收

日期：2026-11-25
分支：`workbuddy/run012-product-completion`

## 1. 统一上下文设计

新增 `apps/web/src/workspace.ts`（轻量响应式共享模块，不引入新状态库）：

- 登录后从服务端 `GET /api/v1/workspaces` 获取**已按 Principal 过滤**的空间列表；`/auth/me` 只用于确认会话有效。
- `localStorage['tg-workspace']` 只保存用户偏好；每次加载与切换都重新向服务端拉取并校验，手改偏好不能授权。
- 偏好失效或用户无可用空间时自动回落到第一个合法空间；无可用空间时展示独立空状态，不渲染业务视图。
- `workspaceGeneration` 在每次空间切换时递增，`App.vue` 用 `:key="workspaceGeneration"` 强制 `<router-view>` 重挂载，旧空间数据不残留、不闪现。
- `wsPath()` / `workspaceParams()` 统一生成列表查询参数，实体详情与写操作仍由服务端权威解析实体归属，前端不覆盖。

`App.vue` 顶栏新增工作空间切换控件（含无权限空状态），全局搜索、导航统计、Run/TestCase/Intent/Diagnosis/Repair 计数均随当前空间重新加载；切换同时清空搜索结果。

## 2. 15 个视图审查结果

| 视图 | Workspace 处理 | 结论 |
|---|---|---|
| DashboardView | 四个统计列表全部走 `wsPath()` | 已接入 |
| IntentsView | 列表与新建 Intent 使用当前空间 | 已接入 |
| TestCasesView | 列表与新建 TestCase 使用当前空间 | 已接入 |
| TestCaseDetailView | 实体详情/版本由服务端按实体行授权 | 无需前端指定 |
| OraclesView | 列表与新建 Oracle 使用当前空间 | 已接入 |
| ExplorerView | 探索/需求列表使用当前空间；实体探索仍服务端解析 | 已接入 |
| RunsView | 列表使用当前空间 | 已接入 |
| RunDetailView | 实体详情/步骤/证据/事件由 Run 归属决定 | 无需前端指定 |
| DiagnosesView | 诊断与处置列表使用当前空间 | 已接入 |
| DiagnosisDetailView | 实体详情、探针按 Diagnosis/Run 归属 | 无需前端指定 |
| RepairsView | 列表使用当前空间；审批/晋升/回滚按实体归属 | 已接入 |
| BenchmarkView | 实验记录为平台级视图，服务端 scope_filter | 保持 |
| ModelsView | 模型运行汇总，无空间业务实体 | 不适用 |
| SystemView | 运维健康/配置；`/system/metrics` 见 WP3 | 保持 |
| UsersView | 用户管理为平台级 | 不适用 |

前端已无 `workspace_id=default` 硬编码（`grep -rn` 复查为空）。

## 3. 真实浏览器验收

脚本：`scripts/workspace_ui_acceptance_run012.py`（真实 Chromium + 真实 API + 真实 SQLite + 正式前端构建产物；零 Mock）。

原始证据：`docs/assets/run012/wp2/ui/workspace-acceptance.json` + 4 张深色模式截图。

| 检查项 | 结果 |
|---|---|
| 通过真实 API 创建 Workspace A / B（服务端生成 ID） | PASS |
| 7 个用户（A/B × viewer/qa-engineer/qa-lead + platform-admin） | PASS |
| A 三角色只看到 A 空间，B 三角色只看到 B 空间 | PASS（6/6） |
| 普通用户切换控件不含未授权空间 | PASS（6/6） |
| 普通用户伪造 change 事件切到未授权空间 | PASS（服务端拒绝，刷新后仍 A） |
| 手改 `localStorage` 后刷新 | PASS（恢复服务端授权空间） |
| 直接伪造请求 `workspace_id` 写入 | PASS（HTTP 403） |
| platform-admin 可见两空间并成功切换 | PASS |
| 切换后列表刷新、无旧空间数据残留 | PASS（A=1 / B=1，互不混入） |
| 刷新后恢复上次选择 | PASS |
| 390 / 768 / 1024 / 1440px 深色模式 | PASS（4/4，横向溢出 0） |
| **合计** | **25 passed / 0 failed** |

窄屏验收中真实发现并修复了一个缺陷：390px 顶栏新增切换控件后横向溢出 182px；通过 `@media (max-width:560px)` 折叠面包屑/环境标签/用户名并压缩控件解决，复验溢出 0。

## 4. 边界

- 实体级接口（Run / TestCase / Diagnosis / Repair）继续由服务端权威解析 Workspace，前端不传覆盖值。
- 本轮没有新增 Pinia 依赖；使用 Vue `reactive` 共享模块。
- `/system/metrics` 的全局计数问题不在本工作包修改范围，见 WP3。
