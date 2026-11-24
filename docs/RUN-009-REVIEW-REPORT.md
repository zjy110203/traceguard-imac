# RUN-009 复核报告（WP0：RUN-008 最终状态与 GitHub 集成）

> 生成时间：2026-11-24 16:10 CST。所有结论均以 git 实测与 GitHub API 返回为准，历史文档正文保留但不再作为权威起点。

## 1. Git 与差异复核

- 仓库与分支：`zjy110203/traceguard-imac`，`workbuddy/run008-quality-hardening`，HEAD `cb80591`，工作区干净（RUN-009 新增文件前的起点）。
- `origin/main` = `ac2876e`；本地无 `main` 分支，必须使用 `origin/main` 做差异比较。
- 差异规模：32 提交、422 文件、`+32604 / -644`，领先 32、落后 0、无冲突。
- 构成：RUN-007 遗留 19 提交（`d2e86ae`..`ce1acea`）+ RUN-008 13 提交（`00d2ee0`..`cb80591`）。
- 差异内容已分类审查：RUN-007 遗留提交、RUN-008 代码提交（`00d2ee0`/`1f8df24`/`40efee6`）、数据库迁移 v2–v10（内嵌于 `db.py`）、CI 配置（`pr-gate.yml`/`nightly-gate.yml`/`release-gate.yml`）、测试证据（`docs/assets/run007|run008/`、`logs/run008/`）、文档与恢复文件。未发现需要丢弃的历史提交。
- 当前 HEAD 的 PR Gate check runs：`backend=success`、`frontend=success`。Nightly/Release 三段全绿证据属于 `40efee6`，不得外推为 `cb80591` 的运行结果。

## 2. Pull Request 集成

- PR #1（`workbuddy/run007-resume-20261123` → `main`）仍 open，`mergeable=true`、`mergeable_state=clean`，其 head `ce1acea` 是本分支祖先；因此新建 PR #2 覆盖完整 32 提交，避免合并时丢失 RUN-008 历史。
- 已创建 **PR #2**：<https://github.com/zjy110203/traceguard-imac/pull/2>，源分支 `workbuddy/run008-quality-hardening`，目标 `main`，状态 open。
- **合并决策：暂不合并（NEEDS_DECISION / HOLD）**。理由：完整授权允许合并，但 WP0 复核发现仍有未处理的高风险事项——真实认证/RBAC 未实现（请求体可伪造审批人）、B-17 下四个 Worker 故障场景 BLOCKED、网络出口受控代理尚未验证；这些事项正在 RUN-009 处理。按「没有未处理的高风险问题才可合并」的条件，先保持分支与 PR 开放；RUN-009 完成后在 WP9 重新评估，必要时以新提交更新同一 PR 的 head_sha 并重跑 CI。
- 未执行 `reset` / `clean` / 强推 / 历史改写；未关闭或合并 PR #1。

## 3. 状态不一致修正

已在保留历史记录的前提下，为以下文件顶部追加最新权威状态摘要（不覆盖旧内容）：

| 文件 | 原问题 | 处理 |
|---|---|---|
| `docs/RUN-008-FINAL-REPORT.md` | 提交链止于 `0a6e3c3`、称报告未提交、无 `cb80591` | 顶部追加更正：实际 HEAD `cb80591`；`40efee6` 仅为代码/CI 基线 |
| `.workbuddy-run/progress.md` | 顶部仍为 RUN-001、非 Git 仓库、阶段 0 NOT_STARTED | 顶部追加 RUN-009 权威摘要并保留 RUN-001~RUN-007 历史 |
| `.workbuddy-run/resume.md` | 标题为 RUN-006，RUN-008 完成段 HEAD `804fc5d`/领先 31 过时 | 顶部追加 RUN-009 恢复起点 |
| `.workbuddy-run/blockers.md` | 标题为 RUN-002，B-18 内部前后矛盾 | 顶部追加权威状态；B-17 保持 BLOCKED |
| `.workbuddy-run/phase-status.md` | 标题为 RUN-002 复核，无 RUN-008 段 | 顶部追加 RUN-009 阶段入口 |
| `docs/TraceGuard-持续开发任务台账.md` | R8-08/R8-11/R8-12 仍写「进行中」 | 在 RUN-009 段统一口径，历史行保留 |
| `docs/FEATURE-STATUS.md` | Release `45336007166` 仍写「进行中」 | 顶部追加 RUN-009 状态说明，历史行保留 |

统一口径：

- `40efee6` = 最终代码提交与三段 CI 基线；`804fc5d` = 最终报告提交；`cb80591` = 实际最终 HEAD（仅恢复文件）。
- RUN-008 WP3/WP6 = PARTIAL；B-17、LLM smoke、Benchmark LLM/人工基线 = BLOCKED；真实认证/RBAC = NOT_IMPLEMENTED。
- 「5 个 / 7 个前端修复」以 `docs/RUN-008-WP5-FRONTEND-AUDIT.md` 的 7 项明细为准：5 项前端 defect、2 项验收/证据修复。
- `docs/RUN-008-WP3-WORKER.md` 中「脚本未提交」的描述已过时，`worker_concurrency_run008.py` 实际由 `40efee6` 提交。

## 4. 结论

WP0 的核对、文档状态修正、PR 创建和差异审查已完成。GitHub 合并保持 HOLD，原因与重新评估条件已记录；后续每个 WP 的新证据写入 `docs/assets/run009/` 与新增 `docs/RUN-009-*.md`，不覆盖历史证据。
