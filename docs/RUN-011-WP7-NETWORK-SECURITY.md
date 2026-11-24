# RUN-011 WP7：网络安全与 Egress 深度验证（PARTIAL）

> 执行时间：2026-11-25 01:30–02:10 CST。
> 证据：
> - 正式闭环两轮：`docs/assets/run011/full-loop/loop{1,2}.json`（`report.browser.egress`）
> - 负向验证：`docs/assets/run011/wp7/network-negative.json`（4/4）
> - 历史多次运行（含 2 次对照失败）：`network-negative-1.json` 等（保留，不删除失败样本）

## 0. 结论

- **未授权目标在真实执行链路上被阻断，且不可静默直连**：本轮共 7 次负向运行，
  5 次 4/4 通过（阻断分布在"路由守卫前置拒绝 → egress total=0"与"代理 BLOCK"两条路径），
  2 次的**对照用例**因授权目标自身的 https 变体被拦而失败（见 §4，与未授权目标无关）。
- **阻断在步骤层可见**：本轮修复前，被阻断的 `navigate` 会被记成 **PASSED**
  （只在 egress 报告里留 blocked=1），这是真实缺陷；修复后步骤 `FAILED`，
  错误为 `TargetAccessDenied: TARGET_NOT_ALLOWED`。
- **宿主级隔离仍然 BLOCKED**：本机无 pf / 容器网络权限，未修改系统防火墙。
- **OPEN-RISK-01**：2 次对照运行出现"授权目标 `https://127.0.0.1:8112` 被代理 BLOCK"
  （Chromium HTTPS-Upgrade 的 CONNECT），与未授权目标无关，但会偶发使正常用例失败。

## 1. 正式闭环中的 Egress 证据

| 项 | loop1 | loop2 |
|---|---|---|
| `report.browser.egress.enforced` | true | true |
| 代理地址 | `http://127.0.0.1:56698` | `http://127.0.0.1:57379` |
| 请求数 / 允许 / 阻断 | 14 / 14 / 0 | 14 / 14 / 0 |
| `worker_id` | run011-w3 | run011-v2 |
| `run_id` 绑定 | 与 Run 一致 | 与 Run 一致 |
| 每个 Run 独立代理 | 是（端口不同） | 是 |

正式执行的 14 个请求全部经由代理；没有"直连回退"路径。

## 2. 负向验证（`scripts/network_negative_run011.py`）

构造一个**人工编写**用例（`compile_source=human`，审核门禁显式记录豁免），
步骤只有 `navigate http://127.0.0.1:9555`（无监听、未授权）。qa-lead 绑定 Oracle 并审批，
由独立 Worker 执行。

| 检查 | 结果 |
|---|---|
| N1 执行者是独立 Worker，且不判为通过 | ✅ `run011-v2`，`execution_status=FAILED`，`verdict=NOT_EVALUATED` |
| N2 未授权目标零成功响应 | ✅ 路由守卫前置拒绝，`egress total=0`（该 Run 未发出任何出站请求） |
| N3 失败原因明确 | ✅ `TargetAccessDenied: TARGET_NOT_ALLOWED` |
| N4 对照：授权目标正常执行 | ✅ 同一 Worker 池中 `PASSED`，`egress 14/14 allowed, 0 blocked` |

另一类路径（最终两次运行，`network-negative.json` / `network-negative-2.json`）：
请求到达代理 → `blocked_targets=["https://127.0.0.1:9555"]`，
`blocked_reasons=["127.0.0.1:9555 https PORT_NOT_ALLOWED"]`，
`total=20/23, allowed=16/22, blocked=4/1`。两种路径都满足"未授权目标不可达"。

## 3. 本轮修复的两个真实缺陷

### 3.1 被阻断的导航被记成 PASSED（安全可见性缺陷）

- 现象：未授权目标被代理阻断后，浏览器收到代理的 403 页面，`page.goto()` 正常返回 →
  步骤记录为 `PASSED`，只在 `report.browser.egress.blocked=1` 里留痕。
- 影响：用户/审计者会看到"导航成功"，与"目标被拒绝"事实相反；验收无法据此判定失败。
- 修复：
  1. 代理的阻断响应增加 `X-TraceGuard-Egress: BLOCK` 头（不含任何目标内容）；
  2. `playwright_observer` 在 `navigate` 后检查该头 → 抛 `TargetAccessDenied` → 步骤 FAILED。
