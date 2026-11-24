# RUN-007 实施报告（续跑：WP0–WP6）

- 时间：2026-11-23（GMT+8）
- 分支：`workbuddy/run007-resume-20261123`（基于 `origin/main` `ac2876e`）
- 起点：515 passed / 5 skipped；本轮终点见 `docs/RUN-007-TEST-REPORT.md`
- 证据目录：`docs/assets/run007/`
- 口径：本报告只写**实际做了并留下证据**的事；未做/未验证的显式标 BLOCKED / PARTIAL。

---

## 1. WP1：五项核心修复复核

复核方式：逐项阅读实现 + 运行专项测试（**163 passed / 0 failed**，12.5s）+ 迁移 v8 幂等与旧库升级实测。

| 项 | 修复点 | 专项测试 | 结果 |
|---|---|---|---|
| WP1-01 | F08 稳定性回放接入正式审核门禁（Agent 候选无回放 → 412，不得 APPROVED；旧版本回放不得复用） | `tests/integration/test_stability_replay.py`、`test_stability_gate.py` | **VERIFIED** |
| WP1-02 | Repair 晋升/回滚幂等回执 + 崩溃恢复（回执与版本指针同事务） | `tests/integration/test_repair_crash_recovery.py`、`test_repair_atomic_cas.py` | **VERIFIED** |
| WP1-03 | 审批主体校验统一（`agent:*` / `llm:*` / `system:*` 等参数化非法主体一律拒绝） | `tests/integration/test_actor_validation.py` | **VERIFIED** |
| WP1-04 | ProbeBudget 跨**真实 OS 进程**并发安全（脚本驱动，不是线程模拟） | `tests/integration/test_probe_budget_multiprocess.py` | **VERIFIED** |
| WP1-05 | 目标策略统一（DNS rebinding / 私网解析 / 逐跳重定向 / 顶层 302 阻断） | `tests/unit/test_target_policy_dns.py` + `tests/e2e/test_target_policy_browser.py` | **VERIFIED** |

### 1.1 数据库迁移 v8 幂等与旧版本兼容（本轮补测）

审计发现此前**没有任何迁移测试**。新增 `tests/integration/test_migration_v8.py`（4 用例）：

| 场景 | 断言 | 结果 |
|---|---|---|
| 全新库 | 迁移 2–8 恰好各一次，v8 `tg_promotion_receipt` 字段齐全 | PASS |
| 同实例 / 新实例重复 `create_schema()` | 迁移记录不重复 | PASS |
| 模拟"刚升到 v7 的旧库"（删 v8 表 + 删 v8 记录） | 启动后自动补 v8，既有 `tg_testcase` 数据不丢 | PASS |
| 两实例并发启动建表 | 不崩溃、迁移记录仍唯一 | PASS（修复前**失败**，见 §1.2） |

### 1.2 复核中发现并修复的真实缺陷（本轮新增）

**缺陷 A：并发启动建表非原子。**
`Database.create_schema()` 直接调用 `Base.metadata.create_all()`，其"先查后建"不是原子的。
API + 多个 Worker 同时指向同一个库启动时，会抛 `table tg_testcase_version already exists`，
迁移记录的 `INSERT` 也可能撞主键 —— 表现为**启动期随机崩溃**。

修复（`packages/persistence/traceguard_persistence/db.py`）：
- `create_all` 对"已存在"类错误重试 6 次（指数小退避），其余错误照常抛出；
- 每条迁移 DDL 包在 `SAVEPOINT`（`conn.begin_nested()`）内：PostgreSQL 下单条语句失败会让整个事务 aborted，必须回滚到保存点才能继续后续迁移；
- 迁移记录插入容忍主键冲突（并发下另一个进程已写入同一 version）。

**缺陷 B：`RunPipeline._state_cache` 跨 Run 泄漏。**
常驻 Worker 复用同一个 pipeline 实例，业务状态断言读到上一个 Run 的 `projects.count`，
导致"假成功漏检"（场景 B 的假通过第一次跑成 PASSED）。
修复：`execute()` 开头 `self._state_cache.clear()`，状态缓存生存期严格等于一次 Run；
新增 `tests/unit/test_pipeline_state_cache.py`（2 用例）。

