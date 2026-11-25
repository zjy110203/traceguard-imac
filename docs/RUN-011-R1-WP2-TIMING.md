# RUN-011-R1 WP2：时间诊断三态与 TF-DB-004 延迟复测

> 状态：**VERIFIED**（2026-11-25）｜ 处理 OPEN-RISK-02（时序故障误 CONFIRMED/PERSISTENCE）
> 决策记录：`.workbuddy-run/decisions.md`（R1-01 ~ R1-05，语义变更先行记录、非静默修改）
> 证据：`docs/assets/run011-r1/timing/timing-retest.json`（真实场景 4/4）
> 回归测试：`tests/unit/test_timing_retest_run011_r1.py`（7 项）+ 既有套件更正 2 项

## 0. 结论摘要

| 验收问题 | 结论 |
|---|---|
| 时间未知是否还会被当作 ALIGNED？ | ✅ 不会。三态 ALIGNED / POST_HOC / **UNKNOWN**；`resolve_alignment()` 未知 → UNKNOWN |
| UNKNOWN 是否还能成为 CONFIRMED/EXCLUDED 权威依据？ | ✅ 不能。feedback 层降权（confirming=False、exclude=False、trust=TIME_UNKNOWN）+ Graph 裁决层门槛（`_time_ok` 拒绝 UNKNOWN 与 captured_at=0）双层拦截 |
| TIMING 是否不再被轻易误判为 PERSISTENCE？ | ✅ 真实延迟场景（TF-DB-004 滞后 9.51s）：即时 count=0 + 延迟后 count=1 → 生成 TIMING 方向假设，PERSISTENCE 停留在 SUPPORTED、root_causes=[] |
| 单次 count=0 是否还会直接确认永久性持久化故障？ | ✅ 不会（决策 R1-04）：探针 count=0 仅 SUPPORT 非确权；永久失败场景两次 count=0 后 PERSISTENCE 仍为候选 |
| 事后数据出现是否被伪造为 Run 当时已成功？ | ✅ 不会。延迟后证据标 POST_HOC，只附着反驳/生成 TIMING 假设，不改写 Run 时结论、不排除 Run 时假设 |

## 1. 语义修复（OPEN-RISK-02 根因）

### 1.1 修复前的缺陷（WP0 审计确认）

| 位置 | 缺陷 | 后果 |
|---|---|---|
| `probe/feedback.py::resolve_alignment()` | 时间未知（observed_at=0 / run_ended_at 缺失）时返回 **ALIGNED**（"保守处理"） | 时间未知被当成时间已对齐 → 可确权 |
| `graph/adjudication.py::_time_ok()` | captured_at=0（未知）视为匹配（"未知时间视为匹配，留痕不阻断"） | 未知时间证据可通过 CONFIRMED/EXCLUDED 权威门槛 |
| `probe/feedback.py::assess()` http_state | 单次 count=0 → `directly_confirming=True` | 「单查询直接确认永久性持久化故障」——TF-DB-004 延迟场景下把 TIMING 误判为 PERSISTENCE（OPEN-RISK-02 实测路径） |

### 1.2 三态语义（决策 R1-01/R1-02/R1-03）

```
ALIGNED  ：run_scope=True 或有证据证明落在 Run 时间窗口 → 可支持/反驳/排除
POST_HOC ：observed_at > run_ended_at（明确事后）      → 只附着；冲突时生成 TIMING 新假设
UNKNOWN  ：观测时间无法确定                            → 只附着；不确认、不排除、不确反驳权
```

- `resolve_alignment()`：未知 → `UNKNOWN`（notes 如实标注 unknown_timing）；
- `assess()/upd()`：POST_HOC 与 UNKNOWN 均降权（confirming=False、exclude=False），
  原因前缀分列 `[POST_HOC]` / `[TIME_UNKNOWN]`；TIMING 新假设仅 POST_HOC 生成
  （已知发生在 Run 后才叫"滞后"；UNKNOWN 时间关系本身未知，不伪造方向）；
- `merge_signals()`：引用 trust 标记——`TRUSTED` / `POST_HOC` / **`TIME_UNKNOWN`**
  （UNKNOWN 不持有 TRUSTED，引擎与裁决层均不作权威）；
