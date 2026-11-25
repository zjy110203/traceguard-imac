# RUN-013 WP5：AI 真实能力与 Benchmark 科学性

> 日期：2026-11-25（CST）
> 证据：`.workbuddy-run/llm-smoke-results.json`、`docs/assets/run013/wp5/ai-quality-eval.json`
> 脚本：`scripts/llm_smoke.py`、`scripts/ai_quality_eval_run013.py`

---

## 1. 真实 Provider 状态：VERIFIED（低成本 Smoke）

本机 `.env` 存在合法配置的 DeepSeek API Key（未打印、未写入证据文件）。执行
`scripts/llm_smoke.py`，真实调用 DeepSeek：

| 检查 | 结果 |
|---|---|
| `/models` 连通性 | PASS（`deepseek-flash`、`deepseek-v4-pro`） |
| Flash 文本调用 | PASS（38 in / 86 out，1315ms） |
| JSON Output | PASS（action/params/reason） |
| Pro 推理调用 | PASS（3239ms） |
| 超时边界 | PASS（0.03s 超时被正确分类） |
| 截断输出 | PASS（`invalid_json`） |
| Token/成本估算 | PASS（136 calls，$0.0620，应用侧估算） |
| 密钥脱敏（配置/错误消息） | PASS |

**11/11 passed**。真实 Provider 路径不再是 NOT_RUN；但仍只覆盖 Smoke，
不等于四类顾问在全部业务上下文的准确率。

## 2. 质量量化评估（n=6，真实模型）

6 个诊断场景使用同一输入，Ground Truth 只在脚本内打分、不进入模型上下文。

| 指标 | 规则组 | LLM 组 |
|---|---|---|
| 结构合法率（called） | — | **1.000** |
| Schema 通过率（有候选） | — | **1.000** |
| 有证据支持的候选比例 | — | **1.000** |
| 无效候选比例 | — | **0.000** |
| 重复候选比例 | — | **0.000** |
| 候选采纳率（原始域标签） | 0.833 | 0.500 |
| 候选采纳率（归一化域标签） | 0.833 | **0.833** |
| Fallback 率 | — | **0.000** |
| 平均响应耗时 | — | **3005 ms** |
| Token（6 次） | — | 1908 in / 2313 out |
| 估算成本 | — | **$0.001116** |

**统计限制**：n=6，仅用于回归与相对比较，不能外推总体准确率。
原始域标签下 LLM 采纳率偏低，主要因为模型使用 `TIMEOUT/API/PERSISTENCE/JS` 等细分标签，
而 Ground Truth 使用 `TIMING/NETWORK/PRODUCT/UI`；按脚本内显式映射归一后与规则组持平。
这属于**标签体系差异**，不是模型错误，报告同时保留两组数字。

## 3. 四类调用点审查

| 调用点 | Schema | 重试 | Fallback 标注 | 追踪 | 真实模型 Smoke |
|---|---|---|---|---|---|
| Compiler Advisor | 已有 | 有界 | 有 | 有 | 未单独调用（规则模式默认 `rule_only`） |
| Diagnosis Advisor | RUN-012 补齐 | 仅 `invalid_json` | 有 | 有 | **本轮真实调用 6 次** |
| Probe Planner | 已有 | 有界 | 有 | 有 | 未启用（`llm_probe_planner_enabled=false`） |
| Repair Planner | 已有 | 有界 | 有 | 有 | UI 未配置时如实提示禁用 |

边界保持不变：

- 模型不接触 Benchmark Ground Truth；
- 模型不能 `CONFIRMED` 根因；
- 模型不能审批 Oracle / Repair；
- 预算、超时、重试均有上限；证据文件不含密钥。

## 4. Benchmark v5 重跑：NOT_RUN

| 项 | 状态 | 原因 |
|---|---|---|
| 23 场景 × 2 轮真实重跑 | **NOT_RUN** | 60 分钟长稳实验占用同一宿主与隔离执行环境；本轮未在长稳期间并行启动 Benchmark，避免端口/浏览器/数据库互相污染。历史归档 `docs/assets/run011-r1/benchmark/v5-2rounds.json` 未修改。 |
| 历史 False Pass 指标 | **PARTIAL / INFERRED_ONLY** | 保持 RUN-012 Errata 口径，不升级为 VERIFIED |
| FakeProvider 契约测试 | VERIFIED（历史） | 与真实 Provider 分组统计，不混算 |

未执行就是未执行：不以 FakeProvider 结果冒充真实 Benchmark，不以历史 PARTIAL 冒充本轮 PASS。

## 5. 后续建议

1. 在独立窗口（不与长稳并行）执行 23×2，并保存 `fault_state` 真实快照；
2. 扩大 AI 质量样本到 ≥30，并统一域标签体系后再比较规则/LLM；
3. 对 Compiler / Probe / Repair 三类顾问各做一次真实模型 Smoke（当前仅 Diagnosis）。
