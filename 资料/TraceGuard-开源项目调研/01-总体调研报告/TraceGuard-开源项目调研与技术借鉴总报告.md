# TraceGuard 开源项目调研与技术借鉴总报告

- 任务编号：TG-RESEARCH-01
- 研究批次：R1（本轮）
- 基线：TraceGuard 完整项目实施路线图 v1.1、v1.1 修订摘要、项目资料 00～10（文档集 1.1.0）
- 研究性质：专项研究与文档交付，不属于正式业务开发；未修改任何 TraceGuard 正式工程文件
- 研究时间：2026-11-18
- 研究资料目录：`D:/桌面/TraceGuard/TraceGuard-开源项目调研/`（位于工作区内，与"实施线路""项目资料"平级，独立于正式资料；源码克隆在 `.research-tmp/` 临时目录）

---

## 第一章　研究背景与目标

### 1.1 为什么 TraceGuard 需要开源调研

TraceGuard 已完成 v1.1 总体设计与实施路线规划，准备进入阶段 0 工程建设。项目横跨 UI 自动化、Browser Agent、Agentic Testing、证据可观测、故障诊断、测试自愈和 Benchmark 等多个快速演进的开源领域。若直接进入开发，团队容易重复实现成熟框架已提供的基础能力，或因不了解同类项目的失败教训（如"Agent 自报 verdict"）而重蹈覆辙。

本次调研的目的不是收集 GitHub 仓库清单，而是：
1. 找到与 TraceGuard 各模块/功能相近的成熟开源项目；
2. 对重点项目做源码级研究，理解架构、核心代码与执行流程；
3. 明确每个项目可为 TraceGuard 哪个阶段提供参考；
4. 区分"可直接采用 / 借鉴架构 / 借鉴算法 / 仅学习参考"；
5. 建立 TraceGuard 架构与开源项目之间的系统映射；
6. 为团队提供针对性的学习资料与源码阅读路线；
7. 把调研成果转化为未来可执行的技术选型、架构设计与开发任务。

### 1.2 本次研究范围

- TraceGuard 侧：v1.1 正式资料（路线图 1821 行、修订摘要、00～10 设计文档），覆盖 14 阶段、五层、6 里程碑、C01～C12 契约、D-01～D-19 决策、P01～P12 待确认项。
- 开源侧：任务书指定的 19 个项目（A～S 共 19 个），（A～S 共 19 个，其中 OWASP Juice Shop 为指定被测应用类项目）。本轮实际核实全部 19 个指定项目，未发现需额外新增的候选项目。其中两个重点项目（ATE、Hercules）做源码级研究，其余 17 个做官方资料核实级研究。
- 深度分层：R1 完成两项目源码级 + 17 项目官方核实；R2～R6 后续深化（见第十一章）。

### 1.3 研究方法

1. **基线先行**：先完整阅读 v1.1 正式资料，锁定 14 阶段正式名称、模块边界、冻结原则，再开展开源调研，确保不以开源架构取代 TraceGuard 自身架构。
2. **官方来源核实**：所有项目通过官方 GitHub 仓库、LICENSE 文件、README、GitHub API、官方文档站核实；不凭 README 摘要或搜索引擎结果认定能力。
3. **源码级研究**：两个重点项目克隆到临时目录，对**核心执行链路做源码级阅读与交叉验证**（grep 定位类/函数并回到源码核对）；对辅助模块采用**关键代码阅读与符号检索**；保留"尚未从源码核实"清单。区分"源码事实 / 文档声称 / 推断"，不把辅助模块的未核实结论写成已验证事实。（ATE/Hercules 源码研究文档内已逐节标注阅读粒度。）
4. **并行分工**：生态调研分 4 组并行核实；源码研究分 2 组并行深挖；综合文档由主线统一编写以保证一致性。
5. **映射与决策**：按 14 阶段逐阶段建立映射，按 A/B/C/D 分类建立决策台账，单列许可证风险。

### 1.4 资料可信度分级

| 级别 | 含义 | 本轮对应 |
|---|---|---|
| L3 源码级 | 实际阅读源码文件、可定位类/函数/行号 | ATE、Hercules 的关键机制 |
| L2 官方核实 | 官方仓库/LICENSE/README/API/文档确认 | 17 个项目的基本信息、架构要点 |
| L1 官方声称 | 仅 README/文档描述，未源码验证 | 部分能力描述（如 Automatic Analysis 算法） |
| 未核实 | 未能从官方来源确认 | 已逐项标注"尚未从官方资料核实" |
| 推断 | 基于源码/文档的合理推断，非事实 | 架构图中的推断部分已明确标注 |

### 1.5 与 v1.1 的关系

本报告所有结论以 v1.1 为唯一基线。开源项目的设计只作为参考，不构成对 v1.1 架构、阶段顺序、模块边界、数据契约、验收标准的修改。所有优化建议均标注影响范围与是否需重新评审（见第九章）。v1.1 为"评审修订稿，待通过评审后冻结"，本调研不改变其状态。

---

## 第二章　TraceGuard 自身架构概览

### 2.1 项目定位（v1.1）

**TraceGuard：基于 Agent 的智能 Web UI 自动化测试与质量诊断平台**（Agentic Web UI Testing & Quality Intelligence Platform）。以专业 Web UI 自动化测试框架为工程底座，引入 Agent 自主探索和测试生成能力，通过可信 Oracle、多源 Evidence、故障诊断和受保护的测试自愈，降低测试设计、执行、分析和维护成本。TraceGuard 不是浏览器 Agent Demo，也不是纯日志分析的 AIOps 系统——UI 自动化测试是核心业务，Agent 是提升测试生产与质量分析的智能层（D-01）。

