# TraceGuard 持续开发任务台账

> **RUN-010 台账（2026-11-24）**：10 个工作包全部完成（15 提交、66 文件、+7,736 行）。
> 真实新增：认证产品闭环、Egress 内核集成、提交回执幂等、诊断 v2、Benchmark v4 执行框架、
> axe 审查、依赖一致性测试。真实缺陷修复 10 项（见 FINAL-REPORT §3）。
> BLOCKED 项与生产风险如实记录；PR #2 保持 HOLD 待负责人决策。以下历史台账保留。

> **RUN-009 WP0 状态更正（2026-11-24 16:05 CST）**：下方 RUN-002~RUN-008 条目为历史台账。
> 当前 HEAD `cb80591`；RUN-008 实际结论：WP4/WP7 VERIFIED、WP8 完成，R8-08/R8-11/R8-12 原标「进行中」已过时；
> WP3/WP6 仍是 PARTIAL，B-17/LLM/Benchmark LLM 与人工基线 BLOCKED，认证/RBAC NOT_IMPLEMENTED。
> 权威基线见 `docs/RUN-009-BASELINE.md`，复核见 `docs/RUN-009-REVIEW-REPORT.md`。历史条目保留不覆盖。

## RUN-009 任务台账（2026-11-24，权威）

| 编号 | 任务 | 状态 | 证据 |
|---|---|---|---|
| R9-01 | WP0 状态复核 + PR 集成 | PASS | `docs/RUN-009-BASELINE.md`、`docs/RUN-009-REVIEW-REPORT.md`、PR #2（open/HOLD） |
| R9-02 | WP1 14/14 路由验收 | PASS（功能）/ PARTIAL（axe 缺失） | `docs/RUN-009-WP1-FRONTEND-REPORT.md`、154 张截图、`route-coverage.json` |
| R9-03 | WP2 认证/RBAC/CSRF/审计 | PASS | `docs/RUN-009-WP2-AUTH-RBAC.md`、9 个安全用例 + 全量回归 |
| R9-04 | WP3 受控出站代理 | PARTIAL（宿主级封锁 BLOCKED） | `docs/RUN-009-WP3-NETWORK-SECURITY.md`、17/17 检查、未授权命中 0 |
| R9-05 | WP4 Worker 故障矩阵 | PARTIAL（4 场景 BLOCKED/B-17） | `docs/RUN-009-WP4-WORKER-FAULT-MATRIX.md`、契约测试 4/4 |
| R9-06 | WP5 资源监控与性能基线 | PASS | `docs/RUN-009-WP5-PERFORMANCE.md`、33 帧真实采样 |
| R9-07 | WP6 诊断增益研究 | PASS（含明确负结果） | `docs/RUN-009-WP6-AGENT-DIAGNOSIS.md` |
| R9-08 | WP7 Benchmark v4 | PASS（LLM/人工臂 BLOCKED） | `docs/RUN-009-WP7-BENCHMARK.md`、2 轮原始样本 |
| R9-09 | WP8 供应链治理 | PARTIAL（升级 NEEDS_DECISION） | `docs/RUN-009-WP8-SUPPLY-CHAIN.md`、SBOM 三份 |
| R9-10 | WP9 最终集成与交付 | 见 `docs/RUN-009-FINAL-REPORT.md` | — |

> 生成时间：2026-11-22（RUN-002）
> 依据：工作区真实代码 + `资料/` 设计资料 + `.workbuddy-run/` 历史记录。
> 状态枚举：TODO / IN_PROGRESS / PASS / FAIL / BLOCKED / NEEDS_REVIEW
> 原则：代码存在 ≠ 功能完成；只有真实运行证据才可标 PASS。

## 0. 真实基线（审计结论）

