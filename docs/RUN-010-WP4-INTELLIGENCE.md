# RUN-010 WP4 实施报告：诊断内核增强（Diagnosis v2）

> 生成时间：2026-11-24 CST（RUN-010-FINAL-CLOSEOUT 补齐）。代码提交：`132c09c`（+ `3ac6725` 格式修正）。
> 证据口径：本报告所有数字均来自最新 HEAD 的真实测试运行与 `docs/assets/run010/benchmark/` 原始 JSONL，
> 不复制历史报告中的估计值。状态枚举：IMPLEMENTED / VERIFIED / PARTIAL / BLOCKED。

## 1. 结论摘要

| 项 | 结论 |
|---|---|
| 实现状态 | **IMPLEMENTED / VERIFIED**：代码、13 项专项测试、接入正式诊断链路（首诊 + 重诊断） |
| 策略版本 | `strategy_version = "v2"`（`diagnosis_service.py` 诊断结果字段） |
| 图参与裁决 | **已参与**：GraphAdjudicator 成为主因裁决输入之一 |
| 图对准确率的影响 | **无提升**。Benchmark v4 两轮：C 臂（规则）与 D 臂（规则+图消融）主因准确率均 **64.3%（9/14）**，选择性准确率均 **88.9%（8/9）** |
| 图改变输出的样本 | **2/14（14.3%）**：改变了诊断输出，但未改变对错分布 |
| 未解决冲突检出 | **0/14**（14 个样本中未出现"权威支持 + 权威反驳"同时成立） |
| 真实探针回灌 | Benchmark 各臂 `probes_per_sample = 0`（评测过程未执行真实探针），因此 E/F/G 三臂实际等价于规则臂 |

**必须明确区分：Graph 参与裁决 ≠ Graph 提升诊断准确率。** 本轮没有证据表明图裁决提高了准确率；
D 臂与 C 臂的差异只在 2/14 个样本的预测输出内容上，最终对错完全一致。

## 2. 整体架构

```
Run 观测 (steps / network / page_errors / state)
   │
   ├─► 规则假设生成（diagnosis_service._build_hypotheses）
   │      · 5xx → API 缺陷假设        · 断言失败 → 产品断言/UI 渲染
   │      · 网络失败 → NETWORK        · 401/403 → AUTHORIZATION（本轮补齐）
   │      · 超时 → TIMING             · Flaky 特征 → 打标/兜底假设
   │
   ├─► 探针回灌（evidence.probe.feedback.assess）
   │      · ProbeSignal(observed_at, run_scope) → 时间对齐 ALIGNED / POST_HOC
   │      · POST_HOC 不确认/不排除；与 Run 期假设冲突 → 生成 TIMING 新假设
   │
   ├─► 证据元数据索引（graph.adjudication.evidence_meta_from_rows）
   │      · trust ∈ {TRUSTED, POST_HOC, UNTRUSTED, UNKNOWN}
   │      · integrity_check PASS/NOT_RUN 才算可用
   │
   ├─► GraphAdjudicator.adjudicate(hypotheses) → AdjudicationReport
   │      · 支持/反驳边、来源独立性、全局消重、时间窗、冲突
   │
   └─► DiagnosisEngine.diagnose(symptom, hypotheses, adjudication=...)
          · 规则状态计算 → 图裁决校正（降级/反驳/冲突）→ outcome / disposition
```

改动文件（`git show --stat 132c09c`）：

| 文件 | 变更 | 作用 |
|---|---|---|
| `packages/evidence/traceguard_evidence/graph/adjudication.py` | 新增 291 行 | 图裁决层（EvidenceMeta / HypothesisAdjudication / AdjudicationReport / GraphAdjudicator） |
| `packages/evidence/traceguard_evidence/probe/feedback.py` | +227/-76 | 时间对齐、POST_HOC 降权、TIMING 新假设 |
| `packages/evidence/traceguard_evidence/diagnosis/engine.py` | +61 | 裁决层接入 `diagnose()`，`graph_adjudication` 落库 |
| `apps/api/traceguard_api/services/diagnosis_service.py` | +136 | 403/401 → AUTHORIZATION；首诊与重诊断统一走裁决层；`strategy_version=v2` |
| `apps/api/traceguard_api/services/probe_service.py` | +5 | 探针信号透传 `observed_at` / `run_scope` |
| `tests/unit/test_intelligence_run010.py` | 新增 444 行 / 13 用例 | 本轮专项测试 |
| `tests/integration/test_active_diagnosis_feedback.py` | +41/-? | 事后探针语义修正（不再错误排除假设） |

