# RUN-007 安全与可靠性报告

- 时间：2026-11-23（GMT+8）
- 分支：`workbuddy/run007-resume-20261123`
- 原则：**代码完成 ≠ VERIFIED**；未真实执行的项一律标 BLOCKED / NEEDS_DECISION，不冻结安全边界。

---

## 1. 安全边界现状（指令要求保持待确认的项）

| 编号 | 主题 | 本轮状态 | 说明 |
|---|---|---|---|
| **D-01** | 身份认证 / 多租户隔离 | **NEEDS_DECISION（未冻结）** | 当前是"单机可信部署"：审批主体校验（`require_human_actor()`）只保证**主体命名空间**不能是 `agent:` / `llm:` / `system:` / `bot:` / `service:`，**不是身份认证**。任何人只要在请求里写 `role:qa-lead` 就能通过。是否引入 API Key / OIDC 需负责人决策，本轮不擅自实现、不擅自标记为已解决。 |
| **P03** | 外网/真实第三方站点泛化 | **NEEDS_DECISION（未执行）** | 本轮未访问任何未授权外部站点；`TargetPolicy` 默认只允许本地被测应用 origin。外部泛化需要新的授权域名清单。 |
| **P08** | 生产凭据与密钥管理 | **NEEDS_DECISION** | LLM Key 走 `.env` / CI Secret，未引入 Vault 类方案；密钥轮换、最小权限未定义。 |
| **P06** | Release 制品与供应链签名 | **NEEDS_DECISION** | 本轮只修 CI 门禁逻辑，未引入 SBOM / 镜像签名；不创建 Release Tag。 |

> 这四项**没有**被本轮"顺手实现"，也没有被写成"已完成"。人工审批不可被 Agent 绕过（见 §2.1）。

## 2. 安全机制与真实证据

### 2.1 人工审批不可绕过（Agent 不能自我批准）

| 入口 | 约束 | 证据 |
|---|---|---|
| 用例审核 | 非人类主体（`agent:*`、`llm:*`、`system:*`、`bot:*`、`service:*`）→ 403/412 | `tests/integration/test_actor_validation.py`（`test_review_rejects_non_human[...]`，参数化 5 类主体） |
| Oracle 审批 / 绑定 | 同上 | 同上（`test_oracle_approval_rejects_non_human`、`test_oracle_binding_rejects_non_human`） |
| 修复 审批 / 拒绝 / 回滚 | 同上 | 同上（`test_repair_approve/reject/rollback_rejects_non_human`） |
| 处置复核 | 同上；人类主体必须具名 | 同上（`test_disposition_review_accepts_named_human`） |
| E2E 实测 | Agent 审批被拒（`D8`）、人类 `role:qa-lead` 审批通过（`D9`） | `docs/assets/run007/e2e-worker-run007.json` |

### 2.2 LLM 不得直接确认根因

- 诊断主因由**规则引擎 + 证据**决定；LLM 只作为 advisor（建议/摘要），其输出进入
  `decision_source`、`llm_stats` 审计字段，不直接写 `outcome`；
- 场景 B 实测：`outcome=CONFIRMED`、`disposition=PRODUCT_DEFECT` 由证据与规则得出，
  LLM 调用次数 5、`fallback_calls=0`；
- 场景 C 探针闭环后假设状态迁移（`SUPPORTED` / `CONFIRMED`）由证据支持，`STOP_NO_HIGH_VALUE_PROBE` 为规则终止条件；
- 探针**失败/超时**不会被当作目标系统故障证据（探针执行状态 `SUCCEEDED` 是前提）。

### 2.3 出站网络边界（目标策略统一）

证据：`docs/assets/run007/network-boundary.json`（本轮之前的实验，本轮复核未回退）。

| 场景 | 结果 |
|---|---|
| 跨 origin fetch（未授权端口） | 请求在浏览器层被 abort，`reason=PORT_NOT_ALLOWED`，`forbidden_hits=0` |
| 顶层 302 跳转 | 重定向每一跳被校验，落地 URL 判定 `BLOCKED`，步骤失败并返回 `TargetAccessDenied`，`forbidden_new_hits=0` |
| 点击导航到未授权 origin | 落地 URL 校验失败，`SCHEME_NOT_ALLOWED` / `PORT_NOT_ALLOWED`，`forbidden_new_hits=0` |
| DNS rebinding | 授权域名解析到私网 → 阻断；部分重叠仍允许；解析失败不得放行 | 