### 2.2 核心架构

- 总体架构（D-02/D-18）：模块化单体 + 独立异步 Worker + 隔离浏览器执行环境。
- 三大业务子系统（A 智能测试生成与执行 / B 证据驱动的故障诊断 / C 安全修复与持续质量优化）。
- 四大支撑体系（Agent Orchestration / Data & Evidence Infrastructure / Test Environment & Benchmark / Platform & Visualization）。
- 两项横向保障（Trusted Oracle & Deterministic Verification / Security & Privacy Governance）。
- 技术栈（5.3）：Python/FastAPI/Vue3/PostgreSQL（阶段 0）、Playwright/pytest（阶段 2）、Redis/Celery/SSE（阶段 3）、MinIO/OpenTelemetry（阶段 4）、Browser Use/LangGraph/LLM（阶段 5 起）。

### 2.3 模块划分

核心模块（保留 v1.1 正式术语）：Test Intent Manager、Explorer Agent、Exploration Trace、TestCase Compiler、TestCase Service、Oracle Manager、Deterministic Verifier、Execution Orchestrator、Execution Engine、Event Collector、Evidence Manager、Evidence Correlation Service、Evidence Graph、Failure Triage、Diagnosis Workflow、Hypothesis Manager、Active Diagnosis Planner、Probe Registry/Engine、Disposition Router、Repair Advisor、Repair Service、Approval Manager、Sandbox Validator、Regression Manager。

四类核心 Agent：Explorer Agent、Test Generation Agent、Diagnosis Agent、Repair Agent。**Agent 是能力层，不是事实层**（D-04）——Agent 仅提出候选信息，不自行批准预期、不修改权威状态、不宣布通过/根因、不绕过权限网关。

### 2.4 阶段关系

五层建设路线：第一层 0～3（UI 自动化基础设施）、第二层 4（测试平台与证据基础设施）、第三层 5～6（Agentic Testing）、第四层 7～10（智能诊断与安全自愈）、第五层 11～13（评测与工程化交付）。6 里程碑 M0～M5。阶段依赖图见 v1.1 2.4；阶段 13 不是第一次全链路集成，各里程碑持续集成。

### 2.5 核心数据流

两条核心产品线：
- **智能测试生产线**：TestIntent → Explorer Agent → Exploration Trace → TestCase Compiler → Candidate TestCase → Human Review → Trusted Oracle Binding → Formal TestCaseVersion → Playwright Replay。
- **智能质量诊断线**：Run/Event/Evidence → Diagnosis v1 → Evidence Graph → Active Diagnosis → Authorized Probe → New Evidence → Updated Diagnosis → Disposition。

六阶段业务闭环：需求理解 → 探索生成 → 确定性执行 → 证据关联 → 诊断处置 → 安全修复回归 → 回流。

### 2.6 关键技术问题（四项冻结核心技术）

1. **TestCase Compiler**：探索轨迹→可审核可回放的 TestCase；研究问题包括 NL 需求→步骤映射、Locator 稳定性、无效/过弱断言识别。
2. **Evidence Correlation Graph**：把 UI/DOM/网络/API/业务状态/日志/探针组织为证据网络；节点 Run/Step/Event/Evidence/Assertion/Hypothesis/ProbeExecution/Source；边 parent_child/emitted_by/derived_from/correlated_by_trace_id/correlated_by_request_id/same_execution_context/temporal/supports/contradicts/evaluates。
3. **Active Diagnosis Planner**：Score(p)=w_I·ÊEIG(p)−w_C·Ĉ(p)−w_R·R̂(p)；首期可解释启发式，EIG 为估计量非校准概率（P05 待确认）。
4. **Oracle-Protected Self-Healing**：修复测试资产但保护原始 Oracle 与 Assertion；沙箱/审批/回归/CAS 原子晋升/回滚；禁止修改产品代码、修改 Oracle、删除必需断言、无依据降低断言严格度。

### 2.7 结果判定与边界（冻结）

四个独立结果维度：`execution_status`、`test_verdict`、`diagnosis_status`、`diagnosis_outcome`（D-11）。Test Verdict 四状态 PASSED/FAILED/INCONCLUSIVE/NOT_EVALUATED，按阶段 2 冻结聚合规则产生；Oracle 不可用不得 PASSED；Optional Assertion 不决定 Verdict；多 Oracle 冲突无法裁决判 INCONCLUSIVE。灵魂原则：Agent 能力层非事实层、Playwright 确定性执行、Oracle 独立、Event/Evidence 分离、相关不等于因果、自愈不得削弱 Assertion。

---

## 第三章　开源生态分析

按能力方向分为 11 类（完整分类与综合对比见《开源生态分类与综合对比.md》）：

| 分类 | 项目 |
|---|---|
| UI 自动化基础 | Playwright、pytest |
| Browser Agent | Browser Use、Stagehand |
| Agentic Testing | Agentic Test Explorer、TestZeus Hercules |
| 测试执行/工作台 | ReportPortal、Allure、Temporal |
| 证据与可观测性 | OpenTelemetry、Langfuse |
| 故障诊断/评测组织 | ReportPortal、Inspect AI |
| 自愈 | Healenium、Hercules（healing） |
| 工作流编排 | LangGraph、Temporal |
| Benchmark/评测 | Inspect AI、AgentLab、BrowserGym |
| 安全测试与被测应用 | OWASP ZAP、OWASP Juice Shop |
| 图数据 | Neo4j |

