# RUN-015 WP3：SSE 授权重核验间隔上限

> 分支：`workbuddy/run015-execution-consistency`｜状态：**PASS**

---

## 1. 缺陷与修复

### 1.1 缺陷

`GET /events/stream` 的 `auth_recheck_seconds` 客户端参数只有下界：
`max(0.1, auth_recheck_seconds)`。客户端可以传入极大值（如 `1e9`）把活跃连接的
授权复核周期拖到几乎无限，从而延迟 Workspace 撤权、会话撤销、用户禁用生效。
NaN / Infinity / 非数值参数也没有明确处理。

### 1.2 修复（`apps/api/traceguard_api/main.py`）

新增服务端常量与纯函数：

- `SSE_AUTH_RECHECK_DEFAULT_SECONDS = 2.0`
- `SSE_AUTH_RECHECK_MIN_SECONDS = 0.1`（保留短周期测试能力）
- `SSE_AUTH_RECHECK_MAX_SECONDS = 10.0`（**服务端硬上限**）
- `effective_auth_recheck_seconds(requested)`：
  - 非数值 / `NaN` / `±Infinity` → 回落默认值 2.0；
  - 有限数值 → 收敛到 `[0.1, 10.0]`。

连接建立时计算一次 effective 值，recheck 循环使用该值；响应头新增
`X-TraceGuard-Auth-Recheck-Seconds` 暴露实际生效周期，便于审计与测试。
客户端传入超大间隔后，撤权仍在 ≤10s 的窗口内生效（测试中以收紧后的上限 0.4s 快速验证机制）。

撤权语义未变：`auth.revoked` 控制帧 + 立即关闭连接，原因分别为
`SESSION_INVALID` / `SESSION_REBOUND` / `WORKSPACE_REVOKED`。

---

## 2. 修改文件

| 文件 | 变更 |
|---|---|
| `apps/api/traceguard_api/main.py` | 常量、`effective_auth_recheck_seconds()`、recheck 使用收敛值、响应头 |
| `tests/integration/test_sse_recheck_limit_run015.py` | 7 项专项测试 |
| `docs/assets/run015/wp3/sse-recheck-raw.txt` | 原始 pytest 输出 |

---

## 3. 测试与证据

原始输出：`docs/assets/run015/wp3/sse-recheck-raw.txt`（7 passed）。

| 类别 | 覆盖 |
|---|---|
| 默认值 | `2.0`；异常值回落到默认值 |
| 最小值 | `0.01 → 0.1` |
| 正常值 | `1.5 → 1.5` |
| 超大值 | `1e9 → 10.0`；端到端撤权仍生效 |
| Infinity / NaN | 端到端会话/Workspace 撤权仍生效；响应头显示回落的默认值 |
| Workspace 撤权 | `WORKSPACE_REVOKED`，重新授权后可订阅 |
| 会话撤销 | `SESSION_INVALID` |
| 用户禁用 | `SESSION_INVALID` |
| 授权重新授予 | 重新登录后正常订阅 |

影响面：`pytest tests/integration/test_sse_recheck_limit_run015.py tests/integration/test_sse_revocation_and_global_rbac_run014.py -q` → **20 passed**（RUN-014 原 13 项 SSE/RBAC 测试无回归）。

## 4. 边界

- 上限 10s 是本轮服务端默认策略；如需配置化须由负责人决定，当前写死以避免客户端绕过。
- `heartbeat_seconds` / `poll_interval` 仍只有下界，但它们不参与授权判定，本轮不改。
