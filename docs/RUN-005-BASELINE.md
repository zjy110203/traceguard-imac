# RUN-005 基线审计（WP0）

- 审计时间：2026-11-23
- 基线提交：`9d751fe`（RUN-004-FE 最新交付）
- 分支：`workbuddy/traceguard-autonomous`
- 测试基线（审计时）：224 passed / 0 failed（`.venv/bin/python -m pytest -q`）
- 审计方式：逐文件阅读源码 + 运行测试 + 真实服务调用（TeamFlow 8011 / API 8010 / 隔离数据库）

> 状态口径（本轮统一为六种）：
> **IMPLEMENTED** 代码已实现、无真实运行证据；**VERIFIED** 已有本轮真实运行证据；
> **PARTIAL** 只完成部分能力或证据不完整；**BLOCKED** 外部条件阻塞；
> **NEEDS_DECISION** 需项目负责人批准；**NOT_IMPLEMENTED** 尚未实现。
>
> 明确区分：代码存在 ≠ 功能完成；测试通过 ≠ 真实业务验证；历史验收 ≠ 当前版本验收。

## 1. 审计发现（审计前 → 本轮修复后）

| # | 模块 | 审计前真实状态 | 关键问题（代码级） | 本轮状态 |
|---|---|---|---|---|
| 1 | Probe 预算 | IMPLEMENTED（但语义错误） | `ProbeService` 持有**全局唯一** `ProbeEngine/ProbeBudget`；60s 窗口从**服务启动**计时，`_calls` 全局累加。进程跑满 60s 后，后续所有诊断探针永久 `PROBE_TIME_BUDGET_EXHAUSTED`；多诊断互相消耗 | **VERIFIED**（按 Run/Diagnosis 隔离、持久化、关闭语义） |
| 2 | Active Planner | PARTIAL | `suggest()` 只返回静态建议 + 打分；`choose()` 从未参与执行；无"执行→新证据→再评估"闭环 | **VERIFIED**（真实闭环，见 plan `probeplan_1795409170…`） |
| 3 | SSE | NOT_IMPLEMENTED（伪 SSE） | `/events/stream` 只发 `: keepalive` 注释，无事件、无 Last-Event-ID、无过滤 | **VERIFIED**（真实事件流 + 断线续传 + 轮询降级） |
| 4 | Consumer Inbox | PARTIAL | 内存版有 PROCESSING/FAILED/SUCCEEDED；持久化版只有 `PROCESSED` 单状态、无租约、无并发抢占、无事务性；**没有任何真实消费者** | **VERIFIED**（统一状态机 + 原子抢占 + 崩溃恢复 + Run 时间线投影真实消费者） |
| 5 | Event Store 并发 | PARTIAL | 数据库版以 `MAX(sequence)+1` 分配，多进程同时写入时唯一约束冲突被**误报为业务 CONFLICT**；无 SQLite busy_timeout/WAL | **VERIFIED**（DB 原子序号分配器；4 进程 × 25 事件实测） |
| 6 | Compiler | IMPLEMENTED（仅规则） | 无 LLM 路径、无模式开关、无稳定性回放接口 | **PARTIAL→VERIFIED（LLM 路径真实冒烟；回放编排 VERIFIED，未做真实浏览器多次回放）** |
| 7 | Active Probe LLM | NOT_IMPLEMENTED | 无 LLM 规划；无"只能从已注册探针中选择"的边界实现 | **VERIFIED**（真实 LLM 选择 + 白名单过滤 + 确定性 Planner 仍为决策者） |
| 8 | Repair LLM | NOT_IMPLEMENTED | 仅接受人工/规则 changes | **VERIFIED**（真实 LLM 生成受限变更，禁止触碰断言/Oracle；仍需审批/CAS） |
| 9 | Benchmark v2 | VERIFIED（历史） | 30 场景 verdict 对照已存在 | **VERIFIED**（本轮重跑 30/30，独立数据库） |
| 10 | Benchmark v3 多臂对照 | NOT_IMPLEMENTED | A/C/D/E + LLM Flash/Pro/Routed 对照未做 | **BLOCKED**（成本与时长；见 Benchmark 报告） |
| 11 | PostgreSQL | BLOCKED（本机无 Docker/psql） | 无 PG 驱动依赖、无 PG 专项测试 | **PARTIAL**（补齐 parity 测试 + CI Service Container；本机仍 BLOCKED） |
| 12 | CI 门禁 | PARTIAL（仅 PR） | 无 Nightly / Release | **VERIFIED（工作流已入库）/ 未在真实 GitHub 运行** |
| 13 | 前端自动化验收 | PARTIAL | 历史 53 项验收无脚本 | **VERIFIED**（`scripts/ui_acceptance.py` 30 项全通过 + 截图） |
| 14 | 长稳 60 分钟 | NOT_IMPLEMENTED | 无长稳证据 | **BLOCKED**（本轮未执行，不得用短测冒充） |
| 15 | 多 Worker 故障恢复 | VERIFIED（RUN-003-E 历史） | 冻结在历史提交 | **PARTIAL**（未在本轮版本重跑） |
| 16 | P03 / P08 决策 | NEEDS_DECISION | 无正式决策包 | **NEEDS_DECISION**（本轮产出决策包） |

## 2. 真实能力边界（审计结论）

- **Agent 是能力层不是事实层**：LLM 三条路径均只产出候选，`CONFIRMED` 仍只由确定性规则 + 权威证据产生（本轮回归测试覆盖）。
- **SSE 不是状态权威**：前端在 Run 详情页以 SSE 触发刷新，但界面状态始终来自 `/api/v1/runs/{id}` 等权威接口。
- **预算与探针是 provisional**：P06 未确认，默认 50 次 / 100 成本 / 60 秒全部标注 `provisional=true`。
- **Oracle 未被自动化**：任何路径（规则/LLM）都不会绑定或批准 Oracle（`oracle_id=None` 强制）。

## 3. 本轮依赖关系（决定执行顺序）

```
WP1.1 预算隔离 ──► WP1.2 Active Planner 闭环 ──► WP2.2 LLM 探针规划（必须在预算与校验之后）
WP1.5 事件并发 ──► WP1.3 SSE 续传（同一存储顺序语义） ──► 前端实时升级
WP1.4 Inbox 状态机 ──► 时间线投影真实消费者（证明能力被使用）
WP1.3/1.4/1.5 ──► WP4 CI Nightly（SSE/Inbox/Worker 专项）
WP2.1 编译模式 ──► F08 稳定性回放 ──► Release 门禁
```

## 4. 环境与安全前置

- 本地无 Docker / psql：PostgreSQL 真实验证只能走 CI（已配置 Service Container），本地标记 BLOCKED。
- `.env` 仅由程序通过配置机制读取；本轮未读取、未输出任何密钥内容。
- 所有验收与基准使用隔离数据库：`.traceguard/run005/{api.db,teamflow.db}` 与隔离 artifact 目录；未触碰原有 `.traceguard/traceguard.db`。
