# TraceGuard 十四阶段与六里程碑验收状态

状态枚举：NOT_STARTED / IN_PROGRESS / BLOCKED / READY_FOR_REVIEW / ACCEPTED / REJECTED
规则：只有具备真实验收证据（测试日志、运行结果）的 ACCEPTED 才代表阶段完成；代码写完 ≠ ACCEPTED。

- 最后更新：2026-11-22 00:10

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
| 4 | 7 | 基础故障分类与 Diagnosis v1 | ACCEPTED | 10 测试 + 八维独立/多假设/确认规则/Flaky/Disposition | LLM不得宣布CONFIRMED |
| 4 | 8 | Evidence Correlation Graph | ACCEPTED | 10 测试 + 关联/因果限制/来源独立性/冲突分析 | 修复派生证据独立性bug |
| 4 | 9 | Active Diagnosis Planner | ACCEPTED | 16 测试 + 五项校验/任意SQL拒绝/脱敏/预算/EIG/停止条件 | — |
| 4 | 10 | Oracle-Protected Self-Healing | ACCEPTED | 12 测试 + 断言保护/Oracle完整性/状态机/CAS版本保护/审批/回滚 | M4达成 |
| 5 | 11 | UI-FaultBench 完整 Benchmark | ACCEPTED | 13 测试 + 数据隔离/GT令牌/指标口径/INCONCLUSIVE排除/消融 | 指标口径明确 |
| 5 | 12 | 外部 Web 泛化、安全与可靠性 | READY_FOR_REVIEW | 12 本地安全/可靠性测试 + 全项目173测试 | 外部泛化BLOCKED(未授权P-EXT)；本地SSRF/DNS/Redirect/凭证/并发序号已验证 |
| 5 | 13 | 全链路集成、最终验收与交付 | READY_FOR_REVIEW | 5全链路集成测试 + 10/10演示场景 + 最终交付报告 | 178总测试通过；外部泛化授权限制如实记录 |

## 六里程碑

| 里程碑 | 名称 | 含阶段 | 状态 | 退出条件 |
|---|---|---|---|---|
| M0 | 工程环境与 TeamFlow 可重复运行 | 0,1 | ACCEPTED | ✅ 阶段0/1 ACCEPTED（前端 build 环境限制已记录） | 达成 |
| M1 | 专业 UI 自动化测试平台完成 | 2,3 | ACCEPTED | ✅ 阶段2/3 READY_FOR_REVIEW→ACCEPTED（真浏览器 PoC 因网络限制记录为待验证） | 达成 |
| M2 | 证据基础设施与 Agentic Testing 完成 | 4,5,6 | ACCEPTED | ✅ 阶段4/5/6 ACCEPTED | 达成 |
| M3 | Evidence Graph 与智能诊断完成 | 7,8,9 | ACCEPTED | ✅ 阶段7/8/9 ACCEPTED | 达成 |
| M4 | 安全测试自愈完成 | 10 | ACCEPTED | ✅ 阶段10 ACCEPTED（P08审批角色待确认，用role字符串未编造人员） | 达成 |
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
