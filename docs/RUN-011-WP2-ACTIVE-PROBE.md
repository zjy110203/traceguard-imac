# RUN-011 WP2：Active Probe 真正参与诊断

> 执行时间：2026-11-25 01:40–02:10 CST。
> 执行体：`scripts/probe_lab_run011.py`（纯 HTTP，驱动独立 API + 独立 Worker + 真实 Chromium）。
> 原始证据：`docs/assets/run011/wp2/probe-lab.json`（10/10 检查通过）。
> 使用拓扑：loop2（API `:8120`、TeamFlow `:8112`、Worker `run011-v1/v2/v3`、PG `traceguard_run011_l2`）。

## 0. 结论

1. **探针确实被真实执行**：一轮 Active Diagnosis 执行了 3 个只读探针
   （`network_trace` / `http_state` / `dom_snapshot`），全部 `SUCCEEDED`，落库 3 条新证据。
2. **证据真的回灌进假设**：`network_trace` 反驳 NETWORK、`http_state` 反驳 UI_RENDERING、
   `dom_snapshot` 支持 LOCATOR；诊断文档的假设证据集合因此改变（`hypothesis_signature=true`）。
3. **但最终结论没有变化**（PROBABLE → PROBABLE）。这是**设计语义**，不是"回灌没生效"：
   探针都在 Run 结束后执行，证据是 POST_HOC，按 §2.3 不允许反驳 Run 当时的事实、也不能用于
   CONFIRMED。如实记录，不宣称"探针提高了准确率"。
4. RUN-010 v4 的 Probe 臂测量口径无效（用已有证据重建信号），本轮已修复并在报告中更正。

## 1. 代码审计（§2.1）

链路：`POST /runs/{id}/probe-plan` → `ProbeService.plan_and_probe()`
→ `_evidence_gaps()`（确定性规则）→ `ActivePlanner.choose()`（EIG + 预算）
→ `ProbeEngine.execute()`（五项校验 + 预算原子占位）→ `Evidence.add_raw(source_type="probe")`
→ `DiagnosisService.reassess()` → `merge_signals()` → `DiagnosisEngine.diagnose()`。

审计发现（按严重度）：

| # | 发现 | 性质 | 处置 |
|---|---|---|---|
| A1 | 探针证据只写 `diagnosis_id`，**没有 run_id / observed_at / collected_at / ingested_at** | 违反指令 §2.3、§2.4 第 1/3/4 条；事后观测与历史状态无法区分 | 已修（见 §2.1） |
| A2 | 未注册探针返回 **201 + BLOCKED**，且生成执行记录、占用预算 | 调用方无法区分"探针不存在"与"被策略拒绝"；污染执行历史 | 已修：预算占位前 `404 UNKNOWN_PROBE`，不产生记录 |
| A3 | DOM 探针只看 `assert` 步骤的期望元素；定位器在 click 阶段失败时断言步骤不存在 → **期望集合为空 → 直接 skipped** | 这是 RUN-010 中 LOCATOR 与 NETWORK 长期混淆的机制性原因之一 | 已修：按"失败步骤目标 / 断言目标"分别判定 |
| A4 | Run 报告不含 `step.target` 与 `assertion.expected` | DOM 探针无法判断"失败步骤的目标是否在 DOM"，历史证据链断裂 | 已修：报告自包含目标与期望 |
| A5 | 定位器失败时 `business_state` 断言未评估，但 `_evidence_gaps` 不补业务状态缺口 | 缺少"是否真正落库"这一关键判别证据 | 已修：无有效业务判定时补 `http_state` 缺口（EIG 0.7） |
| A6 | LLM advisor 会在规则假设之外追加同机制候选（本轮多次真实运行观察到每次 2–3 条，含 2 条近似的 UI_RENDERING），未做语义去重 | 会稀释"独立支持来源"计数（WP3 §3.2 风险） | **未修**，记录为 WP3 待办 |
| A7 | `plan_and_probe` 的 `close_on_confirm` 参数未被使用（确认后固定 STOP） | 参数误导 | 记录，未改（避免行为变化） |

## 2. 本轮修复

### 2.1 证据溯源与三段式时间语义（§2.3）

`ProbeService.execute()` 写入的证据内容新增：

| 字段 | 语义 | 示例（真实） |
|---|---|---|
| `run_id` | 探针归属的 Run | `run_179554355506138440fe3adbc` |
| `diagnosis_id` | 探针归属的诊断版本 | `diagnosis_179554358012214b749b81f4f` |
| `probe_id` / `execution_id` | 探针与执行记录 | `network_trace` / `probeexec_…` |
| `observed_at`（epoch）+ `observed_at_iso` | **实际观测时刻**（探针收到目标响应的时刻） | `1795543580.1369019` |
| `collected_at`（epoch）+ `collected_at_iso` | **证据装配时刻** | `1795543580.1369128` |
| `ingested_at`（ISO8601） | **写入证据库时刻** | `2026-11-24T18:06:20Z` |

