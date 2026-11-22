# RUN-003-G：真实 LLM Benchmark 对照报告

> 实验时间：2026-11-22。全部数值来自真实浏览器执行 + 真实 DeepSeek API 调用。
> 数据集：UI-FaultBench TeamFlow 4 场景（3 故障 + 1 正常），Ground Truth 评测侧隔离。

## 1. 实验设计

| 组 | 输入 | 方法 |
|---|---|---|
| A_raw_report | 原始断言结果 | 传统自动化基线：只看 UI 断言（False Pass 盲区来源） |
| C_rule | 全部证据 | 规则诊断引擎（CONFIRMED 需确定性规则 + TRUSTED 证据） |
| D_evidence_graph | C + 证据图 | 跨来源冲突确认 |
| E_ordered_probes | D + 只读探针 | 主动补证 |
| LLM_flash | 症状 + 失败步骤 + 证据摘要 | DeepSeek Flash 单次调用，取最高置信候选 |
| LLM_pro | 同上 | DeepSeek Pro（推理模型，输出预算 4000） |
| LLM_routed | 同上 | Flash 起步，最高置信度 < 0.5 升级 Pro（记录升级原因） |

分组隔离：C/D/E 显式关闭 LLM 顾问（`use_llm_advisor=False`），LLM 组不使用图/探针增强。
GT 隔离：所有基线只看到症状与证据摘要，Ground Truth 仅评测侧可读。

## 2. 结果（exp_1795356831687450a804fa64f 及复现轮）

| 组 | 主因准确率 | 诊断覆盖 | 假通过率（PERSISTENCE 样本） |
|---|---|---|---|
| A_raw_report | 33.3% (1/3) | 100% (3/3) | **1/1** |
| C_rule | 100% (3/3) | 100% (3/3) | 0/1 |
| D_evidence_graph | 100% (3/3) | 100% (3/3) | 0/1 |
| E_ordered_probes | 100% (3/3) | 100% (3/3) | 0/1 |
| LLM_flash | 66.7% (2/3) | 66.7% (2/3) | 0/1 |
| LLM_pro | 100% (3/3) | 100% (3/3) | 0/1 |
| LLM_routed | 100% (3/3) | 100% (3/3) | 0/1 |

成本：LLM 三组共 12 次真实调用，估算 **$0.029**。

## 3. 诚实披露

1. **样本量 n=3**：结论只描述本数据集，无统计泛化力；Wilson CI 见实验 metrics（宽度大）。
2. **覆盖口径不同**：LLM 候选受安全约束永远不是 CONFIRMED，其"覆盖"指"给出候选"，
   不是"确认根因"——不能与规则组的 CONFIRMED 覆盖直接等同比较。
3. Flash 一次无效 JSON 输出被如实记录为 UNKNOWN（未重试出结果前不计入正确）。
4. Pro 首轮因 reasoning tokens 耗尽 max_tokens 全部失败（诚实记录 0/3），
   修复输出预算后复现轮 3/3 —— 两轮数据都保留在实验记录中。
5. LLM 组未使用探针/图增强，输入与 C 组对齐但不与 E 组比较。

## 4. 结论（限于本数据集）

- 传统"只看 UI 断言"基线在 False Pass 场景 100% 被骗（1/1 假通过）；
- 规则引擎在小数据集上已 3/3，且判定可解释、可审计；
- LLM 路由组以 ~$0.01/轮 成本达到同正确率，且能补充规则引擎未覆盖的假设表述
  （如 workspace 语义不一致假设），可作为诊断"广度"补充；
- 推荐生产形态：**规则引擎为主判定 + LLM 候选为广度补充**（即已实现的 rule-v1+llm-advisor）。

## 5. 后续

- 扩充数据集至 ≥30 场景（RUN-003-D）后重跑本实验，观察显著性；
- 记录 Prompt 版本（diagnosis_advisor@v1）与模型版本，保证可复现；
- 增加 Flash JSON 输出的 few-shot 或 schema 约束实验。