- `_time_ok()`：`alignment ∈ {POST_HOC, UNKNOWN}` 或 `captured_at=0` → 不通过权威
  确认/反驳/独立支持来源门槛（留痕 notes；POST_HOC/超窗另记 time_mismatched）。

### 1.3 单查询降权（决策 R1-04，WP4 Pilot 后扩展至业务断言路径）

探针 `http_state count=0` 从 `directly_confirming=True` 改为**仅 SUPPORT（候选级）**。
**WP4 Pilot 实测扩展**：业务断言失败路径（`diagnosis_service` failed_biz 规则）背后
也是同一次状态查询——TF-DB-004 真实滞后场景中它把 PERSISTENCE 直接 CONFIRMED，
探针 STOP_CONFIRMED 零执行、TIMING 方向永远无法识别（OPEN-RISK-02 完整形态）。
故该路径的 state 证据同步降为 `confirming=False`（决策 R1-04 扩展，见 decisions.md）。

- TF-DB-001（False Pass）诊断从 CONFIRMED 变为 PROBABLE/SUPPORTED（候选）：
  指令 §4.1 预期矩阵第一条「数据始终不存在：允许保留 PERSISTENCE 候选」即此语义；
  RUN-011 的 D1「Trusted Oracle 揭穿假成功」指 **Run 真实失败**（verdict 级证据），
  不受影响；D5 一直接受 CONFIRMED/PROBABLE/SUPPORTED/INCONCLUSIVE 全部合法结论。
- 延迟复测后仍持续缺失 → PERSISTENCE 候选保留；数据延迟出现 → TIMING 方向
  （探针不再被过早 STOP_CONFIRMED 截断）。

**既有测试更正（非弱化）**：
- `test_probe_confirms_persistence_and_stops`（原断言单查询 → CONFIRMED + STOP_CONFIRMED）
  重写为 `test_single_probe_count_zero_does_not_confirm_persistence`（断言单查询 → SUPPORTED
  候选、root_causes=[]、证据仍可追溯）——期望值随语义决策同步更正，不是删断言；
- `test_feedback_detects_conflicting_evidence` 的探针信号补显式 `run_scope=True`
  （冲突检测需要 TRUSTED 反驳；三态后时间未知不再默认 ALIGNED，场景设置显式化）。

### 1.4 契约兼容性

`ProbeSignal / HypothesisUpdate / FeedbackResult / EvidenceMeta` 均为 `traceguard_evidence`
内部类型（非 `traceguard_contracts` 冻结契约）；`to_dict()` 字段形状不变，仅
`time_alignment`/`trust` 取值域扩展（ALIGNED/POST_HOC/UNKNOWN、TRUSTED/POST_HOC/TIME_UNKNOWN/UNTRUSTED）。
老数据（无 time_alignment 字段）由 `_time_ok()` 按 captured_at 判定，captured_at 也未知时
保守不确权（留痕）。全量测试 **670 passed / 0 failed / 0 skipped**（PG16），Ruff 全绿。

## 2. TF-DB-004 延迟复测（指令 §4.1 七步 × 四场景，真实执行）

脚本：`scripts/timing_retest_run011_r1.py`（真实 TeamFlow 故障注入 + 真实双查询 +
真实判定引擎 `merge_signals`/`DiagnosisEngine` 重评估）；证据：`docs/assets/run011-r1/timing/timing-retest.json`。

| 场景 | 故障 | 即时查询（Run 内，ALIGNED） | 延迟后查询（POST_HOC） | 重评估结论 | 判定 |
|---|---|---|---|---|---|
| S1 正常 | 无 | count=**1** @ t | — | PERSISTENCE **CONTRADICTED+排除**；无 TIMING；root_causes=[] | ✅ |
| S2 真实延迟 | TF-DB-004（查询滞后 9s） | count=**0** @ t | count=**1** @ t+9.51s | **TIMING 方向识别**（`rule:probe-timing` 新假设）；PERSISTENCE=SUPPORTED 候选、未确认、未排除；root_causes=[] | ✅ |
| S3 永久失败 | TF-DB-001（持久化回滚） | count=**0** @ t | count=**0** @ t+2s | PERSISTENCE **保留候选**（SUPPORTED，非确权证据×2）；无 TIMING；root_causes=[] | ✅ |
| S4 探针超时 | TF-NET-002（9s 强延迟）+ 客户端 1s 超时 | HTTP **TIMED_OUT**（真实超时） | — | **零证据、零假设变更**（PERSISTENCE 仍 CANDIDATE）；不冒充目标系统故障；outcome=INCONCLUSIVE | ✅ |

