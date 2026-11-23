# RUN-007-FINALIZATION-2 源码审查补充轮报告

- 仓库：`zjy110203/traceguard-imac`
- 分支：`workbuddy/run007-resume-20261123`
- 基线 commit：`fdbe0f5f38981a5f8d94397847641d2e1de0e6a8`（已先 `git fetch` 同步，无他人新增提交）
- PR：#1（→ `main@ac2876e`）
- 本轮提交：见 §8（每个工作包单独提交，保留原始历史；未强推、未改写历史）
- 环境：macOS / Python 3.13 / SQLite（隔离库 `/tmp/traceguard-f2/api.db`）/ 真实 Chromium
- 服务：TeamFlow `:8021`、TraceGuard API `:8020`（`TG_INLINE_WORKER=0`）+ 独立 Worker 进程

> 本轮的核心要求是"不要把实现完成当成验收通过"。因此每一项结论都对应可复核的运行证据，
> 未执行/受阻的项在 §9 明确列出，不用推断值填充。

---

## 1. 结论摘要（先看这里）

| 工作包 | 结论 | 关键证据 |
|---|---|---|
| WP1-A Seed 幂等 | **已修复**：连续 Seed 版本不再增长；Repair 后 Seed 以新版本+审核恢复基线 | `tests/integration/test_seed_idempotency.py`（2 项）；两次 Seed 版本数一致 |
| WP1-A Repair 基线/定位器 | **已修复**：动态读取最高 APPROVED（本轮实测 base=3，非硬编码 1）；LOC-002 → step 3 | `benchmark-v3-f2.json` 的 `repair_records` |
| WP1-A Promotion fail-closed | **已实现**：7 项检查全通过才回归；否则只记录 | 同上 `promotion.checks` |
| WP1-A 固定版本回归 | **已验证**：`?version=promoted_version`，`run.testcase_version_id` 严格等于预期 | `regression.version_binding_ok=true` |
| WP1-A 回滚内容级验证 | **已验证**：指针/步骤/断言/Oracle 绑定全部恢复 | `rollback.checks` 全 true |
| WP1-B 回执身份绑定 | **已修复**：跨 Proposal/跨 kind/跨 workspace 复用键显式冲突 | `test_repair_receipt_identity.py`（7 项） |
| WP2 Evidence 摘要语义 | **已修复**：来源/持久化内容/附件三种摘要分离（迁移 v9） | `test_evidence_storage_integrity.py`（6 项） |
| WP2 服务端验证 | **已实现**：读库内内容重算 + 附件字节重算 + 派生来源；旧请求单列 | `test_api_flow.py::test_evidence_verify_is_server_side...` |
| WP2 E2E 逐条验收 | **已实现**：A 15/15、B 28/28 PASS，附件 36/36 字节一致，NOT_RUN 0 | `e2e-run007-f2.json` |
| WP3 Macro-F1 | **已修复**：浮点分子（各类别 F1 之和）+ 类别数 + 各类别明细 | `test_macro_f1_numerator_is_real_f1_sum_not_fake_int` |
| WP3 口径分离 | **已统一**：execution_status 与 test_verdict 分开；4 个中断场景标 NOT_EVALUATED | `dataset_statistics()`；两轮 mismatch=0 |
| WP3 E 臂禁止回退 | **已实现**：失败如实记 NOT_RUN/INCONCLUSIVE；单独统计探针成功率 | 探针执行 19/19，探针后有效诊断 15/19 |
| WP3 重复实验 | **已完成**：2 轮独立实验（各 38 次执行），原始样本分文件保存 | `samples-f2.jsonl` / `samples-f2.repeat1.jsonl` |
| WP4 安全审查 | 无真实密钥入库、`.env` 未跟踪；npm audit high=0、moderate=3（dev-only） | §7.1 |
| WP5 PostgreSQL | 本机 **BLOCKED**（无 psql/docker）；CI 的 `postgres:16` job 为唯一真实覆盖 | §7.2、§9 |
| WP6 前端关键交互 | 75/75 检查通过（15 视图 × 4 断点 + 明暗主题），0 console 错误 | `docs/assets/run007/ui-f2/ui-review.json` |
| WP7 最终回归 | **546 passed / 11 skipped**；ruff check + format 全绿 | §7.4 |
| WP8 CI / PR | PR Gate 由 push 自动触发（结果见 §7.5）；Nightly/Release 需手动 dispatch，本轮**未执行** | §9 |

---

