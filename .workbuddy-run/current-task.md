# 当前任务（RUN-019-R2：产品收口、CI 修复与发布准入，2026-11-27）

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
