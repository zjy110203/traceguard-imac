# RUN-009 WP3：网络安全与浏览器执行隔离（受控出站代理）

> 状态时间：2026-11-24（CST）。本文件只描述**本机用户态受控出口代理**的实际能力与
> 已披露边界。不把「URL 检查通过」写成「网络安全」，不把 BLOCKED 写成通过。
> 本 WP 未修改任何既有源码，未 `git commit` / `push` / `reset` / `clean`，未读取 `.env`。

## 0. 结论摘要

| 项目 | 状态 | 依据 |
|---|---|---|
| HTTP/HTTPS/CONNECT 目标按 `TargetPolicy` 在出站前校验 | **VERIFIED** | 17/17 检查通过；27 条代理审计；证据见 `docs/assets/run009/wp3/egress-proxy-evidence.json` |
| 未授权服务器实际收到的请求数 = 0 | **VERIFIED** | 三个未授权记录器（含 IPv6、DNS 翻转落点）合计 **0** |
| 每跳重定向（明文 HTTP）复核 | **VERIFIED** | 多跳链 `/chain1 → /chain2 → 未授权` 中未授权新增命中 0 |
| DNS pinning + 解析结果变化（rebinding）拒绝 | **PARTIAL** | 注入 resolver 的确定性场景 VERIFIED；真实系统 DNS 不受控，见 BLOCKED |
| WebSocket：`ws://` 升级请求校验 | **VERIFIED** | 授权回显 `msg:echo:ping`；未授权 WS 失败且未授权服务器 0 命中 |
| WebSocket：`wss://` / HTTPS 内部内容 | **PARTIAL** | 只校验 CONNECT 的 host:port，不解密 TLS |
| 私网 / 链路本地 / 非法协议 / 内联凭据拒绝 | **VERIFIED** | 全部返回 403，且审计中 `connected=false` |
| 不泄露认证头 | **VERIFIED** | `Proxy-Authorization` 未转发到目标；审计无任何头值/令牌 |
| 宿主级出口封锁（绕过代理也不可达） | **BLOCKED** | 无防火墙/容器网络策略权限，未验证，见 §7 |
| 浏览器内核 DNS 与 TLS 中间人内容检查 | **BLOCKED** | 用户态代理无法覆盖；刻意不做 MITM，见 §7 |
| 接入 `BrowserSession` | **NOT_IMPLEMENTED** | 按任务约束未改该文件，仅给出最小建议 §8 |

## 1. 对现有边界的复核（未修改这些文件）

### 1.1 `packages/core/traceguard_core/security/target_policy.py`

- `TargetPolicy.check()`（:213-271）：scheme 只允许 `http/https`；拒绝 URL 内联
  `user:pass@`；按 `Origin(scheme, host, port)` 精确匹配白名单；对未授权的回环/私网
  字面量给出 `PRIVATE_NETWORK_BLOCKED` / `LOCALHOST_NOT_AUTHORIZED`；未授权主机解析到
  内网时给出 `DNS_RESOLVES_TO_PRIVATE_NETWORK`。
- `pin_dns()` / `_rebinding_check()`（:202-283）：构造时钉住授权主机 A/AAAA 结果，
  后续解析与钉住结果**无交集**即 `DNS_REBINDING_DETECTED`。
- `resolve()` / `check_redirect_chain()` / `check_landed_url()`（:285-318）：协议相对 URL
  显式拒绝；重定向链逐跳校验；落地复核默认带 DNS 校验。
- 文件头注释（:15-19）已声明边界：单机身份不可强认证；用户态无法接管系统 DNS，
  「钉住 + 复核」只能发现**已发生变化的**解析，不能阻止浏览器内核缓存导致的短时不
  一致，生产应改用受控出站代理。

### 1.2 `packages/execution/traceguard_execution/browser/session.py`

- `_guard_route()`（:100-139）：请求发出前 `route.abort`；未授权直接阻断并记录。
- `_guard_document_navigation()`（:152-214）：对文档导航 `route.fetch(max_redirects=0)`
  自己取 3xx、检查 `Location`；未授权时合成 403，授权时原样返回。
