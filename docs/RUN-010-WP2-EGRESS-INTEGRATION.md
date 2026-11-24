# RUN-010 WP2：受控出站代理接入正式执行内核

> 状态：**IMPLEMENTED / VERIFIED**（真实 Chromium 集成测试 5 用例 + 既有 27 个 E2E 全部通过）。
> 提交：`651d1e6`。
> 宿主级网络隔离：**BLOCKED**（本机无防火墙/网络策略权限，见 §5）。

## 1. 集成架构（§2.1 目标架构落地）

正式执行链路（默认强制，`TG_EGRESS_ENFORCED=false` 仅限开发调试且必须打警告）：

```
BrowserManager
  ├─ 1:1 EgressProxyController（后台线程事件循环，先于浏览器启动）
  └─ BrowserSession
       ├─ TargetPolicy（会话层 route guard，scheme 严格）
       └─ chromium(proxy=controller.url)
            → EgressProxy（代理层，scheme 对偶 + DNS pinning + IP 校验）
            → TargetPolicy.check（逐请求白名单）
            → Authorized Target
```

关键设计：

| 要求 | 实现 |
|---|---|
| 代理启动与关闭生命周期 | controller 随 manager.start() 先启动；manager.close() 先关浏览器再停代理（生命周期覆盖整个执行周期） |
| 浏览器启动前配置代理 | chromium `launch(proxy={"server": controller.url})`；代理起不来 → `BrowserUnavailableError`，浏览器不启动 |
| 浏览器实例与代理实例绑定 | 1:1（thread-local manager ↔ controller），审计与 Run 关联经 `set_run_context(run_id, worker_id)` |
| Worker 与浏览器会话隔离 | 每个 worker 线程独立 manager + 独立代理实例 + 独立审计缓冲 |
| 代理异常时执行失败关闭 | ① 代理死亡 → `require_egress_alive()` 在新建上下文/导航前抛 `EgressProxyDeadError`；② 浏览器因代理失联无法建立任何新连接（双重保障） |
| 不允许静默退回直连 | 无任何回退路径：代理失败 = 启动失败/执行失败；实测见 `test_proxy_death_fails_closed` |
| 代理审计关联 Run/Worker | 每条 AuditEntry 携带 run_id/worker_id；`manager.egress_audit()` 输出摘要（ALLOW/BLOCK 计数 + 被拒目标清单） |
| Explorer 与 Runner 一致 | 两者都经 BrowserSession 构造 → `set_egress_policy(session.policy)` 单一授权事实源，自动一致 |

## 2. TargetPolicy 一致性（§2.3）

- 代理层评估与 RUN-009 相同口径：URL 白名单 → DNS 解析 → 全部 A/AAAA 校验 → 私网/回环/链路本地拒绝 → 连接使用已校验 IP（关闭 rebinding TOCTOU）；
- 覆盖入口：HTTP（绝对形式）、HTTPS/WSS（CONNECT）、WS（GET Upgrade）、逐跳重定向（明文每跳重新校验）；
- URL 内联凭据拒绝（`URL_USERINFO_FORBIDDEN`）、非法协议拒绝（`SCHEME_NOT_ALLOWED`）、链路本地/组播/保留地址拒绝。

### scheme 对偶策略（本轮新增，`scheme_dual_policy`）

实测发现 Chromium 在代理模式下会对授权的 `http://` 目标先发 CONNECT（视为 https，
HTTPS-Upgrades 行为；`--disable-features=HttpsUpgrades` 实测无效）。处理：

- 代理侧策略 = 会话策略的 **scheme 对偶变体**（同 host:port 的 http/https 都放行）；
- 会话层 route guard 保持原策略（scheme 严格）——授权面没有扩大；
- host/port/私网/rebinding 检查全部不变。

### 实测结论（重要，防止复发）

`--host-resolver-rules=MAP * ~NOTFOUND`（意图强制代理承担 DNS）**禁止使用**：
实测会让 Chromium 对 http:// 目标走 CONNECT 升级路径，且 TLS 失败后存在直连回退风险。
不加该参数时 Chromium 行为正确：http:// 走绝对形式（逐请求校验），https:// 走 CONNECT（hostname 交代理解析）。

