# TraceGuard API 参考（v1）

> RUN-006 实测：OpenAPI 共 **94** 个方法/路径组合（含 `/health` 与 `/docs` 相关基础端点）；
> 本文件按资源分组列出业务端点，RUN-006 新增 `POST /testcases/{id}/versions/{v}/oracle-bindings`。

- Base URL：`http://127.0.0.1:8000/api/v1`
- 交互式文档：`/docs`（OpenAPI 3.1，`/openapi.json`）
- 错误格式：`{"detail": {"code": "...", "message": "..."}}`（HTTP 400/403/404/409/412/422）
- 认证：当前为**本地单租户**部署，无登录；生产部署需前置网关鉴权（见 `SECURITY.md`）。

## 1. 系统

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/system/health` | DB 可用性、Worker 状态、API 进程 RSS/CPU、按 Workspace 授权范围统计的计数（SQL COUNT，无 1000 截断） |
| GET | `/system/browser?probe=true` | 真实启动一次浏览器自检（渠道/版本/冒烟结果） |
| GET | `/system/config` | 脱敏后的运行配置 |
| GET | `/system/metrics` | 运行/证据/事件/诊断/修复统计 |
| GET | `/system/llm/summary` | **RUN-004-FE 新增，只读**：Provider/模型配置、调用/错误/Token/估算成本/预算、by_agent、by_model、daily_series、延迟、决策来源、最近调用（安全元数据） |
| POST | `/system/seed` | 幂等写入 TeamFlow 演示资产（需求/用例/Oracle） |

`GET /system/llm/summary` 安全边界：不返回 API Key、完整 Prompt、页面内容、task_id/run_id 明细或原始凭证；
成本是应用侧按牌价估算，不是 DeepSeek 平台账单；`configured` 是静态配置态，`connected` 恒为 `unknown`
（该端点不主动触网，真实连通性由独立自检给出）。

## 2. 测试资产（Catalog）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET/POST | `/workspaces` | 工作区列表 / 创建 |
| GET/POST | `/intents` | 测试需求列表（`workspace_id`、`status` 过滤）/ 创建 |
| GET | `/intents/{id}` | 详情 |
| POST | `/intents/{id}/status` | `DRAFT/REVIEW/APPROVED/DEPRECATED` |
| GET/POST | `/testcases` | 用例列表（含步数/断言数/审核状态）/ 创建（PENDING） |
| GET | `/testcases/{id}` | 详情 + 最新版本 |
| GET | `/testcases/{id}/versions` | 全部不可变版本 |
| GET | `/testcases/{id}/versions/{v}` | 指定版本 |
| POST | `/testcases/{id}/versions/{v}/review` | 人工审核；APPROVED 前强制校验全部 required assertion 的 Oracle；`reviewer` 主体类型不得为 agent/llm/system/service/bot；并发审核用 `review_revision` CAS |
| POST | `/testcases/{id}/versions/{v}/oracle-bindings` | **RUN-006 新增**：`{"bindings": {assertion_id: {oracle_id, oracle_version?}}, "actor": "role:..."}`，校验存在/审批/同 Workspace；APPROVED 版本不可重绑 |
| GET/POST | `/oracles` | Oracle 列表 / 创建（`approved=true` 需具名人类 `approved_by`，非人类主体 → 403） |
| GET | `/oracles/{id}` | 指定或最新版本 |

## 3. 执行

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/testcases/{id}/execute` | 一键执行；未审核版本 → 412；`{"sync":false}` 入队，`true` 同步 |
| POST | `/runs/{id}/execute` | 同步执行已入队 Run（线程池，避免 Playwright 与事件循环冲突） |
| GET | `/runs` | `workspace_id/status/verdict/limit` |
| GET | `/runs/{id}` | 含完整 report |
| GET | `/runs/{id}/steps` | 步骤轨迹（观测裁剪后） |
| GET | `/runs/{id}/report` | 只取报告 |
| GET | `/runs/{id}/evidence` \| `/events` | 该 Run 的证据 / 事件 |
| POST | `/tasks` | 创建任务（幂等键） |
| GET | `/tasks` \| `/tasks/{id}` \| `/tasks/{id}/summary` | 任务查询与展示汇总 |
| POST | `/tasks/{id}/runs` | 重试：新 Run，attempt+1 |
| POST | `/tasks/{id}/cancel` | 取消任务及未完成 Run |
| GET | `/queue/stats` \| POST `/queue/reap` | 队列状态 / 回收过期租约 |

