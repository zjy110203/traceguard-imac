# RUN-006 测试报告

- 时间：2026-11-23（GMT+8）
- 环境：macOS / Python 3.13.12（`.venv`）/ Node 22.22.2 / playwright 1.65.2（自带 Chromium）
- 数据库：SQLite（本地）；PostgreSQL 本机不可用 → parity 5 项 skip
- 结论：**367 passed / 5 skipped / 0 failed**，`ruff check` 全绿；真实浏览器 E2E 11 项全通过

> 口径：本报告只统计**本轮实际执行**的结果。skip 一律不计入 passed；未执行的验证（PG、长稳、真实 CI、
> 前端截图验收）在 §5 明确列为未执行，不用历史结果替代。

## 1. 汇总

| 命令 | 结果 | 耗时 |
|---|---|---|
| `.venv/bin/python -m pytest -q` | **367 passed, 5 skipped, 0 failed** | 30.0s |
| `.venv/bin/python -m pytest tests/e2e -q` | **11 passed** | 16.1s |
| `.venv/bin/ruff check packages apps tests` | All checks passed | <1s |
| `ruff format`（仅本轮改动文件） | 已格式化 | — |
| `vue-tsc` / `vite build` | **本轮未执行**（前端零改动，不作为验收证据） | — |

基线对比：`dadb4ae` 时 285 passed / 5 skipped → 本轮 **+82 个采集用例**（62 个测试函数，含参数化）。
skip 的 5 项全部是 `tests/integration/test_postgres_parity.py`（`TG_TEST_POSTGRES_URL` 未配置）。

## 2. 新增测试清单（全部为本轮新增，不允许删改旧断言）

| 文件 | 采集用例 | 覆盖 |
|---|---|---|
| `tests/unit/test_target_policy.py` | 18 | 目标策略：协议相对/绝对越权/端口/内网/凭证/scheme/重定向/落地/网关一致性/与 SecurityPolicy 一致性 |
| `tests/unit/test_probe_budget_failsafe.py` | 9 | 预算读失败、写失败回滚、CAS 冲突重试、跨"进程"不超发、重启恢复、关闭不可绕过、provisional 保留 |
| `tests/integration/test_oracle_binding_gate.py` | 26 | 无 Oracle/不存在/未审批/跨 Workspace/未知断言/10 类非人类主体/4 类人类主体/版本固定/不可重绑/并发/stale CAS |
| `tests/integration/test_repair_atomic_cas.py` | 9 | stale base、并发仅一成功、断言与 Oracle 保持、幂等、冲突不覆盖、回滚校验、Agent 回滚拒绝、沙箱隔离 |
| `tests/integration/test_active_diagnosis_feedback.py` | 11 | CONFIRMED 闭环、反驳与排除、探针失败不是证据、冲突检出、冲突零消耗停止、预算关闭/权限失效、LLM 不得 CONFIRMED（参数化）、LLM 参数未应用的诚实标注 |
| `tests/e2e/test_target_policy_browser.py` | 4 | 真实 Chromium：跨域 fetch 被 abort 且服务端 0 次请求、正常目标可用、302 落地被拒、策略层协议相对拒绝 |

### 修改的既有测试（只允许"更严格"，不允许放宽）

| 文件 | 修改 | 理由 |
|---|---|---|
| `tests/unit/test_persistence.py::test_catalog_versioning_and_review` | 审核前先创建+审批 Oracle 并绑定；新增"缺 Oracle 必须 412"断言 | 门禁变严，测试跟着变严 |
| `tests/integration/test_api_flow.py::test_health_and_catalog_flow` | 增加 4 类非人类主体审核拒绝、无 Oracle 412、绑定后审核 200、版本固定断言 | 同上 |
| `tests/integration/test_api_flow.py::test_oracle_agent_cannot_approve` | 断言从 `412` 改为 `403` | 语义修正：非人类主体是"无权限"而非"前置条件不满足"；并新增 `agent:gpt-4` 用例 |

未删除任何测试、未降低任何阈值、未使用 `pytest.mark.skip` 规避失败。

## 3. 真实环境验证（隔离库 `.traceguard/run006/`）

