# 变更（RUN-017，2026-11-26 21:55 CST）

- `benchmark/traceguard_benchmark/fault_effect.py`：重写（基线/分类/一致性/清理）。
- `benchmark/traceguard_benchmark/browser_effect.py`：新增浏览器级观测。
- `apps/teamflow/traceguard_teamflow/api.py`：状态接口返回 projects；新增 probe-cleanup。
- `apps/api/.../repair_service.py`：在途 verdict 修正；`regression_retry()` + 台账。
- `apps/api/.../routers/intelligence.py` + `auth.py`：`/regression-retry` 与 RBAC。
- `packages/execution/.../tasks/service.py`：`fenced_session` 重入检测。
- `packages/persistence/.../stores.py`：GC keyset 分页 + 定向复核；ArtifactStore 边界。
- `.github/workflows/*`：分支通配 + 新增 Nightly 专项 + 新增 Reliability Gate。
- `scripts/`：bench_v2、backup_restore、doctor、UI run017、GC bench；PG 端点参数化。

---

# 变更清单（RUN-016）

## 代码

| 文件 | 变更 |
|---|---|
| `packages/persistence/traceguard_persistence/stores.py` | `ArtifactStore.put` 增加 fence/run_id、原子写、失败回滚；新增 `verify_consistency` / `scan` / `collect_garbage`；引用收集覆盖 Evidence + Run 报告 |
| `packages/execution/traceguard_execution/runtime/pipeline.py` | artifact sink 传递 fence/run_id；`_build_observer` 接受 fence |
| `apps/api/traceguard_api/services/repair_service.py` | 新增 `promotion_recovery_plan` / `promote_recover` |
| `apps/api/traceguard_api/routers/intelligence.py` | 新增 GET `promotion-recovery` / POST `promote-recover` |
| `apps/api/traceguard_api/auth.py` | `promote-recover` 映射到 `repair.promote` |
| `apps/api/traceguard_api/routers/system.py` | `/system/config` 非管理员脱敏 |
| `apps/api/traceguard_api/main.py` | SSE 重核验上限 10s → 5s |
| `packages/core/traceguard_core/security/target_policy.py` | 新增 `deny_path_prefixes`，阻断 Ground Truth 路径 |
| `benchmark/traceguard_benchmark/fault_effect.py` | 新增：故障效果独立观测（4 类探针） |
| `scripts/bench_scenario_contract.py` | v2 样本语义、`FALSE_PASS_UNVERIFIED`、独立证据要求 |
| `scripts/bench_pilot_run011_r1.py` | 效果判定改为独立探针 |
| `scripts/soak_run013.py` | 删除 95% 容差、墙钟口径、参数化标记、240s 故障窗、串行重试、强杀后补位 |
| `scripts/business_loop_ui_acceptance_run012.py` | 新增 `--extended`：证据图/探针/回归三段校验 |
| `.github/workflows/nightly-gate.yml` / `release-gate.yml` | LLM 状态工件 + Release 强制 PASSED |

## 新增脚本 / 测试

- `scripts/artifact_gc_run016.py`、`scripts/regression_multiprocess_run016.py`、
  `scripts/business_loop_ui_acceptance_run016.py`
- `tests/integration/test_artifact_lifecycle_run016.py`（12）、`test_fencing_audit_run016.py`（4）、
  `test_promotion_recovery_run016.py`（7）、`test_system_config_rbac_run016.py`（10）
- `tests/unit/test_benchmark_fault_effect_run016.py`（9）、`test_benchmark_false_pass_v2_run016.py`（6）、
  `test_fencing_entrypoint_audit_run016.py`（3）

## RUN-019-R2（2026-11-27）

| 文件 | 变更 |
|---|---|
| `apps/web/src/components/StepEditor.vue` | 稳定 data-testid；断言 expected 可视化 JSON 编辑与校验；新步骤默认无 locator；选策略未填值→null |
| `apps/web/src/views/TestCasesView.vue` | 提交前校验（空 target/缺描述）；`data-testid` 创建入口 |
| `scripts/business_loop_ui_acceptance_run012.py` | TestCase 段改真实可视化操作（清空→逐步录入→JSON 往返→创建→服务端回读） |
| `apps/api/traceguard_api/routers/catalog.py` | 拒绝客户端声明来源；HTTP 固定 human + manual_http 血缘；版本继承来源 |
| `packages/contracts/traceguard_contracts/models.py` | `source_origin` / `source_ref` |
| `packages/persistence/traceguard_persistence/catalog.py` | `create_trusted_candidate()`；来源血缘；`authoring_hash` 写入与审核/绑定前复核；晋升/回滚血缘+摘要 |
| `apps/api/.../exploration_service.py` / `repair_service.py` | Compiler/自愈候选改走可信入口并带 exploration/proposal 血缘 |
| `apps/api/traceguard_api/seed.py` | 内置资产记录 `source_origin=seed` |
| `apps/web/src/views/*.vue` | 8 个视图补齐权限门禁（Intents/RunDetail/TestCaseDetail/Repairs/OracleDetail/Users/System/Benchmark/Explorer/DiagnosisDetail） |
| `apps/web/src/views/MonitoringView.vue` | 默认当前 Workspace；全局汇总显式勾选 + scope 展示 |
| `apps/web/src/views/{Runs,Diagnoses}View.vue` | 「包含 Benchmark」标注为跨全部已授权 Workspace 聚合 |
| `.github/workflows/nightly-gate.yml` | 全量测试接入看门狗（15min 栈/35min 强制失败） |
| `scripts/ci_pytest_watchdog.py` | 新增：CI 挂起可诊断 |
| `scripts/rbac_role_ui_acceptance_run019_r2.py` | 新增：四角色真实权限验收 |
| `scripts/stability_replay_acceptance_run013.py` | 改走可信候选入口（不再经 HTTP 声明来源） |
| `tests/integration/test_testcase_source_provenance_run019_r2.py` | 新增 8 项正反例 |
| `tests/integration/test_testcase_version_integrity_run019_r2.py` | 新增 5 项完整性 |
| `tests/integration/test_review_gate_http_security_run011_r1_2.py` | compiler/healed 候选改由服务层构造 |
| 文档 | RUN-019-FINAL-REPORT / R2-GAP-CLOSURE / WP1 / WP3 / WP5 / FEATURE-STATUS / README |
