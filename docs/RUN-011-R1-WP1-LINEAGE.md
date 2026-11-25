# RUN-011-R1 WP1：单一 TestCase 血缘闭环

> 状态：**VERIFIED**（两轮独立运行 + 数据库双向复核，2026-11-25）
> 产出证据：`docs/assets/run011-r1/lineage/`（loop1.json / loop1-db.json / loop2.json / loop2-db.json）
> 起点：`docs/RUN-011-R1-BASELINE.md`（WP0 审计结论）

## 0. 结论摘要

| 验收问题（指令 §1.5） | 结论 |
|---|---|
| 是否同一 TestCase ID | ✅ 两轮各自唯一主线（loop1 `tcase_1795576462905a6038d543b3a`；loop2 `tcase_1795576688704b1b1a7f08064`），全部 Run/Diagnosis/Repair 收敛于该用例 |
| Diagnosis 对应真实失败 Run | ✅ `diagnosis.run_id == failed_run_id`（API T3 + DB 复核） |
| Repair 对应该 Diagnosis | ✅ `repair.diagnosis_id + source_run_id + base_version` 全绑定（T9–T12 + DB 复核） |
| 回归和回滚对应同一版本线 | ✅ v1（批准）→ v2（晋升）→ v3（回滚恢复），T15–T18 + DB 回执复核 |
| 数据库与 API 双向复核 | ✅ 每轮 API 86/86 + DB 17/17 |
| 不允许仅凭 ID 可读判血缘 | ✅ T1–T18 全部是**关系断言**（相等/归属/端点存在），非可读性检查 |

**对比 RUN-011（`f604e5d`）的血缘断裂**：旧闭环用 3 个 TestCase 拼接
（编译用例 / `tcase_tf_false_pass` / `tcase_bench_loc001`），phase_trace 只查可读性、
`phaseD.evidence_refs` 恒为空、Repair changes 预先写死且无 Diagnosis 关联。
本轮全部修复（见 §2）。

## 1. 修复内容（代码变更清单）

### 1.1 Repair ↔ Diagnosis 契约（指令 §1.3，向后兼容扩展）

| 文件 | 变更 |
|---|---|
| `apps/api/traceguard_api/services/repair_service.py` | `propose()` 新增可选 `diagnosis_id` / `source_run_id` / `repair_source`，落库进 repair doc；`repair_source` 强制 `human:`/`rule:`/`llm:` 前缀（三类不得混淆）；`llm_suggest(propose=True)` 自动标注 `llm:<model>`；`_summary` 返回血缘字段 |
| `apps/api/traceguard_api/routers/intelligence.py` | `RepairProposeReq` 新增同名可选字段并透传 |
| `packages/contracts/.../models.py` | **未改**（RepairProposal 契约的必填字段不变；新字段以 doc 扩展承载，老调用方行为完全不变） |

**兼容性分析**：全部新参数可选、缺省 None/空串；不带这些字段的调用（含 663 项既有测试、
RUN-010/RUN-011 的既有调用路径）行为不变——102 项 repair/healing 相关测试先行验证通过。

### 1.2 证据引用提取（指令 §1.4 根因修复）

- 根因：脚本取 `h.get("evidence_ids")`，但 Diagnosis API 假设结构是
  `supporting[*].id` / `contradicting[*].id`（无 `evidence_ids` 字段）→ 恒为空；
  同时脚本没读 doc 级 `evidence_refs`。
- 修复：`_extract_evidence_refs()` 合并三个真实来源
  （`supporting[*].id` + `contradicting[*].id` + `doc.evidence_refs`）；
  两轮各提取 3 条引用且全部归属失败 Run（T5/T6）。
- `packages/evidence/traceguard_evidence/evidence_manager.py`：`to_dict()` 增加
  `run_id`（DB 列，向后兼容新增字段），使「证据 source_run_id 归属」可从 API 校验。

### 1.3 phase_trace 关系校验（指令 §1.4）

`scripts/full_loop_run011.py` 的 trace 阶段重写为 18 项**关系断言**（T1–T18）：
Run↔TestCase/版本、Diagnosis↔失败 Run、证据↔Run 归属与跨 Run 误引检查、
Graph 节点/边端点存在性、Repair↔Diagnosis/Run/base_version/来源标注、
沙箱候选副本、Promotion/Rollback 回执版本关系、回归精确版本、回滚后验证版本。

### 1.4 单一 TestCase 18 步链（指令 §1.2）

`scripts/full_loop_run011.py` 重构阶段编排（不再切换预置 TestCase）：