`ProbeResult` 新增 `observed_at`，由 `ProbeEngine` 在执行器返回后立即打点（不是入库时间）。

时间对齐规则（不变，但现在是可验证的）：
`resolve_alignment()` 用 `observed_at > run.ended_at` 判定 POST_HOC；POST_HOC 证据
**不持有 TRUSTED**、不触发排除、不参与 CONFIRMED，只以 `time_alignment=POST_HOC` 附着并留痕。

### 2.2 未注册探针 fail-closed

- 修改前：`POST /probes/execute {"probe_id":"nope"}` → 201 + `BLOCKED` + 新增执行记录。
- 修改后：→ **404** `{"code":"NOT_FOUND","message":"UNKNOWN_PROBE: nope"}`，执行记录数不变（4→4），
  预算不扣减。
- 回归：`test_unknown_probe_rejected_before_budget_and_creates_no_record`。

### 2.3 DOM 探针的机制特异性判定（LOCATOR vs UI_RENDERING）

修改前（真实缺陷）：只从 `assert` 步骤提取期望元素。定位器在 `click` 阶段失败时，
断言步骤从未执行 → 期望集合为空 → `skipped_reason=NO_EXPECTED_ELEMENT_TO_CHECK` → 无任何判定。

修改后：
- 分别提取 `failed_step_targets`（失败步骤的 target test_id）与 `assertion_targets`；
- 失败步骤目标不在 DOM → **SUPPORT LOCATOR**（"定位器无法解析，属于用例资产/定位策略问题"），
  且**不再**把"缺失"解释成渲染缺陷；
- 断言元素存在 → CONTRADICT UI_RENDERING；断言元素缺失 → SUPPORT UI_RENDERING。

真实回灌结果（本次实验）：

```
dom_snapshot SUCCEEDED
  SUPPORT    LOCATOR        "失败步骤的目标元素 ['create-submit'] 不在 DOM 中：定位器无法解析…"
  CONTRADICT UI_RENDERING   "DOM 已渲染期望元素，'未渲染'假设不成立"
```

### 2.4 报告自包含（支撑历史 Run 的证据判定）

`report.steps[]` 新增 `target` / `value`；`report.assertion_outcomes[]` 新增 `description` / `expected`。
用途：事后探针与复盘必须能读到"这一步想操作什么"，而不是依赖 catalog 的最新版本（版本已演进时
会得出错误结论）。

## 3. 真实链路验收（§2.4 十步）

场景：`tcase_bench_loc001`（用例内注入 `TF-LOC-001`：提交按钮 test_id 变更）。

| 步骤 | 结果 |
|---|---|
| 1. 有明确证据缺口的 Run | `run_1795544185946e83c41eb2234`，`NOT_EVALUATED`，24 条证据 |
| 2. 生成 Diagnosis | `PROBABLE` / `NEEDS_MORE_EVIDENCE`，2 个假设（NETWORK / LOCATOR，均为规则） |
| 3. 产生真实 Probe Plan | `probeplan_…`，3 个候选缺口（network / business / dom） |
| 4. 真实执行探针 | `network_trace`（1ms）、`http_state`（8ms）、`dom_snapshot`（6ms），全部 SUCCEEDED |
| 5. 保存 Evidence | 3 条 `source_type=probe`，见 §2.1 字段 |
| 6. 重新读取 Evidence | `GET /evidence/{id}` 可读，`captured_at` 与 `observed_at` 一致（差 <1ms） |
| 7. 验证来源与时间 | `run_id` / `diagnosis_id` / `probe_id` / `execution_id` 全部匹配 |
| 8. 回灌 Diagnosis | `applied_to_diagnosis=true`；2 条 hypothesis_update 落库 |
| 9. 假设是否重新评估 | 是：假设证据集合改变（`hypothesis_signature=true`），`evaluation_history` 3 条 |
| 10. 回灌前后比较 | 见下表 |

