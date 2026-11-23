# RUN-005 Benchmark 报告（v2 复验 + v3 状态）

- 数据集：`teamflow-faultbench-v2`（30 场景，GT 未改写）
- 数据集版本标识：v2（复用 RUN-003-D 冻结版本，本轮未修改 `benchmark/traceguard_benchmark/`）
- 环境：TeamFlow `127.0.0.1:8011`，TraceGuard API `127.0.0.1:8010`，隔离库 `.traceguard/run005/*`
- 数据集与 GT 隔离：GT 仅评测脚本可读（`bench_verify.py` 内 EXPECTED 表），Agent / LLM 输入不含 GT

## 1. 本轮实际执行：30/30 verdict 对照（真实浏览器）

- 命令：`TG_API_URL=http://127.0.0.1:8010 .venv/bin/python scripts/bench_verify.py`
- 原始数据：`.workbuddy-run/bench-v2-verify.json`
- 结果：`total=30, matched=30`（100% verdict 与 GT 预期一致）

| 指标 | 值 |
|---|---|
| 场景数 | 30 |
| verdict 一致 | 30 |
| verdict 分布 | FAILED 15 / PASSED 11 / NOT_EVALUATED 4（LOC 2、TIME 1、COMP 1，均为预期"执行失败"） |
| 单场景耗时 | 中位数 7.65s，最短 3.1s，最长 15.1s |
| 总耗时 | 242.9s |

失败样本：无（30/30 一致）。这是**verdict 层**的对照，不等于诊断能力与 LLM 能力已被评测。

## 2. RUN-005 目标中的 v3 多臂对照：未执行（BLOCKED）

计划实验组（A/C/D/E + LLM Flash/Pro/Routed）需要：每个场景 × 7 组 × 诊断/图/探针/LLM 调用，
单场景 LLM 成本约 2–5 次调用，30 场景即 400+ 次调用，且需多次重复以评估 Flaky。本轮未执行，原因：

1. **成本预算**：应用侧日预算 1 USD（历史基线），完整多臂 + 重复运行会超出；不得把预算改成无限。
2. **时长**：单场景诊断 + 探针闭环约 10–20s（含真实探针与 LLM 延迟），7 组 × 30 场景 ≈ 1–2 小时以上，且需失败重试。
3. **口径风险**：若为凑数只跑少量场景，会违反"不得通过减少失败样本来美化结果"与"小样本不得宣称显著性"。

因此本轮**不输出** Top-1 准确率 / Macro-F1 / Selective Accuracy / Wilson CI 等指标数值——
没有真实实验就不能有这些数字。相关指标定义与分子分母规则见 `benchmark/traceguard_benchmark/evaluation.py`。

## 3. 已具备的评测能力（代码层，VERIFIED by unit tests）

- `DiagnosisEvaluator`：`macro_f1`、`primary_cause_accuracy`、`selective_accuracy`、`false_pass_rate`（均返回分子/分母）。
- `ExperimentReport` / `AblationRunner`：按变体组织报告。
- `TestCaseCompiler.validate_stability`：STABLE_PASS / DETERMINISTIC_FAILURE / FLAKY_UNSTABLE / EVIDENCE_INSUFFICIENT。
- `StabilityReplayService`：真实回放编排（独立 Run、sandbox、Oracle 完整性门槛）。
- LLM 用量与成本：`/api/v1/system/llm/summary` 提供 calls/error/token/估算成本/平均与 P95 延迟（应用侧估算）。

## 4. 下一轮执行建议（可直接照做）

1. 先跑 **A（原始 Run 报告基线）+ C（规则诊断）** 两臂 × 30 场景：无 LLM 成本，约 20 分钟。
2. 再跑 **D（Evidence Graph）+ E（Ordered Probe）**：无 LLM 成本，约 40 分钟。
3. LLM 臂按日预算分批：每天 10 场景 × 3 臂，记录"已完成/未完成样本"，禁止丢弃失败样本。
4. Flaky 场景（TF-FLAKY-001/002）至少重复 5 次，单独报告稳定性，不与确定性场景混算。
5. 人工基线在没有真实参与者时继续标记 **BLOCKED**。

## 5. 统计限制（必须随结果一起报告）

- 30 场景为固定数据集，不是随机抽样；Wilson CI 只能描述"该数据集上的不确定性"，不能外推总体能力。
- NOT_EVALUATED 不是 0 分，也不等于失败；统计时必须单列。
- LLM 候选假设质量需与规则引擎"已确认根因"分开统计，二者口径不同。
