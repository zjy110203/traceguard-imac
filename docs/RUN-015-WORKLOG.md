# RUN-015 工作日志：执行一致性最终加固与发布就绪验收

> 分支：`workbuddy/run015-execution-consistency`｜基线：`438c54a`（RUN-014 最终 HEAD）
> 开始：2026-11-26 13:36 CST｜原则：不扩范围、不覆盖用户修改、不把未完成项写成 PASS。

---

## 1. 执行顺序与提交台账

| WP | 主题 | 提交 | 状态 | 关键交付 |
|---|---|---|---|---|
| WP0 | 基线核查 | `aa7f67a` | PASS | `RUN-015-BASELINE.md`；HEAD/工作区/main/PR Gate 核验 |
| WP1 | Worker Fencing 最终一致性 | `554a566` | PASS | `RUN-015-WP1-FENCING.md`；SQLite 10 + PG16 6 |
| WP2 | Regression 并发幂等与时效 | `868743f` | PASS | `RUN-015-WP2-REGRESSION.md`；10 并发单 Run |
| WP3 | SSE 重核验间隔上限 | `99fcda3` | PASS | `RUN-015-WP3-SSE-SECURITY.md`；7 专项 |
| WP4 | CI 覆盖 | `b1291a6` | 配置 PASS | `RUN-015-WP4-CI.md`；真实 run 待最终推送核验 |
| WP5 | PG16 长稳与全链路 | 最终收口提交 | PASS | 两轮 60 分钟长稳；PG16 全链路 40/40；`RUN-015-WP5-PG16-SOAK.md` |
| WP6 | Benchmark 证据 | `3fc9154` | PARTIAL | `RUN-015-WP6-BENCHMARK.md`；历史归档冻结 |
| WP7 | 文档 / 台账 / 恢复状态 | 最终收口提交 | PASS | 本日志 + FINAL-REPORT + `.workbuddy-run/*` + CI 证据 |

## 2. 各 WP 实际动作

### WP0

- 从 `438c54a` 创建分支；核对工作区 clean、`origin/main=ac2876e`、`origin/...` 一致、ahead 94。
- 经 GitHub 公开 API 核验 RUN-014 最终 PR Gate `45756471627` 在 `438c54a` 上 `success`。
- 发现待修订问题：WORKLOG 24/92 矛盾、FEATURE-STATUS「26 passed」无证据、血缘 ID 14 vs 15、
  台账缺 RUN-012/013/014、`.workbuddy-run/*` 停留旧阶段、full-loop.json 绝对路径。

### WP1

- `submit_result()` 在提交事务内调用 `fenced_guard`，拿锁后刷新真实行并复查回执；
  保留同键重放、终态冻结、`FINALIZING→COMPLETED` 与 `allow_own_submission`。
- `RunPipeline._emit()` 的 `step.completed` / `assertion.evaluated` 补 fence；全仓确认 Run 事件
  只从 `_emit()` 发出，六个调用点均受保护。
- 实测 PG16：把 `clock_timestamp()` 写进条件 UPDATE 的 WHERE 仍会在等锁后用旧快照求值；
  改为 `SELECT … FOR UPDATE` 取锁后再读数据库时钟，`renew_lease` / `mark_running` / guard 统一使用。

### WP2

- 新增 `tg_regression_claim` 唯一认领行 + `create_run_once()`，Run 与 Claim 同事务插入；
  `create_task` 捕获 `IntegrityError` 并发复用；Repair 文档写入使用 CAS 并对历史结论去重。
- `regression_status()` 读取时按当前批准版本重算 stale，不改原始 Verdict。
- INCONCLUSIVE 重试语义登记 NEEDS_DECISION，未改状态机。

### WP3

- 服务端硬上限 10s、下界 0.1s；NaN/Infinity/异常值回落默认 2s；响应头暴露生效间隔。

### WP4

- PR Gate 纳入 RUN-014 三专项 + RUN-015 三专项，新增 `workbuddy/run015-*` 触发；
  Nightly 增加 push 触发与 PG16 fencing 两项；脚本支持 `TG_TEST_POSTGRES_URL` 且新证据独立落盘。
- 本地等价快检 114 passed；Release 仍 dispatch-only，NOT_RUN。

### WP5

- 脚本新增 `--workers`；冒烟验证 3 独立 Worker 环境（15/16，唯一失败是冒烟周期数不足，非缺陷）。
- 首轮正式长稳：`traceguard_run015_soak`，59.61 分钟 / 114 Run / 30 PASS / 1 FAIL / 1 NOTE；
  FAIL 为“必须指定 replacement 接管”的断言误判，DB 证明由 `r15-soak-w3` 合法接管、fencing 2。
- 修正观测逻辑（任一存活 Worker 接管且 fencing 递增；pause 观察窗 60→180s）后复验：
  `traceguard_run015_soak_r2`，59.71 分钟 / 115 Run / 31 PASS / 0 FAIL / 2 NOTE。
- PG16 全链路：`full_loop_run014.py` 增加 `--out` / `--db-url` / `--tf-db-url`，在新库
  `traceguard_run015_full_loop` 上 40/40 PASS，Regression 正向/失败、回滚、证据回读均通过。

### WP6

- Benchmark 记录新增真实故障激活/效果时间戳、 observational effect status、evidence_refs；
  `--out` 可指向新目录；历史归档 SHA-256 单元测试冻结。
- 新一轮 23×2 因长稳占用资源未执行，False Pass 保持 PARTIAL。

### WP7

- 修正 RUN-014 WORKLOG / FINAL REPORT 口径、FEATURE-STATUS 统计、血缘 ID 数；
  台账按时间顺序补齐 RUN-012/013/014/015；`.workbuddy-run/*` 实际更新到 RUN-015；
  `full-loop.json` 截图路径改为仓库相对路径并验证 13 张均存在。
- 最终报告、CI run 证据与全量回归证据在 WP5 收口后补齐；
  首次全量 pytest 的 1 failed / 827 passed 已另存，修复后最终 828 passed / 16 skipped。

## 3. 外部阻塞与未解决问题

B-17、宿主级隔离、外部真实站点、真实人工基线、LLM 四臂成本实验、Release Gate、
Benchmark 新一轮、INCONCLUSIVE 重试（NEEDS_DECISION）、PROMOTION_FAILED 重发入口、
`/system/config` 权限边界、P03/P08 决策、LICENSE、PR #1/#2 集成策略——均如实保留，不标 PASS。
