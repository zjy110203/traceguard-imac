# RUN-009 WP6：Agent 智能诊断能力提升

> 范围：对 RUN-008 Benchmark v3 真实证据做逐场景误差分析；审查 Evidence Graph（EG）与
> Active Probe（AP）是否真正参与推理；用真实内核函数离线重算量化增益/负增益。
> 证据：`docs/assets/run009/wp6/error-analysis-v3.json`、`eg-ap-audit.json`、
> `docs/assets/run009/benchmark-v4/{benchmark-v4.json,samples*.jsonl}`。
> 口径：n=19/轮，2 轮合并 n=38；**不做统计显著性宣称，不外推**。

---

## 1. 19 个计分场景逐场景误差分析

### 1.1 原始证据与四臂预测（每轮 19 个样本，两轮结果一致）

| 场景 | GT | A 原始报告 | C 规则 | D 图 | E 探针 | C 是否正确 | C 是否弃权 | 探针状态 | 探针数 | 探针回灌 | 新证据是否改变假设 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| TF-DB-001 | PERSISTENCE | NONE | PERSISTENCE | PERSISTENCE | PERSISTENCE | ✅ | 否 | OK | 2 | 否 | 否 |
| TF-DB-002 | PERSISTENCE | NONE | PERSISTENCE | PERSISTENCE | PERSISTENCE | ✅ | 否 | OK | 2 | 否 | 否 |
| TF-DB-004 | **TIMING** | NONE | **PERSISTENCE** | **PERSISTENCE** | **PERSISTENCE** | ❌ | 否 | OK | 2 | 否 | 否 |
| TF-UI-001 | UI_RENDERING | UI_RENDERING | UI_RENDERING | UI_RENDERING | UI_RENDERING | ✅ | 否 | OK | 3 | 否 | 覆盖下降（见 3.2） |
| TF-UI-002 | UI_RENDERING | UI_RENDERING | UNKNOWN | UNKNOWN | UNKNOWN | ❌ | **是** | OK | 3 | 否 | 否 |
| TF-UI-003 | UI_RENDERING | UI_RENDERING | UI_RENDERING | UI_RENDERING | UI_RENDERING | ✅ | 否 | OK | 3 | 否 | 覆盖下降（见 3.2） |
| TF-API-001 | API | NONE | API | API | API | ✅ | 否 | OK | 2 | 否 | 否 |
| TF-API-002 | API | UI_RENDERING | API | API | API | ✅ | 否 | OK | 3 | 否 | 否 |
| TF-NET-002 | NETWORK | UI_RENDERING | UNKNOWN | UNKNOWN | UNKNOWN | ❌ | 是 | OK | 3 | 否 | 否 |
| TF-LOC-001 | LOCATOR | NONE | UNKNOWN | UNKNOWN | UNKNOWN | ❌ | 是 | OK | 3 | 否 | 无诊断结论 |
| TF-LOC-002 | LOCATOR | NONE | UNKNOWN | UNKNOWN | UNKNOWN | ❌ | 是 | OK | 3 | 否 | 无诊断结论 |
| TF-TIME-003 | TIMING | NONE | UNKNOWN | UNKNOWN | UNKNOWN | ❌ | 是 | OK | 3 | 否 | 无诊断结论 |
| TF-SEC-001 | AUTHORIZATION | UI_RENDERING | UNKNOWN | UNKNOWN | UNKNOWN | ❌ | 是 | OK | 3 | 否 | 否 |
| TF-SEC-002 | AUTHORIZATION | UI_RENDERING | **UI_RENDERING** | UI_RENDERING | UI_RENDERING | ❌ | 否 | OK | 3 | 否 | 否 |
| TF-COMP-001 | API | NONE | API | API | API | ✅ | 否 | OK | 2 | 否 | 否 |
| TF-COMP-002 | PERSISTENCE | NONE | PERSISTENCE | PERSISTENCE | PERSISTENCE | ✅ | 否 | OK | 2 | 否 | 否 |
| TF-COMP-003 | LOCATOR | NONE | UNKNOWN | UNKNOWN | UNKNOWN | ❌ | 是 | OK | 3 | 否 | 无诊断结论 |
| TF-FLAKY-001 | PERSISTENCE | NONE | PERSISTENCE | PERSISTENCE | PERSISTENCE | ✅ | 否 | OK | 2 | 否 | 否 |
| TF-FLAKY-002 | API | UI_RENDERING | **UI_RENDERING** | UI_RENDERING | UI_RENDERING | ❌ | 否 | OK | 3 | 否 | 覆盖下降（见 3.2） |

### 1.2 混淆矩阵（C 臂，19 样本/轮）

