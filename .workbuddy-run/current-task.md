# 当前任务（RUN-020-PREPARATION：最终交付审计与智能化升级准备，2026-11-27）

> 分支 `workbuddy/run019-product-completion`，HEAD `564b21c`（本地 = 远端）。
> 本轮**只做审计、准入材料与 RUN-020 设计**，不改业务代码。

## 完成情况（M0）

- 第一阶段审计：分支/HEAD/工作区/相对 main（172 提交、0 落后、线性）/R2 七提交/文档一致性/
  临时产物/RUN-014~018 证据完整性，全部逐条核实 →
  `docs/RUN-020-PREPARATION-AUDIT.md`。
- 发现并修复文档不一致：README / FEATURE-STATUS / RUN-019-FINAL-REPORT / R2-GAP-CLOSURE
  的 CI 编号仍停在 `#113/#66 @ 19f527f`，已回填 **#115/#68 @ `564b21c`**（历史行保留不改写）。
- R2-B1：Reliability Gate 需 `workflow_dispatch` + Actions 写权限；本机无 `gh`/令牌 →
  **NOT_RUN（外部阻塞）**，执行步骤与 9 条硬验收条件已成文。
- R2-B2：真实 LLM —— **本地 REAL_PROVIDER 11/11 PASS**（`scripts/llm_smoke.py`，HEAD `564b21c`）；
  **CI 仍 BLOCKED**（仓库未配 Secret，Nightly #68 注释确认）。未读取/导出任何密钥。
- R2-B3：PR 材料已产出（`docs/RUN-020-PR-DESCRIPTION.md`）；**未创建 PR**（无授权/无 `gh`）。
- 第三阶段：RUN-020 方案（WP1~WP5）→ `docs/RUN-020-DESIGN.md`（架构/数据流/文件作用域/
  验收标准/测试策略/依赖/风险）+ `docs/RUN-020-WORKPACKAGES.md`（30 个任务卡，含验收命令）。

## 复核结果

- `pytest -q`：**1144 passed / 16 skipped / 0 failed**（382.40s，16 skip 均本地无 PG）。
- `ruff check`：All checks passed；`ruff format --check`：281 files already formatted。
- CI：PR Gate **#115** SUCCESS（3m40s）；Nightly **#68** SUCCESS（6/6，10m52s）。

## 遗留与如实记录

- Reliability Gate（CI）仍 NOT_RUN：需有 Actions 权限者 dispatch（62/100/3，不得下调）。
- CI 真实 LLM 仍 BLOCKED：需配置仓库 Secret；本地通过不替代 CI。
- PR #1/#2 仍 open 且为本分支祖先：不得分别合并，以 RUN-019-R2 PR 为唯一入口。
- LICENSE 仍 NEEDS_DECISION；`docs/assets` 241.7 MB 入库（本轮不改历史证据）。

禁止：改 main、强推、reset --hard、git clean、自动合并 PR、读取凭据。

---

# 前一任务（RUN-019-R2：产品收口、CI 修复与发布准入，2026-11-27）

> 分支 `workbuddy/run019-product-completion`。RUN-019 起点 `73edc346`；
> R2 起点 `61ffd4a`；R2 代码提交 `2b90d6c` → `dfb49b2` → `19f527f`。
> RUN-018/RUN-019 台账全部保留，本文件切换为 R2 口径。

## 完成情况

- R2-1 UI E2E：`StepEditor` 稳定 `data-testid` + 断言 expected 可视化编辑；
  验收脚本改走真实可视化操作并从服务端回读。core profile **42 项 / 41 PASS / 0 FAIL / 1 NOTE**。
- R2-2 来源可信：公共 HTTP 固定 human（`source_origin=manual_http`），
  compiler/healed 声明 → 403；版本递增继承血缘；Explorer/Repair 走
  `create_trusted_candidate()`。专项 8/8。
- R2-3 权限门禁：补齐 8 个无 `can()` 视图；四角色真实验收（真实登录 + 真实浏览器 +
  服务端 403）**24/24**。
- R2-4 Workspace/版本：Monitoring 默认当前 Workspace、全局汇总显式勾选并展示 scope；
  `authoring_hash` 校验历史业务内容（篡改 → 409）。专项 5/5。
- 本地门禁：ruff 全绿；后端全量 **1144 passed / 16 skipped**；typecheck + build 通过。
- R2-5/R2-6：CI 与最终报告（进行中，见 `.workbuddy-run/blockers.md`）。

## 遗留与如实记录

- LLM Secret 未配置：llm-smoke 按规则 BLOCKED，不声称真实调用成功。
- Reliability Gate（CI）需 workflow_dispatch：本机无 gh CLI/凭据，无法触发；
  本地 62 分钟 soak（RUN-019）33/33 保留，CI 侧如实标记 NOT_RUN。
- PR 创建：未授权，不执行。

禁止：改 main、强推、reset --hard、git clean、自动合并 PR、读取凭据。
