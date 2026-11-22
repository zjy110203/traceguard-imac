# RUN-003-G：DeepSeek 双模型接入实施报告

> 完成时间：2026-11-22（GMT+8）。全部数值来自真实 API 调用与真实测试，无编造。

## 1. 接入架构

```
Explorer / DiagnosisService
        │ 调用
        ▼
LLMExplorerPlanner / LLMDiagnosisAdvisor      ← 结构化校验 + 安全约束
        ▼
ModelRouter（任务级选型：默认 Flash，Pro 需显式 reason 升级）
        ▼
ProviderRegistry → DeepSeekProvider（OpenAI-compatible，有限重试/超时/错误映射）
        ▼
TokenUsageTracker（JSONL 落盘 .traceguard/llm_usage.jsonl，成本估算）
```

失败语义：任何 LLM 错误（超时/429/5xx/无效 JSON/预算耗尽）→ 规则引擎兜底，
决策 reason 标记 `fallback(rule): <原因>`，绝不伪装成 LLM 决策或测试通过。

## 2. 模型分配（双模型路由）

| 任务 | 模型 | 说明 |
|---|---|---|
| Explorer 探索规划 | deepseek-flash | 每步 1 次调用 |
| Diagnosis 假设候选 | deepseek-flash | 诊断时 1 次 |
| Complex Diagnosis（Pro 升级路径） | deepseek-v4-pro | 需显式 reason，Router 记录升级日志 |
| Benchmark LLM_pro 组 | deepseek-v4-pro | 输出预算 4000（reasoning tokens 计入 max_tokens） |
| Benchmark LLM_routed 组 | Flash→Pro | Flash 最高置信度 < 0.5 时升级 Pro，记录原因 |

模型可用性经 `GET /models` 官方接口验证：`deepseek-flash`（DeepSeek-V4.1-Flash）、
`deepseek-v4-pro`（DeepSeek-V4-Pro）均存在；已退役的 deepseek-chat / deepseek-reasoner 未使用。

## 3. 真实 API 调用验收（11/11 通过）

证据：`.workbuddy-run/llm-smoke-results.json`、`.workbuddy-run/llm-connectivity-2026-11-22.json`

| # | 验收项 | 结果 |
|---|---|---|
| 1 | API 连通性（官方 models 接口） | PASS |
| 2 | 配置模型真实存在 | PASS |
| 3 | Flash 普通文本调用 | PASS（38in/77out, 793ms） |
| 4 | Token 统计（含 cached/reasoning） | PASS |
| 5 | JSON Output | PASS |
| 6 | Pro 复杂推理调用 | PASS（3612ms） |
| 7 | 超时边界（0.001s → LLMError(timeout)） | PASS |
| 8 | 无效/截断输出处理 | PASS（invalid_json 显式分类） |
| 9 | 成本计算（估算值明确标记） | PASS |
| 10 | 密钥脱敏（public_dict + 异常消息） | PASS |
| 11 | not_configured 错误映射 | PASS |

## 4. Agent 真实接入记录

### Explorer（真实调用）
- `POST /intents/{id}/explore?max_steps=8` → `engine: llm:deepseek`，`llm_stats: {llm_calls: 3~4, fallback_calls: 0~1}`。
- 真实行为记录：LLM 发现根路径 404 → 自主导航 /projects → read_page 寻找 test_id → 填表超时被如实阻断。
- 安全实证：LLM 曾尝试导航到未授权目标（intent 文本中的旧端口），ToolGateway 以
  `UNAUTHORIZED_TARGET` 拒绝——**权限边界不因 LLM 改变**。
- Prompt 已包含允许的 base_url 提示；页面内容只作为数据（防 Prompt 注入）。

### Diagnosis（真实调用）
- `POST /runs/{id}/diagnose` → `engine: rule-v1+llm-advisor`。
- CONFIRMED 仍由确定性规则产生；LLM 候选以 `suggested_by=llm:deepseek-flash`、
  status=CANDIDATE 追加，引用的 evidence_id 全部真实存在（虚构 ID 被剔除并有警告）。
- 单测防线：引擎内 `suggested_by=="llm"` 不得 CONFIRMED；离线测试覆盖虚构 ID 剔除。

### Compiler / Self-Healing / Active Probe
- 本轮未接入 LLM（保持规则实现）。原因见审计报告 §4；接口已备好，无架构障碍。