| GT \ 预测 | API | AUTHORIZATION | LOCATOR | NETWORK | PERSISTENCE | TIMING | UI_RENDERING | UNKNOWN |
|---|---|---|---|---|---|---|---|---|
| API (4) | **3** | 0 | 0 | 0 | 0 | 0 | 1 | 0 |
| AUTHORIZATION (2) | 0 | 0 | 0 | 0 | 0 | 0 | 1 | 1 |
| LOCATOR (3) | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 3 |
| NETWORK (1) | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 1 |
| PERSISTENCE (4) | 0 | 0 | 0 | 0 | **4** | 0 | 0 | 0 |
| TIMING (2) | 0 | 0 | 0 | 0 | 1 | 0 | 0 | 1 |
| UI_RENDERING (3) | 0 | 0 | 0 | 0 | 0 | 0 | **2** | 1 |

对角线 9/19；`UNKNOWN` 7/19（其中 4 个根本无诊断结论）。

### 1.3 10 个错误案例库（根因归类）

| 错误模式 | 场景 | 机制解释 | 需要但缺失的证据 |
|---|---|---|---|
| 症状冒充根因（时序→持久化） | TF-DB-004 | 状态查询滞后 9000ms，断言窗口内读旧值；规则把 `failed_biz` 且无 5xx 直接判 PERSISTENCE 并 CONFIRMED | 延迟读取证据、`duration_ms` 时序证据 |
| 症状冒充根因（403→UI） | TF-SEC-002 | 403 触发 UI 断言失败，规则没有 403 模式，把表层 UI 失败当主因 | HTTP 状态码证据（403） |
| 规则不识别 | TF-SEC-001 | 同样缺 403 模式，直接 INCONCLUSIVE | HTTP 状态码证据（403） |
| 无假设可裁决 | TF-UI-002 | 提交被静默忽略、无失败断言 → 规则不生成任何 Hypothesis | DOM 快照 + 业务状态证据 |
| 确认门槛未满足 | TF-NET-002 | 网络假设缺少 `TRUSTED + directly_confirming`，且探针未回灌 | 可确认的网络失败证据 |
| 执行中断无诊断 | TF-LOC-001/002、TF-COMP-003 | `NOT_EVALUATED` 使 diagnose 接口拒绝（契约），样本没有任何诊断结论 | DOM/定位器证据 + 允许"只补证据不下结论"的路径 |
| 执行中断无诊断 | TF-TIME-003 | 双提交竞态执行 FAILED，同上 | 多次执行差异证据 |
| 单次证据不足 | TF-FLAKY-002 | 该次执行未观测到 5xx，规则判 UI_RENDERING（Flaky 特征只作标注，不参与裁决） | 多次执行差异证据 |

### 1.4 为什么 D/E 与 C 同为 9/19（核心结论）

这不是三个臂碰巧同分，而是两条具体通路都失效：

1. **D（Evidence Graph）没有参与主因裁决**。`graph_enhanced` 只读取图中 Evidence 节点的
   `source_type` 集合；若同时含 `browser`+`api` 且原诊断 `outcome == PROBABLE`，
   才把它提升为 `CONFIRMED`。但 C 臂 12 个有结论样本里绝大多数本来就是
   `CONFIRMED`，提升逻辑从未触发。两轮共 38 个计分样本中
   `D 与 C 主因类别不同` 的样本数 = **0/38**。
2. **E（Active Probe）闭环断裂**。RUN-008 的 `probe-plan` 调用**没有传 diagnosis_id**，
   `probe_service.plan_and_probe` 因此走 `applied_to_diagnosis=False` 分支：
   100 个探针步骤全部"证据已落库但未回灌任何诊断"，`assessments` 全为空。
   `diag_after_probe` 与 `diag_rule` 同源。两轮 `E 与 C 主因不同` = **0/38**。

因此 C/D/E 的 9/19 完全来自确定性规则引擎的模式匹配（业务断言失败→PERSISTENCE、
网络 5xx→API、UI 断言失败→UI_RENDERING），EG/AP 对主因准确率贡献为 0。

---

## 2. Evidence Graph 是否真正参与推理

审查对象：两个 pass 共 38 个样本的真实图快照（证据见 `eg-ap-audit.json`）。

