# TraceGuard 阶段与里程碑验收状态（RUN-002 复核）

状态枚举：NOT_STARTED / IN_PROGRESS / BLOCKED / READY_FOR_REVIEW / ACCEPTED
规则：只有具备真实验收证据（测试日志、运行结果）才可 ACCEPTED；代码写完 ≠ 完成。
最后更新：2026-11-22 13:40

## 五层 / 十四阶段

| 层 | 阶段 | 名称 | 状态 | 本轮证据 |
|---|---|---|---|---|
| 1 | 0 | 工程初始化与设计基线 | ACCEPTED | 22 组 Schema 实导出（+1 防漂移测试）；lint/format/build 全绿 |
| 1 | 1 | TeamFlow 与故障注入 | ACCEPTED | 9 测试 + 6 场景故障注入 + GT 隔离 |
| 2 | 2 | 专业 Web UI 自动化测试框架 | ACCEPTED | **真实 Chromium 4 场景**；PlaywrightObserver 重写；E2E 3 项 |
| 2 | 3 | 任务调度与工作台 | ACCEPTED | Task/Run 状态机 9 测试；**独立 Worker 实测**；前端 9 页 |
| 3 | 4 | Event 与 Evidence 基础设施 | ACCEPTED | 持久化 9 测试（并发/冲突/重启）；产物篡改检测 |
| 3 | 5 | Explorer Agent | ACCEPTED | 真实探索 3 动作 allowed；网关审计落库；API 入口 |
| 3 | 6 | TestCase Compiler | ACCEPTED | 轨迹→候选→审核门禁全链路；Benchmark A 实测执行 |
| 4 | 7 | Diagnosis v1 | ACCEPTED | 规则诊断 3/3 主因；False Pass 识别；5xx 优先修复 |
| 4 | 8 | Evidence Graph | ACCEPTED | 40 节点/39 边；快照持久化；前端 SVG 可视化 |
| 4 | 9 | Active Diagnosis Planner | ACCEPTED | 4 类只读探针真实执行；越权 BLOCKED；建议评分 |
| 4 | 10 | Oracle-Protected Self-Healing | ACCEPTED | 沙箱真实执行→审批→CAS 晋升→回归→回滚 全链路 |
| 5 | 11 | UI-FaultBench | ACCEPTED（部分基线 BLOCKED） | A/B/C 真实实验 + 分子分母 + Wilson CI；LLM/手工基线 BLOCKED |
| 5 | 12 | 外部泛化、安全与可靠性 | READY_FOR_REVIEW | 12 安全测试 + 越权/篡改/并发；外部泛化 BLOCKED(P-EXT) |
| 5 | 13 | 全链路集成与交付 | READY_FOR_REVIEW | API 集成 8 + 全链路 5 + 真实 E2E 3；交付文档已按真实状态重写 |

## 六里程碑

| 里程碑 | 含阶段 | 状态 | 依据 |
|---|---|---|---|
| M0 | 0,1 | ACCEPTED | 工程与 TeamFlow 可重复运行 |
| M1 | 2,3 | ACCEPTED | 真实浏览器执行 + 执行平台 + Worker |
| M2 | 4,5,6 | ACCEPTED | 证据持久化 + 探索 + 编译 |
| M3 | 7,8,9 | ACCEPTED | 诊断 + 图 + 探针 |
| M4 | 10 | ACCEPTED | 自愈全链路 |
| M5 | 11,12,13 | READY_FOR_REVIEW | Benchmark 真实运行；外部泛化阻塞；P03/P08 待决策 |

## C01～C12 契约状态

| 契约 | Schema | 状态 |
|---|---|---|
| C01 TestIntent | `C01_TestIntent.TestIntent.schema.json` | 已导出 |
| C02 TestCase | 4 个（Locator/Step/Assertion/TestCaseVersion） | 已导出 |
| C03 Oracle | `C03_Oracle.OracleSpec` | 已导出（`approved_by_agent` 强制 false） |
| C04 Execution | 4 个（Task/Run/Step/AssertionResult） | 已导出 |
| C05 Event | `C05_Event.EventEnvelope` | 已导出 |
| C06 Evidence | `C06_Evidence.Evidence` | 已导出 |
| C07 Evidence Graph | Node/Edge | 已导出 |
| C08 Diagnosis | Hypothesis/Diagnosis | 已导出 |
| C09 Probe | Definition/Execution | 已导出 |
| C10 Disposition | `C10_Disposition.Disposition` | 已导出 |
| C11 Repair | `C11_Repair.RepairProposal` | 已导出 |
| C12 Benchmark | Case/EvaluationResult | 已导出 |

共 22 个 schema 文件 + `manifest.json`，由 `tests/contracts/test_contracts.py::test_exported_schemas_are_present_and_consistent` 防漂移。

## 仍开放

