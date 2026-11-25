# RUN-012 WP0：Benchmark v5 历史归档 Errata（不修改原始证据）

日期：2026-11-25
分支：`workbuddy/run012-product-completion`（基线 `0ca128f`）

## 1. 复核结论：R1.2 报告不能直接采信

R1.2 把 9 条「HTTP 412 + Run PASSED」记录全部称为正向控制，并给出 False Pass 0/37。复核后发现三个问题：

1. **正向控制靠异常文本反推**：`error` 字符串中的 412 与 run PASSED 不能证明「场景预期通过」，也不能区分「故障未触发」与「故障生效但断言容忍」。
2. **异常记录丢失上下文**：`bench_v5_run011_r1.py` 的 `except` 只保留 scenario / round / error，GT、故障、TestCase、run_id 与诊断状态全部丢失。
3. **False Pass 口径缺少触发条件**：原统计只看 GT 与 Run Verdict，没有确认故障是否真的生效；Flaky 未触发样本无法与 False Pass 分开。

本轮新增场景契约模块 `scripts/bench_scenario_contract.py`，23 个场景全部显式声明 `scenario / testcase / fault / gt_mechanism / expected_run_outcome / control_kind / fault_trigger_condition`，归档于 `docs/assets/run012/wp0/scenario-contracts.json`。契约校验 `validate_contracts(V5_SCENARIOS)` 返回空问题列表。

## 2. 五类记录必须分开

| 类别 | 定义 | 是否进诊断准确率分母 | 是否进 False Pass 口径 |
|---|---|---|---|
| `EXPECTED_FAILURE_CONFIRMED` | 场景预期失败，且观测到 FAILED / NOT_EVALUATED | 是 | 是（作分母） |
| `NORMAL_POSITIVE_CONTROL` | 场景定义明确预期 PASSED（含故障生效但断言容忍） | 否 | 否 |
| `FAULT_NOT_TRIGGERED` | Flaky 故障未生效，Run PASSED | 否 | 否，单独报告 |
| `FALSE_PASS` | 预期失败且故障确认生效，但 Run PASSED | 否 | 是（作分子） |
| `EXECUTION_EXCEPTION` | 普通执行异常，与场景预期无关 | 否 | 否 |
| `UNKNOWN_FAULT_ACTIVATION` | 无法确认故障是否触发 | 否 | 否，单独报告 |

> `EXPECTED_FAILURE_CONFIRMED` 是正常负样本的第六个内部标签，用于让五类异常口径不互相污染；不改变业务状态机。

## 3. 历史 9 条异常逐条重核

判定依据：`apps/api/traceguard_api/seed.py` 的用例定义（等待窗口、断言对象、标题中的「预期通过」）+ `apps/teamflow/traceguard_teamflow/{api,faults}.py` 的故障效果 + 412 表示诊断接口正确拒绝 PASSED Run。
置信度 `CONTRACT_INFERRED` 表示依据契约与 seed 语义推断，历史归档**没有** fault_state 快照，不能写成 `CONFIRMED` 实测。

| # | 场景 | 轮次 | 故障 | 分类 | 激活状态 | 效果状态 | 置信度 |
|---|---|---|---|---|---|---|---|
| 1 | UI-DELAYED | 1 | TF-UI-004 | NORMAL_POSITIVE_CONTROL | CONFIRMED* | INFERRED_FROM_VERDICT | CONTRACT_INFERRED |
| 2 | API-EDIT-500 | 1 | TF-API-003 | NORMAL_POSITIVE_CONTROL | CONFIRMED* | INFERRED_FROM_VERDICT | CONTRACT_INFERRED |
| 3 | API-EDIT-503 | 1 | TF-API-004 | NORMAL_POSITIVE_CONTROL | CONFIRMED* | INFERRED_FROM_VERDICT | CONTRACT_INFERRED |
| 4 | TIME-STRONG | 1 | TF-TIME-002 | NORMAL_POSITIVE_CONTROL | CONFIRMED* | INFERRED_FROM_VERDICT | CONTRACT_INFERRED |
| 5 | FLAKY-DB | 1 | TF-FLAKY-001 | FAULT_NOT_TRIGGERED | UNKNOWN | NOT_TRIGGERED | PROBABILISTIC_NO_EFFECT_EVIDENCE |
| 6 | UI-DELAYED | 2 | TF-UI-004 | NORMAL_POSITIVE_CONTROL | CONFIRMED* | INFERRED_FROM_VERDICT | CONTRACT_INFERRED |
| 7 | API-EDIT-500 | 2 | TF-API-003 | NORMAL_POSITIVE_CONTROL | CONFIRMED* | INFERRED_FROM_VERDICT | CONTRACT_INFERRED |
| 8 | API-EDIT-503 | 2 | TF-API-004 | NORMAL_POSITIVE_CONTROL | CONFIRMED* | INFERRED_FROM_VERDICT | CONTRACT_INFERRED |
| 9 | TIME-STRONG | 2 | TF-TIME-002 | NORMAL_POSITIVE_CONTROL | CONFIRMED* | INFERRED_FROM_VERDICT | CONTRACT_INFERRED |

