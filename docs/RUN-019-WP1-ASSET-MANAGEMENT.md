# RUN-019 WP1：核心资产管理功能完整化

> 分支 `workbuddy/run019-product-completion`；代码 HEAD 见 `docs/RUN-019-FINAL-REPORT.md`（WP6 统一收口）。
> 状态口径：VERIFIED = 真实 API/DB/浏览器运行证据；IMPLEMENTED = 代码完成但缺本轮真实运行；
> PARTIAL = 部分；BLOCKED / NOT_RUN 如实保留。

## 1. 完成清单

| 能力 | 状态 | 证据 |
|---|---|---|
| Workspace 列表/详情/创建/编辑 | **VERIFIED** | `tests/integration/test_workspace_roles_run019.py` 5 项；创建者取服务端 Principal 并写审计链；默认 Workspace 编辑返回 403 |
| Workspace 成员及授权范围展示 | **IMPLEMENTED** | `GET /auth/workspaces/{id}/members`（platform-admin）；成员来自既有 `tg_user.workspaces` 权威来源；未引入新成员模型 |
| Workspace 切换全量刷新 | **VERIFIED（前轮保持）** | `workspace.ts` generation + `<router-view :key>`；RUN-012 25/25 |
| 用户角色修改 API + 前端入口 | **VERIFIED** | `POST /auth/users/{id}/roles`；`UsersView` 编辑弹窗；`test_role_change_reflects_in_existing_session` 验证旧会话下一次请求即反映新权限（无需撤销） |
| 最后管理员保护 | **VERIFIED** | `tg_admin_guard` 单行锁靶（PG `FOR UPDATE` / SQLite 写锁）；停用/降级最后一个管理员 409；并发由数据库锁串行化 |
| 用户操作审计 | **VERIFIED** | `auth.user_roles_changed` 带 before/after；审计链 verify intact |
| TestIntent 编辑 | **VERIFIED** | `PATCH /intents/{id}`：版本递增、原始来源保留、编辑后回 DRAFT；已关联用例时 412 |
| TestIntent 状态机 | **VERIFIED** | DRAFT→REVIEW/DEPRECATED；REVIEW→APPROVED/DRAFT/DEPRECATED；APPROVED→DEPRECATED；非法跳转 412 |
| 提交评审 / 审批权限分离 | **VERIFIED** | `POST /intents/{id}/submit`（testcase.create）与 `/approve`（testcase.review）；旧 `/status` 对 APPROVED 目标额外校验 review 权限；qa-engineer 审批 403 |
| TestCase 可视化步骤编辑器 | **IMPLEMENTED** | `StepEditor.vue`：增删改排序、target/value/description、断言与 Oracle 绑定、JSON 高级模式；8 类 action 全部可选；前端 typecheck+build 通过 |
| TestCase 新版本 | **VERIFIED** | `POST /testcases/{id}/versions`；服务端递增版本；历史版本不可变；`test_testcase_new_version_immutable_and_diff` |
| TestCase 版本差异比较 | **VERIFIED** | `GET /testcases/{id}/versions/{a}/diff/{b}`；用例详情显示 changed_fields + unified diff |
| Oracle 详情/历史/审批记录/反向关联 | **VERIFIED** | `OracleDetailView.vue` + `/oracles/{id}/versions|references|approve`；审批基于旧版新建版本、历史不可覆盖；反向引用真实落库 |
| Oracle 跨 Workspace ID 复用拒绝 | **VERIFIED** | `create_oracle()` 对已有 ID 的 workspace 校验；专项 403 |
| 管理员保护 / Agent 禁批 | **VERIFIED（保持）** | `require_human_actor()` + 契约 `approved_by_agent: Literal[False]`；专项测试 403 |

## 2. 关键设计决策

1. **不引入成员表**：Workspace 成员来自既有 `tg_user.workspaces` 白名单（含 `*`），避免与现有 RBAC 双轨；
2. **旧会话不盲目撤销**：`get_session()` 每次请求 JOIN `tg_user` 读取当前角色/scope，权限变更下一请求立即生效；只有用户停用仍撤销全部会话；
3. **默认 Workspace 保护**：不提供删除入口，编辑返回 `DEFAULT_WORKSPACE_PROTECTED`；
4. **需求编辑保护**：需求已关联 TestCase 且处于 APPROVED/DEPRECATED 时拒绝编辑，防止编译血缘断裂；
5. **审批主体**：所有新增写操作使用 `trusted_actor(request, "", action, ...)`，认证开启时忽略请求体声明并写审计；
6. **状态机严格化**：seed、e2e 脚本与 `test_api_flow` 同步改为 DRAFT→REVIEW→APPROVED 两步，未为兼容放宽断言。

## 3. 联合验收（真实 API + 真实数据库 + 真实 TeamFlow/Chromium）

脚本：`scripts/wp1_business_loop_run019.py`
证据：`docs/assets/run019/wp1/wp1-loop.json`

20 步全部 PASS（0 FAIL）：

1. 管理员登录 → 2. 创建 Workspace（创建者为 `user:admin`）→ 3. 创建用户 → 4. 分配角色 → 5. 分配 Workspace →
6. 创建需求 → 7. 编辑需求 → 8. 提交评审 → 9. 审核 → 10. 真实探索（真实 Chromium + 真实 TeamFlow）→
11. 编译候选 → 12. 创建 TestCase → 13. 创建 v2 → 14. 创建 Oracle → 15. Oracle 审批新版本 →
16. 绑定 Oracle → 17. 审核 TestCase → 18. 稳定性门禁预览 → 19. 执行 Run（版本引用固定）→ 20. Run 引用核对。

## 4. 质量门禁

- `ruff check` / `ruff format --check`：全绿；
- `pytest tests/ -q --timeout=180`：**1125 passed / 16 skipped**（16 skip 全为未配置 `TG_TEST_POSTGRES_URL` 的 PG 专项）；
- `npm run typecheck` + `npm run build`：通过（新增 2 个业务路由、1 个组件、3 个视图）；
- PostgreSQL 16 专项与 CI 实跑在 WP5 统一核对，不在此提前声明。

## 5. 仍保留的缺口（不伪装完成）

- Workspace 成员管理是**展示 + 通过用户页修改范围**，没有独立成员邀请/角色-in-workspace 模型：NEEDS_DECISION；
- 测试用例 `compile_source` 仍由创建请求声明，前端已不再伪造审核主体，但服务端血缘推导未实现：PARTIAL；
- `TestCaseVersion.immutable` 仍是流程级标记（审核后锁定绑定），非存储级只读约束：PARTIAL；
- 前端无 JS/TS 单元测试（沿用既有 Python E2E/无障碍脚本），本轮新增视图的真实浏览器验收在 WP3/WP5 执行。
