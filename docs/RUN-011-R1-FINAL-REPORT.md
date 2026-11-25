# RUN-011-R1 最终报告：单一 TestCase 血缘闭环与诊断可信度修复

> 执行日期：2026-11-25（CST）｜ 指令：`指令13.txt`（RUN-011-R1）
> 分支：`workbuddy/run011-full-loop`（HEAD 基线 `f8e1d62`，不改 PR #1/#2、不动 main、不改写历史）
> 范围：修复 RUN-011 的业务血缘断裂、诊断可信度、实验测量问题——
> **把核心闭环从多模块集成演示提升为可追溯、可复核、可重复的单一业务链**。

## 0. 最终回答指令 §十一 的九个问题

**Q1：是否真正形成同一 TestCase 的完整血缘？**
**是。** 两轮独立运行各自唯一主线 TestCase（loop1 `tcase_17955764629…@1`、
loop2 `tcase_17955766887…@1`，均来自 Intent→真实探索→编译治理，3/3 稳定性回放通过、
未走人工回退）。Intent → Exploration → TestCase → Oracle → 正式 Run → 失败 Run
（同一用例同一版本，sticky TF-LOC-001 注入）→ Diagnosis → Probe → Repair → 沙箱 →
晋升 v2 → 回归 → 回滚 v3 → 回滚后复核，**18 步全链同用例**。API 侧 86/86 ×2、
DB 侧 17/17 ×2（`docs/assets/run011-r1/lineage/`）。RUN-011 的三用例拼接问题
（编译用例 / `tcase_tf_false_pass` / `tcase_bench_loc001`）已消除。

**Q2：Repair 是否真正关联 Diagnosis？**
**是。** Repair 契约向后兼容扩展 `diagnosis_id + source_run_id + repair_source`；
两轮 Repair 均绑定真实失败 Run 的诊断（`T9–T13` 关系校验 + DB 回执复核）。
修复来源三分如实标注：实际采用 **`rule:locator-fixture`**（从失败 Run 的真实失败
步骤推导，确定性夹具）；LLM 规划器如实记录 `called=false, reason=disabled`；
未冒充"Agent 自动推导"。

**Q3：探针是否在正确时间范围内产生证据？**
**是。** 时间语义三态化（ALIGNED/POST_HOC/UNKNOWN）落地；WP2 四场景真实复测：
Run 内即时查询标 ALIGNED、延迟后查询标 POST_HOC（lag 9.51s 实测），
`observed_at/collected_at/ingested_at` 全链留痕。Probe Evidence 的 run_id 与
diagnosis 归属经 T5/T6 关系校验（无跨 Run 误引）。

**Q4：TIMING 是否不再被轻易误判为 PERSISTENCE？**
**是。** 根因修复：①`resolve_alignment` 未知时间不再按 ALIGNED；②`_time_ok`
未知时间不作权威；③**单次 count=0 不再直接确认永久性持久化故障**（探针路径 +
业务断言路径双降权，决策 R1-04）。实测 TF-DB-004 延迟场景：即时 0 / 延迟后 1 →
生成 TIMING 方向假设、PERSISTENCE 停留候选、root_causes=[]（修复前该场景
CONFIRMED/PERSISTENCE 且探针 STOP_CONFIRMED 零执行）。

**Q5：LLM 候选是否稳定去重？**
**是。** `diagnosis/dedup.py`：机制分组 + 确定性相似度合并（阈值 0.72）+ 顺序无关
归并 + 证据并集去重 + 来源全保留 + LLM 候选不 CONFIRMED 双层拦截。
五个指令回归场景 7 项测试全过（含「两次独立运行候选顺序不同 → 合并结果一致」）。

**Q6：Benchmark v5 是否具备可信测量条件？**
**是。** 测量口径重建：探针 54/54 **真实执行**（全 ID 绑定：run_id/diagnosis_id/
probe_id/execution_id/evidence_id/observed_at/collected_at/ingested_at），
不再从旧 Evidence 重建信号。三场景 Pilot 15/15 + 23 场景 × 2 轮（37 失败样本）：
F 臂（探针+时间对齐）主因准确率 **0.7027** vs C 规则臂 0.6486；Macro-F1 0.5973；
Selective Accuracy 0.875；False Pass Rate 0；探针执行/回灌成功率均 1.0。
n=37 小样本，**不宣称统计显著**。