| 项目 | 报告声称 | 实际核查 | 判定 |
|---|---|---|---|
| 自动化测试 | 178 通过 | 实测 `pytest -q` → 178 passed / 3.8s | PASS（基线可信） |
| C01～C12 契约 | 12 组 | `contracts/models.py` 仅有模型定义 | FAIL：`schemas/` 目录为空，无导出 JSON Schema |
| JSON Schema | 22 个 | `packages/contracts/traceguard_contracts/schemas/` 不存在 | FAIL |
| 后端 API | 完整 | 仅 `/health` + Task/Run/Step 8 个端点；无执行触发、无证据/诊断/修复/Benchmark API | FAIL |
| 前端工作台 | Vue3 工作台 | `App.vue` 45 行，仅展示 health JSON | FAIL |
| 数据持久化 | 部分完成 | 仅 TaskService 落 SQLite；Event/Evidence/Graph/Diagnosis/Repair 全部进程内内存字典 | FAIL |
| Worker | 预留 | 无任何 worker 进程代码 | FAIL |
| Playwright 真浏览器 | 未验证 | 依赖未安装；本次已装 playwright 1.65.2，Edge/Chromium 可启动 | IN_PROGRESS |
| 执行触发链路 | — | `POST /tasks/{id}/runs` 只建 QUEUED 记录，无任何执行者消费 | FAIL |
| Benchmark | 引擎+口径 | 有指标口径测试，无真实实验执行器 | FAIL（部分） |

结论：项目是"模块级原型 + 单元测试齐全"，但**产品链路断裂**：没有执行者、没有持久化、没有可用前端、没有真实浏览器证据。
本轮工作按"先打通可运行的最小产品闭环，再横向扩展"推进。

## 1. 任务清单

| 编号 | 任务 | 状态 | 优先级 | 依赖 | 产物 | 验收标准 | 证据 |
|---|---|---|---|---|---|---|---|
| T-01 | 真实项目审计与任务台账 | PASS | P0 | — | 本文件 | 逐项对照代码核实 | 本文件 §0 |
| T-02 | 统一持久化层（SQLite/PG 可切换）+ 迁移 | PASS | P0 | T-01 | `packages/persistence` | 事件/证据/Run 重启后仍可读；唯一约束生效 | 待补 |
| T-03 | 组合根（服务容器）与配置 | PASS | P0 | T-02 | `api/runtime.py` | 所有模块由容器构造，单例可替换 | — |
| T-04 | 真实浏览器执行引擎（BrowserManager） | PASS | P0 | T-01 | `execution/browser/` | 真实启动 Chromium/Edge，Context 隔离，异常释放 | — |
| T-05 | PlaywrightObserver 强化（DOM/截图/console/network/trace） | PASS | P0 | T-04 | `runtime/playwright_observer.py` | TeamFlow 真实点击/填表/断言成功，证据落盘 | — |
| T-06 | Run 执行器 + 事件/证据写入 | PASS | P0 | T-02,T-03,T-05 | `runtime/pipeline.py` | `POST /runs/{id}/execute` 真实执行并产出报告 | — |
| T-07 | Worker 进程（租约/fencing/回收/心跳/优雅退出） | PASS | P1 | T-06 | `apps/workers` | 独立进程消费 QUEUED Run；崩溃后可被重新领取 | — |
| T-08 | 完整 REST API（intent/testcase/oracle/evidence/graph/diagnosis/probe/repair/benchmark） | PASS | P0 | T-03 | `api/routers/*` | OpenAPI 覆盖全部资源，真实读写 | — |
| T-09 | 事件/证据持久化适配器 | PASS | P0 | T-02 | `persistence/event_store.py` 等 | 与内存实现语义一致 + 重启可读 | — |
| T-10 | Artifact Store + 完整性校验 | PASS（本地已实现；S3 未实现） | P1 | T-02 | `persistence/artifacts.py` | 截图/HTML/Trace 上传下载，sha256 校验 | — |
| T-11 | 前端工程化骨架（router/API client/布局） | PASS | P0 | T-08 | `apps/web/src/**` | 路由可用，真实 API 数据，无 Mock | — |
| T-12 | 前端业务页面 | PASS | P0 | T-11 | 同上 | 每个页面有 loading/error/empty 状态 | — |
| T-13 | 全链路 Pipeline（Intent→…→Benchmark） | PASS | P0 | T-06,T-08 | `pipeline` 服务 + 集成测试 | 一次调用串起全链路，ID 关联一致 | — |
| T-14 | UI-FaultBench 真实实验执行器 | PASS（A/B/C） | P1 | T-13 | `benchmark/runner.py` | 真实跑 TeamFlow 场景并输出带分子分母的指标 | — |
| T-15 | LLM 基线 | BLOCKED | P2 | T-14 | — | 无 API Key / 无本地模型，不伪造结果 | 见 blockers |
| T-16 | 安全与可靠性测试 | PASS（长稳压测未做） | P1 | T-02,T-07 | tests | 幂等、租约回收、重启恢复有真实断言 | — |
| T-17 | P03 Oracle 多来源裁决决策文档 | NEEDS_REVIEW | P2 | — | `docs/decisions/P03-*.md` | 提供方案与风险，不擅自冻结 | — |
| T-18 | P08 Repair 审批角色矩阵 | NEEDS_REVIEW | P2 | — | `docs/decisions/P08-*.md` | 角色矩阵 + 推荐，不擅自认定权限 | — |
| T-19 | 文档与交付报告更新（真实状态） | PASS | P1 | 全部 | `docs/**` | 区分已运行/仅代码/Mock/阻塞 | — |

