# RUN-020 技术实施方案：从"闭环完成"到"可度量的智能能力"

> 基线：`workbuddy/run019-product-completion@564b21c`（见 `RUN-020-PREPARATION-AUDIT.md`）。
> 本文件是**实施方案**，不是概念文档：每个工作包都给到**文件作用域、接口契约、数据结构、
> 失败语义、验收标准、测试策略、依赖与风险**，并可直接拆成任务卡
> （`docs/RUN-020-WORKPACKAGES.md`）。
>
> 三条不可谈判的约束（贯穿全文）：
> 1. **LLM 只产候选，不裁决事实**：不得决定 Test Verdict、不得审批 Oracle、不得审批/晋升 Repair；
> 2. **一切能力必须可度量**：固定数据集 + 独立 Ground Truth + 规则基线 + 多轮重复 + 失败样本归档；
> 3. **无凭据不伪造**：`RULE` / `FAKE_PROVIDER` / `REAL_PROVIDER` 三态必须显式落库、可区分。

## 0. 为什么这一轮不是"再修小问题"

RUN-011～RUN-019 已经把 11 个环节（Explorer → Compiler → TestCase → Oracle → Replay →
Diagnosis → Probe → Repair → Sandbox → Promotion → Regression）做成真实可跑的闭环，
并有三层 CI 门禁与 PG16 长稳证据。此时**继续加功能的边际收益极低**，真正的短板是：

| 短板 | 事实 | 后果 |
|---|---|---|
| 智能能力没有独立评测 | Compiler 只有"JSON Schema 合法率"；Diagnosis 的 Holdout 是**单轮、单标签、与规则同源**；Repair 的成功率来自少量演示用例 | 无法回答"模型到底行不行"，也无法判断换模型/改 Prompt 是变好还是变坏 |
| LLM 只在"顾问位" | `compiler_mode=rule_only`、`probe/repair planner=false`；Explorer/诊断顾问即使调用也只是候选 | 平台仍是"规则系统 + LLM 装饰"，不是智能系统 |
| 真实调用证据不闭环 | CI 无 Secret → 每轮 `BLOCKED`；本地 smoke 通过但不进实验口径 | Release 无法声明任何 AI 能力 |
| 没有统一的成本/延迟口径 | 只有 `llm_usage.jsonl` 与 `/system/llm/summary` 的粗聚合 | 无法做"质量/成本"取舍，也无法给预算决策 |

RUN-020 的目标因此定义为：**把已有的 11 个环节接上"可度量的智能"，而不是再建一套架构**。

## 1. 现状能力盘点（代码级，不重复建设）

| 能力 | 现有实现（文件） | 现状成熟度 | RUN-020 要补的缺口 |
|---|---|---|---|
| Explorer | `packages/agents/.../explorer/workflow.py`；`gateway/model_gateway.py`（`RuleBasedPlanner`）+ `gateway/llm_gateway.py`（`LLMExplorerPlanner`） | 真实可跑，规则/LLM 可切换，工具网关强制校验 | 规划质量未评测（无 holdout）；无"多步回退"策略评测 |
| Compiler（trace→用例） | `packages/agents/.../compiler/compiler.py`（`TestCaseCompiler`、`COMPILER_MODES`）；`gateway/llm_planners.py::LLMCompilerAdvisor` | 稳定：locator 优先级、断言来源、候选审核、稳定性回放 | **只支持"有 trace"**；无自然语言→步骤；无编译 holdout 与指标 |
| TestCase / Oracle | `packages/contracts/.../models.py`、`packages/execution/.../oracle/`、`apps/api/.../services/stability_service.py` | 版本不可变 + `authoring_hash` + 审核/绑定复核（R2 加固） | Oracle 候选生成质量未度量；断言"强度"无量化 |
| Replay / Stability | `stability_service.py`、`packages/execution/.../runtime/` | 多次独立 Run、不覆盖历史、不稳定不晋升 | 可作为 Compiler 可执行率的验证器（需接入） |
| Diagnosis | `packages/evidence/.../diagnosis/engine.py`（假设/状态机/多根因/弃权）、`apps/api/.../services/diagnosis_service.py`（规则生成）、`packages/evidence/.../graph/adjudication.py`（图裁决） | 真实：PERSISTENCE/TIMING/LOCATOR/API/NETWORK/AUTHORIZATION；多根因已支持；冲突→人工复核；LLM 假设强制降级 | 多故障/间歇/证据冲突的**系统性**覆盖不足；Holdout 单标签；缺 Recall/Precision/弃权率口径 |
| Probe | `apps/api/.../services/probe_service.py`、`packages/evidence/.../probe/` | 只读、作用域、预算、幂等、清理 | LLM 探针规划默认关；无规划质量指标 |
| Repair | `packages/agents/.../healing/{rules,service}.py`；`gateway/llm_planners.py::LLMRepairAdvisor`；`apps/api/.../services/repair_service.py` | 完整链路：规则校验 → 沙箱 → 审批 → 晋升（CAS+回执）→ 回归 → 回滚；来源可信（R2） | LLM 仅"顾问"；无 locator/断言的系统性修复策略；无"误修复自动拒绝"；无接受率指标 |
| Benchmark | `benchmark/traceguard_benchmark/{dataset,evaluation,scenarios_v4,runner}.py`；`scripts/holdout_benchmark_run019.py` | GT token 隔离、manifest 冻结、macro-F1/主因准确率/False Pass | 三个能力（编译/诊断/修复）**各一套口径**，未统一；无成本/延迟；无失败样本归档规范 |

**结论**：RUN-020 不新增"第 12 个环节"，只做三件事——**补能力（Compiler/Diagnosis/Repair 的智能路径）、
补度量（统一 Benchmark）、补治理（Provider 与审计）**。

## 2. 产品能力矩阵

能力等级定义（用于对外表述，避免"用了 LLM"就宣称智能）：

| 等级 | 定义 | 判定依据 |
|---|---|---|
| **L0 规则** | 纯确定性，无模型调用 | 输出 `provenance=RULE`，无 token 记录 |
| **L1 顾问** | LLM 提候选，规则裁决；失败可回落 | 输出带 `llm_meta` + `fallback_reason`；LLM 不得 CONFIRMED |
| **L2 主路径** | LLM 是主生成器，确定性校验 + 弃权；规则退为基线 | 同一输入在 RULE/LLM 两臂均可跑，结果可比 |
| **L3 可度量** | L2 + 冻结 Holdout + 多轮 + 成本/延迟 + 失败归档 + 门禁阈值 | 每次改动产出可复现实验报告 |

| 能力 | 现状 | RUN-020 目标 | 关键指标（首轮实测后校准，但口径先冻结） |
|---|---|---|---|
| Provider 接入 | L1（顾问位可用，CI 无 Secret） | **L3** | 调用可审计率 100%、三态区分 100%、超时/限流可复现 |
| Compiler（NL→用例） | **未实现**（仅 trace→用例） | **L2 + L3** | 步骤正确率、Locator 有效率、Oracle 正确率、可执行率、人工修改率、错误生成率 |
| Diagnosis（多故障） | L1（规则为主，LLM 顾问） | **L2 + L3** | Recall/Precision/Macro-F1、False Pass、False Positive、弃权率、主因准确率 |
| Repair | L1（规则修复，LLM 顾问） | **L1 + L3**（本轮不把 LLM 提为审批路径） | 修复成功率、误修复率、回归通过率、人工接受率 |
| Benchmark | 分散（三套口径） | **L3 统一** | 数据集版本固定、GT 独立、规则基线、成本/延迟、多轮、失败归档 |

