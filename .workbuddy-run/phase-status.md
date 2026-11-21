# TraceGuard 十四阶段与六里程碑验收状态

状态枚举：NOT_STARTED / IN_PROGRESS / BLOCKED / READY_FOR_REVIEW / ACCEPTED / REJECTED
规则：只有具备真实验收证据（测试日志、运行结果）的 ACCEPTED 才代表阶段完成；代码写完 ≠ ACCEPTED。

- 最后更新：2026-11-21 23:45

## 五层 / 十四阶段

| 层 | 阶段 | 名称 | 状态 | 验收证据 | 备注 |
|---|---|---|---|---|---|
| 1 | 0 | 工程初始化与设计基线 | IN_PROGRESS | 待产出 | 正在实施 |
| 1 | 1 | TeamFlow 最小被测应用与故障注入 | NOT_STARTED | — | 前置：阶段0 |
| 2 | 2 | 专业 Web UI 自动化测试框架 | NOT_STARTED | — | 前置：阶段1 |
| 2 | 3 | 任务调度、执行编排与测试管理工作台 | NOT_STARTED | — | 前置：阶段2 |
| 3 | 4 | Event 与 Evidence 证据基础设施 | NOT_STARTED | — | 前置：阶段3 |
| 3 | 5 | Explorer Agent 自主 Web 探索 | NOT_STARTED | — | 前置：阶段4 |
| 3 | 6 | TestCase Compiler 智能用例编译器 | NOT_STARTED | — | 前置：阶段5 |
| 4 | 7 | 基础故障分类与 Diagnosis v1 | NOT_STARTED | — | 前置：阶段6 |
| 4 | 8 | Evidence Correlation Graph | NOT_STARTED | — | 前置：阶段7 |
| 4 | 9 | Active Diagnosis Planner | NOT_STARTED | — | 前置：阶段8 |
| 4 | 10 | Oracle-Protected Self-Healing | NOT_STARTED | — | 前置：阶段9 |
| 5 | 11 | UI-FaultBench 完整 Benchmark | NOT_STARTED | — | 前置：阶段10 |
| 5 | 12 | 外部 Web 泛化、安全与可靠性 | NOT_STARTED | — | 前置：阶段11；需外部应用授权 |
| 5 | 13 | 全链路集成、最终验收与交付 | NOT_STARTED | — | 前置：阶段12 |

## 六里程碑

| 里程碑 | 名称 | 含阶段 | 状态 | 退出条件 |
|---|---|---|---|---|
| M0 | 工程环境与 TeamFlow 可重复运行 | 0,1 | NOT_STARTED | 阶段0/1 ACCEPTED |
| M1 | 专业 UI 自动化测试平台完成 | 2,3 | NOT_STARTED | 阶段2/3 ACCEPTED |
| M2 | 证据基础设施与 Agentic Testing 完成 | 4,5,6 | NOT_STARTED | 阶段4/5/6 ACCEPTED |
| M3 | Evidence Graph 与智能诊断完成 | 7,8,9 | NOT_STARTED | 阶段7/8/9 ACCEPTED |
| M4 | 安全测试自愈完成 | 10 | NOT_STARTED | 阶段10 ACCEPTED |
| M5 | Benchmark、泛化、安全与最终交付 | 11,12,13 | NOT_STARTED | 阶段11/12/13 ACCEPTED |

## C01～C12 契约状态

| 契约 | 名称 | Schema 文件 | 状态 |
|---|---|---|---|
| C01 | TestIntent | 待创建 | NOT_STARTED |
| C02 | TestCase | 待创建 | NOT_STARTED |
| C03 | Oracle | 待创建 | NOT_STARTED |
| C04 | Execution | 待创建 | NOT_STARTED |
| C05 | Event | 待创建 | NOT_STARTED |
| C06 | Evidence | 待创建 | NOT_STARTED |
| C07 | Evidence Graph | 待创建 | NOT_STARTED |
| C08 | Diagnosis | 待创建 | NOT_STARTED |
| C09 | Probe | 待创建 | NOT_STARTED |
| C10 | Disposition | 待创建 | NOT_STARTED |
| C11 | Repair | 待创建 | NOT_STARTED |
| C12 | Benchmark | 待创建 | NOT_STARTED |
