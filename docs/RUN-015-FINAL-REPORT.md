# RUN-015 最终报告：执行一致性最终加固与发布就绪验收

> 分支：`workbuddy/run015-execution-consistency`｜基线：`438c54a4ed689801c24b8cfb62a7496fd7e66d70`（RUN-014 最终 HEAD）
> 任务书：`/Users/dlp--test/Downloads/%E6%8C%87%E4%BB%A419.txt`｜开始：2026-11-26
> 原则：不扩范围、不改 main、不合并 PR #1/#2、不关闭 PR、不强推/reset --hard/git clean、不覆盖历史实验证据。
>
> **当前状态**：WP0~WP7 均已交付；PG16 长稳、PG16 全链路、全量测试、Ruff、前端构建已完成。
> 仅最终 HEAD 的 GitHub Actions run ID 与结论在推送后由 GitHub 公开 API 核验（见 §7）。

---

## 1. 执行摘要

RUN-015 针对 RUN-014 收尾后仍存在的“最后几个正确性缺陷”做了最小范围加固：

1. 把 `TaskService.submit_result()` 从 Python 层 TOCTOU 检查改成同事务原子 fencing；
2. 给真实 `RunPipeline` 中此前漏挂 fence 的 `step.completed` / `assertion.evaluated` 事件补齐守卫；
3. 定位并修复 PostgreSQL 16 行锁等待期间的租约时间失真（先取行锁、后读数据库时钟）；
4. 用数据库唯一认领行 + Repair 文档 CAS 消除 Regression 并发重复 Run；
5. 在读取回归状态时按当前批准版本重算 stale，不改写原始 Run Verdict；
6. 将 INCONCLUSIVE 重试策略明确登记为 `NEEDS_DECISION`，未擅自改状态机；
7. 给 SSE `auth_recheck_seconds` 增加服务端硬上限 `[0.1, 10.0]` 秒与 NaN/Infinity 回落；
8. 将 RUN-014 三个新增安全专项与 RUN-015 三个专项纳入 CI；
9. 补齐 PostgreSQL 16 独立库、3 Worker、60 分钟真实长稳与全链路复验；
10. 增强 Benchmark fault_state 记录能力，同时冻结 RUN-014 历史归档，False Pass 继续如实保留 PARTIAL；
11. 修正 RUN-014 WORKLOG / FINAL REPORT / FEATURE-STATUS / 台账 / 恢复文件 / 证据路径的统计与口径问题。

最终 HEAD：`<<PENDING:FINAL_HEAD>>`（提交信息：`<<PENDING:FINAL_COMMIT_MESSAGE>>`）
推送状态：`<<PENDING:PUSH_STATUS>>`

---

## 2. Git 基线与不变量

| 项 | 结果 |
|---|---|
| 基线 HEAD | `438c54a4ed689801c24b8cfb62a7496fd7e66d70` |
| 本轮分支 | `workbuddy/run015-execution-consistency` |
| `origin/main` | `ac2876ecef779b0bed900bdf3b5361ce1d2fa3d6`，本轮未修改 |
| PR #1 / #2 | 未合并、未关闭、未改变集成关系 |
| 历史实验数据 | RUN-011/012/013 原始数据与 RUN-014 归档未改写 |
| 强制操作 | 未执行强推、`reset --hard`、`git clean`、覆盖用户修改 |

基线核验证据：`docs/RUN-015-BASELINE.md`；RUN-014 最终 PR Gate `45756471627` 在 `438c54a` 上 success。

---

## 3. 最终验收标准逐项结论

