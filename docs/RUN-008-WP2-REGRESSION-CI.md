# RUN-008 WP2：完整回归与真实 GitHub Actions 验证

- 分支：`workbuddy/run008-quality-hardening`，验证提交：`1f8df24`
- 本文件只记录**真实执行**结果；原始日志位于 `logs/run008/`。
- 本工作包不读取任何凭据内容；CI 触发仅使用既有 git credential store，请求只发往 GitHub 官方 API。

## 1. 本机回归（2026-11-24）

| 项目 | 命令 | 结果 | 原始日志 |
|---|---|---|---|
| 全量 pytest | `.venv/bin/python -m pytest -q` | **562 passed / 13 skipped / 12 warnings / 45.01s** | `logs/run008/pytest-full.log` |
| 真实浏览器 E2E | `.venv/bin/python -m pytest tests/e2e -q` | **12 passed / 18.55s** | `logs/run008/pytest-e2e.log` |
| Ruff lint | `ruff check packages apps tests scripts benchmark` | All checks passed | `logs/run008/ruff-check.log` |
| Ruff format | `ruff format --check ...` | 169 files already formatted | `logs/run008/ruff-format.log` |
| 前端 typecheck | `npm run typecheck`（apps/web） | 通过，无错误 | `logs/run008/web-typecheck.log` |
| 前端构建 | `npm run build` | 2198 modules transformed，built in 2.12s | `logs/run008/web-build.log` |

13 项 skip 均为本机无 PostgreSQL 的 parity 用例，不写作 passed。

## 2. CI 配置变更（提交 `1f8df24`）

`.github/workflows/pr-gate.yml`：
1. 分支触发白名单新增 `workbuddy/run008-*`，使本轮分支能触发 PR Gate；
2. 新增回归测试文件 `tests/integration/test_soak_run007_guards.py` 进入执行清单。

未修改门禁判定逻辑、未放宽任何断言。

## 3. 真实 GitHub Actions 运行结果

| 门禁 | run ID | 结论 | 关键 Job 实测 |
|---|---|---|---|
| PR Gate | `45331287430` | **success** | backend：283 passed + 49 passed；frontend：build 2.40s |
| Nightly Gate（手动 dispatch） | `45331346274` | **success** | backend-full：562 passed / 13 skipped + 35 passed；postgres（postgres:16 service）：5 passed + 8 passed；security：3 moderate（无 high/critical）；LLM smoke：**BLOCKED（缺 Secret）** |
| Release Gate（错误输入 `benchmark_report=false`） | `45331793408`（commit `1f8df24`） | **failure（正确行为）** | `release-evidence` job 执行 fail-closed：仓库存在 Benchmark 报告却声明 false → 拒绝放行 |
| PR Gate（最终代码） | `45335765297`（commit `40efee6`） | **success** | backend 288 passed + 49 passed；frontend build 3.81s |
| Nightly Gate（最终代码） | `45335777381`（commit `40efee6`） | **success** | backend-full 567 passed / 13 skipped + 35 passed；postgres 16.16：5+8 passed；security high=0/critical=0/moderate=3；LLM smoke BLOCKED（缺 Secret） |
| Release Gate（正确输入 `benchmark_report=true`） | `45336007166`（commit `40efee6`） | **success** | nightly-attestation 严格校验 head_sha 一致；frontend / full-verification / release-evidence 全 success |

原始 Job 日志：`logs/run008/actions-<run-id>-<job>.log`（从 GitHub Actions API 下载，
下载过程未把任何凭据写入日志或仓库）。一次错误输入的 Release Gate run `45335952987`
已主动取消，避免与正确配置的那次混淆。

### 重要披露

- Nightly 的 LLM smoke 是 **BLOCKED（缺 Secret）**，门禁日志明确写「该证据不得用于 Release 放行」。
  本轮不把它计为通过，也不据此声称 LLM 能力已验证。
- security job 仍有 3 个 moderate 漏洞；未做 `--force` 升级，保持既有风险登记。
- PostgreSQL 6 个平台用例在 **CI 的 postgres:16 上真实通过**；本机 skip 不改变这一结论。
- PR Gate 与本轮此前在 Ubuntu 上失败的 `backend-full` 现已在 Nightly 全绿，问题已消除。

## 4. 未解决

- LLM smoke BLOCKED、3 moderate 依赖漏洞：保持 BLOCKED / 已知风险，不伪造通过。
- 错误输入的 Release Gate 那次失败是 fail-closed 正确行为，不是产品缺陷；已取消重复的误触发 run。
