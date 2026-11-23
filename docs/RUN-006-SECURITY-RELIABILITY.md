# RUN-006 安全与可靠性专项

- 时间：2026-11-23
- 范围：WP1-01 / WP1-02 / WP1-03 / WP1-04 / WP1-05 的安全边界与故障安全语义
- 原则：**安全控制必须落在代码与数据上**，不能靠提示词、人工审核或文档约定替代

## 1. 网络访问控制（WP1-05）

### 已实现并由真实浏览器验证

| 控制 | 实现位置 | 验证方式 | 结果 |
|---|---|---|---|
| 目标 origin 白名单（scheme+host+port） | `TargetPolicy` | 18 项策略单测 | 越权 host/port、内网/回环/链路本地、`0.0.0.0`、URL 内联凭证、非 http scheme 全部拒绝 |
| 协议相对 URL 绕过 | `TargetPolicy.resolve()` | 单测 + e2e | `//evil.example.com/x` → `PROTOCOL_RELATIVE_URL_FORBIDDEN` |
| 请求级阻断（子资源 / XHR / fetch） | `BrowserSession._guard_route` | 真实 Chromium + 双本地服务 | 未授权服务端**收到 0 次请求** |
| 导航后落地地址复核 | `PlaywrightObserver.navigate` | e2e（302 场景） | 落地越权 → `TargetAccessDenied` → 步骤失败，不可能 PASSED |
| 点击跳转复核 | `PlaywrightObserver.click` / Explorer `_click` | 单测 + 代码审查 | 与 navigate 同一策略 |
| Explorer 与 Runner 同源策略 | `ToolGateway(target_policy=...)` | `test_gateway_uses_same_policy_for_protocol_relative` 等 4 项 | 不再存在两套 host/port 常量 |

### 已知限制（必须披露）

1. **顶层 302 跳转的出站请求会到达目标**：Playwright 的 route 拦截不会对服务端 302 顶层跳转再次触发可拦截请求。
   当前保证是"落地地址复核 → 拒绝继续"，不是"请求未发出"。缓解方向：出网代理 / DNS 解析白名单 / 严格网络命名空间，
   未实现，列入后续工作。
2. **DNS Rebinding**：`SecurityPolicy.check_url(resolve_dns=True)` 提供检查，但执行路径未强制开启 DNS 解析校验。
3. 白名单之外的一切目标默认拒绝，包括被授权的本地 TeamFlow 的其它端口。

## 2. 身份与审批边界（WP1-01 / WP1-04）

| 控制 | 行为 |
|---|---|
| 非人类主体审核用例 | `agent` / `agent:gpt` / `agent-1` / `llm:deepseek` / `system:promotion` / `bot:*` / `service:*` / 空串 → `403` |
| 非人类主体审批 Oracle | 同上 → `403`（原来只在 `approved_by ∈ {agent,llm}` 时拒绝） |
| 非人类主体审批/回滚 Repair | 同上 → `403` |
| Oracle 审批人复核 | 绑定/审核时回查 `approved_by` 与 `approval_history`，非人类审批的 Oracle 不得被使用 |
| LLM 假设晋升为根因 | 引擎在状态计算后强制降级，`llm:*` 永不 `CONFIRMED` |

### 身份验证的真实局限（不宣称生产级 RBAC）

- 系统**没有**登录、会话、Token、签名；`reviewer` / `approved_by` 是请求体字段。
- 因此本轮的保证是"**不会把非人类主体当作人类审批人**"，而不是"确认请求者是谁"。
- 单机本地开发环境的威胁模型是"误操作与自动化越权"，不是"恶意攻击者伪造身份"。
- 生产级要求：认证层 + 角色矩阵（P08 未批准）+ 审计留痕（当前有 `review_history` / `binding_history` / `promotion_record`，
  但**没有独立审计表**）。

## 3. 判定与证据完整性（WP1-02）

