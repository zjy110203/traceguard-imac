# RUN-006 实施报告（WP1：核心正确性与安全性）

- 时间：2026-11-23
- 分支：`workbuddy/traceguard-autonomous`
- 起点：`dadb4ae` / HEAD `78a09f6`（285 passed / 5 skipped）
- 本轮终点：**367 passed / 5 skipped / 0 failed**（新增 82 个采集用例），`ruff check` 全绿
- 证据目录：`docs/assets/run006/`

本报告只覆盖**实际完成并验证**的部分。未完成的 WP2–WP6 在各自章节明确标为 PARTIAL/BLOCKED。

---

## 1. WP1-01 TestCase 与 Oracle 正式审核闭环

### 问题（审计前）

- 无 Oracle 绑定接口，`AssertionSpec` 只有 `oracle_id`，执行时 `pipeline._load_oracles()` 恒取**最新版本**；
- 审核只做字符串精确匹配（`reviewer.lower() in {"agent","llm","system"}`），`agent:gpt` 可直接通过；
- 审核不校验必需断言是否绑定了合法 Oracle；跨 Workspace 的 Oracle 可被使用；
- 并发审核无互斥，可能双写审核历史。

### 实施

| 变更 | 位置 | 说明 |
|---|---|---|
| 契约扩展 | `packages/contracts/traceguard_contracts/models.py` | `AssertionSpec.oracle_version: int \| None`（结构扩展，不改 P03 裁决规则） |
| 审核修订号 | `models.TestCaseVersionRow.review_revision` + 迁移 v7 | 并发审核 CAS 令牌；迁移幂等（识别列是否存在） |
| 主体类型校验 | `catalog.require_human_actor()` | 按命名空间首段判断：`agent/agents/ai/assistant/bot/llm/model/robot/service/system/worker` 全部拒绝；空主体拒绝 |
| Oracle 绑定 API | `catalog.bind_oracles()` + `POST /api/v1/testcases/{id}/versions/{v}/oracle-bindings` | 校验断言存在、Oracle 存在、已审批、审批人是人类、同 Workspace；写入 `oracle_bindings` 与 `binding_history` |
| 审核门禁 | `catalog._oracle_gate()` | APPROVED 前逐条检查 required assertion；缺 Oracle / 未审批 / 跨 Workspace / 找不到 → 412 并列出全部问题 |
| 版本固定 | `catalog._pin_oracle_versions()` | 审核通过时把 `oracle_version` 写进断言并标记 `oracle_binding_frozen=true` |
| 执行端 | `pipeline._load_oracles()` | 有 pinned 版本则按版本加载；**pinned 版本不存在时不回退到最新版本** |
| 文档字段兼容 | `pipeline._load_testcase()` | 按模型字段过滤（`extra=forbid`），避免审核元数据污染契约 |
| 数据自洽 | `seed.py` | 新增 `oracle_tf_biz_created` / `oracle_tf_row_render2`，消除跨断言隐式复用 |

### 明确的身份验证局限（不宣称 RBAC）

单机开发环境没有登录、没有 token、没有服务端会话。`reviewer` / `approved_by` 仍是**请求体声明**，
本轮做的是"主体类型门禁"（拒绝非人类主体、拒绝空主体），**不是身份认证**。
生产级 RBAC 需要 P08 批准的角色矩阵 + 认证层，属于 NEEDS_DECISION（见 `RUN-006-DECISION-REQUESTS.md`）。

### 验证

- `tests/integration/test_oracle_binding_gate.py`（26 项，含参数化）：无 Oracle、不存在、未审批、
  跨 Workspace、未知断言、10 种非人类主体、4 种合法人类主体、固定版本复现、已批准不可重绑、并发审核、
  stale revision 确定性冲突。
- 真实服务实测（`.traceguard/run006/` 隔离库）：
  - 无绑定审核 → `412 ORACLE_BINDING_INCOMPLETE: MISSING_ORACLE: a_live`
  - `agent:gpt` 审核 → `403 REVIEWER_MUST_BE_HUMAN`
  - 绑定 + 审核 → `200`，版本固定 `oracle_version=1`、`oracle_binding_frozen=true`

---

## 2. WP1-02 Active Diagnosis 真正动态闭环

### 问题（审计前）

`plan_and_probe()` 能执行探针，但探针证据**从不回灌**：假设状态、结论、Disposition 全部不变；
`conflicts_unresolved` 硬编码 `False`；失败探针与目标故障没有区分。

### 实施

