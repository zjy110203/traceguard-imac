# RUN-016 基线核对与开发起点

> 核对时间：2026-11-26 17:20–17:40 CST
> 核对方式：git 实查 + 源码复核 + 文档比对 + 真实环境探测
> 结论：**基线可信，但运行环境与 RUN-015 不一致（本机 PostgreSQL 实例已不存在），
> 已按原拓扑重建 PG 16.16 后继续；12 个已知问题逐项复核结果见 §3。**

---

## 1. Git 与分支

| 项 | 实测 |
|---|---|
| 当前分支（开始时） | `workbuddy/run015-execution-consistency` |
| 本地 HEAD | `73e4fe689cdd950334b000cfe45961c9c7adc383`（RUN-015 CI: rerun gates on final tree） |
| `origin/workbuddy/run015-execution-consistency` | `73e4fe6`（与本地一致） |
| `origin/main` | `ac2876e`（第五轮，未改动） |
| 工作区 | 开始时干净（`git status --short` 为空） |
| 新建分支 | `workbuddy/run016-finalization`（自 `73e4fe6` 创建） |
| PR | PR #1 / #2 保持 open，未合并、未关闭 |
| 强制操作 | 未使用 `reset --hard` / `git clean` / 强推 / 改 main |

备注：`origin/main` 自 RUN-007 之后未再合并任何 RUN 分支，因此本轮以 RUN-015 交付分支为起点，
与指令"从最新已确认的 RUN-015 交付分支建立"一致。

### 已读交付文档

`RUN-015-BASELINE / FINAL-REPORT / WORKLOG / WP1-FENCING / WP2-REGRESSION /
WP3-SSE-SECURITY / WP4-CI / WP5-PG16-SOAK / WP6-BENCHMARK`、`FEATURE-STATUS.md`、
`.workbuddy-run/{current-task,phase-status,progress,resume,blockers}.md`。

---

## 2. 运行环境实际状态（与 RUN-015 的差异）

| 项 | RUN-015 报告 | RUN-016 实测 | 处理 |
|---|---|---|---|
| PostgreSQL | 16.16 @ `127.0.0.1:55432`，用户 `traceguard` | **实例与二进制均不存在**（无 psql/pg_ctl，5432/55432 均未监听） | 用 PostgreSQL 16.16 官方二进制重建同拓扑实例（数据目录 `~/.traceguard-pg16/data`，端口 55432，用户 `traceguard`，trust 认证仅本机） |
| Python | 3.13 | 3.13.12 | 一致 |
| Node | — | v22.22.2 | 用于前端 typecheck/build |
| Docker | 无 | 无 | 不依赖 |
| `gh` CLI | 无 | 无（`gh not found`） | **无法触发或查询 GitHub Actions**，CI 相关结论不得标 PASS |
| Playwright Chromium | 已安装 | `chromium-1255` 可用 | 真实浏览器执行可复现 |
| 凭据 | 不读取 | 未读取、未使用（`.env` 中的 LLM Key 未用于任何调用） | 遵守"不擅自读取或使用凭据" |

重建后自检：`select version()` → `PostgreSQL 16.16 (Postgres.app) on aarch64-apple-darwin21.6.0`；
`tests/integration/test_postgres_platform.py` + `test_postgres_workspace_run012.py` 共 **11 passed**（未设置
`TG_TEST_POSTGRES_URL` 时这 11 项在 RUN-015 报告里是 skip 的）。

基线全量测试（未接 PG 时）：**828 passed / 16 skipped**，与 RUN-015 报告的 828 passed 一致。

---

## 3. 12 个已知问题逐项复核

> 规则：不因历史报告写 PASS 就跳过源码复核；也不因报告写了问题就直接认定仍存在。
> 结论分为 **CONFIRMED（仍存在）/ PARTIAL（部分存在）/ ALREADY_OK（已不成立）**。

