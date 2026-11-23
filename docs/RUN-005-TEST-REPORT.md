# RUN-005 测试报告

- 执行时间：2026-11-23
- 命令：`.venv/bin/python -m pytest -q`
- 结果：**285 passed, 5 skipped, 0 failed**（25s 量级）
- 跳过的 5 项全部是 PostgreSQL parity 测试（`TG_TEST_POSTGRES_URL` 未配置），**不是通过**
- Lint：`.venv/bin/ruff check packages apps tests benchmark scripts` → All checks passed

## 1. 测试基线对比

| 轮次 | 用例数 | 结果 |
|---|---|---|
| RUN-004-FE 基线（本机复跑） | 224 | 224 passed |
| RUN-005 本轮 | 290（285 + 5 skip） | 285 passed / 0 failed |

新增 66 项：61 项本地通过，5 项 PG 用例在 CI 执行（本地 skip）。

## 2. 新增专项测试（WP1）

| 文件 | 用例 | 覆盖点 |
|---|---|---|
| `tests/unit/test_probe_budget.py` | 15 | 服务跑 600s 后新 Run 仍有完整 60s 窗口；旧 Run 独立过期；多 Run 计数隔离；成本/次数/时间三种耗尽原因；6 路并发诊断互不影响；**同一预算 20 并发不超发**；关闭后 `PROBE_BUDGET_CLOSED`；快照含剩余量/耗时/provisional；重启后按 scope 恢复且不混用；owner 不匹配拒绝恢复；TIMED_OUT/FAILED 事实保留且计入尝试；审计含 budget_scope |
| `tests/integration/test_probe_service.py` | 6 | 真实 Runtime+DocStore：预算按 Run 隔离并持久化；重启恢复；Active 闭环真实执行并产生证据；确认后立即停止并关闭预算，关闭后再探测被拒；诊断级与 Run 级预算互不干扰；`suggest` 暴露 gap 与 budget_scope |
| `tests/integration/test_sse.py` | 7 | 真实业务事件推送（非 keepalive）；多事件顺序；`Last-Event-ID` 续传不重复；workspace 隔离；run 过滤；无新事件心跳；轮询降级接口仍可用；非法游标从头开始 |
| `tests/unit/test_inbox_persistence.py` | 9 | 成功不可重复；多消费者独立；PROCESSING 租约阻塞；租约过期崩溃恢复（attempts=2）；FAILED 可重试 / UNKNOWN 不可盲目重试；**副作用与消费记录同事务**（失败全回滚、成功同提交）；两独立 Database 并发抢占仅 1 个成功；旧 `PROCESSED` 迁移为 `SUCCEEDED`；状态统计 |
| `tests/integration/test_timeline_projection.py` | 3 | Inbox 驱动的真实消费者投影；重复投影幂等（skipped=3/projected=0）；Run 隔离；`/inbox/stats` 反映真实消费 |
| `tests/unit/test_event_concurrency.py` | 4 | **4 个真实进程** 并发写同一 producer 实例 100 事件：序号 1..100 无重复无丢失；4 进程同键同载荷并发 → 同一 event_id、仅 1 行；同键异载荷 → CONFLICT 留审计且可继续写入；进程内幂等 |

## 3. 新增专项测试（WP2）

| 文件 | 用例 | 覆盖点 |
|---|---|---|
| `tests/unit/test_llm_advisors.py` | 10 | 编译器只接受轨迹中真实存在的定位值（虚构 `ghost-button` 被丢弃并回退规则）；业务断言缺接口/缺来源被丢弃；LLM 报错/无凭据 → 规则兜底且 `fallback=true`；`rule_only` 零模型调用；探针规划丢弃未注册探针/非白名单模板/`DROP TABLE` 参数；修复顾问丢弃 assertion/expected/越界 index，合法路径规范化 |
| `tests/integration/test_stability_replay.py` | 7 | 每次回放独立 Run + Task.mode=sandbox；不稳定（PASS/FAIL/PASS）→ FLAKY_UNSTABLE 不可晋升；STABLE_PASS 但 Oracle 不完整 → 不可晋升且提示"不得宣称业务预期已验证"；Oracle 完整 → promotable 但仍 `requires_human_review`；失败分类 Locator/Business/Environment 各自计数；repeats 越界拒绝；版本不存在 404 |