### 执行示例

```bash
curl -s -X POST http://127.0.0.1:8000/api/v1/testcases/tcase_tf_create_normal/execute \
  -H 'Content-Type: application/json' -d '{"sync": true}' | jq '.execution.test_verdict'
# "PASSED"
```

## 4. 事件与证据

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/events` | 过滤：workspace / run_id / event_type / limit / offset |
| GET | `/events/stats` \| `/events/conflicts` | 统计 / 幂等冲突审计 |
| GET | `/events/{id}` | 单事件 |
| GET | `/evidence` | 过滤：workspace / run_id / kind / source_type |
| GET | `/evidence/{id}` | 证据内容（已脱敏）+ 元数据 |
| GET | `/evidence/{id}/download` | 下载附件（返回 `X-TraceGuard-SHA256`） |
| POST | `/evidence/{id}/verify` | 服务端存储完整性验证（读持久化内容重算摘要 + 附件字节 + 派生来源）；旧请求的 `content` 只作为 `legacy_source_check` 单独返回 |
| GET | `/artifacts` \| `/artifacts/{id}/download` | 产物列表 / 下载（读取时校验完整性） |

## 5. 诊断 · 图 · 探针

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/runs/{id}/diagnose` | 规则诊断；PASSED / NOT_EVALUATED / 无证据 → 412 |
| GET | `/diagnoses` \| `/diagnoses/{id}` | 诊断列表 / 详情（含 Hypothesis 与证据引用） |
| GET | `/dispositions` | 处置清单（按 diagnosis 关联） |
| POST | `/dispositions/{id}/review` | 人工批准/驳回分流 |
| POST | `/runs/{id}/graph` \| GET | 构建 / 读取证据图快照 |
| GET | `/graphs` | 图快照列表 |
| GET | `/probes/definitions` | 只读探针目录（能力/模板/风险/成本） |
| POST | `/probes/execute` | 五项校验后执行；越权返回 `status=BLOCKED` |
| GET | `/probes/executions` \| `/probes/audit` | 执行记录 / 审计日志 |
| GET | `/runs/{id}/probe-suggestions` | 基于证据缺口的探针建议（含 EIG 启发式评分） |

## 6. Agent 探索与编译

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/intents/{id}/explore?max_steps=8` | 真实浏览器探索（工具白名单、域名/端口白名单、步数预算） |
| GET | `/explorations` \| `/explorations/{id}` | 轨迹列表 / 详情（含网关审计） |
| POST | `/explorations/{id}/compile` | 轨迹 → 候选用例（PENDING，必须人工审核） |

## 7. 修复（Self-Healing）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/repairs` | 提案；越界字段（断言/Oracle/业务）→ 412 |
| GET | `/repairs` \| `/repairs/{id}` | 列表 / 详情（含 diff、校验结果、晋升/回滚记录） |
| POST | `/repairs/{id}/validate` | 规则校验（断言保持、Oracle 完整、Diff 白名单） |
| POST | `/repairs/{id}/sandbox-run` | **真实再执行**候选版本，产出沙箱 Run |
| POST | `/repairs/{id}/submit` | 沙箱必须 PASSED，否则 412 |
| POST | `/repairs/{id}/approve` \| `/reject` | 人工审批（agent 被拒） |
| POST | `/repairs/{id}/promote` | CAS 晋升 + 幂等键 |
| POST | `/repairs/{id}/rollback` | 回滚（生成恢复版本，不删历史） |