## 5. Benchmark 对照实验（真实执行，同数据集同 GT）

数据集：UI-FaultBench TeamFlow 4 场景（3 个故障样本 + 1 正常），GT 评测侧隔离。
实验 ID：exp_1795356831687450a804fa64f（与最近一次 API 触发的实验互为复现）。

| 组 | 主因准确率 | 诊断覆盖 | 假通过率 | 说明 |
|---|---|---|---|---|
| A_raw_report（传统基线） | 33.3% (1/3) | 100% | **1/1** | 只看 UI 断言，被 False Pass 骗过 |
| C_rule（规则引擎） | **100% (3/3)** | 100% | 0/1 | 确定性规则 + 独立状态查询 |
| LLM_flash | 66.7% (2/3) | 66.7% | 0/1 | 1 次无效 JSON 输出（如实记 UNKNOWN） |
| LLM_pro | **100% (3/3)** | 100% | 0/1 | 推理模型，修复输出预算后 3/3 |
| LLM_routed（Flash→Pro） | **100% (3/3)** | 100% | 0/1 | 置信度 < 0.5 升级 Pro |

调用与成本：12 次真实调用，估算成本 **$0.029**（应用侧牌价估算，非平台账单）。

**统计限制（必须强调）**：
- 样本量 n=3，所有结论仅描述本数据集表现，不具备统计泛化力；
- LLM 组的"诊断覆盖"含义与规则组不同：LLM 候选永远不是 CONFIRMED（安全约束），
  覆盖率指"给出了候选假设"，不是"确认了根因"；
- LLM 组输入与规则组相同（症状+证据摘要），但 LLM 组不使用探针/图增强，口径差异已在文档标注。

## 6. 密钥安全

- Key 只存于 `.env`（已被 .gitignore 覆盖，`git check-ignore` 验证）；提供 `.env.example` 模板。
- `Settings.public_dict()` 对含 key 字段自动 `***REDACTED***`（有单测）。
- LLMError 消息不含 Key（有单测）；Prompt 不含 Key；日志不含 Key。
- 开发工具的模型配置与产品运行时配置分离（产品只读 `DEEPSEEK_API_KEY`）。

## 7. 失败、超时与降级测试（离线 16 项 + 真实验证）

- 无效 JSON → invalid_json + 1 次有限重试（真实发生：Flash 组 1 次失败被如实记录）。
- 超时 → timeout（真实验证 0.03s 触发）。
- 429/5xx → 有限重试（MockTransport 验证 1+2 次后停止）。
- 预算耗尽 → should_skip_llm()，调用方规则兜底（单测：0 次真实调用）。
- Pro 空 content（finish_reason=length）→ 已修复：按模型类型区分输出预算。

## 8. 新增/修改文件

新增：
- `packages/agents/traceguard_agents/gateway/llm_gateway.py`（约 900 行）
- `tests/unit/test_llm_gateway.py`（16 项离线单测）
- `scripts/llm_smoke.py`（11 项真实验收）
- `docs/RUN-003-G-ModelGateway-Audit.md`
- `.env.example`

修改：
- `packages/core/config.py`（llm_* 配置 + 密钥字段）
- `apps/api/.../exploration_service.py`（LLM planner 注入）
- `apps/api/.../diagnosis_service.py`（LLM 顾问 + use_llm_advisor 分组开关）
- `apps/api/.../tool_gateway.py`（只读工具空参数放行）
- `benchmark/traceguard_benchmark/runner.py`（LLM 三组对照）
- `tests/conftest.py`（测试环境 LLM 默认禁用 + settings 缓存重置）

## 9. 成本报告摘要（详见 RUN-003-G-Cost-Report.md）

- 本轮开发+验收+Benchmark 累计：约 5 万输入 / 3 万输出 tokens，估算成本 **约 $0.06**。
- 预算目标：单日 $1 / 累计 $10 —— 实际消耗远低于预算。
- 应用侧熔断已实现（`should_skip_llm`），平台侧无硬性封顶（如实记录）。

## 10. 尚未完成 / 后续建议

1. Compiler / Self-Healing / Active Probe 的 LLM 辅助接入（接口已备）。
2. Benchmark 扩充到 ≥30 场景后再评估 LLM 与规则的统计显著差异。
3. Flash 的 JSON 输出稳定性可探索 few-shot 示例或更严格的 schema 约束。