生态观察：
1. 没有单一项目覆盖 TraceGuard 全链路，验证了自建整合架构的必要性。
2. Agentic Testing 类（ATE/Hercules）判定机制不同但都缺少独立 Oracle：ATE 靠 LLM 分析探索结果并自报 PASS/FAIL；Hercules 最终 `is_passed` 由 Planner 提供，但有默认 API 断言与报告期账本覆盖（不等于独立 Oracle）——反向印证 TraceGuard "Oracle 独立 + 可信预期绑定 + 确定性 Verdict"冻结原则的价值。
3. 可观测（OTel/Langfuse）与结果分析（ReportPortal）项目不与测试执行、诊断闭环打通。
4. 自愈类（Healenium/Hercules healing）普遍缺少 Oracle/断言保护、审批与回归门禁。
5. Benchmark 类提供框架与实验管理，但业务指标（Macro-F1、False Pass Rate 等）需 TraceGuard 自建。

---

## 第四章　开源项目综合对比

（完整 19 项对比表见《开源生态分类与综合对比.md》第二章，此处给出决策向摘要。）

| 项目 | 功能 | 架构 | 技术栈 | 成熟度/维护（依据日期） | License | 匹配度 | 主要局限 |
|---|---|---|---|---|---|---|---|
| Playwright | Web 自动化与测试 | 库+Test Runner | TypeScript，多语言 | v1.65.1/2026-11-13，活跃 | Apache-2.0 | 高 | 版本须 P01；无 Agent 探索 |
| pytest | Python 测试框架 | 插件/钩子 | Python | 9.2.0/2026-10-27 | MIT | 高 | 无浏览器能力 |
| ATE | Agentic 探索 QA | 单 Supervisor 集中路由 | Python/LangGraph/Playwright | 0.2.0/commit 2026-10-03 | MIT | 中高 | 无独立 Oracle、LLM 自报 verdict |
| Hercules | E2E 测试 Agent | Planner+多 nav agent | Python/LangGraph/MCP | 1.0.1/commit 2026-10-24 | AGPL-3.0 | 中（仅设计） | 强 copyleft；无独立 Assertion Agent，Planner 自报 verdict（有默认 API 断言/账本覆盖，非独立 Oracle） |
| Browser Use | AI 浏览器 Agent | model+REPL+CDP | Python | 1.0.6/2026-11-16 | MIT | 中高 | 1.0 架构重构；集成待 P02 |
| Stagehand | 浏览器 SDK | act/observe/extract | TypeScript 为主 | commit 2026-11-18 | MIT | 中高 | v4 无 Playwright interop |
| LangGraph | Agent 编排 | StateGraph/Checkpoint | Python | 1.2.22/2026-11-14 | MIT | 高 | Checkpoint≠业务权威 |
| OpenTelemetry | 可观测规范 | Trace/Span/Event | Markdown+SDK | v1.62.0/22 Oct（推定） | Apache-2.0 | 高 | 字段状态 Mixed |
| Langfuse | LLM 工程平台 | OTel+LLM Trace | TS/Rust/Python | v4.82.0/2026-11-17 | MIT 核心+EE | 中 | LLM Trace≠业务 Evidence |
| Allure | 测试报告 | 结果数据文件→生成器 | Java/TypeScript | 2.46.2/10 Nov（推定） | Apache-2.0 | 中 | 自建工作台优先；引入待论证 |
| ReportPortal | TestOps | 微服务/插件 | 子仓服务 | 26.0.7/2026-10-27 | Apache-2.0 | 中 | 失败分类≠根因；QG 付费 |
| Temporal | 持久执行 | Workflow/Activity | Go | v1.33.0/2026-11-16 | MIT | 低-中 | 与 Celery 冲突 |
| Healenium | Locator 自愈 | Proxy+LSC | 多语言 | 2.2.1/2026-03-31 | Apache-2.0 | 中 | 无 Oracle/审批保护 |
| Inspect AI | LLM 评测 | Task/Dataset/Scorer | Python | 0.3.291/2026-11-17 | MIT | 中高 | 非浏览器自动化 |
| Neo4j | 图数据库 | 属性图+Cypher | Java/Scala | commit 2026-10-20 | GPL-3.0/商业 | 低-中 | P12 未定；GPL 合规 |
| BrowserGym | Web Agent 环境 | Gym Task/Env | Python | commit 2026-11-18 | Apache-2.0* | 中 | 非产品 |
| AgentLab | Agent 实验管理 | Study/Ray | Python | v0.4.2/2026-01-20 | Apache-2.0* | 中 | 不支持跨实例并行 |
| Juice Shop | 安全靶场 | Angular+Express | TypeScript | v20.4.0/2026-11-08 | MIT | 低-中 | 靶场≠UI 测试套件 |
| ZAP | 安全扫描 | MITM 代理 | Java | commit 2026-11-16 | Apache-2.0+第三方 | 低-中 | 扫描≠UI 诊断 |

\* BrowserGym/AgentLab 的 API 元数据为 Other/NOASSERTION、README 徽章文字 MIT 但链接 Apache-2.0；以仓库 LICENSE 文件为准认定 Apache-2.0。

## 第五章　两个重点项目深度研究

### 5.1 Agentic Test Explorer（ATE）

**基本信息**（源码核实，详见 `02-重点项目源码研究/Agentic-Test-Explorer/`）：v0.2.0，MIT（Copyright 2026 Oscar Barrios Torrero），Python，LangGraph ~1.1.9 + LangChain + Playwright ~1.58 + langmem + langgraph-checkpoint-sqlite，最后提交 2026-10-03（单人维护，dependabot 依赖更新）。

