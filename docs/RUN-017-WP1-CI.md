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

## 4. 各 Job 结果与说明

（本节按最终 HEAD 的实际运行结果填写；最终提交见 `RUN-017-WORKLOG.md`。）

| Job | 结论 | 证据 |
|---|---|---|
| PR Gate / backend | 见最终 run | Ruff + format + 单测/契约 + 9 个集成专项 |
| PR Gate / frontend | 见最终 run | `npm ci` + `npm run build` |
| Nightly / backend-full | 见最终 run | 全量 pytest + RUN-016/017 专项 + 真实浏览器业务流 |
| Nightly / postgres | **succeeded**（#26） | PG16.16 Service Container；fencing 重入与审计 |
| Nightly / security | succeeded（#26） | npm audit 无 High/Critical；密钥扫描无命中 |
| Nightly / llm-smoke | BLOCKED（无 Secret） | 明确记录，**不计为通过** |
| Nightly / gate-verdict | 见最终 run | 汇总；LLM BLOCKED 会告警但不冒充通过 |

## 5. 仍然存在的 CI 边界

- **Release Gate** 需要释放凭据与 LLM Secret，本轮不触发（NOT_RUN）；
- **Reliability Gate** 需要手动触发与 120 分钟窗口，本轮不触发（NOT_RUN）；
- `gh` CLI 不在本机，PR #3 的创建按指令记为 **NOT_RUN**，PR 描述已完整生成
  （见 `docs/RUN-017-FINAL-REPORT.md`）；
- GitHub Actions 日志无法导出为文件（无 gh/API 凭据），因此本文件引用
  **run 页面 + head_sha + Job 结论**，不伪造日志内容。
