# RUN-014 最终报告：产品闭环补全、安全可靠性强化与发布就绪

> 日期：2026-11-26（CST）｜ 分支：`workbuddy/run014-product-closure`
> 基线：`85f18c1b7c71fb2ee1214e78d7a955a3ece8651d`（RUN-013 交付 HEAD）
> 代码交付 HEAD：`a1b3fe8`（WP5）；其后 `cf05558` 与本报告为 docs-only 收口提交
> 原则：已完成工作包据实列出；**BLOCKED / NOT_RUN / PARTIAL 不写成 PASS**；本轮同时修正两处历史口径（见 §13）。

---

## 1. 项目基线

| 项 | 值 |
|---|---|
| 仓库 | `zjy110203/traceguard-imac` |
| 基线提交 | `85f18c1`（`workbuddy/run013-production-reliability`，与 origin 一致） |
| 本轮分支 | `workbuddy/run014-product-closure` |
| `main` | `ac2876e`，未修改、未合并 |
| RUN-013 状态 | WP0~WP7 完成；代码交付 `3fc5289`，CI 证据 run `45658081853` |
| 本轮代码交付 | `55d1e5a` → `8cba090` → `02c4b0a` → `52617d1` → `a1b3fe8`（WP1~WP5） |
| 收口提交 | `cf05558`（WP6/WP7 证据与文档）+ 本报告 |

## 2. 交付总览

| WP | 主题 | 状态 | 关键结果 | 证据 |
|---|---|---|---|---|
| WP0 | 基线核查 | 完成 | HEAD 一致、工作区 clean、`main` 未动 | `docs/RUN-014-BASELINE.md` |
| WP1 | Repair Regression 真实闭环 | PASS | 独立 `regression_status`，13 passed；回归只能由真实 Run 判定 | `RUN-014-WP1-REGRESSION.md` |
| WP2 | Lease / Fencing 原子性 | PASS | 过期即失效，同事务守卫；7 条并发故障测试 | `RUN-014-WP2-LEASE-FENCING.md` |
| WP3 | SSE 撤权 / 全局 RBAC / 统计口径 | PASS | 活跃连接每 2s 复核；13 passed | `RUN-014-WP3-SECURITY.md` |
| WP4 | 单资产全血缘 UI 验收 | PASS | 40/40，13 张截图，14 个血缘 ID | `RUN-014-WP4-FULL-UI-LOOP.md` |
| WP5 | Benchmark v5 + AI 质量 | PARTIAL（如实） | 23×2 真跑；AI n=32；False Pass 仍 PARTIAL | `RUN-014-WP5-BENCHMARK-AI.md` |
| WP6 | 发布门禁 | PARTIAL | Ruff / build / 805 passed / PG16 / fencing 9/9；60 分钟长稳 **NOT_RUN** | §8 |
| WP7 | 文档与状态 | 完成 | 本报告 + BASELINE + WORKLOG；README/FEATURE-STATUS 更新 | §11 |

## 3. WP1：Repair → Promotion → Regression → Rollback

- 原缺陷：前端把 Promotion 与 Regression 合成一个 `promoted` 条件，`promotion_record` 非空（含 `VERSION_CONFLICT`）即显示成功；后端根本没有 Regression 阶段；列表虚构 `base+1` 版本。
- 实现：新增独立 `regression_status`（`NOT_STARTED / QUEUED / RUNNING / PASSED / FAILED / INCONCLUSIVE`），`regression_run()` 复用现有 Task/Run/Worker 队列，`COMPLETED + PASSED + evidence_ids 非空` 才判 PASSED；Oracle 或证据缺失 fail-closed；终态幂等、在途只收敛同一 Run，超时不伪造完成。
- 前端：七段生命周期全部取服务端字段（`done / current / fail / todo`）；新增回归面板与列表列；Promotion 失败不再显示成功。
- RBAC：新增 `repair.regression`（qa-lead+），只读状态仍为 `read`。
- 验证：`tests/integration/test_repair_regression_run014.py` **13 passed**；12 条验收 + 5 条补充 fail-closed/RBAC 全部 PASS；回归影响面 **101 passed**。
- 边界：`PROMOTION_FAILED` 后无"重新发起晋升"入口；回归不是与基线版本的 A/B 对比；`regression_stale` 只在收敛/回滚时落库，读取时不重算。

## 4. WP2：租约过期即失效与同事务 fencing