| 变更 | 位置 | 说明 |
|---|---|---|
| 证据回灌规则 | 新增 `packages/evidence/.../probe/feedback.py` | 确定性映射表：`http_state` / `network_trace` / `dom_snapshot` / `backend_log` → 支持/反驳/排除；探针失败不产生任何证据 |
| 冲突与排除语义 | `diagnosis/engine.py` | 新增 `EXCLUDED` 判定、`unresolved_conflicts` 计算（同一假设同时有 TRUSTED 支持与反驳）；冲突无确认时 Disposition=MANUAL_REVIEW |
| 重诊断 | `DiagnosisService.reassess()` | 读回诊断 → 合并探针信号 → 重新裁决 → 更新 outcome/root_causes/disposition/conflicts → 写 `evaluation_history` |
| Disposition 刷新 | `_refresh_disposition()` | 保留历史条目，不静默改写旧记录 |
| 循环改造 | `ProbeService.plan_and_probe()` | 每轮先判 CONFIRMED / 冲突 → 执行探针 → 成功即回灌并重算 outcome → 重算 Gap → 继续/停止；失败/超时不回灌 |
| 停止原因 | `_blocked_stop()` | 权限 / 预算耗尽 / 预算不可用 / 其他拒绝分别映射 `STOP_PERMISSION_INVALID` / `STOP_BUDGET_EXHAUSTED` / `STOP_BUDGET_UNAVAILABLE` / `STOP_PROBE_BLOCKED` |
| 证据图 | `graph_service.build_for_run()` | 新增 `ProbeExecution` 节点、`derived_from`（证据→探针）、`evaluates`（探针→假设）边；探针结束后自动刷新图 |
| LLM 诚实标注 | `_apply_llm_advice()` | `effect=ranking_only`、`params_used_in_execution=false`、`applied_to_execution=false` |
| LLM 确认漏洞 | `engine.diagnose()` | 前缀判断 + **状态计算后**强制降级（`llm:*` 永不 CONFIRMED） |

### 真实环境证据（隔离库，真实 HTTP 探针）

| 场景 | 结果 |
|---|---|
| TF-DB-001 真实故障 Run（`run_1795417175537b8fd57ae15bf`，FAILED / playwright） | 诊断 `CONFIRMED PERSISTENCE` → probe-plan **STOP_CONFIRMED，0 探针**（不浪费预算） |
| INCONCLUSIVE 诊断 + 真实 `http_state` 探针 | 探针真实返回 `count=1` → PERSISTENCE 假设 `CONTRADICTED` 且 `excluded_by` 1 条；`evaluation_history` 2 条 |
| Evidence Graph | 60 节点 / 81 边，含 2 个 `ProbeExecution` 节点、1 条 `contradicts` 边 |

原始数据：`docs/assets/run006/probe-plan-confirmed-no-probe.json`、
`probe-plan-real-feedback.json`、`diagnosis-after-probe-feedback.json`、
`evidence-graph-with-probe-nodes.json`。

> **诚实边界**：真实故障 Run 的规则诊断通常已 CONFIRMED，探针没有介入空间；上述 INCONCLUSIVE 案例的
> 诊断文档是为验证闭环语义构造的（探针与 Run 均为真实）。"真实浏览器故障场景下探针把 INCONCLUSIVE
> 变成 CONFIRMED"的完整实例本轮**未产出**，标记 PARTIAL。

---

## 3. WP1-03 ProbeBudget 持久化与跨进程安全

| 变更 | 说明 |
|---|---|
| 读取失败 fail-closed | 新增 `BudgetUnavailable`（`DEPENDENCY_ERROR`）；loader 异常 → 抛错，**不再新建预算继续执行** |
| 写入失败 fail-closed | 扣减采用"先写库后计入"：持久化失败 → 回滚内存计数 → 探针 `BLOCKED / PROBE_BUDGET_UNAVAILABLE` |
| 跨进程原子 | `ProbeBudgetRegistry.reserve()` 用 DocStore 版本 CAS；冲突 → 重新加载最新值 → 重试（默认 5 次），收敛失败即阻断 |
| 预算文档版本 | `ProbeBudget.doc_version`；`_load_budget_doc` 回填行版本，`_save_budget_doc` 带 `version=` 写入 |
| 关闭语义 | `close()` 持久化失败不回滚关闭状态，但记 warning；新进程恢复仍是 CLOSED，不能靠重新请求绕过 |
| provisional 保留 | P06 数值（50 / 100 / 60s）与 `note` 原样保留，不因 CAS 往返丢失 |

验证：`tests/unit/test_probe_budget_failsafe.py`（9 项）——读失败、创建写失败、扣减写失败回滚、
两"进程"共享预算不超发（含 CAS 冲突计数 ≥1）、CAS 冲突后重载、重启保留计数/成本/起始时间、
关闭不可绕过、provisional 数值保持。

> **诚实边界**：跨进程测试用两个 Registry 实例共享同一版本化存储模拟，**未做两个 OS 进程压测** → PARTIAL。

---

## 4. WP1-04 Self-Healing 原子 CAS

