# RUN-011-R1 WP0：源码与证据审计基线

> 状态：**VERIFIED**（本轮审计完成，2026-11-25）
> 范围：`scripts/full_loop_run011.py` 各阶段实际使用的实体 ID、RUN-011 报告体系、
> 闭环原始 JSON 证据、Diagnosis/Repair 契约现状。
> 不推翻 RUN-011 已通过的能力；仅确认 R1 修复的起点事实。

## 1. 执行基线

| 项 | 值 |
|---|---|
| 仓库 | `zjy110203/traceguard-imac` |
| 分支 | `workbuddy/run011-full-loop` |
| 远程 HEAD | `f8e1d62e580c52d89761ad67a3d395f6345532f1`（本地一致，无未推送提交） |
| 代码实现提交 | `f604e5dc81f3a65638e2c5bd7988f66c6e36ea79` |
| RUN-010 基线 | `b470518cb13b9428925a739badb3b62b30a97a9b` |
| main | 未动（`ac2876e`），PR #1 / PR #2 保持原状 |
| 既有测试 | 663 项收集（`pytest --collect-only`）；RUN-011 报告：PG16 全量 663 passed / 0 skipped |
| RUN-011-R1 起点 | 本文件首次出现 `RUN-011-R1` 标识（此前仓库无任何 R1 文件） |

## 2. `scripts/full_loop_run011.py` 各阶段实体审计

以 loop1（`docs/assets/run011/full-loop/loop1.json`）为样本，loop2 结构相同（ID 不同）：

| 阶段 | TestCase | Run | Diagnosis | Repair | 版本 |
|---|---|---|---|---|---|
| A/B（Intent→探索→编译→治理） | `tcase_17955439984472ae9b57bab85`（编译生成） | — | — | — | v1，`cand_a_create_ui@1` |
| C（正式执行） | **同一编译用例** | `run_1795544003583a58bb2f8b846`（PASSED） | — | — | @1 |
| D（故障+诊断） | **`tcase_tf_false_pass`（预置种子用例）** | `run_179554400793228c130169a7b`（FAILED） | `diagnosis_17955440238638dac12dcef3d`（CONFIRMED/PERSISTENCE） | — | — |
| E（探针） | （挂在 D 的 Run） | 同上 | 同上 | — | `STOP_CONFIRMED`，0 步，0 新证据 |
| F/G（修复→晋升→回归→回滚） | **`tcase_bench_loc001`（预置种子用例）** | 基线 `run_…`（NOT_EVALUATED）/ 回归 / 回滚后复核 | — | `repair_17955440367266686639fd04f` | v1→v2（晋升）→v3（回滚） |
| trace | 仅逐 ID 可读性 | — | — | — | — |

**结论：主闭环实际跨越 3 个互不相同的 TestCase**，Diagnosis 与 Repair 分属不同用例、
不同 Run，不存在"同一 TestCase 的完整血缘"。指令 §二 已知问题全部属实（见 §3）。

## 3. 已知问题逐项确认

| # | 指令描述 | 代码/证据位置 | 确认 |
|---|---|---|---|
| 1 | phase_de 使用 `tcase_tf_false_pass` | `full_loop_run011.py` L702 `ex.execute("tcase_tf_false_pass", …)` | ✅ 属实 |
| 2 | phase_fg 使用 `tcase_bench_loc001` | L829 `base_case = "tcase_bench_loc001"` | ✅ 属实 |
| 3 | phase_trace 仅检查可读性，不验证关系 | L1026–1081：只有 `readable[...] = True/False`，无任何 `run.testcase_id == …` 类断言 | ✅ 属实 |
| 4 | phase_fg 的 Repair changes 预先写死 | L852–859：`test_id=create-submit → role=button:创建` 硬编码在脚本里，`created_by="agent:repair-planner"` | ✅ 属实（可作确定性夹具，但不可称为 Agent 推导） |
| 5 | Phase E 因 CONFIRMED 正确 STOP，未真实执行探针 | loop1/loop2 均 `stop_reason=STOP_CONFIRMED, steps=[], new_evidence=[]`；来源 `probe/planner.py` L739 `if confirmed: return None, "STOP_CONFIRMED"` | ✅ 属实（STOP 语义本身诚实，但闭环缺真实探针执行段） |

### 3.1 附带发现（指令 §1.4 点名的证据引用问题，根因已定位）

- **API 侧**：`diagnosis_service.py` L375 生成的 `doc["evidence_refs"]` 取自
  `h.to_dict()["supporting"][*]["id"]`，字段名是 `supporting`（含 `id/trust/confirming`），
  **不是** `evidence_ids`。
- **脚本侧**：`full_loop_run011.py` L722 提取
  `h.get("evidence_ids") or []`——该字段在假设 dict 中不存在 → 永远为空。
  这就是 `phaseD.evidence_refs == []` 而 Graph 有 28 个 Evidence 节点的直接原因。
- **修复方向**：脚本改读 `diag.get("evidence_refs")` 并并入
  `h.supporting[*].id + h.contradicting[*].id`；不得仅以 Graph 节点数量证明关联完整。

### 3.2 附带发现：Repair ↔ Diagnosis 契约缺口（指令 §1.3）

