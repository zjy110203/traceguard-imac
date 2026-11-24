# RUN-008 WP6：安全与权限专项审查

## 1. 本轮修复（VERIFIED）

### 1.1 Repair 内存服务与 API 层主体门禁不一致（中危）

- **问题**：`SelfHealingService.approve()` 只挡 `approver.lower().startswith("agent")`。
  因此 `system:promotion`、`llm:deepseek`、`bot:ci`、`worker:1` 等非人类主体可以自批 Repair，
  而 API 层 `RepairService.approve()` 早已调用 `require_human_actor`。两层服务权限判定不一致。
- **修复**：`packages/agents/traceguard_agents/healing/service.py` 的 `approve()` 与 `reject()`
  统一复用 `traceguard_persistence.catalog.require_human_actor`；`reject()` 也补上主体校验。
  `approval_ref` 使用规范化后的 actor。
- **依据**：P08 原问题「审批主体只有类型门禁、无强认证」在授权范围内可独立修复的部分是
  **统一两层主体类型判定**；真实身份认证（B-09）仍需外部基础设施，不伪造。
- **替代方案**：在 API 路由层再包一层校验 —— 缺点是无法覆盖直接调用领域服务的 Agent 路径。
- **影响**：内部接口 `SelfHealingService.approve(reviewer)` / `reject(proposal_id, reviewer)`；
  `reject` 新增可选参数（默认 `human:qa`），现有调用不破坏。数据无迁移。
- **测试**：`tests/unit/test_healing.py` 参数化覆盖 5 种非人类主体 + 非人类 reject；
  连同原有用例 **17/17 通过**。

### 1.2 日志字符串脱敏词表窄于字典键（中危）

- **问题**：`redact()` 字典键覆盖 `cookie/access_key/private_key/credential`，
  但字符串内联正则只覆盖 `password/secret/token/authorization/api_key`。
  形如 `Cookie: session=xxx` 的日志会把真实凭据原样写出。
- **修复**：抽出 `_SENSITIVE_INLINE`，由同一份 `_SENSITIVE_KEY.pattern` 构造，
  字符串路径与字典路径共用词表。
- **测试**：`tests/unit/test_core.py` 新增 4 类凭据断言；与 `test_security.py`、`test_healing.py`
  合计 **36/36 通过**。

## 2. 已确认的设计边界（不伪造为「安全」）

| 面 | 现状 | 边界说明 |
|---|---|---|
| 身份认证 | 仅主体类型门禁（`require_human_actor`），无真实用户认证 | `role:qa-lead` 之类由请求体声明，可被伪造；生产需接 SSO/OIDC（B-09） |
| 顶层 302 | `TargetPolicy` 校验重定向链，但顶层跳转的出站请求**会到达目标**，只是落地被拒 | 不可表述为「请求未发出」；证据见 RUN-007 网络边界测试 |
| DNS rebinding | `pin_dns()` + 每次落地复核；无法接管系统 DNS，浏览器内核缓存可能短时不一 | 生产应改用受控出站代理 |
| 依赖漏洞 | CI 真实 `npm audit`：high=0 / critical=0 / **moderate=3**（dev-only，`minimatch` DoS） | 修复需 semver-major（`vite`），未 `--force` 升级；保持风险登记 B-07 |
| 凭据 | `.env` 与 `.env.*` 在 `.gitignore`，仅 `.env.example` 入库；本轮未读取任何真实凭据 | `str(SQLAlchemy URL)` 默认 hide_password=True；拼 URL 时必须 `render_as_string(hide_password=False)` |
| CORS | 默认只允许 `http://127.0.0.1:5173`，未用通配符 | 生产需按部署域名配置 |
| 产物下载 | 内容寻址 + SHA256 校验；路径由服务端 digest 生成，不接受用户路径 | `original_name` 仅用于响应头显示 |

## 3. 本轮验证证据

- `tests/unit/test_healing.py`：17 passed
- `tests/unit/test_core.py` + `test_security.py` + `test_healing.py`：36 passed
- Ruff check / format：全绿
- CI Nightly Gate `45331346274` security job：high=0 / critical=0 / moderate=3（原始日志
  `logs/run008/actions-45331346274-security.log`）

## 4. 仍需外部决策/基础设施（不标通过）

- 真实身份认证与审计主体（B-09）
- `vite` 相关 dev-only moderate 漏洞的 semver-major 升级决策（B-07）
- 生产出站代理以彻底解决 DNS rebinding 短时窗口