服务：TeamFlow `127.0.0.1:8011`（`teamflow.db`）+ API `127.0.0.1:8010`（`api.db`），`TG_INLINE_WORKER=0`。
种子：启动时幂等写入 5 个 Intent / 30 个 TestCase / 6 个 Oracle。

| # | 动作 | 结果 |
|---|---|---|
| 1 | `POST /testcases/tcase_tf_create_normal/execute {sync:true}` | `COMPLETED / PASSED / playwright`（真实 Chromium） |
| 2 | 激活 `TF-DB-001` → 执行 `tcase_tf_false_pass` | `FAILED`，`a_biz_count FAILED (countgte1 got=0)`：真实假通过被识破 |
| 3 | `POST /runs/{id}/diagnose` | `CONFIRMED / PRODUCT_DEFECT`，PERSISTENCE 确认，另 3 条 LLM 候选为 SUPPORTED（未被确认） |
| 4 | `POST /runs/{id}/probe-plan?diagnosis_id=…` | `STOP_CONFIRMED`，**0 探针 / 0 预算**（已确认不再消耗） |
| 5 | 清故障 + 重跑正常用例 | `PASSED`，独立状态 `count=1` |
| 6 | INCONCLUSIVE 诊断 + 真实 `http_state` 探针 | 探针真实返回 `count=1` → PERSISTENCE `CONTRADICTED` + `excluded_by`；`evaluation_history` 2 条 |
| 7 | `POST /runs/{id}/graph` | 60 节点 / 81 边；含 2 个 `ProbeExecution` 节点、1 条 `contradicts` 边 |
| 8 | Oracle 门禁（真实 API） | 无绑定 412 / `agent:gpt` 403 / 绑定后 200 且 `oracle_version=1`、`oracle_binding_frozen=true` |

原始 JSON：`docs/assets/run006/`
- `real-env-run-summary.json`（运行汇总）
- `probe-plan-confirmed-no-probe.json`
- `probe-plan-real-feedback.json`
- `diagnosis-after-probe-feedback.json`
- `evidence-graph-with-probe-nodes.json`
- `sha256-key-files.json`（关键文件 SHA-256）

## 4. 覆盖到的关键不变量（本轮）

1. Oracle：必需断言无合法 Oracle → **不可 APPROVED**；版本在审核时固定，历史用例复现不跟随最新版本。
2. 身份：Agent/LLM/System/Service 命名空间不能审核、不能审批 Oracle、不能审批/回滚 Repair。
3. 诊断：`CONFIRMED` 只由确定性规则 + TRUSTED 确认证据产生；LLM 假设永不 CONFIRMED（含 `llm:*` 前缀）。
4. 探针：失败/超时/被拒绝 → 不产生支持或反驳证据；证据冲突 → 停止并转人工。
5. 预算：读失败/写失败 → 拒绝执行（不是静默续跑）；关闭是终态。
6. 修复：晋升/回滚在同一事务完成比较与写入；冲突不覆盖已发布版本。
7. 网络：未授权目标在请求级被 abort（真实浏览器实测服务端 0 次请求）。

## 5. 本轮未执行（不得视为通过）

| 项 | 状态 | 说明 |
|---|---|---|
| PostgreSQL 运行时验证 | **未执行（skip）** | 本机无 Docker/psql；parity 5 项被 skip，不计入 passed |
| 4 Worker / 多进程故障恢复 | **未执行** | 未在本版本重跑真实独立进程实验 |
| 60 分钟长稳 | **未执行** | 无独立时间窗；禁止用短测冒充 |
| API/DB/Worker/SSE 故障注入 | **部分** | 仅有预算与 DB 读写的单测级故障；无服务级注入 |
| CI PR / Nightly / Release 真实运行 | **未执行** | 工作流在库，未触发真实 Actions |
| 前端类型检查/构建/截图验收 | **未执行（本轮）** | 前端零改动；不作为本轮证据 |
| UI-FaultBench 多臂实验 | **未执行（本轮）** | 不引用历史数字 |
| 真实浏览器故障场景下的"探针改变结论" | **PARTIAL** | 见实施报告 §2 的诚实边界 |
