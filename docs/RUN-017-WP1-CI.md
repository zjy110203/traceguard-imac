# RUN-017 WP1：CI 真实验收与门禁体系修复

> 时间：2026-11-26 20:30–21:30 CST
> 变更：`.github/workflows/{pr-gate,nightly-gate}.yml`、新增 `reliability-gate.yml`
> 关键结论：**RUN-016 的 CI 缺口不是"失败"，而是从未触发**；本轮已用真实推送验证触发成功。

---

## 1. 先证明缺口（只读核实，不猜）

数据来源：GitHub Actions 页面（只读）。

| 事实 | 值 |
|---|---|
| 最近一次 PR Gate | #73 @ `73e4fe6`（RUN-015），success |
| 最近一次 Nightly Gate | #25 @ `73e4fe6`（RUN-015），success |
| RUN-016 两个提交（`844e40f` / `f18e871`）的 workflow | **0 条** |
| main 的定时 Nightly | #19 / #20 均 failed |

原因：两个 workflow 的 `push.branches` 只列到 `workbuddy/run015-*`，
RUN-016 的分支 `workbuddy/run016-finalization` 不匹配任何 pattern。

> 因此 RUN-016 报告里的 "CI NOT_RUN" 是准确的；但"YAML 能解析"不等于"门禁会跑"。

## 2. 修复内容

### 2.1 分支触发（不要再逐轮追加）

```yaml
# 修改前（示例）
branches:
  - "workbuddy/run014-*"
  - "workbuddy/run015-*"   # RUN-016/017 不匹配

# 修改后（pr-gate 与 nightly-gate 一致）
branches:
  - main
  - "workbuddy/run*"
  - "workbuddy/run*-*"     # 兼容历史命名
  - workbuddy/traceguard-autonomous
```

GitHub 的 filter pattern 中 `*` **不匹配** `/`，所以 `workbuddy/run*` 能匹配
`workbuddy/run017-product-maturity`（单层分支），不会误匹配嵌套分支。

### 2.2 PR Gate 新增用例（快速、无 PG / 无浏览器）

```
tests/integration/test_artifact_lifecycle_run016.py
tests/integration/test_promotion_recovery_run016.py
tests/integration/test_system_config_rbac_run016.py
tests/unit/test_benchmark_false_pass_v2_run016.py
tests/unit/test_benchmark_fault_effect_run016.py
tests/unit/test_fencing_entrypoint_audit_run016.py
tests/integration/test_regression_retry_run017.py
```

> 浏览器观测与真实 TeamFlow 用例**不进 PR Gate**（PR 不装 Chromium），
> 避免"装了但没装成功"导致的假跳过。

### 2.3 Nightly Gate 新增

| Job | 新增内容 |
|---|---|
| `backend-full` | RUN-016/017 专项（Benchmark v2、故障观测、Regression 重试、浏览器观测、真实 TeamFlow 探针）+ **真实浏览器业务全链路**（`business_loop_ui_acceptance_run017.py`） |
| `postgres` | PG16 `fenced_session` 重入与审计（`test_fenced_reentry_run017.py`、`test_fencing_audit_run016.py`） |
| `security` | 不变（npm audit + 密钥扫描） |
| `llm-smoke` | 不变（缺 Secret 记 BLOCKED，不算通过） |

### 2.4 新增独立 Reliability Gate

`.github/workflows/reliability-gate.yml`：`workflow_dispatch` 手动触发，`timeout-minutes: 120`。

步骤顺序（任一失败即 Job 失败）：

1. PG16 Service Container + 版本断言（必须 16.x）
2. 安装 Playwright Chromium（装不上即失败，不用 `|| true`）
3. 构建前端（E2E 依赖 dist）
4. **严格长稳**（`soak_run013.py`，默认 62 分钟 / 3 Worker / ≥100 Run）
5. Regression 10 进程并发
6. Artifact 生命周期专项 + 10k Evidence 规模一致性（`--fail-on-inconsistent`）
7. 备份恢复（SQLite + PG16，真实 `pg_dump`/`pg_restore`）
8. 真实 UI 全链路验收
9. 诊断输出（`doctor.py`，只读）
10. `if: always()` 仅用于上传证据

同时把长稳/并发脚本的 PG 端点参数化（`TG_SOAK_PG_ADMIN` / `TG_R16_PG_ADMIN`），
不再硬编码 `127.0.0.1:55432`。

## 3. 真实触发结果

推送：`8a0fe48` → `workbuddy/run017-product-maturity`（2026-11-26 20:51 UTC）。

