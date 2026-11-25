# RUN-012 WP1：CI 触发修复与 PostgreSQL 16 真实验收

日期：2026-11-25
分支：`workbuddy/run012-product-completion`（基线 `0ca128f`）

## 1. CI 工作流复核与修复

| 工作流 | 复核结论 | 本轮改动 |
|---|---|---|
| `.github/workflows/pr-gate.yml` | push 分支列表截止 `run008-*`，`run011-*` / `run012-*` 不触发；且格式门禁在基线代码上实际会失败 | 新增 `workbuddy/run011-*`、`workbuddy/run012-*` 触发规则；把 R1.2 审核门禁与全局观测 RBAC 两个安全专项纳入 Fast service integration |
| `.github/workflows/nightly-gate.yml` | PostgreSQL 16 Service Container 已存在；PG 专项只跑 parity + platform | 新增 PG Workspace 隔离 / 权限过滤 / 审计链 / 审核 CAS 专项步骤 |
| `.github/workflows/release-gate.yml` | 依赖 Nightly attestation 的 head_sha，本轮不需要发布 Tag | 不改；不为通过门禁伪造旧 Nightly 证据 |

**格式门禁真实缺陷（CL-R12-01）**：PR Gate 的 `ruff format --check packages apps tests benchmark scripts` 在基线 `0ca128f` 上有 29 个文件未通过（主要为 R1 / R1.1 / R1.2 新增文件），即 CI 一旦在 RUN-011 分支触发就会真实失败。本轮对这 29 个文件执行机械格式化（仅空白/换行，无断言或语义变化），并执行：

- `ruff format --check packages apps tests benchmark scripts` → 218 files already formatted
- `ruff check packages apps tests benchmark scripts` → All checks passed
- PR Gate 后端命令本地复现 → **416 passed**（含 R1.2 两个安全专项）

## 2. PostgreSQL 16 真实验收

此前 R1.2 报告把 PG 标为 BLOCKED（"本机无服务/容器"），本轮复核发现**本机实际有隔离 PG 16.16 实例**（Postgres.app，端口 55432，数据库用户 `traceguard`，无密码，仅本地测试库）。未读取或输出任何真实凭据。

| 项 | 值 |
|---|---|
| 服务端版本 | `16.16 (Postgres.app)`（SQL `SHOW server_version` 实测） |
| 隔离数据库 | `traceguard_run012_wp1`（本轮新建；历史库未触碰） |
| DSN | `postgresql+psycopg://traceguard@127.0.0.1:55432/traceguard_run012_wp1`（测试专用、无密码） |
| `tests/integration/test_postgres_parity.py` | **5 passed** |
| `tests/integration/test_postgres_platform.py` | **8 passed** |
| `tests/integration/test_postgres_workspace_run012.py`（新增） | **3 passed** |
| 合计 | **16 passed / 0 failed / 0 skipped** |

新增 PG 专项覆盖：

1. **Workspace 数据查询与权限过滤**：`ws-a` / `ws-b` 各建用户与 Intent；确认用户只能解析自己的 Workspace scope，过滤查询只返回授权数据，跨空间判定为 false。
2. **审计记录完整性**：连续写入 3 条带 `before/after` 的审计记录，`verify_audit_chain()` 返回 `intact=True`、`broken=[]`。
3. **审核修订号 CAS**：在 PG 上用确定性陈旧修订号模拟并发审核（审核期间推进 `review_revision`），服务层返回 `CONFLICT` / `CONCURRENT_REVIEW_CONFLICT`，未静默覆盖。

既有 PG platform 专项已覆盖：迁移 v9/v10 幂等与并发建库、租约代次与旧 Worker 拒绝、晋升 CAS 单赢家与回执重放、序列不重号；parity 覆盖事件序列、幂等、Inbox 原子领取、DocStore CAS 与 JSON roundtrip。多进程并发由上述用例中的多线程/多连接场景与本地全量 `test_event_concurrency.py` 覆盖。

## 3. GitHub Actions 真实运行

- 本机 `gh` 不存在，环境无 `GH_TOKEN` / `GITHUB_TOKEN`；**不能**通过 API 触发 `workflow_dispatch`。
- 本轮分支推送后，PR Gate 的 push 规则已在代码中修复，是否真实触发取决于远端仓库 Webhook/Actions 配置；在拿到真实 run ID 之前，CI 状态保持 **NOT_RUN**，不得写 PASS。
- 不创建 Release Tag，不合并 PR #1 / PR #2，不修改 main。

## 4. 结论

| 项 | 状态 |
|---|---|
| CI 分支触发规则 + 安全专项入门禁 | IMPLEMENTED（代码与 YAML 校验通过，真实运行待远端证据） |
| PR Gate 格式门禁基线失败 | 已修复（29 个文件机械格式化） |
| PostgreSQL 16 两个既有专项 | **VERIFIED**（本地隔离实例 13 passed） |
| PG Workspace / 权限 / 审计 / 审核 CAS | **VERIFIED**（新增 3 passed） |
| GitHub Actions 真实运行 | **NOT_RUN**（无凭据，未伪造） |