> Repair 停留在 L1 是**刻意的**：修复会改动生产测试资产，本轮只允许 LLM 产 Proposal，
> 审批/晋升链路保持人工，先用量化数据证明"值得放开"，再谈 L2。

## 3. 技术架构

```
┌──────────────────────────────────────────────────────────────────────────┐
│ 度量层  benchmark/registry.py  ·  holdout manager  ·  metrics  ·  report  │
│         固定数据集版本 + manifest SHA-256 + GT token 隔离 + 成本/延迟      │
└───────────────▲──────────────────────────▲──────────────────▲────────────┘
                │ 实验记录（样本级）        │                  │
┌───────────────┴───────────┐  ┌───────────┴──────────┐  ┌────┴─────────────┐
│ WP2 Compiler              │  │ WP3 Diagnosis        │  │ WP4 Repair       │
│ nl_compiler / ambiguity   │  │ multifault/adjudicate│  │ locator/assertion│
│ （候选，不写正式资产）     │  │ （假设，不裁决事实）  │  │ （Proposal 而已） │
└───────────────▲───────────┘  └───────────▲──────────┘  └────▲─────────────┘
                │                          │                  │
┌───────────────┴──────────────────────────┴──────────────────┴─────────────┐
│ 能力基座（既有，不重写）                                                    │
│  ToolGateway · TestCaseCompiler · Oracle · Replay/Stability · Probe ·      │
│  Repair 状态机（DRAFT→…→PROMOTED/ROLLED_BACK）· Regression                 │
└───────────────▲────────────────────────────────────────────▲──────────────┘
                │                                            │
┌───────────────┴────────────────────────────────────────────┴─────────────┐
│ WP1 Provider 层（改造既有 llm_gateway）                                    │
│  ProviderAdapter(Protocol) · DeepSeekAdapter · FakeAdapter(仅测试)         │
│  RetryPolicy · StructuredValidator · CallAudit(DB+JSONL) · Budget/Cost     │
│  三态：RULE / FAKE_PROVIDER / REAL_PROVIDER                                │
└───────────────────────────────────────────────────────────────────────────┘
```

**分层职责（不可跨越）**：

| 层 | 可以 | 不可以 |
|---|---|---|
| Provider | 发请求、重试、解析、记账 | 决定业务语义、写业务表 |
| 能力 | 产候选（步骤/断言/假设/补丁） | 写正式 TestCase 版本、审批、晋升 |
| 治理 | 校验、沙箱、审批、晋升、回归、回滚 | 跳过人工审批 |
| 度量 | 读证据、算指标、归档失败样本 | 改 GT、删失败样本、改历史实验 |

## 4. 数据流与调用链

### 4.1 WP1：一次真实调用发生了什么

```
Service/Agent
  └─ build_llm_stack(settings) ──> ProviderRegistry.get(provider_name)
        └─ ProviderAdapter.chat_json(messages, model, agent, ids, schema_id)
              ├─ BudgetGuard.check(max_calls_per_run, daily_budget)   → 超限: LLMError(budget_exhausted)
              ├─ RetryPolicy.execute()
              │     ├─ POST /chat/completions（timeout, Retry-After 感知, 全抖动退避）
              │     └─ 429/5xx/网络 → 有限重试；401/403 → rejected（不重试）
              ├─ StructuredValidator.validate(payload, schema_id)
              │     ├─ 失败 → 一次"带错误回问"；仍失败 → LLMError(invalid_json)
              │     └─ 通过 → 返回 typed payload
              └─ CallAudit.write(LLMCallRecord)   ← 与结果同事务/同批次
                    ├─ 成功：provenance=REAL_PROVIDER, tokens/latency/cost
                    └─ 失败：status=error:<kind>, tokens=0, 保留错误类型
调用方
  └─ 失败 → FallbackPolicy：回落 RULE，并在输出写 provenance=RULE + fallback_reason
```

**关键不变量**：审计写入失败 → 该结果**不得**作为证据使用（`LLMError("audit_failed")`），
调用方只能走规则回落；不允许"调用了但没记账"。

### 4.2 WP2：自然语言需求 → 可审核候选

```
POST /intents/{id}/compile-draft
  └─ NLCompiler.compile(intent, app_profile)
        ├─ RequirementParser   : intent.description + business_context + preconditions
        │                        → RequirementGraph{entities[], actions[], assertions[]}
        ├─ PageObjectResolver  : app_profile(routes + locator_map) → 每个 action 的候选页面/元素
        │                        未命中 → Ambiguity(missing_locator)
        ├─ StepGenerator       : RequirementGraph × PageObject → TestStep[]
        ├─ OracleProposer      : AssertionSpec[] + expected 来源（business_api / ui_text）
        ├─ AmbiguityDetector   : 缺实体/缺字段/缺阈值/缺前置/多义路由/权限不明
        │                        阻断级 → CompileQuestion[]（必须回答后才允许下一步）
        └─ 返回 CompileDraft{draft_id, steps, assertions, questions[], provenance, llm_meta}

POST /intents/{id}/compile（仅在无阻断问题后）
  ├─ DraftValidator（契约 + locator 白名单 + 断言强度下限）
  ├─ 可执行性验证：候选 → 隔离 workspace → 真实 Replay × 2
  │     └─ 任一不通过：返回 executable=false + 失败证据，不创建候选
  └─ CatalogService.create_trusted_candidate(source=compiler, origin=compiler_flow)
        └─ review_status=DRAFT（仍需人工审核，不晋升）
```

### 4.3 WP3：多故障诊断

```
POST /runs/{id}/diagnose
  └─ DiagnosisService.collect_evidence(run)        （既有：步骤/网络/业务状态/延迟复测）
       └─ HypothesisBuilder（扩展）
             ├─ 单机制规则（既有 6 类）
             ├─ 多故障：同一 Run 内独立证据簇 → 每簇独立假设（不合并成一条）
             ├─ 证据冲突：TRUSTED 支持 vs TRUSTED 反驳 → 标记 conflict，不强行择一
             └─ 间歇：同用例历史分歧 → flaky_characteristic（既有）+ 多次执行要求
       └─ GraphAdjudicator（扩展 MultiFault）
             ├─ 权威确认/反驳判定（时间窗、完整性、来源独立）
             └─ 输出 root_cause_set / conflict_set / abstain_reason
       └─ DiagnosisEngine.diagnose（既有状态机 + 新增多根因排序）
             └─ outcome ∈ {CONFIRMED, PROBABLE, INCONCLUSIVE}；root_causes 可为多条
       └─ LLMDiagnosisAdvisor（可选）：只产 SUPPORTED 及以下假设，永不 CONFIRMED
```

