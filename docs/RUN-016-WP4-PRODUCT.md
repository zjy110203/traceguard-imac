# RUN-016 WP4：产品权限与交互功能完善

> 范围：`/system/config` 权限边界、SSE 撤权及时性、前端核心业务流真实 E2E。
> 原则：服务端判定，不靠前端隐藏；不为了"看起来绿"放松断言。

---

## 1. `/system/config` 权限边界

### 1.1 修复前

- 端点只读，无密钥泄漏；
- 但**任何已认证角色**都能读到 `database`（方言）、`artifact_dir`（宿主机绝对路径）、
  `worker.id`（`hostname-pid`）、`environment`；
- 运行期**没有任何配置写接口**（配置全部来自 `TG_*` 环境变量，重启生效）。

### 1.2 修复后

| 字段 | platform-admin | 其它角色 | 认证关闭（本地） |
|---|---|---|---|
| `environment` | ✅ | ✅ | ✅ |
| `teamflow_base_url` | ✅ | ✅ | ✅ |
| `browser.{headless,channel,timeout_ms}` | ✅ | ✅ | ✅ |
| `worker.inline` | ✅ | ✅ | ✅ |
| `database` | ✅ | `null` | ✅ |
| `artifact_dir` | ✅ | `null` | ✅ |
| `worker.id` | ✅ | `null` | ✅ |
| `access_level` | `admin` | `restricted` | `admin` |

说明：敏感字段保持**键存在、值为 null**（脱敏而非隐藏），前端无需分支，权限测试可直接断言。

### 1.3 写入口审计

| 路径 | 方法 | 权限 |
|---|---|---|
| `/api/v1/system/config` | GET | `read`（脱敏） |
| `/api/v1/system/config` | PUT/PATCH/POST/DELETE | **不存在**（404/405） |
| `/api/v1/system/seed` | POST | `system.configure`（platform-admin） |
| `/api/v1/system/browser?probe=` | GET | `system.observe`（platform-admin） |
| `/api/v1/users/**` | 写 | `user.manage`（platform-admin） |

结论：不存在"普通用户修改全局安全配置"的 API 路径；改配置只能改环境变量并重启，
因此不需要运行期写审计（没有写操作可审计）。

### 1.4 测试

`tests/integration/test_system_config_rbac_run016.py`（10 项全部通过）：

- 未登录 → 401；
- admin 看到完整视图（含三敏感字段）；
- engineer / lead / viewer 三敏感字段均为 `null`，非敏感字段仍可用；
- 4 种写方法均 404/405（带 CSRF 的管理员请求）；
- 认证关闭模式保持完整视图。

## 2. SSE 撤权及时性

### 2.1 参数口径变化

| 参数 | 修复前 | 修复后 |
|---|---|---|
| 默认重核验间隔 | 2.0s | 2.0s（不变） |
| 最小间隔 | 0.1s | 0.1s（不变） |
| **服务端上限** | **10.0s** | **5.0s** |
| 非法值（NaN/Inf/字符串/None） | 回落默认 2s | 回落默认 2s（不变） |

### 2.2 撤权路径

| 撤权动作 | 服务端行为 |
|---|---|
| 会话撤销 / 账户禁用 | `resolve()` 返回 None → `auth.revoked(SESSION_INVALID)` 并关闭连接 |
| 会话被重绑到其它用户 | `SESSION_REBOUND` 并关闭 |
| 取消某 Workspace 授权 | 针对**用户请求的那个 workspace** 判定 → `WORKSPACE_REVOKED` 并关闭 |
| 其它 Workspace 授权变化 | 立即更新过滤集合，无需重连 |

### 2.3 测试

- `tests/integration/test_sse_recheck_limit_run015.py`（更新上限断言）：**17 项通过**
  （含 1e9 / NaN / Inf 三种恶意间隔下的 session / workspace / 禁用用户撤权）；