## 3. 验收（§2.5，真实 Chromium + 服务端收数验证）

测试：`tests/e2e/test_egress_integration_run010.py`（5 用例）

| 验收项 | 测试 | 结果 |
|---|---|---|
| 合法目标正常访问 | `test_browser_traffic_flows_through_proxy`（目标真实收到 ≥2 请求，页面渲染成功） | ✅ |
| 未授权目标实际收到 0 个请求 | `test_unauthorized_target_receives_zero_requests`（导航/302/page.request 三种路径，服务端收数=0，含会话层 blocked_requests 与代理层 BLOCK 双证据） | ✅ |
| 所有跳转逐跳校验 | 302→未授权：`/open-redirect` 场景 0 请求；多跳场景由 RUN-009 17/17 保留覆盖 | ✅ |
| WebSocket 同样受保护 | RUN-009 `test_dns_change_and_websocket` 保留通过（authorized ws echo 正常、未授权 ws 0 请求） | ✅ |
| 代理断开时执行失败关闭 | `test_proxy_death_fails_closed`（杀代理线程 → check_target 抛 TargetAccessDenied、new_context 抛 EgressProxyDeadError、导航失败、0 请求） | ✅ |
| 不存在自动回退直连 | 同上：代理死亡后目标 0 请求，无直连路径 | ✅ |
| 代理审计可以关联具体 Run | `test_browser_traffic_flows_through_proxy`（audit entry.run_id == run-e2e-010-a）| ✅ |
| Explorer 和 Runner 的行为一致 | `test_session_policy_is_single_source_of_truth`（两条路径的授权规则同源） | ✅ |

既有回归：`test_playwright_real`（真实执行链路 happy path/false-pass）、`test_execution_e2e`、
`test_target_policy_browser`、`test_login_ui_run010`、RUN-009 egress 17/17 场景 —— 全部通过。

## 4. 本轮发现并修复的真实缺陷

1. **`EgressProxy.serve()` 端口覆盖**：先 `start()` 后 `serve()` 时会绑定第二个临时端口并覆盖
   `_server` —— 先前监听 socket 泄漏，浏览器拿到的端口与审计/存活检查指向的端口不一致
   （浏览器日志 `proxy=...58338` vs `egress_proxy_started ...58337`）。改为幂等：已启动则直接服务现有 server。
2. **代理模式下 fetch POST 静默失败**（MAP 参数引入）：见 §2 实测结论，已移除该参数并记录。
3. **`probe_browser`/观察者路径代理缺失**：RUN-009 的代理仅在独立脚本场景运行；本轮全部执行路径默认强制。

## 5. 宿主级安全边界（§2.4）——BLOCKED（如实声明）

- 本机 macOS 15.2（arm64，非 root，euid=504）：`pfctl -s info` → `/dev/pf: Permission denied`；
- 未获得任何宿主级防火墙/网络策略授权，**未安装、未修改任何系统级防火墙配置**（遵守红线）；
- 结论：用户态代理 + 浏览器 proxy 参数 + 会话 route guard 三层防护在本进程范围内有效
  （未授权目标 0 请求为服务端实测），但**不能**据此宣称「绕过代理也不可达」的宿主级网络隔离；
- 若应用进程内有其他组件（非浏览器 socket、原生扩展、子进程）绕过 Chromium 网络栈，
  仍不受本代理约束——该边界已在 `egress_proxy.py` 模块文档披露；
- 解除条件：在具备容器网络隔离或防火墙策略授权的环境复测 WP3 场景（延续 RUN-009 建议）。

## 6. 状态

- 代理接入正式内核：**IMPLEMENTED / VERIFIED**；
- 宿主级隔离：**BLOCKED**（无权限，未伪装通过）；
- 后续：WP8 完整业务闭环将在认证开启 + egress 强制状态下执行正式 Run 并归档代理审计。
