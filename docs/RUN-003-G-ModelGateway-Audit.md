# RUN-003-G ModelGateway 审查报告

> 审查时间：2026-11-22 21:30（GMT+8）
> 结论：已有接口清晰可复用，未重复造轮子；缺失真实 Provider 层，已按最小侵入补齐。

## 1. 已有模块（RUN-002 遗产，直接复用）

| 模块 | 位置 | 状态 | 与本轮关系 |
|---|---|---|---|
| `ModelGateway` 抽象接口（plan → ModelDecision） | `packages/agents/.../gateway/model_gateway.py` | 已有，接口稳定 | LLM planner 实现同构接口，可互换 |
| `RuleBasedPlanner`（rule-based-v1） | 同上 | 已有，诚实标注规则实现 | 作为 LLM 失败/预算耗尽时的兜底 |
| `ToolGateway`（权限/参数/域名/风险/预算五检） | `.../gateway/tool_gateway.py` | 已有 | **不变**；LLM 输出仍必须过网关 |
| `Explorer` 工作流（Trace + 预算 + 停止条件） | `.../explorer/workflow.py` | 已有 | 直接注入 LLM planner，trace.prompt_version 自动跟随 |
| `TestCaseCompiler`（规则编译器） | `.../compiler/compiler.py` | 已有 | 本轮未替换为 LLM（见 §4 边界） |
| 诊断引擎（确定性 CONFIRMED 规则 + CANDIDATE 状态 + `suggested_by` 字段） | `packages/evidence/.../diagnosis/engine.py` | 已有 | 引擎早已预留 `suggested_by="llm"`，并有防线：llm 假设不得 CONFIRMED |
| `DiagnosisService` | `apps/api/.../diagnosis_service.py` | 已有 | 追加 LLM 顾问步骤（不改判定规则） |
| 配置体系（TG_ 前缀 + `.env` + 密钥自动脱敏） | `packages/core/config.py` | 已有 | 新增 llm_* 字段复用该体系 |

## 2. 缺失能力（本轮补齐）

1. 真实 Provider 层：无任何真实模型调用代码（此前只有规则 planner）。
2. 双模型路由：无默认/推理模型区分。
3. Prompt 版本化管理：prompt 散落在 planner 内部，无版本。
4. Token/成本计量：无任何 usage 记录。
5. 预算与熔断：无。

## 3. 实施方案（已落地）

- `packages/agents/traceguard_agents/gateway/llm_gateway.py`（新增）：
  - `DeepSeekProvider`：OpenAI-compatible `/chat/completions`，JSON 输出，有限重试（429/5xx/网络），超时，错误映射 `LLMError(kind)`，`GET /models` 健康检查；
  - `ProviderRegistry` / `ModelRouter`（Flash 默认、Pro 需显式 reason 升级）/ `PromptManager`（版本化 + 系统消息固定在前 + 页面内容只作数据）；
  - `TokenUsageTracker`：JSONL 落盘（`.traceguard/llm_usage.jsonl`）+ 成本估算（价格查证 2026-11，全部标记 `cost_is_estimated=true`）+ 当日预算熔断 `should_skip_llm()`；
  - `LLMExplorerPlanner`：输出白名单动作 + 参数形状校验，任何失败 → 规则兜底且 reason 标记 `fallback(rule): ...`；
  - `LLMDiagnosisAdvisor`：仅产生 CANDIDATE 假设，`suggested_by="llm:<model>"`，虚构 evidence_id 剔除 + 域白名单 + confidence 截断。
- `apps/api/.../exploration_service.py`：按配置注入 LLM planner（`base_url` 提示），doc 记录 `engine` 与 `llm_stats`。
- `apps/api/.../diagnosis_service.py`：`_llm_advise()` 追加候选假设（诊断 doc 记录 `llm_advisor`，engine 变为 `rule-v1+llm-advisor`）。
- `packages/core/config.py`：新增 `deepseek_api_key`（`DEEPSEEK_API_KEY` / `TG_DEEPSEEK_API_KEY`）与 `TG_LLM_*` 配置；`public_dict()` 自动脱敏。
- `apps/api/.../tool_gateway.py`：只读工具（read_page/read_dom/screenshot）允许空参数（原本一刀切拒空参数导致 LLM 合法 read_page 被拒）；写工具检查不变。

## 4. 明确不做 / 边界

- Compiler 本轮保持规则实现：LLM 生成用例必须过 Schema + Oracle 关联审核，涉及正式资产晋升链路，
  按"先单模块后完整链路"原则放在 Explorer/Diagnosis 验证稳定之后再评估（如实记录，不冒进）。
- Self-Healing / Active Probe 的 LLM 辅助：同上，本轮未接（架构允许，接口已备好）。
- 不重试无限；不让模型自评升级 Pro；不让 LLM 接触 Ground Truth；不让 LLM 改变 CONFIRMED/审批/晋升边界。