| §1.2 步骤 | 实现 | 关键检查 |
|---|---|---|
| 1–6 Intent→探索→编译→Oracle→回放→审批 | phase_ab（保留治理门禁，两轮编译候选均 STABLE_PASS 3/3，**未走人工回退**） | A1–A4、B1–B7 |
| 7 正式执行 | phase_c（同一 testcase@1，PASSED） | C1–C8 |
| 8–9 注入 TF-LOC-001 → 同用例再执行 → 真实失败 | phase_fail（**sticky 注入**，见 §1.5） | D0–D3（失败 Run 与主线同用例同版本） |
| 10 Diagnosis 读失败 Run | phase_diag | D4–D7（含证据引用真实提取） |
| 11 Probe 或真实 STOP | phase_probe | E1–E4：**两轮均真实执行 3 个探针**（network_trace / http_state / dom_snapshot，全部 SUCCEEDED，3 条新证据），停止原因 `STOP_ITERATION_LIMIT`——不再出现 RUN-011「STOP_CONFIRMED 零探针」的缺口 |
| 12 Repair Proposal | phase_repair：从失败 Run 的真实失败步骤推导修复，绑定 diagnosis_id/source_run_id | F1–F2 |
| 13–15 沙箱→审批→CAS 晋升 | 同 phase_repair | F3–F10（含 RBAC 403、幂等重放） |
| 16 固定版本回归 | phase_regress（精确 @v2） | G1–G3 |
| 17 回滚 | 同 phase_regress | G4–G5（内容级恢复） |
| 18 回滚后再次验证 | 同 phase_regress（恢复版本 + 晋升版本双复核） | G6–G7 |

### 1.5 真实缺陷发现与修复：外部故障注入被 RESET_DATA 吞掉

**现象**（首次运行 37/40 时暴露）：`tf.activate("TF-LOC-001")` 后 D0 确认故障活跃，
但故障 Run 仍 PASSED。

**根因**：编译器为用例设置 `cleanup_policy="RESET_DATA"`（`compiler.py:187`）；
Run 启动预清理调用 TeamFlow `POST /api/test/reset`，该端点执行 `FAULT_STATE.clear()`
**连故障一起清掉**（`api.py:333`）。RUN-011 的 `tcase_tf_false_pass` 能工作是因为
它把故障激活写成**用例内 api 步骤**（reset 之后再激活）——外部注入路径此前从未打通。

**修复**（TeamFlow 向后兼容扩展，`apps/teamflow/.../faults.py` + `api.py`）：
- `FaultState` 增加 **sticky** 集合：`activate(id, {"sticky": true})` 的故障不被
  `/api/test/reset` 清除（`sticky_kept` 如实返回）；`/api/faults/{id}/clear` 显式清除；
- 非 sticky 故障行为完全不变——既有测试 `test_reset_clears_data_and_faults` 等 9/9 通过，断言未动；
- 闭环脚本改用 sticky 注入 + 逐个显式清理（原 `clear_faults()` 只清 `TF-NORMAL-001` 的无效写法一并修正）。

该修复使「对同一个 TestCase 注入故障后再次执行」（§1.2 步骤 8–9）语义成立，
且不改用例、不换版本、不伪造任何字段。

### 1.6 修复来源三分（指令 §1.3）

两轮均如实记录：

| 来源类别 | 本轮实际 | 标注 |
|---|---|---|
| LLM 生成 | `llm_suggest` 调用返回 `called=false, reason=disabled`（`llm_repair_planner_enabled` 默认关闭，如实记录不冒充） | 若启用则标 `llm:<model>` |
| 规则生成（确定性夹具） | **实际采用**：从失败 Run 报告的失败步骤（s4 `test_id=create-submit`）运行时推导 `test_id → role:button:创建` | `repair_source: "rule:locator-fixture"` |
| 人工提供 | 未使用（编译候选两轮均通过稳定性治理，无需人工用例回退） | 若走人工路径标 `human:*` |

修复**不是** Agent 根据 Diagnosis 自动推导（脚本内夹具推导规则固定、可复核），
Diagnosis 提供机制方向（LOCATOR/TEST_ASSET），推导规则与来源均如实标注。

## 2. 两轮独立运行结果