**架构与核心机制**：
- 所谓 "Supervisor-Worker / Swarm" 实为**单 Supervisor 集中路由**——`AgentState.next_agent`（`graph_base.py:70,547,643`）选择下一 worker，Agent 之间**无直接任务传递**；`make_supervisor_node`（`graph_base.py:468`）用精简路由 brief 控制 token。
- **Brain/Hands 分离**是最大亮点：Agent 只输出结构化 JSON 意图，确定性 `_dispatch` 层执行 Playwright 动作；Action Tape 以 jsonl 落盘（`engine.py:11,49-59`）；`generate_playwright_spec`（`engine.py:565-611`，Agent 侧包装为 `generate_reproduction_spec`，:619）把 Tape 转写为 `reproduction_*.spec.ts`，但 replay 中 snapshot/check_page_health 为 no-op。
- 记忆：SQLite Checkpoint + langmem 语义记忆（`memory.py`），按 thread_id 做 prompt 优化与 Bug Catalog 汇总。
- PR 驱动：`pr_analyzer.py` 从 GitHub PR 变更生成定向探索任务。

**优点**：Brain/Hands 分离契合"Agent 是能力层"；意图→确定性执行骨架清晰；Tape→spec 可复现链路；有界路由控制成本。**缺陷/局限**：无独立 Oracle，bug 判定与 PASS/FAIL 靠 LLM 文本自述（`analyze_visual_state` 视觉 Oracle 未接入主图），存在 False Pass 风险；spec 仅线性回放，`extract_text` 只有 `expect(_t.length).toBeGreaterThan(0)` 弱检查、无目标缺陷/业务预期的有效断言；Tape 为可变 `List` + JSONL 追加写、失败仅 warn、无哈希链/签名/篡改检测（源码注释 "immutable" 属设计意图非安全保证），且 DOM 快照工具不写 Tape，故 Tape 不是完整交互轨迹；架构文档标注行号系统性过时；快照 selector 含 `:nth-of-type` 却被自身守卫拒绝；记忆按 thread_id 而非角色优化。

**与 TraceGuard 对照**：Browser 控制、状态管理、任务输入、测试用例生成有借鉴价值（Brain/Hands B 类、Tape→spec C 类）；Oracle/Assertion/修复/Benchmark/人工审批维度缺失或与"确定性 Verdict"原则冲突，不采用。ATE 是"探索轨迹≠测试""Oracle 必须独立"的**反面案例**。

### 5.2 TestZeus Hercules

**基本信息**（源码核实）：v1.0.1，LICENSE 为 **AGPL-3.0**（第 13 条仅针对修改版网络交互，非所有 SaaS），Python，LangGraph + Playwright（`<=1.49.0` 旧锁定）+ MCP + Pydantic + SQLAlchemy，最后提交 2026-10-24。

**架构与核心机制**：
- README 声称 planner/executor/assertion 三节点 LangGraph 状态机，源码核实（`simple_hercules.py:1447` `StateGraph(AgentState)`）基本一致——但 **StateGraph 在 `simple_hercules.py`，不在 `state_handler.py`**。
- Planner（`high_level_planner_agent.py:72,186`）输出 JSON：plan/next_step/terminate/final_response/is_assert/assert_summary/is_passed/target_helper。**无独立 Assertion Agent**：`_assertion_node`（`simple_hercules.py:1386`）只是信任 Planner 自报的 `is_assert/is_passed`，非 LLM 节点。确定性判定依赖默认注册的 API 断言 `assert_json_path`（API Agent prompt 明确要求调用）、opt-in 默认关闭的 UI 断言 `assert_ui`，以及报告期 JUnit 断言账本覆盖（FAILED/ERROR 可推翻 Planner 的 PASS）。没有独立于 Agent、绑定可信来源与人工审批的 Trusted Oracle。
- 多工具 Agent：`executor_nav`/`browser_nav`/`api_nav`/`sql_nav`/`mcp_nav` 等经 `agent_registry.py` 注册；浏览器封装 playwright_manager + get_interactive_elements + accessibility tree；报告链 post_process_responses + reporting/bundle.py（解析 JUnit XML 生成失败 bundle/截图）+ markdown_report + gherkin_generator。

**优点**：Planner 结构化计划、AgentRegistry 组织、报告产物结构化（JUnit XML + 截图 bundle + Markdown）、失败分类（healing/），以及已接入主链路的循环防护（`_PLANNER_STALL_LIMIT=3`、`progress_stall_count`、nav 最大轮次）；`assert_json_path` 确定性 API 断言默认注册并写审计账本。**缺陷/局限**：**AGPL-3.0 强 copyleft**（硬约束）；无独立 Assertion Agent、默认判定靠 LLM 自报，弱证据；默认配置下 UI 断言/observer/历史多关闭、零断言 pass 仅标记不判红、`STRICT_VERDICT` 默认关；`detect_llm_loops.is_agent_stuck_in_loop` 有实现但本快照未发现调用点；playwright 锁定旧版本；复杂度高。

**与 TraceGuard 对照**：可借鉴 Planner 结构化计划（B）、报告产物组织（C，独立重实现）、失败分类与 selector 候选（C，需叠加保护）；**禁止复制任何源码**（AGPL）；"Planner 自报 verdict"与"无审批自愈"与冻结原则冲突，不采用。

### 5.3 两项目共同教训