## 2. WP1-A：Seed 版本漂移与 Repair Benchmark

### 2.1 缺陷

`apps/api/traceguard_api/seed.py::_seed_bench_v2()` 用 `utc_now_iso()[:19]` 作为测试数据后缀，
写进用例步骤（`Bench 正常2-2026-11-23T22:06:01` 之类）。每次 Seed 步骤内容都变 →
变更检测必然判"语义变化" → 新建一个版本并审核。后果：

1. 用例版本随时间无限增长（不可变历史被垃圾版本淹没）；
2. Benchmark 里"最高 APPROVED 版本"不再等于"原始故障基线"，隐藏了 `base_version=1` 的硬编码问题；
3. 任何"用最新正式版本做基线"的修复实验都在错误版本上进行。

### 2.2 修复

- 引入固定常量 `BENCH_STATIC_DATA_TAG = "基准"`；动态数据（时间戳/随机串）只在运行期生成，
  **不固化进用例版本**；
- 幂等判定同时要求：`最新版本 == 最高 APPROVED 版本` **且** 语义指纹一致
  （步骤 `action/value/target` + 断言完整字段，含 `oracle_id`）；
- 不满足时走**新版本 + 正常审核流程**恢复基线（Repair 改过定位器后再次 Seed 即此路径），
  绝不就地修改已存在的不可变版本。

### 2.3 验证（`tests/integration/test_seed_idempotency.py`）

| 用例 | 断言 |
|---|---|
| 第一次 Seed | 30 个 Benchmark 用例各建立 1 个 APPROVED 版本 |
| 第二次 Seed | 每个用例版本数仍为 1（此前会变 2、3…） |
| 已存在版本 | 不重复创建、不产生 PENDING 影子版本 |
| Repair 后再次 Seed | 生成 v3，内容等于原始基线，v2/v1 内容逐字节未变 |
| 多次 Seed | 最新正式版本仍为 v3，不再新增 |

### 2.4 Repair 实验（真实运行，非单元测试）

本轮 `scripts/bench_run007.py` 的自愈闭环实测（`benchmark-v3-f2.json` → `repair_records`）：

| 场景 | 基线版本 | 晋升 | 回归（固定版本） | 回滚 | 结果 |
|---|---|---|---|---|---|
| TF-LOC-001 | **v3**（动态读取，非 1） | v4，7/7 检查通过 | `tcase_bench_loc001@4` → PASSED | v5，内容/断言/Oracle 全部恢复 | 闭环 |
| TF-LOC-002 | **v3** | v4，7/7 检查通过 | `tcase_bench_loc002@4` → PASSED | v5，全部恢复 | 闭环 |

- 基线为 v3 是"上一轮运行留下的正式版本"，恰好证明**没有硬编码 base_version=1**；
- LOC-002 的定位器修正：真实读取 `/projects/new` 的 DOM 后确认
  `<input id="name" data-testid="project-name-v2" name="name" required>`，
  `<label for="name">项目名称</label>` → 可访问名称 `项目名称`；
  `get_by_test_id("project-name")` 计数 **0**（原定位器确实失效），
  `get_by_role("textbox", name="项目名称")` 计数 **1**，
  `get_by_role("button", name="创建")` 计数 **1**（所以不能用按钮角色定位输入框）。
- 步骤索引：`tcase_bench_loc002` 的第 0 步是故障注入 `api` 步骤，`fill project-name` 在
  **index 3**（旧值 2 指向 `click nav-new`，改上去会破坏业务步骤）。

自愈三指标（`repair_metrics`）：断言保持 **2/2**、接受率 **2/2**、回归通过 **2/2**
（上一轮为 0/1，本轮为真实闭环；样本量 2，不作外推）。

---

## 3. WP1-B：幂等回执身份绑定

### 3.1 缺陷

`CatalogService._receipt_replay()` 只要幂等键命中就 `return row.result`，`RepairService._match_receipt()`
同样只看 `kind`。因此：

1. Proposal B 用 Proposal A 的 `key-A` 晋升 → 直接拿到 A 的 `promoted_version` 并被标 PROMOTED；
2. PROMOTE 回执可被 ROLLBACK 复用（只校验 kind 时也可能命中错误分支）；
3. 历史 PROMOTED 提案"补回执"只在内存里构造字典，没有真正落库，下次重试仍拿不到回执。

### 3.2 修复

- 回执严格绑定 6 元组：`idempotency_key / kind / proposal_id / testcase_id / workspace_id / base_version`
  （`target_version` 随回执记录）；任一不一致 → `CONFLICT: IDEMPOTENCY_KEY_REUSED`；
