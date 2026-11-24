# RUN-009 WP2：真实身份认证与 RBAC

> 状态：IMPLEMENTED / VERIFIED（服务端认证、会话、CSRF、RBAC、审计；本地认证模式）。
> 生成时间：2026-11-24。权威起点 `cb80591`。

## 1. 目标与边界

RUN-008 的事实是：`require_human_actor()` 只能按主体命名空间判断「不是 agent/llm/bot」，请求体中的
`reviewer` / `approved_by` / `operator` 由客户端声明，可被伪造；没有登录、没有会话、没有服务端 Principal。

本阶段实现的是**单机可用的本地认证模式**（Authentication → Session → Principal → Authorization → Audit），
保留后续 OIDC/SSO 扩展能力；不引入企业 IAM，不引入 OAuth/JWT。

## 2. 实现

### 2.1 数据库迁移 v11（`auth_rbac_audit`）

`packages/persistence/traceguard_persistence/db.py` 追加迁移 11，幂等创建：

- `tg_user(user_id PK, username UNIQUE, password_hash, roles JSON, active, created_at, updated_at)`
- `tg_session(token_hash PK, user_id, csrf_token, expires_at, revoked, created_at)` + user 索引
- `tg_audit_log(audit_id PK, actor_id, role, action, resource_type, resource_id, timestamp, result,
  request_id, before_json, after_json, prev_hash, entry_hash)` + actor/action 索引

沿用既有迁移规范：不修改 v2–v10；DDL 幂等；SQLite/PostgreSQL 同构。

### 2.2 密码、会话与主体（`packages/persistence/traceguard_persistence/auth.py`）

- 密码：`pbkdf2_sha256$200000$salt$digest`，`hashlib.pbkdf2_hmac` + `hmac.compare_digest`；不存明文。
- 会话：`secrets.token_urlsafe(32)`，库里只存 SHA-256 摘要；会话可撤销、可过期、可全量撤销。
- 角色：`viewer / qa-engineer / qa-lead / platform-admin`，未知角色创建即拒绝。
- 权限矩阵（代码即事实，`PERMISSIONS`）：

| 动作 | viewer | qa-engineer | qa-lead | platform-admin |
|---|---|---|---|---|
| 读（GET） | ✓ | ✓ | ✓ | ✓ |
| testcase/需求 创建 | | ✓ | ✓ | ✓ |
| testcase.review（审核） | | | ✓ | ✓ |
| oracle.create / oracle.bind | | | ✓ | ✓ |
| run.execute / 任务执行 | | ✓ | ✓ | ✓ |
| diagnosis.operate / probe.execute / repair.submit | | ✓ | ✓ | ✓ |
| repair.approve / reject | | | ✓ | ✓ |
| repair.promote | | | | ✓ |
| repair.rollback | | | | ✓ |
| system.configure / user.manage | | | | ✓ |

Agent 和 LLM 仍由 `require_human_actor()` 拒绝，永远不在角色集合中；高风险 Repair 的 approve/promote/rollback
必须经过授权人类主体（Principal）。

### 2.3 API 层（`apps/api/traceguard_api/auth.py` + `routers/auth.py`）

- `POST /api/v1/auth/login`：校验密码，签发 HttpOnly Cookie（`SameSite=Lax`，`Secure` 由配置决定）；
  返回 `csrf_token`；同一用户名+来源在 300 秒内失败 5 次后 429。
- `POST /api/v1/auth/logout` / `GET /api/v1/auth/me` / `POST /api/v1/auth/password`：
  会话撤销、当前主体、密码修改（修改后全量撤销会话）。
- `GET/POST /api/v1/auth/users`、`POST /api/v1/auth/users/{id}/active`：仅 `platform-admin`；
  停用用户时同时撤销其全部会话。
- `GET /api/v1/auth/audit/verify`：审计哈希链校验（platform-admin）。
- 全局 HTTP 中间件在 `TG_AUTH_ENABLED=true` 时对所有 `/api/v1/**` 生效：
  1. 未认证 → `401 UNAUTHENTICATED`，**不回退匿名管理员**；
  2. 非安全方法（POST/PUT/PATCH/DELETE）必须带与服务端会话一致的 `X-CSRF-Token`，否则 `403 CSRF_FAILED`；
  3. 按 `action_for_request()` 映射 RBAC 动作（未知写操作 fail-closed 归入 `system.configure`），
     无权限 → `403 PERMISSION_DENIED` 并写审计 DENIED 记录；
  4. 通过后把 Principal 放入 `request.state`，响应带 `X-Request-ID`。