**缺陷 C：沙箱在 API 进程内执行。**
`repair_service` 直接 `rt.execute_run(...)`，绕开队列，无法证明"Worker 消费队列"。
修复：改为 `sync:false` 入队 + `wait_for_run()` 只等待不执行；无 Worker 时 **fail-closed**
（`PRECONDITION_FAILED`，Run 保持 QUEUED）；成功后记录 `sandbox_executed_by` / `sandbox_fencing_token`；
新增 `tests/integration/test_sandbox_worker_queue.py`（2 用例）。

---

## 2. WP2：E2E 改造为「独立 Worker 消费队列」

`scripts/e2e_run007.py` 重写执行器层：

| 改造点 | 实现 |
|---|---|
| 不再内嵌执行 | 所有 Run 用 `{"sync": false}` 入队；API 以 `TG_INLINE_WORKER=0` 启动 |
| 轮询状态 | `GET /runs/{run_id}` 直到终态，记录 `polls` / `elapsed_s` / `workers_seen` / `fencing_tokens_seen` |
| 执行者校验 | `assert_worker_executed()`：拒绝 inline 模式、拒绝 `lease_owner` 为空或等于 API Worker ID、要求 `fencing_token >= 1` |
| 证据校验修正 | 不再读 `report.evidence_ids`，改为 `GET /runs/{run_id}/evidence`，并校验证据图存在对应 `Evidence` 节点与来源关系 |
| 场景保留 | A/B/C/D 四场景**一个不删**（原有断言全部保留，仅加执行者校验） |
| 输出 | `docs/assets/run007/e2e-worker-run007.json`（**不覆盖**旧 `e2e-run007.json`） |

实测结果（独立 API `:8020` + 4 个 Worker `run007-w1..w4`）：

- **55 步，0 失败，总耗时 50.6s**；`api_inline_worker=false`
- 5 个 Run 全部由独立 Worker 领取（w1/w2/w3/w4 均出现过），`fencing_token=1`，`attempt=1`
- 场景 A：Verdict `PASSED`；真实 Evidence **18 条**（api 1 / browser 15 / network 2）；5 个 Step；
  LLM `engine=llm:deepseek`、`llm_calls=5`、`fallback_calls=0`、`decision_source=llm`
- 场景 A 门禁：无稳定性回放时审核返回 **412**（`STABILITY_REPLAY_REQUIRED`），回放 3 次真实 Run 后
  `replay_classification=STABLE_PASS` 才允许审批
- 场景 B：受控假成功 → Verdict `FAILED`；Evidence **28 条**；证据图 54 节点 / 70 边，
  节点类型含 `Run/Step/Event/Evidence/Assertion/Hypothesis/Source`；诊断 `CONFIRMED` / `PRODUCT_DEFECT`
- 场景 C：探针 `http_state`、`network_trace` 均 `SUCCEEDED`；`STOP_NO_HIGH_VALUE_PROBE`；
  新增 2 条证据；证据图 58 节点 / 76 边；假设状态从候选转为 `CONFIRMED/SUPPORTED`
- 场景 D：基线在故障下 `FAILED / NOT_EVALUATED`（步骤中断，符合规范）→ 提案 `VALIDATED`
  （断言保护 PASS、Oracle 完整性 PASS）→ 沙箱由 `run007-w1` 执行 `PASSED` → 人工 `role:qa-lead` 审批 →
  CAS 晋升 v2 → 幂等重放仍 v2 → 回归由 `run007-w3` 执行 `PASSED` → 回滚 `ROLLED_BACK`（v3 恢复）→
  回滚后复核 Run 仍由 Worker 执行 `PASSED`

> 注：场景 A 的 `provider` / `model` 字段由响应侧返回为空（后端只回填 `engine=llm:deepseek` 与调用统计），
> 本报告不编造 provider/model；真实调用次数与 fallback 情况以上表为准。

---

## 3. WP4-01：PostgreSQL 16 Service Container 扩展验证

本机无 Docker / psql（`docker`、`psql`、`initdb` 均不存在），因此**运行时验证在本机 BLOCKED**，
改为把验证固化进 CI 的 PostgreSQL 16 作业，并新增平台级用例：

`tests/integration/test_postgres_platform.py`（6 用例，本地无 PG 时整体 skip 并打印原因）：

