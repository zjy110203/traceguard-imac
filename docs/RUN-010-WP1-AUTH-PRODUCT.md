# RUN-010 WP1：身份认证与 RBAC 产品化

> 状态：**IMPLEMENTED / VERIFIED**（集成测试 21 用例 + 真实 Chromium 登录 E2E 2 用例 + 全量回归 580 passed）。
> 提交：`e43e0ca`（RUN-010 WP1: 认证产品化与 RBAC 硬化）。

## 1. 安全配置（§1.1）

### 环境分级策略（新增 `apps/api/traceguard_api/auth_policy.py`）

| 环境 | 认证要求 | 强制机制 |
|---|---|---|
| development / test / local / ci | 允许显式关闭 | 启动日志打醒目警告（`AUTH_DISABLED: ...`） |
| staging | 必须开启 | 显式关闭 → 启动失败 |
| production | 必须开启 | 显式关闭 → 启动失败；`TG_AUTH_COOKIE_SECURE=false` → 启动失败；`TG_AUTH_SESSION_TTL_S > 86400` → 启动失败 |

关键设计：**不依赖开发者记得设置 `TG_AUTH_ENABLED=true`**。
- `TG_ENVIRONMENT=staging/production` 且未显式设置 `TG_AUTH_ENABLED` → 按开启处理（fail-safe 默认）；
- 判定与中间件共享同一决策（`_effective_auth_enabled()`），杜绝「启动检查通过、中间件却按关闭执行」的裂缝；
- 已验证 6 个策略用例（production 关闭失败 / production 默认开启 / production 不安全 Cookie 失败 / TTL 超限失败 / staging 强制 / development 警告放行）。

### 配置一致性检查结论

- CORS：显式 origin 白名单 + `allow_credentials=True`，与 Cookie 会话一致；
- Cookie：HttpOnly + SameSite=Lax + 生产强制 Secure；
- CSRF：所有非安全方法强制 `X-CSRF-Token` 与会话绑定 token 比对（hmac.compare_digest）；
- Session：库内仅存 SHA-256 摘要；过期/撤销/停用即时失效；
- 代理信任边界：认证与 egress 代理正交——代理策略目标白名单不受认证配置影响（WP2 文档详述）。

## 2. 前端登录系统（§1.2）

新增/修改：

| 文件 | 内容 |
|---|---|
| `apps/web/src/views/LoginView.vue` | 登录页（label 关联、错误提示、限速提示、密码可见切换、回跳地址携带、认证状态预检） |
| `apps/web/src/views/UsersView.vue` | 用户管理（platform-admin）：列表/新建（角色+workspace 范围）/停用启用 + 角色×权限矩阵（服务端权威映射展示） |
| `apps/web/src/auth.ts` | 认证 store：用户状态仅来自 `/auth/me`；`can()` 仅做 UI 呈现；密码修改后清空会话 |
| `apps/web/src/api.ts` | 非 GET 自动注入 `X-CSRF-Token`；401 → 清凭据 + 全局跳登录（携带回跳）；403 抛 ApiError 由视图提示 |
| `apps/web/src/router.ts` | `/login`、`/users` 路由；beforeEach 守卫（未登录跳登录、已登录访问登录页跳工作台、adminOnly 路由守卫） |
| `apps/web/src/App.vue` | 顶栏用户会话区（用户名+角色徽章+菜单）、修改密码弹窗、退出登录、全局 401 注册、管理菜单按角色显示 |

行为验证（真实 Chromium，`tests/e2e/test_login_ui_run010.py`）：
1. 未登录访问 `/runs` → 跳转登录页；
2. 错误密码 → 「用户名或密码错误」提示，不进入；
3. 正确密码 → 进入工作台，顶栏显示真实用户名（来自 `/auth/me`）；
4. platform-admin 可见「用户与角色」菜单；viewer 不可见且直接访问 `/users` 被守卫送回；
5. 修改密码弹窗开/关正常；退出登录 → 回登录页。

**服务端仍是最终权限判定者**：前端 `can()` 只影响菜单/按钮显隐，所有 API 的权限判定在服务端中间件 + 路由层完成。

## 3. RBAC 权限审查与对象级授权（§1.3）

### 权限映射审查结论

- `action_for_request()`：未知写操作 fail-closed 落入 `system.configure`（仅 platform-admin）——新增 API 默认拒绝 ✅；
- Repair 审批（`repair.approve`，qa-lead+）与晋升（`repair.promote`，仅 platform-admin）分别授权 ✅；
- Agent/LLM 永不在角色集合；`require_human_actor` 拒绝 agent:/llm:/bot:/system:/worker: 主体（既有测试保持）✅；
- 请求体 `reviewer / approved_by / operator` 声明一律忽略，审批主体只来自服务端 Principal ✅（测试 `test_forged_identity_declarations_are_ignored`）。

### Workspace 级数据隔离（本轮新增）

「仅按 HTTP 路径映射权限 ≠ Workspace 级数据隔离」——补齐三层：

