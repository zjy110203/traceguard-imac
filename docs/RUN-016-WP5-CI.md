# RUN-016 WP5：CI、回归与正式交付准备

> 核心原则：**没有实跑就不写 PASS**。本轮无 `gh`、无 GitHub 凭据，
> 因此 GitHub Actions 的结论一律 **NOT_RUN**，只用本地等价门禁 + 工作流静态审计来交付。

---

## 1. CI 证据一致性审计

### 1.1 发现的口径问题

| # | 问题 | 位置 | 风险 |
|---|---|---|---|
| 1 | 汇总表把 `Job 结果` 与 `真实 LLM 证据` 写在同一格（`success / BLOCKED`） | `nightly-gate.yml` gate-verdict | 读者容易理解成"通过" |
| 2 | Release Gate 只校验 Job `conclusion`，**不校验 llm_status** | `release-gate.yml` nightly-attestation | 缺 Secret 时 LLM 证据为 BLOCKED，但 Job 退出码 0 → **Release 可放行**，属证据掩盖 |
| 3 | 报告引用的 SHA 与最终树不一致 | RUN-015 报告（`438c54a` vs HEAD `73e4fe6`） | 无法把 CI 结论对应到被验证的代码 |

### 1.2 修复

1. **`llm-smoke` 增加状态工件**：作业结束写 `llm-status.json`
   （`{"status": "PASSED|BLOCKED", "real_llm_executed": true|false}`）并上传为
   `llm-smoke-status`（`if-no-files-found: error`）。
2. **汇总表拆列**：`| Job | Job 结果 | 真实测试证据 |`，并单独一行文字说明
   "Job 正常结束 ≠ 真实 LLM 测试通过"；`llm_status` 为空视为证据缺失 → 直接失败。
3. **Release Gate 强制下载工件并要求 `PASSED` + `real_llm_executed=true`**，
   BLOCKED 一律 `exit 1`；工件缺失同样失败（fail-closed）。
4. **SHA 口径**：本轮所有报告只引用"实际被验证的那棵树"的 SHA；
   GitHub Actions 未运行时写明 NOT_RUN，不写 PASS。

### 1.3 静态验证

- `python -c "yaml.safe_load(...)"` 对 `pr-gate.yml` / `nightly-gate.yml` / `release-gate.yml`
  全部解析通过；
- 工作流改动未触碰触发条件与权限，只增加证据校验步骤。

## 2. 最新代码门禁（本地等价执行）

| 门禁 | 命令 | 结果 |
|---|---|---|
| 全量 pytest | `TG_TEST_POSTGRES_URL=... pytest -q` | **895 passed / 0 failed / 0 skipped**（142.71s） |
| PostgreSQL 16 专项 | `TG_TEST_POSTGRES_URL=... pytest tests/integration/test_postgres_*.py` | 11 passed |
| Ruff | `ruff check packages apps scripts tests benchmark` | All checks passed |
| 前端类型 | `npx vue-tsc --noEmit` | 通过 |
| 前端构建 | `npm run build` | 通过 |
| UI E2E | `scripts/business_loop_ui_acceptance_run016.py` | 见 `RUN-016-WP4-PRODUCT.md` |
| PG16 长稳 | `scripts/soak_run013.py --minutes 62 --min-minutes 60` | 见 `RUN-016-WP1-SOAK.md` |
| Regression 多进程 | `scripts/regression_multiprocess_run016.py --procs 10` | 见 `RUN-016-WP2-REGRESSION.md` |

**GitHub Actions：NOT_RUN**（本机无 `gh`、无 token；不读取、不使用任何凭据）。

## 3. Release Gate

- 未创建 Tag、未发布版本（未获授权）；
- 工作流已加入"LLM 证据必须 PASSED"硬门禁；
- Benchmark 报告门禁仍要求 `workflow_dispatch` 显式声明 `benchmark_report=true`；
- 结论：**Release Gate = NOT_RUN**（无授权 + 无 CI 凭据）。

## 4. 全量回归

见 §2 与最终报告的"全量测试"章节；失败项逐条列根因，不做"整体 PASS"。

## 5. 未完成 / 阻塞

| 项 | 状态 | 原因 |
|---|---|---|
| PR Gate / Nightly Gate 真实运行 | NOT_RUN | 无 `gh`、无 GitHub 凭据 |
| Release Gate | NOT_RUN | 未授权发布 |
| LLM Smoke 真实执行 | BLOCKED | 不擅自读取或使用凭据 |
| 多 API 实例压测 | NOT_RUN | 单实例部署形态，无第二节点 |