## 2. 已知设计约束（不可破坏）

1. Agent 不得自行批准正式 Oracle（`OracleSpec.approved_by_agent: Literal[False]`）。
2. LLM 不得宣布根因 CONFIRMED；INCONCLUSIVE ≠ PASSED。
3. Self-Healing 不得削弱正式 Assertion；Repair 需 CAS 版本保护。
4. `(producer_instance_id, sequence)` 唯一；`(producer_id, idempotency_key)` 幂等，同键异载荷拒绝。
5. 真实浏览器模式与 HTTP 降级模式必须在报告中可区分，不得伪装。
6. Ground Truth 隔离，仅评测侧可读。
7. 外部真实网站未授权（P-EXT），仅本地受控目标。

## 3. 阻塞项

| 编号 | 阻塞 | 影响 | 处理 |
|---|---|---|---|
| B-01 | 无 LLM API Key / 无本地大模型 | 实验 A/B/C 的 LLM 基线无法产出真实数值 | 实现可插拔基线接口 + 真实规则基线；LLM 基线标记 BLOCKED，不伪造 |
| B-02 | 本机无 Docker / PostgreSQL 服务 | 无法实测 PG 集群行为 | 持久层保持 PG 方言兼容 + 提供 compose；以 SQLite 实测 + 单测覆盖 PG 生成 SQL |
| B-03 | 外部应用泛化未授权（P-EXT） | 阶段12 真实外部站点验证 | 仅本地受控站点/TeamFlow |
| B-04 | P03/P08/路线冻结需负责人批准 | 契约冻结 | 输出决策文档，不擅自冻结 |

## 4. RUN-004-FE 新增条目（2026-11-23，追加）

| 编号 | 任务 | 状态 | 证据 |
|---|---|---|---|
| T-20 | 统一设计系统与 AppShell（Token / 图标 / 组件 / 移动抽屉） | PASS | `RUN-004-FE-IMPLEMENTATION-REPORT.md`、15 张截图 |
| T-21 | Explorer 真实 LLM 信息展示（engine / llm_stats / 三类决策来源） | PASS | 浏览器检查 `explorer-engine`、`explorer-llm-stats`、`explorer-no-hardcoded-rule-text` |
| T-22 | 模型运行中心与只读汇总 API | PASS | `/models`、`GET /system/llm/summary`、2 项新增测试 |
| T-23 | Dashboard 真实时间分桶趋势与四态统计口径 | PASS | `dashboard.png`、`sample-strip` 样本量 |
| T-24 | Diagnosis / Graph / Repair / Benchmark 展示升级 | PASS | 53/53 浏览器检查 |
| T-25 | 响应式与深色主题 | PASS | 六档断点无溢出；12 路由深色无浅色残留 |

状态说明：B-01 的历史描述保留为 RUN-002 时点快照；LLM 基线已由 RUN-003-G 解锁，B-01 不再适用于当前状态。

---

## RUN-005-CORE 台账（2026-11-23）