补充：`tests/unit/test_target_policy_dns.py` 覆盖 8 项（含 IPv6 AAAA、逐跳链、URL 级守卫参数化），
`tests/e2e/test_target_policy_browser.py` 用真实 Chromium 覆盖跨域请求 / 重定向落地 / 点击导航三类。

**已知限制（继承且未放宽）**：顶层 302 的"出站"请求在部分浏览器实现下仍会到达目标，
产品保证是"**落地拒绝 + 步骤失败 + 出站记录**"，不宣称"零出站"。本轮没有修改该保证。

## 3. 可靠性

### 3.1 已修复的可靠性缺陷（本轮）

| # | 缺陷 | 影响 | 修复 | 回归测试 |
|---|---|---|---|---|
| 1 | `RunPipeline._state_cache` 跨 Run 泄漏 | 常驻 Worker 下**假通过漏检**（读到上一个 Run 的业务状态） | `execute()` 开头清空缓存 | `tests/unit/test_pipeline_state_cache.py`（2） |
| 2 | 沙箱在 API 进程内执行 | 队列/Worker 形同虚设；无法证明消费链路 | 入队 + 等待终态；无 Worker 时 fail-closed | `tests/integration/test_sandbox_worker_queue.py`（2） |
| 3 | 并发启动建表非原子 | API + 多 Worker 同时启动随机崩溃 | `create_all` 重试 + SAVEPOINT + 迁移记录容忍主键冲突 | `tests/integration/test_migration_v8.py`（4） |

### 3.2 队列 / 租约 / 代次隔离

- 原子领取：单条条件 `UPDATE` + `rowcount` 判定；PG 上 12 连接抢 1 个 Run 只有 1 个赢家（CI 用例）；
- 代次隔离：接管后 `fencing_token` 严格递增，旧代次 `submit_result` / `renew_lease` 被拒
  （SQLite 集成测试 + PG 平台用例 + 故障恢复实验三方证据）；
- 过期回收：`reap_expired_runs()` 不改变执行历史，只把 Run 重新置为可领取。

### 3.3 长稳与崩溃恢复

- 60 分钟长稳：19 项 checks（进度、无 FAILED/NOT_EVALUATED、无超时、SSE 有事件且续传有效、
  TeamFlow 真实健康、DB 完整性、无重复事件键、无孤儿 Step、无未回收租约…）→ 结果见
  `docs/RUN-007-TEST-REPORT.md`；
- Worker 被杀接管：真实 kill + 真实计时 + 同 Run 接管 + 陈旧代次拒绝 → `worker-fault-recovery-verified.json`。

### 3.4 数据一致性

- 晋升/回滚：`compare + write` 在同一事务，幂等回执与版本指针同事务提交（崩溃后可确定性重放）；
- 事件序号：DB 侧原子分配（`INSERT … ON CONFLICT … RETURNING`），并发不重号（PG 用例 20 次分配全唯一）；
- 迁移：v8 幂等 + 旧库（v7）升级不丢数据。

## 4. 安全回归清单（本轮实际执行）

| 检查 | 命令/证据 | 结果 |
|---|---|---|
| 主体校验 | `pytest tests/integration/test_actor_validation.py` | 全部通过（含 5 类非法主体 × 6 个入口） |
| 目标策略（URL/DNS） | `pytest tests/unit/test_target_policy_dns.py` | 通过 |
| 出站边界（真实浏览器） | `pytest tests/e2e/test_target_policy_browser.py` | 通过（见 TEST-REPORT） |
| 密钥泄露扫描 | Nightly `secret pattern scan`（`sk-…`/`ghp_`/`github_pat_`/`AKIA`/私钥块 + 长度阈值） | **CI 真实通过**（Nightly job `security` = success）。首跑曾因规则过宽命中本文档占位符而误报，已改用 `git grep` 只扫已跟踪文件并收紧模式 |
| 前端依赖漏洞 | Nightly `npm audit`（区分工具错误与漏洞） | **CI 真实运行**（Nightly `security` job 内）；High/Critical 阻断 |

## 5. 未解决风险（如实登记）

1. **无身份认证**：任何人可自称 `role:*`。这是 D-01 待决策项，**不是**本轮引入的回归，但也不能宣称"已安全"。
2. **CI 未真实运行**：门禁逻辑修复无运行证据。
3. **顶层 302 出站**：见 §2.3。
4. **资源监控缺口**：沙箱环境无法采集 CPU/RSS，长稳只能用存活探测与 DB 指标间接证明稳定。
5. **单机 SQLite 承载**：本轮未做多机/分布式验证（不引入 Redis/Celery 的前提下不做）。