- P03（Oracle 多来源裁决）/ P08（Repair 审批角色）：NEEDS_REVIEW，见 `docs/decisions/`。
- 路线图 v1.1：未冻结（无批准记录）。
- 外部应用泛化：BLOCKED（P-EXT 未授权）。
- LLM 基线：BLOCKED（无凭据）。

## RUN-004-FE 追加记录（2026-11-23）

- 以上 RUN-002 结论保留为历史快照。RUN-003-G 已完成真实 DeepSeek 接入；RUN-004-FE 完成前端产品化与模型运行监控。
- 新增验收：阶段 3（工作台）前端 14 视图 + 统一设计系统；阶段 11 Benchmark 前端对照与样本不足标识；模型运行监控 READY_FOR_REVIEW → 已有 53/53 浏览器检查与 224 项测试证据。
- P03 / P08 / 外部泛化状态不变。

## RUN-005 阶段状态（覆盖第 12、13 阶段推进）

| 阶段 | 状态 | 说明 |
|---|---|---|
| 0–11 | 维持历史 VERIFIED | 本轮未回退；关键路径回归通过 |
| 12（工程质量） | PARTIAL → 推进 | CI 三级门禁入库、前端验收脚本化、PG parity 测试；长稳/多 Worker 未复验 |
| 13（长期演进） | PARTIAL | LLM 三模块 + 稳定性回放 + 可观测性完成；Benchmark v3 与决策项未完成 |

---

## RUN-006 阶段/工作包状态（2026-11-23）

| 工作包 | 状态 | 依据 |
|---|---|---|
| WP1-01 Oracle 绑定与审核闭环 | **VERIFIED** | 26 项门禁测试 + 真实 API 412/403/200 |
| WP1-02 Active Diagnosis 闭环 | **VERIFIED（语义层）/ PARTIAL（真实故障链）** | 11 项集成测试 + 真实探针回灌证据 |
| WP1-03 预算故障安全与跨进程 | **VERIFIED（逻辑）/ PARTIAL（双 OS 进程）** | 9 项测试；CAS 冲突重载 |
| WP1-04 晋升/回滚原子 CAS | **VERIFIED（SQLite）** | 9 项测试，含并发仅一成功 |
| WP1-05 目标域名与网络管控 | **VERIFIED（子资源）/ PARTIAL（顶层 302）** | 18 单测 + 4 真实浏览器 e2e |
| WP2 稳定性回放 F08 | **PARTIAL（继承 RUN-005）** | 本轮未改 |
| WP2 完整端到端真实验收 | **PARTIAL** | 正常/故障场景真跑，修复全链未走完 |
| WP3 前端（诊断视图） | **VERIFIED（仅该视图）** | 冲突/回灌/闭环入口 + 390px 表格；30/30 UI 验收；4 断点无溢出 |
| WP3 前端（其余 13 视图） | **NOT_IMPLEMENTED（本轮）** | 未审查 |
| WP4 PostgreSQL / Worker / 长稳 | **BLOCKED** | 环境与时间窗 |
| WP5 CI 三级门禁真实运行 | **BLOCKED** | 未触发真实 Actions |
| WP6 UI-FaultBench 深化 | **NOT_IMPLEMENTED（本轮）** | 未追加实验 |

## RUN-007-RESUME 更新（2026-11-23）

状态枚举沿用：NOT_STARTED / IN_PROGRESS / BLOCKED / READY_FOR_REVIEW / ACCEPTED。

| 层 | 阶段 | 名称 | 状态 | 本轮证据 |
|---|---|---|---|---|
| 2 | 3 | 任务调度与工作台 | **ACCEPTED（强化）** | 独立 Worker 队列 E2E 55 步 0 失败；执行归属（Worker/fencing/租约）可见 |
| 3 | 4 | Event 与 Evidence 基础设施 | **ACCEPTED（强化）** | 证据改 `/runs/{id}/evidence` + 图绑定校验；长稳 845 事件无重复键 |
| 4 | 8 | Evidence Graph | **ACCEPTED（强化）** | 图 54/58 节点、70/76 边；前端自适应渲染修复裁切 |
| 4 | 10 | Oracle-Protected Self-Healing | **PARTIAL** | E2E 场景 D 全链通过；Benchmark 定位器场景晋升未闭环（0/1 回归），已如实记录 |
| 5 | 11 | UI-FaultBench | **READY_FOR_REVIEW（四臂已跑）** | A/C/D/E 30 场景 + LLM 两臂；D/E 无增益为负结论；人工基线 BLOCKED |
| 5 | 12 | 外部泛化、安全与可靠性 | **READY_FOR_REVIEW** | 60 分钟长稳 19/19、故障恢复 17/17；外部泛化仍 BLOCKED(P03) |
| 5 | 13 | 全链路集成与交付 | **READY_FOR_REVIEW** | 七份报告 + 证据索引；推送/PR 与 CI 触发 BLOCKED（无凭据） |
| — | — | 前端产品化（15 视图） | **ACCEPTED（视觉人工复核）** | 75/75 检查 + 79 张截图 + 四态；深色真实生效 |
