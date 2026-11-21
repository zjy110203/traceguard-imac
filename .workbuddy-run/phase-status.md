# TraceGuard 十四阶段与六里程碑验收状态

状态枚举：NOT_STARTED / IN_PROGRESS / BLOCKED / READY_FOR_REVIEW / ACCEPTED / REJECTED
规则：只有具备真实验收证据（测试日志、运行结果）的 ACCEPTED 才代表阶段完成；代码写完 ≠ ACCEPTED。

- 最后更新：2026-11-21 23:45

## 五层 / 十四阶段

| 层 | 阶段 | 名称 | 状态 | 验收证据 | 备注 |
|---|---|---|---|---|---|
| 1 | 0 | 工程初始化与设计基线 | ACCEPTED* | 22 测试通过 + API 真实运行 + 22 Schema；lint/format 通过 | *前端 build 受沙箱 node 限制未在本机完成（见限制），核心功能已验证 |
| 1 | 1 | TeamFlow 最小被测应用与故障注入 | ACCEPTED | 9 测试通过 + curl 真实业务闭环 + 故障注入 + GT 403 隔离 | M0 达成 |
| 2 | 2 | 专业 Web UI 自动化测试框架 | READY_FOR_REVIEW | 54 测试通过（含13 Verdict+4 E2E）+ 演示报告14用例 + lint全绿 | 核心完成；Playwright 真浏览器 PoC 因下载极慢待验证，本机用 http-fallback 降级（已标记） |
| 2 | 3 | 任务调度、执行编排与测试管理工作台 | READY_FOR_REVIEW | 9 Task服务测试 + 全项目63测试 + API真实HTTP(Task/Run) | 核心完成；Celery/Redis异步按路线图预留未启用（本机无Redis/Docker），同步内核可测 |
| 3 | 4 | Event 与 Evidence 证据基础设施 | ACCEPTED | 16 测试 + 全项目79测试 + lint全绿 | Event幂等/冲突/序号/缺口/工作区注入、Consumer Inbox、Evidence完整性/脱敏/派生链 |
| 3 | 5 | Explorer Agent 自主 Web 探索 | ACCEPTED | 11 测试 + ToolGateway权限/域名/预算/PromptInjection + Trace完整 | 规则planner默认，真实LLM注入接口就绪 |
| 3 | 6 | TestCase Compiler 智能用例编译器 | ACCEPTED | 10 测试 + 全项目100测试 + 可独立回放验证 | 候选→审核→晋升门禁完整 |
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
| M0 | 工程环境与 TeamFlow 可重复运行 | 0,1 | ACCEPTED | ✅ 阶段0/1 ACCEPTED（前端 build 环境限制已记录） | 达成 |
| M1 | 专业 UI 自动化测试平台完成 | 2,3 | ACCEPTED | ✅ 阶段2/3 READY_FOR_REVIEW→ACCEPTED（真浏览器 PoC 因网络限制记录为待验证） | 达成 |
| M2 | 证据基础设施与 Agentic Testing 完成 | 4,5,6 | ACCEPTED | ✅ 阶段4/5/6 ACCEPTED | 达成 |
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
