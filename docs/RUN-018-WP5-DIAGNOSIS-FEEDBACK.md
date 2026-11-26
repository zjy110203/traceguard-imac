# RUN-018 WP5：Active Probe → Diagnosis Feedback 真正闭环

> 时间：2026-11-27
> 范围：探针证据契约 / 证据反馈规则 / 五个真实场景 / 预算与安全 / 前端诊断详情
> 测试：`tests/integration/test_diagnosis_feedback_scenarios_run018.py`（10）、
> `tests/unit/test_probe_contract_run018.py`（9）

---

## 1. 目标业务流程（闭环）

```
Run → Evidence Collection → Diagnosis vN → Hypotheses → Evidence Graph
→ Active Probe Plan → Probe Execution → New Probe Evidence
→ Evidence Trust / Time Alignment → Graph Adjudication → Diagnosis vN+1 → Conclusion
```

不变量：

- **新诊断必须引用新增证据**：每次重诊断生成 `diagnosis_revision` 版本记录，
  携带 `new_evidence_ids`（探针新增证据），诊断文档同步更新 `probe_evidence_ids`；
- **原诊断不得覆盖**：版本链（kind=`diagnosis_revision`）追加写、不可变。
  v1 = 原始诊断快照（`before=None`）；每次重诊断追加 vN+1，其中 `before` 为原
  Diagnosis 完整快照、`after` 为新 Diagnosis 快照。活文档（活的诊断文档）继续
  演进以兼容既有闭环 API，但任何历史版本都可从版本链完整还原。

## 2. 5.2 探针证据契约

新增内部模型：`packages/evidence/traceguard_evidence/probe/contract.py`
（不改公共 Schema 既有字段，全部为新增/别名，向后兼容）。

一次探针执行由**同一份契约结构**同时写入 `probe_execution` 文档与
`source_type=probe` 的 raw evidence content：

| 字段 | 说明 |
|---|---|
| `probe_id` / `probe_execution_id` | 探针与执行实例（`execution_id` 别名并存） |
| `run_id` / `diagnosis_id` / `workspace_id` | 新证据与 Run、诊断版本、Workspace 强关联 |
| `probe_status` | SUCCEEDED / FAILED / TIMED_OUT / BLOCKED / **UNKNOWN** |
| `observed_at` / `captured_at` | 真实观测时刻 / 装配为证据时刻（epoch + ISO） |
| `source_type` / `source_trust` | `probe`；SUCCEEDED=TRUSTED，其余=UNKNOWN |
| `time_alignment` | ALIGNED / POST_HOC / UNKNOWN（与 `probe.feedback` 同一口径） |
| `evidence_refs` / `raw_result_ref` | 新增证据 id；原始结果指向 `probe_execution:<id>` |
| `failure_reason` / `duration_ms` / `cost` | 失败原因 / 耗时 / 预算消耗 |
| `idempotency_key` | 显式键=幂等重放；自动键唯一（延迟复测不被误去重） |

幂等语义：

- 显式 `idempotency_key` 的重复请求**只执行一次**（返回原执行记录
  `idempotent_replay=true`，不重复扣预算）；
- 未提供时生成 `auto:<probe>:<hash>` 唯一键——延迟复测与首查参数可完全相同，
  但它们是两次独立观测，绝不共享幂等键。

## 3. 5.3 证据反馈规则（确定性）

规则内核在 `probe/feedback.py`（补强）与 `diagnosis/engine.py`：

| 规则 | 实现 |
|---|---|
| Probe PASS 不能自动确认根因 | 确认只来自证据内容（TRUSTED+directly_confirming）经确定性裁决；单查询 count=0 仅支持不确权（R1-04） |
| Probe FAIL ≠ 业务根因确认 | `PROBE_FAILED_NOT_EVIDENCE`：零证据、零假设变更 |
| Probe UNKNOWN 不提升置信度 | `PROBE_UNKNOWN_NOT_EVIDENCE`：状态与置信度零变化 |
| Probe 超时不伪造证据 | TIMED_OUT 不落任何证据（仅 SUCCEEDED 落证据） |
| Probe 失败不改 Run Verdict | 重诊断链路不触碰 `run.test_verdict`（场景 E 断言） |
| POST_HOC / UNKNOWN 时间证据不作权威 | `upd()` 强制 confirming=False、exclude=False；裁决层时间门禁不变 |
| LLM 假设不进 CONFIRMED | 引擎内 LLM 降级规则（既有，回归保持） |
| 新证据关联 Run/Workspace/时间 | 契约强制 `run_id`/`workspace_id`/`observed_at`/`captured_at` |
| 冲突证据交 GraphAdjudicator | 重诊断走同一裁决口径（`GraphAdjudicator.adjudicate`） |
| **5xx 后果守卫（新增）** | Run 存在 5xx 时 http_state 探针不得确权 UI_RENDERING（症状不能冒充根因） |

每次重诊断保留（`evaluation_history` + `diagnosis_revision` 双写）：

原 Diagnosis（before 快照）、新 Diagnosis（after 快照）、新增 Evidence、
假设变化（`hypothesis_changes`：状态 before→after、是否探针新增）、
置信度变化（`confidence_changes`，置信度为确定性启发式标量，仅作对比展示）、
最终裁决原因（`final_reason`：为什么确认 / 为什么弃权）。

## 4. 5.4 五个真实场景（测试）

`tests/integration/test_diagnosis_feedback_scenarios_run018.py`（真实 Runtime +
DiagnosisService + ProbeService + GraphService，确定性探针执行器）：