时间线证据（epoch，观测时刻如实保存）：
- S2：`observed_at_immediate=1795577797.43`、`observed_at_later=1795577806.94`（lag=9.51s）；
- S3：`observed_at_immediate=1795577806.95`、`observed_at_later=1795577808.96`；
- S2 的延迟后证据 `time_alignment=POST_HOC`——只做反驳附着与 TIMING 生成，
  **没有**把"事后数据已出现"改写成"Run 当时已成功"。

### 2.1 与预期矩阵对照（指令 §4.1）

| 指令预期 | 实测 |
|---|---|
| 数据始终不存在：允许保留 PERSISTENCE 候选 | S3：SUPPORTED 候选保留 ✓ |
| 数据延迟出现：应识别 TIMING 方向 | S2：TIMING 新假设（rule:probe-timing）✓ |
| 证据不足：INCONCLUSIVE | S4：INCONCLUSIVE（零证据）✓ |
| 不能因为单次 count=0 直接确认永久性持久化故障 | S2/S3：root_causes=[] ✓（+ 单元测试 `test_single_probe_count_zero_does_not_confirm_persistence`） |
| 不能因为事后数据出现就伪造 Run 当时已经成功 | S2：POST_HOC 反驳附着、PERSISTENCE 未被排除、Run 时结论不改写 ✓ |

## 3. 回归测试清单

`tests/unit/test_timing_retest_run011_r1.py`（新增 7 项）：
1. `test_resolve_alignment_three_states`（ALIGNED/POST_HOC/UNKNOWN 互斥）
2. `test_unknown_time_evidence_cannot_confirm_or_exclude`（UNKNOWN 双层拦截）
3. `test_adjudicator_unknown_captured_at_not_authoritative`（captured_at=0 不确权）
4. `test_retest_normal_data_visible_excludes_persistence`（S1）
5. `test_retest_real_delay_identifies_timing_not_persistence`（S2）
6. `test_retest_permanent_failure_keeps_persistence_candidate`（S3）
7. `test_retest_probe_timeout_produces_no_evidence`（S4）

集成层：`tests/integration/test_active_diagnosis_feedback.py`
（`test_single_probe_count_zero_does_not_confirm_persistence` + 冲突检测场景显式对齐）。

全量：**670 passed / 0 failed / 0 skipped**（PG16.16）；Ruff 全绿。

## 4. 边界与如实声明

1. UNKNOWN 对 CONTRADICTED 同口径保守处理（`TIME_UNKNOWN` 不持有 TRUSTED）：
   指令只明文禁止 CONFIRMED/EXCLUDED，本实现把权威反驳一并挡在时间门槛外——
   保守方向（宁可 INCONCLUSIVE 不可误判），决策记录 R1-02 注明。
2. 业务断言级 PERSISTENCE 路径（failed_biz 规则）在 WP4 Pilot 实测后**已同步降为
   候选级**（决策 R1-04 扩展）：TF-DB-001 的 Run 级失败事实（verdict/断言证据）保留，
   但单次状态查询不再直接确认「永久性持久化故障」——确认门槛改为延迟复测后的
   多点一致证据。
3. TF-DB-003（2s 短滞后）与 TF-DB-005（写入慢 4s）未单独跑场景：S2 用 TF-DB-004
   （9s 强滞后，指令点名场景）覆盖"查询滞后"类；机制同一（滞后快照 vs 真实值）。
4. 本报告的"真实延迟"以 TeamFlow 故障注入模拟生产时序问题（受控测试环境），
   不外推到任意生产数据库的最终一致性结论。
