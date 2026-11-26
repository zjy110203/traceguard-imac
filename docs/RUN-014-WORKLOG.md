# RUN-014 工作日志（WORKLOG）

> 分支：`workbuddy/run014-product-closure`（基线 `85f18c1`）
> 规则：每个 WP 独立提交 + 测试 + 证据；不把 NOT_RUN/BLOCKED/PARTIAL 写成 PASS。

| WP | 提交 | 主题 | 关键结果 |
|---|---|---|---|
| WP0 | （含于 WP1 之前的核查，无独立代码） | 基线核查 | HEAD 一致、工作区 clean、`main` 未动；`docs/RUN-014-BASELINE.md` |
| WP1 | `55d1e5a` | Repair → Promotion → Regression 闭环 | 独立 `regression_status`；真实 Regression Run；前端生命周期修正；13 passed（12 条验收） |
| WP2 | `8cba090` | Lease / Fencing 原子性 | 过期即失效；Step/Evidence/Event 同事务守卫；7 条并发故障测试；全量 791 |
| WP3 | `02c4b0a` | SSE 撤权 + 全局 RBAC + 统计口径 | 活跃连接每 2s 复核；`llm/summary`/`browser` 收归 `system.observe`；13 passed；全量 804 |
| WP4 | `52617d1` | 单资产全血缘 UI 验收 | 40/40；真实 Chromium + 2 独立 Worker；发现并修复 `step_index=None` 500 |
| WP5 | `a1b3fe8` | Benchmark v5 + AI 质量 | 23×2 真跑（46 执行/38 样本）；AI n=32（规则 0.9688 / LLM 0.875） |
| WP6 | 见最终报告 | 发布门禁 | Ruff / build / 全量 / PG16 / PG fencing / 长稳 / CI |
| WP7 | 见最终报告 | 文档与恢复状态 | 本文件 + 最终报告 + README/FEATURE-STATUS/台账/.workbuddy-run |

---

## WP0（基线）

- 核查：分支 `workbuddy/run013-production-reliability`、HEAD `85f18c1` 与 origin 一致、工作区 clean、
  `main=ac2876e` 未修改；创建 `workbuddy/run014-product-closure`。
- 复核 RUN-013 报告、FEATURE-STATUS、DEPLOYMENT、`.workbuddy-run/*`：状态文件仍停在 RUN-011，
  本轮已全部更新到 RUN-014。
- 产出：`docs/RUN-014-BASELINE.md`（功能状态 / 架构 / 已验证 / 未验证 / 缺陷 / 风险 / 阻塞 / 计划）。

## WP1（Repair Regression）

- 缺陷：前端 Promotion 与 Regression 共用一个 `promoted` 条件；`promotion_record` 非空（含
  `VERSION_CONFLICT`）即显示完成；后端根本没有 Regression。
- 实现：独立 `regression_status`（NOT_STARTED/QUEUED/RUNNING/PASSED/FAILED/INCONCLUSIVE），
  `regression_run()` 复用现有 Task/Run/Worker 队列，Oracle 缺失/证据缺失 fail-closed，
  终态幂等、在途只收敛同一 Run、超时不伪造完成；`regression_stale` 标记结论失效。
- 前端：7 段生命周期全部取服务端字段，四态（done/current/fail/todo），新增回归面板与列表列。
- RBAC：新增 `repair.regression`（qa-lead+），只读状态仍为 `read`。
- 测试：`tests/integration/test_repair_regression_run014.py` 13 passed（覆盖 12 条验收 + 3 条 fail-closed）；
  影响面 101 passed。

## WP2（Lease / Fencing）

- 缺陷：`lease_valid`/`renew_lease` 不看 `lease_expires_at`；Pipeline 的"先 SELECT 再写"存在 TOCTOU；
  `submit_result` 不校验租期；中间态提交提前清零租约。
- 实现：新增 `persistence/fencing.py` 的 `fenced_guard()`——在写入同一事务内对 `tg_run` 做
  条件空更新（行锁 + CAS 判定）；Step/Evidence/Event 全部接入；`run.completed` 用
  `allow_own_submission` 只接受"同 worker 同代次已提交终态"。
- 测试：7 条并发故障（过期未接管、暂停→接管→恢复、FINALIZING、4 线程争抢、重启、幂等、续租边界）。
- 全量：791 passed / 16 skipped。