| 用例 | 覆盖指令要求 |
|---|---|
| 全新 PG 库迁移 v8 恰好一次 + 字段齐全 + 重启幂等 | 迁移 v8 幂等 |
| 4 连接并发 `create_schema()` | 多 Worker 同时启动 |
| 12 连接抢 1 个 Run，恰好 1 个赢家、`fencing_token=1` | 并发 CAS / 队列原子性 |
| 租约过期 → reap → 接管 → `fencing_token` 1→2 → 旧代次 `STALE_FENCING_TOKEN` 被拒 → 新代次提交成功 | 崩溃恢复 / 代次隔离 |
| 两连接基于同一 `base_version` 并发晋升：1 成功 + 1 `VERSION_CONFLICT`；幂等重放不加版本；回执行数与版本号断言 | 晋升 CAS + Repair 回执 |
| 4 线程共享 producer 实例并发 append 20 事件：序列不重号 | 事件并发 |

`nightly-gate.yml` 的 `postgres` 作业额外校验：
`SHOW server_version` 必须为 `16`（否则 BLOCKED）、跑 `test_postgres_parity.py` + `test_postgres_platform.py`、
在**全新 PG 库**上重复建表验证迁移（含 v8）幂等。

> 状态：**IMPLEMENTED（用例与 CI 已就绪）/ 运行时 BLOCKED（本机无 PG；CI 因无凭据无法触发）**。

---

## 4. WP4-02：Worker 故障恢复实验（脚本重写）

旧脚本问题（审计确认）：`killed_after_s` 恒为 0（无真实计时）、记录里没有被杀 Run ID、
"接管成功"可被**其它 Run** 的租约变化满足、错误要求 `attempt` 增加。

重写要点（`scripts/worker_fault_recovery.py`）：

1. 真实计时：记录 `started_at` / `kill_at` / `killed_after_s` / `takeover_latency_s`；
2. 真实定位受害者：按 `lease_owner` 反查 PID，记录 victim 的 Worker ID / PID / 当时 `fencing_token` / 租约到期时间；
3. **同 Run 接管验证**：接管后该 Run 的 `lease_owner` 必须变化，`fencing_token` 必须严格增加，
   且 `attempt` **不变**（接管不是重试）；
4. 陈旧代次拒绝：被杀前的 `fencing_token` 调用 `renew_lease` / `submit_result` 必须被拒；
5. 账实核对：Evidence、Step、事件、报告、终态与 DB 行数逐项比对；
6. 长用例（`SLOW_WAIT_MS=14000`）保证"杀进程时 Run 还在跑"。

产出：`docs/assets/run007/worker-fault-recovery-verified.json`（旧 `worker-fault-recovery.json` 保留）。
运行结果见 `docs/RUN-007-TEST-REPORT.md`。

---

## 5. WP4-03：60 分钟长稳（脚本重写）

旧脚本问题：`sync:true` 绕过 Worker、`teamflow_ok` 恒真（`or True`）、SSE 未真实校验。

重写要点（`scripts/soak_run007.py`）：

| 维度 | 实现 |
|---|---|
| 架构 | 脚本自启独立 API（`TG_INLINE_WORKER=0`，`:8030`）+ 4 个 Worker 进程 + 独立库 `.traceguard/run007soak/api.db` |
| 执行 | 每周期 `{"sync": false}` 入队，轮询到终态；记录领取 Worker、`fencing_token`、耗时 |
| SSE | 真实 `SseReader` 线程：统计事件帧 / 心跳 / `retry:` 帧，并用 `Last-Event-ID` 做续传验证 |
| TeamFlow | 每周期真实 `GET /api/state/projects`，要求 `ok is True` 且含 `count`，不再恒真 |
| 进程 | `ps` 采样 CPU/RSS；沙箱禁止 `ps` 时回退 `/proc`，再回退为存活探测并标注 `source=liveness_only`（资源指标显式 `null`，不伪造 0） |
| 收尾完整性 | `run_verdicts`、`runs_without_report`、`duplicate_event_keys`、`orphan_steps`、`runs_without_evidence`、`unrecovered_leases`、`PRAGMA quick_check` |
| 判定 | 19 项 checks 全真才 `passed=true` |

产出：`docs/assets/run007/soak-60min.json` + `docs/assets/run007/soak-samples.jsonl`（每周期原始采样）。
运行结果见 `docs/RUN-007-TEST-REPORT.md`。

---

## 6. WP3：前端 14 视图产品化验收

后端补齐（让界面有真实数据可显示，而不是空壳）：

