# RUN-017 基线（WP0）

> 核实时间：2026-11-26 20:00–20:30 CST
> 仓库：`/Users/dlp--test/Desktop/traceguard`（GitHub `zjy110203/traceguard-imac`）
> 原则：**所有结论以本轮实际命令输出为准**；与历史报告冲突时以本文件为准并标注差异。

---

## 1. Git 基线

| 项 | 实际值 | 核实方式 |
|---|---|---|
| remote | `https://github.com/zjy110203/traceguard-imac.git` | `git remote -v` |
| main | `ac2876ecef779b0bed900bdf3b5361ce1d2fa3d6`（"第五轮"） | `git log origin/main -1` |
| RUN-015 交付 | `73e4fe689cdd950334b000cfe45961c9c7adc383` | `origin/workbuddy/run015-execution-consistency` |
| RUN-016 代码 | `844e40f50610c0ad9a9356d0b4ea18ab4f3498ab` | `git log --oneline` |
| RUN-016 文档 | `f18e8716bdc3ec0ea5eab1563ed55379b9315ae3` | `origin/workbuddy/run016-finalization` HEAD |
| 本轮起点 | `f18e871`（RUN-016 分支 HEAD） | `git checkout -b workbuddy/run017-product-maturity` |
| 工作区 | clean（无用户未提交修改） | `git status --short` |

- 本轮分支：`workbuddy/run017-product-maturity`（从 `f18e871` 创建，不覆盖任何历史分支）。
- 未修改 `main`、未强推、未 `reset --hard`、未 `git clean`。
- PR #1 / PR #2 保持 open，未合并、未关闭。

## 2. 运行环境

| 项 | 实际值 | 备注 |
|---|---|---|
| OS | macOS（darwin 25.x，aarch64） | `platform.platform()` |
| Python | **3.13.12**（`.venv`） | 项目声明 `>=3.11,<3.14`，符合 |
| Node / npm | v22.22.2 / 10.9.7 | 前端构建 |
| Playwright | 1.65.2 | `pip show playwright` |
| Chromium | 159.0.8154.6 | `chromium.launch()` 实测可启动 |
| Ruff | 0.17.6（lock 固定） | 与 `requirements.lock.txt` 一致 |

## 3. PostgreSQL

| 项 | 实际值 |
|---|---|
| 版本 | **PostgreSQL 16.16**（`127.0.0.1:55432`，用户 `traceguard`，trust 认证） |
| 数据目录 | `/tmp/traceguard-f2/pgdata`（Postgres.app 16.16 二进制） |
| pg 工具 | `pg_dump/pg_restore/psql` 16.x（`/Volumes/Postgres-2.9.7-16/.../bin` 与 `/private/tmp/pgw/.../bin`） |
| 另一实例 | `::1:55432` 上还有一个 PG **16.2**（`~/.traceguard-pg16/data`） |

**注意（拓扑缺陷）**：`localhost` 解析到 IPv6，会连到 16.2 实例；`127.0.0.1` 才是
RUN-016 使用的 16.16 实例。本轮所有 PG 用例统一显式使用 `127.0.0.1:55432`，
避免"同名端口、不同实例"造成证据串台。该缺陷已记录为技术债（见 §8）。

## 4. 数据库结构版本

- `MIGRATIONS` 共 **12** 个迁移，最高版本 **13**（`run_submission_receipts`）。
- 健康库应有 `tg_schema_migration` 12 行；`doctor.py` 实测 "12 个迁移已应用"。

## 5. 当前核心能力（代码复核）

| 能力 | 状态 | 证据 |
|---|---|---|
| 需求 → 用例编译（规则 + 可选 LLM） | IMPLEMENTED / VERIFIED | `packages/agents`、`tests/unit/test_compile*` |
| Oracle 绑定/审批/版本固定 | VERIFIED | `tests/integration/test_oracle*`、UI E2E |
| 真实浏览器执行（Playwright） | VERIFIED | 长稳 100+ Run、UI E2E |
| 租约 + Fencing + 终态冻结 | VERIFIED | `test_fencing_*`、PG16 专项 |
| Event / Evidence / Artifact 血缘 | VERIFIED | `test_artifact_lifecycle_run016.py` |
| 诊断（规则 + 可选 LLM） | VERIFIED | `diagnosis_service.py`、Benchmark 数据 |
| Evidence Graph / Active Probe | VERIFIED | UI E2E、`graph_service.py` |
| 修复 / 沙箱 / 审批 / 晋升 | VERIFIED | `repair_service.py`、14 项回归测试 |
| 回归验证（独立 Run） | VERIFIED | `test_repair_regression_run01{4,5}.py` |
| 回滚 | VERIFIED | `rollback()` + UI E2E |
| Benchmark 故障效果独立观测 | **PARTIAL → 本轮修复** | 见 §8 缺陷 D3–D6 |
| SSE + 撤权 | VERIFIED | `test_sse_*` |
| RBAC + Workspace 隔离 | VERIFIED | `test_system_config_rbac_run016.py` |

## 6. RUN-016 各工作包验收状态（复核）

