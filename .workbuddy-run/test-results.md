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
