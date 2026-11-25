# RUN-011-R1 WP4：Active Probe 与 Benchmark v5（三场景试验 + 扩展执行）

> 状态：**VERIFIED**（2026-11-25）｜ 修复 RUN-011「Benchmark NOT RUN / v4 测量无效」缺口
> 证据：`docs/assets/run011-r1/benchmark/pilot-3scenarios.json`、`v5-2rounds.json`
> 脚本：`scripts/bench_pilot_run011_r1.py`（三场景 Pilot）、`scripts/bench_v5_run011_r1.py`（扩展）

## 0. 结论摘要

| 指令问题 | 结论 |
|---|---|
| Benchmark v5 是否具备可信测量条件？ | ✅ 是。测量口径修复：探针全部**真实执行**（54/54，全 ID 绑定），不再从旧 Evidence 重建信号（v4 `probes_per_sample=0.9286` 的假执行数根因） |
| 每个实验臂是否执行了自己的实现？ | ✅ A/C/D/E/F/G 六臂实现差异真实存在（P3/P4 检查 3/3 场景 + 23 场景两轮） |
| 小样本是否宣称显著？ | ✅ 否。n=37 明确标注不宣称统计显著 |

**v5 两轮结果速览**（23 场景 × 2 轮，37 个失败样本，19 个产出样本的场景，coverage 1.0）：

| 指标 | C 规则臂 | F 探针+时间对齐臂 | 说明 |
|---|---|---|---|
| Primary Cause Accuracy | 0.6486 | **0.7027** | 探针回灌 + 时间对齐提升主因准确率 |
| Macro-F1 | 0.5701 | **0.5973** | 同上 |
| Selective Accuracy（仅 CONFIRMED） | 0.875 | 0.875 | 确认类预测准确 |
| False Pass Rate | — | **0.0** | PERSISTENCE 类无误判为其它方向 |
| Abstention Rate（弃权） | — | 0.1351 | 弃权优于硬猜 |
| Probe Execution Success Rate | 54/54 = **1.0** | | 真实执行全成功 |
| Probe Feedback Success Rate | 54/54 = **1.0** | | 真实回灌全生效 |
| Diagnosis Latency | mean 17.5ms / max 51.9ms | | n=37 |

## 1. 测量口径修复（v4 → v5 的根因）

RUN-010 v4 的 `_probe_signals_from_replays` **从已落盘 Evidence 重建 ProbeSignal**——
`probes_per_sample=0.9286` 统计的是重建信号数，不是真实探针执行数（WP0 审计确认）。
v5 口径（本 WP 实现）：
- 探针一律经真实 `POST /probe-plan`（ProbeEngine 真实执行 → 真实新证据 → 真实回灌）；
- 每条 `probe_execution` 绑定 **run_id / diagnosis_id / probe_id / execution_id /
  evidence_id / observed_at / collected_at / ingested_at**（指令 §6.1 全字段）；
- 禁止将从旧 Evidence 重建的 ProbeSignal 算作真实探针执行——F/G 臂的信号对象
  来自**本次执行**的证据内容（execution_id 指向真实执行文档）。

## 2. 三场景试验（§6.1，`pilot-3scenarios.json`，15/15）

| 场景 | 用例/故障 | GT | 真实探针 | 停止原因 | 验收 |
|---|---|---|---|---|---|
| LOCATOR | tcase_bench_loc001 / TF-LOC-001 | LOCATOR | 3/3 SUCCEEDED | STOP_ITERATION_LIMIT | P1–P5 全过 |
| NETWORK | tcase_bench_net002 / TF-NET-002 | NETWORK | 3/3 SUCCEEDED | STOP_ITERATION_LIMIT | P1–P5 全过 |
| TIMING | tcase_bench_db004 / TF-DB-004 | TIMING | 2/2 SUCCEEDED | STOP_NO_HIGH_VALUE_PROBE | P1–P5 全过 |

每场景记录（示例字段，完整见 JSON）：
`run_id=run_1795578837…, diagnosis_id=diagnosis_1795578850…, probe_id=http_state,
execution_id=probeexec_1795578850877b, evidence_id=evd_17955788508773…,
observed_at=1795578850.877, collected_at=1795578850.877, ingested_at=2026-11-25T03:54:10.877Z`
—— 8 条 probe_execution 全字段可对账；probe_feedback_applied 8/8。