- 原缺陷：`lease_valid` / `renew_lease` 不看 `lease_expires_at`（过期未接管仍自认合法、可"续租复活"）；`_ensure_lease()` 的 SELECT 与写入分属两个事务（TOCTOU）；`submit_result` 不校验租期，且任何中间态提交都提前清零租约；`mark_running` 无代次条件。
- 实现：执行权判定改为 `lease_owner == worker && fencing_token == token && lease_expires_at > now && status ∉ 终态/FINALIZING`；新增 `persistence/fencing.py` 的 `fenced_guard()`——在 Step / Evidence / Event 写入的**同一事务**内做条件空 UPDATE（行锁 + CAS），未命中立即 `LEASE_LOST` 并回滚整笔写入；只有终态提交才释放租约。
- 验证：`tests/integration/test_worker_fencing_run014.py` **7 passed**（过期未接管、暂停→接管→恢复、FINALIZING、4 线程争抢、重启、幂等、续租边界）；全量 **791 passed / 16 skipped**。
- 边界：Artifact 内容寻址落盘未纳入守卫（最坏留下无人引用的孤儿对象）；`run.completed` 靠 `allow_own_submission` 接受同 worker 同代次终态回执；Event 序号在守卫失败时可能跳号，不重复/倒退。

## 5. WP3：SSE 活跃连接撤权、全局接口 RBAC 与统计口径

- 原缺陷：`/events/stream` 只在建连时算一次 Workspace 范围；`/system/llm/summary` 未纳入 `GLOBAL_OBSERVATION_PATHS`；`llm/summary` 用 `limit=1000/500` 静默截断；Artifact 授权与读取分两个事务；`/queue/stats` 响应不声明范围。
- 实现：`auth_recheck_seconds`（默认 2s）每次轮询重新解析会话、用户 `active`、Workspace 授权，失效发 `auth.revoked` 控制帧并关闭连接；`requested_ws` 与可见并集分开记录（本轮实测踩坑并修复）；`/system/llm/summary`、`/system/browser` 收归 `system.observe`；`DocStore.iter_all()` 键集分页 + `coverage` 声明；Artifact 授权单源化；`/probes/audit` 声明 `X-TraceGuard-Audit-Persistent: false`。
- 验证：`tests/integration/test_sse_revocation_and_global_rbac_run014.py` **13 passed**（含 620 文档不截断、跨空间 403、撤权关闭连接）；全量 **804 passed / 16 skipped**。
- 口径修订：RUN-013 WP6 所写"`/queue/stats` 全局队列"有误，该接口自 RUN-012 WP3 起已按授权空间 SQL 过滤，本轮补 `scope` 声明与对照测试。

## 6. WP4：单一业务资产全血缘 UI 验收

- 环境：真实 API（认证 + CSRF，`TG_INLINE_WORKER=false`）+ 2 个独立 Worker + 真实 TeamFlow + 真实 Chromium + SQLite。
- 结果：**40 条记录 = 40 PASS / 0 FAIL**；13 张截图；机器可读 `docs/assets/run014/wp4/full-loop.json` 含 14 个血缘 ID。
- 链路：Intent → Explorer → Trace → Compiler → Oracle → Binding → Replay → Review → Run（独立 Worker）→ Diagnosis → Probe → Repair → Sandbox → Approval → Promotion → Regression → Rollback。正向、负向（无回放审核 412、真实故障 Run FAILED、回归 FAILED、人工回滚、未审批 Oracle 门禁）均按服务端真实结果记录。
- 发现并修复：`_step_index()` 对 `step_index=None` 调 `int(None)`，导致 `run_config.*` 提案经 HTTP 创建即 500；改为 `is not None` 判定并补回归测试。
- 边界：Explore / Compile / Replay / Probe Plan / Oracle 操作通过真实 API（无对应 UI 控件），其余人工决策点由真实 UI 点击；探索轨迹 `browser_version=http-observer` 是标签口径问题，不据此宣称 HTTP 降级；规则编译器 `rule_only`，编译告警如实保留。

## 7. WP5：Benchmark v5 与 AI 质量

### 7.1 Benchmark v5（独立 SQLite + TeamFlow + 3 Worker，LLM 关闭）

| 指标 | 结果 |
|---|---|
| 23 场景 × 2 轮 | **46 次真实执行**，产出失败样本场景 19/23（coverage 0.8261） |
| 诊断样本 / 正控 | 38 / 8 |
| 合同检查 | 5/5 PASS |
| False Pass | **0 / 38，证据级别 PARTIAL / INFERRED_ONLY** |
| Primary Cause Accuracy | 规则臂 0.6842；探针事后回灌臂 0.7368 |
| 探针执行 / 回灌成功率 | 1.0 / 1.0 |
| 时间对齐 | `ALIGNED=0 / POST_HOC=56 / UNKNOWN=56` |