- 直接 Catalog 调用与 RepairService 两条路径都做同一套校验，底层不再"服务层修了、Catalog 还错"；
- `record_receipt()` 真正写库；历史 PROMOTED 补回执后可被下一次重试命中；
- 服务层 fail-closed：Catalog 返回的版本号不是有效正整数 → `PROMOTION_RESULT_INVALID`，不宣告 PROMOTED。

### 3.3 故障测试（`tests/integration/test_repair_receipt_identity.py`，7 项）

| # | 覆盖点 | 结果 |
|---|---|---|
| 1 | A 用 key-A 晋升成功 | PASS |
| 2-4 | B 复用 key-A → CONFLICT；B 不是 PROMOTED；不继承 A 的 promoted_version | PASS |
| 5 | B 不改动正式版本指针（仍为 v2，版本集合 {1,2}） | PASS |
| 6 | PROMOTE 与 ROLLBACK 不共享回执 | PASS |
| 7 | 不同幂等键重试同一 Proposal 仍幂等（不新增版本） | PASS |
| 8 | 历史 PROMOTED 补回执**持久化**，二次重试命中 | PASS |
| 9 | 崩溃恢复回归：`test_repair_crash_recovery.py` 全绿（19 项 Repair 测试） | PASS |
| 附 | Catalog 返回无效版本号 → PROMOTION_FAILED | PASS |

---

## 4. WP2：Evidence 完整性

### 4.1 摘要语义（迁移 v9）

| 字段 | 覆盖对象 | 说明 |
|---|---|---|
| `sha256` | 脱敏**前**原始来源 payload | 历史字段名，保持兼容 |
| `stored_content_sha256` | 脱敏**后**实际持久化内容 | 服务端完整性只认它 |
| `attachment_sha256` | ArtifactStore 落盘字节 | 附件篡改检测 |

以前只有 `sha256`（来源摘要）而库里存脱敏内容 → 服务端用库内内容重算永远对不上，
只能让调用者重新提交原始内容，这恰好是"调用者说没事就没事"。

### 4.2 服务端验证（`POST /evidence/{id}/verify`）

1. 读 Evidence 持久化记录；
2. 读真实存储内容，重算 `stored_content_sha256` 并比对；
3. 附件调用 `ArtifactStore.read()`（其内部已校验字节摘要）并二次比对记录摘要；
4. 派生证据：`raw_ref` 存在、是 raw、同工作区、`derivation_rule` 已记录；
5. Workspace 归属（调用方声明时校验）；
6. 结果只写 PASS / FAIL / NOT_RUN；历史行无摘要 → NOT_RUN，不冒充 PASS。

旧请求兼容：`content` 只写入 `legacy_source_check`，与存储完整性**分开报告**。

### 4.3 测试

| 测试 | 覆盖 |
|---|---|
| `test_digests_are_semantically_separated` | 来源摘要 ≠ 持久化摘要；脱敏后内容入库 |
| `test_storage_verify_passes_and_survives_restart` | 关闭并重开数据库后仍 PASS |
| `test_storage_verify_detects_content_tampering` | 改库内内容 → FAIL/`STORED_CONTENT_MISMATCH` |
| `test_storage_verify_detects_attachment_tampering` | 改磁盘字节 → FAIL/`ATTACHMENT_TAMPERED` |
| `test_derived_evidence_source_integrity` | 派生来源删除 → FAIL |
| `test_legacy_rows_are_not_run_not_pass` | 历史行 → NOT_RUN |
| `test_evidence_verify_is_server_side_and_keeps_legacy_separate` | 接口层：服务端验证 / 旧请求分离 / workspace 不符 FAIL |

### 4.4 E2E 逐条验收（`docs/assets/run007/e2e-run007-f2.json`）

| 场景 | Evidence 总数 | PASS | FAIL | NOT_RUN | 附件字节一致 | 派生证据 |
|---|---|---|---|---|---|---|
| A（正常业务） | 15 | 15 | 0 | 0 | 12/12 | 0（本场景无派生） |
| B（假通过揭穿） | 28 | 28 | 0 | 0 | 24/24 | 0（本场景无派生） |

- 每条 Evidence 单独调用验证接口，逐条记录 `reasons`，不是"统计历史 `integrity_check` 字段"；
- `NOT_RUN` 不计 PASS；本轮为 0；
- 来源摘要可复核性如实输出：本轮 43 条证据全部脱敏，因此"不提供原始内容即可复核来源摘要"的数量为 0，
  以持久化内容摘要为准（这正是本轮要区分的地方）；