**Q7：Egress 误拦是否解决？**
**是。** 根因：`set_egress_policy` 只更新 controller 引用、`EgressProxy` 实例
（dataclass 按值持有）保留 start() 时旧策略——共享 Worker 上先跑 Run 的策略
滞留导致授权目标 HTTPS 变体偶发 `PORT_NOT_ALLOWED`（RUN-011 7 次中 2 次）。
修复：`set_policy()` 双引用同步 + 启动安全预热（fail-closed）+ `policy_origins`
审计快照。复验 6/6（冷启动对照、stale-policy 回归、未授权零成功、跨 Worker 对照），
**未扩大端口白名单、未放宽任何网络限制**。

**Q8：PG 并行测试是否稳定？**
**基本稳定，保留一项限制。** 并行失败已复现（3 进程并发 → 每进程 7-8 errors）并
定位两个根因（共享 `--basetemp` 被会话清理误删的错误级联；TeamFlow 共享库 +
固定端口 8011 的用例互扰）——**与 PG 连接池无关**（如实更正 RUN-011 的归因）。
修复后：PG16 串行 680/680；3 进程并行两轮 = 一轮 3/3 全绿 + 一轮 2/3 全绿
（1 次 SQLite 并发 DDL 锁超时 flake）。限制如实保留，不宣称 100% 稳定。

**Q9：哪些能力仍然 BLOCKED 或 NOT RUN？**
- **BLOCKED**：B-17 Worker 四真实故障场景（安全批准申请已备
  `docs/RUN-011-R1-WP7-B17-SECURITY-REQUEST.md`，零次触碰被拦截操作）；
  宿主级网络隔离（无 pf 权限）；Benchmark LLM 臂的统计级使用（无计费授权；
  本轮 LLM 仅作记录性对照）；人工基线（无真人）；CI 触发（无 gh/凭据）；增量 PR 创建。
- **NOT RUN**：UI 全链路点击验收、多 API 实例/DB 断连/长事务、生产部署验收、
  pytest-xdist 形态并行；多 Worker 独立部署回归已由 WP1 两轮闭环覆盖。

## 1. 交付清单

### 1.1 报告（docs/）

| 报告 | 状态 |
|---|---|
| `RUN-011-R1-BASELINE.md`（WP0 源码与证据审计） | VERIFIED |
| `RUN-011-R1-WP1-LINEAGE.md`（单 TestCase 血缘） | VERIFIED |
| `RUN-011-R1-WP2-TIMING.md`（时间三态 + TF-DB-004 复测） | VERIFIED |
| `RUN-011-R1-WP3-HYPOTHESIS.md`（假设去重） | VERIFIED |
| `RUN-011-R1-WP4-BENCHMARK-PILOT.md`（三场景 + v5 两轮） | VERIFIED |
| `RUN-011-R1-WP5-EGRESS.md`（OPEN-RISK-01） | VERIFIED |
| `RUN-011-R1-WP6-PG-STABILITY.md`（OPEN-RISK-04） | VERIFIED |
| `RUN-011-R1-WP7-B17-SECURITY-REQUEST.md`（安全批准申请） | **BLOCKED**（保持） |
| `RUN-011-R1-FINAL-REPORT.md`（本文件） | — |

### 1.2 证据（docs/assets/run011-r1/）

`lineage/`（loop1/2.json + loop1/2-db.json：API 86/86 + DB 17/17 ×2）、
`timing/`（timing-retest.json：四场景 4/4）、
`benchmark/`（pilot-3scenarios.json：15/15；v5-2rounds.json：37 样本统计）、
`egress/`（egress-verify.json：6/6）。

### 1.3 源码变更（全部向后兼容，不弱化断言）

