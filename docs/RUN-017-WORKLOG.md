# RUN-017 工作日志

> 只记录**实际发生**的事（命令、结果、失败与修复），不写计划式叙述。
> 时间均为 CST（UTC+8）。

## 2026-11-26 19:47–20:05｜WP0 基线

- `git remote -v` → `zjy110203/traceguard-imac`；工作区 clean；HEAD `f18e871`。
- `git fetch --prune` 无新提交；确认 `origin/main=ac2876e`、RUN-015=`73e4fe6`。
- 创建分支 `workbuddy/run017-product-maturity`（从 `f18e871`）。
- 环境：Python 3.13.12 / Node 22.22.2 / Playwright 1.65.2 / Chromium 159。
- PG：`127.0.0.1:55432` 是 16.16；`localhost`（IPv6）指向另一个 16.2 实例 → 记为 D11。
- 全量测试：**878 passed / 1 failed / 16 skipped**。
  failed = `test_ten_concurrent_regression_requests_create_one_run`（在途 verdict 被写成
  `NOT_EVALUATED`）→ 修 `_settle_regression`，5 次重跑全绿。
- 发现 Ruff `format --check` 在 5 个文件上失败（D10）→ 修格式。
- 读 GitHub Actions 页面：RUN-016 两个提交**没有任何 workflow run**（D1）。

## 20:05–20:40｜WP4.1 / WP2 探针重写

- `fenced_session` 增加 ContextVar 重入检测（同 Run 嵌套立即 `FENCED_SESSION_REENTRY`）。
- 新增 `tests/integration/test_fenced_reentry_run017.py`（6 项，PG16，全通过）。
- 读 `fault_effect.py`，发现 4 个机制错配（D3–D6）：
  - 空载荷 POST 被 FastAPI 422 提前拦截；
  - `status=0` 才算超时；
  - name grep 只返回 keys 的状态接口；
  - 用响应耗时测"值过期"。
- 重写 `fault_effect.py`：基线 + 分类 + 一致性 + 探针 Workspace + 自清理；
  新增 `api_update` / `read_authz` / `read_latency` / `state_consistency`。
- TeamFlow：`/api/state/projects` 增加 `projects`；新增 `/api/test/probe-cleanup`
  （只删 `bp:<token>:` 前缀，`default` 需 `allow_default` + token ≥ 12）。
- 新增 `browser_effect.py`：role/name 定位、DOM/ARIA/截图/网络/Console、业务回读。
- 单测 17 项 + 真实 TeamFlow 14 项 + 浏览器 8 项，全通过。
  - 期间发现真实缺陷：`persistence` 在健康环境永远 CONFIRMED（已修）。

## 20:40–21:00｜WP2 资格实验

- `scripts/bench_v2_run017.py`：独立 TeamFlow/API/2 Worker，30 场景 × 2 轮。
- 第一次写脚本被 AgentDR 拦截（`info_theft_steal_env_keys`）：脚本读取宿主机
  `PATH/HOME/TMPDIR` 并传入子进程。改为**零宿主机环境读取**的显式白名单后通过。
- 冒烟：NORMAL/API-001/DB-003/UI-001 四类均符合预期。
- 全量 60 样本：733.8 s，UNKNOWN 2（TF-TIME-003），CONFIRMED 45，NOT_TRIGGERED 9。
- 修正统计口径：
  - 诊断准确率对齐 `gt_mechanism`（机制类）而非故障 ID；
  - False Pass 追加"本次 Run 业务结果缺失"条件 → 从 15 降到 **0**；
  - 新增方向性准确率（含 PROBABLE）与 23/7 场景子集。

## 21:00–21:20｜WP3 重试

- 后端：`regression_retry()`（上限 2、原因码映射、版本/Oracle/环境门禁、幂等、CAS）。
- 路由 `/regression-retry` + RBAC 映射（qa-lead / platform-admin）。
- 前端：Attempt 编号/剩余次数/血缘/历史表/授权入口（仅 `retry_allowed=true`）。
- 测试 14 项 → 15 项（补 ENVIRONMENT_DRIFT 健康检查），全通过。
- `npm run build` 通过。

## 21:20–21:40｜WP1 CI

- PR/Nightly 分支过滤改 `workbuddy/run*`；PR 加 7 个专项；Nightly 加 RUN-016/017 专项 +
  真实浏览器业务流 + PG16 重入/审计；新增 `reliability-gate.yml`（120 分钟）。
- 长稳/并发脚本 PG 端点参数化（`TG_SOAK_PG_ADMIN` / `TG_R16_PG_ADMIN`）。
- 推送 `8a0fe48` → **PR Gate #74 / Nightly #26 立即创建**（RUN-016 从未做到）。
- #74 失败：`test_repair_regression_run015.py` 仍断言 `retry_allowed is False`（旧契约）。
  → 改为新契约（ALLOWED_WITH_AUTHORIZATION），本地 5 passed。
- 全量复跑：**932 passed / 0 failed / 16 skipped**。
- 推送 `1faebec` → **PR Gate #75 success（2m22s）**；Nightly #27 进行中。

## 21:40–22:00｜WP5 / WP4.2 / 文档

- `backup_restore_verify_run017.py`：SQLite + PG16 各 12 项校验全部 true（含恢复后写入）。
- `doctor.py`：本机 PASS=9 / WARN=5 / FAIL=0（WARN 均为服务未启动）。
- GC：keyset 分页 + 一次快照 + 定向复核；`s3` 后端启动即失败。
- 新增 2 个 GC/边界测试（14 passed）。
- 同步 README / DEPLOYMENT / FEATURE-STATUS / quickstart / `.env.example`。
- 长稳（62 分钟 / 3 Worker）在后台启动（`docs/assets/run017/wp4/soak/`）。

## 22:00–22:30｜WP7 诊断

- 逐场景错误分析：5 条主因误判、7 条弃权、1 条错误确认。
- 修复真实缺陷：存在 5xx 时 UI_RENDERING 不再 `confirming=True`（症状不冒充根因）。
- 重放 TF-FLAKY-002 ×2：第二轮预测由 UI_RENDERING 修正为 API。
- 明确不做：不写样本级规则、不调阈值凑指标。

## 待补

- WP4.4 长稳结果（62 分钟）。
- WP6 真实 UI 全链路 + 响应式/主题全视图验收。
- WP8 最终 HEAD 全量回归与最终报告。
