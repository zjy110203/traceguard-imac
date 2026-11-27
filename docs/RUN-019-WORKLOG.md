# RUN-019 WORKLOG（2026-11-27，产品完整性收口）

> 分支 `workbuddy/run019-product-completion`，基线 = RUN-018-R2 HEAD `05a3639`。
> 提交序列：f5946d5 → 76eca7a → 60a2e5a → ba37662 → 8feed81 → fee7a87 →
> 714b679 → 9f21f03 → 1b39d29。

## WP0：基线核查与功能差距分析

- fetch 远端，确认 HEAD `05a3639` 与指令一致；从 RUN-018 分支创建 RUN-019 分支。
- 读取 RUN-018 全部专题报告与台账，结合源码逐项建立
  `docs/RUN-019-GAP-ANALYSIS.md`（VERIFIED/IMPLEMENTED/PARTIAL/BLOCKED/
  NEEDS_DECISION/NOT_IMPLEMENTED 六态口径）。

## WP1：核心资产管理功能完整化

后端：
- Workspace：详情/编辑 API、服务端可信创建者（不采信请求体）、成员列表、
  默认工作区保护。
- 用户与角色：角色/scope 变更 API、`tg_admin_guard` 单行锁靶 +
  PG `FOR UPDATE`/SQLite 写锁，防"最后一个管理员"并发绕过；审计链 verify。
- TestIntent：PATCH 编辑（版本递增、回到 DRAFT、来源保留、被引用时拒绝）、
  状态机（DRAFT→REVIEW→APPROVED→DEPRECATED）、submit/approve 独立端点 +
  权限映射（qa-engineer 可提交，仅 qa-lead+ 可审批）。
- TestCase：POST /versions（不可变版本递增）、GET /versions、版本差异 API；
  Oracle：详情/历史版本/审批记录/反向引用（testcases 绑定到版本）。
- 专项测试：`test_workspace_roles_run019.py`、`test_intent_lifecycle_run019.py`、
  `test_asset_versions_run019.py`。

前端：
- 新增 `WorkspacesView`、`OracleDetailView`；路由/导航/全局搜索接入。
- `TestCasesView`：可视化步骤编辑器（StepEditor：动作/定位策略/目标/输入值/
  上移下移/删除 + JSON 高级模式）替代裸 JSON 文本框。
- `TestCaseDetailView`：版本列表/回看、版本差异视图、新建版本弹窗。
- `IntentsView`：编辑弹窗、按状态流转按钮（提交评审/审批/废弃）。
- 联合验收：`scripts/wp1_business_loop_run019.py`（真实 API + 真实 DB + 真实
  TeamFlow）：SQLite 20/20、PG16 20/20（双证据保留）。

## WP2：智能诊断能力升级

- `diagnosis_service.py`：PERSISTENCE/TIMING/LOCATOR 证据链细化——
  持久化失败需权威状态证据支持、TIMING 分档阈值、LOCATOR 判定要求 DOM 快照
  或业务状态证据（避免误报）；根因描述携带步骤级细节。
- 专项单测 `test_diagnosis_evidence_chain_run019.py`；RUN-018 诊断回归全绿。
- 独立新 Holdout：`scripts/holdout_benchmark_run019.py`，30 变体 × 2 轮，
  manifest（`docs/assets/run019/wp2/holdout-manifest.json`）冻结规则文件
  SHA-256 与样本清单；RUN-018 原始 Holdout 与 manifest 未改动。
- 结果：PERSISTENCE/TIMING/LOCATOR Recall 1.0；API/NETWORK 无回归；
  ambiguous 全弃权（诚实口径记录于 `docs/RUN-019-WP2-DIAGNOSIS.md`）。

## WP3：前端产品级完整性

- 权限门禁真实化：Repairs/Monitoring/DiagnosisDetail/TestCases/Oracles/
  Intents/Workspaces 视图按钮按会话角色禁用；审核主体取会话用户。
- 前后端权限矩阵对齐（补 `repair.regression`、`system.observe`）。
- a11y：`scripts/ui_a11y_run019.py` → 19 视图 × 2 主题 × 2 断点 = 77/77。
  修复 3 类违规（键盘焦点/对比度/390px 溢出）。
- `scripts/asset_ui_acceptance_run019.py`：认证开启 + Cookie 会话的真实浏览器
  资产业务闭环 8/8；发现并修复 `list_users` roles 字符串化缺陷。

## WP4：完整业务闭环回归（PG16）

- `full_loop_run014.py` @ PG16：首轮 32/33 → 定位前端权限矩阵缺漏（WP3 已修）
  → 复跑 **40/40**（含回归验证 PASSED、故障注入 FAILED 不伪造、两次回滚）。
- `wp1_business_loop_run019.py` 支持 `--db-url`：PG16 20/20。
- `regression_multiprocess_run016.py` @ PG16 十进程：13/13。
- 证据：`docs/assets/run019/wp4/`（截图、full-loop.json、regression-multiprocess.json）。

## WP5：质量门禁

- ruff check/format 全仓通过（5 个例外为历史文档 md，非本轮代码）。
- 后端全量 pytest：**1131 passed / 16 skipped**（skipped 均为本地无 PG 触发的
  PG 专项，单独执行）。
- PG16 专项：parity/platform/workspace/fencing×3 = **32 passed**。
- 前端：TS strict typecheck + vite build 通过。
- Reliability：62 分钟真实 soak（PG16 + 2 独立 Worker + 真实故障注入）后台执行，
  结果落 `docs/assets/run019/wp5/soak/`。
- CI：分支已推送 `9f21f03`+；PR Gate/Nightly 状态 BLOCKED（无 gh/凭据，同 B-26/B-27）。

## WP6：文档与台账

- README、FEATURE-STATUS（新增 RUN-019 段）、`docs/RUN-019-WP{1..4}-*.md`、
  `.workbuddy-run/{current-task,phase-status,progress,blockers}.md`。
- RUN-018 历史报告与台账未删除。