| 文件 | 改动 |
|---|---|
| `apps/api/.../routers/intelligence.py` | `diagnose` 增加 `use_llm` 查询参数，用于 Benchmark 规则组口径纯净（不改变默认行为） |
| `apps/web/src/views/DashboardView.vue` | 新增 `/system/health` 拉取；Worker 面板明确「独立 Worker / 内嵌 Worker」、API Worker ID、数据库正常/异常 + dialect |
| `apps/web/src/views/RunDetailView.vue` | 新增「执行归属」条：`Worker`、`attempt`、`fencing token`、队列状态、租约到期/已释放 |
| `apps/web/src/components/GraphView.vue` | 修复证据图被裁切：节点尺寸常量 + 自适应 `fit()`（`watch` graph/容器尺寸）+ 「适应画布」按钮 + 缩放下限 0.35 |
| `apps/web/src/style.css` | 证据图画布高度由 `max-height: 520px` 改为 `height: clamp(360px, 56vh, 560px)` |
| `apps/web/src/types.ts` | `Run` 增加 `lease_expires_at?: number` |

验收方式（`scripts/ui_review_run007.py`）：**真实 Chromium** 逐页截图，不用 Mock、不只看脚本绿灯。

- 覆盖 **15 个视图 / 14 个分组**（含 `04-testcase-detail`、`07-run-detail`、`08-run-evidence-graph`、`12-diagnosis-detail`）
- 4 断点 **1440 / 1024 / 768 / 390** + **Dark@1440**（暗色同时校验：关键内容命中、无横向溢出、`body` 背景亮度 < 0.35 且文字更亮、无 console error）
- 四态真实截图：loading（拦截 API 延迟 4s）/ empty（`?verdict=INCONCLUSIVE`）/ error（不存在的 Run）/ success（Dashboard）
- 79 张 PNG + `docs/assets/run007/ui/ui-review.json`

细节与人工核验结论见 `docs/RUN-007-UI-REVIEW.md`。

---

## 7. WP5：CI 三级门禁修复

### 7.1 PR Gate（`.github/workflows/pr-gate.yml`）

- push 分支过滤由旧分支改为 `main` + `workbuddy/run007-resume-*` + `workbuddy/run007-*`（兼容旧名），确保新分支能触发；
- 快速集成集合加入本轮新增的 `test_sandbox_worker_queue.py`、`test_pipeline_state_cache.py`；
- 增加 `timeout-minutes`，避免挂死。

### 7.2 Nightly Gate（`.github/workflows/nightly-gate.yml`）

| 修复项 | 修复前 | 修复后 |
|---|---|---|
| Playwright 安装 | `playwright install --with-deps chromium \|\| true` | 去掉 `\|\| true`，并显式校验 chromium 目录非空，否则 exit 1 |
| LLM 缺 Secret | `echo "BLOCKED: ..."` 后整步成功，无结构化状态 | 写 `$GITHUB_OUTPUT status=BLOCKED` + Step Summary；Release 侧可判定"证据缺失" |
| LLM 有 Secret 但失败 | `scripts/llm_smoke.py \|\| echo "BLOCKED..."`（**真实失败被吞**） | 直接 `llm_smoke.py`，失败即 Job 失败 |
| npm audit | `npm audit --json > audit.json \|\| true` 后解析，工具报错会变成"0 漏洞" | 区分 `AUDIT_TOOL_ERROR`（JSON 非法/缺 metadata/退出码异常 → 阻断）与 `VULNERABILITIES`（High/Critical 阻断） |
| PG 测试 | 只跑 parity，未校验 PG 版本 | 增加 `SHOW server_version` 必须为 16、平台级用例、全新库迁移幂等 |
| 汇总 | 无 | 新增 `gate-verdict` Job：任一关键 Job 非 success 即失败；LLM BLOCKED 打 warning 且不计通过 |

### 7.3 Release Gate（`.github/workflows/release-gate.yml`）

| 修复项 | 修复前 | 修复后 |
|---|---|---|
| 文档依赖 | 校验 `docs/RUN-005-BASELINE.md` 等 RUN-005 历史文件 | 改为校验 **RUN-007 七份报告** + FEATURE-STATUS/ARCHITECTURE/DATABASE/API |
| 迁移检查 | 只建两次表，不断言版本 | 断言迁移集合、无重复、v8 存在 |
| 证据 | 无 | 逐份校验 `e2e-worker-run007.json` / `worker-fault-recovery-verified.json` / `soak-60min.json` / `ui/ui-review.json` / `benchmark/benchmark-v3.json` 存在、非空、JSON 合法、关键字段齐全（缺一即 fail-closed） |
| Nightly 归属 | 无任何校验（旧 commit 的通过可冒充新 RC） | 新增 `nightly-attestation`：用 Actions API 查 `nightly-gate.yml` 运行，**`head_sha` 必须严格等于 `github.sha`**，且全部 Job 无失败；找不到即 BLOCKED |
| Benchmark 声明 | 只打印输入 | `benchmark_report != true` 或报告缺失 → 阻断 |
| Release Tag | — | 本工作流**不创建 Tag**（保持指令） |