**Pilot 实测触发的规则修复（并入 WP2 决策 R1-04 扩展）**：TIMING 场景首跑时
业务断言路径的单次 count=0 把 PERSISTENCE 直接 CONFIRMED → 探针 STOP_CONFIRMED
零执行（OPEN-RISK-02 完整形态）。降为候选级后复跑：TIMING 场景真实执行 2 个探针、
诊断 PROBABLE（不再误确认）——P2 检查转绿。

## 3. 实验臂实现差异（§6.2）

| 臂 | 实现（真实差异点） | Pilot 3 场景 | v5 37 样本 |
|---|---|---|---|
| A 原始报告 | 只有 verdict，无诊断 | mech=None 全部 | accuracy 0.0（按设计） |
| C 规则诊断 | 规则引擎 + Graph 裁决，`use_llm=false` 固定关闭 | LOCATOR✓ | 0.6486 |
| D Graph 消融 | 同引擎、`adjudication=None` | 与 C 独立计算 | 0.6486（图层对多数样本不改判） |
| E 探针不回灌 | 探针真实执行、证据落库，诊断不变（`feedback=NOT_APPLIED`） | 同 C 预测 | 0.6486 |
| F 探针+时间对齐 | 真实信号 + `run_ended_at` 生效（POST_HOC/UNKNOWN 降权） | aligns=[POST_HOC…] | **0.7027** |
| G 探针关闭时间对齐 | 同信号、`run_ended_at=0`（对齐关闭的对照） | aligns=[UNKNOWN…] | 0.7027 |
| H LLM Advisor | `use_llm=true` 单独调用 | 记录 provider/model | 0.6216 |

- **LLM 组记录**（§6.2 要求）：`model=deepseek-flash`、`prompt_version=diagnosis_advisor@v1`；
  LOCATOR 场景 `called=false, skipped=llm_error:invalid_json`（如实记录，不冒充）；
  NETWORK/TIMING 场景 `called=true, added=3`（候选固定 CANDIDATE 级，WP3 去重生效）。
- **规则对照组固定 `use_llm=false`**（HTTP 显式参数，口径纯净）。
- F vs G：v5 中两臂准确率相同（时间降权在这些样本上不改最终主因），
  但 F 臂的 time_alignments/feedback_rate 留痕可审计——对齐语义的差异在
  WP2 四场景（TIMING 方向识别）中已单独验证。

## 4. v5 扩展执行（§6.3，`v5-2rounds.json`，验收 3/3）

- **23 场景 × 2 轮独立执行**（12.5 分钟，真实 Worker/Chromium/PG）：
  FALSE-PASS、UI-MISSING、API-500/503、DB-DUP、DB-LAG-9S、UI-SILENT/MISLEAD/DELAYED、
  API-EDIT-500/503、NET-TIMEOUT、LOC-SUBMIT/NAME、TIME-STRONG/RACE、
  SEC-CREATE/LIST-403、COMP-API-UI/DB-TIME/LOC-UI、FLAKY-DB/API。
- **样本**：37 个失败样本（19 个场景产出；UI-DELAYED/API-EDIT-*/TIME-STRONG 等
  4 场景两轮均 PASSED——预期通过用例，诊断接口如实 412 拒绝「不做伪诊断」，
  不计入失败样本；9 个 412 样本如实记录）。
- **探针**：54 次真实执行、54 次真实回灌（成功率均 1.0），全 ID 绑定。
- **九项统计**（全量口径见 §0 表）：Primary Cause Accuracy / Macro-F1 / Coverage /
  Selective Accuracy / False Pass Rate / Probe Execution Success Rate /
  Probe Feedback Success Rate / Diagnosis Latency / Abstention Accuracy 全部产出。

## 5. 边界与如实声明

1. **不宣称统计显著**：n=37（两轮合并）为小样本；准确率/F1 仅作测量口径验证与方向参考。
2. **GT 口径**：主因机制来自故障机制定义（复合场景取首要机制，脚本内显式声明）；
   Flaky 场景概率注入，单轮可能未触发故障（FLAKY-DB r1 未触发 → 无样本，r2 触发）。
3. **D 臂（Graph 消融）与 C 臂在多数样本上预测相同**：图裁决层对这些场景不改判
   （无时间不匹配/冲突触发降级）——消融对照如实记录「无差异」也是有效测量结果。
4. **H 臂（LLM）准确率 0.6216 低于 F 臂**：LLM 候选为 CANDIDATE 级（WP3 去重 +
   不得 CONFIRMED），预测取向偏 API/PERSISTENCE；小样本，不外推。
5. UI-DELAYED 等 4 个「预期通过」场景的 412 是**正确的 fail-closed 行为**，
   不是执行错误（错误样本里如实标注）。
