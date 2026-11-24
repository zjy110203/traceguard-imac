# RUN-007 测试报告

- 时间：2026-11-23（GMT+8）
- 分支：`workbuddy/run007-resume-20261123`
- 环境：Python 3.13 `.venv`；SQLite；真实 Chromium（Playwright）；LLM = DeepSeek（真实调用）
- 判据：**skip 不算 pass**；`|| true` 类掩盖不算 pass；脚本绿灯 ≠ 视觉验收。

---

## 1. 自动化测试总览

| 目录 | 采集用例数 | 说明 |
|---|---|---|
| `tests/unit` | 261 | 纯逻辑/状态机/规则/策略 |
| `tests/contracts` | 13 | 契约与 Schema 防漂移 |
| `tests/integration` | 239 | API/服务/持久化/Worker/SSE/修复链路 |
| `tests/e2e` | 12 | 真实 Chromium（目标策略、浏览器执行） |
| `tests/teamflow` | 9 | 被测应用与故障注入 |
| **合计** | **534** | 本轮新增 19 个用例（见 §2） |

全量运行结果见 §7：**523 passed / 11 skipped**；11 个 skip 全部是 PostgreSQL 相关（本机无 PG），逐条打印了跳过原因。

## 2. 本轮新增测试

| 文件 | 用例数 | 目的 |
|---|---|---|
| `tests/unit/test_pipeline_state_cache.py` | 2 | 钉住「状态缓存生存期 = 一次 Run」：同一 Run 复用缓存、不同 Run 必须重读（1→2） |
| `tests/integration/test_sandbox_worker_queue.py` | 2 | 沙箱必须走队列：无 Worker 时 fail-closed（Run 仍 QUEUED）；有 Worker 时 `sandbox_executed_by` / `fencing_token=1`，且沙箱用例 ID 不污染正式版本线 |
| `tests/integration/test_migration_v8.py` | 4 | 迁移 v8 幂等、重复启动、**v7 旧库升级不丢数据**、并发启动建表 |
| `tests/integration/test_postgres_platform.py` | 6 | PR16 平台级契约（迁移/并发建表/claim 原子性/租约接管代次/晋升 CAS+回执/事件序列），本地无 PG 时 skip |

## 3. WP1 五项修复专项复核

命令：

```bash
.venv/bin/pytest -q \
  tests/integration/test_stability_replay.py tests/integration/test_stability_gate.py \
  tests/integration/test_repair_crash_recovery.py tests/integration/test_repair_atomic_cas.py \
  tests/integration/test_actor_validation.py tests/integration/test_probe_budget_multiprocess.py \
  tests/unit/test_target_policy_dns.py
```

结果：**163 passed / 0 failed，12.46s**。

补充：`tests/e2e/test_target_policy_browser.py`（真实 Chromium 出站边界）**5 passed，5.55s**。

## 4. 迁移 v8 幂等与旧库兼容

`tests/integration/test_migration_v8.py`：**4 passed**。

- 全新库：迁移 2–8 各一次，`tg_promotion_receipt` 字段齐全（`idempotency_key` / `kind` / `proposal_id` / `testcase_id` / `base_version` / `target_version` / `result`）；
- 重复 `create_schema()`（同实例 + 新实例）：迁移记录不重复；
- **v7 旧库升级**：删掉 v8 表与记录后重启，v8 自动补上，既有 `tg_testcase` 数据保留，回执表可写且主键约束生效；
- 双实例并发启动：不崩溃、迁移记录唯一。

> 该文件在修复 `db.py` 之前是**失败**的（`table tg_testcase_version already exists`），修复后通过。

## 5. 端到端（独立 Worker 消费队列）

架构：API `:8020` 以 `TG_INLINE_WORKER=0` 启动 + 独立 Worker `run007-w1..w4`；所有 Run 走 `{"sync": false}` 入队轮询。

### 5.1 结果

