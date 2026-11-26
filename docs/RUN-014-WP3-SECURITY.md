# RUN-014 WP3：SSE 活跃连接撤权与全局接口权限

> 分支：`workbuddy/run014-product-closure`
> 结论：SSE 活跃连接会**持续重新核验会话与 Workspace 授权**，失效即停止推送并关闭；
> 全局模型摘要纳入 `system.observe`；统计不再因 `limit` 静默失真。

---

## 1. 审查发现的缺陷

| # | 位置 | 缺陷 | 严重度 |
|---|---|---|---|
| S1 | `/events/stream` | 连接建立时算一次 Workspace 范围，之后**一直沿用**；撤权/禁用/会话撤销后仍然收事件 | P0（授权撤销不生效） |
| S2 | `/system/llm/summary` | 全局摘要（跨 Workspace 的调用/成本/顾问统计）未纳入 `GLOBAL_OBSERVATION_PATHS`，普通 read 角色可读 | P1 |
| S3 | `/system/browser` | 可触发真实浏览器自检（资源消耗 + 系统元数据），任何 read 角色可调 | P2 |
| S4 | `llm/summary` 统计 | 用 `docs.list(limit=1000/500)` 遍历，文档超限即**静默截断**却仍标称全量 | P0（数据可信度） |
| S5 | `/artifacts/{id}/download` | 授权用 `get_meta()`、内容用 `read()`（两个事务）；元数据缺失时完全跳过对象级授权，仅靠 404 兜底 | P1 |
| S6 | `/queue/stats` | 实际已按授权空间过滤，但响应不声明范围；RUN-013 WP6 报告把它写成"全局队列" | P1（文档与代码不一致） |
| S7 | `/probes/audit` | 进程内 `_audit` 列表被当作"审计"，未声明其非持久化边界 | P2 |

## 2. 修复方案

### 2.1 SSE 活跃连接授权重新核验

`/events/stream` 新增 `auth_recheck_seconds`（默认 2s，下限 0.1s）。每次轮询前：

1. `AuthManager.resolve(session_token)` 重新解析会话——`get_session()` 已同时校验
   会话撤销、过期、用户 `active`；
2. 解析失败 → `auth.revoked`（`SESSION_INVALID`）+ 关闭连接；
3. `user_id` 变化（会话被重绑）→ `SESSION_REBOUND` + 关闭；
4. 显式请求的 Workspace 不再被 `workspace_allowed()` 允许 → `WORKSPACE_REVOKED` + 关闭；
5. 其他授权变更（新增/移除别的 Workspace）→ 就地更新过滤集合，不必重连。

关键实现细节：`requested_ws`（用户请求的空间）与 `stream_workspaces`（可见并集）分开记录——
旧写法 `stream_workspace = workspace_id if allowed is None else None` 对普通用户永远是 `None`，
撤权判定根本不会触发（本轮实测踩到的真实缺陷）。

`auth.revoked` 是独立控制帧（`id: notice:auth.revoked`），与业务事件区分，客户端可据此停止重连。

### 2.2 全局接口 RBAC 矩阵（逐项核实）

| 接口 | 数据性质 | 处理 | 普通角色 |
|---|---|---|---|
| `/system/health` | 进程/DB 健康 + **按授权空间过滤的计数** | 保留 read，计数走 `scope_filter` | 可读（仅自己空间） |
| `/system/metrics` | 业务聚合 | 保留 read，显式 workspace 403 校验，未指定时只聚合授权空间 | 可读（仅自己空间） |
| `/system/llm/summary` | **全局**模型调用/成本/顾问统计 | 加入 `GLOBAL_OBSERVATION_PATHS` → `system.observe` | **403** |
| `/system/browser` | 系统级能力探测（可启动真实浏览器） | 加入 `GLOBAL_OBSERVATION_PATHS` | **403** |
| `/system/config` | 静态配置（方言/路径/超时，不含密钥） | 保留 read（显式决定，见 §5） | 可读 |
| `/queue/stats` | 队列状态 | 保留 read，**按授权空间 SQL 聚合**，新增 `scope` 声明 | 可读（仅自己空间） |
| `/events/conflicts` | 全局事件冲突 | `system.observe` | 403 |
| `/probes/audit` | 全局工具调用审计轨迹 | `system.observe` + 响应头声明非持久化 | 403 |
| `/inbox/stats` | 全局消费汇总 | `system.observe` | 403 |

前端同步：模型中心 / 系统页对 403 显示明确权限提示（而不是整页失败）。

### 2.3 统计不再静默截断