| 维度 | 实测 | 结论 |
|---|---|---|
| 节点 | 1880 个，含 Run/Step/Event/Evidence/Assertion/Hypothesis/Source | 结构完整 |
| 边 | 2298 条；`parent_child` 398、`emitted_by` 282、`same_execution_context` 172、`evaluates` 1298、`supports` 148 | 关联边占绝对多数 |
| basis（建立依据） | 2298/2298 条边都有 basis | 可复核性强 |
| 时间 | `temporal` 边 0 条 | **无时序推理能力** |
| 来源 | 图含 `browser`/`network`/`api`/`probe` source_type；D 臂只读该集合 | 只用于置信度装饰 |
| 可信度 | Evidence 节点有 `trust`/`integrity` 属性 | 已记录，但 D 臂未读 |
| 冲突 | `contradicts` 边 0 条 | 图里没有负向证据边，无法做冲突裁决 |
| 派生/重复 | `derived_from` 边 0 条 | 来源独立性与重复证据消重在图中未生效 |
| 直接因果 | `direct_causal` 边 0 条 | 符合"不轻易断言因果"的约束，但也意味着图不提供因果链 |
| 主因改变 | 0/38 | **EG 未真正参与主因推理** |

补充：D 臂使用的图快照是探针执行**之前**构建的（`/graph` 在 `/probe-plan` 之前调用），
因此图中根本没有 ProbeExecution 节点与探针派生证据——即使 `graph_service` 支持这些边，
该臂也看不到。这是"图未参与"的直接结构性原因。

**结论**：EG 具备数据层能力（节点、边、basis、trust、integrity），但在当前 benchmark 通路中
只被用作 `source_type` 集合的布尔检查。它需要被接到假设裁决层（独立佐证计数、
冲突边、时序边），否则只是装饰。

---

## 3. Active Probe 的信息增益 / 成本 / 风险 / 停止条件

### 3.1 设计与实测

- 白名单只读探针 4 种：`http_state`(cost 1.0)、`network_trace`(0.2)、
  `dom_snapshot`(0.2)、`backend_log`(0.3)；权限 `probe.read`，元预算 provisional
  （50 次/100 成本/60 秒，P06 未冻结）。
- EIG 评分公式 `Score = 1.0*EIG − 0.3*Cost − 0.5*Risk`；停止条件完整：
  CONFIRMED / 权限失效 / 冲突未解 / 无高价值探针 / 预算耗尽 / 边际价值不足。
- 实测：100 个探针步骤全部 SUCCEEDED；停止原因
  `STOP_NO_HIGH_VALUE_PROBE` 14 次、`STOP_ITERATION_LIMIT` 24 次（两轮 38 样本合计）；
  **0 次 `STOP_CONFLICT_UNRESOLVED`、0 次预算类停止**——说明停止条件大多由迭代上限
  与"无高价值缺口"触发，而非诊断信息已足够。
- **回灌率 0/100**（核心缺陷），详见 1.4。

### 3.2 真实内核重算后的增益/负增益（预算消融）

用真实 `merge_signals` + `DiagnosisEngine.diagnose` 离线重算，n=38：

| 臂 | 探针预算 | 主因准确率 | 覆盖率 | Selective Acc. |
|---|---|---|---|---|
| C_rule | 0 | 18/38 | 24/38 | 18/24 |
| F_probe_feedback | 1 | 18/38 | 24/38 | 18/24 |
| G_probe_feedback | 2 | 18/38 | **16/38** | 14/16 |
| H_probe_feedback | 3 | 18/38 | **16/38** | 14/16 |
| CF 时间对齐反事实（**非实测**） | 3 | 18/38 | 22/38 | 18/22 |

发现：

1. **主因准确率在所有探针预算下都不变（18/38）**。AP 只能增强/削弱已有假设，
   不能创造新假设；10 个错误案例里 7 个根本没有正确机制的假设存在。
2. **b2/b3 造成覆盖净损失 8/38**（24→16）。原因不是"探针无用"，而是**探针时间不对齐**：
   `TF-UI-001`/`TF-UI-003` 的业务断言 `a_biz_count=PASSED`（数据已落库），
   但探针在事后查询当前实时状态得到 `count=0`，于是对正确的 UI_RENDERING 假设
   产生假反驳（TRUSTED contradicting → CONTRADICTED → INCONCLUSIVE）。
   这是跨场景状态污染，不是目标系统故障。
3. 反事实臂 CF 只用 Run 自身业务断言推导"当时状态"，覆盖率回到 22/38，
   说明**时间对齐能消除大部分负增益**，但仍不能修正任何主因错误。

---

## 4. 多假设竞争四态

引擎实现五态：`CANDIDATE / SUPPORTED / CONTRADICTED / CONFIRMED / EXCLUDED`，
并有确定性确认规则（`TRUSTED + directly_confirming` 且无可信反驳 → CONFIRMED；
LLM 提出的假设固定降级为 SUPPORTED，永不 CONFIRMED）。

但 38 个真实样本的探针前假设状态分布为：
`CONFIRMED 24`、`SUPPORTED 2`、`CANDIDATE/CONTRADICTED/EXCLUDED 0`。