| 项 | 值 |
|---|---|
| 步骤 | **55 步 / 失败 0** |
| 总耗时 | **59.4s** |
| API 内嵌 Worker | `false` |
| Run 数 | 5（A 1、B 1、D 3：基线/沙箱/回归，另 D18 复核复用） |
| 执行者 | `run007-w1` / `w2` / `w3` / `w4` 均有领取，`fencing_token=1`、`attempt=1` |
| 场景 A 证据 | **18 条**（api 1 / browser 15 / network 2），5 Step |
| 场景 B 证据 | **28 条**；证据图 54 节点 / 70 边，含 `Run/Step/Event/Evidence/Assertion/Hypothesis/Source` |
| 场景 C | 探针 `http_state`、`network_trace` 均 `SUCCEEDED`；`STOP_NO_HIGH_VALUE_PROBE`；新增 2 条证据；图 58 节点 / 76 边 |
| 场景 D | 基线 `NOT_EVALUATED`（步骤中断）→ 沙箱 `PASSED` by `run007-w4`（`sandbox_fencing_token=1`）→ 晋升 v2 → 幂等重放 v2 → 回归 `PASSED` by `run007-w3` → 回滚 `ROLLED_BACK` → 回滚后复核 `PASSED` |
| 审核门禁 | 无回放 → **412 `STABILITY_REPLAY_REQUIRED`**；回放 `STABLE_PASS` 后允许审批；Agent 审批被拒、`role:qa-lead` 通过 |
| LLM | `engine=llm:deepseek`、`provider=deepseek`、`model=deepseek-flash`、`llm_calls=5`、`fallback_calls=0`、`decision_source=llm`、`real_llm_call_observed=true` |

原始数据：`docs/assets/run007/e2e-worker-run007.json`（不覆盖第五轮的 `e2e-run007.json`）。

## 6. 长稳与故障恢复

### 6.1 60 分钟长稳（独立 API + 4 Worker）

命令：`.venv/bin/python scripts/soak_run007.py --minutes 60 --interval 60`

| 项 | 值 |
|---|---|
| 时长 | **60.03 min**（65 个周期 × 60s） |
| 架构 | API `:8030`（`inline_worker=false`）+ Worker `soak-w1..w4` + 独立库 `.traceguard/run007soak/api.db` |
| 每周期结果 | **65 PASSED / 0 FAILED / 0 NOT_EVALUATED / 0 API error / 0 timeout** |
| Worker 轮换 | w1 14 次 / w2 16 次 / w3 18 次 / w4 17 次（每 Run `fencing_token=1`、`attempt=1`） |
| 单 Run 耗时 | 中位 5.2s，P95 5.7s，最长 7.2s |
| SSE | 65/65 周期收到事件；`Last-Event-ID` 续传：38 次有新事件、27 次确认无新事件；心跳 158 次；重复 event id 0 |
| 完整性 | `runs_without_report=0`、`duplicate_event_keys=0`、`orphan_steps=0`、`runs_without_evidence=0`、`unrecovered_leases=0`、`PRAGMA quick_check=ok` |
| 数据规模 | 65 runs / 65 tasks / 845 events / 1820 evidence / 520 steps；DB 5.35MB（WAL 3.97MB） |
| 判定 | **19 项 checks 全 true，`passed=true`** |

原始数据：`docs/assets/run007/soak-60min.json` + `docs/assets/run007/soak-samples.jsonl`（每周期原始采样）。

**限制（如实记录）**：本机沙箱禁止执行 `ps` 且 macOS 无 `/proc`，因此每周期 CPU/RSS 采样退化为
`source=liveness_only`（`cpu_pct`/`rss_mb` 为 `null`，不伪造成 0）；进程存活由 `os.kill(pid, 0)`
与数据库租约共同证明。

### 6.2 Worker 被杀 → 同 Run 接管

命令：`.venv/bin/python scripts/worker_fault_recovery.py`（4 Worker，租约 TTL 6s）

| 项 | 值 |
|---|---|
| 被杀对象 | `wfr-w1`，PID `22555`，真实 `SIGKILL`（exit code `-9`） |
| `killed_after_s` | **3.02s**（真实计时，不再是恒 0） |
| 被杀时状态 | `RUNNING`，`fencing_token=1`，`attempt=1`，已完成 1 个 Step |
| 接管延迟 | **21.3s**（租约 6s + 回收/轮询） |
| 同 Run 接管 | `final_owner=wfr-w4`，**`fencing_token` 1 → 2**，`attempt` 仍为 1 |
| 陈旧代次 | 旧 token 的 `renew_lease` / `submit_result` 均被拒（`STALE_FENCING_TOKEN`） |
| 账实核对 | 4 个 Run 全部终态；无重复事件/步骤/孤儿步骤；证据与报告齐全；被杀 Run 有事件 |
| 判定 | **17/17 checks 全 true，`passed=true`** |

