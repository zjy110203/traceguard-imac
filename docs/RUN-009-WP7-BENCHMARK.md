# RUN-009 WP7：Benchmark v4

> 产物：`scripts/bench_run009.py`（新建）、`benchmark/traceguard_benchmark/scenarios_v4.py`（新建）、
> `docs/assets/run009/benchmark-v4/*`、`docs/assets/run009/wp6/*`。
> 未修改 `scenarios.py`、`bench_run007.py`、诊断内核、RUN-008 证据或其它 WP 文档。
> **n=19/轮 × 2 轮 = 38 个计分样本；全部比例只报分子/分母/样本数，不做统计显著性宣称。**

---

## 1. Benchmark v4 设计

### 1.1 v3 四臂语义保留

| 臂 | 来源 | 语义 |
|---|---|---|
| A_raw_report | `bench_run007.baseline_raw_report` | 只看 UI 断言（传统自动化） |
| C_rule | `bench_run007.diag_to_pred` | 确定性规则诊断，不回灌探针 |
| D_graph | `bench_run007.graph_enhanced` | 证据图跨源确认（只升置信度） |
| E_probe_v3 | `bench_run007.probe_arm_prediction` | RUN-008 原探针臂（证据未回灌） |

四臂函数由 `bench_run009.py` **动态加载原模块**调用，不复制、不改写口径，保证与
RUN-008 报告直接可比。

### 1.2 新增消融臂（全部用真实内核函数离线重算）

| 臂 | 配置 | 说明 |
|---|---|---|
| F_probe_feedback_b1 | Graph 关 / Probe 开（预算 1） | 真实 `merge_signals` + `DiagnosisEngine.diagnose` |
| G_probe_feedback_b2 | Graph 关 / Probe 开（预算 2） | 同上，2 个探针 |
| H_probe_feedback_b3 | Graph 开 / Probe 开（预算 3） | 3 个探针 + 图增强 |
| CF_time_aligned_probe | Graph 开 / 探针时间对齐 | **反事实模拟**，只用 Run 自身业务断言推导状态时状态；明确标注 `COUNTERFACTUAL_NOT_MEASURED`，不计入实测 |
| GRAPH_SEM | Graph 结构审计 | 输出节点/边/来源/派生/冲突指标，不改主因（诚实反映图当前作用） |
| LLM_CONSTRAINED | 受约束 LLM | 需 provider 启用 + 凭据 + 显式 `--allow-llm`；否则 BLOCKED |

无 Graph 臂由 C/F/G 承担，有 Graph 臂由 D/H/GRAPH_SEM 承担；
无 Probe 臂由 A/C/D 承担（预算 0）；不同探针预算由 F/G/H 承担。

### 1.3 指标口径（全部含 numerator / denominator / n / note）

主因准确率、Coverage、Selective Accuracy、False Pass Rate、Macro-F1（各类 TP/FP/FN、
precision/recall/F1、support、浮点分子与类别数）、每样本探针数、每样本证据数、
Latency（离线重算毫秒，不含执行时间）、LLM 调用次数/Token/成本、Repair Assertion
Preservation / Regression Success。每次原始样本逐条写入 JSONL，失败样本不删不取平均。

---

## 2. 实验方法与诚实声明

- **证据来源**：RUN-008 真实执行落盘的 `docs/assets/run008/wp7/samples.jsonl` 与
  `samples.repeat1.jsonl`；每条样本含真实 report / diagnosis / graph / probe_plan / evidence。
- **本轮没有重新注入故障、没有修改内核、没有覆盖历史证据**。新臂是在真实证据上用
  真实内核函数（`merge_signals`、`DiagnosisEngine.diagnose`）重算，因此是"新的计算实验"，
  不是"新一轮故障注入执行"。
- GT 只在评测脚本中读取：v3 用样本字段 `expected_failure_mechanism`；
  v4 用 `GT_MANAGER` 评测令牌（无令牌读取 0 条，有令牌读取 14 条，`isolated=True`）。
- LLM 臂未发起外网请求；不冒充。

---

## 3. 多臂结果（2 轮合并，n=38）

| 臂 | 主因准确率 | Coverage | Selective Acc. | Macro-F1 | False Pass | 探针/样本 | 证据/样本 | LLM 调用 |
|---|---|---|---|---|---|---|---|---|
| A_raw_report | **6/38 (0.158)** | 38/38 | 6/38 | 0.0682 | **8/8 (1.0)** | 0/38 | 1098/38 | 0 |
| C_rule | **18/38 (0.474)** | 24/38 | 18/24 | 0.2897 | 0/8 | 0/38 | 1098/38 | 0 |
| D_graph | **18/38 (0.474)** | 24/38 | 18/24 | 0.2897 | 0/8 | 0/38 | 1098/38 | 0 |
| E_probe_v3 | **18/38 (0.474)** | 24/38 | 18/24 | 0.2897 | 0/8 | 100/38 (2.63) | 1098/38 | 0 |
| F_probe_feedback_b1 | **18/38 (0.474)** | 24/38 | 18/24 | 0.2897 | 0/8 | 30/38 | 1098/38 | 0 |
| G_probe_feedback_b2 | **18/38 (0.474)** | **16/38 (0.421)** | 14/16 | 0.2897 | 0/8 | 60/38 | 1098/38 | 0 |
| H_probe_feedback_b3 | **18/38 (0.474)** | **16/38 (0.421)** | 14/16 | 0.2897 | 0/8 | 76/38 | 1098/38 | 0 |
| CF_time_aligned（**非实测**） | 18/38 | 22/38 | 18/22 | 0.2897 | 0/8 | 76/38 | 1098/38 | 0 |
| GRAPH_SEM | 18/38 | 24/38 | 18/24 | 0.2897 | 0/8 | 0/38 | 1098/38 | 0 |
| LLM_CONSTRAINED | — | — | — | — | — | — | — | **BLOCKED** |