| 变更 | 说明 |
|---|---|
| `catalog.promote_version_cas()` | 同一事务内：读当前 APPROVED 版本 → 与 `base_version` 比对 → 条件更新 `latest_version`（`WHERE latest_version=expected`）→ 插入新版本行；任一步失败即 CONFLICT |
| `catalog.rollback_version_cas()` | 只有"当前 APPROVED 版本 == 本提案晋升版本"才允许回滚；否则 `ROLLBACK_CONFLICT` |
| `repair_service.promote()` | 改为调用上述原子方法；冲突 → `PROMOTION_FAILED / VERSION_CONFLICT`，不覆盖新版本；幂等键重放不产生新版本 |
| `repair_service.rollback()` | 校验 promoted_version；回滚操作者必须是人类主体 |
| 审批主体 | `approve()` 改用 `require_human_actor`（原先是"包含 agent 字样"的模糊匹配） |

验证：`tests/integration/test_repair_atomic_cas.py`（9 项）——stale base 拒绝、并发仅一个成功且版本集合为 `{1,2}`、
断言与 Oracle 绑定原样保留、服务层幂等、冲突不覆盖、回滚版本校验、他人在其后晋升时回滚被拒、
Agent 回滚被拒、沙箱 PENDING 版本不影响正式版本。

> **诚实边界**：并发测试在 SQLite 上以线程方式验证条件更新的原子性；PostgreSQL 行锁行为未实测（BLOCKED）。

---

## 5. WP1-05 统一目标域名与网络管控

### 实施

| 变更 | 说明 |
|---|---|
| 新增 `TargetPolicy` | `scheme+host+port` 白名单；拒绝协议相对（`//host`）、绝对 URL 越权、URL 内联凭证、非 http(s)、未授权内网/回环/链路本地；重定向链逐跳校验 |
| 浏览器请求级拦截 | `BrowserSession` 对 context 安装 route guard：未授权请求 **abort 并记录** `blocked_requests` |
| 导航校验 | `PlaywrightObserver.navigate` 用策略解析目标；`goto` 后、`click` 后复核落地地址，越权 → `TargetAccessDenied` → 步骤失败（不可能 PASSED） |
| Explorer 同源策略 | `ToolGateway` 接受同一个 `TargetPolicy`（不再是另一套 host/port 常量）；`ExplorationService` 构造一次策略，会话与网关共用；点击后同样复核 |
| 配置 | `TG_TARGET_EXTRA_ORIGINS`：除 `teamflow_base_url` 外显式授权的 origin |

### 验证

- `tests/unit/test_target_policy.py`（18 项）：相对路径、协议相对、越权绝对 URL、错端口、6 类内网/回环地址、
  URL 凭证、非 http scheme、`about:`/`data:`、重定向链、跳数上限、落地地址、网关侧一致性、与 `SecurityPolicy` 结论一致性。
- `tests/e2e/test_target_policy_browser.py`（4 项，真实 Chromium + 两个本地服务）：
  跨域 `fetch` 被 abort 且未授权服务端 **收到 0 次请求**；正常目标不受影响；顶层跳转被拒；
  协议相对 URL 被策略拒绝。

### 已知限制（如实披露）

Playwright 的 route 拦截不会对**服务端 302 顶层跳转**再触发一次可拦截请求，因此该次出站请求会到达目标；
产品保证是"落地地址复核 → `TargetAccessDenied` → 步骤失败"，不是"请求未发出"。
DNS/网络层强制阻断（如解析白名单、出网代理）列入后续工作，本轮**不声称已做到**。

---

## 6. 本轮未完成（不得标完成）

| 项 | 状态 | 原因 / 下一步 |
|---|---|---|
| WP2-01 稳定性回放 F08 完善 | PARTIAL（继承 RUN-005） | 本轮未改；下一步补"真实浏览器多次回放 + 门禁校验" |
| WP2-02 完整端到端真实验收 | PARTIAL | 正常/故障场景真实跑通；修复→晋升→回归全链未在本轮走完 |
| WP3 前端产品体验升级 | NOT_IMPLEMENTED（本轮） | 未改前端，不声称任何 UI 改善；需独立工作包 |
| WP4-01 PostgreSQL | BLOCKED | 本机无 Docker/psql |
| WP4-02 4 Worker 故障恢复 | PARTIAL（历史证据） | 未在本版本重跑 |
| WP4-03 60 分钟长稳 | BLOCKED | 未执行 |
| WP4-04 异常恢复注入 | PARTIAL | 部分覆盖（预算/DB 异常单测），未做服务级注入 |
| WP5 CI 三级门禁真实运行 | BLOCKED | 工作流在库，未在真实 GitHub 触发 |
| WP6 UI-FaultBench 深化 | NOT_IMPLEMENTED（本轮） | 未追加实验，不引用历史数字冒充本轮 |