## WP3（SSE / RBAC / 统计）

- 缺陷：SSE 只在建连时算一次范围；`system/llm/summary` 未纳入 `GLOBAL_OBSERVATION_PATHS`；
  `llm/summary` 用 `limit=1000/500` 静默截断；Artifact 授权与读取分两个事务。
- 实现：SSE 每轮询重新核验会话/用户/Workspace（`auth.revoked` + 关闭）；
  `DocStore.iter_all()` 键集分页 + `coverage` 声明；Artifact 授权单源化；
  `/queue/stats` 增加 `scope` 声明；`/probes/audit` 声明非持久化边界。
- 实测踩坑：`requested_ws` 与"可见集合"混用导致非管理员撤权永不触发，已修复并覆盖测试。
- 测试：13 passed；全量 804 passed。

## WP4（全血缘 UI 验收）

- 脚本 `scripts/full_loop_run014.py`：Intent→Explorer→Trace→Compiler→Oracle→Binding→Replay→
  Review→Run→Diagnosis→Probe→Repair→Sandbox→Approval→Promotion→Regression→Rollback。
- 环境：真实 API（认证+CSRF，关闭内嵌 Worker）+ 2 独立 Worker + 真实 TeamFlow + 真实 Chromium。
- 结果：**40/40 PASS**，13 张截图，机器可读 `full-loop.json`（含 14 个血缘 ID）。
- 负向：无回放审核 412、真实故障 Run FAILED、回归 FAILED、人工回滚、未审批 Oracle 门禁。
- 新缺陷：`_step_index` 对 `step_index=None` 调 `int(None)` → `run_config.*` 提案 HTTP 500；已修复 + 测试。

## WP5（Benchmark v5 + AI）

- Benchmark：`scripts/bench_v5_run014.py` 起独立环境（SQLite + TeamFlow + 3 Worker），
  跑 `bench_v5_run011_r1.py` 23 场景 × 2 轮 → 46 执行 / 38 诊断样本 / 合同 5/5 /
  False Pass 0/38（PARTIAL）/ 规则 PCA 0.6842 / 探针事后回灌 0.7368 / 探针成功率 1.0。
- AI：`scripts/ai_quality_eval_run014.py`，8 域 × 4 = 32 例，规则 vs LLM 同输入；
  规则 0.9688/0.9683，LLM 细粒度 0.875/0.6119、粗粒度 0.875/0.6481，fallback 0、无效 0、
  重复 0、证据支持 1.0、P95 5320ms、成本 $0.0065。
- 四类顾问真实 Smoke：Diagnosis（32 次）、Compiler（2 步/1 丢弃）、Probe（3 候选，valid）、
  Repair（2 条受限变更 + 2 条告警）；Probe Planner 生产默认仍关闭。

## WP6（发布门禁）

- Ruff check / format：全绿（230 files formatted）。
- 前端 `npm run build`（含 `vue-tsc --noEmit`）：通过。
- 全量 pytest：**805 passed / 16 skipped**（skip 均为未配置 PG 的专项）。
- PostgreSQL 16 专项：**16 passed**（parity / platform / workspace）。
- PG16 Fencing 专项：`scripts/verify_fencing_pg_run014.py` **9/9 PASS**。
- 60 分钟 PG16 长稳：**NOT_RUN / INCONCLUSIVE**：仅留下约 4 分钟 Worker 日志与瞬时指标，无 `soak-60min.json` 汇总；脚本已参数化 `TG_SOAK_PG_DB` 以隔离历史库，待重跑。
- CI：见最终报告（PR Gate 通过公开 API 读取；Nightly/Release 未触发 → NOT_RUN）。

## WP7（文档与恢复状态）

- 新增：BASELINE / WORKLOG / WP1~WP5 专题 / FINAL-REPORT。
- 更新：README、FEATURE-STATUS、TraceGuard 台账；`.workbuddy-run/*` 保留 RUN-011-R1.1 及更早历史记录，本轮未改写（权威状态以最终报告与 RUN-014 专题为准）。
- 历史 RUN-013 交付记录只追加、不覆盖；`docs/assets/run011*|run012|run013` 零改动（已用 git diff 校验）。