| ID | 任务 | 状态 | 证据 |
|---|---|---|---|
| R5-01 | ProbeBudget 生命周期重构（按 Run/Diagnosis） | VERIFIED | `tests/unit/test_probe_budget.py`(15)、`tests/integration/test_probe_service.py`(6) |
| R5-02 | Active Planner 真实决策闭环 | VERIFIED | plan `probeplan_…`；2 探针 + 2 证据 |
| R5-03 | 真实 SSE 事件流 + 前端订阅/降级 | VERIFIED | `tests/integration/test_sse.py`(7)、`docs/assets/run005/ui-acceptance.json` |
| R5-04 | Consumer Inbox 持久化状态机 | VERIFIED | `tests/unit/test_inbox_persistence.py`(9) |
| R5-05 | Inbox 真实消费者（Run 时间线投影） | VERIFIED | `tests/integration/test_timeline_projection.py`(3) |
| R5-06 | Event Store 多进程并发修复 | VERIFIED | `tests/unit/test_event_concurrency.py`(4)，4 进程 |
| R5-07 | LLM 编译器三模式 + Oracle 保护 | PARTIAL（顾问真实冒烟通过，完整链路未跑） | `tests/unit/test_llm_advisors.py`(10)、`llm-advisor-smoke.json` |
| R5-08 | 候选稳定性回放 F08 | PARTIAL（编排 VERIFIED，真实多次回放未跑） | `tests/integration/test_stability_replay.py`(7) |
| R5-09 | LLM 探针规划 | VERIFIED | 闭环中 `llm_planner.called=true` |
| R5-10 | LLM 修复规划 | VERIFIED | 3 条受限变更（真实调用） |
| R5-11 | 三类 Agent 可观测性 | VERIFIED | `/api/v1/system/llm/summary` |
| R5-12 | Benchmark v2 复验 | VERIFIED | `.workbuddy-run/bench-v2-verify.json` 30/30 |
| R5-13 | Benchmark v3 多臂 | BLOCKED | `docs/RUN-005-BENCHMARK-REPORT.md` |
| R5-14 | CI 三级门禁 | VERIFIED（文件）/未在真实 CI 跑 | `.github/workflows/{pr,nightly,release}-gate.yml` |
| R5-15 | 前端自动化验收 | VERIFIED | `scripts/ui_acceptance.py` 30/30 + 18 截图 |
| R5-16 | PostgreSQL parity 测试 | PARTIAL（本地 BLOCKED） | `tests/integration/test_postgres_parity.py`(5 skip) |
| R5-17 | 60 分钟长稳 | BLOCKED | 未执行 |
| R5-18 | P03/P08 决策包 | NEEDS_DECISION | `docs/RUN-005-DECISION-PACKAGE.md` |

---

## RUN-006 台账（2026-11-23）

> 口径：IMPLEMENTED / VERIFIED / PARTIAL / BLOCKED / NEEDS_DECISION / NOT_IMPLEMENTED。
> 起点 285 passed / 5 skipped → 本轮 367 passed / 5 skipped。