`apps/api/.../services/repair_service.py` `propose()` 现接受
`testcase_id / base_version / changes / reason / created_by / workspace_id`，
**不接受、不持久化** `diagnosis_id`、`source_run_id`。
`RepairProposeReq`（intelligence.py L353）同样无这两个字段；
`llm_suggest` 虽有 `diagnosis_id/run_id`，但仅用于抽取 symptom 文本。
`packages/contracts/.../models.py` `RepairProposal` 契约也无 `diagnosis_id/source_run_id`。

→ **最小变更方案（待 WP1 实施）**：`propose()` 与 `RepairProposeReq` 增加可选字段
`diagnosis_id`、`source_run_id`（缺省 None，向后兼容），落库进 repair doc 并在
`GET /repairs/{id}` 返回；不改动既有必填字段与状态机。属于**向后兼容的扩展**，
不破坏既有契约（老调用方行为不变）。

### 3.3 附带发现：修复来源标注

RUN-011 的定位器修复（`test_id=create-submit → role=button:创建`）由脚本预先写死
（人工/规则夹具），`created_by="agent:repair-planner"` 名不副实。R1 必须按指令 §1.3
明确区分 **人工提供 / 规则生成 / LLM 生成** 三种来源，不得混淆。

## 4. 闭环原始证据复核（loop1 / loop2）

| 复核点 | loop1 | loop2 | 判定 |
|---|---|---|---|
| summary | 62/62 passed | 62/62 passed | 与报告一致 |
| 主用例（A/B/C） | `tcase_17955439984472ae9b57bab85` | `tcase_17955441308212d34c9420fcf` | 两轮各自独立生成 |
| D 失败 Run 用例 | `tcase_tf_false_pass`（不同用例） | 同左 | 血缘断裂 |
| F/G 修复用例 | `tcase_bench_loc001`（不同用例） | 同左 | 血缘断裂 |
| phaseD.evidence_refs | `[]`（提取 bug，见 §3.1） | `[]` | 需修 |
| Graph Evidence 节点 | 28（referenced=28） | 同构 | 仅数量，非关系证明 |
| Phase E | STOP_CONFIRMED / 0 步 | 同左 | 无真实探针执行 |
| trace.readable | 8/8 true | 8/8 true | 只证可读，未证关系 |
| promotion receipt（脚本内） | `receipt: null`（提取不全） | 同左 | DB 侧实际有回执（loop1-db 2 行） |
| loop-db 复核 | 8/8（lease/孤儿/Event/审计/多版本/回执） | 8/8 | 与报告一致；但回执绑定的是 `tcase_bench_loc001` |

## 5. RUN-011 报告体系状态快照（供 R1 各 WP 起点）

- **FINAL-REPORT**：两轮 62/62 + DB 8/8；探针 3/3（WP2 单独实验）；Repair 2/2；
  663 passed / 0 skipped；时间窗 01:10–02:25 CST。
- **BLOCKED（保持）**：B-17 四故障场景、宿主级网络隔离、Benchmark LLM 臂、人工基线、CI、增量 PR。
- **NOT RUN（保持）**：Benchmark v5、UI 全链路点击、多 API/DB 断连/长事务、生产部署验收。
- **OPEN-RISK（R1 目标）**：
  - OPEN-RISK-01：授权目标 https 偶发 `PORT_NOT_ALLOWED`（WP5 处理）；
  - OPEN-RISK-02：时序故障误 `CONFIRMED/PERSISTENCE`（GT=TIMING，WP2 处理）；
  - OPEN-RISK-03：LLM 候选未去重、结论漂移（WP3 处理）；
  - OPEN-RISK-04：PG 并行测试偶发资源竞争 60 errors（WP6 处理）。
- **WP3（诊断 v3）自评 PARTIAL**：5 类历史问题 2 修 3 未修（时序复测未实现、LLM 漂移未修、Flaky 单 Run）。
- **WP4（Benchmark）**：NOT RUN（v4 测量口径无效：`probes_per_sample=0.9286` 是重建信号数非执行数）。
- **时间统计**：各 WP 报告时间窗 01:10–02:25 互相自洽；UTC/CST 换算一致
  （`ingested_at=2026-11-24T18:06Z ≈ 11-25 02:06 CST`）。WP1 §3 与 §4 的 Intent ID
  不同属实（§3 为另一实例），R1 报告中需分开描述，不得混引。

## 6. R1 修复优先级（由本审计导出）

1. **WP1 血缘闭环（最高优先级）**：统一 18 步链到同一 TestCase；修 phase_trace 关系校验；
   修 evidence_refs 提取；Repair 关联 Diagnosis（§3.2 最小变更）；修复来源三分标注。
2. **WP2 时间三态**：`resolve_alignment()` 未知时间不返回 ALIGNED；`_time_ok()` 未知时间
   不作权威依据；TF-DB-004 延迟复测四场景。
3. **WP3 假设去重**：机制分类 + 语义相似度合并 + 同一 Evidence 不计多份独立支持。
4. **WP4/5/6/7/8**：按指令顺序执行。

## 7. 约束（持续生效）

- 不重新搭建项目；不推翻 RUN-011 已通过能力；不修改 PR #1/#2；不改写历史；
- 不伪造关联字段；契约变更走最小扩展 + 兼容性说明；
- 不放宽安全/端口白名单；B-17 未批准保持 BLOCKED；
- 失败如实记录，不弱化断言换通过。