| 项 | loop1（`r1-1`） | loop2（`r1-2`） |
|---|---|---|
| 数据库 | `traceguard_run011_r1_l1` | `traceguard_run011_r1_l2` |
| 主线 TestCase | `tcase_1795576462905a6038d543b3a@1` | `tcase_1795576688704b1b1a7f08064@1` |
| 来源 | Intent `intent_1795576451…` → 探索 `exploration_1795576451…` → 编译（rule_only） | Intent `intent_1795576678…` → 探索 `exploration_1795576679…` → 编译 |
| Oracle | `oracle_run011r1_compiled_cand_a_create_ui@1` | 同名（独立实例） |
| 正式 Run（PASSED） | `run_1795576467843ed00afc32d75` | `run_179557669385449c1fc43eae5` |
| 失败 Run（TF-LOC-001 sticky） | `run_17955764721925f11e3fce7b6`（NOT_EVALUATED + s4 失败步骤） | `run_1795576698218fce1edb2efb3`（同构） |
| Diagnosis | `diagnosis_1795576502919eff4037ad9cc`（PROBABLE；NETWORK+LOCATOR SUPPORTED） | `diagnosis_17955767252765843791e8340`（同构） |
| 证据引用（API 提取） | 3 条，全归属失败 Run | 3 条，全归属失败 Run |
| Probe | 3/3 SUCCEEDED（network_trace/http_state/dom_snapshot），STOP_ITERATION_LIMIT，3 条新证据 | 同构 |
| Repair | `repair_1795576503017aa0234f9836b`（`rule:locator-fixture`，step_index=4） | `repair_179557672537557cb4ee671b8`（同构） |
| 沙箱 | PASSED（`run_1795576503026dbb1c86cc713`，独立 Worker，候选副本） | PASSED（`run_1795576725386af57eb8029fa`） |
| 晋升 / 回滚 | v1→v2（CAS + 幂等重放同版本）→ v3（内容级恢复） | 同构 |
| 回归（@v2） | PASSED | PASSED |
| 回滚后复核 | 恢复版本 @v3 PASSED + 晋升版本 @v2 PASSED | 同构 |
| **API 检查** | **86/86** | **86/86** |
| **DB 复核** | **17/17** | **17/17** |

### 2.1 关系校验（T1–T18）覆盖点

T1/T2 Run↔主线用例与批准版本；T3/T4 Diagnosis↔失败 Run 与用例；
T5/T6 证据引用可读、归属失败 Run、无跨 Run 误引；
T7/T8 Graph Evidence 节点引用真实 Evidence、边端点全部落在节点集；
T9–T13 Repair↔用例/Diagnosis/失败 Run/base_version/来源标注；
T14 沙箱候选副本；T15 Promotion 回执；T16 回归精确版本；
T17/T18 Rollback 回执版本关系与回滚后验证版本。

### 2.2 DB 侧独立复核（`verify_db_run011.py --lineage-json`，只读 SQL）

tg_run 版本归属、tg_doc(diagnosis) run_id、tg_doc(repair) 四元绑定
（testcase/diagnosis/source_run/base_version）+ repair_source、
tg_promotion_receipt 的 PROMOTE/ROLLBACK 版本关系、诊断证据引用 ⊆ 失败 Run 证据集。

## 3. 边界与如实声明

1. **失败形态**：TF-LOC-001（定位器失效）失败 Run 的 verdict 是 `NOT_EVALUATED`
   （fail_fast 中断在 click 步骤，断言未评估）——这是执行层的真实失败状态，
   不是 FAILED 的替代说法；verdict 语义见 `verdict.py` 规则 4。
2. **Diagnosis 结论为 PROBABLE**（LOCATOR 为 SUPPORTED 非 CONFIRMED）：
   LOCATOR 的规则确认门槛要求 DOM 直接确认证据（`confirming=False`），本轮如实保留，
   未把 SUPPORTED 夸大为 CONFIRMED；Repair 与 Diagnosis 的关联是「基于该诊断方向的
   规则夹具修复」，不是「诊断已确认根因后的自动修复」。
3. **LLM 修复规划器未启用**（`called=false, reason=disabled`）：三来源区分已留痕；
   LLM 臂的真实调用属 WP4 Benchmark 范围（受 B-03 计费授权限制）。
4. **两轮编译候选均通过稳定性治理**，未走人工用例回退路径；回退逻辑保留
   （B5 检查位仍在），但本轮未触发，不作为已验证能力宣称。
5. 同一 Evidence 被 NETWORK 与 LOCATOR 两个假设共同引用（两轮各 3 条证据、2 个假设）——
   这是 WP3（假设去重与独立支持计数）的输入，本报告不将其计为 6 条独立支持。

## 4. 环境与复现

- PG16.16（Postgres.app 隔离实例 :55432）；API :8110（认证开启、TG_INLINE_WORKER=0、
  强制出站代理）；TeamFlow :8111；独立 Worker ×3（run011r1-w1/w2/w3，真实 Chromium）。
- 复现：`docs/RUN-011-WP9-OPERATIONS.md` 的部署流程 + 本文档 §1.5 的 sticky 注入语义。
- 命令：`TG_RUN011_PASSWORD=*** .venv/bin/python scripts/full_loop_run011.py --api ... --out docs/assets/run011-r1/lineage/loopN.json`
  与 `scripts/verify_db_run011.py --lineage-json ...`。