### 4.4 WP4：修复提案

```
POST /repairs（LLM 参与时）
  └─ RepairPlanner（规则优先；LLM 仅当 llm_repair_planner_enabled）
        └─ 产出 Patch[]（路径白名单：locator.* / steps[i].value / assertions[i].expected）
  └─ PatchContract.validate
        ├─ 禁止：删除断言、放宽阈值、改 Oracle 版本、改断言 kind、跨用例改动
        └─ 违规 → VALIDATION_FAILED（reason=PATCH_CONTRACT_VIOLATED，不入沙箱）
  └─ RULE 校验（既有 RULE_SOURCE_CONTRACTS）→ 沙箱 Run（真实执行，隔离版本）
  └─ 人工审批（repair.approve）→ 晋升（CAS + 回执）→ Regression Run
        ├─ Regression PASSED → PROMOTED
        └─ Regression FAILED → 自动回滚（ROLLED_BACK）+ 误修复标记
```

### 4.5 WP5：统一实验

```
scripts/run020_benchmark.py --suite {compiler,diagnosis,repair} --arms rule,llm --repeats 3
  ├─ HoldoutLoader.load(dataset_id, version)  → 校验 manifest SHA-256（不符即拒绝运行）
  ├─ for arm, repeat, sample:  run  → SampleResult（含 tokens/latency/cost/verdict/root_causes）
  ├─ Metrics.compute(...)      → 指标 + 置信区间（bootstrap）
  ├─ FailureArchive.write(...) → 失败样本全量归档（只增不改）
  └─ Report.render(...)        → docs/RUN-020-BENCHMARK-REPORT.md + 机器可读 JSON
```

## 5. WP1：真实 LLM Provider 接入（L3）

### 5.1 现状与缺口

| 已有 | 缺口 |
|---|---|
| `DeepSeekProvider`（重试 2 次、超时、错误分类、usage、成本估算）；`ProviderRegistry`；`ModelRouter`；`PromptManager`；`TokenUsageTracker`（JSONL + 日预算）；`/system/llm/summary`；`scripts/llm_smoke.py`（11 项） | ①Provider 是**具体类**，registry 类型写死 DeepSeek；②无结构化 schema 校验（仅 `json.loads` + 个别键检查）；③审计只在 JSONL 文件，**无 DB 记录**，不能按 Run/Agent 查询；④重试不感知 `Retry-After`、无抖动；⑤三态 provenance 未统一落库；⑥预算只按日，缺少 per-run 硬闸（有 `max_calls_per_run` 但未覆盖全部路径） |

### 5.2 设计

**5.2.1 Provider 抽象**

```python
# packages/agents/traceguard_agents/gateway/providers/base.py
class ProviderAdapter(Protocol):
    name: str                      # "deepseek" | "fake"
    def chat(self, messages, *, model, agent, task_id="", run_id="", prompt_version="",
             max_tokens=1024, response_json=False) -> ChatResult: ...
    def chat_json(self, messages, *, schema_id: str, **kw) -> tuple[dict, ChatResult]: ...
    def health(self) -> ProviderHealth: ...     # ok / degraded / down + 最近错误
    def capabilities(self) -> ProviderCaps: ... # json_mode / reasoning / max_context
```

- `ProviderRegistry` 改为按 `name` 注册 `ProviderAdapter`，**不写死类型**；
- 新增第二个 provider 时只加一个 adapter 文件，不改调用方（用 `FakeAdapter` 验证这条契约）。

**5.2.2 重试与超时（可复现）**

| 错误 kind | 重试 | 退避 | 备注 |
|---|---|---|---|
| `rate_limit` (429) | 是 | 优先 `Retry-After`，否则 `min(2^n, 8s)` + 全抖动 | 计入 retry_count |
| `server` (5xx) | 是 | 同上 | 5xx 连续 3 次 → `degraded` 健康态 |
| `network` | 是 | 同上 | DNS/连接重置 |
| `timeout` | 是（≤1 次） | 不退避，直接重试 | 总耗时受 `timeout_s × (1+retries)` 约束 |
| `rejected` (401/403) | **否** | — | 凭据问题，立即上抛 |
| `invalid_json` | 1 次"带错误回问" | — | 仍失败 → 上抛 |
| `budget_exhausted` | **否** | — | 回落规则，不排队 |

**5.2.3 结构化输出校验**

- 新增 `gateway/schemas.py`：每类输出一个 **pydantic 模型**（`ExplorerPlan`、`CompileAdvice`、
  `DiagnosisHypotheses`、`ProbePlan`、`RepairPatch`），字段级约束 + `extra="forbid"`；
- `chat_json(..., schema_id=...)` 校验失败时：把 `validation_error` 摘要回问一次（不泄露 GT），
  第二次仍失败 → `LLMError("invalid_json")`；
- **不做"尽力修复"**（不猜测缺失字段、不把字符串强转列表）——错误必须显式。

**5.2.4 审计（DB + JSONL 双写）**

新表（迁移 v15）：

| 字段 | 类型 | 说明 |
|---|---|---|
| `call_id` | VARCHAR(64) PK | `llmcall_...` |
| `ts` | VARCHAR(40) | ISO8601 UTC |
| `provider` / `model` | VARCHAR(40) | `deepseek` / 实际返回模型 |
| `agent` | VARCHAR(40) | explorer / compiler / diagnosis / probe / repair / smoke |
| `intent_id` / `testcase_id` / `run_id` / `task_id` | VARCHAR(64) | 可空，用于串联 |
| `prompt_id` / `prompt_version` / `prompt_hash` | VARCHAR(120) | 可复现 |
| `input_tokens` / `output_tokens` / `cached_tokens` | INTEGER | 真实 usage |
| `latency_ms` / `retry_count` | INTEGER | 真实延迟 |
| `status` | VARCHAR(24) | `ok` / `error:<kind>` |
| `estimated_cost_usd` / `cost_is_estimated` | FLOAT / BOOLEAN | 成本口径 |
| `provenance` | VARCHAR(16) | **RULE / FAKE_PROVIDER / REAL_PROVIDER** |
| `request_fingerprint` | VARCHAR(64) | messages+model 的 SHA-256（不含明文） |

- JSONL 保留（离线排查），DB 为**权威**；
- `FakeAdapter` 必须在 `provenance=FAKE_PROVIDER` 落库，且被 `release_evidence` 过滤器排除；
- 审计写入失败 → `LLMError("audit_failed")`，结果不可用（fail-closed）。

**5.2.5 成本/延迟统计**

`GET /system/llm/summary` 扩展维度：`by_agent`、`by_model`、`by_day`、`p50/p95 latency`、
`error_rate`、`cost`、`budget_remaining`、`provenance_breakdown`。
新增 `GET /system/llm/calls?run_id=&agent=&limit=`（只返回审计字段，不含 prompt 明文）。

### 5.3 文件作用域