| ID | 任务 | 状态 | 证据 |
|---|---|---|---|
| R6-01 | 基线审计（逐项标注证据等级） | PASS | `docs/RUN-006-BASELINE.md` |
| R6-02 | Oracle Binding API + required assertion 审核门禁 | VERIFIED | `tests/integration/test_oracle_binding_gate.py`(26)；真实 API 412/403/200 |
| R6-03 | Oracle 版本固定到 TestCaseVersion（历史复现） | VERIFIED | `test_historical_case_uses_pinned_oracle_version`；pipeline 按 pinned 加载 |
| R6-04 | 审核主体类型校验（agent:*/llm:*/system:*） | VERIFIED | 10 类非人类主体参数化拒绝；403 |
| R6-05 | 并发审核 CAS（review_revision + 迁移 v7） | VERIFIED | `test_stale_review_revision_is_rejected` |
| R6-06 | 探针证据回灌假设 + 动态重诊断 | VERIFIED（语义层） | `tests/integration/test_active_diagnosis_feedback.py`(11) |
| R6-07 | 冲突真实计算 + STOP_CONFLICT_UNRESOLVED | VERIFIED | 同上；真实 plan `unresolved_conflicts` 字段 |
| R6-08 | 探针失败不当目标故障证据 | VERIFIED | `test_probe_failure_is_not_target_failure_evidence` |
| R6-09 | LLM 假设不得 CONFIRMED（前缀漏洞修复） | VERIFIED | 3 类 `suggested_by` 参数化；真实诊断中 LLM 假设为 SUPPORTED |
| R6-10 | Evidence Graph 增加 ProbeExecution 节点/边 | VERIFIED | 真实图 60 节点/81 边含 2 探针节点 |
| R6-11 | ProbeBudget 故障安全（读/写失败即阻断） | VERIFIED | `tests/unit/test_probe_budget_failsafe.py`(9) |
| R6-12 | 预算跨进程 CAS 一致性 | PARTIAL（多 Registry 验证；未做双 OS 进程压测） | `test_two_processes_share_one_budget_without_overspend` |
| R6-13 | Self-Healing 晋升/回滚原子 CAS | VERIFIED（SQLite） | `tests/integration/test_repair_atomic_cas.py`(9) |
| R6-14 | 统一目标域名策略（Explorer + Runner） | VERIFIED | `tests/unit/test_target_policy.py`(18) |
| R6-15 | 请求级网络管控（真实浏览器） | VERIFIED（子资源/XHR） | e2e：未授权服务端 0 次请求 |
| R6-16 | 顶层 302 越权落地拒绝 | PARTIAL（拒绝继续，但请求已出站） | `test_redirect_landing_on_unsanctioned_origin_is_refused` |
| R6-17 | 真实环境正常/故障链路 | VERIFIED（单场景） | `docs/assets/run006/real-env-run-summary.json` |
| R6-18 | 完整端到端全链（含修复晋升回归） | PARTIAL | 未在本轮走完 |
| R6-19 | 前端诊断视图（冲突/回灌/闭环） | VERIFIED（仅该视图） | `DiagnosisDetailView` + `types.ts`；30/30 UI 验收；4 断点 + 深色截图 |
| R6-19b | 前端其余 13 视图体验审查 | NOT_IMPLEMENTED（本轮） | 未审查 |
| R6-20 | PostgreSQL 实测 / 4 Worker / 60 分钟长稳 | BLOCKED | 环境与时间窗限制 |
| R6-21 | CI 三级门禁真实运行 | BLOCKED | 未触发真实 Actions |
| R6-22 | UI-FaultBench 深化（30 场景多臂） | NOT_IMPLEMENTED（本轮） | 不引用历史数字 |
| R6-23 | 稳定性回放 F08 完善 | PARTIAL（继承 RUN-005） | 未改动 |

## RUN-007-RESUME 任务清单（2026-11-23，追加；历史条目不改写）