| # | 验收标准 | 结论 | 证据 |
|---|---|---|---|
| 1 | `submit_result` 原子 Fencing | **PASS** | `docs/RUN-015-WP1-FENCING.md`；`docs/assets/run015/wp1/fencing-sqlite-raw.txt`；`test_fencing_pg_run015.py` |
| 2 | 所有 Run 业务 Event 均有 Fencing | **PASS** | WP1 §1.2；真实 Pipeline 级测试；`run.completed` 保留 `allow_own_submission` |
| 3 | 租约过期与数据库锁等待竞争 | **PASS** | PG16 6 项专项；先锁后读 DB 时钟；`docs/assets/run015/wp1/fencing-pg16-raw.txt` |
| 4 | Regression 并发只有一个有效 Run | **PASS** | `tg_regression_claim` 唯一认领 + 文档 CAS；10 并发专项 5 passed |
| 5 | Regression Stale 反映正式版本变化 | **PASS** | 读取时按 `catalog.approved_version_number()` 重算；`APPROVED_VERSION_MOVED:2->3` |
| 6 | INCONCLUSIVE 重试策略明确 | **PASS（NEEDS_DECISION）** | `regression_retry_allowed=false`、`regression_retry_decision=NEEDS_DECISION`；未改状态机 |
| 7 | SSE 最大重核验间隔受服务端约束 | **PASS** | `[0.1, 10.0]s` 硬上限、NaN/Infinity 回落；专项 7 passed，影响面 20 passed |
| 8 | RUN-014 三个新专项进入 CI | **PASS** | `.github/workflows/pr-gate.yml`；最终 HEAD 真实 run 见 §7 |
| 9 | PostgreSQL 16 专项通过 | **PASS** | 本地 PG16.16 22 passed；Nightly postgres job 含新增两项 fencing |
| 10 | 60 分钟真实长稳完成并有完整汇总 | **PASS** | 59.71 分钟 / 115 Run / 31 PASS / 0 FAIL；`docs/assets/run015/wp5/soak-r2/soak-60min.json`；首轮证据与 NOTE 见 WP5 报告 |
| 11 | Benchmark 证据增强或保留 PARTIAL | **PASS（PARTIAL 保留）** | 新字段 + 历史 SHA 冻结 + 25 passed；23×2 新一轮 NOT_RUN |
| 12 | 文档与项目台账准确 | **PASS** | `RUN-015-WORKLOG.md`、`FEATURE-STATUS.md`、`TraceGuard-持续开发任务台账.md` 已按真实证据对齐 |
| 13 | WorkBuddy 本地恢复文件指向最新阶段 | **PASS** | `.workbuddy-run/{current-task,progress,resume,phase-status,blockers}.md` 已更新到 RUN-015 收口 |
| 14 | Ruff、TypeScript、Build、全量测试通过 | **PASS** | 828 passed / 16 skipped；Ruff 235 文件 formatted；`vue-tsc + vite build` 通过 |
| 15 | CI 真实证据与最终 HEAD 一致 | `<<PENDING:CI_STATUS>>` | `<<PENDING:CI_EVIDENCE>>` |
| 16 | 外部问题仍如实记录 | **PASS** | §9；B-17、宿主隔离、人工基线、LICENSE 等均未伪装为完成 |

---

## 4. 工作包交付明细

### 4.1 WP0：基线核查（提交 `aa7f67a`）

见 `docs/RUN-015-BASELINE.md`。确认 HEAD/工作区/main/PR Gate 基线，并列出 WP7 需修正的历史口径问题。

### 4.2 WP1：Worker Fencing（提交 `554a566`）

- `fencing.py`：新增 `db_now(session, run_id=...)`；PG 下先 `SELECT ... FOR UPDATE` 取到 Run 行锁，再读 `clock_timestamp()`；`fenced_guard()` 增加 `allow_finalizing`。
- `service.py`：`submit_result()` 在事务内先调用 `fenced_guard(..., allow_own_submission=True, allow_finalizing=True)`，随后 `s.refresh(r)` 并在锁内复查回执与终态；`renew_lease()`/`mark_running()` 使用锁后时钟。
- `pipeline.py`：`step.completed`、`assertion.evaluated` 补 `fence=(self.worker_id, token)`；全仓确认 Run 事件仅由 `_emit()` 发出。
- 关键结论：真实 PG16 实测表明把 `clock_timestamp()` 直接写入条件 UPDATE 的 WHERE 仍会因语句快照在等锁后使用旧值；因此修复采用“先取锁后读时钟”。

### 4.3 WP2：Regression（提交 `868743f`）

- 新增 `RegressionClaimRow` / `tg_regression_claim`；`create_run_once()` 同事务插入 Claim 与 Run；`create_task()` 捕获 `IntegrityError`。
- Repair 文档读写改为 CAS；`_settle_regression()` 重读权威文档并对历史结论去重。
- `regression_status()` 读取时重算 stale；不改原始 Verdict。
- INCONCLUSIVE 重试保持 `NEEDS_DECISION`。

### 4.4 WP3：SSE 安全（提交 `99fcda3`）

- `effective_auth_recheck_seconds()`：非数值/NaN/Infinity 回落 2.0s，有限值收敛到 `[0.1, 10.0]`。
- 响应头 `X-TraceGuard-Auth-Recheck-Seconds` 暴露实际生效值。
- 撤权控制帧语义不变：`SESSION_INVALID` / `SESSION_REBOUND` / `WORKSPACE_REVOKED`。

### 4.5 WP4：CI（提交 `b1291a6`）