| 类型 | 路径 |
|---|---|
| 新增 | `packages/agents/traceguard_agents/gateway/providers/{__init__,base,deepseek,fake}.py` |
| 新增 | `packages/agents/traceguard_agents/gateway/validation.py`（schema 注册与校验） |
| 新增 | `packages/agents/traceguard_agents/gateway/audit.py`（`LLMCallRecord` / `AuditSink`） |
| 新增 | `packages/persistence/traceguard_persistence/llm_audit.py` |
| 修改 | `packages/persistence/traceguard_persistence/db.py`（迁移 v15） |
| 修改 | `packages/agents/.../gateway/llm_gateway.py`（适配新 Provider/审计，保留旧 API 兼容） |
| 修改 | `packages/core/traceguard_core/config.py`（`TG_LLM_PROVIDER_TIMEOUT_*`、`TG_LLM_AUDIT_ENFORCE`） |
| 修改 | `apps/api/traceguard_api/routers/system.py`（summary/calls） |
| 新增 | `apps/api/traceguard_api/services/llm_service.py`（聚合查询） |
| 测试 | `tests/unit/test_llm_provider_contract.py`、`test_llm_structured_output.py`、`test_llm_retry_policy.py`；`tests/integration/test_llm_audit_persist.py` |

### 5.4 验收标准（DoD）

| # | 条件 | 验证方式 |
|---|---|---|
| 1 | 新增第二个 Provider（Fake）不改调用方 | `test_llm_provider_contract.py` 用 FakeAdapter 跑通 explorer/compiler/diagnosis 三条链 |
| 2 | 6 类错误语义可复现 | `test_llm_retry_policy.py` 用 httpx MockTransport 覆盖 429/5xx/网络/超时/401/invalid_json |
| 3 | 结构化校验拒绝非法输出 | 缺字段/多字段/类型错 → `invalid_json`，不静默修复 |
| 4 | 每次调用有 DB 审计 | 集成测试：调用 3 次 → `tg_llm_call` 3 行，字段完整，`provenance=REAL_PROVIDER` |
| 5 | 三态可区分 | Fake 调用落 `FAKE_PROVIDER`；规则路径无 LLM 行；`/system/llm/summary` 分列 |
| 6 | 审计不可绕过 | 注入写失败 sink → 调用返回 `audit_failed`，不返回内容 |
| 7 | 预算硬闸 | 超过 `max_calls_per_run` → `budget_exhausted`，调用方回落 RULE 且标记 |
| 8 | 真实 smoke 仍 11/11 | `scripts/llm_smoke.py` |

## 6. WP2：Compiler 质量提升（L2 + L3）

### 6.1 现状与缺口

- `TestCaseCompiler.compile(trace, intent)`：**输入必须有 trace**（Explorer 产出的动作序列）；
- `LLMCompilerAdvisor.suggest(trace, intent)`：在 trace 基础上补步骤/断言，仍是"有 trace"路径；
- `COMPILER_MODES = {rule_only, llm_only, llm_plus_rule_validation}` 已存在，但默认 `rule_only`，
  且 `llm_only` 没有质量数据支撑；
- **没有**"自然语言业务需求 → 步骤"的路径，也没有编译质量评测。

### 6.2 设计要点

**6.2.1 需求拆解（RequirementParser）**

输入：`TestIntent{title, description, business_context, preconditions, candidate_assertions}`。
输出 `RequirementGraph`：

| 字段 | 说明 |
|---|---|
| `entities[]` | 业务实体（如 `project`, `task`, `member`）+ 来源片段 |
| `actions[]` | 业务动作（create/update/submit/approve/delete）+ 目标实体 + 前置条件 |
| `assertions[]` | 期望（UI 文案 / 业务状态 / 计数变化）+ 来源（intent 明示 / 推断） |
| `unknowns[]` | 未在 intent 中出现但生成所必需的槽位（字段名、阈值、路由、权限） |

**6.2.2 页面对象识别（PageObjectResolver）**

- 数据源：`app_profile`（新概念，见 §6.3）——路由清单、页面名、可交互元素与**已知 locator**；
- **禁止凭空造 locator**：只允许从 `app_profile.locator_map` 选，或对已见元素使用
  `role/name` 派生；无法确定 → 产出 `Ambiguity(missing_locator)`；
- 优先级沿用既有：`role > test_id > label > placeholder > css > xpath`，并记 `stability_score`。

**6.2.3 歧义检测与主动询问（本轮必须做，不是可选项）**

| 歧义类型 | 触发条件 | 处理 |
|---|---|---|
| `MISSING_ENTITY` | 动作没有明确对象（"提交表单"但没说是哪个） | 阻断：必须回答 |
| `MISSING_FIELD` | 创建类动作缺必填字段 | 阻断 |
| `MISSING_THRESHOLD` | "金额较大""超过限制"无具体值 | 阻断（不得猜数值） |
| `MISSING_PRECONDITION` | 需要登录/角色/初始数据但未声明 | 阻断 |
| `AMBIGUOUS_ROUTE` | 同一动作对应多个页面 | 阻断（给出候选让用户选） |
| `MISSING_ORACLE` | 只有步骤没有可验证的期望 | 阻断（Oracle 不能用"页面能打开"充数） |
| `OPTIONAL_DETAIL` | 文案/顺序等非阻断差异 | 非阻断：记录 + 使用默认 |

阻断级问题 → `CompileQuestion{code, question, options[], blocking=true}`；
**在问题未回答前，`POST /compile` 返回 409 `COMPILE_BLOCKED_BY_AMBIGUITY`**，
不允许"先生成再让人改"（那会把不确定性藏进产物）。

**6.2.4 可执行性验证（生成结果必须真跑）**

- 候选 → 隔离 workspace → **真实 Replay × 2**（复用 `stability_service` 的 Run 机制，不新建执行器）；
- 判定：两次都 `COMPLETED + PASSED` 且 locator 全部解析成功 → `executable=true`；
- 任一次失败 → 返回 `executable=false` + 失败步骤 + 证据引用，**不创建候选**；
- 这一步是"可执行率"指标的来源，也是防止 LLM 产出"看起来对但跑不了"的关键。

**6.2.5 独立 Holdout（Compiler 专用，与诊断 Holdout 完全隔离）**

| 项 | 设计 |
|---|---|
| 数据集 | `compiler-holdout-v1`：≥40 条 intent（覆盖 6 类页面 × 4 类动作 × 3 类断言） |
| 每条样本 | `intent`（自然语言）+ `app_profile` + `expected_steps`（规范化形式）+ `expected_assertions` + `ambiguities[]` |
| GT 隔离 | 与 `GroundTruthManager` 同机制：评测 token 才可读；生成侧（prompt 构造）**不可见** |
| 冻结 | `manifest.json` 记录每条样本 SHA-256 + 数据集版本；加载时校验，不符即拒绝 |
| 污染控制 | holdout 不进 `docs/`、不进 prompt 示例、不进 few-shot；`dev` 集单独 10 条可看 |

**6.2.6 指标（口径先冻结）**