- `_guard_websockets()`（:227-266）：`route_web_socket` 单独拦截 `ws/wss`。
- 已披露边界：顶层 302 的**第一个出站请求已经到达授权服务器**（只是不再自动跟到未
  授权目标）；DNS rebinding 短窗口；浏览器内核 DNS 不受应用控制。

### 1.3 RUN-008 的要求

`docs/RUN-008-SECURITY-AUDIT.md`:36-37,55 明确：顶层 302 不能表述为「请求未发出」；
生产应叠加受控出站代理；若宿主无法提供网络控制必须标 BLOCKED，不能伪造通过。
本 WP 据此实现代理，并如实保留 BLOCKED 项。

## 2. 代理实现

文件：`packages/core/traceguard_core/security/egress_proxy.py`（新建，仅标准库 + 既有策略）。

- **监听**：`EgressProxy.start()` 用 `asyncio.start_server` 绑定 `127.0.0.1:0`
  （临时端口，避开 8000/8011/8020/8042/8050/5173/5175）。
- **明文 HTTP / `ws://`**：浏览器发绝对形式请求；代理解析 scheme/host/port/path，
  调用 `TargetPolicy.check(url, resolve_dns=True)` 校验白名单与 rebinding，再解析
  **全部** A/AAAA，逐 IP 校验合法性。
- **HTTPS / `wss://`**：浏览器发 `CONNECT host:port`；代理把 authority 映射为
  `https://host:port` 走同一策略，校验通过后连接已校验 IP，回 `200 Connection
  Established`，之后只转发原始字节（不解密）。
- **DNS pinning 落在连接上**：先解析 → 校验 → 用**该 IP** 建连，连接阶段不再解析。
  即使 DNS 在校验与 connect 之间翻转，连接仍指向被校验的地址，关闭 TOCTOU 窗口。
- **IP 合法性**：私网 / 回环 / 链路本地 / 组播 / 保留地址默认拒绝；仅当该 IP 字面量
  本身在白名单，或它是授权主机在策略构造时钉住的精确答案时放行（后者仍受 rebinding
  检查约束）。混合 A/AAAA 应答中只要有一个非法 IP 即整批拒绝。
- **逐跳重定向**：明文 HTTP 的每一跳都是新连接/新请求，必然重新解析与校验。
- **不泄露认证头**：转发前剥离 `Proxy-Authorization` / `Proxy-Connection` 等
  `Proxy-*` 逐跳头；审计只记录方法、host、port、**不含 query 的 path**、解析结果与
  判定，绝不记录任何头值。端到端 `Authorization` 会透传给目标（功能所需）但从不落盘。

### 2.1 实测发现并修复的两个真实缺口

这两项不是理论推演，而是第一版代理跑真实 Chromium 时由证据暴露出来的：

1. **keep-alive 后续请求绕过校验（严重）**：原实现只解析每条 TCP 连接上的**第一个**
   请求，随后把客户端剩余字节原样转发。证据显示 `fetch('/ok')` 与多跳链 `/chain1`
   在目标侧表现为带 `proxy-connection` 头的绝对形式请求，且代理审计中**没有**对应
   记录 —— 即第二条请求完全绕过了策略。修复：明文 HTTP 强制 `Connection: close`，
   且非 WebSocket 升级连接不再做 client→upstream 盲目转发；只有 `ws://` 升级与
   CONNECT 才保留双向原始转发。修复后审计从 19 条增至 27 条，每跳均可追溯。
2. **IPv6 URL 重建错误**：`host="::1"` 直接拼进 `http://::1:port/` 导致
   `MALFORMED_URL`，授权 IPv6 目标被误拒。修复：`_format_host()` 对 IPv6 字面量加
   `[]`。修复后授权 `http://[::1]:port` 返回 200。

这两项说明：**只做 URL 字面量检查、不验证真实转发路径，会得出错误的「安全」结论**。

## 3. 验证方法

脚本：`scripts/egress_proxy_run009.py`。测试：`tests/e2e/test_egress_proxy_run009.py`。

- 本地 HTTP 服务器（端口全为 OS 随机分配）、WebSocket 回显服务、代理同机运行。
- Playwright Chromium `1.65.2` headless，配置
  `proxy={"server": "...", "bypass": "<-loopback>"}`。
  CDP `Browser.getBrowserCommandLine` 采集到的真实参数（原始证据）：
  ```
  --proxy-server=http://127.0.0.1:52690
  --proxy-bypass-list=<-loopback>
  ```
  `<-loopback>` 取消 Chromium 对 `127.0.0.1` / `::1` 的隐式代理绕过；审计中确实出现
  loopback 目标，且未授权目标 0 命中，构成功能证据。