## 3. Hypothesis 生成与更新机制

**生成（确定性规则，LLM 只提候选）**：`diagnosis_service._build_hypotheses()` 从 Run 的
step / network / page_errors / business_state 观测生成假设，每条假设携带
`failure_domain` + `failure_mechanism` + `supporting/contradicting` 证据引用。

**更新（探针回灌）**：`feedback.assess()` 把探针执行结果转为对假设的
SUPPORT / CONTRADICT / NONE，并输出 `HypothesisUpdate`（含 `time_alignment`）。
规则表见模块 docstring（http_state / network_trace / dom_snapshot / backend_log）。

**状态分层**：`CANDIDATE → SUPPORTED → CONFIRMED`，另有 `CONTRADICTED / EXCLUDED`。
约束（`engine.diagnose`）：

1. `CONFIRMED` 必须由规则判定（`rule.is_confirmed`）；
2. `suggested_by=llm*` 的假设即使规则判 CONFIRMED 也强制降级为 SUPPORTED（能力层 ≠ 事实层）；
3. 本轮新增：图裁决可**再降级/反驳**规则给出的结论（见 §5）。

## 4. Evidence Graph 的数据流

1. **证据落库**：Run 执行过程中证据写入 Evidence Store（`source_type` / `source_ref` /
   `captured_at` / `source_trust` / `integrity_check`）。
2. **元数据索引**：`evidence_meta_from_rows()` 把 Evidence Store 行转换为
   `dict[evidence_id, EvidenceMeta]`；`integrity_check` 非 PASS/NOT_RUN 视为完整性失败。
3. **假设边集**：假设的 `supporting` / `contradicting` 列表即图中的边，元素含
   `id`（证据 id）、`confirming`、`trust`、`time_alignment`。
4. **裁决**：`GraphAdjudicator(meta_index, run_started_at, run_ended_at).adjudicate(...)`
   输出 `AdjudicationReport{verdicts[], global_duplicates[], unresolved_conflicts[]}`。
5. **落地**：`Diagnosis.graph_adjudication` 字段整体写入诊断文档，前端/报告可复核
   （`explain()` 回答"哪些证据支持/反驳、是否同源、是否重复、时间是否匹配、是否有未解决冲突"）。

图在本轮之前只用于可视化与"升置信度"，本轮起真正进入裁决输入。

## 5. GraphAdjudicator 裁决逻辑

`packages/evidence/traceguard_evidence/graph/adjudication.py`：

| 规则 | 实现 |
|---|---|
| 全局消重 | `seen_ids` 跨假设去重，重复 id 记入 `duplicate_evidence`，只计首条 |
| 来源独立性 | 按 `source_ref`（无则 `evidence_id`）归并；同一来源多条证据 = 一条独立支持链，不叠加 |
| 权威确认 | 仅 `trust=TRUSTED` **且** `confirming=True` **且** 完整性通过 **且** 时间窗内 |
| 权威反驳 | 仅 `trust=TRUSTED` **且** 完整性通过 **且** 时间窗内 |
| 冲突 | `authority_confirmed and authority_refuted` → `has_conflict`，进入 `unresolved_conflicts` |
| 降权 | `integrity_check` 失败 → 记 note 并跳过，不能作为权威 |
| 数量无关 | 不用"支持证据条数"作为可信度（docstring 明确约束） |

引擎侧消费（`engine.diagnose`）：