| 场景 | 验证要点 | 结果 |
|---|---|---|
| A DB-001 永久持久化故障 | 独立读取 + 延迟复测均 count=0 → PERSISTENCE 的 TRUSTED 证据保留、两次探针证据附着不确权、**不生成 TIMING**（与 DB-004 严格区分） | PASS |
| B DB-004 状态读取滞后 | 即时读 0、延迟复测恢复 → `rule:probe-timing` 生成"查询滞后"TIMING 假设、PERSISTENCE 不确认/不被事后证据排除、反驳以 POST_HOC 附着 | PASS |
| C API 5xx | API 假设唯一 CONFIRMED 根因（Run 窗口内证据时间对齐、authority_confirmed）；UI_RENDERING 不被探针再次确权（ALIGNED 探针也被 5xx 守卫拦截）；POST_HOC 探针证据不具权威 | PASS |
| D LOC-001/002 定位器变化 | 独立用户可见操作成功（DOM 期望元素在）、业务已落库（count≥1）→ 无业务失效 CONFIRMED、不分流 PRODUCT_DEFECT、LOCATOR 方向有机制特异性 DOM 证据 | PASS |
| E Probe 不可用 | 产生 UNKNOWN：零证据、假设状态/置信度零变化、无错误 CONFIRMED、Run Verdict 不变 | PASS |

补充规则测试：Probe PASS 无判定内容不产生任何更新；同一证据缺口不重复尝试、
迭代上限 `STOP_ITERATION_LIMIT`（不允许无限重试）。

## 5. 5.5 Probe 预算与安全

- **预算体系保留**：每 Run/Diagnosis 独立 `ProbeBudget`、CAS 持久化、fail-closed（未改动）；
- **不允许无限重试**：`plan_and_probe` 迭代上限 + 同 gap 不重复尝试（新增测试）；
- **写入型探针隔离与清理**：`ProbeDefinition` 新增 `isolation` / `cleanup_policy`；
  `read_only=False` 而未声明两者 → 执行前拒绝 `WRITE_PROBE_REQUIRES_ISOLATION_AND_CLEANUP`
  （与既有 TeamFlow 探针清理接口 WP3 的 token 隔离配套）；
- **Ground Truth 永不进入 Runtime Diagnosis**：新增 `_strip_ground_truth()`
  递归剔除 `ground*truth*` 键后才进入 LLM 证据摘要；既有
  `target_policy` 对 `/api/ground-truth` 的阻断保留；
- **Benchmark 代码不被 Runtime Diagnosis 导入**：架构测试
  `test_runtime_diagnosis_does_not_import_benchmark_or_ground_truth` 扫描
  `traceguard_evidence/**` 与诊断/探针/图服务源码，禁止 import
  `traceguard_benchmark` / `traceguard_teamflow`（评测侧单向调用运行侧，反向禁止）。

## 6. 5.6 前端（Diagnosis 详情页）

`apps/web/src/views/DiagnosisDetailView.vue` + `types.ts`：

- **最终结论与裁决原因**卡片（为什么确认 / 为什么弃权 + 版本号）；
- **重诊断过程（诊断版本链）**卡片：v1=原始诊断、每次重诊断的 before→after
  结论、假设变化表（状态/置信度 before→after、探针新增标记）、新增证据
  （可点击跳转）、每版裁决原因；
- **探针计划**步骤表新增：时间对齐徽标（ALIGNED/POST_HOC/UNKNOWN）、成本、
  新增证据跳转；探针执行记录新增失败原因/时间对齐/成本/零证据标识；
- **引用证据**列表展示证据可信度（source_trust）与时间对齐，条目锚点可寻址；
- **证据图（Graph）**卡片内嵌 GraphView：点击 Evidence 节点 → 滚动并高亮
  对应证据条目（不在列表中则先加载）——Graph 与 Evidence 双向可跳转。

`npm run typecheck`（vue-tsc）通过。

## 7. API

- 新增 `GET /diagnoses/{diagnosis_id}/revisions`：诊断版本链（RBAC 与诊断详情同口径）；
- `probe_execution` 文档 / probe 证据 content 新增契约字段（向后兼容）；
- 既有 `/runs/{run_id}/probe-plan`、`/probes/execute` 返回体新增契约字段（纯增量）。

## 8. 测试与验证

- 新增：`tests/integration/test_diagnosis_feedback_scenarios_run018.py`（10 项）、
  `tests/unit/test_probe_contract_run018.py`（9 项）；
- 既有反馈/时间三态/诊断规则测试全部保持通过（未弱化任何断言）；
- 全量：`.venv/bin/python -m pytest tests/ -q -x --timeout=120`。

## 9. 设计取舍记录

1. **活文档 + 不可变版本链**：既有闭环测试与 API 要求诊断文档随回灌演进
   （如 `test_single_probe_count_zero_*` 直接读取活文档断言），因此"原诊断不得
   覆盖"以**版本链不可变快照**（`diagnosis_revision`）实现，而非冻结活文档；
2. **置信度为启发式标量**：状态基线 + 确认证据加成 + POST_HOC/UNKNOWN 封顶 0.4，
   仅用于版本间对比与展示，不参与 CONFIRMED 判定（判定仍是二值确定性规则）；
3. **UNKNOWN 是一等探针状态**：区别于 FAILED（探针自身失败）——"不可用/无法
   判定"（如状态服务 502/503/504）由 `ProbeUnavailable` 显式表达，语义是
   "未提供证据"，两者都不污染诊断，但审计可区分。
