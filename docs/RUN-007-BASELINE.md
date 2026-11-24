# RUN-007 基线审计（WP0）

- 审计时间：2026-11-23（GMT+8）
- 仓库：`https://github.com/zjy110203/traceguard-imac`
- 指令基线 Commit：`ac2876ecef779b0bed900bdf3b5361ce1d2fa3d6`（提交信息「第五轮」）
- 远程核对：`git fetch origin main` 成功，`origin/main` HEAD = `ac2876e`，**与指令基线严格一致**
- 本轮工作分支：`workbuddy/run007-resume-20261123`（基于 `origin/main` 创建，未 reset / 未强推 / 未删历史）
- 测试基线（本轮开工前实测）：**515 passed / 5 skipped / 0 failed**（`.venv/bin/pytest -q`）

> 状态口径（六种）：**VERIFIED** 有本轮真实运行证据；**IMPLEMENTED** 代码已实现但无本轮运行证据；
> **PARTIAL** 部分完成或证据不完整；**BLOCKED** 外部条件阻塞；**NEEDS_DECISION** 待负责人批准；
> **NOT_IMPLEMENTED** 尚未实现。
>
> 证据等级严格递进，禁止跨级替代：
> `代码存在` → `单元测试通过` → `集成测试通过` → `真实环境运行通过` → `长时间运行通过` → `产品验收通过`。

## 1. 环境事实

| 项 | 值 |
|---|---|
| Python | 3.13（`.venv`，`requirements-dev.txt`） |
| Node / 前端 | Node 20+（`apps/web`，Vue3 + TS + Vite） |
| 浏览器 | Playwright 自带 Chromium（真实执行，非 Mock） |
| 数据库（本地） | SQLite；本轮隔离目录 `.traceguard/run007resume/`、`.traceguard/run007soak/` |
| PostgreSQL | 本机无 Docker / psql → **运行时验证 BLOCKED**（用例已写入 CI PG16 Service Container 作业） |
| LLM | `.env` 已配置 DeepSeek（`TG_LLM_PROVIDER=deepseek`），真实调用发生在场景 A |
| 隔离端口 | TeamFlow `8021` / API `8020` / 长稳 API `8030`；未触碰既有 `.traceguard/traceguard.db` |
| Git 凭据 | **无任何可用凭据**（https 无 keychain 记录、无 SSH key、无 GITHUB_TOKEN）→ push / PR **BLOCKED** |

## 2. 已进入 main 的 RUN-007 产物（本轮禁止重复开发）

| 产物 | 事实 | 本轮动作 |
|---|---|---|
| `docs/assets/run007/e2e-run007.json` | 45 步 E2E，**API 内嵌 Worker** 执行；场景 B `evidence_ids` 为空 | 保留原文件不覆盖；新增独立 Worker 版 `e2e-worker-run007.json` |
| `docs/assets/run007/network-boundary.json` | 出站网络边界实验记录 | 只读引用 |
| `docs/assets/run007/probe-budget-*.json` | 探针预算并发/关闭/被杀三组记录 | 只读引用 |
| `docs/assets/run007/worker-fault-recovery.json` | 旧版故障恢复记录：`killed_after_s` 恒为 0、无被 Run ID、接管验证可被其它 Run 满足 | **新增** `worker-fault-recovery-verified.json`，旧文件保留 |
| `.github/workflows/{pr,nightly,release}-gate.yml` | 三级门禁存在但存在掩盖式写法 | 本轮修复（WP5） |

## 3. 审计发现（本轮修复的真实缺陷）

| # | 缺陷 | 证据 | 处理 |
|---|---|---|---|
| 1 | **`RunPipeline._state_cache` 跨 Run 泄漏**：常驻 Worker 复用同一 pipeline 实例，业务状态断言读到上一个 Run 的 `projects.count`，导致假成功漏检 | 独立 Worker 首次跑 E2E 场景 B 时 `B2 假成功必须被判 FAILED: verdict=PASSED` | 已修：`execute()` 开头 `self._state_cache.clear()`；新增 `tests/unit/test_pipeline_state_cache.py`（2 用例） |
| 2 | **沙箱在 API 进程内执行**：`repair_service` 直接 `rt.execute_run(...)`，绕开独立 Worker，无法证明"队列被 Worker 消费" | 沙箱 Run 的 `lease_owner` 恒为 API Worker | 已修：改为 `sync:false` 入队 + `wait_for_run()` 等待终端状态，记录 `sandbox_executed_by` / `sandbox_fencing_token`；新增 `tests/integration/test_sandbox_worker_queue.py`（2 用例，含无 Worker 时 fail-closed） |
| 3 | **并发启动建表非原子**：API + 多 Worker 同时 `create_schema()` 时 `create_all` 抛 `table ... already exists`；迁移记录 `INSERT` 会撞主键 | 新增 `tests/integration/test_migration_v8.py` 的并发用例复现 | 已修：`create_schema` 重试 + 容忍重复 DDL；迁移每条语句包 `SAVEPOINT`（PG 事务 aborted 兼容）；迁移记录插入容忍主键冲突 |
| 4 | **E2E 证据取错来源**：`report.evidence_ids` 为空被当成"没有证据" | 旧 `e2e-run007.json` 场景 B `evidence_ids: []` | 已修：改为 `GET /runs/{run_id}/evidence` + 证据图节点/边校验（`_verify_graph_binds_evidence`） |

## 4. 本轮范围与不做的事

**做**：WP1 复核、WP2 独立 Worker E2E、WP3 前端产品化验收、WP4-01/02/03 可靠性实验、WP5 CI 门禁修复、WP6 Benchmark v3、WP7 报告与台账。

**不做**（遵循指令）：
- 不重复开发已进 main 的 RUN-007 五项 WP1 逻辑、45 步 E2E、WP4-02/03 脚本原始实现（只修缺陷）；
- 不 `git reset --hard` / `git clean -fd` / `git push --force` / 覆盖数据库 / 删除历史证据 / 强制切分支；
- 不改冻结的安全边界（D-01 认证、P03、P08、P06 保持待确认）；
- 不引入 Redis / Celery / MinIO / S3 / 图数据库；
- 不创建 Release Tag。

## 5. 阻塞与限制（如实披露）

| 项 | 状态 | 说明 |
|---|---|---|
| Git push / PR | **BLOCKED** | 网络已恢复（`git ls-remote` / `git fetch` 均成功），但本机无可用的 https 凭据或 SSH key，`git push` 返回 `could not read Username`；无 `gh` CLI 与 token。本地提交完整保留在 `workbuddy/run007-resume-20261123`。 |
| CI 真实触发（PR Gate / Nightly / Release） | **BLOCKED** | 触发需要仓库写权限与 token；工作流改动已完成并通过 YAML 解析校验，但无本轮运行链接。 |
| PostgreSQL 16 运行时验证 | **BLOCKED（本机）/ 已实现（CI）** | 本机无 Docker/psql；已新增 `tests/integration/test_postgres_platform.py`（6 用例）与 Nightly PG16 作业，本地全部 skip 并显式打印原因。 |
| 进程资源采样（CPU/RSS） | **LIMITED** | 沙箱禁止执行 `ps`（`Operation not permitted`）且 macOS 无 `/proc`；长稳脚本退化为存活探测并标注 `source=liveness_only`，资源指标显式为 `null` 而非伪造 0。 |