两项目的判定机制并不相同，不能笼统归为"都靠 LLM 自报 verdict"。**ATE** 主要依赖 LLM 对探索结果进行缺陷分析与 PASS/FAIL 自述，缺少独立可信 Oracle。**Hercules** 没有独立 Assertion Agent，最终 `is_passed` 仍由 Planner 提供，但具备默认注册的 API 确定性断言 `assert_json_path`、opt-in 默认关闭的 UI 断言 `assert_ui`，以及报告期读取断言账本、以 FAILED/ERROR 覆盖 Planner PASS 的机制（零断言 PASS 仅标记 `Assertions Missing` 不判红，`STRICT_VERDICT` 默认关）。**二者共同缺少** TraceGuard 所要求的独立 Trusted Oracle、绑定可信来源并经审批的预期，以及完整确定性 Verdict 机制——这正是 TraceGuard 立身的差异化设计（Trusted Oracle + 确定性 Verdict + 证据驱动诊断 + 受保护自愈）。这两个项目是"可以借鉴探索、工具组织与断言工程，但判定与可信机制必须自建"的最佳证据。

---

## 第六章　TraceGuard 全阶段映射

完整逐阶段映射（阶段 0～13，每阶段十项结构）见《TraceGuard-十四阶段开源项目映射.md》。此处给出摘要表：

| 阶段 | 正式名称 | 主要参考项目 | 核心借鉴方式 | 研究状态 |
|---|---|---|---|---|
| 0 | 工程初始化与设计基线 | pytest、Playwright、OpenTelemetry | A/C | 官方核实完成 |
| 1 | TeamFlow 最小被测应用与故障注入 | OWASP Juice Shop、BrowserGym | D/B | 官方核实完成 |
| 2 | 专业 Web UI 自动化测试框架 | Playwright、pytest、Allure、ATE(反面) | A/B/C | 官方+ATE 源码完成 |
| 3 | 任务调度、执行编排与测试管理工作台 | Temporal、ReportPortal、Allure、LangGraph | B/C | 官方核实完成 |
| 4 | Event 与 Evidence 证据基础设施 | OpenTelemetry Specification、Langfuse、ATE | B（OTel Spec 标准借鉴、Langfuse 仅 LLM 可观测层）/ D（ATE 仅反面对照） | 官方核实完成；SDK/Collector 为独立仓库，留待 R4 |
| 5 | Explorer Agent 自主 Web 探索 | Browser Use、Stagehand、LangGraph、ATE、Hercules | B/C/A(待RV-03) | ATE 源码完成 |
| 6 | TestCase Compiler 智能测试用例编译器 | ATE、Stagehand、Healenium、Hercules | C/B | ATE 源码完成 |
| 7 | 基础故障分类与 Diagnosis v1 | ReportPortal、Inspect AI、Langfuse、ATE | B/C/D | 官方核实完成 |
| 8 | Evidence Correlation Graph | Neo4j、Langfuse、ReportPortal | B/C(待P12) | 官方核实完成 |
| 9 | Active Diagnosis Planner | LangGraph、Hercules、Temporal、Inspect AI | B/C/D | 官方核实完成 |
| 10 | Oracle-Protected Self-Healing | Healenium、Hercules、LangGraph、Temporal | C/B(禁复制AGPL) | Hercules 源码完成 |
| 11 | UI-FaultBench 完整 Benchmark | Inspect AI、AgentLab、BrowserGym、Langfuse | B/C | 官方核实完成 |
| 12 | 外部 Web 应用泛化、安全与可靠性强化 | OWASP ZAP、Juice Shop、Browser Use、Stagehand | B/C/D | 官方核实完成 |
| 13 | 全链路集成、最终验收与交付 | 全生态综合 | A/B/C/D | 映射框架建立 |

跨阶段多对多总表、错误映射边界（如 Playwright≠Explorer、Langfuse Trace≠业务 Evidence、ZAP≠UI 诊断、Neo4j 非必选等 10 条）见映射文档末两节。

---

## 第七章　借鉴方式和采用决策

### 7.1 四类定义

- **A 直接采用/集成**：成熟通用、维护可接受、架构兼容、无不可接受许可证/安全问题，需明确组件/版本/边界/许可证/维护风险（如 Playwright、pytest）。
- **B 借鉴架构与设计**：整体值得研究但运行模式不适合直接采用，明确哪些思想可吸收、哪些不照搬（如 Temporal、BrowserGym）。
- **C 借鉴算法与局部实现**：只取特定机制，按 TraceGuard 业务规则重新实现，说明原始机制/适用条件/不同约束/需修改部分（如 Healenium Locator Healing）。
- **D 仅学习参考**：设计有启发性但技术/许可证/维护/运行模式不适合引入，说明原因。

### 7.2 决策结论摘要

需区分两个独立维度：**借鉴类型**（A/B/C/D，学什么、怎么学）与**引入决定**（是否作为依赖进入正式工程）。同一项目可"建议借鉴其架构（B）"但"暂不引入为依赖"——二者不矛盾。下表以决策台账（PRJ-xx）口径为准：