### 7.4 真实运行结果（三级门禁均已跑通，不再是 BLOCKED）

证据索引：`docs/assets/run007/ci-gates.json`。全部运行针对同一 commit
`cc3339d291cc6b0e328922934ec82f6b689f4769`，分支 `workbuddy/run007-resume-20261123`，PR #1（→ `main@ac2876e`）。

| 门禁 | 事件 | commit | 结论 | 运行 |
|---|---|---|---|---|
| PR Gate | push | `cc3339d` | ✅ success | [#45201761239](https://github.com/zjy110203/traceguard-imac/actions/runs/45201761239) |
| PR Gate | pull_request | `cc3339d` | ✅ success | [#45201768385](https://github.com/zjy110203/traceguard-imac/actions/runs/45201768385) |
| Nightly Gate | workflow_dispatch | `cc3339d` | ✅ success（5/5 job） | [#45201774473](https://github.com/zjy110203/traceguard-imac/actions/runs/45201774473) |
| Release Gate | workflow_dispatch | `cc3339d` | ✅ success（4/4 job） | [#45202185410](https://github.com/zjy110203/traceguard-imac/actions/runs/45202185410) |

Nightly 的 5 个 job：`backend-full` / `postgres` / `security` / `llm-smoke` / `gate-verdict` 全部 success。
其中 `postgres` 用 `postgres:16` Service Container，先断言 `SHOW server_version` 以 `16` 开头
（实测 `16.16 (Debian 16.16-1.pgdg13+1)`），再执行 6 个平台级用例（迁移 v8、租约/代次、CAS、回执）。

Release Gate 的 `nightly-attestation` 实测输出：

```
NIGHTLY_OK id=45201774473 head_sha=cc3339d291cc6b0e328922934ec82f6b689f4769
```

即 Nightly 的 `head_sha` 与 Release Gate 的 `github.sha` **严格相等**。
作为对照：仓库里原本存在的历史 Nightly success 是 `sha=8f912f2`（旧 commit），按新门禁不会被接受——
这正是"旧 commit 的通过不得冒充新 RC"要解决的问题。

### 7.5 CI 首跑暴露并修复的缺陷（4 轮迭代）

CI 一旦真实运行，就暴露出 8 个本地环境掩盖的问题（本地 523 passed 全部放过）。逐轮记录，不省略失败：

| 轮 | commit | Nightly | 暴露的问题 | 修复 |
|---|---|---|---|---|
| 1 | `d2e86ae` | ❌ | ① `str(URL)` 把密码渲染成 `***` → 6 个 PG 用例全挂在 fixture<br>② secret 规则 `DEEPSEEK_API_KEY=[^[:space:]]+` 命中文档占位符（误报）<br>③ `probe_budget_multiprocess` 子进程 `ModuleNotFoundError: traceguard_api` | ① `render_as_string(hide_password=False)`<br>② 改用 `git grep`（只扫已跟踪文件）+ 模式收紧到真实密钥形态<br>③ 补 `sys.path` 注入 |
| 2 | `7fdd2e9` | ❌ | ④ **`claim_next_run.previous_status` 恒为新值**（ORM 在 UPDATE 后 expire 重读 PREPARING）— **真实产品缺陷**<br>⑤ PG 接管用例缺 `mark_running`（状态机不允许 `PREPARING→COMPLETED`）<br>⑥ **`create_schema` 只重试 `already exists`，不重试 `database is locked`** — **真实产品缺陷**<br>⑦ 路径清单漏 `packages/evidence` | ④ UPDATE 前快照状态<br>⑤ 测试补 `mark_running`（真流程见 `pipeline.py:130`）<br>⑥ 新增 `_is_transient_lock_error`（含 PG deadlock）+ 8 次指数退避<br>⑦ 改为目录扫描 |
| 3 | `f00f149` | ❌ | ⑧ 内联 `python - <<'PY'` 不走 pytest 的 `pythonpath` → `ModuleNotFoundError: traceguard_persistence` | job 级 `PYTHONPATH`（nightly 的 postgres/backend-full + release 的 full-verification） |
| 4 | `cc3339d` | ✅ | — | — |

> 其中 ④ 与 ⑥ 是**产品代码缺陷**，其余是测试/CI 自身缺陷。④ 当前无消费方（仅审计字段），
> 但返回契约错误；⑥ 直接影响"API + N 个 Worker 同时启动"这一真实部署形态。

结论：CI 改动状态为 **VERIFIED**（不再是 IMPLEMENTED 未运行）。

---

## 8. WP6：UI-FaultBench v3 多臂实验

见 `docs/RUN-007-BENCHMARK-REPORT.md`（四臂 A/C/D/E、30 固定场景、GT 仅评测侧读取、
Flaky 重复 5 次保留原始、指标带分子/分母、LLM 与人工基线未执行处标 BLOCKED）。

---

## 9. 已知限制（本轮未解决，需如实继承）

1. ~~Git push / PR 无法完成~~ → **已解决**：分支已推送（`workbuddy/run007-resume-20261123`），PR #1 已创建（→ `main@ac2876e`）。
2. ~~CI 未真实运行~~ → **已解决**：PR Gate / Nightly Gate / Release Gate 三级在同 commit（`cc3339d`）全部 success，见 §7.4。
3. ~~PostgreSQL 运行时未验证~~ → **CI 已真实验证**（`postgres:16` → `16.16`，6 个平台级用例）。**本机仍无 Docker/psql**，本地运行时这 6 个用例仍是 skip（逐条打印原因），不能把 skip 当 pass。
4. **进程资源采样受限**：沙箱禁止 `ps` 且无 `/proc`；长稳以存活探测 + DB 租约为准，资源字段为 `null`（`source=liveness_only`）。
5. **冻结边界未动**：D-01 认证、P03、P08、P06 仍为待确认，见 `docs/RUN-007-DECISION-REQUESTS.md`。
6. **自愈未闭环**：定位器场景沙箱通过后 `/promote` 未返回 `promoted_version`，回归 `NOT_EVALUATED`（0/1）。见 `RUN-007-BENCHMARK-REPORT.md`。
7. **D/E 臂无增益**：证据图与探针在 19 个失败样本上没有额外主因命中（Macro-F1 略降），不宣称提升。
8. **CI 网络抖动**：本机经 HTTP 代理访问 `github.com` 间歇性 `Empty reply` / `CONNECT 502`，push 需重试；`api.github.com` 稳定。不影响已在 CI 侧获得的结论。

## 10. RUN-007-FINALIZATION-2 追加实现（2026-11-24）

完整报告：`docs/RUN-007-FINALIZATION-2-REPORT.md`。本节只列实现要点与入口：

| 工作包 | 实现位置 | 备注 |
|---|---|---|
| Seed 幂等 | `apps/api/traceguard_api/seed.py` | 静态数据标签 + 语义指纹 + 新版本恢复基线 |
| 回执身份 | `packages/persistence/.../catalog.py`、`services/repair_service.py` | 6 元组校验、`record_receipt()`、晋升 fail-closed |
| Evidence 摘要 | `packages/evidence/.../evidence_manager.py`、`persistence/stores.py`、`models.py`、`db.py`（迁移 v9） | 来源/持久化/附件三种摘要；历史行 NOT_RUN |
| 服务端验证 | `routers/evidence.py` | 读库内容重算 + 附件字节 + 派生来源 + workspace |
| 执行链 | `packages/execution/.../pipeline.py` | 附件摘要随证据入库 |
| 迁移 v9 / v10 | `packages/persistence/.../db.py`、`models.py` | 证据摘要列；`intent_ref` 放宽到 255 |
| PG-only 缺陷 | `packages/contracts/.../models.py`、`agents/.../compiler.py`、`services/exploration_service.py`、`persistence/catalog.py` | `intent_ref` 只存引用，正文进 `intent_text`；超长显式 `VALIDATION_ERROR` |
| PG 验证脚本 | `scripts/pg_soak_run007.py` | 真实 PG 持续运行 + 4 Worker 并发，DB 级不变量校验 |
| Benchmark | `scripts/bench_run007.py` | 动态基线、固定版本回归、回滚内容验证、E 臂禁回退、`--repeats` |
| E2E | `scripts/e2e_run007.py` | 逐条证据验证、回滚内容级复核 |
| 评测口径 | `benchmark/.../evaluation.py`、`scenarios.py` | Macro-F1 浮点分子；四字段口径 |

未做/受阻项与安全审查结论见该报告 §7、§9。