- PR Gate 纳入 RUN-014 三专项与 RUN-015 三专项；触发分支新增 `workbuddy/run015-*`。
- Nightly 新增 push 触发；PostgreSQL job 纳入 `test_fencing_pg_run015.py` 与 `scripts/verify_fencing_pg_run014.py`。
- `verify_fencing_pg_run014.py` 支持 `TG_TEST_POSTGRES_URL`，新证据写 `docs/assets/run015/wp4/pg-fencing-ci.json`，不覆盖 RUN-014 历史归档。
- Release Gate 未触发，保持 NOT_RUN。

### 4.6 WP5：PG16 长稳与全链路（提交见最终 `git log`）

- 首轮：`traceguard_run015_soak`，59.61 分钟 / 114 Run / 30 PASS / 1 FAIL / 1 NOTE。
  FAIL 是脚本把接管者写死为指定 replacement 的误判；DB 证明 `r15-soak-w3` 合法接管，
  `fencing_token=2`，Run COMPLETED/PASSED。原始 FAIL 保留。
- 复验轮：`traceguard_run015_soak_r2`，59.71 分钟 / 115 Run / **31 PASS / 0 FAIL / 2 NOTE**；
  暂停旧 Worker 后由 `r13-soak-w2` 接管，SIGCONT 后终态和证据数不变。
- PG16 全链路：`traceguard_run015_full_loop` 上 40/40 PASS，15 项血缘 ID 可回读。
- 详见 `docs/RUN-015-WP5-PG16-SOAK.md`。

### 4.7 WP6：Benchmark（提交 `3fc9154`）

- `scripts/bench_pilot_run011_r1.py` 新增真实故障激活/效果时间戳、observational `fault_effect_status`、`evidence_refs` 等字段；Ground Truth 只用于评测端，不进入 Prompt。
- `scripts/bench_v5_run014.py` 新增 `--out`，默认仍指向 RUN-014 路径；新一轮可写 RUN-015 独立目录。
- `tests/unit/test_benchmark_fault_state_run015.py` 冻结 RUN-014 两个归档 SHA-256；专项 25 passed。
- 新一轮 23×2 因与 60 分钟长稳资源隔离要求冲突而未执行；False Pass 保持 **PARTIAL**。

### 4.8 WP7：文档与恢复状态（提交 `137433a` 及最终收口提交）

- 修正 RUN-014 WORKLOG 24/92 行矛盾、FINAL REPORT §13、FEATURE-STATUS「26 passed」无证据问题与血缘 ID 14→15。
- 台账按时间顺序补齐 RUN-012/013/014/015；`.workbuddy-run/*` 实际更新到 RUN-015。
- `docs/assets/run014/wp4/full-loop.json` 截图改仓库相对路径，13 张均存在。
- 最终统计、CI、长稳、恢复状态与本报告在收口提交中一次性对齐。

---

## 5. 测试与证据索引

| 类别 | 结果 | 原始证据 |
|---|---|---|
| WP1 SQLite fencing | 10 passed | `docs/assets/run015/wp1/fencing-sqlite-raw.txt` |
| WP1 PG16 fencing | 6 passed | `docs/assets/run015/wp1/fencing-pg16-raw.txt` |
| WP2 Regression 专项 | 5 passed（timeout 修复后重跑） | `docs/assets/run015/wp2/regression-run015-raw.txt` |
| WP3 SSE 专项 | 7 passed | `docs/assets/run015/wp3/sse-recheck-raw.txt` |
| WP3 影响面 | 20 passed | `docs/assets/run015/wp3/sse-impact-raw.txt`（如存在） |
| WP4 本地等价快检 | 114 passed | `docs/assets/run015/wp4/pr-gate-local-raw.txt` |
| WP4 PG16 fencing 契约 | 9/9 | `docs/assets/run015/wp4/pg-fencing-ci.json` |
| WP6 Benchmark 契约与归档冻结 | 25 passed | `docs/assets/run015/wp6/fault-state-tests-raw.txt` |
| PG16 本轮专项全集 | 22 passed | `docs/assets/run015/final/pg16-suite-raw.txt` |
| Ruff / format | PASS | `docs/assets/run015/final/ruff-raw.txt` |
| TypeScript / Build | PASS | `docs/assets/run015/final/frontend-build-raw.txt` |
| 全量 pytest | **828 passed / 16 skipped / 0 failed** | `docs/assets/run015/final/full-pytest-raw.txt`（首次失败证据另存 `full-pytest-first-raw.txt`） |
| 60 分钟长稳 | **59.71 分钟 / 115 Run / 31 PASS / 0 FAIL** | `docs/assets/run015/wp5/soak-r2/soak-60min.json`、`soak-samples.jsonl`、日志 |
| PG16 全链路 | **40/40 PASS** | `docs/assets/run015/wp5/full-loop/full-loop.json`、`full-loop-run.log` |