| 借鉴类型 | 项目（PRJ 编号） | 引入决定（候选意见，待评审） |
|---|---|---|
| **A 直接采用/集成** | Playwright（PRJ-01）、pytest（PRJ-02） | v1.1 已列为阶段 2 组件；版本待 P01 锁定。LangGraph（PRJ-04）引入范围待 RV-03 独立评审、Browser Use（PRJ-08）与 Playwright 会话集成待 P02 PoC；二者总体借鉴类型分别为 A/B、B/C，不预设为纯 A。**OpenTelemetry 规范仓库 PRJ-03 不是 SDK 实现**，SDK/Collector 的引入评估不计入本行 A 类，单列于下方对照表并留待 R4 |
| **B 架构借鉴** | OpenTelemetry Specification（PRJ-03）、Temporal（PRJ-07）、ReportPortal（PRJ-06）、Inspect AI（PRJ-13）、Langfuse（PRJ-15）、BrowserGym（PRJ-16）、AgentLab（PRJ-17）、ATE（PRJ-10）、Browser Use（PRJ-08）、Stagehand（PRJ-09）、Neo4j（PRJ-14，条件性） | 均不作为完整依赖引入：OpenTelemetry Specification 仅借鉴标准与数据模型（SDK/Collector 为独立仓库，见 R4）；Temporal 不替换 Celery；Langfuse/BrowserGym/AgentLab 职能边界不符；Neo4j 待 P12；ATE 部分设计可借鉴 |
| **C 算法/局部实现** | ATE（PRJ-10）、Healenium（PRJ-12）、Hercules（PRJ-11）、Stagehand（PRJ-09）、LangGraph（PRJ-04） | 用 TraceGuard 自有设计独立重实现，不复制源码；Hercules 受 AGPL 硬约束，仅学设计。**OpenTelemetry Specification（PRJ-03）不属于本类**——规范仓库只提供标准/数据模型，无实现源码可供局部借鉴 |
| **D 学习参考** | Juice Shop（PRJ-18）、ATE（PRJ-10，反面案例）、Hercules（PRJ-11） | 不引入；作为被测场景样例或反面案例 |

**OpenTelemetry 对象级映射（避免"规范仓库=SDK"误读）**：

| 对象 | 正确定位 | 借鉴方式 / 后续处理 |
|---|---|---|
| OpenTelemetry Specification（PRJ-03） | 跨语言标准和数据模型规范（Trace/Span/Event/Context/Resource、语义约定） | **B：架构/标准借鉴**；阶段 4 Event 字段命名参考，不引入为依赖 |
| OpenTelemetry Python SDK | 独立 Python 实现仓库 `opentelemetry-python` | **R4 条件性集成评估**（后端埋点、许可、版本、与 Evidence 关联）；本轮不预设引入 |
| OpenTelemetry JavaScript SDK | 独立 JavaScript 实现仓库 `opentelemetry-js` | **R4 条件性集成评估**（前端采集、Context 传播）；本轮不预设引入 |
| OpenTelemetry Collector | 独立采集处理服务仓库 `opentelemetry-collector` | **R4 条件性集成评估**（是否部署、与 Evidence 边界）；本轮不预设引入 |

> 四个对象不得互相代称：PRJ-03 仅指 Specification 规范仓库；三个实现/服务仓库未纳入本轮 19 项 PRJ 编号，也不因被提及而成为已批准依赖。

**暂不建议引入的依赖**（与借鉴类型无关，是引入决定）：Hercules（AGPL-3.0，禁复制代码）、Neo4j（GPL，待 P12）、Langfuse（混合许可+数据边界）、ReportPortal/Allure（与自建工作台重复，待论证）、Temporal（替换 Celery 既定设计）、ZAP（安全扫描职能边界）、BrowserGym/AgentLab（非管理平台）、ATE（无独立 Oracle，仅作反面案例）。

**关键纪律**：本轮 19 个项目**无一被批准采用**——此处仅指 **R1 研究未批准任何新增依赖**，所有决策状态为"待评审"或"不采用/待法律评估"；**不撤销、不否定 v1.1 已选定的 Playwright、pytest、OpenTelemetry、Browser Use、LangGraph 等技术**（其版本/边界仍按 P01～P03、RV-03 待确认）。"研究完成"不等于"批准采用"，也不等于"推翻既有选型"（详见决策台账）。

---

## 第八章　技术引入与许可证风险

- **许可证分布**：MIT 8 个、Apache-2.0 8 个、AGPL-3.0 1 个（Hercules）、GPL-3.0/商业 1 个（Neo4j）、MIT 核心+Enterprise 1 个（Langfuse）；BrowserGym/AgentLab 元数据不一致（以 LICENSE 文件为准认定 Apache-2.0）。
- **最高风险**：Hercules AGPL-3.0 第 13 条——仅当修改其程序并支持网络远程交互时才需向远程用户提供对应源码，**不可泛化为所有 SaaS 自动披露整个平台源码**；结论为不复制代码、仅学设计、待法律评估。
- **GPL 与 AGPL 区分**：Neo4j 的 GPL-3.0 **无** AGPL 第 13 条网络条款，义务主要在复制/修改/分发/组合时产生，内部运行或独立服务/API 调用通常不自动触发源码披露；两者触发条件不同，不可混用。
- **条件性风险**：Neo4j GPLv3（义务主要在复制、修改、组合程序及向第三方分发时产生；独立服务/API 调用不应直接等同于 GPL 网络源码披露义务，具体集成方式由法律评估确认；P12 通过后再评估）；Langfuse EE 目录商业许可（禁复制 `ee/`）。
- **元数据/打包风险**：BrowserGym/AgentLab API license=Other（已澄清）；OWASP ZAP LEGALNOTICE 含 BSD/MIT/GPL+classpath/EPL/LGPL 等第三方许可，打包分发需逐项核对。
- **维护与版本风险**：Hercules playwright<=1.49 旧锁定（版本须按 P01 实测）；Healenium release 2026-03 相对偏旧；部分 release 年份推定项已标注；Stagehand v4 与 v3 架构差异大。
- **合规纪律**：研究学习≠代码复制；依赖引入≠复制源码；不凭 README 标识下结论，一律查看官方 LICENSE；不确定项标记"待法律评估"（完整逐项目表见《开源许可证与技术复用风险.md》）。