补充事实指标：

- `probe_applied_to_diagnosis_rate[v3]` = **0/100**
- `graph_changed_prediction_count` = **0/38**
- `probe_changed_prediction_count[v3]` = **0/38**
- `false_pass_rate[execution]` = 0/8（v3 执行层已无假通过；A 臂的 8/8 是诊断口径的假通过）
- Repair（沿用 RUN-008 真实记录，非本轮重跑）：
  `assertion_preservation_rate` 2/2、`acceptance_rate` 2/2、`repair_regression_pass_rate` 2/2

每轮单独结果与全部 106 项指标（含各类 F1、Latency、Token/成本）见
`benchmark-v4.json`。两轮各 19 样本，数值一致，未取平均、未删样本。

---

## 4. 是否发现增益

**主因准确率：未发现任何增益。** C/D/E/F/G/H/GRAPH_SEM/CF 全部 18/38；
A 仅 6/38（且假通过率 8/8）。这与 WP6 的误差分析一致：规则引擎已经拿到它能拿到的
9/19，剩下的 10 个错误缺的是**新假设生成能力**（403、时序、定位器、Flaky、静默忽略）
与**证据入口能力**（NOT_EVALUATED 不给诊断），不是更多探针或更漂亮的图。

**覆盖率：发现负增益。** 探针证据真实回灌后，预算 2/3 的覆盖率从 24/38 降到 16/38。
原因是事后查询的 `http_state` 探针拿到的是当前实时状态而非 Run 当时状态，
对正确的 UI_RENDERING 假设产生假反驳。反事实时间对齐臂 CF 回到 22/38，说明这是
**可修复的工程缺陷**（探针应读 Run 作用域状态快照），但仍不提升主因准确率。

**Evidence Graph：未发现增益。** 0/38 样本的主因因图而改变；图快照在探针前构建，
没有 `contradicts`/`temporal`/`derived_from` 边，D 臂只检查 `source_type` 集合。

因此本轮诚实结论是：**EG/AP 在当前接入方式下没有产生主因增益，AP 甚至有覆盖负增益**；
要产生增益必须先修复（1）探针结果回灌与 Run 作用域时间对齐、（2）图分析进入裁决层、
（3）规则层补齐 403/时序/定位器/Flaky 的假设生成。

---

## 5. Benchmark v4 场景集（14 个，11 类，独立 GT）

`scenarios_v4.py` 新增 14 个场景（要求 ≥10），覆盖：

多源冲突（2）、相似故障（2）、时序（1）、复合（1）、UI 正常后端失败（1）、
网络与应用错误混淆（2）、定位器波动（1）、Flaky（1）、权限错误（1）、
证据不完整（1）、派生重复证据（1）。

每个场景声明 `required_evidence` 证据签名、期望执行终态/判定、`seeded=False`、
`execution_state=BLOCKED_NOT_SEEDED`。GT 独立声明于 `V4GroundTruthManager`，
无评测令牌不可读；`isolation_check()` 实测 `no_token_read_count=0`、
`token_granted_read_count=14`、`isolated=True`。GT 只含权威主因类别、机制、
期望业务状态、分流与理由，不通过任何 API 暴露。

**v4 场景的真实执行 BLOCKED**：未在 TeamFlow 植入对应故障，且任务约束禁止修改
源码/seed；因此本轮不产出 v4 实测数值，也不把 v3 证据冒充为 v4 结果。
场景定义与 GT 隔离本身已可评测。

---

## 6. BLOCKED 项

| 项 | 状态 | 原因 |
|---|---|---|
| 受约束 LLM 诊断臂 | BLOCKED | `TG_LLM_PROVIDER` 未启用；未获外网计费授权；未发起请求，不以规则冒充 |
| v4 新场景真实执行 | BLOCKED_NOT_SEEDED | 未植入故障，禁止改源码/seed；仅有定义与独立 GT |
| 人工基线 | BLOCKED | 无真实人类工程师参与 |
| 统计显著性 | BLOCKED | n=19/轮过小，只报分子/分母/样本数，不做 CI 外推 |

---

## 7. 证据索引与复现

- 报告：`docs/assets/run009/benchmark-v4/benchmark-v4.json`
- 原始样本（每次实验完整保留）：`docs/assets/run009/benchmark-v4/samples.jsonl`、
  `samples.repeat1.jsonl`
- v4 场景与 GT 隔离：`benchmark/traceguard_benchmark/scenarios_v4.py`
- 误差分析：`docs/assets/run009/wp6/error-analysis-v3.json`
- EG/AP 审查：`docs/assets/run009/wp6/eg-ap-audit.json`
- 复现命令：`python scripts/bench_run009.py`（可选 `--allow-llm` 显式授权；
  默认不发起 LLM 调用）。脚本不修改任何既有源码或既有证据。