- 裁决层"权威确认不成立" → 规则判定的 `CONFIRMED` **降级为 SUPPORTED**；
- 裁决层"权威反驳成立" → `CONTRADICTED`；
- 同时成立 → 记入 `unresolved_conflicts`；若无其他 CONFIRMED，`Disposition = MANUAL_REVIEW`
  （不强行在冲突中选唯一根因）。

## 6. 时间窗口与来源独立性

- 时间窗：`run_started_at = run.started_at`，`run_ended_at = run.ended_at`；
  证据 `captured_at > run_ended_at + 5s` 视为窗外（容忍 5 秒时钟/落库抖动）；
- 时间未知（`captured_at=0`）**视为匹配**，只留痕不阻断（避免误杀）；
- `time_alignment=POST_HOC` 一律不进权威链（见 §7）；
- 来源独立性：同一 `source_ref`（如同一 step、同一次探针执行）只算一条独立支持，
  防止"一次失败产生多条同源证据"被当成多重印证。

## 7. POST_HOC 证据处理

问题（RUN-009 误差分析暴露）：Run 结束后的探针查询反映的是"当前状态"，不是"Run 时状态"；
旧实现会让事后探针**错误反驳/排除** Run 执行时的正确假设。

本轮实现（`probe/feedback.py`）：

1. `ProbeSignal` 新增 `observed_at`（epoch）与 `run_scope`（是否 Run 作用域内采集）；
2. `_alignment()`：`run_scope=True` → `ALIGNED`；`observed_at > run_ended_at` → `POST_HOC`；
3. `POST_HOC` 的 `HypothesisUpdate.time_alignment = POST_HOC`，**不排除、不确认**，
   只在 `contributing_factors` 留痕；
4. 事后状态与 Run 期假设冲突时，不写"反驳"，而是生成
   `failure_mechanism=TIMING` 的新假设（写入延迟 / 查询滞后）；
5. 回灌到假设边集时 `trust="POST_HOC"`，因此裁决层永远不会用它做权威确认。

## 8. 403 / AUTHORIZATION 规则

`diagnosis_service` 观测到 `HTTP 401/403` 时生成独立假设（`failure_mechanism=AUTHORIZATION`），
理由：403/401 是目标服务器的确定性授权拒绝，此时"业务断言失败"是授权被拒的后果，
不应再单独算作产品断言缺陷的根因。证据引用取该 step 的 network + step 证据 id 去重集合。

## 9. Flaky 处理机制

`_flaky_check()`：同一 `testcase_version_id` 的历史 Run 中同时出现 PASSED 与 FAILED
（排除 NOT_EVALUATED）→ 标记 Flaky。

- 已生成假设：全部打 `flaky_characteristic=True`；
- 无假设时：生成一条 `failure_mechanism=TIMING` 的兜底假设（`confirming=False`，
  因此不会被判 CONFIRMED）；
- Flaky 结论不产生"通过"语义，只影响 disposition 与人工复核提示。

## 10. 13 项专项测试与实际结果

`tests/unit/test_intelligence_run010.py`（13 个用例，全部来自本轮提交）：

| # | 用例 | 覆盖点 |
|---|---|---|
| 1 | `test_http_403_generates_authorization_hypothesis` | 403 → AUTHORIZATION 假设 |
| 2 | `test_http_401_also_maps_to_authorization` | 401 同规则 |
| 3 | `test_adjudicator_deduplicates_and_flags_time_mismatch` | 全局消重 + 时间不匹配标记 |
| 4 | `test_adjudicator_blocks_confirmation_without_time_aligned_authority` | 无时间对齐权威 → 不得确认 |
| 5 | `test_engine_downgrades_confirmed_on_graph_refutation` | 图反驳 → CONFIRMED 降级 |
| 6 | `test_engine_records_conflict_from_graph` | 冲突记录（不静默选边） |
| 7 | `test_integrity_failed_evidence_cannot_confirm` | 完整性失败不可确认 |
| 8 | `test_evidence_meta_from_rows_parses_iso_and_integrity` | 元数据索引解析（ISO/epoch/integrity） |
| 9 | `test_multi_hypothesis_competition_and_abstention` | 多假设竞争与弃权 |
| 10 | `test_untrusted_supporting_cannot_confirm` | UNTRUSTED 支持不可确认 |
| 11 | `test_post_hoc_probe_generates_timing_hypothesis_not_refutation` | POST_HOC → TIMING 新假设而非反驳 |
| 12 | `test_run_scope_probe_keeps_authority` | Run 作用域探针保留权威 |
| 13 | `test_failed_probe_recorded_but_no_hypothesis_change` | 探针失败不产生支持/反驳 |