---

## 6. PostgreSQL 16 长稳与全链路

完整细节见 `docs/RUN-015-WP5-PG16-SOAK.md`。结论摘要：

| 实验 | 环境 | 关键结果 |
|---|---|---|
| 长稳首轮 | `traceguard_run015_soak`，3 Worker | 59.61 分钟 / 114 Run / 30 PASS / 1 FAIL / 1 NOTE；FAIL 为观测断言误判，已保留原始证据 |
| 长稳复验 | `traceguard_run015_soak_r2`，3 Worker | 59.71 分钟 / 115 Run / 31 PASS / 0 FAIL / 2 NOTE；租约接管与旧 Worker 恢复 PASS |
| 全链路 | `traceguard_run015_full_loop`，2 Worker | 40/40 PASS；Regression 正向/失败、回滚、Evidence 回读、Workspace 授权、独立 Worker 均通过 |

两轮长稳均为本轮独立 PG16 库，不引用 RUN-013/014 历史长稳。SIGKILL 故障覆盖由首轮
`kill_w1` PASS 与首轮 `kill_w2` 的数据库证据提供；复验轮 NOTE 明确写出观察窗跳过，不伪装通过。

---

## 7. CI 真实运行证据（最终 HEAD）

`<<PENDING:CI_DETAIL_SECTION>>`

---

## 8. Benchmark 与 AI 边界

- RUN-014 23×2 历史归档冻结，不修改 SHA-256；False Pass 仍为 PARTIAL / INFERRED_ONLY。
- 本轮已增强下一次实验的记录字段，但没有用 schema 测试冒充完整 23×2 实验；新一轮 NOT_RUN。
- RUN-014 `n=32` 规则组与 LLM 结果保留为历史回归基线；人工构造语料不能外推为通用能力。
- 无人工标注，不伪造人工基线；未获计费授权，不运行 LLM 四臂成本实验；Probe Planner 生产默认仍关闭。

---

## 9. 未解决项与诚实边界

| 项 | 状态 | 说明 |
|---|---|---|
| B-17 补充故障矩阵 | **BLOCKED** | 安全批准未获；未重试、未绕过、未改安全策略 |
| 宿主级网络隔离 / 外部真实站点 | **BLOCKED** | 无容器/pf 权限与授权 |
| 真实人工诊断基线 | **BLOCKED** | 无真人评审，不用 Mock 补齐 |
| LLM 四臂成本实验 | **NOT_RUN** | 无额外计费授权 |
| Benchmark 23×2 新一轮 | **NOT_RUN** | 与长稳资源隔离冲突；False Pass 保持 PARTIAL |
| INCONCLUSIVE 重试 | **NEEDS_DECISION** | `regression_retry_allowed=false`，未改状态机 |
| PROMOTION_FAILED 重发入口 / `/system/config` 权限边界 | **NEEDS_DECISION** | 未擅自扩展产品范围 |
| P03 / P08 | **NEEDS_DECISION** | 路线图未冻结，未擅自改契约 |
| Release Gate | **NOT_RUN** | 需 workflow_dispatch + 凭据；本轮不创建 Tag |
| LICENSE | **NEEDS_DECISION** | 待负责人选定条款 |
| PR #1 / #2 集成策略 | **NEEDS_DECISION** | 未合并、未关闭、未改变相对关系 |
| 生产就绪 | **NOT READY** | 局部通过不等于生产就绪；外部问题未解除 |

---

## 10. 复现命令

```bash
# 专项
.venv/bin/pytest -q tests/integration/test_worker_fencing_run015.py tests/integration/test_worker_fencing_run014.py
TG_TEST_POSTGRES_URL='postgresql+psycopg://traceguard@127.0.0.1:55432/<db>' \
  .venv/bin/pytest -q tests/integration/test_fencing_pg_run015.py
.venv/bin/pytest -q tests/integration/test_repair_regression_run015.py
.venv/bin/pytest -q tests/integration/test_sse_recheck_limit_run015.py

# 全量与静态
.venv/bin/pytest -q
.venv/bin/ruff check . && .venv/bin/ruff format --check .
(cd apps/web && npm run build)

# PG16 全链路（独立库）
.venv/bin/python scripts/full_loop_run014.py \
  --out docs/assets/run015/wp5/full-loop \
  --db-url 'postgresql+psycopg://traceguard@127.0.0.1:55432/traceguard_run015_full_loop'
```

---

## 11. 最终结论

`<<PENDING:FINAL_CONCLUSION>>`
