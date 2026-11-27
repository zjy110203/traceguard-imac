# 阻塞（RUN-018-R1，2026-11-27）

| 编号 | 事项 | 状态 |
|---|---|---|
| B-28 | Nightly ui-acceptance 持续失败 | **CLOSED**：Nightly #59 @ `05a3639` **整体 success（12m55s，6/6 job）**，ui-acceptance 2m4s 且无 FAIL annotation、证据 3.04 MB（失败时 133 KB）。根因：独立 Worker 白名单 PYTHONPATH 漏 `apps/workers`（本地 `traceguard.pth` 掩盖），`05a3639` 已修 |
| B-26 | PR #3 创建 | NOT_RUN（无 gh CLI，未授权不创建） |
| B-27 | gh/API 导出 CI 日志 | BLOCKED（无凭据，匿名 API 限流 403） |
| B-24 | Release Gate 真实 LLM 证据 | NOT_RUN（缺 Secret，禁止寻找凭据） |
| B-25 | Reliability Gate 真实运行 | NOT_RUN（需手动 120 分钟窗口） |
| B-NEW-2 | PR Gate #99~#101 早退（30-33s） | CLOSED：426f1e6 补丁漏跑 ruff format → format --check 门禁失败；94c706e 修复后 #102 全绿 |
| B-NEW-1 | WP6 Holdout 弃权率 66.7%（PERSISTENCE/TIMING/LOCATOR 全弃权） | OPEN：引擎设计使然（R1-04 弃权优于硬猜），如需提升 Recall 须修改确认规则 → 必须换新 Holdout |

# 阻塞（RUN-019-R2，2026-11-27）

| 编号 | 事项 | 状态 |
|---|---|---|
| R2-B1 | Reliability Gate CI 实跑 | **NOT_RUN**：只能 `workflow_dispatch`；本机无 gh CLI/凭据（B-26/B-27 同源）；本地 62min soak 33/33 保留，不替代 CI |
| R2-B2 | 真实 LLM 调用 | **BLOCKED**：缺 `DEEPSEEK_API_KEY`；nightly llm-smoke Job success 但如实标记 BLOCKED，gate-verdict 按既有规则通过 |
| R2-B3 | PR 创建 | NOT_RUN：无 gh CLI/凭据，未授权不创建；分支已推送 |
| R2-B4 | CI job log 导出 | BLOCKED：匿名 API 403（需 admin 权限），只能读 runs/jobs/annotations 与 Job Summary |
| R2-C1 | Nightly #66 全量测试"疑似挂起" | **CLOSED（误读）**：真实耗时 6m16s；早前"30 分钟仍在跑"来自页面/接口缓存。已加看门狗，后续真实挂起可诊断 |

# 阻塞（RUN-020-PREPARATION，2026-11-27）

| 编号 | 事项 | 状态 |
|---|---|---|
| M0-B1 | Reliability Gate CI 实跑 | **NOT_RUN（外部阻塞）**：仅 `workflow_dispatch`，需 Actions 写权限；本机无 `gh`、无授权令牌（凭据存储访问被安全策略拦截，未绕过）。步骤与 9 条硬条件见 `docs/RUN-020-RELEASE-ADMISSION.md` §1 |
| M0-B2 | CI 真实 LLM | **BLOCKED**：仓库未配置 `DEEPSEEK_API_KEY`（Nightly #68 注释确认）。**本地** `scripts/llm_smoke.py` 11/11 REAL_PROVIDER PASS @ `564b21c`，不替代 CI |
| M0-B3 | RUN-019-R2 PR 创建 | **NOT_RUN**：未授权 + 无 `gh`；材料已备（`docs/RUN-020-PR-DESCRIPTION.md`） |
| M0-B4 | 匿名 GitHub API | **BLOCKED**：`api.github.com` 403 限流；仅能读 HTML 页面（PR #115 / Nightly #68 由此核实） |
| M0-B5 | LICENSE | **NEEDS_DECISION**：仍无 `LICENSE` 文件，合并前需负责人决策 |
| M0-B6 | 历史证据体积 | **NOTE**：`docs/assets` 241.7 MB / 2623 文件（占入库 95%）；103 个重复文件 / 5.36 MB；3 张同字节截图。本轮不改历史证据 |
