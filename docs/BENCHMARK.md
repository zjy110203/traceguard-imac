# UI-FaultBench 实验报告（真实运行，2026-11-22）

> 所有数值来自本机真实执行（Chromium 159 + TeamFlow），可用
> `GET /api/v1/benchmark/experiments/{id}` 复核，`observations` 中保存了每个样本的 run_id。
> 小样本（n=3）置信区间很宽，**不做过度外推**。
> RUN-003-G 后 LLM 基线已解锁（DeepSeek Flash/Pro/Routed 真实接入，见 RUN-003-G-LLM-Benchmark.md）。

## 0. 数据集 v2（RUN-003-D 扩充：30 场景）

`teamflow-faultbench-v2`：30 个独立 Ground Truth 场景，覆盖十类机制：

| 机制族 | 场景 | 数量 |
|---|---|---|
| 正常/回归 | NORMAL-001/002、DB-003、DB-005、UI-004、NET-001(-FAST)、TIME-001/002、API-003/004 | 11（PASSED 预期） |
| PERSISTENCE | DB-001（回滚假通过）、DB-002（重名静默失败） | 2 |
| UI_RENDERING | UI-001（提示缺失）、UI-002（静默忽略）、UI-003（误导文案） | 3 |
| API | API-001（500）、API-002（503） | 2 |
| NETWORK | NET-002（9s 必然超时） | 1 |
| LOCATOR | LOC-001/002（test_id 变更） | 2 |
| TIMING | DB-004（状态滞后 9s）、TIME-003（双提交竞态） | 2 |
| AUTHORIZATION | SEC-001（写 403）、SEC-002（读 403） | 2 |
| 复合 | COMP-001/002/003 | 3 |
| Flaky | FLAKY-001/002（50% 概率） | 2 |

**逐场景真实验证**：`scripts/bench_verify.py` 对全部 30 场景逐一真实浏览器执行，
verdict 与 GT 预期 **30/30 一致**（证据：`.workbuddy-run/bench-v2-verify.json`，
含每场景 run_id 与耗时；2026-11-22 共 8 轮迭代验证，修复了用例步骤、幂等、
缓存、观测合并等真实问题——过程记录见 Git 历史）。
注：定位器失效类场景的确定性执行失败表现为 execution_status=FAILED +
verdict=NOT_EVALUATED（架构语义：执行中断不产生断言结论），与 GT 的 EXEC_FAILED 语义对齐。

## 0.1 数据集 v2 上的诊断对照（exp_17953628312779018edea4741）

前 9 个场景（DB/UI 机制族）中 3 个故障样本：

| 指标 | A 原始报告 | C 规则 | D 图 | E 探针 | LLM_flash | LLM_pro | LLM_routed |
|---|---|---|---|---|---|---|---|
| Primary Cause | 0.000 (0/3) | 0.667 (2/3) | 0.667 | 0.667 | 0.000 | 0.000 | 0.333 |
| Coverage | 1.000 | 1.000 | 1.000 | 1.000 | 0.667 | 1.000 | 0.333 |
| False Pass | **1.000 (2/2)** | 0.000 (0/2) | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |

诚实结论：
1. 引入时序类机制（DB-004 状态滞后）后，规则组不再 3/3——滞后场景的主因归类
   （TIMING vs PERSISTENCE）是真实的难点，如实暴露；
2. 传统基线 A 在两个假通过场景 2/2 全部被骗——TraceGuard 核心价值再次被证实；
3. LLM 各组在此切片上低于规则组（n=3，方差大，不作外推）；
4. 全 30 场景的诊断对照（含 LOCATOR/SEC/COMP/Flaky 族）需要更大样本预算，
   实验框架已支持（`MAX_CASES_PER_EXPERIMENT=15`，可按需扩大）。

## 1. 数据集 v1 与隔离

| 项 | 值 |
|---|---|
| 数据集 | `teamflow-faultbench-v1` |
| 场景 | TF-NORMAL-001 / TF-DB-001 / TF-UI-001 / TF-API-001 |
| GT | 评测侧私有（`benchmark/traceguard_benchmark/scenarios.py`），API 不暴露 |
| 隔离验证 | `/benchmark/dataset` 只返回场景 ID 与故障注入 ID，`ground_truth_exposed=false` |
| 执行环境 | 每样本真实浏览器执行一次，`RESET_DATA` 清理 + 故障注入 |
| 指标口径 | 全部指标保存 numerator / denominator，比例给 Wilson 95% CI |

## 2. 实验 B：故障诊断对照（A / C / D / E）

样本：3 个有效故障样本（PERSISTENCE / UI_RENDERING / API），正常场景只用于确认不误报。

| 指标 | A 原始报告 | C 规则诊断 | D 证据图增强 | E 主动探针 |
|---|---|---|---|---|
| Primary Cause Accuracy | 0.333 (1/3) | **1.000 (3/3)** | 1.000 (3/3) | 1.000 (3/3) |
| Diagnosis Coverage | 1.000 (3/3) | 1.000 (3/3) | 1.000 (3/3) | 1.000 (3/3) |
| Selective Accuracy | 0.333 (1/3) | 1.000 (3/3) | 1.000 (3/3) | 1.000 (3/3) |
| False Pass Rate | **1.000 (1/1)** | 0.000 (0/1) | 0.000 (0/1) | 0.000 (0/1) |
| 探针/样本 | — | — | — | 1.0 (3/3) |