- 派生证据来源完整性：E2E 本次未产生 derived 证据（数量 0），该维度由集成测试覆盖，不虚报通过率。

E2E 总计 **56 步 / 0 失败**（含场景 D 新增的 D17b 回滚内容级复核）。

---

## 5. WP3：Benchmark 评测纠偏

### 5.1 Macro-F1（D1）

旧实现：`numerator = int(value * len(f1s))` —— 用平均值反推分子，必然截断且不是真实统计量。
新实现：`numerator = Σ F1_c`（浮点，可逐类复核），`denominator = C`，
并输出 `per_class_f1[{tp,fp,fn,support,precision,recall,f1}]`、`class_count`、`formula`。

单测 `test_macro_f1_numerator_is_real_f1_sum_not_fake_int`：GT {X,X,Y} / 预测 {X,Y,Y} →
两类 F1 均为 2/3，分子 **1.3333**（旧实现会得到 1），分母 2，macro-F1 0.6667。

### 5.2 执行状态与测试 Verdict 分离（D2）

`Scenario` 新增四个字段：`expected_execution_status` / `expected_test_verdict` /
`diagnosis_eligible` / `expected_failure_mechanism`；TIME-003 的 `EXEC_FAILED`
特殊值并入统一口径。

数据集口径（`dataset_statistics()`）：

| 维度 | 分布 |
|---|---|
| expected_execution_status | COMPLETED 26 / **FAILED 4** |
| expected_test_verdict | PASSED 11 / FAILED 15 / **NOT_EVALUATED 4** |
| diagnosis_eligible | 19（PASSED 预期的 11 个不计分） |

4 个"步骤执行中断"场景：LOC-001、LOC-002、COMP-003、TIME-003 —— 执行终态 FAILED、
正式 Verdict NOT_EVALUATED，**没有**为了对齐 GT 改写成 FAILED。

两轮实验中 `verdict_mismatch = 0`（Flaky 概率场景除外），说明数据集口径与产品状态机已对齐。

### 5.3 E 臂禁止静默回退（D3）

旧代码两处静默回退：诊断异常时 `diag_after_probe = diag_rule`，图异常时
`graph_after_probe = graph` —— 探针没跑成功也能拿 C/D 的成绩。

现在：记录 `probe_error` / `diag_after_probe_error` / `graph_after_probe_error`，
无有效结果标记 `NOT_RUN` / `INCONCLUSIVE`，E 臂记 UNKNOWN 而非复制 C；
单独统计 `probe_execution_success_rate`、`probe_post_diagnosis_valid_rate`、`probe_not_run_rate`。

本轮实测：探针执行成功 **19/19**（50 个探针，2.63/样本），探针后有效诊断 **15/19**，
`probe_not_run_rate = 0`。4 个无有效诊断的样本全部是"执行中断"场景，诊断接口按产品策略返回
`412 NOT_EVALUATED：只能补证据，不能下结论` —— 这是产品规则，不是回退被掩盖。

### 5.4 重复实验（D4）

- `--repeats 2`：两轮**独立**执行 + 独立诊断 + 独立样本文件；
- 原始文件不覆盖：`_unique_path()` 对同名文件追加时间戳；本轮为
  `samples-f2.jsonl`（第 1 轮）与 `samples-f2.repeat1.jsonl`（第 2 轮）；
- 逐条记录：dataset_version / scenario / run_id / testcase_version_id / diagnosis_id /
  probe_plan_id / evidence_refs / execution_status / test_verdict / elapsed_s；
  非诊断样本显式置 null（不是漏记）；
- 失败样本全部保留，不删除、不平均。

### 5.5 结果（两轮独立实验，每轮 30 场景 / 38 次执行）

| 指标 | A 传统基线 | C 规则 | D 证据图 | E 主动探针 |
|---|---|---|---|---|
| Primary Cause Accuracy | **3/19 = 15.8%** | 9/19 = 47.4% | 9/19 = 47.4% | 9/19 = 47.4% |
| Diagnosis Coverage | 19/19 = 100% | 12/19 = 63.2% | 12/19 = 63.2% | 12/19 = 63.2% |
| Selective Accuracy | 3/19 = 15.8% | 9/12 = 75.0% | 9/12 = 75.0% | 9/12 = 75.0% |
| False Pass（A 臂自判） | **4/4 = 100%** | 0/4 | 0/4 | 0/4 |
| Macro-F1（分子/类别数） | 0.5455/8 = **0.0682** | 2.3175/8 = **0.2897** | 2.3175/8 = 0.2897 | 2.3175/8 = 0.2897 |
| 探针/样本 | — | — | — | 50/19 = **2.63** |

