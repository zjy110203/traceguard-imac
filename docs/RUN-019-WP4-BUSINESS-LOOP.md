# RUN-019 WP4：完整业务闭环回归（真实 API + PostgreSQL 16 + 独立 Worker + Chromium + TeamFlow）

> 分支 `workbuddy/run019-product-completion`，本专题 HEAD `9f21f03`。
> 执行时间：2026-11-27（本地 iMac，PG16 @ 127.0.0.1:55432）。

## 1. 完整链路回归（RUN-014 全血缘脚本 @ PG16）

`scripts/full_loop_run014.py --db-url postgresql+psycopg://traceguard@127.0.0.1:55432/traceguard_run019_full_loop`

- **结果：40/40 PASS**（`docs/assets/run019/wp4/full-loop/full-loop.json`）
- 覆盖链路：Intent（UI 创建）→ Explorer 真实探索 → 目标策略网关审计 → Compiler 候选
  → Oracle 绑定（版本固定）→ Stability Replay 门禁（未回放时审核被真实拒绝）→
  人工审核 APPROVED → 独立 Worker 执行 Run（fencing=1）→ 注入真实故障 FAILED →
  诊断 → Active Probe → 证据回灌再诊断 CONFIRMED → 修复提案规则校验 →
  沙箱 PASSED → 人工审批 → 晋升 PROMOTED（CAS）→ 回归验证 PASSED →
  回滚 ROLLED_BACK（回归结论失效）→ 负向：故障注入后回归 FAILED 不伪造通过 +
  回滚 → 未审批 Oracle 门禁回读。
- 执行证据：13 张真实截图 + 双 Worker 日志。

### 过程中发现并修复的问题

1. **首轮 32/33**：`运行回归验证` 按钮被禁用。根因是 WP3 给按钮加权限门禁
   `can('repair.regression')` 后暴露前端权限矩阵缺漏——前端
   `ROLE_PERMISSIONS` 与后端 `PERMISSIONS` 不同构（缺 `repair.regression`、
   `system.observe`）。已对齐修复（`apps/web/src/auth.ts`）。
2. 该修复后复跑：**40/40 PASS**。

## 2. WP1 资产闭环（双数据库口径）

`scripts/wp1_business_loop_run019.py`（新增 `--db-url` 支持）：

| 数据库 | 结果 | 证据 |
|---|---|---|
| SQLite（隔离临时库） | **20/20 PASS** | `docs/assets/run019/wp1/wp1-loop-sqlite.json` |
| PostgreSQL 16（traceguard_run019_wp1_loop） | **20/20 PASS** | `docs/assets/run019/wp1/wp1-loop-pg16.json` |

覆盖：管理员登录 → Workspace 创建（服务端可信创建者）→ 用户创建/角色分配/
Workspace 分配 → 需求创建/编辑/评审/审批 → 真实探索 → 编译候选 → TestCase 创建
→ 新版本 → Oracle 创建/审批 → 绑定 → 人工审核 → 稳定性回放 → Run 执行（版本引用固定）。

## 3. 回归并发唯一性（RUN-016 十进程 @ PG16）

`scripts/regression_multiprocess_run016.py --procs 10`
（`TG_REGRESSION_PG_DB=traceguard_run019_regression`）

- **结果：13/13 PASS**（`docs/assets/run019/wp4/regression-multiprocess.json`）
- 10 个独立 OS 进程 Barrier 同步竞争同一 PROMOTED 提案的回归创建：唯一 Claim、
  唯一 Run、唯一 History、无重复执行、Runtime 重启恢复、失败请求不破坏赢家状态。

## 4. 与指令要求的覆盖对照

| 要求 | 状态 | 证据 |
|---|---|---|
| 真实 API | DONE | 全部步骤 HTTP 真实调用 |
| PostgreSQL 16 | DONE | 三条独立链路均落真实 PG16 |
| Chromium | DONE | full-loop 13 张截图 + UI 点击 |
| 独立 Worker | DONE | 2 个独立 Worker 进程（fencing 生效） |
| TeamFlow | DONE | 真实 TeamFlow 进程 + 故障注入 |
| 多 Workspace | DONE | WP1 闭环 + `test_workspace_roles_run019.py` |
| 角色与权限撤销 | DONE | WP1 闭环角色变更 + RBAC 回归 37 项 |
| 版本更新/历史引用固定 | DONE | Oracle 版本审批固定 + Run 版本引用（⑬/⑮） |
| 修复回归 | DONE | ㉕-㉛（回归 PASSED/FAILED/回滚） |