Wilson 95% CI（3/3）：[0.438, 1.000]；（1/3）：[0.061, 0.792]；（1/1）：[0.207, 1.000]。

### 结论

1. **传统基线（A）会把"UI 成功但数据未落库"判为通过** —— False Pass Rate = 100%。
   这就是 TraceGuard 存在的核心理由：UI 断言无法代表业务事实。
2. 规则诊断（C）在 3 个故障样本上主因全对，且不产生假通过。
3. **D 与 C 相同，E 与 D 相同**：在本数据集上，证据图与主动探针没有带来主因准确率增益。
   这是一个诚实的负结果 —— 当前证据已足够"单点确认"，图的增益需要更复杂的多源冲突场景才能体现。
   E 每个样本确实执行了 1 个只读状态查询探针（可审计）。
4. B（单次 LLM 分析）**BLOCKED**：无 API Key、无本地模型。不填写任何估计值。

## 3. 实验 C：Oracle-Protected 自愈对照

| 指标 | Oracle-Protected | 无保护对照 | 手工 | LLM |
|---|---|---|---|---|
| Repair Validation Success Rate | 1.000 (1/1) | 未执行（对照组仅验证断言保护） | BLOCKED | BLOCKED |
| Assertion Preservation Rate | **1.000 (1/1)** | **0.000 (0/1)** | BLOCKED | BLOCKED |
| Repair Acceptance Rate | 1.000 (1/1) | — | BLOCKED | BLOCKED |
| Repair Regression Rate | 0.000 (0/1) | — | BLOCKED | BLOCKED |

Protected 路径的每一步都是真实的：规则校验 → 真实沙箱执行（Playwright，PASSED）→
人工审批 → CAS 晋升（v3）→ 回归执行（PASSED）→ 回滚（生成 v4）。

对照组演示了"没有断言保护时，自愈可以悄悄把 `count >= 1` 改成 `count >= 0`"，
断言保持率 0/1。这不是模拟分数，而是规则函数对同一候选的实际判定。

## 4. 实验 A：用例生成对照

| 指标 | 人工可信用例 | 规则编译器（离线轨迹） | LLM |
|---|---|---|---|
| Execution Success Rate | 1.000 (4/4) | 1.000 (2/2) | BLOCKED |
| Assertion Pass Rate | 0.625 (5/8) | 0/0（无可评估断言） | BLOCKED |
| Locator Warning Rate | 0.000 (0/2) | 0.000 (0/2) | BLOCKED |

说明：

- 人工用例的断言通过率 0.625 是**预期**：故障场景的断言本来就应当失败（这是测试的目的）。
- 编译器产出的候选用例可真实执行（2/2 COMPLETED），但断言 `oracle_id=None`，
  因此 verdict 为 `NOT_EVALUATED` —— 这是契约要求：**没有审批的 Oracle 不能产生结论**。
  分母为 0 时不计算比率（不把 NOT_EVALUATED 当作失败）。
- 编译器基线使用人工用例转换的**离线轨迹**，不代表在线探索能力（在线探索在
  `POST /intents/{id}/explore` 已打通，但未纳入本基准）。

## 5. 未执行/阻塞项

| 项 | 状态 | 原因 |
|---|---|---|
| B 组单次 LLM 诊断 | BLOCKED | 无 LLM API Key / 本地模型 |
| 手工修复基线 | BLOCKED | 需要人类工程师真实操作，不伪造 |
| LLM 直接生成用例 | BLOCKED | 同上；且需固定 prompt/model 版本 |
| 实验 D/E 的"消融"（移除图/移除探针） | 部分 | 当前 D/E 与 C 结果相同，等价于已观测到"无增益" |
| Macro-F1 | 未计算 | 单标签 3 样本无统计意义；需扩充数据集 |
| Time to Diagnosis | 未计 | 未实现统一计时起点/终点的埋点 |

## 6. 可复现命令

```bash
# 确保 TeamFlow 与 API 已启动
curl -s -X POST http://127.0.0.1:8000/api/v1/benchmark/experiments \
  -H 'Content-Type: application/json' -d '{"kind":"B"}' | jq '.metrics'
```

实验记录持久化在 `tg_doc(kind=experiment)` 与 `tg_metric`，包含每个样本的 `run_id`，
可回到 Run 详情查看截图、DOM、断言与诊断依据。

## RUN-008 补充（2026-11-24）

- 本轮在隔离 API `8060` / TeamFlow `8061` / 独立 Worker `run008-bench-w1` 上真实执行
  UI-FaultBench v3（30 场景 + 故障样本，2 次独立重复），原始样本写入
  `docs/assets/run008/wp7/samples*.jsonl`，不覆盖 `docs/assets/run007/`。
- 未配置 LLM Secret 的臂保持 BLOCKED；不得用历史数据或 Mock 冒充本轮实验。
- 最终分子/分母/样本量/模型状态以 `docs/RUN-008-BENCHMARK-REPORT.md` 为准。