诚实口径：历史归档缺 `fault_state` 快照，False Pass 只能标 PARTIAL；`H_llm_advisor` 是 LLM + 规则 Fallback 混合臂，不是 LLM 单独准确率；4 个场景未产出失败样本，未用正控填充；LLM 四臂本轮未执行。

### 7.2 AI 质量（n=32，8 域 × 4，同输入规则 vs LLM）

| 指标 | 规则 | LLM |
|---|---|---|
| 域准确率（细粒度归一，默认口径） | **0.9688** | **0.8750** |
| Macro-F1（细粒度） | **0.9683** | 0.6119 |
| 结构合法 / Schema / 证据支持 | — | 1.0 / 1.0 / 1.0 |
| 无效 / 重复 / Fallback | — | 0 / 0 / 0 |
| 平均延迟 / P95 | — | 3590.5 ms / 5320 ms |
| 估算成本 | — | $0.006515（+顾问 smoke $0.000991） |

四类顾问真实 Smoke 均调用成功且无 Fallback：Diagnosis（32 次）、Compiler（2 步 / 1 步白名单丢弃）、Probe Planner（3 候选，生产默认仍关闭）、Repair Planner（2 条受限变更 + 2 条告警）。语料为人工构造分层样本，每域 4 例，不能外推为"LLM 准确率 87.5%"，也不能当作通用能力公平比较。

## 8. 发布门禁与可靠性（WP6）

| 门禁 | 结果 | 说明 |
|---|---|---|
| Ruff check / format | 通过 | 230 files formatted |
| 前端 `npm run build` | 通过 | 含 `vue-tsc --noEmit` |
| 全量 pytest | **805 passed / 16 skipped** | skip 均为未配置 `TG_TEST_POSTGRES_URL` 的 PG 专项 |
| PostgreSQL 16 专项 | **16 passed** | parity / platform / workspace |
| PG16 Fencing 专项 | **9/9 PASS** | `docs/assets/run014/wp6/pg-fencing.json` |
| 60 分钟 PG16 长稳 | **NOT_RUN / INCONCLUSIVE** | 见下方说明 |
| `git diff --check` | 通过 | — |

长稳说明：`scripts/soak_run013.py` 已参数化 `TG_SOAK_PG_DB`（默认仍为历史库名，避免覆盖 RUN-013 库），本轮留下 `docs/assets/run014/soak/` 下 2 个 Worker 日志与瞬时指标，但日志只覆盖约 4 分钟，**没有 `soak-60min.json` / `soak-samples.jsonl` 汇总**；因此不得把 RUN-013 的 60 分钟 / 113 Run 结果外推为本轮结果。运行时中间件按 `.gitignore` 规则不入库。

## 9. CI 真实运行证据

