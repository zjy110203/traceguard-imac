# 测试结果（RUN-018，2026-11-27 01:00 CST）

| 命令 | 结果 |
|---|---|
| `pytest tests/unit/` | **419 passed** |
| `pytest tests/unit/test_probe_cleanup_security_run018.py` | 19 passed |
| `pytest tests/unit/test_double_submit_run018.py` | 9 passed |
| `pytest tests/integration/test_fault_effect_teamflow_run017.py` | 14 passed |
| PR Gate #81 @ c807284 | success (2m28s) |
| Nightly #33 @ c807284 | failed (ui-acceptance) |

---

# 测试结果（RUN-018 执行中，2026-11-27 00:40 CST）

（待填）

---

# 测试结果（RUN-017 收口，2026-11-26 22:40 CST）

| 命令 | 结果 |
|---|---|
| `pytest -q` | **932 passed / 0 failed / 16 skipped**（跳过=PG 专项） |
| PG16 专项（真实 16.16） | **28 passed** |
| `pytest tests/unit/test_benchmark_fault_effect_run016.py` | 17 passed |
| `pytest tests/integration/test_fault_effect_teamflow_run017.py` | 14 passed |
| `pytest tests/integration/test_browser_effect_run017.py` | 8 passed |
| `pytest tests/integration/test_fenced_reentry_run017.py` | 6 passed |
| `pytest tests/integration/test_regression_retry_run017.py` | 15 passed |
| `pytest tests/integration/test_artifact_lifecycle_run016.py` | 14 passed |
| `scripts/soak_run013.py --minutes 62` | 62.05 min / 119 Run / 33 PASS / 0 NOTE |
| `scripts/bench_v2_run017.py`（2 次） | 120 样本 / UNKNOWN 4 / FP 0 / 确认率 84.3% |
| `scripts/artifact_gc_bench_run017.py` | 10k/50k/100k 全一致，0.058/0.198/0.383 s |
| `scripts/backup_restore_verify_run017.py` | SQLite + PG16 all_passed=true |
| `scripts/doctor.py` | PASS=9 WARN=5 FAIL=0 |
| `scripts/business_loop_ui_acceptance_run017.py` | 58 PASS / 0 FAIL |
| `scripts/ui_matrix_run017.py` | 128 / 128 PASS |
| Ruff / format / vue-tsc / vite / npm audit / secret scan / git diff --check | 全绿 |

---

# 测试结果（RUN-017，2026-11-26 21:55 CST）

| 命令 | 结果 |
|---|---|
| `pytest -q`（本地，无 TG_TEST_POSTGRES_URL） | **932 passed / 0 failed / 16 skipped** |
| `pytest tests/unit/test_benchmark_fault_effect_run016.py` | 17 passed |
| `pytest tests/integration/test_fault_effect_teamflow_run017.py` | 14 passed |
| `pytest tests/integration/test_browser_effect_run017.py` | 8 passed |
| `pytest tests/integration/test_fenced_reentry_run017.py` | 6 passed（PG16） |
| `pytest tests/integration/test_regression_retry_run017.py` | 15 passed |
| `pytest tests/integration/test_artifact_lifecycle_run016.py` | 14 passed |
| `scripts/backup_restore_verify_run017.py`（SQLite + PG16） | all_passed=true（12/12 项） |
| `scripts/doctor.py` | PASS=9 WARN=5 FAIL=0 |
| Ruff check / format（packages apps tests benchmark scripts） | 全绿 |
| `npm run build`（vue-tsc + vite） | 通过 |

---

# 测试结果（RUN-016，最终）