\* `CONFIRMED` 指用例步骤中的 `POST /api/faults/{id}/activate` 是无条件执行的确定性步骤；**不是**从 HTTP 412 推出来的。效果状态仍只到 INFERRED。

具体理由：

- **UI-DELAYED**：提示延迟 3000ms，用例等待 4000ms，断言读取成功提示 → 预期 PASSED。
- **TIME-STRONG**：seed 标题直接写「列表刷新延迟 5000ms（预期通过）」→ 预期 PASSED。
- **API-EDIT-500 / API-EDIT-503**：`edit_flow()` 的断言是读取 `edit-error`，即验证故障被正确检出；Run PASSED 是测试目标达成，不是漏检。
- **FLAKY-DB 第 1 轮**：50% 概率故障，Run PASSED，且没有任何故障生效证据 → 不能算正向控制，也不能算 False Pass。第 2 轮 Run FAILED，可作为故障生效的推断证据。

## 4. 修订统计与历史指标结论

| 指标 | R1.2 报告 | RUN-012 Errata |
|---|---|---|
| 执行记录 / 场景 | 46 / 23 | 46 / 23（不变） |
| 诊断样本 | 37 | 37（不变，但证据级别为推断） |
| 正向控制兼容桶 | 9 | 9（其中**预期通过控制 8** + **Flaky 未触发 1**，不再混为一类） |
| 普通执行异常 | 0 | 0 |
| 未知触发状态 | 无 | 0（9 条已全部按契约归类，置信度如实标记） |
| False Pass（R1.2 兼容字段） | 0/37 | 0/37（保留，便于历史对照） |
| False Pass（严格口径） | 无 | 0/37，`evidence_level=INFERRED_ONLY` |
| 历史 False Pass 指标状态 | VERIFIED（报告口径） | **PARTIAL** |

**结论：历史 False Pass 指标为 PARTIAL，不是 VERIFIED，也不是 NOT_MEASURED。** 37 条负样本的故障生效只有「Run FAILED 与预期一致」这一推断证据，缺少 fault_state 快照；分子 0 可信（未发现预期失败而 PASSED 的记录），但证据等级不足以标 VERIFIED。未来运行写入 `fault_activation_status` / `fault_effect_status` 真实快照后才可升级。

## 5. 原始证据保护

| 文件 | SHA-256（修复前 = 修复后） |
|---|---|
| `docs/assets/run011-r1/benchmark/v5-2rounds.json` | `d99bc2e8f0da92a061b9b7ce1941746421ac8e67e6c2d1ecbbc3080f273c3e44` |
| `docs/assets/run011-r1/benchmark/pilot-3scenarios.json` | `2e96e24dd15ae3b7126a78cae833f76072806435125e2abd94c8427859808f6b` |

本轮所有修订统计都是**计算层新增字段 + 独立 Errata 文档**，原始字节未改，历史测试未删、未弱化。

## 6. 未来运行的可测性修复

- `run_scenario()` 在 Run 完成后立即读取 `GET /api/faults` 的 state 快照，写入 `fault_activation_status`（真实激活证据，失败时标 UNKNOWN，不伪造）。
- 诊断接口 412 被捕获为带完整上下文的 `ScenarioExecutionError.sample`，异常路径保留 scenario / testcase / fault / GT / run_id / verdict / 诊断状态 / 错误类型与消息。
- `compute_statistics()` 输出 `record_classification_counts`、`false_pass_evaluation`、`contract_problems`，任何未知场景或契约缺失都会暴露，而不是被静默丢进 error 桶。
