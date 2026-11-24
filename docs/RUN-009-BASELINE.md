# RUN-009 基线审计（WP0）

> 权威状态摘要（2026-11-24 16:05 CST，以 git 实测为准）。历史快照保留在下文与既有文档中，不得覆盖。

## 0. Git 权威事实

- 仓库：`/Users/dlp--test/Desktop/traceguard`；远端 `origin` = `https://github.com/zjy110203/traceguard-imac.git`。
- `origin/main` = `ac2876ecef779b0bed900bdf3b5361ce1d2fa3d6`；本地没有名为 `main` 的分支，因此 `git log main..HEAD` 会报错，必须使用 `origin/main`。
- 当前分支：`workbuddy/run008-quality-hardening`；HEAD = `cb8059196db2aeffe8bdaba04f03011d4f5fa36b`（`RUN-008: 收尾恢复文件与最终阻塞状态`），与 `origin/workbuddy/run008-quality-hardening` 同步。
- 工作区在 RUN-009 开始前干净；相对 `origin/main` 领先 32 提交、落后 0，无分叉。未执行且不得执行 `reset` / `clean` / 强推 / 历史改写。
- 32 个提交 = RUN-007 遗留 19 个（`d2e86ae`..`ce1acea`）+ RUN-008 新增 13 个（`00d2ee0`..`cb80591`）。

## 1. 提交角色定义（防止误判基线）

| 提交 | 角色 | 内容 |
|---|---|---|
| `40efee6` | RUN-008 **最终代码提交**、三段 CI 的真实基线 | PR `45335765297` / Nightly `45335777381` / Release `45336007166` 均 success；其后提交不改变应用代码 |
| `804fc5d` | RUN-008 最终报告定稿 | 仅修改 `docs/RUN-008-FINAL-REPORT.md` |
| `cb80591` | RUN-008 **实际最终 HEAD** | 仅修改 `.workbuddy-run/blockers.md`、`.workbuddy-run/resume.md` |

三者不是同一角色的候选。`40efee6` 上的 CI 结果不得表述为 `cb80591` 上的运行结果（本轮若要声明新提交通过 CI，必须重新触发并核对 head_sha）。`.workbuddy-run/resume.md` 中「最终 HEAD `804fc5d`、领先 31」以及最终报告中「工作区仅剩本报告待提交」均已过时；RUN-009 权威起点是 `cb80591`。

## 2. 继承的能力状态（不照抄旧快照）

- **VERIFIED / 完成**：RUN-008 WP0/WP1/WP2/WP4/WP5/WP7/WP8。本机 `567 passed / 13 skipped`，E2E 12/12；长稳 60.14 分钟 / 66 周期 / 23 checks；Benchmark 两轮 × 30 场景；前端当时自报 60/60，但原始证据只覆盖 12 视图（见 WP1 缺口）。
- **PARTIAL**：RUN-008 WP3（Worker 并发 14/14、真实 SIGKILL 恢复 17/17 已 VERIFIED；补充 4 场景因 B-17 BLOCKED）；WP6（2 项真实修复，其余为设计边界）。
- **BLOCKED**：B-17（四个 Worker 补充故障场景，AgentDR 两次拦截）、LLM smoke（CI 缺 Secret）、Benchmark LLM 与人工基线、.dev-only moderate×3 未处置。
- **NOT_IMPLEMENTED**：真实身份认证与 RBAC。请求体中的 `reviewer` / `approved_by` 仍由客户端声明，`require_human_actor()` 只做主体命名空间门禁。
- **已披露设计边界，不得写成「已安全」**：顶层 302 出站可能已到达目标；DNS rebinding 仍存在短窗口；浏览器内核 DNS 不受应用控制；资源采样 CPU/RSS 为 null。

## 3. RUN-008 文档状态不一致清单（RUN-009 已修正或待修正）

1. `.workbuddy-run/progress.md` 顶部仍为 RUN-001（2026-11-22、非 Git 仓库、阶段 0 NOT_STARTED），无 RUN-008 段。已在文件顶部追加权威摘要。
2. `.workbuddy-run/phase-status.md` 顶部为 RUN-002 复核且无 RUN-008 段。已在文件顶部追加权威摘要。
3. `.workbuddy-run/resume.md` 顶部为 RUN-006 结束点，内部 RUN-008 完成段的 HEAD 过时。已在文件顶部追加权威摘要。
4. `.workbuddy-run/blockers.md` 标题为 RUN-002，B-18 内部前后矛盾（陈旧「仍在运行」与末尾「已解除」）。已在文件顶部追加权威摘要。
5. `docs/RUN-008-FINAL-REPORT.md` 提交链止于 `0a6e3c3`，且称报告未提交。已在文件顶部追加更正摘要，历史正文保留。
6. `docs/TraceGuard-持续开发任务台账.md` 中 R8-08/R8-11/R8-12 仍写「进行中」；`docs/FEATURE-STATUS.md` 中 Release `45336007166` 仍写「进行中」。统一口径见 `docs/RUN-009-REVIEW-REPORT.md`。
7. `docs/RUN-008-WP3-WORKER.md` 称 `worker_concurrency_run008.py` 未提交，实际由 `40efee6` 提交。
8. `docs/RUN-008-FINAL-REPORT.md` 与 WP5 报告对修复数量存在「5 个 / 7 个」口径差异：WP5 报告表格列 7 项，其中 5 项为前端缺陷修复，另 2 项为验收/证据修复；统一以 WP5 报告 7 项明细为准。

## 4. 沿用禁令

- 不覆盖 `docs/assets/run007/`、`docs/assets/run008/` 等历史证据；新证据写入 `docs/assets/run009/`。
- 不输出 `.env`、Token、密码；`DEEPSEEK_API_KEY` 等凭据只经授权配置机制读取。
- 不把 skip / BLOCKED 写成通过；不把旧 SIGKILL 证据当作四个新场景证据。
- B-17 的 AgentDR 拦截不得通过换工具、拆写入、改参数或其他方式规避。
- 不创建正式 Release tag；所有未完成事项必须在最终报告列出阻塞原因、影响范围、已完成工作、缺少条件与后续建议。

## 5. 本轮起点结论

可以开始 RUN-009 开发。权威起点：`cb80591`，干净工作区，`origin/main` 落后 32 提交。WP0 的 GitHub 集成结论见 `docs/RUN-009-REVIEW-REPORT.md`。