## 8. Benchmark

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/benchmark/dataset` | 数据集元信息（**不含 GT 标签**） |
| GET | `/benchmark/experiments` \| `/{id}` | 实验列表 / 详情（指标含分子、分母、Wilson CI） |
| POST | `/benchmark/experiments` | `{"kind":"A"｜"B"｜"C"}`，真实执行（浏览器） |

## 9. 语义约定（消费方必读）

1. `test_verdict` 与 `execution_status` 独立；`NOT_EVALUATED` 不等于失败，也不等于通过。
2. `POST /runs/{id}/diagnose` 对 PASSED 的 Run 返回 412 —— 不做伪诊断。
3. 未审批 Oracle 不参与判定；`oracle_available=false` 的断言结论必须是 `NOT_EVALUATED`。
4. 审核通过时 Oracle 版本被固定到 `AssertionSpec.oracle_version`；执行端按固定版本加载，缺失时不回退到最新版本。
4. 修复沙箱 `sandbox_status` 与生命周期 `status` 是两个维度，不可互相替代。
5. Benchmark 的 `blocked_baselines` 表示**没有可用模型/人力**，不是 0 分。

## 10. RUN-005 新增 / 变更接口

| 方法 | 路径 | 语义要点 |
|---|---|---|
| GET | `/events/stream` | 真实 SSE。**必须认证**（认证开启时未登录 401）；`workspace_id` 越权 403，省略时只推送调用方授权 Workspace 并集。参数 `workspace_id/run_id/event_type/last_event_id/heartbeat_seconds/poll_interval/limit`；也接受 `Last-Event-ID` 头。`id` 游标形如 `pi=…;seq=…;at=…;eid=…`（来源+续传位置）；**sequence 是 producer 实例内序号，不存在跨 producer 全局顺序**；投递 at-least-once，客户端按 `event_id` 去重；游标不能绕过 Workspace 授权 |
| POST | `/runs/{run_id}/probe-plan` | Active Diagnosis 闭环：返回 `steps[]`（probe/score/status/evidence/execution_id）、`stop_reason`、`budget`、`gaps_before/gaps_after`、`llm_planner` |
| GET | `/probe-plans`、`/probe-plans/{id}` | 计划列表/详情（含 LLM 建议与 fallback 标记） |
| GET | `/probes/budgets` | 预算快照；`provisional=true` 表示 P06 未确认 |
| POST | `/probes/budgets/close` | 关闭预算（`run_id` 或 `diagnosis_id`），记录关闭原因 |
| GET | `/runs/{run_id}/timeline` | Inbox 驱动的投影视图，`authority="derived_view"`，非业务权威 |
| GET | `/inbox/stats` | 消费状态计数（含 UNKNOWN） |
| POST | `/explorations/{id}/compile?mode=` | `rule_only / llm_only / llm_plus_rule_validation`；失败回退规则并在 `compiled.llm_meta.fallback=true` |
| POST | `/testcases/{id}/versions/{v}/stability-replay` | `repeats`（1..10）、`risk_profile`、`sync`；返回独立 Run 记录与 `promotable`（不稳定或 Oracle 不完整 → false） |
| GET | `/stability-replays`、`/{id}` | `refresh=true` 汇总最新 verdict 并重算分类 |
| GET | `/testcases/{id}/versions/{v}/stability-gate` | 审核条件只读预检：`satisfied` + `problems` + 回放证据摘要；与审核事务同一规则（含 Oracle 版本重核验） |
| POST | `/repairs/llm-suggest` | LLM 受限变更候选；`propose=true` 仅生成提案，不审批/不晋升 |
| GET | `/system/llm/summary` | 新增 `enabled.compiler_mode/probe_planner/repair_planner` 与各模块调用/兜底计数 |

**语义红线**：SSE 与时间线都是展示/派生通道；任何界面状态必须以 `/runs/{id}`、`/diagnoses/{id}` 等权威读接口为准。