| 指标 | 定义 | 计算方式 |
|---|---|---|
| 步骤正确率 | 期望步骤被正确生成的比例 | 规范化后按 `(action, target, value)` 做**有序对齐**（LCS），`matched/expected` |
| Locator 有效率 | 生成的 locator 在真实回放中能解析的比例 | 回放日志中 `resolved/total` |
| Oracle 正确率 | 断言集合与期望一致（kind + 语义值） | 精确匹配的样本比例；`expected` 值允许类型等价 |
| 可执行率 | 首次生成即"×2 回放通过"的比例 | `executable=true / 样本数` |
| 人工修改率 | 审核时人工改动的步骤/断言占比 | 审核前后 diff 的元素数 / 总元素数 |
| 错误生成率 | 生成"危险或非法"步骤的比例 | 越权动作、删除类动作无确认、断言强度低于下限、非法 locator |

> **不允许**用 "JSON Schema 合法率" 代替上述任一指标（合法 ≠ 正确 ≠ 可执行）。

### 6.3 新增概念：`app_profile`

```json
{
  "profile_id": "teamflow-v1",
  "base_url": "http://127.0.0.1:8011",
  "auth": {"roles": ["qa-engineer", "qa-lead"], "login_route": "/login"},
  "routes": [{"path": "/projects", "page": "project_list", "requires_role": "qa-engineer"}],
  "locator_map": {
    "project_list.create_button": {"strategy": "test_id", "value": "project-create"},
    "project_form.name": {"strategy": "label", "value": "项目名称"}
  },
  "business_api": {"project_state": "/api/v1/projects/{id}"}
}
```

- 来源：人工维护 + Explorer 观测沉淀（只允许"已见元素"进 map）；
- 版本化：`profile_id + version`，编译产物记录使用的 profile 版本（可复现）。

### 6.4 文件作用域

| 类型 | 路径 |
|---|---|
| 新增 | `packages/agents/traceguard_agents/compiler/nl_compiler.py` |
| 新增 | `packages/agents/traceguard_agents/compiler/requirements.py` |
| 新增 | `packages/agents/traceguard_agents/compiler/ambiguity.py` |
| 新增 | `packages/agents/traceguard_agents/compiler/app_profile.py` |
| 修改 | `packages/agents/traceguard_agents/compiler/compiler.py`（复用 locator/断言构造，不改既有行为） |
| 修改 | `packages/agents/.../gateway/llm_planners.py`（新增 `compile_nl@v1` prompt 与 advisor） |
| 新增 | `apps/api/traceguard_api/routers/intelligence.py` 新端点（§6.5） |
| 新增 | `benchmark/traceguard_benchmark/compiler_holdout.py` |
| 新增 | `benchmark/datasets/compiler-holdout-v1/{cases.jsonl,manifest.json,README.md}` |
| 新增 | `scripts/compiler_holdout_run020.py` |
| 测试 | `tests/unit/test_compiler_ambiguity.py`、`test_compiler_requirements.py`；`tests/integration/test_nl_compile_flow.py` |

### 6.5 API 契约

| 方法 | 路径 | 说明 | 关键错误 |
|---|---|---|---|
| POST | `/api/v1/intents/{intent_id}/compile-draft` | 生成草稿 + 问题清单 | 404、409 `COMPILE_BLOCKED_BY_AMBIGUITY`（仅当请求要求直接编译） |
| POST | `/api/v1/intents/{intent_id}/compile` | 校验 + 真实回放 + 创建候选 | 409（未答问题）、422（契约）、412（回放不通过） |
| GET | `/api/v1/intents/{intent_id}/compile-questions` | 当前阻断问题 | 404 |

权限：`testcase.create`（qa-engineer+）；候选仍需 `testcase.review`（qa-lead+）审核，不自动晋升。

### 6.6 验收标准（DoD）

| # | 条件 |
|---|---|
| 1 | 6 类歧义全部可被检测，且阻断级问题**必然**拦住编译（有反例测试） |
| 2 | 生成结果 100% 经真实回放验证；`executable=false` 时不产生候选 |
| 3 | Holdout 加载校验 manifest SHA-256；篡改任一字符 → 拒绝运行 |
| 4 | 规则臂与 LLM 臂都能在同一 Holdout 上跑（可对比） |
| 5 | 6 个指标全部产出，报告含样本级明细与失败归档 |
| 6 | 不写正式资产：候选 `review_status=DRAFT`，来源 `compiler_flow`（R2-2 的可信入口） |
| 7 | 负结果照实报告（若 LLM 臂低于规则臂，写明"无提升"并给失败样本） |

## 7. WP3：Diagnosis 真实故障能力（L2 + L3）

### 7.1 现状与缺口

已有：6 类机制规则（PERSISTENCE/TIMING/UI_RENDERING/API/AUTHORIZATION/NETWORK/LOCATOR）、
多根因（`root_causes` 可为多条）、冲突 → `MANUAL_REVIEW`、LLM 假设强制降级、
图裁决（时间对齐/完整性/来源独立）。

缺：**系统性覆盖**以下 7 类难例；Holdout 单标签；缺 Recall/Precision/弃权率口径。

### 7.2 难例矩阵（每类至少 4 条 Holdout 样本）

| # | 难例 | 期望行为 | 判定依据 |
|---|---|---|---|
| D1 | 多根因（如网络超时 + 业务未持久化） | 输出 ≥2 条 CONFIRMED，不合并 | `root_causes` 集合与 GT 集合一致 |
| D2 | 证据冲突（TRUSTED 支持 vs TRUSTED 反驳） | 不择一；`unresolved_conflicts` 非空 → 人工复核 | outcome=PROBABLE/INCONCLUSIVE + conflict 记录 |
| D3 | 间歇性故障（同用例 3 次 1 失败） | 标 `flaky`，PERSISTENCE 单次确认降级 | `flaky_characteristic=true` |
| D4 | 业务状态正确但 UI 失败 | `UI_RENDERING`（且无 5xx 时方可 CONFIRMED） | mechanism=UI_RENDERING |
| D5 | UI 成功但业务状态错误 | `PERSISTENCE`（False Pass 风险） | mechanism=PERSISTENCE + 业务证据链 |
| D6 | 延迟/最终一致性（即时缺失、延迟恢复） | `TIMING`，且必须在 Run 窗口内 | mechanism=TIMING + 时间窗证据 |
| D7 | 多个独立故障并发（不同步骤） | 每条独立假设，分别给证据 | 多根因集合 |

### 7.3 设计

1. **HypothesisBuilder**：把"证据簇"显式化——每个独立失败签名（step + 机制 + 时间窗）生成一个
   假设，避免把多故障压成一条"复合"假设；
2. **MultiFaultAdjudicator**（扩展 `GraphAdjudicator`）：
   - 输入：假设 + 证据元数据（trust/时间/来源/独立性）；
   - 输出：每条假设 `authority_confirmed/refuted`、`conflict`、`independent_support_count`；
   - 规则：同一证据不得同时支持两条互斥机制（重复计数检测）；无独立支持 → 不 CONFIRMED；
3. **弃权（Abstention）显式化**：新增 `abstain_reason` 枚举
   （`NO_TRUSTED_EVIDENCE` / `CONFLICTING_EVIDENCE` / `TIME_MISMATCH` / `INSUFFICIENT_REPEATS` /
   `OUT_OF_SCOPE`），弃权率可计算，不再用 `INCONCLUSIVE` 一个桶装所有情况；
