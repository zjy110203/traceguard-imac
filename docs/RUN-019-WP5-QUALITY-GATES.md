# RUN-019 WP5：质量门禁与发布准备

> 分支 `workbuddy/run019-product-completion`，HEAD 见 WORKLOG。执行时间：2026-11-27。

## 1. 后端门禁

| 门禁 | 结果 | 证据/命令 |
|---|---|---|
| ruff check（全仓） | **PASS** | `.venv/bin/ruff check .` → All checks passed |
| ruff format --check | **PASS** | 533/538 formatted；5 个例外均为历史文档 md（`docs/RUN-017-*`、`资料/`），非本轮代码 |
| 单元 + 契约 + 集成（SQLite 全量） | **PASS：1131 passed / 16 skipped** | `pytest tests/ -q`（skipped 均为 PG 专项，见下行单独执行；411s） |
| PostgreSQL 16 专项 | **PASS：32 passed** | parity/platform/workspace/fencing×3 @ `traceguard_run019_pgtests`（24s） |
| 并发门禁 | **PASS：13/13** | `regression_multiprocess_run016.py` 十进程 @ PG16（WP4 证据） |

## 2. 前端门禁

| 门禁 | 结果 | 证据 |
|---|---|---|
| TypeScript strict（vue-tsc） | **PASS** | `npm run typecheck` |
| 生产构建 | **PASS** | `npm run build`（vite，无错误） |
| 浏览器视图验收 | **PASS：77/77** | 19 视图 × 2 主题 × 2 断点，真实 Chromium + axe + 键盘 + 溢出检查（`docs/assets/run019/wp3/`） |
| 真实浏览器资产业务闭环 | **PASS：8/8** | 认证开启 Cookie 会话（`docs/assets/run019/wp3/ui-asset-loop.json`） |
| E2E 全血缘（UI 部分） | **PASS** | full-loop 13 步真实 UI 点击（WP4） |

## 3. 可靠性门禁

62 分钟真实长稳（`scripts/soak_run013.py --minutes 62`）：

- 真实 PG16（`traceguard_run019_soak`）+ 独立 API + 2 个独立 Worker 进程；
- 真实 TestCase → 真实 Chromium → 真实 TeamFlow 周期执行；
- 真实故障注入（SIGTERM / SIGKILL / SIGSTOP 接管 / API 重启 / DB 连接中断 / SSE 断连）；
- **结果：33/33 checks PASS**（`docs/assets/run019/wp5/soak/soak-62min.json`）——
  墙钟 62.16 分钟、122 个真实 Run 全部终态、四类故障全部恢复、数据一致性七项全零异常。

CI 版 Reliability Gate（≥60 分钟 + min_runs）需要手动 workflow_dispatch，
本轮未获得授权触发（与 B-25 同口径，如实记录）。

## 4. CI 门禁核对（head_sha 口径）

| 工作流 | 状态 | 说明 |
|---|---|---|
| PR Gate | **BLOCKED（无法核实）** | 分支已推送：`workbuddy/run019-product-completion` @ `9f21f03`→`756afac`；无 gh CLI/凭据（B-26/B-27），匿名 GitHub API 限流 403，无法核对该 head_sha 的实际运行结果 |
| Nightly | **BLOCKED（无法核实）** | 同上；分支过滤含 `workbuddy/run*`（RUN-017 已修），待推送后按调度触发 |
| Reliability Gate | **NOT_RUN（CI）/ 本地替代** | 需要 workflow_dispatch 手动触发 + 120 分钟窗口；本地已执行 62 分钟真实 soak |

## 5. 发布准备清单

- [x] 分支推送（`origin/workbuddy/run019-product-completion`）
- [x] 全部工作包证据归档 `docs/assets/run019/` 与 `docs/RUN-019-*.md`
- [x] 台账更新（`.workbuddy-run/`）与 FEATURE-STATUS/README
- [ ] PR 创建（BLOCKED：无 gh/凭据，B-26 口径）
- [ ] CI 门禁真实 head_sha 核验（BLOCKED：B-27 口径）
- [ ] main 合并：未授权，不执行
