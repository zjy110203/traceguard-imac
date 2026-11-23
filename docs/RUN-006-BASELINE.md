# RUN-006 基线审计（WP0）

- 审计时间：2026-11-23（GMT+8）
- 分支：`workbuddy/traceguard-autonomous`
- 指令基线 Commit：`dadb4ae`（RUN-005 UI 验收证据刷新）；审计时 HEAD：`78a09f6`（RUN-005 差距分析）
- 测试基线（审计时实测）：**285 passed / 5 skipped / 0 failed**（`.venv/bin/python -m pytest -q`，约 26s）
- 审计方式：逐文件阅读源码 + 运行全量测试 + 启动真实隔离服务（TeamFlow 8011 / API 8010 / 独立 SQLite）实测

> 状态口径（六种）：**IMPLEMENTED** 代码已实现但无本轮运行证据；**VERIFIED** 有本轮真实运行证据；
> **PARTIAL** 部分完成或证据不完整；**BLOCKED** 外部条件阻塞；**NEEDS_DECISION** 待负责人批准；
> **NOT_IMPLEMENTED** 尚未实现。
>
> 证据等级严格区分，不混为一谈：
> `代码存在` → `单元测试通过` → `集成测试通过` → `真实环境运行通过` → `长时间运行通过` → `产品验收通过`。
> 本文件对每项标注到具体等级，禁止用前一等级替代后一等级。

## 1. 环境事实

| 项 | 值 |
|---|---|
| Python | 3.13.12（`.venv`） |
| Node | 22.22.2（前端本轮未改） |
| playwright | 1.65.2 + 自带 Chromium |
| 数据库（本地） | SQLite；本轮隔离目录 `.traceguard/run006/` |
| PostgreSQL | 本机无 Docker / psql → 运行时验证 BLOCKED（CI Service Container 已配置但未真实触发） |
| LLM | `.env` 已配置 DeepSeek；本轮真实诊断调用发生（`rule-v1+llm-advisor`） |
| 隔离端口 | TeamFlow 8011 / API 8010（未触碰 `.traceguard/traceguard.db`、`teamflow.db`） |

## 2. 指令 WP1 逐项审计（审计前真实状态）

| 编号 | 要求 | 审计前状态 | 代码级证据（审计前） |
|---|---|---|---|
| WP1-01 | Oracle 绑定 + 审核闭环 | **PARTIAL** | `catalog.review_testcase()` 只做 `reviewer.lower() in {"agent","llm","system"}` 精确匹配；`bind` 接口不存在；审核不校验 required assertion 的 Oracle；`pipeline._load_oracles()` 恒取最新版本；`AssertionSpec` 无版本字段 |
| WP1-02 | Active Diagnosis 动态闭环 | **PARTIAL** | `probe_service.plan_and_probe()` 已能执行探针并落证据，但**不把证据回灌假设、不重跑诊断**；`choose(conflicts_unresolved=False)` 硬编码；`diagnose()` 不消费 `probe` 证据；探针失败与目标故障未区分 |
| WP1-03 | ProbeBudget 持久化与跨进程安全 | **PARTIAL** | `_load_budget_doc` 异常 → 返回 None → **新建一份新预算继续执行**；`_save_budget_doc` 失败只 `logger.warning`；`DocStore.put` 支持版本 CAS 但预算未使用；`engine.execute` 先扣内存再落库 |
| WP1-04 | Self-Healing 原子 CAS | **PARTIAL** | `promote()` 先 `latest_approved_version()` 再在**另一个事务** `create_testcase()`，中间可被并发插入；`rollback()` 不校验"当前正式版本仍是本提案晋升的版本" |
| WP1-05 | 正式浏览器执行安全 | **PARTIAL** | `SecurityPolicy` 只被单测使用；`PlaywrightObserver.navigate()` 对绝对 URL 直接 `goto`，无白名单；点击跳转/重定向不检查；Explorer `ToolGateway` 另有一套 host/port 常量（两套策略） |

## 3. 其他关键问题（审计新发现）

1. **LLM 假设可绕过确认门禁（真实漏洞）**：`DiagnosisEngine.diagnose()` 只比较 `h.suggested_by == "llm"`，
   而实际写入值是 `llm:deepseek-flash` 这类带命名空间的形式，因此"先降级再判定"的顺序 + 精确匹配
   使得 LLM 假设可以保留 CONFIRMED。已在本轮修复（前缀判断 + 状态计算后强制执行降级）。
2. **`diagnosis` 文档缺 `unresolved_conflicts` 字段**：`Diagnosis` 对象有，落库丢失，前端与 API 都看不到冲突。
3. **Evidence Graph 缺 `ProbeExecution` 节点**：文档声明 11 类节点，实际只建 8 类，探针与假设之间没有边。
4. **seed 数据存在跨断言复用 Oracle**：`a_biz_created`/`a_b2`/`a_row_render2` 绑定的是别的 assertion 的 Oracle，
   在严格门禁下必须显式可见（本轮记录为 warning 而非静默通过）。

## 4. 各能力状态矩阵（本轮结束状态）