4. **LLM 顾问（可选）**：只在 `NO_TRUSTED_EVIDENCE` 且预算允许时调用，输出假设
   `suggested_by=llm`，**永不 CONFIRMED**（既有约束，保留并加测试）；
5. **主因排序规则**（用于 Primary Cause Accuracy）：`CONFIRMED` 中按
   (证据独立支持数, 机制优先级 PERSISTENCE > API > AUTHORIZATION > LOCATOR > TIMING > UI_RENDERING > NETWORK)
   排序，取第一条为 primary；规则写进文档并单测，禁止"事后调参"。

### 7.4 Holdout 设计

| 项 | 设计 |
|---|---|
| 数据集 | `diagnosis-holdout-v3`：≥56 样本 = 7 类难例 × 4 × 2 轮 |
| GT | **多标签**：`expected_root_causes[]`（1–3 条，含机制 + 主因标记） |
| 来源分层 | `source: synthetic`（注入器）与 `source: real`（真实 Regression/Run 失败）**分开统计**，不混算 |
| 冻结 | manifest SHA-256 + 版本号；与 RUN-018/RUN-019 Holdout **完全隔离**（不修改旧集） |
| 重复 | 每条样本 ≥2 轮（间歇类 ≥3 轮），报告给出每轮一致性 |

### 7.5 指标

| 指标 | 定义 |
|---|---|
| Recall / Precision / Macro-F1 | 以"机制标签集合"为单位（多标签） |
| False Pass | 业务失败但判定 PASSED 的比例（硬门禁：必须 0） |
| False Positive | 正常样本被判为故障的比例 |
| Abstention Rate | 弃权样本 / 应判样本（按 `abstain_reason` 分列） |
| Primary Cause Accuracy | 主因（按 §7.3 规则投影）与 GT 主因一致的比例 |
| Multi-root Exact Match | 多根因集合完全一致的比例 |

### 7.6 文件作用域

| 类型 | 路径 |
|---|---|
| 新增 | `packages/evidence/traceguard_evidence/diagnosis/multifault.py` |
| 新增 | `packages/evidence/traceguard_evidence/diagnosis/abstention.py` |
| 修改 | `packages/evidence/.../diagnosis/engine.py`（主因排序、弃权原因，不改既有判定语义） |
| 修改 | `packages/evidence/.../graph/adjudication.py`（独立性/冲突计数） |
| 修改 | `apps/api/traceguard_api/services/diagnosis_service.py`（簇化、难例规则、弃权原因落库） |
| 新增 | `benchmark/traceguard_benchmark/diagnosis_holdout.py` |
| 新增 | `benchmark/datasets/diagnosis-holdout-v3/*` |
| 新增 | `scripts/diagnosis_holdout_run020.py` |
| 测试 | `tests/unit/test_multifault_adjudication.py`、`test_abstention_reasons.py`；`tests/integration/test_multi_root_diagnosis.py` |

### 7.7 验收标准（DoD）

| # | 条件 |
|---|---|
| 1 | 7 类难例各有 ≥4 条样本，且至少 1 条能真实复现（非纯构造） |
| 2 | 多根因：`root_causes` 支持多条，且不把互斥机制重复计数（有反例测试） |
| 3 | 冲突：不强行择一，进入 `unresolved_conflicts` 并给出弃权原因 |
| 4 | 弃权率按原因分列，且 `NO_TRUSTED_EVIDENCE` 不得被当作"通过" |
| 5 | False Pass 硬门禁 = 0（有测试守住） |
| 6 | LLM 假设永不 CONFIRMED（既有测试保留 + 新增多故障场景） |
| 7 | 合成 / 真实故障分层统计，报告不混算 |

## 8. WP4：Repair 智能修复能力（L1 + L3）

### 8.1 现状与缺口

已有完整治理链（Proposal → 规则校验 → 沙箱 → 审批 → 晋升 CAS+回执 → 回归 → 回滚），
规则修复（`healing/rules.py`）+ LLM 顾问（`LLMRepairAdvisor`，默认关）。

缺：locator/步骤/断言修复的系统策略；"错误修复自动拒绝"；接受率/误修复率指标。

### 8.2 设计

**8.2.1 修复策略（按优先级，规则优先）**

| 场景 | 策略 | 约束 |
|---|---|---|
| DOM 结构变化（test_id 失效） | `role + accessible_name` → `label` → `css`（仅当唯一） | 不得跨页面；`stability_score` 必须提升或不降 |
| 元素重命名/移动 | 从 `app_profile.locator_map` 重新解析 | 只允许 profile 内元素 |
| 等待不足（时序） | 插入显式 `wait`（条件等待，非固定 sleep） | 最多 1 处/步骤；不得全局延长超时 |
| 步骤顺序错误 | 重排（仅相邻、保持断言语义） | 不得删步骤 |
| 断言值变更（业务文案） | 仅当 Oracle 版本允许且 `expected` 类型一致 | 不得放宽断言 kind/阈值 |
| Oracle 不兼容 | 拒绝修复，转 `ORACLE_INCOMPATIBLE` | 不允许改 Oracle 版本 |

**8.2.2 LLM 补丁契约（`PatchContract`）**

```python
ALLOWED_PATHS = {
    r"steps\[\d+\]\.target\.(strategy|value)",
    r"steps\[\d+\]\.value",
    r"steps\[\d+\]\.wait\.(timeout_ms|condition)",
    r"assertions\[\d+\]\.expected",
}
FORBIDDEN = {"remove_assertion", "change_assertion_kind", "widen_threshold",
             "cross_testcase", "oracle_version_change", "global_timeout"}
MAX_CHANGES = 5
```

- 违规 → `VALIDATION_FAILED`（`reason=PATCH_CONTRACT_VIOLATED`），**不入沙箱**；
- LLM 必须给出 `evidence_refs`（指向具体证据），无证据的补丁直接拒绝。

**8.2.3 错误修复自动拒绝**

| 触发 | 动作 |
|---|---|
| 沙箱 Run 未 PASSED | 自动 `VALIDATION_FAILED`，记录 `SANDBOX_<verdict>` |
| 断言被删除/放宽 | 自动 `REJECTED`（`ASSERTION_WEAKENED`） |
| 改动超过 5 处 / 跨用例 | 自动 `REJECTED`（`PATCH_SCOPE_EXCEEDED`） |
| 晋升后 Regression FAILED | **自动回滚** `ROLLED_BACK`，标 `MISREPAIR` |
| 连续 2 次同类误修复 | 该规则/策略进入 `quarantine`，需人工复核后恢复 |

**8.2.4 指标**

| 指标 | 定义 |
|---|---|
| 修复成功率 | `PROMOTED` 且 Regression `PASSED` / 提交的 Proposal 数 |
| 误修复率 | 晋升后 Regression `FAILED` 或 Oracle 冲突 / `PROMOTED` 数 |
| 回归通过率 | Regression `PASSED` / 发起回归数 |
| 人工接受率 | `APPROVED` / 进入审批的 Proposal 数 |
| 自动拒绝命中率 | 被自动拒绝的错误补丁 / 全部错误补丁（人工标注集） |

