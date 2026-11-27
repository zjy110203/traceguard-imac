# RUN-019 WP5：质量门禁与发布准备

> 分支 `workbuddy/run019-product-completion`，HEAD 见 WORKLOG。执行时间：2026-11-27。

## 1. 后端门禁

| 门禁 | 结果 | 证据/命令 |
|---|---|---|
| ruff check（全仓） | **PASS** | `.venv/bin/ruff check .` → All checks passed |
| ruff format --check | **PASS** | 533/538 formatted；5 个例外均为历史文档 md（`docs/RUN-017-*`、`资料/`），非本轮代码 |
| 单元 + 契约 + 集成（SQLite 全量） | **PASS：1144 passed / 16 skipped**（R2 最终） | `pytest -q`（skipped 均为 PG 专项；419s） |
| PostgreSQL 16 专项 | **PASS：32 passed** | parity/platform/workspace/fencing×3 @ `traceguard_run019_pgtests`（24s） |
| 并发门禁 | **PASS：13/13** | `regression_multiprocess_run016.py` 十进程 @ PG16（WP4 证据） |

## 2. 前端门禁

| 门禁 | 结果 | 证据 |
|---|---|---|
| TypeScript strict（vue-tsc） | **PASS** | `npm run typecheck` |
| 生产构建 | **PASS** | `npm run build`（vite，无错误） |
| 浏览器视图验收 | **PASS：77/77** | 19 视图 × 2 主题 × 2 断点 = 76 组合 + 1 弹窗焦点检查；R2 后复跑见 `docs/assets/run019-r2/local/a11y/`（原证据 `docs/assets/run019/wp3/` 保留） |
| 四角色权限门禁 | **PASS：24/24** | 真实登录 × 真实 Chromium × 服务端 403（`docs/assets/run019-r2/local/rbac/`） |
| 真实浏览器资产业务闭环 | **PASS：8/8** | 认证开启 Cookie 会话（`docs/assets/run019/wp3/ui-asset-loop.json`） |
| UI 全链路（R2 复跑） | **PASS：41/0/1 NOTE** | 可视化 StepEditor 真实操作 + 服务端回读（`docs/assets/run019-r2/local/ui/`） |
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
| PR Gate #113 | **SUCCESS** @ `19f527f` | backend 3m36s（ruff/format/unit+contract/fast integration）+ frontend 26s（typecheck+build） |
| Nightly #66 | **SUCCESS（6/6 Job）** @ `19f527f` | backend-full 9m40s / security 17s / postgres 1m23s / ui-acceptance **2m01s** / llm-smoke 23s（真实调用 BLOCKED）/ gate-verdict 3s |
| Nightly #65（对照） | **FAILURE** @ `61ffd4a` | ui-acceptance `TimeoutError: Page.fill #tc-steps` → R2-1 修复后 #66 通过 |
| PR Gate #112（对照） | SUCCESS @ `61ffd4a` | 仅 backend+frontend，不能替代 Nightly 的 UI 验收 |
| Reliability Gate | **NOT_RUN（CI）** | 只能 `workflow_dispatch`；本机无 gh CLI/凭据；本地 62 分钟 soak 33/33 保留，不替代 CI |

R2 追加：`scripts/ci_pytest_watchdog.py` 把"整步挂起"变成可诊断失败
（15 分钟写线程栈到 Job Summary，35 分钟强制退出码 124）。

## 5. 发布准备清单

- [x] 分支推送（`origin/workbuddy/run019-product-completion`）
- [x] 全部工作包证据归档 `docs/assets/run019/` + R2 证据 `docs/assets/run019-r2/` 与 `docs/RUN-019-*.md`
- [x] 台账更新（`.workbuddy-run/`）与 FEATURE-STATUS/README
- [ ] PR 创建（BLOCKED：无 gh/凭据，B-26 口径）
- [x] CI 门禁真实 head_sha 核验：PR Gate #113 / Nightly #66 @ `19f527f` 均 success
- [ ] Reliability Gate CI 实跑（NOT_RUN：B-25，需 workflow_dispatch/凭据）
- [ ] 真实 LLM 调用（BLOCKED：B-24，缺 Secret）
- [ ] main 合并：未授权，不执行