- 分支已推送至 `origin/workbuddy/run014-product-closure`，`head_sha=cf055587b3a2f8aeae0cd487e1f51b8e6339ebbc`。
- PR Gate **真实触发**：run [`45756292936`](https://github.com/zjy110203/traceguard-imac/actions/runs/45756292936)，event=`push`，`run_attempt=1`，创建于 2026-11-26 04:36:32 UTC。
- 最终结论：**success**——`backend` 与 `frontend` 两个 Job 均 `completed / success`（backend 04:37:33 UTC、frontend 04:36:56 UTC 完成；含 Ruff check/format、单元+契约、快速服务集成、typecheck+build）。该 run 覆盖分支累计代码（WP1~WP5）与 `cf05558` 收口改动。
- 结论来源：GitHub 公开 API 的 run/jobs 状态，不读取 Secrets；未创建 PR、未合并、未改 `main`。
- Nightly Gate / Release Gate：本轮未触发（需 `workflow_dispatch` 与凭据）→ **NOT_RUN**。

## 10. 测试统计汇总

| 项目 | 结果 |
|---|---|
| WP1 Regression 专项 | 13 passed |
| WP1 影响面回归 | 101 passed |
| WP2 Fencing 并发专项 | 7 passed |
| WP2 全量 | 791 passed / 16 skipped |
| WP3 SSE / RBAC 专项 | 13 passed |
| WP3 全量 | 804 passed / 16 skipped |
| WP4 全血缘 UI | 40/40 PASS + 13 截图 |
| WP5 Benchmark | 46 执行 / 38 样本 / 合同 5/5 |
| WP5 AI 质量 | n=32，规则 0.9688 / LLM 0.8750（细粒度） |
| 最终全量 | 805 passed / 16 skipped |
| PG16 专项 / Fencing | 16 passed / 9/9 PASS |
| Ruff / 前端 build / diff check | 全部通过 |

## 11. 修改文件与提交

| 提交 | 主题 |
|---|---|
| `55d1e5a` | RUN-014 WP1：真实 Regression 阶段与生命周期语义修正 |
| `8cba090` | RUN-014 WP2：租约过期即失效与 fencing 守卫下的原子写入 |
| `02c4b0a` | RUN-014 WP3：SSE 活跃连接撤权、全局接口 RBAC 与统计口径 |
| `52617d1` | RUN-014 WP4：单一业务资产全血缘 UI 验收 40/40 |
| `a1b3fe8` | RUN-014 WP5：Benchmark v5 23x2 真跑与 AI 质量 n=32 |
| `cf05558` | RUN-014 WP6/WP7：门禁证据与文档状态（16 文件 / +867 -134） |
| 本报告提交 | RUN-014 FINAL：最终报告与收口口径修订 |

主要改动类别：执行内核（Regression / Pipeline / Fencing）、API（SSE / RBAC / 统计）、前端（生命周期七段、回归面板）、脚本（全血缘、Benchmark、AI 评估、长稳参数化、PG fencing 验证）、测试（Regression / Fencing / SSE-RBAC 专项）、CI（`pr-gate.yml` 纳入 `workbuddy/run014-*`）、文档（BASELINE / WORKLOG / WP1~WP5 / 本报告 / README / FEATURE-STATUS）、证据（`docs/assets/run014/*`）。

## 12. 剩余缺陷、BLOCKED 与待决策

| 项 | 状态 |
|---|---|
| B-17 Worker 四场景故障注入 | BLOCKED（历史安全拦截，未绕过） |
| 宿主级网络隔离、外部真实站点 | BLOCKED（无容器/授权） |
| 真实人工诊断基线 | BLOCKED（无人工评审资源） |
| LLM 四臂成本实验 | NOT_RUN（未获额外计费授权） |
| Nightly / Release Gate | NOT_RUN（未触发） |
| RUN-014 60 分钟长稳 | NOT_RUN（无汇总证据，待重跑） |
| Benchmark False Pass 证据级别 | PARTIAL（缺 `fault_state` 快照） |
| `PROMOTION_FAILED` 重发晋升入口 | NEEDS_DECISION（状态机允许，无 API/UI） |
| `/system/config` 是否收紧到 `system.observe` | NEEDS_DECISION（当前 read，不含密钥） |
| P03 Oracle 多来源冲突裁决 / P08 审批角色矩阵 | NEEDS_DECISION（未改契约） |
| LICENSE、PR #1 / #2 集成策略 | NEEDS_DECISION |

## 13. 收口修正说明（本轮诚实修订）

1. `docs/RUN-014-WORKLOG.md` 原把 60 分钟长稳写成"见最终报告（独立库 `traceguard_run014_soak`）"，实际只有约 4 分钟 Worker 日志、无汇总 JSON；已在 WORKLOG 与本报告改为 **NOT_RUN / INCONCLUSIVE**。
2. WORKLOG 原称 `.workbuddy-run/*` 已全部推进到 RUN-014；实际这些被 `.gitignore` 忽略的状态文件仍保留 RUN-011-R1.1 及更早历史。已修正 WORKLOG 表述；权威状态以本报告和 RUN-014 专题为准。
3. README / FEATURE-STATUS 此前引用尚不存在的 `docs/RUN-014-FINAL-REPORT.md`；本报告补齐后引用成立。

## 14. 下一阶段建议

1. 在独立宿主窗口重跑 60 分钟 PG16 长稳（`TG_SOAK_PG_DB=traceguard_run014_soak`），落库 `fault_state` 与完整汇总后再标 PASS。
2. 扩展 Benchmark 样本并为故障激活状态落库，把 False Pass 从 PARTIAL 升级为 VERIFIED。
3. 补 LLM 四臂成本实验与真实人工基线（需授权与评审资源）。
4. 触发 Nightly / Release Gate，核实本轮代码在三级门禁下的完整表现。
5. 决策 `PROMOTION_FAILED` 重发入口、`/system/config` 权限边界与 LICENSE。