### 8.3 文件作用域

| 类型 | 路径 |
|---|---|
| 新增 | `packages/agents/traceguard_agents/healing/locator_repair.py` |
| 新增 | `packages/agents/traceguard_agents/healing/assertion_repair.py` |
| 新增 | `packages/agents/traceguard_agents/healing/patch_contract.py` |
| 修改 | `packages/agents/traceguard_agents/healing/service.py`（策略选择，不改状态机） |
| 修改 | `apps/api/traceguard_api/services/repair_service.py`（PatchContract 校验、自动拒绝、误修复回滚标记） |
| 修改 | `packages/agents/.../gateway/llm_planners.py`（`repair_planner@v2`：强制 evidence_refs） |
| 新增 | `benchmark/traceguard_benchmark/repair_bench.py` |
| 新增 | `scripts/repair_bench_run020.py` |
| 测试 | `tests/unit/test_patch_contract.py`、`test_locator_repair.py`；`tests/integration/test_repair_auto_reject_run020.py` |

### 8.4 验收标准（DoD）

| # | 条件 |
|---|---|
| 1 | LLM 产出 100% 经 `PatchContract`；违规补丁 0 进入沙箱 |
| 2 | 断言弱化 0 通过（有专项反例测试） |
| 3 | 晋升后 Regression FAILED → 自动回滚且标 `MISREPAIR`（真实 Run 验证） |
| 4 | 不能直接改生产资产：所有变更经 Proposal/Sandbox/审批/晋升/回归/回滚（有越权反例测试） |
| 5 | 四项指标可从实验数据计算，报告含失败样本 |
| 6 | 默认配置不变：`TG_LLM_REPAIR_PLANNER_ENABLED=false`（需显式开启才产生成本） |

## 9. WP5：Benchmark 统一评估（L3）

### 9.1 设计

**9.1.1 统一注册表**

```python
@dataclass(frozen=True)
class DatasetRef:
    dataset_id: str        # compiler-holdout-v1 / diagnosis-holdout-v3 / repair-bench-v1
    version: str
    manifest_sha256: str   # 加载时强校验

@dataclass
class ExperimentSpec:
    suite: str             # compiler | diagnosis | repair
    dataset: DatasetRef
    arms: list[str]        # rule / llm / llm_plus_rules
    repeats: int           # >= 3（诊断间歇类 >= 3）
    budget_usd: float
    seed: int
```

**9.1.2 样本级结果（可查询、可复算）**

| 字段 | 说明 |
|---|---|
| `experiment_id` / `arm` / `case_id` / `repeat` | 主键维度 |
| `verdict` / `root_causes[]` / `primary_cause` | 能力输出 |
| `gt_match`（bool）/ `matched_labels[]` | 与 GT 对照结果 |
| `latency_ms` / `input_tokens` / `output_tokens` / `cost_usd` | 成本与延迟 |
| `provenance` | RULE / FAKE_PROVIDER / REAL_PROVIDER |
| `failure_reason` | 失败归因（执行/契约/超时/预算） |

**9.1.3 硬约束（代码强制，不靠自觉）**

| 约束 | 实现 |
|---|---|
| 数据集版本固定 | `ExperimentSpec.dataset.version` 必填；加载时校验 manifest SHA-256，不符 → 拒绝运行 |
| GT 独立 | 生成侧只能拿 `case_id + 输入`；GT 通过 `GroundTruthManager` token 读取，调用栈可审计 |
| 不允许改 GT | GT 文件只读 + manifest 校验；写入路径仅评测端 |
| 不允许删失败样本 | `FailureArchive` 只追加；同 `(experiment, case, repeat)` 重复写入 → 覆盖被拒（409） |
| 规则基线必须存在 | `arms` 必须包含 `rule`，否则拒绝 |
| 成本可核算 | 每个样本的成本来自 `tg_llm_call`，缺失 → 标 `COST_UNKNOWN`，不按 0 计 |

**9.1.4 统计口径**

- 多轮重复：报告每轮 + 合并（mean ± 95% bootstrap CI），**不做显著性宣称**当 n<30；
- 负结果必须保留：Δ≤0 时写"无提升/负增益"，并给失败样本链接；
- 合成 vs 真实分层：`source` 字段分组统计，禁止混算成一个数。

### 9.2 文件作用域

| 类型 | 路径 |
|---|---|
| 新增 | `benchmark/traceguard_benchmark/registry.py` |
| 新增 | `benchmark/traceguard_benchmark/archive.py`（FailureArchive，只追加） |
| 新增 | `benchmark/traceguard_benchmark/report.py` |
| 修改 | `benchmark/traceguard_benchmark/evaluation.py`（补 `precision/recall/abstention/primary_cause`） |
| 修改 | `packages/persistence/.../db.py`（迁移 v16：`tg_bench_sample`） |
| 新增 | `apps/api/traceguard_api/routers/benchmark.py` 端点（datasets / experiments v2 / samples） |
| 新增 | `scripts/run020_benchmark.py`（统一入口） |
| 新增 | `docs/RUN-020-BENCHMARK-REPORT.md`（由脚本生成） |
| 测试 | `tests/unit/test_benchmark_registry.py`、`test_metrics_multilabel.py`；`tests/integration/test_holdout_manifest_guard.py` |

### 9.3 验收标准（DoD）

| # | 条件 |
|---|---|
| 1 | 三个 suite 都能用同一入口跑（`--suite compiler|diagnosis|repair`） |
| 2 | 篡改 manifest → 运行被拒绝（有测试） |
| 3 | 缺 `rule` 臂 → 拒绝运行（有测试） |
| 4 | 重复写同一失败样本 → 被拒（有测试） |
| 5 | 报告含：指标、CI、成本、延迟、失败样本、负结果 |
| 6 | 任一样本可回溯到 `run_id` / `call_id` / 证据路径 |

## 10. 数据模型与迁移

| 版本 | 表 | 用途 | 回滚 |
|---|---|---|---|
| v15 | `tg_llm_call` | LLM 调用审计（权威） | 只增表，旧代码不读 → 无需回滚 |
| v16 | `tg_bench_sample` | 实验样本级结果 | 同上 |
| v16 | `tg_bench_archive` | 失败样本归档（只追加） | 同上 |

- 迁移必须幂等（`IF NOT EXISTS` + 迁移表判重），SQLite / PG16 双跑；
- 索引：`tg_llm_call(run_id)`、`(agent, ts)`、`(provider, model, ts)`；
  `tg_bench_sample(experiment_id, arm, case_id)`；
- 不做破坏性变更（无 DROP/无类型收窄）。

## 11. Holdout 治理（防止"改 GT 提指标"）

| 规则 | 执行方式 |
|---|---|
| 冻结 | manifest 记录版本 + 每条 SHA-256 + 总数；加载强校验 |
| 隔离 | GT 只在评测进程可读；生成进程只读输入；LLM prompt 不含 GT |
| 不可改 | GT 目录设置只读（脚本 `chmod`），提交前 CI 校验 manifest |
| 不可删 | 失败样本归档只追加；`git diff` 检查（CI 拒绝删除 holdout 样本的 PR） |
| 轮换 | 每个 holdout 最多用 2 轮；第 3 轮必须新建 vN+1，旧集归档不删 |
| 记录 | 每次运行写 `experiments/{id}/spec.json`（含参数、seed、预算、HEAD） |

