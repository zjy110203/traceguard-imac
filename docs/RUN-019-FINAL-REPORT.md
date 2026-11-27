# RUN-019 最终报告（产品完整性收口）

> 分支 `workbuddy/run019-product-completion`；基线 = RUN-018-R2 HEAD `05a3639`；
> 本轮提交 `f5946d5` → `756afac`+。执行时间：2026-11-27。
> 口径：每项结论给出可回读证据路径；BLOCKED/NOT_RUN 不含糊。

## 0. 结论摘要

| 工作包 | 结论 | 关键证据 |
|---|---|---|
| WP0 差距分析 | DONE | `docs/RUN-019-GAP-ANALYSIS.md`（六态口径） |
| WP1 核心资产管理 | **VERIFIED** | 联合验收 SQLite 20/20 + PG16 20/20；`docs/RUN-019-WP1-ASSET-MANAGEMENT.md` |
| WP2 智能诊断升级 | **VERIFIED** | 独立 Holdout 30×2：目标类 Recall 1.0、ambiguous 全弃权、API/NETWORK 无回归；`docs/RUN-019-WP2-DIAGNOSIS.md` |
| WP3 前端完整性 | **VERIFIED** | a11y 77/77；认证开启真实浏览器资产闭环 8/8；`docs/RUN-019-WP3-FRONTEND.md` |
| WP4 完整闭环回归 | **VERIFIED** | PG16 full-loop 40/40；回归十进程并发 13/13；`docs/RUN-019-WP4-BUSINESS-LOOP.md` |
| WP5 质量门禁 | **VERIFIED**（CI 项除外） | 全量 1131 passed；PG 专项 32；TS/build；soak 62min；`docs/RUN-019-WP5-QUALITY-GATES.md` |
| WP6 文档台账 | DONE | README / FEATURE-STATUS / `.workbuddy-run/` 已更新；历史报告未删除 |

## 1. 本轮新增/修复的真实缺陷（不含 RUN-018 已修项）

1. **最后管理员并发绕过**：角色变更无锁，两个并发撤销可致平台失去管理员。
   修复：`tg_admin_guard` 单行锁靶（PG `FOR UPDATE` / SQLite 写锁）+ 事务内复检。
2. **Workspace created_by 可伪造**：创建 API 采信请求体；修复为服务端可信主体。
3. **TestIntent 状态无门禁**：任何角色可任意改状态；修复为状态机 +
   submit/approve 权限分离 + 被引用资产时拒绝编辑/废弃。
4. **Oracle 审批无历史**：审批覆盖式；修复为追加 `approval_history` + 历史版本只读。
5. **`list_users` roles 字符串化**：API 返回原始 JSON 字符串而非数组；已修复。
6. **前端权限矩阵缺漏**：`repair.regression`/`system.observe` 未同步到前端，
   权限门禁化后合法操作被禁（full-loop 真实暴露）；已对齐。
7. **a11y 三类违规**：滚动块无键盘焦点、暗色主题链接对比度 3.3、
   `.muted` 浅色 4.42、390px 表格溢出；全部修复并复测。

## 2. 诊断能力口径（与 RUN-018 的关系）

- RUN-018 Holdout（24 变体）结论**原样保留**，未重跑、未改动 manifest。
- RUN-019 新 Holdout（30 变体 × 2 轮，独立 manifest + SHA-256 冻结）针对
  PERSISTENCE/TIMING/LOCATOR 三类结构性弃权升级证据链后建立。
- 新 Holdout 结果：目标三类 Recall 1.0；API/NETWORK 无回归；ambiguous 全弃权。
  显式分母与逐样本记录见 `docs/assets/run019/wp2/holdout-evaluation.json`。

## 3. Reliability（62 分钟 soak @ PG16）

> 结果以 `docs/assets/run019/wp5/soak/soak-report.json` 为准（本节由执行结果回填）。

## 4. 未决事项（如实记录）

| 编号 | 事项 | 状态 |
|---|---|---|
| B-26 | PR 创建 | NOT_RUN（无 gh CLI/凭据；分支已推送，等待授权创建 PR） |
| B-27 | CI 真实 head_sha 核验（PR Gate/Nightly） | BLOCKED（匿名 GitHub API 限流；需凭据） |
| B-25 | CI Reliability Gate | NOT_RUN（需 workflow_dispatch 手动触发）；本地 62 分钟真实 soak 已替代执行 |
| — | main 合并 | 未授权，不执行 |

## 5. 交付物索引

- 专题文档：`docs/RUN-019-{GAP-ANALYSIS,WP1-ASSET-MANAGEMENT,WP2-DIAGNOSIS,WP3-FRONTEND,WP4-BUSINESS-LOOP,WP5-QUALITY-GATES}.md`
- 工作日志：`docs/RUN-019-WORKLOG.md`
- 证据：`docs/assets/run019/wp{1..5}/`
- 台账：`.workbuddy-run/{current-task,phase-status,progress,blockers}.md`