即：规则一旦生成假设就几乎直接确认；竞争、反驳、排除三态基本不运转。
`unresolved_conflicts` 也从未触发（因此 `STOP_CONFLICT_UNRESOLVED` 0 次）。
**多假设竞争在框架层存在，在数据层没有真正发生。**

---

## 5. EG / AP 在哪类故障产生增益，何时无必要

| 故障类型 | EG/AP 是否有增益 | 依据 |
|---|---|---|
| 业务状态类（PERSISTENCE, 4 个） | 无必要 | 业务断言 + 权威状态已直接确认，规则 4/4 命中；探针 count=0 只是重复 |
| 接口 5xx 类（API, 4 个） | 无必要（已确认样本）；有潜在增益（Flaky 未观测到 5xx 的样本） | 规则命中 3/4；缺的是多次执行证据，不是单条网络探针 |
| UI 渲染类（UI_RENDERING, 3 个） | 当前**负增益**；时间对齐后中性 | 实时状态探针造成假反驳；DOM 探针在 v3 样本里 `NO_EXPECTED_ELEMENT_TO_CHECK` |
| 时序类（TIMING, 2 个） | 潜在增益但未实现 | 需要 `temporal` 边/延迟读取探针；当前图 temporal 边 0 |
| 网络类（NETWORK, 1 个） | 潜在增益但未实现 | 需要可确认的网络失败证据；当前探针未回灌 |
| 权限类（AUTHORIZATION, 2 个） | 无（探针帮不上） | 缺的是 403 状态码规则，不是更多证据 |
| 定位器/执行中断类（LOCATOR, 3 个 + TIME-003） | 无（探针帮不上） | 诊断接口在 NOT_EVALUATED 时拒绝，问题在证据入口而非诊断层 |

**结论**：AP 的增益前提是"已有正确方向的假设 + 探针能消除不确定性"；
本数据集中满足该前提的样本极少。EG 的增益前提是"图分析结果进入裁决层"；
目前未接通。

---

## 6. LLM 适合做什么 / 哪些必须确定性内核负责

**LLM 适合（能力层）**

- 在症状/步骤/证据摘要上提出**候选假设与排查方向**，尤其规则没有覆盖的长尾
  （403、竞态、时序）；固定 `CANDIDATE` 级，不得 CONFIRMED。
- 对候选探针做**排序建议**（`_apply_llm_advice` 只允许在规则 EIG 基础上限幅上调
  +0.15、≤0.95），不能新增探针、不能绕权限/预算/参数校验。
- 把技术证据整理成人类可读的解释与修复建议；不参与事实判定。

**必须由确定性内核负责（事实层）**

- Hypothesis 的 `CONFIRMED` 裁决：`TRUSTED + directly_confirming`、无未解反驳，
  且来源独立性/派生去重可复核。
- 探针的五项校验（权限/资源范围/风险/预算/参数 Schema）与白名单、只读边界。
- 预算的跨进程原子扣减、关闭、耗尽原因（fail-closed）。
- 证据完整性、sha256、来源可信度、basis、时间戳。
- 自愈的断言保护、Oracle 绑定、晋升/回滚 fail-closed 与固定版本回归。
- GT 隔离与评测计分（评测令牌）。

---

## 7. BLOCKED 项

| 项 | 状态 | 原因 |
|---|---|---|
| 受约束 LLM 诊断臂 | **BLOCKED** | 应用侧 `TG_LLM_PROVIDER` 未启用；本轮未获显式外网计费授权，未发起任何 LLM 请求。代码路径已就绪（`--allow-llm`），不冒充规则结果为 LLM 结论。`.env` 中凭据形态存在性只做布尔检查，不读取、不记录。 |
| 人工诊断/修复基线 | **BLOCKED** | 无真实人类工程师参与，不产生人工基线数值。 |
| v4 新场景真实执行 | **BLOCKED_NOT_SEEDED** | 未植入故障，禁止修改源码/seed；只有场景定义与独立 GT。 |
| 统计显著性 | **BLOCKED** | n=19/轮，样本量不足；不做显著性检验、CI 外推或"提升 X%"宣称。 |

---

## 8. 证据索引

- 逐场景表/混淆矩阵/错误案例库：`docs/assets/run009/wp6/error-analysis-v3.json`
- EG/AP/多假设审查：`docs/assets/run009/wp6/eg-ap-audit.json`
- 多臂指标（含分子/分母/样本数）：`docs/assets/run009/benchmark-v4/benchmark-v4.json`
- 每次原始样本（含所有臂预测与探针回灌明细）：
  `docs/assets/run009/benchmark-v4/samples.jsonl`、`samples.repeat1.jsonl`
- 复现：`python scripts/bench_run009.py`（不修改任何既有源码或既有证据）