## 第九章　TraceGuard 自身架构的保持与优化建议

**总原则**：以下均为候选建议，不修改 v1.1 任何设计；每条标注对应原设计、问题/机会、建议、影响模块、风险、是否需评审、是否影响 v1.1。

| 编号 | 对应原设计 | 问题/改进机会 | 建议内容 | 影响模块 | 风险 | 需评审 | 影响 v1.1 |
|---|---|---|---|---|---|---|---|
| SUG-01 | D-04 Agent 能力层非事实层；阶段 5 | ATE 的 Brain/Hands 分离被源码验证有效，可强化意图与执行的边界 | 在 Explorer 中明确"LLM 出结构化意图 + 确定性 dispatch + 操作留痕"三层，意图经策略校验后执行 | Explorer Agent、Tool Gateway、Execution Engine | 若意图层无校验会引入越权 | 是 | 否（细化实现） |
| SUG-02 | D-05/D-08；阶段 2 冻结 Verdict | ATE 靠 LLM 自报判定；Hercules 最终 is_passed 由 Planner 提供（有默认 API 断言/账本覆盖，但仍非独立 Oracle）——共同印证独立 Oracle 与可信预期绑定的必要性 | 在设计评审中把两项目作为对照案例，强化"Oracle 不可用不得 PASSED""预期须绑定可信来源"的可执行验证；同时参考 Hercules 断言账本覆盖思路 | Oracle Manager、Verifier | 无 | 是（评审材料） | 否 |
| SUG-03 | 阶段 6 TestCase Compiler | ATE Tape→spec 提供确定性转写参考，可缩短 Compiler 原型周期 | Compiler 的可回放步骤转写参考该机制，但断言/Oracle/稳定性/审核按 v1.1 自建 | Compiler（Action Extractor、Stability Validator） | 若弱化断言会违反冻结原则 | 是 | 否 |
| SUG-04 | 阶段 3 执行编排（Celery 既定） | Temporal Durable Execution 提供恢复设计启发 | 在 Worker 租约/Fencing 设计中参考 Event History 思路，不引入 Temporal、不改 Celery | Execution Orchestrator、Worker | 引入 Temporal 会冲突架构 | 是 | 否 |
| SUG-05 | 阶段 8 Evidence Graph；P12 | 图数据库必要性未定，首期关系表已够 | 保留关系表实现，P12 实测后再评估 Neo4j；若引入须先 GPL 合规 | Evidence Graph | GPL 合规、过度工程化 | 是（P12） | 否（维持现状） |
| SUG-06 | 阶段 4 Event/Evidence；D-10 | OTel 字段模型成熟，可统一命名 | Event/Trace/Span 字段命名参考 OTel 语义约定，保持 Event 与 Evidence 分离 | Event Collector、Evidence Manager | 字段状态 Mixed 需甄别 | 是 | 否 |
| SUG-07 | 阶段 10 Self-Healing；D-15 | Healenium/Hercules healing 均缺保护门禁 | 自愈机制必须在候选生成之外，显式实现断言保护+沙箱+审批+回归+CAS | Repair Advisor、Approval、Regression | 缺任一门禁都会削弱断言 | 是 | 否 |
| SUG-08 | 阶段 11 UI-FaultBench | Inspect AI/AgentLab 提供成熟评测组织 | 评测集与实验管理借鉴 Task/Dataset/Scorer、Study/复现日志，业务指标自建 | Benchmark 服务 | 直接引入框架会偏离业务指标 | 是 | 否 |
| SUG-09 | P01 版本锁定 | 两项目分别锁定 playwright 1.58 / <=1.49，差异大 | 以官方兼容资料 + 最小 PoC 确定版本，不照搬任何项目锁定值 | 阶段 0/2 依赖 | 版本错误导致上层返工 | 是（P01） | 否 |

**架构保持声明**：以上建议均未改变五层、14 阶段、6 里程碑、C01～C12、D-01～D-19、四大核心技术、Playwright 确定性执行、Oracle 独立、Event/Evidence 分离、模块化单体 + 独立 Worker 等任何 v1.1 基线内容。

---

## 第十章　模块学习路线

按 14 阶段划分的完整学习路线见 `03-开源项目学习地图/`（阶段0-3.md、阶段4-6.md、阶段7-10.md、阶段11-13.md），每项含：适合角色、先修知识、官方文档入口、源码阅读入口、建议实践、学习完成标准、对应开发任务。跨角色能力成长路径见 `05-后续研究计划/模块学习与实践路线.md`（L1 基础工程→L2 确定性执行→L3 证据与诊断→L4 Agentic Testing→L5 自愈与评测→L6 泛化与交付）。

示例（任务书要求）：
- **阶段 2**：Playwright Browser/Context/Page、Locator 与自动等待、网络监听、Trace Viewer、pytest Fixture、测试隔离并行 → 能参与 UI 自动化执行内核开发。
- **阶段 5**：Browser Use 页面观察、Stagehand observe/act/extract、ATE 多 Agent 探索、LangGraph StateGraph、Agent State/Checkpoint、浏览器操作记录 → 能参与 Explorer Agent 架构与实现。
- **阶段 10**：Healenium Locator Healing、Stagehand 动作恢复、候选定位器生成与验证、Oracle 保护、人工审批与版本晋升 → 能实现不削弱断言的受控自愈机制。

---