执行层假通过（与任何诊断臂无关）：**0/4 = 0%** —— 产品对 4 个持久化故障全部判 FAILED，
"A 臂 4/4 假通过"是"只看 UI 断言"这一基线的属性，二者已分开报告。

**可诊断子集**（剔除 4 个被产品策略拒绝诊断的执行中断场景）：C/D/E **9/15 = 60%**，
覆盖 12/15 = 80%，selective 9/12 = 75%。两轮结果完全一致，说明差异不是随机波动。

### 5.6 解读（诚实结论）

1. 规则诊断仍显著优于"只看 UI"的基线（47.4% vs 15.8%），假通过从 4/4 降到 0/4；
2. **D 与 C 完全相同、E 与 C 完全相同**：证据图与主动探针在本数据集上没有带来主因增益。
   探针确实执行了（50 个，全部成功），但探针证据没有改变规则引擎的结论；
   这是一个负结果，不用它粉饰"主动诊断有效"；
3. 与上一轮（C 10/19）相比本轮 9/19，差异来自 Flaky 场景的随机性与 4 个执行中断样本的口径统一，
   不是能力退化；两轮独立实验内部完全一致；
4. 覆盖率上限被产品策略压住：4 个 NOT_EVALUATED 样本不参与诊断（412），
   平均覆盖 63.2%，这是**设计约束**，已在 §5.3 单独披露。

---

## 6. 本轮修复清单（按文件）

| 文件 | 变更 |
|---|---|
| `apps/api/traceguard_api/seed.py` | 静态测试数据、语义指纹、恢复基线 |
| `packages/persistence/.../catalog.py` | 回执身份校验、`record_receipt`、CAS 传递身份 |
| `apps/api/.../services/repair_service.py` | 回执身份前置校验、历史回执落库、晋升 fail-closed |
| `packages/evidence/.../evidence_manager.py` | 三种摘要、`verify_storage_integrity`、来源比对与完整性解耦 |
| `packages/persistence/.../stores.py` | 持久化版同上 + 附件/派生校验 + 写回结果 |
| `packages/persistence/.../models.py` `db.py` | 新列与迁移 v9 |
| `apps/api/.../routers/evidence.py` | 服务端验证接口、旧请求分离 |
| `packages/execution/.../pipeline.py` | 附件摘要入库 |
| `scripts/bench_run007.py` | 动态基线、fail-closed、固定版本回归、回滚内容验证、E 臂禁回退、重复实验 |
| `scripts/e2e_run007.py` | 逐条证据验证、回滚内容级复核 |
| `benchmark/.../evaluation.py` `scenarios.py` | Macro-F1、口径四字段 |

---

## 7. WP4-WP8

### 7.1 安全审查

- `git ls-files` 无 `.env`；`git grep` 扫描 `sk-* / ghp_* / github_pat_* / AKIA* / 私钥块`：0 命中；
- 本轮新增代码不写原始敏感内容：只存脱敏后内容 + 摘要，原始 payload 不落库；
- `npm audit`：high = 0，moderate = 3（`vue-tsc → @vue/language-core → minimatch` 的 DoS，
  dev-only 链路；修复需 `vue-tsc` 大版本破坏性升级，本轮不做，记录为已知风险）；
- 无身份认证仍是既有架构限制（只有主体类型门禁），本轮未改变，继续披露。

### 7.2 PostgreSQL

- 本机无 `psql` / `pg_ctl` / `docker` → **无法本地做持续性/并发验证**；
- 唯一真实覆盖是 CI Nightly 的 `postgres:16` service container（`test_postgres_platform.py` 等 6 项）；
- 本轮对 schema 的改动（迁移 v9）使用通用 DDL（`ALTER TABLE ADD COLUMN` + 列存在性检查），
  PG 语法兼容，但**未在 PG 上实测**。这条必须作为限制继承到下一轮。

### 7.3 前端关键交互

`scripts/ui_review_run007.py`（真实 Chromium）：15 视图 × 4 断点 + 明暗主题 + 四态：
**75/75 检查通过，0 横向溢出、0 console 错误、0 page error，79 张截图**，
产出 `docs/assets/run007/ui-f2/`。本轮未改前端代码，属回归验证。