真实执行结果（2026-11-24，本地 `.venv`，见收尾报告 §测试证据）：

```
$ .venv/bin/pytest tests/unit/test_intelligence_run010.py -q
13 passed
```

另有 `tests/integration/test_active_diagnosis_feedback.py` 因语义变更同步修改（事后探针不再排除假设）。

## 11. Benchmark v4 中 Graph 的真实实验结果

数据源：`docs/assets/run010/benchmark/benchmark-v4.json` + `raw.round0.jsonl` / `raw.round1.jsonl`
（14 场景 × 2 轮，独立原始记录保留失败样本）。

| 指标（两轮一致） | 值 |
|---|---|
| `primary_cause_accuracy[C_rule]` | **0.6429 (9/14)** |
| `selective_accuracy[C_rule]` | **0.8889 (8/9)** |
| `primary_cause_accuracy[D_graph_ablation]` | **0.6429 (9/14)** |
| `selective_accuracy[D_graph_ablation]` | **0.8889 (8/9)** |
| `diagnosis_coverage[D_graph_ablation]` | 0.6429 (9/14) |
| `graph_changed_prediction_count` | **0.1429 (2/14)** |
| `conflict_detection_count` | **0.0 (0/14)** |
| `probes_per_sample[E/F/G]` | **0.0 (0/14)** |
| `primary_cause_correct[F_minus_G]` | **0.0 (0/14)** |

解读（严格按数据）：

1. 图裁决改变了 **2/14** 个样本的诊断输出，但**对错分布不变** → 不能宣称图提升准确率；
2. 14 个样本中未触发一次"权威支持 + 权威反驳"冲突 → 冲突路径只有单测覆盖，**无真实样本证据**；
3. 所有臂 `probes_per_sample = 0` → Benchmark 未执行真实探针，E/F/G（含时间对齐对照 G）
   实质等价于规则臂，`F_minus_G = 0` **不能**用来证明时间对齐有效或无效；
4. 样本量 14，未做统计显著性检验，任何差异都不应表述为显著。

## 12. 已知缺陷与下一步

| 项 | 状态 | 说明 |
|---|---|---|
| 图裁决无准确率增益 | PARTIAL（如实记录） | 当前图只在"减少错误确认"方向起作用，缺少"补足缺失证据"的正向能力 |
| 冲突路径无真实样本 | PARTIAL | `conflict_detection_count = 0/14`，需构造互斥证据场景补验 |
| 探针未参与 Benchmark | PARTIAL | `probes_per_sample = 0`；E/F/G 臂结论不成立，需接入真实探针后重跑 |
| 图裁决只降不升 | 设计取舍 | 保守策略（宁降级不误杀），代价是可能把正确 CONFIRMED 降为 SUPPORTED |
| 时间窗容差 +5s 为启发式 | 已知限制 | 极端慢落库会误判为窗外；目前只影响权威性判定，不改变事实记录 |
| 来源独立性依赖 `source_ref` 质量 | 已知限制 | `source_ref` 缺失时退化为 `evidence_id`，同源证据可能被计为独立 |
| LOCATOR 优先级 / 失败导航证据落盘 | NOT_IMPLEMENTED | 属 Benchmark 5 个场景缺口，本轮未做（不扩大范围） |

**下一步建议**（不在本轮范围）：构造互斥证据样本验证冲突路径；把真实探针接入 Benchmark
E/F/G 臂后重跑；评估"图补证"（用图找缺失证据）而非仅"图降权"。