原始数据：`docs/assets/run007/worker-fault-recovery-verified.json`（旧 `worker-fault-recovery.json` 保留）。

## 7. 全量回归

```bash
.venv/bin/pytest -q
```

结果：**523 passed / 11 skipped / 0 failed，46.69s**（采集 534 项）。

- 11 个 skip 全部是 PostgreSQL 相关（`test_postgres_parity.py` 5 + `test_postgres_platform.py` 6），
  本机无 Docker / psql，**逐条打印跳过原因**，不记为通过；
- 起点基线为 515 passed / 5 skipped，本轮新增 19 个用例（其中 6 个 PG 用例本地 skip）。

## 8. 静态检查

| 项 | 命令 | 结果 |
|---|---|---|
| Ruff lint | `.venv/bin/ruff check packages apps tests benchmark scripts` | 全绿 |
| Ruff format | `.venv/bin/ruff format --check ...` | 全绿 |
| 前端类型检查 + 构建 | `cd apps/web && npm run build`（内含 `vue-tsc --noEmit`） | 通过 |
| 依赖漏洞 | `cd apps/web && npm audit --json` | high 0 / critical 0 / moderate 3（dev 依赖，不阻断） |
| 密钥模式扫描 | Nightly `secret pattern scan` 同款 grep | 本地 0 命中 |

## 9. 未执行 / BLOCKED 的验证

| 项 | 状态 | 原因 |
|---|---|---|
| PostgreSQL 16 运行时（本机） | BLOCKED（本机） | 无 Docker / psql。**CI 已真实验证**：Nightly 的 `postgres:16`（16.16）上 6 个平台用例全过 |
| ~~PR / Nightly / Release Gate 真实运行~~ | **VERIFIED** | 同 commit `cc3339d` 三级全绿，见 §9.1 |
| 人工诊断基线（Benchmark） | BLOCKED | 无人力投入，不用 Mock 补齐 |
| 多浏览器（Firefox/WebKit） | NOT_IMPLEMENTED | 只有 Chromium |
| 可访问性（axe） | NOT_IMPLEMENTED | 未接 a11y 工具链 |

### 9.1 CI 三级门禁（真实运行，补上原 BLOCKED 项）

同一 commit `cc3339d291cc6b0e328922934ec82f6b689f4769`：