### 7.4 最终回归

| 项 | 结果 |
|---|---|
| `pytest` 全量 | **546 passed / 11 skipped**（跳过全部为未配置 PG 的 parity/platform 用例） |
| `ruff check` | 全绿 |
| `ruff format --check` | 全绿 |
| 前端 `typecheck` | 通过 |
| 前端 `build` | 通过（132.83 kB 主包） |
| E2E（A/B/C/D，独立 Worker） | 56 步 / 0 失败 |
| Benchmark 两轮 | 76 次执行 / 661.2s |

### 7.5 CI / PR

- push 到 `workbuddy/run007-resume-20261123` 会自动触发 **PR Gate**（`pr-gate.yml` 的 push 分支规则）；
- Nightly Gate 只有 `schedule`（北京时间 02:00）与 `workflow_dispatch`；
  Release Gate 只有 tag 与 dispatch。**本轮未手动 dispatch**，
  因此不能声称"三级门禁在本轮 commit 上全绿"——上一轮的 `fdbe0f5` 三级结论不可移植到新 commit。
- PR #1 会随 push 自动更新提交列表；PR 描述/评论未自动追加（无 gh CLI，未使用临时 token）。

---

## 8. 提交记录

| Commit | 内容 |
|---|---|
| `51e99cc` | Seed 幂等（静态数据、语义指纹、恢复基线）+ 测试 |
| `9095801` | 回执身份严格绑定 + 历史回执落库 + fail-closed + 测试 |
| `87d9728` | Evidence 摘要语义分离 + 服务端验证 + E2E 逐条验收 + 测试 |
| `3b99625` | Repair 实验闭环可信化 + 评测口径（Macro-F1 / 口径分离 / E 臂 / 重复实验） |
| `30c306e` | LOC/COMP 三场景口径补齐 + 样本 null 字段 + fail-closed 负向用例 |
| `259d9c6` | 文档同步（Evidence 语义 / API / 迁移 v9） |
| 本轮收尾 | 报告、证据归档、Benchmark/UI 原始数据 |

---

## 9. 未完成 / 受限（如实列出，不美化）

1. **PostgreSQL 本地持续运行与并发验证 BLOCKED**：本机无 PG 运行时，只能依赖 CI `postgres:16`；
   迁移 v9 未在 PG 上实测。
2. **三级 CI 未在本轮 commit 上真实跑通**：PR Gate 随 push 自动触发（结果待查）；
   Nightly / Release 需手动 dispatch 且需要带 Actions 权限的凭据，本轮未执行。
   上一轮 `cc3339d`/`fdbe0f5` 的绿灯**不能**作为本轮验收证据。
3. **诊断覆盖率被产品策略限制**：4 个 `NOT_EVALUATED` 场景诊断接口返回 412，
   这是产品规则（执行中断不下结论），不是本轮可改的缺陷；覆盖率上限 15/19。
4. **D/E 臂无增益**：结论是负结果，不做有利解释；探针/图的价值需要多源冲突数据集才可能体现。
5. **派生证据在 E2E 中未产生**：该维度由集成测试覆盖，E2E 报告 0 条不代表通过。
6. **Flaky 场景**仍是 5 次重复后取首个匹配样本，统计置信区间宽；不做强外推。
7. **Evidence 历史行**（本轮之前入库）无法回溯来源与持久化摘要一致性 → 如实 NOT_RUN，
   未做"看起来通过"的回填。

---

## 10. 证据索引

| 证据 | 路径 |
|---|---|
| Benchmark 汇总（两轮） | `docs/assets/run007/benchmark/benchmark-v3-f2.json` |
| Benchmark 原始样本（第 1 轮） | `docs/assets/run007/benchmark/samples-f2.jsonl` |
| Benchmark 原始样本（第 2 轮） | `docs/assets/run007/benchmark/samples-f2.repeat1.jsonl` |
| E2E A/B/C/D | `docs/assets/run007/e2e-run007-f2.json` |
| 前端 15 视图审查 | `docs/assets/run007/ui-f2/ui-review.json`（79 张截图同目录） |
| Seed 幂等测试 | `tests/integration/test_seed_idempotency.py` |
| 回执身份测试 | `tests/integration/test_repair_receipt_identity.py` |
| Evidence 完整性测试 | `tests/integration/test_evidence_storage_integrity.py` |
| Macro-F1 / 口径单测 | `tests/unit/test_benchmark.py` |
