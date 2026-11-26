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