| 工作包 | 报告口径 | 本轮复核 |
|---|---|---|
| WP1-A 严格长稳 | 62.14 分钟 / 113 Run / 1 NOTE（SIGSTOP 未触发） | 证据文件在 `docs/assets/run016/`，**本轮不复用**，重新执行 |
| WP1-B Fencing 审查 | DONE | 通过；但 `fenced_session` 重入会自锁（D7） |
| WP1-C Artifact 生命周期 | DONE | 通过；GC 扫描 O(N) 全量加载 + 删除前全量重扫（D8） |
| WP2.1 Regression 多进程 | 13/13 | 通过；全量套件下并发用例曾偶发（D2） |
| WP2.2 INCONCLUSIVE 重试 | 决策稿 | 本轮实现（WP3） |
| WP2.3 Promotion 恢复 | 7 测试 | 通过 |
| WP3 Benchmark 独立观测 | 4 类探针 | **探针机制错配**（D3–D6），本轮重写 |
| WP4 产品/权限/SSE/前端 | 58 PASS | 通过；本轮复跑 |
| WP5 CI 口径 | "CI NOT_RUN" | 复核：**RUN-016 从未触发过任何 workflow**（§7） |
| 质量门禁 | "895 passed" | 实测 `878 passed / 1 failed / 16 skipped`（总数 895） |

> 统计口径修正：RUN-016 报告中的 "895 passed" 实际是**用例总数**；
> 本轮基线实测 878 passed + 1 failed + 16 skipped。多出的 failed 是
> `test_ten_concurrent_regression_requests_create_one_run`（D2）。

## 7. 远端 CI 状态（只读核实）

数据来源：`https://github.com/zjy110203/traceguard-imac/actions`（WebFetch 只读）。

| 事实 | 值 |
|---|---|
| 最近一次 PR Gate | #73 @ `73e4fe6`（RUN-015），success，2026-11-26 |
| 最近一次 Nightly Gate | #25 @ `73e4fe6`，success，2026-11-26 |
| RUN-016 提交（`844e40f` / `f18e871`）的 workflow | **不存在（0 条）** |
| main 的定时 Nightly | #19 / #20 均 **failed**（2026-11-25/26） |

结论：RUN-016 的"CI 缺口"不是"跑了但失败"，而是**从未被触发**——
`pr-gate.yml` / `nightly-gate.yml` 的 push 分支过滤停在 `run015-*`。
本轮 WP1 已改为 `workbuddy/run*` 通配，并新增独立 `reliability-gate.yml`。

## 8. 已知技术债（本轮要修的真实缺陷）

| 编号 | 缺陷 | 影响 | 本轮处理 |
|---|---|---|---|
| D1 | CI push 分支过滤停在 `run015-*` | RUN-016/017 推送不触发门禁 | WP1 修复 + 真实触发 |
| D2 | Regression 并发用例偶发失败：在途 Run 的空 verdict 被写成 `NOT_EVALUATED` | 全量套件不稳定；UI 误显示"已评估" | WP3 修复（在途 verdict 用空串） |
| D3 | `api_error` 探针用空载荷 POST：FastAPI 在路由前返回 422，注入的 500/403 永不执行 | API 故障在真实环境永远测不到 | WP2 改用合法载荷 + 授权基线 |
| D4 | `api_timeout` 只有 `status=0` 才算受影响 | 快速 5xx 被判"无影响" | WP2 纳入 5xx + 基线段 |
| D5 | `persistence` 用 name grep `/api/state/projects`（只返回 keys） | **健康环境也判"未持久化"**，并留下 `r16-probe-*` 数据 | WP2 按 key 回读 + 探针 Workspace + 自清理 |
| D6 | `state_lag` 用响应耗时测"值过期"（TF-DB-003/004 服务端不 sleep） | 滞后型故障永远测不到 | WP2 拆成 `read_latency` 与 `state_consistency` |
| D7 | `fenced_session` 同 Run 嵌套自锁（等自己的行锁） | 误用即永久挂起 | WP4.1 ContextVar 重入检测 |
| D8 | GC `scan()` 全量加载 Evidence/Run 文档；`apply` 每条候选重扫全库 | 10 万级 O(N×M) | WP4.2 keyset 分页 + 一次性快照 + 定向复核 |
| D9 | `TG_ARTIFACT_BACKEND=s3` 被静默忽略（仍写本地目录） | 存储能力误报 | WP4.3 启动即失败 |
| D10 | Ruff `format --check` 在仓库内 5 个文件上失败 | CI 必红 | WP1 修正格式 |
| D11 | PG 双实例共用 55432（IPv4=16.16 / IPv6=16.2） | 证据可能串台 | 本轮统一 `127.0.0.1`，记入部署文档 |
| D12 | 运行态目录（26MB 实验产物）被误提交 | 仓库膨胀 | 已 untrack + gitignore（历史仍含一次，未重写） |

## 9. 当前剩余问题（本轮开始时）

- **BLOCKED**：B-17（Worker 补充故障注入）、宿主级网络隔离、真实外部站点、人工诊断基线、
  LLM 四臂计费实验、Release Gate 真实 LLM 证据。
- **NOT_RUN**：GitHub Actions 在 RUN-016/017 HEAD 上的真实执行；23×2 新口径实验；
  60 分钟以上 PG16 长稳（本轮重跑）。
- **NEEDS_DECISION（本轮已采纳方案 A 并实现）**：INCONCLUSIVE 重试策略。
- 许可：仓库无 LICENSE 文件，本轮不擅自增加。

## 10. 本轮起点命令与结果（可复现）

```bash
git remote -v            # origin https://github.com/zjy110203/traceguard-imac.git
git status --short      # 空
git branch --show-current  # workbuddy/run016-finalization
git fetch origin --prune   # 无新提交
git log --oneline -3    # f18e871 / 844e40f / 73e4fe6
```

```bash
.venv/bin/python -m pytest -q
# 878 passed, 1 failed, 16 skipped（failed = test_ten_concurrent_regression_requests_create_one_run）
```