- 回归：负向用例 N1/N3（真实执行）。

### 3.2 阻断原因不可见（可观测性缺陷）

- 现象：`audit_summary()` 只给 `blocked_targets`，不给 `reason`；无法区分
  `PRIVATE_NETWORK_BLOCKED` / `SCHEME_NOT_ALLOWED` / `DNS_REBINDING` 等。
- 修复：`report.browser.egress.blocked_reasons` 新增
  `host:port scheme reason`（最多 20 条，不含 path/query/header）。
- 回归：egress 相关 13 个测试通过。
- 真实效果：负向运行得到 `PORT_NOT_ALLOWED`，不再只有"某目标被拦"。

### 3.3 出站审计跨 Run 串账（可观测性缺陷）

- 现象：代理按 Worker 线程复用，`audit_summary()` 统计**全部历史条目**；
  对照 Run 的报告里出现上一个负向 Run 的 `blocked=4`，无法按 Run 对账。
- 修复：`audit_summary()` 在存在 run 上下文时只统计 `run_id` 匹配的条目。
- 真实效果（修复后两次运行）：对照 Run `blocked=0`；负向 Run 只统计自身。
- 回归：egress 13 个测试通过；`network-negative-1.json` / `-2.json`。

## 4. OPEN-RISK-01：授权目标的 HTTPS-Upgrade 偶发被拦

| 次 | 场景 | 观察 |
|---|---|---|
| 1–2 | 对照（loop2 已审批用例） | `blocked_targets=["https://127.0.0.1:8112"]`，`blocked_reasons=["127.0.0.1:8112 https PORT_NOT_ALLOWED"]`，Run `NOT_EVALUATED` |
| 3–7 | 对照 | 正常（allowed 全部通过，blocked=0） |

> 关键事实：同一个授权 origin（`http://127.0.0.1:8112`）在多数运行里 https 变体也被放行
> （`scheme_dual_policy` 生效），少数运行被判 `PORT_NOT_ALLOWED` —— 说明代理持有的策略
> 在那些运行里**不是对偶变体**。

事实：
- 被拦的是**授权目标自身**的 `https` 变体，不是未授权目标；
- `scheme_dual_policy()` 本应把同一 host:port 的 http/https 都放行；
- 只在 Worker 刚重启后的首批运行中出现，后续稳定。

未定位根因（可能是 Chromium HTTPS-Upgrade 首批 CONNECT 与策略装配时序竞争）。
处置：记录为 **OPEN**，不宣称"代理零缺陷"；下一步需要给审计条目加 `policy_origins` 快照，
并在 `BrowserManager.start()` 后做一次预热请求。

## 5. 未覆盖的测试项（指令 §10 清单）

| # | 项 | 状态 | 原因 |
|---|---|---|---|
| 3 | 私网 IP | **PARTIAL** | 授权目标本身是 127.0.0.1；未构造"未授权私网"用例（需要额外进程） |
| 4 | IPv6 | **NOT_RUN** | 本机监听均为 IPv4 |
| 5 | DNS rebinding | **PARTIAL** | 单测覆盖（`test_target_policy_browser.py`） |
| 6 | 多跳重定向 | **PARTIAL** | 3xx 链路有单测；未做 5 跳真实站点 |
| 7 | 顶层 302 | **PARTIAL** | RUN-007 已知限制：顶层跳转的**出站请求会到达目标**，产品保证是"落地拒绝 + 步骤失败" |
| 8 | WebSocket | **PARTIAL** | 已有 `route_web_socket` 守卫与单测；未做真实 WS 目标 |
| 9 | 代理死亡 | **NOT_RUN（本轮）** | RUN-010 有单测；本轮未在独立拓扑杀代理线程 |
| 10 | 代理端口失效 | **NOT_RUN** | 同上 |
| 11 | Worker 退出 | **PARTIAL** | 有历史证据（RUN-008 SIGKILL），本轮未重跑 |
| 12 | 浏览器残留连接 | **NOT_RUN** | 需要 ps/lsof 观察，沙箱限制 |

## 6. 结论

应用层三层防护（TargetPolicy → 路由守卫 → 受控代理）在真实拓扑中有效，
未授权目标零成功响应；阻断在步骤层可见、原因可审计。
但**宿主级隔离未验证**，且存在 1 个偶发的授权目标 HTTPS 升级拦截问题（OPEN-RISK-01），
因此不能宣称"网络安全已完整验收"。