- 高并发与延迟测量：见 §3 的 E2E（多用户并发订阅）与 `docs/assets/run016/wp4/`。

### 2.4 性能影响

把重核验上限从 10s 收紧到 5s，相当于**每个长连接最坏情况多一倍**的 session 解析
（一次 DB 查询 + 一次密码学无关的 token 哈希比对）。当前部署规模（单 API、并发连接受限）
未观测到压力；若未来出现明确性能问题，处理方式是**给 session 解析加缓存/索引**，
不允许删除权限检查来换性能。

## 3. 前端核心业务流 E2E

执行：`scripts/business_loop_ui_acceptance_run016.py`
（独立 API + 2 个独立 Worker + 真实 Chromium + 真实 TeamFlow，`TG_INLINE_WORKER=false`）

**实测结果：58 PASS / 0 FAIL，15 张截图**（`docs/assets/run016/wp4/ui/`，
原始 JSON：`run016-ui-full-loop.json`）。13 条流程逐条对应：

| # | 流程 | 对应校验 | 结果 |
|---|---|---|---|
| 1 | 创建/查看测试任务 | ① UI 创建需求落库、② 新建用例、⑥ 审核通过 | PASS |
| 2 | 浏览 Run 执行进度 | ⑦ 执行产生 Run、⑧ Worker 执行到终态、⑨a 独立 Worker 持租约 | PASS |
| 3 | 查看 Step / Event / Evidence | ⑩ 真实步骤轨迹与证据（19 条证据；Run 详情页步骤/证据页签） | PASS |
| 4 | 查看 Diagnosis | ⑬ 诊断入口、⑭ UI 生成 Diagnosis | PASS |
| 5 | 查看 Evidence Graph | **⑭a 证据图**：API 35 节点，页面 SVG 38 个元素，标题渲染 | PASS |
| 6 | 查看 Probe 结果 | **⑭b 探针建议**：API 3 条、UI 3 行且"执行（只读）"可点 | PASS |
| 7 | 查看 Repair Proposal | ⑮ 修复入口、⑰ 提案落库、⑱ VALIDATED | PASS |
| 8 | 查看 Sandbox 验证 | ⑲ UI 沙箱 → 服务端真实执行并落库 PASSED | PASS |
| 9 | 人工审批 | ⑳ PENDING_APPROVAL、㉑ APPROVED | PASS |
| 10 | 查看 Promotion 状态 | ㉒ PROMOTED 且 `promoted_version=2` | PASS |
| 11 | 发起/查看 Regression | **㉒a UI 运行回归验证**：真实回归 Run，`PASSED/PASSED` | PASS |
| 12 | 执行允许的 Rollback | ㉓ ROLLED_BACK 且 `rollback_record` 存在 | PASS |
| 13 | 查看历史操作与状态变化 | ⑰-㉓ 各步均以服务端状态回读校验；回滚留痕 | PASS |

补充的 Worker/可靠性场景（22 项）也全部 PASS：Worker 启动失败不污染队列、
SIGTERM 重启、SIGKILL 接管、暂停接管、旧 Worker 恢复不可覆盖、过期提交被 Fencing 拒绝、
API 强杀重启、双 Worker 并发消费无重复步骤/双重提交。

同时验证：按钮真实触发请求、状态与服务端一致、权限不足有明确反馈
（viewer/engineer 的路由守卫与 403 提示在 RUN-011-R1.1 脚本中覆盖，本轮未重复）。

## 4. 前端质量门禁

- `vue-tsc --noEmit`：通过；
- `npm run build`：通过（产物 `apps/web/dist`）。

## 5. 仍未覆盖

- 多浏览器/多分辨率回归：本轮只跑 Chromium 默认视口；
- 前端"重新验证（INCONCLUSIVE retry）"入口：依赖 WP2 决策稿，本轮不实现；
- SSE 撤权延迟的**分布式**测量（多 API 实例）：当前单实例部署，未做。