`DocStore.iter_all(kind, page_size=500)` + `DocStore.count()`：以主键键集分页完整读取，
`llm/summary` 改为一次遍历聚合，并返回：

```json
"coverage": {
  "method": "keyset_pagination (DocStore.iter_all)",
  "documents_scanned": {"exploration": 620, "diagnosis": 0, "probe_plan": 0, "repair": 0},
  "truncated": false,
  "limits_used": null,
  "previous_behavior": "旧实现 limit=1000/500，超出即静默截断"
}
```

620 条探索文档的实测验证：`rule_explorations` / `fallback_calls` 均 ≥620，`truncated=false`。

### 2.4 Artifact 授权单源化

`download_artifact` 只调用一次 `artifacts.read()`，用返回的权威 meta 做 Workspace 校验：
不存在"先授权 A 后读取 B"的窗口；元数据缺失 → 404（不返回内容）。

### 2.5 Probe Audit 边界

`/probes/audit` 保留返回结构（兼容既有消费方），新增响应头：

```
X-TraceGuard-Audit-Persistent: false
X-TraceGuard-Audit-Scope: in-memory-process
X-TraceGuard-Audit-Formal-Source: probe_execution
```

正式的探针执行事实是持久化的 `probe_execution` 文档（workspace / run / 证据 / 预算，重启可回读）；
`ProbeEngine._audit` 只是当前进程内的准入/拒绝轨迹。

## 3. 测试

`tests/integration/test_sse_revocation_and_global_rbac_run014.py`：**13 passed**

| 验收项 | 用例 |
|---|---|
| Alice 建连 → 撤权 → 不再收到事件 → 收到 `WORKSPACE_REVOKED` 且连接关闭 | `test_sse_workspace_revocation_stops_active_stream` |
| 重新授权后可正常订阅 | 同上（末尾重新登录 + 订阅到真实事件） |
| 会话撤销（登出）使活跃连接失效 | `test_sse_session_revocation_logout` |
| 用户被禁用使活跃连接失效 | `test_sse_user_disabled_closes_stream` |
| viewer / qa-lead 读全局模型摘要 403（含 `system.observe` 提示） | `test_global_model_summary_requires_system_observe` |
| platform-admin 200 且 coverage 全量声明 | `test_platform_admin_can_read_global_model_summary` |
| 浏览器自检 403 | `test_browser_probe_requires_admin` |
| 620 文档不被截断 | `test_llm_summary_counts_are_complete_beyond_page` |
| 键集分页无丢失（page_size=4 / 11 条） | `test_doc_iter_all_paginates_without_loss` |
| queue/stats 按授权空间过滤（普通 vs 管理员对照） | `test_queue_stats_are_workspace_scoped` |
| Artifact 元数据缺失 → 404 | `test_artifact_without_metadata_is_not_served` |
| Artifact 跨 Workspace → 403，本人 200 | `test_artifact_cross_workspace_denied` |
| Probe Audit 非持久化声明 | `test_probe_audit_declares_in_memory_scope` |

全量回归：**804 passed, 16 skipped**（skip 全部为未配置 `TG_TEST_POSTGRES_URL` 的 PG 专项）。

## 4. RUN-013 WP6 结论修订

原文："`/queue/stats` 为全局队列状态，普通登录用户可读（不含业务内容，属运维观测）"。

**修订**：`/queue/stats` 自 RUN-012 WP3 起就已使用 `scope_filter` + `workspace_ids` 做 SQL 分组计数，
普通用户只能看到授权 Workspace 的并集，**不是全局队列**。本轮补充：
- 响应显式返回 `scope: {mode, global, authorized_workspaces}`；
- 新增普通用户/管理员对照测试（`test_queue_stats_are_workspace_scoped`）。
RUN-013 的剩余风险条目以本节为准，不再沿用旧描述。

## 5. 保留决定与风险

1. `/system/config` 仍为 read：只返回方言、端口、路径、超时等运行参数，不含密钥/凭据；
   前端"用例"页依赖它拿 `teamflow_base_url`。若要收紧，需同步调整页面提示（NEEDS_DECISION）。
2. SSE 重新核验默认 2s：意味着撤权最多 2s 内生效；如需即时，可传更小的 `auth_recheck_seconds`。
3. 认证关闭（dev）时不存在会话可撤销，`auth_audit=False`，保持既有本地开发体验。
4. `/system/browser` 的 admin 200 正向用例未纳入自动测试（会真实启动浏览器，成本高），
   由 WP4 真实浏览器验收覆盖。
