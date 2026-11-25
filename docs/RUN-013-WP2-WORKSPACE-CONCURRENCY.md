# RUN-013 WP2：多 Workspace 并发一致性与安全深度验证

> 日期：2026-11-25（CST）｜ 证据：`docs/assets/run013/wp2/ui/workspace-concurrency-acceptance.json`
> 测试：`tests/integration/test_workspace_concurrency_run013.py`（7 passed）
> 浏览器脚本：`scripts/workspace_concurrency_acceptance_run013.py`（10 passed / 0 failed）

---

## 1. 验收中发现并修复的真实缺陷

### 1.1 `/events/stream` 完全未鉴权、未按 Workspace 过滤（严重）

**现象**：SSE 路由定义在 `/events/stream`，不在 `/api/v1/**` 下；
`auth_middleware` 只覆盖 `/api/v1/`，因此：

- 匿名用户可直接建立事件流；
- 任意登录用户可通过 `workspace_id=其他空间` 订阅跨空间事件（或省略参数读取全库事件）；
- `Last-Event-ID` 续传同样不受 Workspace 授权约束。

**修复**：

1. `events_stream` 内显式解析会话 Cookie（认证开启时），未认证返回 401；
2. 调用统一 `scope_filter()`：指定无权 Workspace → 403 `WORKSPACE_FORBIDDEN`；
   未指定时使用 `visible_workspaces()`（platform-admin / `*` 才是不受限）；
3. `PersistentEventStore.list_events_after()` 新增 `workspace_ids` 参数并下推 SQL `IN`，
   避免“先取全库再内存过滤”造成游标饥饿与越权数据进入内存；
4. 前端 `events.ts` 仍按当前 Workspace 订阅，切换空间时视图重挂载关闭旧连接。

**回归**：未登录 401；跨空间 403；无参数只返回授权并集；伪造游标不能读取其他空间事件。

### 1.2 前端 Workspace 竞态与身份残留（中）

| 问题 | 修复 |
|---|---|
| 快速切换 A/B 时，较早的 `loadWorkspaces()` 响应可能后到并覆盖新选择 | 增加 `loadSeq` / `switchSeq` 序号，后发请求优先，过期响应丢弃 |
| `workspaceParams(extra)` 允许 `extra.workspace_id` 覆盖当前上下文 | 显式删除 `workspace_id`，调用方不能覆盖 |
| 换用户登录且 workspace_id 相同（如 `default`）时 generation 不变，旧用户数据残留在已挂载视图 | 身份变化先 `resetWorkspaceContext()`（清空 + generation+1），再拉取新授权 |
| 401 会话失效后仅跳登录页，旧 Workspace 数据仍驻留 | `onUnauthorized` 同时 `resetWorkspaceContext()` |
| 多标签页各自停留在不同 Workspace | 监听 `storage` 事件，重新向服务端验证后切换 |
| 加载失败清空 `currentId` 时不增加 generation | 失败回落也递增 generation，强制视图重挂载 |

### 1.3 并发测试暴露的会话隔离问题（测试侧）

带共享 CookieJar 的并发请求会互相覆盖会话；验收脚本改为显式携带独立 Cookie 并清空 jar，
确保“并发”测的是服务端授权，而不是测试客户端串会话。

## 2. 真实浏览器验收（10/10）

| 检查 | 结果 |
|---|---|
| alice 默认空间 ws-a | PASS |
| 只显示 ws-a 数据，不出现 ws-b | PASS |
| localStorage 篡改为 ws-b → 回落 ws-a，无 ws-b 数据 | PASS |
| 页面内 6 个并发越权 `workspace_id=ws-b` 请求全部 403 | PASS |
| 授权撤销后，已打开页面下一次请求立即 403 | PASS |
| multi 用户快速 A/B 切换 12 次后无回填/混显 | PASS |
| 多标签页 storage 同步 | PASS |
| 390px 深色模式无横向溢出 | PASS |

截图：`docs/assets/run013/wp2/ui/alice-ws-a.png`、`multi-390-dark.png`。

## 3. API 级并发与授权测试（7 passed）

`tests/integration/test_workspace_concurrency_run013.py`：

- SSE 未认证 401；
- 跨空间 SSE 403；
- 交错写入 ws-a/ws-b/ws-c 事件后，alice 只能收到 ws-a（前 N 条不会混入越权空间）；
- 伪造 ws-b 事件游标后，alice 仍只能收到游标之后的 ws-a 事件；
- multi 用户收到 ws-a ∪ ws-b，admin 收到全部；
- 8 线程并发 alice/bob 读取，各自只返回本空间；
- 授权撤销后列表/SSE/事件详情全部 403；
- 跨空间事件详情 403。

## 4. 覆盖边界

- 本工作包验证的是**服务端权威授权**与真实浏览器行为；localStorage 仅作偏好，不授予权限。
- Workspace 删除后的回落由 `loadWorkspaces()` 的“首选不存在则取第一个可用空间”保证，
  未单独构造删除场景（受限于无 Workspace 删除 API，避免直接改库伪造业务操作）。
- SSE 事件流的“授权撤销时服务端主动断开旧连接”未实现：当前为下一次读取/重连时拒绝；
  已打开的长连接可能继续收到撤销前已授权的事件。该限制已记录，未标为 VERIFIED。