| 维度 | 回灌前 | 回灌后 | 变化 |
|---|---|---|---|
| outcome | PROBABLE | PROBABLE | 无（POST_HOC 不能升级/排除） |
| disposition | NEEDS_MORE_EVIDENCE | NEEDS_MORE_EVIDENCE | 无 |
| NETWORK 假设 | SUPPORTED，3 条 TRUSTED 支持 | +1 条 POST_HOC 反驳（网络无失败请求） | 有 |
| LOCATOR 假设 | SUPPORTED，3 条 TRUSTED 支持 | +1 条 POST_HOC 支持（`create-submit` 不在 DOM） | 有 |
| UI_RENDERING | 本次诊断未生成该假设 | —— | 无 |
| 评估历史 | 0 | 3 | 有 |

**未变化的原因（如实）**：三条探针都在 Run 结束后观测，按时间对齐规则属于 POST_HOC；
POST_HOC 不能提供 CONFIRMED 所需的 TRUSTED 权威证据，也不能排除 Run 作用域假设。
若要让探针改变最终结论，必须在 Run 执行**期间**采集（run_scope=True）或补充可对齐的证据源，
这是 WP3/WP4 的设计问题，不在本轮伪造。

## 4. 探针执行正确性覆盖（§2.2）

| 场景 | 验证方式 | 结果 |
|---|---|---|
| 正常探针 | 真实执行 3 个只读探针 | SUCCEEDED + 证据落库 |
| 探针失败 | 已有单测：DOM 无证据/日志缺失 → FAILED，不产生证据 | `test_probe_failure_*`（11 passed） |
| 超时 | `ProbeEngine` TimeoutError 分支（单测） | 不计为证据 |
| 超出预算 | 真实执行 `POST /probes/budgets/close` 后再 plan | **STOP_BUDGET_CLOSED**（`DIAGNOSIS_COMPLETED`） |
| 重复执行 | 同一 run/diagnosis 连续两次 `probe-plan` | 每次只执行未尝试过的缺口（2 步 → 2 步，`probes_used` 6→8），不重复同一缺口 |
| 重复回灌 | 同一探针执行结果重复调用 `reassess` | `merge_signals` 按 evidence_id 去重（`existing` 集合） |
| 过期证据 | POST_HOC 规则 + `run_ended_at` 比较 | 标记 `[POST_HOC]`，不参与确认/排除 |
| 事后观测 | `observed_at > run.ended_at` | `time_alignment=POST_HOC`，`run_scope=False` |
| 时间不一致 | `observed_at=0` 或无 run_ended_at | 保守按 ALIGNED 并写 `unknown_timing` 留痕 |
| 权限不足 | 单测：撤销 `probe.read` → `STOP_PERMISSION_INVALID` | 不执行、不产生证据 |

## 5. 可复核的数字（本轮真实）

- 探针执行成功数：**3/3**（一次 plan，max_iterations=3）
- 新证据：**3 条**，全部 `source_type=probe`、`source_trust=TRUSTED`、`run_id` 对齐
- 假设更新：**2 条**（1 条 CONTRADICT + 1 条 SUPPORT，各自附着新证据）
- 预算消耗：`probes_used=3`，`cost_used=1.4`，`elapsed=0.046s`（上限 50 / 100 / 60s）
- 未注册探针：404，执行记录 4→4
- 预算关闭：`STOP_BUDGET_CLOSED`
- 诊断评估历史：0 → 3

## 6. 未解决 / 不在本轮范围

| 项 | 状态 | 说明 |
|---|---|---|
| 探针改变最终结论 | **未实现** | 需要 Run 内探针或可对齐证据；POST_HOC 语义下不应改变 |
| LLM 假设重复 | **OPEN** | 同机制多条候选未去重，影响"来源独立性"统计 |
| 真实探针超时/失败 E2E | **PARTIAL** | 仅单测覆盖（无稳定手段在不破坏 TeamFlow 的情况下制造探针超时） |
| Probe Planner 的 LLM 模式 | **NOT_RUN** | `llm_probe_planner_enabled=false`，无授权成本预算 |
| 探针成本模型校准 | **NOT_RUN** | `estimated_cost` 仍是 provisional（0.2/0.3/0.5） |

## 7. 对 RUN-010 v4 结论的更正

| 旧表述 | 更正 |
|---|---|
| "`probes_per_sample`（所有臂）= 0" | A/C/D 臂为 0；E/F/G 实际 0.9286（13/14）**但该数字来自已有证据重建的 ProbeSignal，不是新执行的探针** |
| "E/F/G 臂因无真实探针而不构成有效消融" | 结论方向正确，但原因应表述为"探针未真实执行（`probes_used_service` 0–1）+ 信号由证据重建" |
| "`primary_cause_correct[F_minus_G]=0/14`" | 数值正确，但不能解释为"时间对齐无价值"——两组输入不是新执行探针的产物 |

Benchmark v5 的重做条件与现状见 `docs/RUN-011-WP4-BENCHMARK.md`。