## 12. 测试策略

| 层 | 内容 | 门禁 |
|---|---|---|
| unit | Provider 契约/重试/校验；PatchContract；歧义检测；多标签指标；manifest 校验 | PR Gate（<60s） |
| contract | 新 API 的请求/响应 schema；provenance 字段；错误码 | PR Gate |
| integration | 审计落库；Holdout 拒绝篡改；自动拒绝/自动回滚；多根因诊断；NL 编译（Fake Provider） | PR Gate（fast）+ Nightly |
| e2e | 真实 Replay 验证编译可执行性；真实浏览器修复沙箱；真实 LLM smoke（opt-in） | Nightly |
| 实验 | 三个 suite × 规则/LLM 臂 × ≥3 轮 | 手动 dispatch（成本受控） |
| 回归 | 既有 1144 passed 不得下降；ruff/format；前端 typecheck/build | PR Gate + Nightly |

**成本控制**：CI 默认 `TG_LLM_PROVIDER=""`（不调用）；`llm-smoke` 有 Secret 才跑；
实验 job 必须显式 `--budget-usd`，超预算即停（`budget_exhausted`），不允许"跑完再说"。

## 13. 外部依赖与阻塞

| 依赖 | 谁提供 | 阻塞范围 | 无它时的降级 |
|---|---|---|---|
| `DEEPSEEK_API_KEY`（CI 仓库 Secret） | 项目负责人 | CI 真实 LLM、实验 LLM 臂 | 只跑规则臂；LLM 臂标 BLOCKED |
| Actions `workflow_dispatch` 权限 | 仓库管理员 | Reliability / 实验 dispatch | 本地执行并如实标 NOT_RUN（不替代 CI） |
| PG16（本地或容器） | 环境 | PG 专项（16 条本地 skip） | CI `postgres` Job 覆盖 |
| Chromium | 环境 | UI/回放/编译可执行性验证 | 无法验证 → 该项标 BLOCKED，不伪造 |
| 预算授权（实验成本） | 项目负责人 | 三 suite × 多轮 | 先跑小样本（n=8）并标注样本量 |
| 人工评审（修复接受率） | qa-lead | 人工接受率指标 | 指标标 NOT_MEASURED，不估算 |
| LICENSE 决策 | 项目负责人 | 对外发布 | 仅内部使用 |

## 14. 风险登记册

| # | 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|---|
| R-01 | 把 LLM 输出当事实（越权裁决） | 低 | 极高 | 既有不变量 + 新增反例测试（LLM 不得 CONFIRMED / 不得晋升） |
| R-02 | Holdout 被"调参污染" | 中 | 高 | 冻结 manifest + 生成侧不可见 + 轮换 + 只读 |
| R-03 | 指标口径被事后调整 | 中 | 高 | 指标定义先写进文档并单测；报告引用版本号 |
| R-04 | 实验成本失控 | 中 | 中 | per-run 调用上限 + 日预算 + 实验预算硬闸 |
| R-05 | Provider 单点（只有 DeepSeek） | 中 | 中 | ProviderAdapter 抽象 + Fake 契约测试；新增 provider 不改调用方 |
| R-06 | 编译产物"能跑但没意义" | 中 | 高 | 断言强度下限 + Oracle 正确率 + 人工审核（不自动晋升） |
| R-07 | 证据资产继续膨胀（已 241.7 MB） | 高 | 中 | 新证据"摘要入库 + 原始走 artifact"；重复/同字节截图检测进 CI |
| R-08 | 多故障规则过度合并 | 中 | 中 | 独立证据簇 + 互斥机制校验 + 多根因 exact match 指标 |
| R-09 | 自动回滚误伤 | 低 | 高 | 回滚仅针对本次晋升；回执幂等；回滚后保留原版本 |
| R-10 | 真实故障样本不足 | 高 | 中 | 合成 + 真实分层，真实样本从 Regression 失败中沉淀；不足时如实标注 |
| R-11 | 修复策略"看起来对"但破坏语义 | 中 | 高 | PatchContract 白名单 + 断言弱化检测 + 沙箱 + 回归 + 回滚 |
| R-12 | 无 CI Secret 导致能力无法声明 | 高 | 高 | 本地 REAL_PROVIDER 证据 + CI 明确 BLOCKED；不混用 |

## 15. 里程碑与完成定义

| 里程碑 | 内容 | 完成定义（DoD） |
|---|---|---|
| **M0（本轮）** | 审计 + 准入 + 设计 + 工作包 | 4 份文档入库；审计发现的不一致已修复；无代码改动 |
| M1 | WP1 Provider/审计/校验 | §5.4 八条全过；`llm_smoke` 11/11；审计表可查 |
| M2 | WP2 NL Compiler + Holdout | §6.6 七条全过；compiler 报告首版（含规则/LLM 两臂） |
| M3 | WP3 多故障诊断 + Holdout v3 | §7.7 七条全过；诊断报告首版（分层统计） |
| M4 | WP4 Repair 策略 + 自动拒绝 | §8.4 六条全过；修复报告首版（四项指标） |
| M5 | WP5 统一 Benchmark | §9.3 六条全过；`RUN-020-BENCHMARK-REPORT.md` 生成 |
| M6 | 发布准入复评 | Reliability + CI LLM + PR 评审三项齐备 |

**M1 之前不改任何 Prompt 版本**：所有 Prompt 变更必须在 M1 的审计与校验落地后进行，
否则"改了什么都说不清"。

## 16. 复用清单（明确不重写）

| 能力 | 复用 | 不重写的原因 |
|---|---|---|
| 执行内核 / Worker / 队列 | `packages/execution`、`apps/workers` | 已通过 62 分钟长稳与 fencing 验证 |
| 修复治理链 | `healing/service.py` + `repair_service.py` | 状态机、CAS、回执、回滚已完备 |
| 证据与图 | `packages/evidence` | 图裁决已参与主因裁决 |
| 数据集/GT 隔离 | `benchmark/.../dataset.py` | token 隔离已实现 |
| 指标基座 | `benchmark/.../evaluation.py` | macro-F1 / False Pass / selective 已有 |
| CI 门禁 | `.github/workflows/*` | 三级门禁已绿，只加 Job 不改语义 |
| 前端 | `apps/web` | 本轮只加最小展示（Benchmark/System），不新增大页面 |

## 17. 与"不要重复建设"的边界（自查）

- 不新增 Agent 框架、不引入向量库、不引入消息队列、不引入第二套执行器；
- 不重写诊断引擎状态机，只扩展假设生成与裁决输入；
- 不重写修复状态机，只加补丁契约与自动拒绝；
- 不新建"AI 服务"，复用 `ModelGateway` + `LLMError` 语义；
- 不把 LLM 放到任何"会写正式资产"的路径上。