| 项 | 命令 | 结果 |
|---|---|---|
| 全量 pytest（PG16） | `TG_TEST_POSTGRES_URL=... pytest -q` | **895 passed / 0 failed / 0 skipped**（142.71s） |
| PG 专项 | `pytest tests/integration/test_postgres_*.py` | 11 passed |
| Fencing（PG） | `pytest tests/integration/test_fencing_audit_run016.py` | 4 passed |
| Fencing 静态审计 | `pytest tests/unit/test_fencing_entrypoint_audit_run016.py` | 3 passed |
| Artifact 生命周期 | `pytest tests/integration/test_artifact_lifecycle_run016.py` | 12 passed |
| Promotion 恢复 | `pytest tests/integration/test_promotion_recovery_run016.py` | 7 passed |
| 系统配置权限 | `pytest tests/integration/test_system_config_rbac_run016.py` | 10 passed |
| SSE 撤权 | `pytest tests/integration/test_sse_recheck_limit_run015.py` | 17 passed |
| Benchmark 新口径 | `pytest tests/unit/test_benchmark_*_run016.py` | 15 passed |
| Regression 多进程 | `python scripts/regression_multiprocess_run016.py --procs 10` | 13/13 checks PASS |
| 长稳（PG16） | `python scripts/soak_run013.py --minutes 62 --min-minutes 60` | 31 PASS / 0 FAIL / 1 NOTE |
| UI 全链路 | `python scripts/business_loop_ui_acceptance_run016.py` | 58 PASS / 0 FAIL |
| Ruff | `ruff check packages apps scripts tests benchmark` | All checks passed |
| 前端类型/构建 | `npx vue-tsc --noEmit` / `npm run build` | 通过 |

## 已知 flaky（已定位并加固）

`test_ten_concurrent_regression_requests_create_one_run`：SQLite 写锁竞争 + 20s 等待超时
→ 改为 60s 等待（断言未放宽），连续全量通过。

## RUN-019-R2（2026-11-27，最终代码）

| 项 | 命令 | 结果 |
|---|---|---|
| Ruff | `ruff check apps packages scripts tests` | All checks passed |
| Ruff format | `ruff format --check apps/api packages scripts tests` | 280 files already formatted |
| 后端全量 | `pytest -q` | **1144 passed / 16 skipped**（419s；skip 均为 PG 专项） |
| 前端类型 | `npm run typecheck` | 通过 |
| 前端构建 | `npm run build` | 通过 |
| 来源可信专项 | `pytest tests/integration/test_testcase_source_provenance_run019_r2.py` | 8 passed |
| 版本完整性专项 | `pytest tests/integration/test_testcase_version_integrity_run019_r2.py` | 5 passed |
| 审核门禁回归 | `pytest tests/integration/test_review_gate_http_security_run011_r1_2.py` | 通过 |
| UI 全链路 | `business_loop_ui_acceptance_run017.py --profile core` | 42 项 / 41 PASS / 0 FAIL / 1 NOTE |
| 四角色权限 | `rbac_role_ui_acceptance_run019_r2.py` | 24/24 PASS |
| a11y（R2 复跑） | `ui_a11y_run019.py --out docs/assets/run019-r2/local/a11y` | 77/77 |
| CI PR Gate | #113 @ `19f527f` | backend 3m36s + frontend 26s，SUCCESS |
| CI Nightly | #66 @ `19f527f` | 6/6 Job SUCCESS（ui-acceptance 2m01s；LLM BLOCKED） |
| Reliability Gate | `workflow_dispatch` | NOT_RUN（无 gh/凭据）；本地 62min soak 33/33 保留 |

## RUN-020-PREPARATION（2026-11-27，HEAD `564b21c`）

| 项 | 命令 | 结果 |
|---|---|---|
| 后端全量 | `pytest -q` | **1144 passed / 16 skipped / 0 failed**（382.40s） |
| Ruff | `ruff check apps packages scripts tests` | All checks passed |
| Ruff format | `ruff format --check apps/api packages scripts tests` | 281 files already formatted |
| 真实 LLM（本地） | `scripts/llm_smoke.py` | **11/11 PASS**（Flash 700ms / Pro 4789ms / 5 calls / $0.000639） |
| CI PR Gate | #115 @ `564b21c` | SUCCESS 3m40s |
| CI Nightly | #68 @ `564b21c` | SUCCESS 6/6（backend-full 9m33s、ui-acceptance 2m5s、postgres 1m3s、security 20s、llm-smoke 23s、gate-verdict 4s） |
| CI llm-smoke | #68 | Job SUCCESS，真实调用 **BLOCKED**（无仓库 Secret） |
| Reliability Gate | — | **NOT_RUN**（无 Actions 权限） |
| 证据核对 | UI 42 项/41 PASS；RBAC 24；a11y 77/77 | 与声明一致（a11y 内部标签为 RUN-018，NOTE） |