- 每个未授权目标由独立记录器统计**实际收到的请求数**（不是「发起次数」）。

复现命令：

```bash
.venv/bin/python scripts/egress_proxy_run009.py \
  --out docs/assets/run009/wp3/egress-proxy-evidence.json
.venv/bin/python -m pytest tests/e2e/test_egress_proxy_run009.py -q
.venv/bin/python -m pytest tests/unit/test_target_policy.py \
  tests/unit/test_target_policy_dns.py tests/e2e/test_target_policy_browser.py \
  tests/e2e/test_egress_proxy_run009.py -q
```

## 4. 验证结果（17/17 检查通过）

| 场景 | 结果 | 关键观测 |
|---|---|---|
| 合法目标 | VERIFIED | 导航 200，`fetch('/ok')` 200，代理审计 ALLOW |
| 未授权目标直接导航 | VERIFIED | 代理返回 403，未授权服务器命中 0 |
| 跨域 fetch | VERIFIED | 未授权服务器命中 0 |
| 顶层 302 | VERIFIED | 302 的第二跳经代理被拒，未授权新增命中 0 |
| 多跳重定向 | VERIFIED | `chain_hits=['/redirect_ext','/chain1','/chain2']`，未授权新增命中 0 |
| DNS 变化 | VERIFIED（注入 resolver） | 翻转前 200（`dns_v4_hits=['/before-flip']`），翻转后 403 `DNS_REBINDING_DETECTED`，翻转落点命中 0 |
| WebSocket 授权 | VERIFIED | 回显 `msg:echo:ping`（经代理 CONNECT 隧道） |
| WebSocket 未授权 | VERIFIED | 升级失败，未授权服务器命中 0 |
| IPv4/IPv6 | VERIFIED | 授权 `[::1]` 200；未授权 `[::1]` 403 且命中 0 |
| 私网访问 | VERIFIED | `10.0.0.5`、`169.254.169.254`、`evil.example→127.0.0.1` 均 403，未建连 |
| 非法协议 | VERIFIED | `ftp` / `file` / `gopher` → `SCHEME_NOT_ALLOWED`；`user:pass@` → `URL_USERINFO_FORBIDDEN`；CONNECT 端口不符 → `PORT_NOT_ALLOWED` |
| 认证头泄露 | VERIFIED | 目标侧头名仅 `authorization, connection, cookie, host`（**无** `proxy-authorization`）；审计 27 条无任何令牌 |

### 未授权请求计数

| 未授权记录器 | 实际收到请求 |
|---|---|
| `forbidden`（HTTP/WS/302/多跳/fetch 共用） | **0** |
| `ipv6-forbidden` | **0** |
| `dns-flip-target`（DNS 变化后的 IPv6 落点） | **0** |
| **合计** | **0** |

计数来自服务端记录器的真实命中列表，证据中保留完整 `hits` 与 27 条代理审计原文。

## 5. 证据

- 原始证据：`docs/assets/run009/wp3/egress-proxy-evidence.json`
  （含各场景导航状态、未授权命中列表、代理审计、CDP 命令行、检查项）。
- 脚本与测试：`scripts/egress_proxy_run009.py`、`tests/e2e/test_egress_proxy_run009.py`。
- 测试运行结果：本文件 8/8 通过；连同既有策略/E2E 回归 48/48 通过。
- 端口：全部由 OS 分配（证据 `ports` 段留有当次实际值），未使用被禁端口。

## 6. 为什么不能只凭 URL 检查宣称安全

- 第一版实现「URL 校验正确」但 keep-alive 后续请求原样转发，真实 Chromium 下形成
  完全绕过；是**目标侧命中与审计条数**的对账发现它，而不是 URL 断言。
- 顶层 302 的第一个出站请求本来就到达了授权服务器；若没有代理的第二跳校验，
  Chromium 会自动把请求发到未授权地址。
- DNS rebinding 在 `check()` 与 `connect()` 之间仍有窗口；只有把解析结果**用于建连**
  才能关闭它。