| 文件 | 变更 |
|---|---|
| `apps/api/.../services/repair_service.py` + `routers/intelligence.py` | Repair +diagnosis_id/source_run_id/repair_source（可选字段） |
| `packages/evidence/.../evidence_manager.py` | 证据 to_dict +run_id（审计可对账） |
| `packages/evidence/.../probe/feedback.py` | 三态时间对齐（UNKNOWN）+ 单查询 count=0 降权 |
| `packages/evidence/.../graph/adjudication.py` | `_time_ok` 拒绝 UNKNOWN/captured_at=0 |
| `packages/evidence/.../diagnosis/dedup.py`（新增） | 假设去重合并 |
| `apps/api/.../services/diagnosis_service.py` | 接线 dedup + failed_biz 单查询降权（R1-04 扩展） |
| `packages/execution/.../browser/egress.py` + `manager.py` | set_policy 双引用同步 + 安全预热 + policy_origins 审计 |
| `apps/teamflow/.../faults.py` + `api.py` | sticky 故障（RESET_DATA 不吞外部注入） |
| `scripts/full_loop_run011.py` | 重构为单 TestCase 18 步链 + T1–T18 关系校验 |
| `scripts/verify_db_run011.py` | +血缘双向复核（--lineage-json） |
| `scripts/timing_retest_run011_r1.py` / `bench_pilot_run011_r1.py` / `bench_v5_run011_r1.py` / `egress_verify_run011_r1.py`（新增） | WP2/4/5 实验脚本 |
| `tests/conftest.py` + `tests/e2e/conftest.py` + `tests/e2e/test_execution_e2e.py` | 并行隔离修复（basetemp/TeamFlow 库/动态端口） |

### 1.4 新增回归测试（23 项）

`test_timing_retest_run011_r1.py`（7）、`test_hypothesis_dedup_run011_r1.py`（7）、
`test_egress_policy_sync_run011_r1.py`（3）、WP2 语义更正（2 项重写 + 1 场景显式化）、
WP1 链路检查并入闭环脚本（86 项检查含 18 项关系校验）。

**全量测试：680 passed / 0 failed / 0 skipped**（PG16.16，串行终态）；Ruff 全绿。

## 2. RUN-011 报告修订（指令 §十.7 项）

对 `docs/RUN-011-FINAL-REPORT.md` 与相关 WP 报告的更正（追加不覆盖）：

1. **执行时间统计不一致** → 以各 WP 报告时间窗为准（01:10–02:25 CST 自洽）；
   R1 各 WP 时间独立标注，不与 RUN-011 混计。
2. **RUN-010 Benchmark v4 口径错误** → 已在 `RUN-010-WP5-BENCHMARK.md` 追加
   **ERRATA**（E1 数字口径、E2 语义口径：0.9286 是重建信号数非执行数、
   E3 受影响表述清单）；原始实验记录未覆盖。
3. **多 TestCase 集成演示 vs 单 TestCase 血缘闭环分开描述** → RUN-011 的 62/62
   闭环为**三用例拼接的集成演示**（本报告 Q1 与 WP0 §2 有 ID 级证据）；
   R1 WP1 为**单用例完整血缘闭环**，两者在文档中分列、不混引。
4. **规则/LLM/人工修复区分** → `repair_source` 强制 `human:/rule:/llm:` 前缀；
   R1 实际为 `rule:locator-fixture`；LLM 未启用如实记录；RUN-011 的
   "agent:repair-planner" 预写死修复在 WP0 §3.3 追认为规则夹具。
5. **POST_HOC 证据不描述为 Run 内证据** → 三态语义 + `time_alignment` 逐条留痕；
   WP2 报告明确 POST_HOC 只附着/生成 TIMING，不改写 Run 时结论。
6. **Benchmark NOT RUN 不描述为已验证** → RUN-011 的 "Benchmark NOT RUN" 保持；
   R1 WP4 的 v5 两轮为**新实测**，含独立数字与样本声明。
7. **B-17 BLOCKED 不描述为通过** → 全文一致标注 BLOCKED；本轮零次触碰被拦操作，
   仅有待批准的申请材料（WP7）。

## 3. 环境与复现

- PG16.16（Postgres.app 隔离实例 :55432）、API :8110（认证开、内嵌 Worker 关、
  强制出站代理）、TeamFlow :8111、独立 Worker ×3、真实 Chromium。
- 复现：WP9 部署流程 + 各 WP 报告命令行；闭环 `scripts/full_loop_run011.py`
  （`--out docs/assets/run011-r1/lineage/loopN.json`）+ `verify_db_run011.py --lineage-json`。
- 全量测试：`TG_TEST_POSTGRES_URL=… pytest tests/ -q` → 680 passed。

## 4. 结论

RUN-011 的核心闭环已从「多模块集成演示」升级为「单一业务链」：
同一 TestCase 的 18 步血缘可追溯（API+DB 双向复核）、Repair 与 Diagnosis 显式关联、
探针证据时间语义可信、TIMING/PERSISTENCE 不再混淆、LLM 候选稳定去重、
Benchmark 测量口径可信、Egress 误拦根因消除、PG 并行基本稳定（限制如实保留）。
未完成项（B-17、宿主级隔离、生产验收等）全部如实标注，不冒充通过。