1. **用户范围模型**：`tg_user.workspaces`（迁移 v12，默认 `["*"]`）；
2. **查询参数层**：中间件解析 `workspace_id` 查询参数，越界 → 403 `WORKSPACE_FORBIDDEN`（覆盖全部列表/创建端点与 SSE）；
3. **实体层**：`require_workspace_doc(principal, doc_workspace)` 供 detail 路由在取得实体后校验；
4. platform-admin 恒放行；`*` 表示全部范围。

已验证：engineer（范围 `["default"]`）访问 `?workspace_id=ws-b` → 403；范围收窄后旧请求立即被拒；停用用户后全部会话撤销且无法再登录。

## 4. 登录安全增强（§1.4）

| 审查项 | 结论/处置 |
|---|---|
| 登录限速被多进程/重启绕过 | **已修复**：失败计数从进程内 dict 迁移到 `tg_login_attempt` 表（原子 INSERT + 窗口 COUNT），多进程共享，重启不可绕过（测试：连续 5 次失败后，新 AuthManager 实例仍 429） |
| 用户名枚举 | 未知用户与错误密码返回同一 `INVALID_CREDENTIALS`；不存在的用户名执行等价 PBKDF2 计算，抹平响应时差 |
| Session 撤销 | 登出/密码变更/用户停用全部撤销；停用用户旧会话经 DB 校验失效 |
| 密码变更后旧 Session | `change_password` → `revoke_all_sessions`（既有）+ 测试覆盖 |
| Cookie 固定攻击 | 会话令牌登录时新造（`secrets.token_urlsafe(32)`），不复用旧 token；HttpOnly + SameSite |
| CSRF | 非安全方法强制 token 比对；错误/缺失 token → 403 `CSRF_FAILED`（测试覆盖） |
| 用户停用 | 停用即撤销全部会话 + 登录拒绝（active=false 校验） |
| 并发登录 | 允许多会话并存（产品决策），支持按 token 粒度撤销；TTL 上限在受控环境强制 ≤ 24h |
| 审计哈希链并发写入 | **已修复**：新增 `seq` 单调列（唯一索引兜底乐观分配 + 冲突重试重读 prev_hash）；迁移回填历史行顺序与哈希链一致；两个独立 Database 连接交替追加后链完整（测试验证） |

明确边界（如实声明）：哈希链保证「可检测删改」；并发下的顺序由 seq 唯一索引保证；
SQLite 单写者 + PG 事务隔离下重试路径均验证。TLS 内容检查、宿主级网络限制不属于本 WP 范围。

## 5. 验收清单核对（§1.5）

| 验收项 | 测试 | 结果 |
|---|---|---|
| 未登录不能访问业务 API | `test_unauthenticated_fail_closed_includes_new_endpoints` | ✅ |
| viewer 不能执行写操作 | `test_viewer_cannot_write_but_can_read` | ✅ |
| qa-engineer 不能审批 Repair | `test_engineer_cannot_approve_repair` | ✅ |
| qa-lead 不能执行管理员高风险操作（promote/rollback/用户管理） | `test_lead_cannot_promote_or_rollback` | ✅ |
| platform-admin 可执行授权范围内管理操作 | `test_admin_can_manage_users_and_permissions` | ✅ |
| 客户端伪造角色被拒绝 | `test_forged_identity_declarations_are_ignored` | ✅ |
| 跨 Workspace 读取被拒绝 | `test_cross_workspace_access_denied` | ✅ |
| 过期与撤销 Session 被拒绝 | `test_expired_session_rejected`、`test_user_workspace_scope_update_and_revocation` | ✅ |
| CSRF 攻击被拒绝 | `test_csrf_attack_rejected` | ✅ |
| 审计链被篡改能够检测 | `test_audit_chain_tamper_detection_with_seq_order` | ✅ |

附加：`test_login_rate_limit_persist_across_restart`、`test_login_failure_counter_scoped_by_client`、
`test_unknown_username_timing_equalized`、`test_audit_append_across_manager_instances_shares_chain`、
策略 6 用例、真实浏览器 E2E 2 用例。

## 6. 真实缺陷修复（本轮发现）

1. **`record_audit` dict 参数 500**：`set_user_active(after={...})` 在 SQLite 下因 raw SQL 无法绑定 dict 抛
   `ProgrammingError`。RUN-009 未测到该路径；已改为显式 JSON 序列化并补测试。
2. **RUN-009 认证限速可绕过**：进程内 dict 计数在多进程部署/重启后归零（本 WP 已按 DB 计数修复）。
3. **测试隔离缺陷**：`_effective_auth_enabled` lru_cache 在测试间泄漏；两套 fixture 均补 `reset_auth_policy_cache()`，
   随机顺序两轮 580 passed 验证稳定。

## 7. 状态

- 代码与测试：IMPLEMENTED / VERIFIED（本地真实执行）。
- 生产候选判定：认证系统达到「生产候选」——受控环境启动期强制、对象级授权、DB 级限速、审计链并发安全均落地并有测试。
- 遗留：修复审批/晋升路由的实体级 workspace 校验（detail 路由逐个接入 `require_workspace_doc`，本轮已覆盖中间件查询参数层与高危审批入口的权限层）；PG 环境下的重复验证依赖 WP8 集成验收。