| 不变量 | 实现 | 测试 |
|---|---|---|
| 探针失败 ≠ 目标故障证据 | `feedback.assess()` 对非 `SUCCEEDED` 直接返回 `PROBE_*_NOT_EVIDENCE` | `test_probe_failure_is_not_target_failure_evidence` |
| 探针不能自证根因 | 探针只提供证据引用；`CONFIRMED` 由 `ConfirmationRule`（TRUSTED + directly_confirming + 无 TRUSTED 反驳）判定 | `test_probe_confirms_persistence_and_stops` 等 |
| 证据冲突不裁决 | 冲突 → `unresolved_conflicts` → `STOP_CONFLICT_UNRESOLVED` + `MANUAL_REVIEW` | `test_feedback_detects_conflicting_evidence` / `test_conflict_stops_loop_before_spending_more_budget` |
| LLM 参数未执行不得宣称已应用 | `effect=ranking_only`、`params_used_in_execution=false` | `test_llm_advice_declares_params_not_applied` |
| 重诊断可追溯 | `evaluation_history`（before/after/trigger/probes） | `test_probe_confirms_persistence_and_stops` |
| Disposition 变更保留历史 | `_refresh_disposition()` 追加 `history` | 代码审查 + 集成测试 |

## 4. 故障安全（WP1-03）

| 故障 | 行为 | 证据 |
|---|---|---|
| 预算读取失败（DB 异常） | `BudgetUnavailable` → 探针不执行（**不**新建预算续跑） | `test_load_failure_raises_instead_of_creating_fresh_budget` |
| 预算写入失败 | 回滚内存计数 → `BLOCKED / PROBE_BUDGET_UNAVAILABLE` | `test_reserve_write_failure_blocks_probe_and_rolls_back_count` |
| 多进程并发扣减 | 版本 CAS + 重载重试；收敛失败即阻断 | `test_two_processes_share_one_budget_without_overspend`（含冲突计数断言） |
| 预算关闭后重试 | 恢复文档为 CLOSED → `PROBE_BUDGET_CLOSED` | `test_closed_budget_cannot_be_bypassed_by_new_process` |
| 数据库短暂不可用 | API 层 `AppError(DEPENDENCY_ERROR)` → 502/503，不伪装成功 | 代码审查 |

> 未做：真实 PostgreSQL 断连注入、API 重启中途恢复、Worker 崩溃恢复（BLOCKED/PARTIAL）。

## 5. 版本与数据完整性（WP1-01 / WP1-04）

| 风险 | 控制 |
|---|---|
| 历史用例跟随"最新 Oracle"漂移 | 审核时固定 `oracle_version`；执行端按固定版本加载；版本缺失不回退 |
| 并发晋升覆盖他人版本 | 单事务比较 + 条件更新 + 唯一约束（`uq_tcase_version`） |
| 旧提案回滚覆盖新版本 | 回滚前校验"当前 APPROVED == 本提案晋升版本" |
| 沙箱版本污染正式状态 | 沙箱版本为 `PENDING`，不改变 APPROVED 版本与断言绑定 |
| 并发审核双写 | `review_revision` 行级 CAS，失败方 `CONFLICT` |

## 6. 残留风险清单

| # | 风险 | 等级 | 现状 | 建议 |
|---|---|---|---|---|
| R1 | 无认证/RBAC，审批人身份可伪造 | 高（生产） | 仅主体类型门禁 | 认证 + P08 角色矩阵（NEEDS_DECISION） |
| R2 | 顶层 302 可发出一次越权出站请求 | 中 | 落地拒绝 + 记录 | 出网代理/DNS 白名单 |
| R3 | `vite` dev-only high 漏洞只能 semver-major 修复 | 中 | 未升级 | 单独评估升级兼容性（见决策请求） |
| R4 | Oracle 跨断言语义复用 | 低 | 记录 warning + 绑定字段 | 数据治理：每条断言独立 Oracle |
| R5 | PostgreSQL 行锁/CAS 未实测 | 中 | 逻辑等价，未运行时验证 | CI Service Container 实跑 |
| R6 | 审计无独立表（仅文档字段） | 中 | `review_history`/`binding_history`/`promotion_record` | 独立审计表 + 只追加 |
| R7 | LLM 顾问调用失败静默降级 | 低 | 记录 `fallback/error`，不阻断确定性流程 | 保持现状（有意设计） |

## 7. 安全结论

- 本轮把"网络访问控制"和"审批主体类型"从**文档约定**落到了**代码门禁**，并有真实浏览器/真实 API 证据。
- 但**没有**解决身份认证，也没有解决顶层跳转的出站问题；这两项必须在报告中保持披露，不能宣称"生产就绪"。
- 所有失败路径（预算、探针、审核、晋升）都是 fail-closed：拒绝执行并留下可审计原因，而不是"继续跑并当作成功"。