### 2.4 审批主体可信化（`trusted_actor()`）

`trusted_actor(request, declared, action, ...)` 的语义：

- 认证开启：**忽略请求体声明的 `reviewer` / `approved_by` / `operator`**，主体一律来自服务端 Principal
  （`user:<username>`），校验动作权限并写审计（含 `declared_actor_ignored`）；Agent/LLM 自批路径不变。
- 认证关闭（显式 dev/迁移模式）：保留既有主体命名空间门禁语义；空/空白主体仍交由
  `require_human_actor()` 以 412 拒绝，不提前改变状态码，因此 RUN-008 的 14 个主体校验用例全部保持通过。
- 已接入：testcase review、oracle-bindings、oracle 创建审批、disposition review、repair approve/reject/
  promote/rollback。promote 在 dev 模式下使用显式声明的 `user:dev-operator`，认证开启后仍是真实 Principal。

### 2.5 审计日志与防篡改

`record_audit()` 追加：actor_id、role、action、resource_type、resource_id、timestamp、result、request_id、
before/after（适用时）；不记录密码/令牌/密钥。`entry_hash = sha256(canonical(entry + prev_hash))` 形成
链式哈希；`verify_audit_chain()` 逐条重算并检测删改。跨进程写入的排序按 `timestamp, audit_id`，单进程内
加锁；如需强一致的多进程审计链，后续应升级为数据库序列/事务锁（本阶段如实披露为已知边界）。

### 2.6 管理员初始化与兼容性

- `scripts/auth_bootstrap.py`：读取 `TG_BOOTSTRAP_USERNAME` / `TG_BOOTSTRAP_PASSWORD`（或 getpass 交互），
  首次创建 `platform-admin`；不把凭据写入仓库；已存在时拒绝覆盖。
- 认证默认**显式关闭**（`TG_AUTH_ENABLED=false`），用于 dev/迁移；启动时记录 `auth_disabled_explicitly`
  日志。开启后未认证请求 fail-closed，不会静默回退匿名管理员；旧 API 客户端必须先走
  `/api/v1/auth/login`，这是显式迁移路径而非无提示失效。
- 新增配置（`traceguard_core/config.py`）：`TG_AUTH_ENABLED`、`TG_AUTH_SESSION_TTL_S`、
  `TG_AUTH_COOKIE_NAME`、`TG_AUTH_COOKIE_SECURE`、`TG_AUTH_CSRF_REQUIRED`、`TG_AUTH_MAX_FAILED_LOGINS`、
  `TG_AUTH_LOGIN_WINDOW_S`。

## 3. 测试与证据

新增 `tests/integration/test_auth_rbac_run009.py`，9 个用例全部通过：

1. 未登录访问 `/testcases` `/runs` `/diagnoses` → 401 fail-closed；
2. 登录成功 / 错误密码 401 / me 返回 `user:admin`；
3. viewer 低权限写 → 403；qa-engineer 伪造 `reviewer=role:qa-lead` 审核被拒，审计保留 DENIED；
4. 缺 CSRF → 403；logout 后 me → 401；
5. 并发双会话：撤销会话 A 后会话 B 仍有效（partial revoke）；
6. 过期会话（DB 置 `expires_at` 过去）→ 401；
7. 密码修改后旧会话全量撤销、旧密码 401、新密码可登录；
8. 审计链完整 → `intact=true`；篡改一条记录后 → `intact=false`；
9. `agent:planner` / `llm:advisor` / `bot:ci` / `system:runner` / `worker:01` 仍被业务门禁拒绝。

回归结果（本轮真实执行）：

- `tests/unit + tests/contracts`：**292 passed**；
- `tests/integration`：修复空主体语义后 **253 passed / 13 skipped**（PG 仅 CI），
  其中 `test_actor_validation.py` 全部恢复绿色；
- 迁移 v11 在全新内存库与既有库上幂等通过（集成测试建立真实 app 时执行）。

## 4. 状态汇总

| 项目 | 状态 |
|---|---|
| 服务端认证、会话、CSRF、RBAC、审计日志 | IMPLEMENTED / VERIFIED |
| 请求体审批人伪造防护（认证开启时） | VERIFIED |
| Agent / LLM 自批防护（业务门禁） | VERIFIED |
| 审计哈希链完整性 | VERIFIED（单进程）；跨进程强一致 NEEDS_DECISION |
| OIDC/SSO 扩展 | NOT_IMPLEMENTED（保留扩展点） |
| 前端登录页接入 | NOT_IMPLEMENTED（API 已具备，前端 UI 未改） |
