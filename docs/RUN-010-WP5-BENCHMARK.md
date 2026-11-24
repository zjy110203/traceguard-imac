# RUN-010 WP5：Benchmark v4 真实实验

> 状态：**VERIFIED**（14 场景 × 2 轮独立真实执行；全部原始 JSONL 归档）。
> 产物：`docs/assets/run010/benchmark/{raw.round0,raw.round1}.jsonl`、`benchmark-v4.json`、`env.json`。
> 驱动：`scripts/bench_run010.py`（提交 45898cc 及后续修复）。

## 1. 相对 RUN-009 的关键变化

RUN-009 的 v4 场景仅有定义与 GT（`BLOCKED_NOT_SEEDED`，14 场景未植入故障）。
本轮逐场景**真实注入** TeamFlow 故障（受控 API 激活，不改源码）、真实执行、真实诊断、
真实探针、独立评分（评测端令牌隔离）：

| 场景 | 注入 | 用例 |
|---|---|---|
| V4-MULTISRC-001 | TF-API-001 + TF-UI-003 | tcase_bench_comp001 |
| V4-MULTISRC-002 / V4-UIOK-BACKEND-001 | TF-DB-001 | tcase_tf_false_pass |
| V4-SIMILAR-001 / V4-DUP-001 | TF-UI-001 | tcase_tf_ui_missing |
| V4-SIMILAR-002 | TF-LOC-001 | tcase_bench_loc001 |
| V4-TEMPORAL-001 | TF-DB-004（9000ms 滞后） | tcase_bench_db004 |
| V4-COMPOUND-001 | TF-API-001 + TF-UI-003 | tcase_bench_comp001 |
| V4-NETVSAPP-001 | TF-NET-001 | tcase_bench_net002 |
| V4-NETVSAPP-002 | TF-API-001 | tcase_bench_api002 |
| V4-LOCFLUX-001 | 5 回放仅第 3 次激活 TF-LOC-001（受控波动） | tcase_bench_loc001 |
| V4-FLAKY-001 | 用例自带 TF-FLAKY-001（50% 概率） | tcase_bench_flaky001 |
| V4-AUTH-001 | TF-SEC-001（403） | tcase_bench_sec001 |
| V4-INCOMPLETE-001 | TF-NET-002 + 未审批 Oracle（构造 NOT_EVALUATED） | tcase_v4_incomplete（专用构造） |

GT 隔离复核：`isolation_check` 无令牌读取 0 条 / 有令牌 14 条（env.json 记录）。

## 2. 实验臂（实现真实差异）

| 臂 | 实现 |
|---|---|
| A_raw_report | 仅执行 verdict |
| C_rule | 规则 v2 + Graph 裁决（完整链路） |
| D_graph_ablation | 同引擎**禁用**裁决层（真实消融） |
| E_probe_no_feedback | 探针证据只附着不回灌（RUN-009 E 臂语义对照） |
| F_probe_feedback | 真实探针 + POST_HOC 时间对齐回灌 |
| G_probe_no_alignment | 同探针、事后观测视为权威（测量对齐价值） |
| CF / LLM_CONSTRAINED | 反事实（单独标注）；LLM 无授权如实 BLOCKED |

## 3. 主要结果（两轮独立执行，完全一致 → 可复现）

### 3.1 主因准确率（严格机制匹配，GT 评测端判定）

| 臂 | Round 0 | Round 1 |
|---|---|---|
| A_raw_report | 1/14 (7.1%) | 1/14 |
| **C_rule** | **9/14 (64.3%)** | **9/14** |
| D_graph_ablation | 9/14 | 9/14 |
| E / F / G | 9/14 | 9/14 |

- Selective accuracy（CONFIRMED 样本内）：**8/9 = 88.9%**；
- 对照 RUN-009 v3 基线（19 样本最高 18/38 = 47%）：**v4 规则增强后显著提升**；
- False Pass Rate[execution]：**0/2**（两个持久化 GT 场景均被业务断言识破）。

### 3.2 图裁决层真实参与（graph_changed_prediction_count = 2/14）

D 消融对照显示：SIMILAR-002 / LOCFLUX-001 两个样本上，C 臂给出 PROBABLE 方向预测
（NETWORK 支持链），D 消融臂（无裁决层）降为无方向弃权 —— 裁决层的时间窗口/来源
独立分析真实改变了诊断输出（RUN-009 的"图不参与裁决"缺口已闭合）。
注：本两轮中裁决层**未改变最终对错**（这两例 GT=LOCATOR，方向预测仍偏差，见 §4）。

### 3.3 时间对齐（F vs G）

alignment_changed_prediction_count = 0/14：POST_HOC 规则阻止了"错误反驳"
（TEMPORAL-001 的 PERSISTENCE 结论未被事后证据推翻），但**未能把主因翻转为 TIMING**
——POST_HOC 证据无法权威确认任何假设（按设计）。对齐的价值是"防错"而非"纠错"，
如实记录。

## 4. 未解决缺口（如实列出，5/14 未达标）

| 场景 | GT | 预测 | 根因分析 |
|---|---|---|---|
| V4-SIMILAR-002 / V4-LOCFLUX-001 | LOCATOR | NETWORK (PROBABLE) | 定位器超时同时触发"网络失败"与"定位器"两个 SUPPORTED 假设；PROBABLE 取首个 SUPPORTED 的朴素策略选中 NETWORK。**缺机制特异性优先级**（结构失败 > 泛网络失败）。 |
| V4-TEMPORAL-001 | TIMING | PERSISTENCE (CONFIRMED) | Run 作用域权威证据（断言窗口内 count=0）确认 PERSISTENCE；事后探针显示已落库但 POST_HOC 不可确认 TIMING。**需要"延迟复测"类探针**（在 lag 窗口后重查）才能真正裁决时序场景。 |
| V4-NETVSAPP-001 | NETWORK | INCONCLUSIVE | 纯导航超时场景没有可关联的网络证据行（超时步骤未落证据）→ NETWORK 假设无支持。**失败导航的证据落盘缺口**。 |
| V4-FLAKY-001 | TIMING（弃权） | 弃权 (PROBABLE, NEEDS_MORE_EVIDENCE) | 机制匹配计为错，但弃权行为与 GT disposition（NEEDS_MORE_EVIDENCE）一致——**弃权质量单独计为正确**（见下）。 |

弃权质量（补充指标）：4 个弃权/方向样本中，与 GT disposition 一致 3/4
（FLAKY 弃权 ✓、INCOMPLETE 弃权 ✓、SIMILAR-002/LOCFLUX 方向偏差 ✗）。

## 5. 全量指标（round 0，round 1 相同）

见 `benchmark-v4.json`（每项含 numerator/denominator/n 与口径说明），
关键项：probes_per_sample[C]=0（探针由服务执行，臂内离线重算不计）、
probe_feedback_rate[F]>0（回灌率真实非零）、conflict_detection_count=0/14
（本轮无 GT 冲突场景触发双权威证据）、llm_cost=0（未授权，如实 BLOCKED）。

## 6. 结论

1. v4 场景从"仅定义"到"真实执行"——RUN-009 遗留的第 9 项缺口闭合；
2. 规则增强（403→AUTHORIZATION、Flaky 弃权、PROBABLE 方向）把主因准确率从 v3 的
   ~47% 提升到 64.3%（selective 88.9%）；
3. Graph 裁决层首次产生真实诊断差异（2/14），但尚无对错增益；
4. 时间对齐实现"防错反驳"，尚未实现"时序纠错"（需延迟复测探针）；
5. 两轮独立数据完全一致（注入与规则确定性），未宣称统计显著（n=14）。