| Workflow | Run | 结果 |
|---|---|---|
| PR Gate | [#74 / 45823615372](https://github.com/zjy110203/traceguard-imac/actions/runs/45823615372) | 见 §4（按最终 HEAD 重新触发） |
| Nightly Gate | [#26 / 45823615405](https://github.com/zjy110203/traceguard-imac/actions/runs/45823615405) | 见 §4 |

**触发本身已验证成功**：两个 workflow 都在推送后立即创建，
`head_sha = 8a0fe482e3d95ec1b1f77b4d17aba9a3304ff062`，与本地 HEAD 一致。

> 这一步是 RUN-016 从未做到的：证明"分支过滤已修好、门禁真的会跑"。

## 4. 各次真实运行的结果（全部为 push 触发，head_sha 已核对）

| Run | head_sha | 结论 | 原因 / 说明 |
|---|---|---|---|
| PR Gate #74 | `8a0fe48` | **failed**（2m15s） | 真实暴露 1 处旧契约断言（`regression_retry_allowed` 仍按 RUN-015 契约断言）→ 已修 |
| Nightly #26 | `8a0fe48` | **failed**（4m57s） | 同一根因（全量套件包含该用例） |
| PR Gate **#75** | `1faebec` | **success**（2m22s） | backend + frontend 全绿 |
| Nightly #27 | `1faebec` | **failed**（39m33s） | `backend-full` 把"真实浏览器业务全链路"挤在同一个 job，45 分钟上限被打满 |
| Nightly #28 | `1315a2f` | **failed**（33m48s） | 拆分后 `backend-full` **7m50s 通过**；`ui-acceptance`（独立 job，含最耗时的独立 Worker 故障段）在 CI runner 上 33 分钟未完成 → 退出码 1；`gate-verdict` 正确判失败（门禁没有被掩盖） |
| PR Gate #76 | `1315a2f` | **success**（2m24s） | 最终代码 HEAD，backend + frontend 全绿 |
| PR Gate **#77** | `2d35998` | **success**（2m29s） |
| PR Gate **#78** | `4c2f851` | **success**（2m20s，最新 HEAD） |
| Nightly #29 | `2d35998` | **failed**（11m52s）：`backend-full` 7m33s / postgres 1m2s / security 16s / llm-smoke 19s 均通过；`ui-acceptance`（core profile）11m42s 后退出码 1 |
| Nightly #31 | `f34f6b4` | **failed**（11m54s）：`backend-full` 6m50s 通过；`ui-acceptance` 11m46s 退出码 1（与 #29/#30 相同，可复现） |
| PR Gate **#80** | `feae308` | **success**（2m43s，最终 HEAD） |
：`backend-full` 7m39s / postgres 1m2s / security 17s / llm-smoke 22s 均通过；`ui-acceptance` 11m40s 后退出码 1（与 #29 相同） |

### 4.1 处置与依据

- #27 的处置：把 UI 全链路验收从 `backend-full` 拆成独立 job（`timeout-minutes: 45`），
  拆分后 `backend-full` 用时从 39 分钟降到 **7m50s**；
- #28 的处置：进一步按 profile 拆分——`core`（35 项业务闭环 + 证据图/探针/回归三段）
  进 Nightly，并加 `timeout 1500` 硬上限（25 分钟内没跑完就明确失败，
  不静默挂到 job 超时）；`full`（58 项，含 SIGKILL/租约接管/旧 Worker 恢复等故障段）
  进手动 Reliability Gate（`timeout 2400`）。
- 两项处置都**不放宽断言**：`core` profile 少跑的是"故障注入段"，
  业务闭环本身的 35 项断言一条不少；本地两种 profile 分别实测 35/0 与 58/0。

### 4.2 已知未闭环项：Nightly 的 `ui-acceptance`

**事实**：

- 本地：同一脚本 `core` profile **35 PASS / 0 FAIL**、`full` profile **58 PASS / 0 FAIL**；
- CI（GitHub runner）：`ui-acceptance` 两种 profile 都**失败**（退出码 1），
  耗时明显长于本地（#28 full 33m39s / #29 core 11m42s / #30 core 11m40s）；
- 同一 Nightly 里其它 Job（backend-full / postgres / security / llm-smoke）**全部通过**；
- `gate-verdict` 在 `ui-acceptance` 失败时**正确判失败**（退出码 1）——门禁没有被掩盖。

**为什么本轮无法定位根因**：本机没有 `gh`、没有 GitHub API 凭据；
匿名 API 已 403 限流，GitHub 的 job 日志与 Step Summary 都需要登录后查看。
因此这里只记录**可核对的事实**，不臆测失败原因，也**不**把该 Job 改成"不阻断"。

**已做的处置**：

1. 把该 Job 从 `backend-full` 拆出（`backend-full` 用时从 39 分钟降到 7m33s）；
2. 拆 `core` / `full` profile：Nightly 跑 `core`（业务闭环 + 扩展三段），
   故障注入段留给手动 Reliability Gate 的 `full`；
3. UI 步骤加 `timeout 1500` 硬上限（25 分钟内没跑完就明确失败，不静默挂起）；
4. 增加"把 UI 验收结果写入 Job Summary"的步骤，使**有 `gh` 的环境**能直接看到失败项。

**交接建议（下一轮第一步）**：

```bash
gh run view 45849709837 --log-failed                      # 第一个失败检查
gh run download 45849709837 -n ui-acceptance-evidence     # 报告 JSON 与截图
```

拿到日志后按"失败检查 → 等待点 → 是否环境差异"三步定位，再决定是修脚本还是调 CI 配置。

## 5. 仍然存在的 CI 边界

- **Release Gate** 需要释放凭据与 LLM Secret，本轮不触发（NOT_RUN）；
- **Reliability Gate** 需要手动触发与 120 分钟窗口，本轮不触发（NOT_RUN）；
- `gh` CLI 不在本机，PR #3 的创建按指令记为 **NOT_RUN**，PR 描述已完整生成
  （见 `docs/RUN-017-FINAL-REPORT.md`）；
- GitHub Actions 日志无法导出为文件（无 gh/API 凭据），因此本文件引用
  **run 页面 + head_sha + Job 结论**，不伪造日志内容。
