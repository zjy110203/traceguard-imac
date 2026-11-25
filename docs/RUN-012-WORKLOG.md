# RUN-012 工作日志

分支：`workbuddy/run012-product-completion`｜基线：`0ca128f`（`workbuddy/run011-r1.2-security-benchmark`）
原则：每个工作包独立提交；不删除旧测试、不弱化断言、不把 NOT_RUN / BLOCKED 写成 VERIFIED。

---

## WP0：R1.2 交付复核与 Benchmark 可信度补修

- **开始时间**：2026-11-25 16:15 CST
- **完成时间**：2026-11-25 16:29 CST

### 修改内容

1. 新增 `scripts/bench_scenario_contract.py`：23 场景显式契约（期望结果 / 控制类别 / 故障触发条件）、五分类 + 正常负样本标签、TriState 触发证据、历史 9 条异常逐条分类表、契约校验器。
2. `scripts/bench_v5_run011_r1.py`：`V5_SCENARIOS` 由契约生成；新增 `enrich_record()` 与 `_exception_record()`，异常路径保留 GT / 故障 / TestCase / run_id / 诊断状态 / 错误细节；统计新增 `record_classification_counts`、`false_pass_evaluation`、`contract_problems` 等字段，严格 False Pass 口径要求故障触发证据，412 不再自动排除。
3. `scripts/bench_pilot_run011_r1.py` + `scripts/full_loop_run011.py`：新增 `TeamFlow.fault_state()`；`run_scenario()` 抓取真实故障 state 快照，并把 412 诊断异常包装为保留上下文的 `ScenarioExecutionError`。
4. 新增 `tests/unit/test_benchmark_scenario_contract_run012.py`（14 项：需求指定的 10 类 + 历史 9 条拆分 + PARTIAL 状态 + 契约完整性 + 上下文保留）。
5. 新增 `docs/RUN-012-WP0-BENCHMARK-ERRATA.md`、`docs/assets/run012/wp0/scenario-contracts.json`（原始归档未修改）。

### 测试结果

- Benchmark 专项（新增 14 + R1.2 原 9 + Evaluator 2）：**25 passed**。
- `ruff check` 改动文件：All checks passed；新增文件 `ruff format` 通过。
- 原始归档 SHA-256 前后一致（`v5-2rounds.json` `d99bc2e…`、`pilot-3scenarios.json` `2e96e24…`）。
- 历史 False Pass 指标如实标记 **PARTIAL**（0/37，证据等级 INFERRED_ONLY），未为维持 0/37 强行归类。
- 当前提交：待本工作包提交后填写。

### 剩余问题 / 下一项

- 未重跑 23×2 真实 Benchmark（成本与独立部署环境约束本轮暂不做；Errata 明确历史状态）。
- 下一项：WP1 — 修复 CI 分支触发规则，检查 PostgreSQL 16 专项，尝试真实 CI 触发并如实记录证据。