## 第十一章　后续专项研究计划

R1～R6 完整计划见 `05-后续研究计划/R1-R6专项研究计划.md`。摘要：

| 批次 | 研究对象 | 状态 | 目标深度 | 开发关系 |
|---|---|---|---|---|
| R1 | ATE、Hercules + 生态初调 + 14 阶段映射 + 决策/许可证/学习路线 | **已完成** | 两项目源码级，其余官方核实 | 全阶段前置 |
| R2 | Browser Use、Stagehand | 待启动 | 源码级 + 二者对比 | 阶段 5；P02 PoC |
| R3 | Playwright、pytest | 待启动（已官方核实） | 关键机制源码 + 自研边界 | 阶段 2；P01 版本 |
| R4 | ReportPortal、Allure、OpenTelemetry、Langfuse | 待启动（已官方核实） | 架构级 + 数据模型对照 | 阶段 3/4/7/13 |
| R5 | Healenium、LangGraph、Inspect AI、Neo4j | 待启动（已官方核实） | 算法/机制级 | 阶段 6/8/9/10/11；P05/P12 |
| R6 | BrowserGym、AgentLab、Juice Shop、ZAP | 待启动（已官方核实） | 平台/环境级 + 安全边界 | 阶段 1/11/12；P09/P10 |

执行纪律：每批开始前确认 v1.1 未变；源码研究区分事实/声称/推断；新增项目登记决策台账；研究完成≠批准采用；交付后同步更新总报告与映射。

---

## 第十二章　结论与可执行事项

### 12.1 已完成的研究（R1）

- v1.1 项目结构完整梳理（14 阶段/五层/6 里程碑/C01～C12/D-01～D-19/P01～P12）。
- 19 个指定项目官方核实（含许可证、维护日期、架构要点、未核实项标注）。
- 两个重点项目（ATE、Hercules）源码级研究，各 6 份文档，关键结论交叉核验。
- 14 阶段完整映射 + 跨阶段多对多总表 + 10 条错误映射边界。
- A/B/C/D 借鉴决策体系 + 19 条决策台账 + 采用候选/不采用/待评审清单。
- 许可证与技术复用风险报告（AGPL/GPL/混合许可专项）。
- 分阶段学习路线（4 份，覆盖 14 阶段）+ 跨角色实践路线。
- 本总报告（12 章）+ R1～R6 计划。

### 12.2 值得优先借鉴的设计

1. **Brain/Hands 分离 + JSON Intent**（ATE，B 类）→ 阶段 5 Explorer，契合"Agent 能力层"原则。
2. **Action Tape→可回放脚本转写**（ATE，C 类）→ 阶段 6 Compiler，需补断言/Oracle/稳定性/审核。
3. **OTel Specification 的 Event/Attribute/Resource 字段模型**（B 类，仅标准借鉴；SDK/Collector 另由 R4 评估）→ 阶段 4 Event/Evidence 标准化。
4. **LangGraph StateGraph/Checkpoint/HITL**（A/B 类，引入范围待 RV-03 独立评审；Checkpoint 仅作恢复信息、不替代 PostgreSQL 业务状态权威）→ 阶段 5/9/10 工作流与审批恢复。
5. **Inspect AI/AgentLab 评测与实验组织**（B 类）→ 阶段 11 UI-FaultBench。
6. **Temporal 恢复与重试思想**（B 类）→ 阶段 3 可靠性，不替换 Celery。

### 12.3 尚待验证的技术

- P02 Browser Use 与 Playwright 会话集成（不假定共享上下文，须 PoC；仅用于集成验证，LangGraph 引入范围另由 RV-03 独立评审）。
- P03 Oracle 多来源冲突裁决细则（基础 Verdict 已冻结，细则阶段 2 前确认）。
- P05 EIG 权重与归一化（启发式先行，实验校准）。
- P12 专用图数据库必要性（阶段 8 实测后评估）。
- 各引入候选的具体版本与兼容矩阵（P01）。

### 12.4 暂不建议引入的项目

Hercules（AGPL-3.0，仅学设计）、Neo4j（GPL，待 P12）、Langfuse（混合许可+数据边界）、Temporal（不替换 Celery）、ReportPortal/Allure（与自建重复，待论证）、ZAP（职能边界）、BrowserGym/AgentLab（非管理平台）、Juice Shop（靶场，学习参考）。

### 12.5 需要团队评审的事项

见《待评审事项.md》（RV-01～RV-14，含技术采用、法律合规 RV-07～RV-10、架构边界与调研局限）：版本锁定、集成方式、引入范围、许可合规、图数据库决策、报告层自建 vs 引入；责任角色统一为"待分配"。

### 12.6 最终交付目标对照

本套资料使团队在进入每个开发模块前能回答：自身需要实现什么（v1.1 阶段目标）、有哪些成熟开源项目值得研究（映射表）、应读哪些源码（源码目录分析/学习路线）、可借鉴什么设计（借鉴建议/决策台账）、哪些不能照搬（边界清单/不采用原因）、如何转化为工程实现（落地任务/R1～R6）。

### 12.7 边界声明

- 本调研未修改 TraceGuard 正式工程文件、未创建正式 GitHub 仓库、未做 git 提交、未引入正式依赖、未复制开源代码到正式源码目录。
- 研究资料位于 `D:/桌面/TraceGuard/TraceGuard-开源项目调研/`，与正式资料平级且独立；外部源码克隆仅在 `.research-tmp/` 临时目录。
- 所有借鉴建议为候选意见，不构成团队批准结论；"研究完成"≠"批准采用"。


