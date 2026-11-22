# TraceGuard 持续开发任务台账

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
| T-02 | 统一持久化层（SQLite/PG 可切换）+ 迁移 | IN_PROGRESS | P0 | T-01 | `packages/persistence` | 事件/证据/Run 重启后仍可读；唯一约束生效 | 待补 |
| T-03 | 组合根（服务容器）与配置 | TODO | P0 | T-02 | `api/runtime.py` | 所有模块由容器构造，单例可替换 | — |
| T-04 | 真实浏览器执行引擎（BrowserManager） | TODO | P0 | T-01 | `execution/browser/` | 真实启动 Chromium/Edge，Context 隔离，异常释放 | — |
| T-05 | PlaywrightObserver 强化（DOM/截图/console/network/trace） | TODO | P0 | T-04 | `runtime/playwright_observer.py` | TeamFlow 真实点击/填表/断言成功，证据落盘 | — |
| T-06 | Run 执行器 + 事件/证据写入 | TODO | P0 | T-02,T-03,T-05 | `runtime/pipeline.py` | `POST /runs/{id}/execute` 真实执行并产出报告 | — |
| T-07 | Worker 进程（租约/心跳/fencing/回收/优雅退出） | TODO | P1 | T-06 | `apps/workers` | 独立进程消费 QUEUED Run；崩溃后可被重新领取 | — |
| T-08 | 完整 REST API（intent/testcase/oracle/evidence/graph/diagnosis/probe/repair/benchmark） | TODO | P0 | T-03 | `api/routers/*` | OpenAPI 覆盖全部资源，真实读写 | — |
| T-09 | 事件/证据持久化适配器 | TODO | P0 | T-02 | `persistence/event_store.py` 等 | 与内存实现语义一致 + 重启可读 | — |
| T-10 | Artifact Store（本地 + S3/MinIO 可切换）+ 完整性校验 | TODO | P1 | T-02 | `persistence/artifacts.py` | 截图/HTML/Trace 上传下载，sha256 校验 | — |
| T-11 | 前端工程化骨架（router/pinia/API client/布局） | TODO | P0 | T-08 | `apps/web/src/**` | 路由可用，真实 API 数据，无 Mock | — |
| T-12 | 前端业务页面（Intent/TestCase/Run/Evidence/Graph/Diagnosis/Repair/Benchmark） | TODO | P0 | T-11 | 同上 | 每个页面有 loading/error/empty 状态 | — |
| T-13 | 全链路 Pipeline（Intent→…→Benchmark） | TODO | P0 | T-06,T-08 | `pipeline` 服务 + 集成测试 | 一次调用串起全链路，ID 关联一致 | — |
| T-14 | UI-FaultBench 真实实验执行器 | TODO | P1 | T-13 | `benchmark/runner.py` | 真实跑 TeamFlow 场景并输出带分子分母的指标 | — |
| T-15 | LLM 基线 | BLOCKED | P2 | T-14 | — | 无 API Key / 无本地模型，不伪造结果 | 见 blockers |
| T-16 | 安全与可靠性测试（并发/重复/中断/重启） | TODO | P1 | T-02,T-07 | tests | 幂等、租约回收、重启恢复有真实断言 | — |
| T-17 | P03 Oracle 多来源裁决决策文档 | NEEDS_REVIEW | P2 | — | `docs/decisions/P03-*.md` | 提供方案与风险，不擅自冻结 | — |
| T-18 | P08 Repair 审批角色矩阵 | NEEDS_REVIEW | P2 | — | `docs/decisions/P08-*.md` | 角色矩阵 + 推荐，不擅自认定权限 | — |
| T-19 | 文档与交付报告更新（真实状态） | TODO | P1 | 全部 | `docs/**` | 区分已运行/仅代码/Mock/阻塞 | — |

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
