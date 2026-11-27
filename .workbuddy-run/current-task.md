# 当前任务（RUN-019：产品完整性收口，2026-11-27）

> 分支 `workbuddy/run019-product-completion`（起点 = RUN-018-R2 HEAD `05a3639`，
> 本轮推进至 `9f21f03`）。RUN-018 台账全部保留，本文件切换为 RUN-019 口径。
>
> 主题：核心资产管理功能完整化、智能诊断升级、前端产品级完整性、
> 完整闭环回归与质量门禁。

## 完成情况

- WP0 差距分析：`docs/RUN-019-GAP-ANALYSIS.md`（六态口径逐项标注）
- WP1 资产管理：Workspace 管理 API（详情/编辑/可信创建者/成员）、用户角色安全
  变更（最后管理员并发保护 tg_admin_guard）、TestIntent 编辑/状态机/审批权限分离、
  TestCase 新版本/版本差异/可视化步骤编辑器、Oracle 详情/历史/审批/反向引用；
  联合验收 SQLite 20/20 + PG16 20/20；`docs/RUN-019-WP1-ASSET-MANAGEMENT.md`
- WP2 诊断升级：PERSISTENCE/TIMING/LOCATOR 证据链与根因规则升级；
  独立新 Holdout（30 变体 × 2 轮，manifest 冻结 SHA-256）；目标类别 Recall 1.0、
  API/NETWORK 无回归、ambiguous 全弃权；RUN-018 原始 Holdout 未改动；
  `docs/RUN-019-WP2-DIAGNOSIS.md`
- WP3 前端完整性：16+ 视图权限门禁（前后端矩阵对齐 repair.regression/
  system.observe）、a11y 修复（对比度/键盘/溢出）→ 77/77、真实浏览器资产
  业务闭环 8/8（认证开启）；`scripts/ui_a11y_run019.py`、
  `scripts/asset_ui_acceptance_run019.py`
- WP4 闭环回归：PG16 full-loop 40/40（含回归验证与回滚）、回归十进程并发
  13/13；`docs/RUN-019-WP4-BUSINESS-LOOP.md`
- WP5 门禁：ruff 全绿；后端全量 1131 passed/16 skipped；PG16 专项 32 passed；
  前端 typecheck/build；a11y 77/77；Reliability soak 62 分钟（PG16）后台执行中
- WP6 文档：进行中

## 遗留与如实记录

- B-26（PR 创建）：NOT_RUN（无 gh CLI/凭据）；本轮分支已推送
  `workbuddy/run019-product-completion` @ `9f21f03`，PR Gate 状态
  BLOCKED（无法核实，同 B-27）。
- Reliability Gate：CI 版本需手动触发（B-25）；本地 62 分钟 soak 替代执行。

禁止：改 main、强推、reset --hard、git clean、自动合并 PR、读取凭据。