| 编号 | 任务 | 状态 | 证据 | 备注 |
|---|---|---|---|---|
| R7-01 | 基线审计（origin/main = `ac2876e`） | PASS | `docs/RUN-007-BASELINE.md` | fetch 成功，远程 HEAD 与指令基线一致 |
| R7-02 | WP1 五项修复复核 | PASS | 163 passed / 0 failed | 见 TEST-REPORT §3 |
| R7-03 | 迁移 v8 幂等 + 旧库升级 | PASS | `test_migration_v8.py`(4) | 修复前并发用例失败 → 已修 `db.py` |
| R7-04 | E2E 改独立 Worker 消费队列 | PASS | `e2e-worker-run007.json` 55 步 | 不覆盖旧 `e2e-run007.json` |
| R7-05 | Evidence 来源修正 + 图绑定校验 | PASS | 18/28 条证据 + 图节点/边 | 不再读空的 `report.evidence_ids` |
| R7-06 | 沙箱改队列（fail-closed） | PASS | `test_sandbox_worker_queue.py`(2) | 无 Worker → PRECONDITION_FAILED |
| R7-07 | Run 级状态缓存隔离 | PASS | `test_pipeline_state_cache.py`(2) | 修复真实假通过漏检 |
| R7-08 | 前端 15 视图 × 4 断点 + 深色 + 四态 | PASS | `ui/ui-review.json` 75/75，79 图 | 修 3 处真实视觉缺陷 |
| R7-09 | Worker 故障恢复实验（重写脚本） | PASS | `worker-fault-recovery-verified.json` | 真实计时/同 Run 接管/陈旧代次拒绝 |
| R7-10 | 60 分钟长稳（重写脚本） | PASS | `soak-60min.json` + `soak-samples.jsonl` | 独立 API + 4 Worker + SSE 续传 |
| R7-11 | PostgreSQL 16 扩展验证 | **PASS（CI）** | `test_postgres_platform.py`(6)，Nightly `postgres:16`（16.16） | 本机无 PG 仍 skip；CI 已真跑 |
| R7-12 | CI 三级门禁修复 | **PASS** | PR/Nightly/Release 同 commit `cc3339d` 全绿；`ci-gates.json` | 首跑暴露 8 个缺陷，4 轮修完 |
| R7-13 | UI-FaultBench v3 四臂 | PASS | `docs/RUN-007-BENCHMARK-REPORT.md` | 30 场景 + Flaky×5 |
| R7-14 | LLM 对照臂（预算内子集） | PASS（子集） | `llm-arm-*.json` | 模型由路由决定，按实际记录；不外推 |
| R7-15 | 七份 RUN-007 报告 + README/台账 | PASS | `docs/RUN-007-*.md` | 状态枚举统一 |
| R7-16 | 分支推送 + PR | **PASS** | PR #1 → `main@ac2876e`，head `cc3339d` | token 由用户临时提供；用完即撤销 |

## RUN-008 任务清单（2026-11-24，追加；历史条目不改写）

| 编号 | 任务 | 状态 | 证据 | 备注 |
|---|---|---|---|---|
| R8-01 | 基线审计与起点状态恢复 | PASS | `docs/RUN-008-BASELINE.md` | 分支/提交/证据逐项核对，不照抄旧快照 |
| R8-02 | WP1 P0 复核 + 长稳脚本守卫 | PASS | 16/16 回归；`wp1/` 三场景真实 PID/SIGKILL | 起点已修，以真实执行验证而非重复改坏 |
| R8-03 | WP2 本机全量回归 | PASS | 567 passed / 13 skipped；E2E 12/12；Ruff 全绿 | 本机 PG parity skip 不写 passed |
| R8-04 | WP2 三级 CI 真实运行 | PASS | PR `45335765297` / Nightly `45335777381` / Release `45336007166` 全 success（commit `40efee6`） | LLM smoke BLOCKED 不冒充通过；错误输入 Release `45331793408` 失败是 fail-closed 正确行为 |
| R8-05 | WP3 4 Worker 并发 | PASS | `worker-concurrency.json` 峰值 4、14/14 | 真实 OS 进程 |
| R8-06 | WP3 执行中途 SIGKILL 故障恢复 | PASS | `worker-fault-recovery.json` 17/17 | 2.77s 时真实 kill PID 80665 |
| R8-07 | WP3 补充故障矩阵 4 场景 | BLOCKED | `blockers.md` B-17 | AgentDR 两次拦截（含白名单重写）；不重试不绕过 |
| R8-08 | WP4 60 分钟长稳 | 进行中 | `BZ1Hp8` 每 60s 采样，全部 PASSED | 报告生成前不标 PASS |
| R8-09 | WP5 前端 14 视图审查 | PASS | 60 checks 全过、64 截图；`fix-verification.json` 4/4 | 修复 5 个真实缺陷 + 假深色断言 |
| R8-10 | WP6 安全主体门禁与脱敏修复 | PASS | `test_healing.py` 17/17、`test_core.py` 36/36 | 其余为已披露设计边界 |
| R8-11 | WP7 Benchmark v3 本轮实验 | 进行中 | 隔离 API/Worker/TeamFlow，2 次重复 | 完成前不引用历史数字 |
| R8-12 | WP8 文档、决策与交付 | 进行中 | `RUN-008-FINAL-REPORT.md` 草稿、`RUN-008-DECISIONS.md` | 实验收尾后定稿 |