| # | 问题 | 复核结论 | 证据（源码/数据） | RUN-016 处理 |
|---|---|---|---|---|
| 1 | 长稳 59.71 分钟 < 60 | CONFIRMED | `docs/assets/run015/wp5/soak-r2/soak-60min.json`：`minutes_observed=59.71`，且判定用 0.95 容差 | 改为真实墙钟口径 + 严格 ≥60 分钟，重跑 62 分钟（WP1-A） |
| 2 | 95% 时间容差 | CONFIRMED | `scripts/soak_run013.py:596` `duration_min >= args.minutes * 0.95` | 删除容差；新增 `--min-minutes`（缺省等于 `--minutes`）、真实 `started_at/ended_at/wall_clock_minutes` |
| 3 | 两个 Worker 故障场景未实际触发 | CONFIRMED | 同一 JSON：`NOTE 故障：r13-soak-w1 强杀`、`NOTE 故障：r13-soak-w2 强杀`，均为"60s 内未观察到租约"跳过 | 强杀/暂停的租约观察窗 60s→240s，且"被别的 Worker 抢到"时立即再发 Run（不再串行等待），提高必然触发概率 |
| 4 | 长稳/全链路 JSON 带 RUN-013/014 标记 | CONFIRMED | `soak_run013.py` 硬编码 `"workpackage": "RUN-013 WP4"` | 参数化 `--workpackage/--run-tag`，并写入 `database_name`、起止时间 |
| 5 | Regression 10 并发是线程级 | CONFIRMED | `tests/integration/test_repair_regression_run015.py` 用 `threading.Barrier` + 线程 | 新增 `scripts/regression_multiprocess_run016.py`：10 个 OS 进程 + 独立 Runtime/连接 + `multiprocessing.Barrier`，PG16 实跑（WP2.1） |
| 6 | Benchmark `fault_effect_status` 依赖 Run Verdict | CONFIRMED | `scripts/bench_pilot_run011_r1.py`：`if activation and verdict in {FAILED, NOT_EVALUATED}: CONFIRMED` | 新增 `benchmark/traceguard_benchmark/fault_effect.py` 独立探针；v2 样本 False Pass 必须有独立证据（WP3） |
| 7 | CI 报告 SHA 与最新 HEAD 不一致 | CONFIRMED（文档口径） | RUN-015 报告中引用 `438c54a` 等中间 SHA，而最终 HEAD 为 `73e4fe6` | 本轮所有报告只引用"被验证的那棵树"的 SHA；无 `gh` 时 CI 一律 NOT_RUN，不写 PASS（WP5） |
| 8 | LLM Smoke 缺 Key 时是 BLOCKED，但 Job 成功易被误读 | PARTIAL | `nightly-gate.yml` 正确区分 BLOCKED 并 warning，但汇总表写成 `success / BLOCKED`；`release-gate.yml` 只校验 Job conclusion，**未校验 llm_status** | 汇总表拆成"Job 结果 / 真实测试证据"两列；新增 `llm-smoke-status` 工件，Release Gate 强制要求 `PASSED` 且 `real_llm_executed=true` |
| 9 | SSE 撤权重核验最大 10s | CONFIRMED | `apps/api/traceguard_api/main.py:36` `SSE_AUTH_RECHECK_MAX_SECONDS = 10.0` | 上限收紧到 5s（默认 2s / 最小 0.1s），更新 RUN-015 断言并新增专项（WP4.2） |
| 10 | Artifact 落盘与 Fencing 一致性 / 孤儿文件 | CONFIRMED | `ArtifactStore.put()` 无 fence 参数；文件先落盘、DB 后登记，失败即留孤儿；无扫描/回收机制 | `put(fence=, run_id=)` 同一事务守卫 + 原子写 + 失败回滚自己新建的文件；新增 `scan/collect_garbage/verify_consistency` 与 CLI（dry-run 默认，隔离区可回滚，审计 JSONL）（WP1-C） |
| 11 | Promotion Failed 后能否安全恢复 | CONFIRMED | `promote()` 仅接受 `APPROVED/PROMOTING`；`PROMOTION_FAILED` 直接 `illegal repair transition` → 失败后**无恢复入口** | 新增 `promotion_recovery_plan()` / `promote_recover()` + API；检查是否已实际晋升、重新校验 Oracle/人工审批/版本漂移，人工 confirm 后以新幂等键重试，不重复建版本（WP2.3） |
| 12 | `/system/config` 权限边界 | PARTIAL | 端点只读、无密钥；但任何已认证角色都能读到 `database` 方言、`artifact_dir` 绝对路径、`worker.id`（宿主名+PID） | 非管理员返回脱敏视图（敏感字段显式为 null），管理员/认证关闭模式保留完整视图；运行期无任何配置写入口（WP4.1） |

---

## 4. 本轮开发起点与约束

- 起点树：`73e4fe689cdd950334b000cfe45961c9c7adc383`
- 工作分支：`workbuddy/run016-finalization`
- 不修改 main、不合并/关闭 PR、不强推、不删历史证据。
- B-17 / 宿主级网络隔离 / 外部真实站点 / 人工诊断基线 / LLM 四臂计费 / Release Gate
  保持原状态（BLOCKED / NEEDS_DECISION / NOT_RUN），本轮不做任何绕过尝试。
- 所有新增证据落在 `docs/assets/run016/`，保留原始 JSON/日志。