| 能力 | 状态 | 本轮证据 |
|---|---|---|
| Oracle 绑定 API + required assertion 门禁 | **VERIFIED** | 26 项门禁测试 + 真实 API 实测（412/403/200） |
| Oracle 版本固定（历史用例复现） | **VERIFIED** | `test_historical_case_uses_pinned_oracle_version`；pipeline 按 pinned 版本加载 |
| 审核并发 CAS（`review_revision`） | **VERIFIED** | 行级条件 UPDATE；`test_stale_review_revision_is_rejected` |
| 非人类主体冒充审核人 | **VERIFIED** | 主体类型（命名空间）校验；`agent`/`agent:gpt`/`llm:*`/`system:*` 全拒 |
| 探针证据回灌假设 / 动态重诊断 | **VERIFIED（语义层）** | 11 项集成测试（真实 SQLite + 真实 DocStore） |
| 真实探针回灌（HTTP 打到 TeamFlow） | **VERIFIED** | `docs/assets/run006/probe-plan-real-feedback.json`（count=1 → 假设被排除） |
| 真实故障 Run 的完整"探针改变结论"链 | **PARTIAL** | 真实 Run 的诊断通常已 CONFIRMED，探针无机会介入（见 §6） |
| 探针失败不当证据 | **VERIFIED** | 失败/超时不计入 supporting/contradicting |
| 冲突不可裁决 → STOP_CONFLICT_UNRESOLVED | **VERIFIED** | 反馈层检出 + 循环顶部零消耗停止 |
| 预算故障安全（读失败/写失败均阻断） | **VERIFIED** | 9 项测试：读失败 `PROBE_BUDGET_UNAVAILABLE`、写失败回滚计数 |
| 预算跨进程一致性 | **PARTIAL** | 多 Registry 共享版本 CAS 校验通过；**未做两个 OS 进程压测** |
| 晋升/回滚原子 CAS | **VERIFIED（SQLite）** | 单事务比较+写入；并发仅一个成功；PostgreSQL 行锁未实测 |
| 目标域名统一策略（子资源/XHR） | **VERIFIED** | 真实 Chromium + 双本地服务：未授权服务端 **0 次请求** |
| 顶层 302 跳转到未授权域 | **PARTIAL** | 落地地址复核 → `TargetAccessDenied` → 步骤失败；但该次出站请求**已到达**目标（route 不拦顶层跳转） |
| 稳定性回放 F08 门禁 | **PARTIAL（继承 RUN-005）** | 本轮未改动；仍为"编排+语义 VERIFIED、真实多次回放未做" |
| 完整端到端真实链路（含修复晋升） | **PARTIAL** | 正常场景 + 故障场景真实跑通；修复全链仍靠沙箱+人工，未在本轮走完 |
| 前端 14 视图体验升级 | **NOT_IMPLEMENTED（本轮）** | 未改前端代码，不声称任何 UI 改善 |
| PostgreSQL / 4 Worker / 60 分钟长稳 | **BLOCKED** | 本机无 PG、无独立时间窗；不得用短测冒充 |
| CI 三级门禁真实运行 | **BLOCKED** | 工作流在库；未在真实 GitHub Actions 触发 |
| Benchmark v3 多臂 | **NOT_IMPLEMENTED（本轮）** | 未追加实验，不得引用历史数字冒充本轮 |
| P03/P06/P08/P10 | **NEEDS_DECISION** | 保持原状态，仅产出决策请求 |

## 5. 冻结不变量（本轮未触碰）

- Test Verdict 四态 + 8 条聚合规则；`NOT_EVALUATED ≠ INCONCLUSIVE`；`execution_status` 独立。
- 必需 Assertion 无可用 Oracle 不得判 PASSED；空必需断言集非法。
- Event：workspace 服务端注入、幂等键 producer 维唯一、同键异载荷拒绝、只追加。
- Inbox：`(consumer_id,event_id)` 唯一、外部副作用不确定不重试。
- Probe：五项校验、模板/表白名单、脱敏、行数限制、禁任意 SQL。
- Repair：不改断言/Oracle、沙箱真跑、人工审批、CAS 晋升。
- Agent 是能力层不是事实层：LLM 不得 CONFIRMED / 批准 Oracle / 批准 Repair。

## 6. 审计结论（决定本轮取舍）

1. WP1 的五项都是**真实逻辑缺陷**，不是"缺功能"，因此本轮优先修逻辑 + 补门禁 + 留下可复核证据。
2. 真实故障链路的诊断大多**在规则层就已经 CONFIRMED**，探针闭环的价值体现在
   `INCONCLUSIVE / 冲突 / 证据不足` 这三类场景；本轮用真实探针 + 真实 Run 验证了"证据回灌改变结论"，
   但**没有**在真实浏览器故障场景下产出"探针把 INCONCLUSIVE 变成 CONFIRMED"的完整实例（PARTIAL，如实记录）。
3. WP2/WP3/WP4/WP5/WP6 需要独立时间窗与外部条件（PG、真实 CI、长稳、前端截图审查），
   本轮未执行的部分一律标 PARTIAL/BLOCKED，不以历史证据替代。