## 4. 真实运行证据（非单测）

### 4.1 真实浏览器执行（隔离环境）

| 场景 | Run ID | 结果 |
|---|---|---|
| 正常创建项目 | `run_17954090434563a303cab544c` | COMPLETED / **PASSED** / playwright |
| False Pass（UI 成功但未落库） | `run_1795409057169736e550fc256` | COMPLETED / FAILED |
| API 500 | `run_1795409062183d8293ab67adf` | COMPLETED / FAILED |

### 4.2 诊断（规则 + LLM 顾问）

`run_1795409062183d8293ab67adf` → `outcome=CONFIRMED`，`disposition=PRODUCT_DEFECT`，`engine=rule-v1+llm-advisor`；
根因"后端接口返回 5xx（`/api/projects`）"由规则确认；LLM 追加 2 条候选假设（未升级为 CONFIRMED）。

### 4.3 Active Diagnosis 闭环

`POST /api/v1/runs/run_1795409062183d8293ab67adf/probe-plan`：

- steps：`http_state(SUCCEEDED, score 0.5)` → `network_trace(SUCCEEDED, 0.49)`
- stop：`STOP_NO_HIGH_VALUE_PROBE`（已无更高价值缺口）
- budget：`scope=run:…`、`probes_used=2`、`cost_used=1.2`、`time_left=54.5s`、`provisional=true`
- evidence：`evd_1795409168058014b522be2f0`、`evd_17954091706598d2ded79b972`（均关联该 run）
- LLM 规划：`called=true, fallback=false, dropped=0`，给出 network_trace/backend_log/http_state 三个选择与理由

### 4.4 三类 LLM 顾问真实冒烟

`scripts/llm_advisor_smoke.py`（`.workbuddy-run/llm-advisor-smoke.json`）：

| 顾问 | 结果 |
|---|---|
| Compiler | called=true；2 步 + 1 断言；丢弃 1 个非法 navigate；**未凭空生成业务预期**（明确写下"当前未凭空生成业务预期"） |
| Probe Planner | called=true；2 个合法选择，理由与 EIG 齐全 |
| Repair Planner | 首次 `max_tokens=900` 导致 `invalid_json` → 如实 fallback；提高到 1800 后成功产出 3 条受限变更（action_config / 等待策略），全部在允许路径内 |

### 4.5 前端自动化验收

`.venv/bin/python scripts/ui_acceptance.py --base-url http://127.0.0.1:8010` →
**30/30 通过**（`docs/assets/run005/ui-acceptance.json`，截图 18 张）。

覆盖：11 个页面渲染与控制台无错误、Runs 表格/筛选、Run 详情、SSE 状态徽标、事件面板、
错误状态（不存在 Run 有可见提示）、移动端导航展开、深色主题。

### 4.6 Benchmark v2 复验

`TG_API_URL=http://127.0.0.1:8010 scripts/bench_verify.py` → **30/30 场景 verdict 与 GT 一致**（真实浏览器，隔离库）。

## 5. 未执行的测试（不得记为通过）

| 项 | 状态 | 原因 |
|---|---|---|
| PostgreSQL parity（本地） | **BLOCKED** | 本机无 Docker / psql；CI Service Container 已就绪但未在真实仓库运行 |
| 60 分钟长稳 | **BLOCKED** | 本轮未执行；不得用分钟级测试冒充 |
| 4 Worker 故障恢复重跑 | **PARTIAL** | 沿用 RUN-003-E 历史证据，未在本轮版本重跑 |
| 53 项历史 UI 验收脚本化 | **PARTIAL** | 新增 30 项可重复脚本；原 53 项以历史基线保留，未逐项脚本化 |
| Benchmark 多臂对照 | **BLOCKED** | 见 Benchmark 报告 |