| 门禁 | 事件 | 结论 | 运行 |
|---|---|---|---|
| PR Gate | push | success | [#45201761239](https://github.com/zjy110203/traceguard-imac/actions/runs/45201761239) |
| PR Gate | pull_request | success | [#45201768385](https://github.com/zjy110203/traceguard-imac/actions/runs/45201768385) |
| Nightly Gate | dispatch | success（5/5 job，含 `postgres:16`） | [#45201774473](https://github.com/zjy110203/traceguard-imac/actions/runs/45201774473) |
| Release Gate | dispatch | success（4/4 job） | [#45202185410](https://github.com/zjy110203/traceguard-imac/actions/runs/45202185410) |

Release 的 `nightly-attestation` 实测：`NIGHTLY_OK id=45201774473 head_sha=cc3339d291cc...` —
Nightly 的 `head_sha` 与 Release 的 `github.sha` 严格相等。

**CI 首跑共暴露 8 个本地掩盖的问题**（4 轮迭代修复，其中 2 个是产品缺陷：`previous_status`
取到更新后的状态、`create_schema` 不重试 DDL 锁冲突）。详细逐轮记录见
`docs/RUN-007-IMPLEMENTATION-REPORT.md` §7.5，原始证据见 `docs/assets/run007/ci-gates.json`。

## 10. Benchmark 结果（摘要）

完整报告：`docs/RUN-007-BENCHMARK-REPORT.md`；原始数据：`docs/assets/run007/benchmark/`。

- 30 个固定场景 / **38 次真实执行**（Flaky 2 场景各重复 5 次）/ 19 个失败样本，全部由独立 Worker 执行；
- 主因准确率：A 传统基线 **3/19**、C 规则 **10/19**、D 证据图 **10/19**、E 探针 **10/19**；
- 假通过率：A **4/4（100%）**，C/D/E **0/4**；
- 覆盖率：C **13/19**、E **14/19**（selective accuracy 分别 10/13、10/14）；
- LLM 对照（真实调用）：Flash **8/19**、Pro **5/8**，同子集 8 例上 Flash 与 Pro **均为 5/8**；Routed 未触发（PARTIAL）；
- 自愈：断言保持 **2/2**、校验成功 **1/2**、回归通过 **0/1** → **自愈未闭环，已在报告中如实标注**。

## 11. 证据索引

`docs/assets/run007/evidence-sha256.txt` 列出证据目录全部 94 个文件的 SHA-256（含 79 张 UI 截图），
便于复核"交付的证据就是当时跑出来的证据"。

---

## 12. 第四轮（RUN-007-FINALIZATION-2，2026-11-24）

本节结果**取代** §10 摘要中与自愈/口径相关的旧结论；§10 保留为上一轮原始记录，不改写。

### 12.1 自动化测试

| 项 | 上一轮（`fdbe0f5`） | 本轮 |
|---|---|---|
| pytest 全量 | 523 passed / 11 skipped | **547 passed / 11 skipped** |
| 跳过原因 | 未配置 PG | 同（`test_postgres_parity.py` / `test_postgres_platform.py`） |
| ruff check / format | 全绿 | 全绿 |
| 前端 typecheck / build | 通过 | 通过 |

新增测试：

| 文件 | 数量 | 覆盖 |
|---|---|---|
| `tests/integration/test_seed_idempotency.py` | 2 | 两次 Seed 版本稳定、Repair 后恢复基线、历史版本不可变 |
| `tests/integration/test_repair_receipt_identity.py` | 7 | 跨 Proposal/跨 kind/跨 workspace 复用键、补回执持久化、fail-closed |
| `tests/integration/test_evidence_storage_integrity.py` | 7 | DB 重启复验、内容/附件篡改、派生来源、历史行 NOT_RUN |
| `tests/integration/test_api_flow.py` | +1 | 验证接口服务端语义、旧请求分离、workspace 不符 |
| `tests/unit/test_benchmark.py` | +3 | Macro-F1 真分子、缺失预测计 FN、数据集口径 |
| `tests/unit/test_evidence.py` | 改写 4 + 新增 4 | 摘要语义分离、存储校验、来源比对不冒充完整性 |

### 12.2 真实运行证据

| 项 | 结果 | 证据 |
|---|---|---|
| E2E A/B/C/D（独立 Worker） | **56 步 / 0 失败** | `docs/assets/run007/e2e-run007-f2.json` |
| Evidence 逐条服务端校验 | A 15/15、B 28/28 PASS；附件 36/36 字节一致；NOT_RUN 0 | 同上 |
| 回滚内容级复核 | 指针/步骤/断言/Oracle 绑定全部恢复 | 同上 D17b |
| Benchmark 两轮 | 76 次执行 / 661.2s / 每轮 19 个失败样本 | `benchmark-v3-f2.json` |
| PostgreSQL 16.16（本机） | 13/13 PG 用例；6 分钟 78 Run / 4 Worker；PG 上 E2E 56/56 | `pg-soak.json`、`e2e-pg-run007.json` |
| 自愈闭环 | 2/2（晋升→固定版本回归→回滚复核全通过） | 同上 `repair_records` |
| 前端 15 视图 × 4 断点 | 75/75，0 溢出 / 0 console 错误 / 79 张截图 | `docs/assets/run007/ui-f2/` |

### 12.3 仍然受限的项

- PostgreSQL 已在本机用 Postgres.app **16.16** 实测（与 CI 同版本）：13 个 PG 用例全过、
  6 分钟持续 78 Run / 4 Worker 并发不变量全过、PG 上 E2E 56/56；迁移 v9/v10 均在旧表升级场景实测。
  仍存边界：只有 6 分钟持续，不是小时级压测；未验证连接池/主从；实例是 `/tmp` 临时进程。
- 本机 PG 暴露并已修复 1 个 **PG-only 缺陷**：编译把需求正文写进 `intent_ref`（varchar(64)），
  SQLite 静默接受、PG 直接 500。修复为 `intent_ref=intent_id` + `intent_text` 正文 + 长度护栏 + 迁移 v10。
- 本轮代码/证据提交 `7e7528f` 的 **PR Gate 已真实通过**（push #15 `45226091595` 53s；
  PR #16 `45226105637` 52s，backend/frontend 全绿）；
  Nightly / Release Gate **未手动 dispatch**，故不宣称三级门禁在本 commit 全绿。
