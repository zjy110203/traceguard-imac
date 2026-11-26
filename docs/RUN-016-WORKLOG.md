# RUN-016 工作日志

> 分支：`workbuddy/run016-finalization`｜起点：`73e4fe6`｜时区：CST (UTC+8)
> 记录顺序即实际执行顺序；失败与返工均保留，不做事后美化。

---

## 2026-11-26 17:20–17:40　WP0 基线核对

- 实查 git：分支 `workbuddy/run015-execution-consistency`、HEAD `73e4fe6`、origin 一致、工作区干净。
- **发现环境差异**：本机 PostgreSQL 实例不存在（无 psql/pg_ctl，55432 未监听），
  与 RUN-015 报告中的 "PostgreSQL 16.16 @ 55432" 不符。
- 处理：用官方 PG 16.16 二进制重建同拓扑实例（端口 55432、用户 `traceguard`、trust 本机认证），
  数据目录 `~/.traceguard-pg16/data`。
- 自检：`select version()` → 16.16；`test_postgres_platform.py` + `test_postgres_workspace_run012.py` 11 passed。
- 基线全量：828 passed / 16 skipped（未接 PG 时）。
- 新建分支 `workbuddy/run016-finalization`。
- 产出：`docs/RUN-016-BASELINE.md`（含 12 个已知问题的逐项复核表）。

## 17:40–18:00　WP1-A 长稳脚本口径修正

- 删除 `duration_min >= args.minutes * 0.95` 的 95% 容差；
- 新增真实墙钟起止时间（`started_at_iso` / `ended_at_iso` / `wall_clock_minutes`）；
- workerpackage / run_tag 参数化（不再硬编码 RUN-013）；
- `--min-minutes`（缺省 = `--minutes`）、`--min-runs`；
- 首轮冒烟（2 分钟）：`wall_clock=2.00 min / required=2`，运行链路正常，1 FAIL 只因 run 数不足（预期）。

## 17:45–18:10　WP1-C Artifact 治理

- 定位缺陷：`ArtifactStore.put()` 无 fence、非原子落盘、无回收机制。
- 实现：fence + run_id、原子写（tmp+rename）、失败回滚自己新建的文件、
  `verify_consistency` / `scan` / `collect_garbage`、CLI `artifact_gc_run016.py`。
- Pipeline 接入 fence。
- 新增 `tests/integration/test_artifact_lifecycle_run016.py` 11 项，全通过。

## 17:50–18:05　WP1-B Fencing 审查

- 逐条核对 claim / renew / mark_running / step / evidence / event / submit / FINALIZING / 终态冻结。
- 新增 AST 审计测试（3 项）与 PG 专项（4 项）。
- **踩坑**：PG 专项首版在 `fenced_session` 内再调用 `record_step(fence=...)`，
  同一 run 行自锁 → 进程挂死（表现为 shell 被 SIGTERM）。改为同事务调用 `_record_step_in()`。
- **语义修正**：测试原以为"新进程用旧 token 必须失败"，实际正确语义是
  "租约未过期时重启进程可继续写；被接管后旧 token 才失效"。已按真实语义重写测试。

## 18:10–18:15　长稳第一次启动与中止（重要教训）

- 为让 SIGKILL/SIGSTOP 场景必然触发，把探测逻辑改成"被别的 Worker 抢到就立即再发一个 Run"。
- 结果：Run 变并发，17:44–17:48 出现 54 个 FAILED。
- 根因（源码确认）：用例 `cleanup_policy=RESET_DATA`，每次 Run 前会 `POST /api/test/reset`；
  并发 Run 互相清空 TeamFlow 测试数据 → `business_state` 计数断言假失败。
- 处理：中止该轮（42m57s），恢复串行探测（保留 240s 窗口），
  证据保存为 `docs/assets/run016/wp1/soak-aborted-concurrent-probe.json`，18:15 重启。

## 18:15–18:35　WP2 多进程回归 + Promotion 恢复

- `repair_service` 新增 `promotion_recovery_plan()` / `promote_recover()`；
- 新增 API `GET /repairs/{id}/promotion-recovery`、`POST /repairs/{id}/promote-recover`；
- 修正 RBAC：`promote-recover` 原会落到 `repair.submit`，已映射到 `repair.promote`；
- 新增 `tests/integration/test_promotion_recovery_run016.py` 7 项，全通过。
- 多进程脚本首跑失败：`RegressionClaimRow` 无 `idempotency_key` 字段（应为 `claim_key`），修正后重跑。
- **结果：10 个 OS 进程（PID 89856–89865）+ PG16，13/13 判定 PASS**，返回同一 Run ID。

## 18:35–18:45　WP3 Benchmark 独立观测

- 新增 `benchmark/traceguard_benchmark/fault_effect.py`（4 类独立探针）；
- `classify_record` 引入 `benchmark-fault-v2` 语义与 `FALSE_PASS_UNVERIFIED`；
- `is_confirmed_failing_sample` 对 v2 只接受 `CONFIRMED`；
- 新增 `TargetPolicy.deny_path_prefixes` 阻断 `/api/ground-truth`；
- 单测 15 项通过；历史 benchmark 测试 41 项无回归。

## 18:45–18:55　WP4 权限 / SSE / UI E2E

- `/system/config` 非管理员脱敏；新增 10 项权限测试；
- SSE 重核验上限 10s → 5s，更新 RUN-015 断言；
- 前端 `vue-tsc` + `npm run build` 通过；
- UI 全链路首跑 55 PASS / 0 FAIL；追加证据图 / 探针 / 回归三段校验后重跑
  **58 PASS / 0 FAIL**，15 张截图。

## 18:55–19:20　WP5 与收尾

- 修复 CI 口径：`llm-smoke-status` 工件 + Release Gate 强制 `PASSED`；
- 全量回归（含真实 PG）**894 passed**；
- 长稳结束、执行产物巡检与最终报告（见后文实测章节）。