- 因此本报告以「未授权服务器实际收到 0 个请求 + 代理审计逐请求可追溯」为通过标准，
  不以「策略函数返回 allowed=False」为通过标准。

## 7. BLOCKED / 已披露边界（不得删除或弱化）

1. **宿主级网络控制缺失 — BLOCKED**。当前环境没有防火墙 / iptables / 容器网络策略
   权限，未验证「即使应用绕过代理，目标也不可达」。代理只在浏览器被正确配置且流量
   经过它时有效。直连 socket、扩展、非浏览器组件或代理配置丢失都不受约束。若宿主
   无法提供此类网络控制，整体网络安全状态必须标 **BLOCKED**，不能因本代理通过而
   宣称通过。
2. **浏览器内核 DNS — BLOCKED（用户态不可控）**。配置代理后解析由代理承担，但浏览器
   自身缓存、Happy Eyeballs 及非代理路径无法从应用侧接管。`TargetPolicy` 的 pin 是
   进程内快照；代理重启需重新 pin。
3. **TLS 内容级检查 — BLOCKED（刻意不实现）**。`CONNECT` 是字节隧道，代理看不到
   HTTPS 内的路径、Header、方法与跳转链，仅强制 host:port 白名单与 IP 校验。实现内容
   检查需要 TLS 中间人，会破坏证书信任模型，本 WP 不做，故 HTTPS/WSS 只能标 PARTIAL。
4. **外网/真实域名验证 — PARTIAL**。场景全部使用本地服务器与注入 resolver；未对真实
   公网目标做连通性验证（避免把外部依赖引入结论）。DNS 变化的确定性来自注入
   resolver，不等同于真实攻击者控制 DNS 的行为。
5. **`BrowserSession` 集成 — NOT_IMPLEMENTED**。按任务约束未修改该文件；当前浏览器
   默认仍走原有 route 守卫，本代理尚未成为生产默认路径。

## 8. 把代理接入 `BrowserSession` 的最小集成建议（未实施）

目标：不动 `session.py` 的守卫语义，只增加「可选把出口交给代理」的开关。

1. **配置层**：在 `BrowserConfig` 增加可选字段，例如 `egress_proxy_url: str | None`
   与 `egress_policy: TargetPolicy | None`。默认值 `None` 保持现有行为不变。
2. **Manager 层**：`BrowserManager.new_context()` 构造 context 时，若配置了代理则传入
   `proxy={"server": url, "bypass": "<-loopback>"}`；否则沿用现状。不要复用生产网络
   环境的系统代理设置，避免绕过。
3. **Session 层**：`BrowserSession.__init__` 接收可选 `EgressProxy` 实例（或由外部进程
   启动后只传 URL）。**保留**现有 `_guard_route` / `_guard_document_navigation` /
   `_guard_websockets` 作为纵深防御的第一层：浏览器层阻断减少代理压力，代理层阻断
   覆盖浏览器守卫被绕过的路径。
4. **策略一致性**：把同一个 `TargetPolicy` 对象传给 `BrowserSession` 与 `EgressProxy`
   （共享 pin 与 resolver），避免两层白名单漂移；代理重启后重新 `pin_dns()`。
5. **生命周期**：代理随 Run 启动/停止；审计 JSON 作为证据附件落盘到该 Run 的证据目录。
6. **失败姿态**：代理不可用时**显式失败**（拒绝运行并标 BLOCKED），不要静默退回直连
   —— 否则会重构出「看起来通过、实际绕过代理」的假阳性。
7. **回归门槛**：接入后重跑本 WP 的 8 个测试，并新增一条「关闭代理时必须无法启动
   被测会话」的负向测试。

## 9. 范围声明

- 本 WP 只新增下列文件，未修改既有源码或文档：
  `packages/core/traceguard_core/security/egress_proxy.py`、
  `scripts/egress_proxy_run009.py`、
  `tests/e2e/test_egress_proxy_run009.py`、
  `docs/assets/run009/wp3/**`、
  `docs/RUN-009-WP3-NETWORK-SECURITY.md`。
- 未执行 `git commit` / `push` / `reset` / `clean`；未读取或输出 `.env` 中的凭据。
- 未使用被禁端口 8000/8011/8020/8042/8050/5173/5175。
