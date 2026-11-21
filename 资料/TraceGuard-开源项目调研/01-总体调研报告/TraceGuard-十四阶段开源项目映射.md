# TraceGuard 十四阶段开源项目映射

- 基线：TraceGuard 完整项目实施路线图 v1.1（五层、14 阶段 0～13、6 里程碑、C01～C12）
- 用途：逐阶段建立"自身目标 → 模块 → 推荐开源项目 → 学习内容 → 可借鉴 → 不照搬 → 借鉴方式 → 落地任务 → 研究状态"映射
- 借鉴方式代号：**A** 直接采用/集成；**B** 借鉴架构与设计；**C** 借鉴算法或局部实现；**D** 仅学习参考
- 研究状态：本轮（R1）完成源码级研究的是 ATE、Hercules；其余项目为官方资料核实级，后续 R2～R6 深化
- 纪律：不因开源项目有某功能就认定 TraceGuard 需要；不因为某阶段无匹配项目就硬塞无关项目；保留边界区分

> 阶段正式名称与职责均引自 v1.1 路线图第 2.2 节与第 3 章，未作改写。

---

## 阶段 0：工程初始化与设计基线

**一、TraceGuard 自身目标**（v1.1）：建立可复现、可协作、可持续集成的工程环境，落实基础工程、安全和版本治理。

**二、对应模块**：仓库骨架、FastAPI/Vue3 基础工程、PostgreSQL 连接与迁移、统一配置/日志/错误处理、Lint/Format/TypeCheck/UnitTest/CI、技术版本锁定与兼容矩阵、C01～C12 Schema 目录与版本治理、安全基线。

**三、推荐开源项目**：pytest（主要）、Playwright（版本锁定参考）、OpenTelemetry（可观测性基线）。

**四、项目对应关系**

| 开源项目 | 对应模块 | 参考功能 | 研究重点 |
|---|---|---|---|
| pytest | CI/Unit Test | Fixture、Scope、Hook、Plugin、测试生命周期 | 如何用 pluggy 组织可扩展测试、CI 集成 |
| Playwright | 版本锁定/兼容矩阵 | 多浏览器驱动、依赖锁定方式 | 版本兼容策略、跨语言绑定 |
| OpenTelemetry Specification | 日志/指标/Trace 基线 | Trace/Span/Event/Resource、语义约定 | 统一可观测数据模型、Stable/Development 状态区分 |

**五、具体学习内容**：pytest 官方文档 fixtures/plugins/how-to；Playwright 仓库 README 与 releases 版本策略；OpenTelemetry spec 的 trace/api、common、resource 章节。

**六、可以借鉴的设计**：pytest 的 hook+plugin 扩展机制组织测试体系（C）；OpenTelemetry 语义约定统一字段命名（B）；依赖锁定 + 兼容矩阵 + PoC 验证组合的工程纪律（B，方法论）。

**七、不应照搬的内容**：不引入任何项目的运行时依赖作为 TraceGuard 业务依赖（阶段 0 只建工程骨架）；不照搬 OpenTelemetry 中所有 Development 状态字段为已冻结。

**八、借鉴方式**：pytest A（作为测试框架直接集成，阶段 2 起）；OpenTelemetry B；Playwright A（阶段 2 起）。

**九、后续开发落地**：阶段 0 CI 配置、依赖锁定文件、C01～C12 Schema 目录规范、可观测性字段命名规范。

**十、研究状态**：官方核实已完成；待阶段 0 详细设计时深化。

---

## 阶段 1：TeamFlow 最小被测应用与故障注入

**一、自身目标**（v1.1）：建立足以支撑首期 UI 自动化和诊断闭环的受控 Web 应用，不建设完整项目协作 SaaS。

**二、对应模块**：登录/测试账户、Workspace、项目创建/列表/详情、独立业务状态查询、数据重置、五类故障注入（TF-API/NET/DB/UI/TIME）、Ground Truth、C12 场景元数据。

**三、推荐开源项目**：OWASP Juice Shop（主要，补充项目发现）、BrowserGym（Task 抽象参考）。

**四、项目对应关系**

| 开源项目 | 对应模块 | 参考功能 | 研究重点 |
|---|---|---|---|
| OWASP Juice Shop | 受控被测应用 / 故障场景 | 固定挑战集、种子数据可重建、Docker 一键启动、Score Board | 如何构造"已知结果"的可控场景与数据自愈 |
| BrowserGym | 故障场景 / Task 定义 | AbstractBrowserTask 的 setup/validate/cheat/teardown | 任务级 validate 与 Ground Truth 分离 |

**五、具体学习内容**：Juice Shop companion guide 的 architecture/challenges 章节、data/ 目录数据种子方式；BrowserGym 的 AbstractBrowserTask 定义。

**六、可以借鉴的设计**：可重复初始化/清理数据的"自愈数据库"机制（C）；任务 validate() 与 cheat()（作弊函数，代表 Ground Truth 视角）分离的设计（B，借鉴任务抽象，不引入运行时依赖）。

**七、不应照搬的内容**：不照搬 Juice Shop 的安全漏洞场景作为 TraceGuard 的 UI 功能测试目标（它是安全靶场）；不引入其 Angular/Express 栈作为 TeamFlow 技术选型（TeamFlow 按 v1.1 用既定技术栈）；不允许 Agent 读取 Ground Truth（v1.1 非目标）。

**八、借鉴方式**：Juice Shop D（学习参考，作为受控应用与场景设计样例）；BrowserGym——**整体项目不引入运行时依赖（不采用为运行时组件），但具体 Task 抽象设计可借鉴（B）：AbstractBrowserTask 的 setup/validate/cheat/teardown 与 Ground Truth 隔离；Ground Truth 不得暴露给 Agent**。（"是否引入"与"是否借鉴设计"分开表达；不引入运行时依赖不意味着完全不学习其设计。）**新增说明**：OWASP Juice Shop 为任务书 4.7 指定的 R 项目（被测应用类）；本轮未发现需额外新增的候选项目。

**九、后续开发落地**：TeamFlow 五类故障注入设计、数据重置脚本、Ground Truth 隔离方案、C12 场景元数据模板。

**十、研究状态**：官方核实已完成；R6 深化其场景组织方式。

---

## 阶段 2：专业 Web UI 自动化测试框架

**一、自身目标**（v1.1）：交付独立可运行的 Playwright + pytest 确定性测试执行内核，不建设完整异步平台。

**二、对应模块**：TestIntent/TestCase/TestCaseVersion/TestStep/AssertionSpec、Browser Manager/Context/Page Manager/Locator/Action Executor/Assertion Executor、Screenshot/Trace/Network、Oracle Manager、Assertion 级结果与 Test Verdict 聚合（已冻结）、本地同步执行。

**三、推荐开源项目**：Playwright（主要）、pytest（主要）、Allure（报告参考）、ATE（反面案例，本地源码研究）。

**四、项目对应关系**

| 开源项目 | 对应模块 | 参考功能 | 研究重点 |
|---|---|---|---|
| Playwright | 执行内核 | Browser/Context/Page/Locator、自动等待、网络监听、Trace/Screenshot/Video、Trace Viewer | 确定性执行与证据录制机制；隔离 context |
| pytest | 测试框架集成 | Discovery/Fixture/Parametrize/Hook/报告 | 测试生命周期埋点、参数化 |
| Allure | 报告数据模型 | TestCase/Step/Attachment、JUnit 结果、历史/重试 | 结果数据文件与展示解耦 |
| Agentic Test Explorer | 反面案例 | 无独立 Oracle、LLM 自报 verdict | 反面验证"Oracle 必须独立" |

**五、具体学习内容**：Playwright docs locators/network/trace-viewer/test-retries；pytest fixtures/plugins hooks；Allure how-it-works/history-and-retries；ATE 的 engine.py 回放机制作为"轨迹≠测试"反面材料。

**六、可以借鉴的设计**：Playwright Locator 自动等待与 Trace Viewer 证据模型（A 直接采用 + B 参考其证据结构）；pytest Fixture 管理浏览器/数据作用域（A）；Allure 结果数据文件→生成器解耦、基于 testCaseId 的历史关联（B/C）。

**七、不应照搬的内容**：不照搬 ATE/Hercules 的"LLM 自报 PASS/FAIL"（直接违反 D-05/D-08 与冻结的 Verdict 规则）；不引入 Allure 作为正式报告依赖除非评审通过；不让 Agent 决定测试结果。

**八、借鉴方式**：Playwright A、pytest A、Allure B/C、ATE D（反面案例）。

**九、后续开发落地**：阶段 2 执行内核、Oracle Manager、四状态 Verdict 聚合、合法/非法样例、Trace/截图采集、P03 Oracle 多来源细则评审输入。

**十、研究状态**：Playwright/pytest/Allure 官方核实完成（R3）；ATE 源码级完成（R1）。

---

## 阶段 3：任务调度、执行编排与测试管理工作台

**一、自身目标**（v1.1）：将阶段 2 执行内核接入平台，形成持久化、异步、可追踪的测试执行系统。

**二、对应模块**：Task/Run/Step、Redis Broker/Celery Worker、队列隔离、Worker 心跳/租约/执行代次（Fencing）、幂等、重试/取消/超时/恢复、SSE、工作台。

**三、推荐开源项目**：Temporal（主要参考）、ReportPortal（工作台/历史参考）、Allure（报告）、LangGraph（Checkpoint 恢复思路）。

**四、项目对应关系**

| 开源项目 | 对应模块 | 参考功能 | 研究重点 |
|---|---|---|---|
| Temporal | 执行编排/可靠性 | Workflow/Activity、Durable Execution、Retry Policy、Event History、失败从最后事件恢复 | 长任务状态持久化与崩溃恢复；声明式重试 |
| ReportPortal | 测试管理工作台 | Launch/Test Item、结果聚合、历史分析、失败分类 | 执行历史与结果组织视图 |
| Allure | 报告 | JUnit 结果、Attachment、历史/重试 | 报告产物结构 |
| LangGraph | Checkpoint/恢复 | 节点即检查点/重试边界、从 checkpoint 恢复 | 恢复语义参考（非业务状态权威） |

**五、具体学习内容**：Temporal docs durable-execution/retry-policies；ReportPortal glossary（Launch/Test Item/Test Case ID）、Automatic Analysis；Allure 报告产物；LangGraph graph-api 节点恢复语义。

**六、可以借鉴的设计**：Temporal Event History + 声明式 Retry Policy 思想（B）；ReportPortal Launch/Step 层级 + 历史签名组织（B）；LangGraph "节点=检查点/重试边界"（C）。

**七、不应照搬的内容**：**不引入 Temporal 替换 v1.1 已定的 Celery + Redis + PostgreSQL 租约设计**（任务书明确：Temporal 仅作研究参考）；不照搬 ReportPortal 失败分类当作根因诊断；不把 LangGraph Checkpoint 当业务状态权威。

**八、借鉴方式**：Temporal B（仅架构思想）、ReportPortal B、Allure B/C、LangGraph C。

**九、后续开发落地**：Task/Run/Step 服务、租约与 Fencing、故障注入测试、SSE、工作台；P11 并发/SLO 实测。

**十、研究状态**：官方核实完成；R4（ReportPortal/Allure/Temporal）、R5（LangGraph）深化。

---

## 阶段 4：Event 与 Evidence 证据基础设施

**一、自身目标**（v1.1）：建立统一事件和证据基础设施，为 Explorer、Diagnosis、Graph 和 Benchmark 提供可追溯数据。

**二、对应模块**：Event Collector/Store、Outbox/Consumer Inbox 幂等、`context_refs` 绑定与 Workspace 注入、序号与缺口检测、Evidence Manager、对象存储、SHA-256、来源/脱敏/检索/过期清理。

**三、推荐开源项目**：OpenTelemetry（主要）、Langfuse（LLM/Agent Trace 对照）、ATE（Action Tape 对照）。

**四、项目对应关系**

| 开源项目 | 对应模块 | 参考功能 | 研究重点 |
|---|---|---|---|
| OpenTelemetry Specification | Event 数据模型 | Event=name+timestamp+Attributes、Context Propagation、Resource、TraceId/SpanId、语义约定 | 事件/追踪字段标准化、跨服务关联 |
| Langfuse | LLM/Agent Trace | built on OTel、LLM 语义 trace、异步批量发送 | LLM 调用记录与业务 Evidence 的边界 |
| Agentic Test Explorer | Evidence 形态对照 | Action Tape jsonl + 截图落盘 | 动作留痕形态；不能替代原始证据 |

**五、具体学习内容**：OTel spec trace/api、context、common、resource；Langfuse observability overview 与 OTel 集成；ATE engine.py 的 tape 落盘。

**六、可以借鉴的设计**：OTel Specification 的 Event/Attribute/Resource 数据模型与 W3C TraceContext（B，仅标准借鉴；SDK/Collector 属独立仓库）；Langfuse "LLM 语义 trace + OTel 底座"模式（B）；ETL 中"原始证据不可变、派生保留来源"（与 v1.1 一致，C）。

**七、不应照搬的内容**：**不将 LLM Agent Trace（Langfuse）等同于 TraceGuard 业务 Testing Evidence**——任务书明确边界；不照搬 Langfuse Enterprise 许可的 `ee/` 代码；不把 `trace_id`/`request_id` 当因果依据（v1.1 阶段 4 明确）；不为满足 `workspace_id` 虚构归属。

**八、借鉴方式**：OpenTelemetry Specification B（仅标准/数据模型；Python/JS SDK 与 Collector 为独立仓库，留待 R4 条件性评估）、Langfuse B（仅 LLM 可观测层）、ATE D（对照）。

**九、后续开发落地**：Event Envelope/C05 契约、Consumer Inbox 去重、Context refs 绑定、Sequence 缺口检测、Evidence 分层与完整性校验、P07 生命周期确认。

**十、研究状态**：官方核实完成；R4 深化 OTel/Langfuse。

---

## 阶段 5：Explorer Agent 自主 Web 探索

**一、自身目标**（v1.1）：根据自然语言 TestIntent 自主探索 TeamFlow，生成结构化 Exploration Trace。

**二、对应模块**：Explorer Agent、LangGraph 有状态工作流、Browser Use 浏览器探索、Playwright Adapter、Tool Gateway、Exploration Trace、预算与越权控制。

**三、推荐开源项目**：Browser Use（主要）、Stagehand（主要）、LangGraph（主要）、ATE（源码研究）、Hercules（多工具 Agent 参考）。

**四、项目对应关系**

| 开源项目 | 对应模块 | 参考功能 | 研究重点 |
|---|---|---|---|
| Browser Use | 浏览器探索决策 | model+REPL+CDP、页面观察、动作规划、结构化输出 | Agent 浏览器操作架构与错误恢复；1.0 新架构 |
| Stagehand | AI+确定性结合 | act()/observe()/extract()、Action 零推理重放 | 元素识别、确定性动作输出、可观测校验 |
| LangGraph | 有状态工作流 | StateGraph、Checkpoint、条件边 | 探索状态机与恢复 |
| ATE | Agent 组织 | Supervisor 路由、Brain/Hands 分离、JSON Intent | 意图→确定性执行骨架（源码级已核实） |
| Hercules | 多工具 Agent | Planner + 多 nav agent、AgentRegistry | 工具型 Agent 组织（仅设计参考，AGPL 不采用代码） |

**五、具体学习内容**：Browser Use 1.0 README 架构与 tools；Stagehand v4 act/observe/extract 文档；LangGraph graph-api/persistence；ATE `graph_base.py`/`engine.py`；Hercules agent_registry（只读理解）。

**六、可以借鉴的设计**：ATE "LLM 出 JSON Intent + 确定性 dispatch + Action Tape"（B，核心）；Stagehand observe→act 的"先校验再执行"与 Action 重放（C）；Browser Use 的 judge 结构化失败原因与 prompt-injection 检测字段（C）；LangGraph 有状态探索工作流（B）。

**七、不应照搬的内容**：不照搬 Browser Use/Stagehand 作为正式测试执行内核（任务书边界：Browser Agent ≠ 测试执行内核）；不假定 Browser Use 与 Playwright 可共享浏览器上下文（v1.1 P02 要求 PoC 验证）；不照搬 Hercules AGPL 代码；不把探索成功视为测试通过或正式 TestCase（v1.1 非目标）。

**八、借鉴方式**：Browser Use B/C、Stagehand B/C、LangGraph A/B（引入范围由 RV-03 独立评审，与 P02 无关；Checkpoint 仅作恢复信息、不替代 PostgreSQL 权威）、ATE B/C、Hercules D（AGPL 限制）。

**九、后续开发落地**：Explorer Agent、Browser Adapter、Tool Gateway 权限/域名/预算校验、Exploration Trace 结构、P02 集成 PoC。

**十、研究状态**：ATE 源码级完成（R1）；Browser Use/Stagehand/LangGraph 官方核实完成，R2/R5 源码级深化。

---

## 阶段 6：TestCase Compiler 智能测试用例编译器

**一、自身目标**（v1.1）：把 Exploration Trace 转换为可审核、可独立回放、具有可信 Oracle 绑定的 Candidate TestCase。

**二、对应模块**：Trace Normalizer、Action Extractor、Locator Resolver、Assertion Constructor、Oracle Binder、Stability Validator、来源版本记录、候选稳定性晋升。

**三、推荐开源项目**：ATE（Action Tape→spec 转写，源码研究）、Stagehand（Locator/Action 重放）、Healenium（Locator 稳定性）、Hercules（报告/断言工具思路，AGPL 限制）。

**四、项目对应关系**

| 开源项目 | 对应模块 | 参考功能 | 研究重点 |
|---|---|---|---|
| ATE | Action Extractor / 可回放步骤 | Action Tape → reproduction_*.spec.ts | 轨迹确定性转写为回放脚本（源码级已核实） |
| Stagehand | Locator Resolver / Action | observe() 返回 selector+method+arguments、Action 重放 | role/accessible name 优先的定位策略 |
| Healenium | Locator 稳定性 | 成功 locator 基线、候选匹配 | Locator 稳定性评估思路 |
| Hercules | Assertion/报告（思路） | assert_json_path（API 默认注册）、assert_ui（UI opt-in）、断言账本、healing selector_store | 显式断言工具+审计账本与候选存储（不复制代码） |

**五、具体学习内容**：ATE `engine.py:565-611` generate_playwright_spec（Tape→spec 代码生成器，仅弱检查 `toBeGreaterThan(0)`；Tape 为追加式操作日志、非防篡改 Evidence）；Stagehand v4 observe/act 文档；Healenium how-it-works；Hercules healing（只读理解）。

**六、可以借鉴的设计**：ATE tape→spec 确定性转写（C）；Stagehand 结构化 Action 与 selector 策略（B/C）；Healenium 成功 locator 基线用于稳定性评分（C）。

**七、不应照搬的内容**：不把 Trace 直接作为正式测试（v1.1 非目标）；不照搬 ATE spec 仅含弱检查（`expect(_t.length).toBeGreaterThan(0)`）/snapshot no-op 的线性回放形式；不自动批准 Oracle、不绕过审核、不覆盖正式 TestCaseVersion；不复制 Hercules 代码（AGPL）。

**八、借鉴方式**：ATE C、Stagehand B/C、Healenium C、Hercules D。

**九、后续开发落地**：Compiler 各子模块、Locator 策略版本、Assertion 构造、Oracle 绑定、稳定性验证策略（次数/门槛按风险配置）、F08 稳定性晋升规则。

**十、研究状态**：ATE 源码级完成（R1）；Stagehand R2、Healenium R5 深化。

---

## 阶段 7：基础故障分类与 Diagnosis v1

**一、自身目标**（v1.1）：建立基于 Run、Oracle、Event 和 Evidence 的结构化诊断基线，为 Graph 和主动诊断提供对照基础。

**二、对应模块**：Diagnosis Service/Agent、Hypothesis 模型、Failure Domain/Mechanism 分类（正式枚举）、确认规则、Disposition Router、报告与初始评测集；八维诊断相互独立。

**三、推荐开源项目**：ReportPortal（失败分类参考）、Langfuse（Trace 辅助）、Inspect AI（评测集组织）、ATE（bug catalog 对照）。

**四、项目对应关系**

| 开源项目 | 对应模块 | 参考功能 | 研究重点 |
|---|---|---|---|
| ReportPortal | 失败分类/历史 | Collaborative analysis、Automatic Analysis、历史数据 | 失败标记分类思路（≠根因诊断） |
| Langfuse | 诊断输入 | LLM/Agent Trace、Evaluation | Trace 作为诊断线索，非业务证据 |
| Inspect AI | 初始评测集 | Task/Dataset/Scorer、200+ 评测 | 评测集组织与 scorer 设计 |
| ATE | bug 记录对照 | bug summaries / Catalog | LLM 提取 bug 的对照（源码已核实） |

**五、具体学习内容**：ReportPortal docs（Automatic Analysis、失败分类）；Inspect AI llms-guide（Task/Dataset/Solver/Scorer）；ATE memory.py/bug_extraction。

**六、可以借鉴的设计**：Inspect AI 的 Dataset/Scorer 分离组织评测集（B）；ReportPortal 历史失败标记辅助分类（C）；多假设结构参考诊断领域常见做法（B）。

**七、不应照搬的内容**：**不把 ReportPortal 失败分类当 TraceGuard 根因诊断**（任务书边界）；不让 LLM 宣布 CONFIRMED、不把 Flaky 当根因、不合并八维诊断、不让所有失败进入 Repair（均为 v1.1 明确不做）。

**八、借鉴方式**：ReportPortal C、Inspect AI B、Langfuse D、ATE D。

**九、后续开发落地**：Diagnosis v1 服务、八维模型与正式枚举、多 Hypothesis、确认规则（P04）、Disposition Router、初始评测集。

**十、研究状态**：官方核实完成；R4（ReportPortal）、R5（Inspect AI）深化。

---

## 阶段 8：Evidence Correlation Graph

**一、自身目标**（v1.1）：将 Run、Step、Event、Evidence、Assertion、Hypothesis 和 Probe 组织为可查询、可追溯的关系图。

**二、对应模块**：Node/Edge Schema、Correlation Service、Graph Query、关联规则、来源独立性、派生关系、冲突分析与可视化；首期用 PostgreSQL 关系表，不预先引入图数据库。

**三、推荐开源项目**：Neo4j（图模型/查询参考，非必选）、Langfuse（Trace 关联）、ReportPortal（关联视图）。

**四、项目对应关系**

| 开源项目 | 对应模块 | 参考功能 | 研究重点 |
|---|---|---|---|
| Neo4j | 图建模/查询（条件性） | 属性图模型、Cypher 多跳查询、GDS 图算法 | 节点/关系建模与多跳查询的完备实现参考 |
| Langfuse | 关联线索 | OTel trace/span 关联 | 关联线索 ≠ 因果 |
| ReportPortal | 关联视图 | 跨 launch 结果关联 | 展示层关联组织 |

**五、具体学习内容**：Neo4j graphdb-concepts、Cypher、GDS；对照 v1.1 节点（Run/Step/Event/Evidence/Assertion/Hypothesis/ProbeExecution/Source）与边类型。

**六、可以借鉴的设计**：Neo4j 属性图建模与有类型/方向/属性的关系表达（B）；Cypher 多跳遍历作为未来图查询能力参照（C，仅当 P12 评估通过）。

**七、不应照搬的内容**：**不能因 TraceGuard 有 Evidence Graph 就默认采用 Neo4j**（任务书明确，P12 待确认）；首期按 v1.1 用 PostgreSQL 关系表；不把 temporal/trace_id 关联当因果、不覆盖原始 Evidence、不自动把候选 Hypothesis 升级根因。

**八、借鉴方式**：Neo4j B/C（条件性，待 P12）、Langfuse D、ReportPortal D。

**九、后续开发落地**：Graph Schema、Correlation Service（依据已校验 context_refs 建边）、关联规则与来源依据、Graph 可视化、P12 图数据库必要性实测。

**十、研究状态**：官方核实完成；R5 深化 Neo4j。

---

## 阶段 9：Active Diagnosis Planner

**一、自身目标**（v1.1）：在已有 Evidence Graph 和 Hypothesis 基础上，主动选择授权 Probe，补充高价值证据并更新诊断。

**二、对应模块**：Probe Registry、Probe Engine、Permission Gateway、Planner、EIG 启发式评分、Hypothesis Update、预算与报告；只读 Probe 白名单与五项校验。

**三、推荐开源项目**：LangGraph（Planner 工作流）、Hercules（多工具 Agent；循环检测函数未接入主流程，AGPL 限制，仅学设计）、Temporal（活动重试/超时）、Inspect AI（工具型 solver）。

**四、项目对应关系**

| 开源项目 | 对应模块 | 参考功能 | 研究重点 |
|---|---|---|---|
| LangGraph | Planner 状态机 | 条件边、Checkpoint、human-in-the-loop、节点重试 | 探针选择循环与停止条件编排 |
| Hercules | 工具调用/循环 | 多工具 Agent、`_PLANNER_STALL_LIMIT`/`progress_stall_count`/nav 最大轮次（已接入）；`is_agent_stuck_in_loop`（有实现，未发现调用点） | 工具调用与循环防护思路（不复制代码）；区分已接入与未接入机制 |
| Temporal | Retry/Timeout | 声明式重试、超时 | 探针重试/超时策略 |
| Inspect AI | 工具型 Solver | solver 用工具多轮交互 | 工具使用型诊断 agent 组织 |

**五、具体学习内容**：LangGraph graph-api 条件边与中断恢复；Hercules `simple_hercules.py` 的 `progress_stall_count`/`_PLANNER_STALL_LIMIT=3`/nav 最大轮次（`browser_nav_max_chat_round=10`，已接入主链路），以及 `detect_llm_loops.py` 的 `is_agent_stuck_in_loop`（有实现但本快照未发现调用点，仅作设计参考）；Temporal retry-policies；Inspect AI tool usage。

**六、可以借鉴的设计**：LangGraph 条件边循环 + 停止条件（B）；Hercules 已接入的 Planner 无进展检测与 nav 轮次上限作为预算防护思路（C，独立实现），`is_agent_stuck_in_loop` 仅作设计参考（D，未验证是否生效）；Temporal 声明式重试/超时（C）。

**七、不应照搬的内容**：不允许任意 SQL、任意内网访问、无预算探测或无限循环；不越过 Workspace/Project 边界；Probe 结果不直接等同根因；不复制 Hercules AGPL 代码。评分函数 EIG 为估计量，权重（P05）与预算（P06）未确认前不得当已校准概率。

**八、借鉴方式**：LangGraph B、Hercules C/D（不复制）、Temporal C、Inspect AI D。

**九、后续开发落地**：Probe Registry/Engine、五项校验与审计、EIG 启发式评分、停止条件、P05/P06 确认、主动诊断前后对照评测。

**十、研究状态**：官方核实完成；R5 深化。

---

## 阶段 10：Oracle-Protected Self-Healing

**一、自身目标**（v1.1）：让 AI 提出测试资产修复候选，通过断言保护、Oracle 完整性、沙箱验证、审批和回归后安全晋升。

**二、对应模块**：Repair Advisor、Proposal Service、Diff Generator、Assertion Preservation、Oracle Integrity Checker、Sandbox Validator、Approval Manager、Regression Manager、Version Promotion/Rollback（CAS 原子晋升）；11 状态状态机。

**三、推荐开源项目**：Healenium（Locator healing 主要）、Hercules（healing 机制参考，AGPL 限制）、Temporal（长任务恢复）、LangGraph（审批中断恢复）。

**四、项目对应关系**

| 开源项目 | 对应模块 | 参考功能 | 研究重点 |
|---|---|---|---|
| Healenium | Locator 修复候选 | 成功 locator 基线、LSC 候选匹配、评分选最优、反馈验证 | Locator 修复算法与记录（源码未开放，官方文档级） |
| Hercules | failure_classifier/heal_proposer/selector_store | 失败分类 + 修复建议 + selector 存储 | 机制思路（不复制 AGPL 代码） |
| LangGraph | 审批环节 | human-in-the-loop interrupt/resume | 审批暂停与恢复 |
| Temporal | 晋升可靠性 | Durable Execution / CAS 类比 | 长事务与失败恢复 |

**五、具体学习内容**：Healenium how-it-works；Hercules healing 三文件（只读理解）；LangGraph human-in-the-loop；v1.1 阶段 10 CAS 原子晋升与版本冲突规则。

**六、可以借鉴的设计**：Healenium "基线→候选→评分→反馈闭环"（C）；Hercules 失败分类+selector 候选（C，独立实现）；LangGraph 审批暂停/恢复（B）。

**七、不应照搬的内容**：**Healenium 的 Locator Healing ≠ 完整安全自愈**；不修改正式 Oracle、不删除必需 Assertion、不降低断言严格度、不覆盖正式版本、不用旧提案覆盖新版本、不允许 Agent 自批自修、未经回归不晋升；不复制 Hercules 代码（AGPL）；VERSION_CONFLICT 不得当普通故障直接重试。

**八、借鉴方式**：Healenium C、Hercules C/D（不复制）、LangGraph B、Temporal D。

**九、后续开发落地**：Repair 服务与 11 状态状态机、Assertion Preservation、Oracle Integrity、Sandbox、Approval（P08）、Regression、CAS 晋升、回滚与版本冲突处理。

**十、研究状态**：官方核实完成；Hercules 源码级完成（R1）；Healenium R5 深化。

---

## 阶段 11：UI-FaultBench 完整 Benchmark

**一、自身目标**（v1.1）：形成覆盖测试生成、故障诊断和安全自愈的可重复评测体系。

**二、对应模块**：Dataset Manager、Ground Truth Manager、Experiment Manager、Baseline Runner、Evaluation Engine、Ablation Runner、指标定义、实验报告与工作台；三类评测与 A～E 对照。

**三、推荐开源项目**：Inspect AI（主要）、AgentLab（主要）、BrowserGym（环境/任务）、Langfuse（实验/eval）、ATE/Hercules（被测对象）。

**四、项目对应关系**

| 开源项目 | 对应模块 | 参考功能 | 研究重点 |
|---|---|---|---|
| Inspect AI | 评测框架 | Task/Dataset/Solver/Scorer、200+ 评测、Log Viewer | 评测的组织与可复现 |
| AgentLab | 实验管理 | Study/ExpResult、Ray 并行、复现日志、AgentXray | 对照实验与复现记录 |
| BrowserGym | 环境/任务 | Task/Episode/Reward、多 benchmark | 任务级评测与 reward |
| Langfuse | Eval | Datasets/Experiments、LLM-as-a-judge | 基于历史轨迹的评测 |
| ATE/Hercules | 被测对象 | 探索/执行/自愈能力 | 作为被评测的同类系统参照 |

**五、具体学习内容**：Inspect AI llms-guide 与 evals；AgentLab README（Study/make_study/复现）；BrowserGym AbstractBrowserTask 与 action space；Langfuse evaluation experiments。

**六、可以借鉴的设计**：Inspect AI Task/Dataset/Scorer 分离（B）；AgentLab Study + 复现日志 + 并行（B/C）；BrowserGym Task.validate 与 reward（C）；统一元数据快照（B）。

**七、不应照搬的内容**：不把 AgentLab/BrowserGym 评测环境当 TraceGuard 管理平台（任务书边界）；Ground Truth 不向 Agent 开放；INCONCLUSIVE 不计 PASSED；Selective Accuracy 同时报 Coverage；数据集按故障机制/场景变体/应用版本隔离防泄漏；无保护修复只作隔离对照不晋升。

**八、借鉴方式**：Inspect AI B、AgentLab B/C、BrowserGym B/C、Langfuse B。

**九、后续开发落地**：三类 Benchmark、A～E 对照与消融、指标口径（统计单位/分子分母/排除条件/缺失值/多根因/置信区间）、P09 规模与统计方案确认。

**十、研究状态**：官方核实完成；R5（Inspect AI）、R6（AgentLab/BrowserGym）深化。

---

## 阶段 12：外部 Web 应用泛化、安全与可靠性强化

**一、自身目标**（v1.1）：验证 TeamFlow 之外适应能力，完成系统性安全、性能和可靠性验证。

**二、对应模块**：外部应用验证、泛化评价、安全强化（SSRF/DNS Rebinding/Prompt Injection/越权/凭证/隔离/配额/脱敏/审计）、性能可靠性、威胁模型与报告。

**三、推荐开源项目**：OWASP ZAP（安全扫描参考）、OWASP Juice Shop（外部/靶场应用）、Browser Use/Stagehand（泛化探索）、Temporal（可靠性）。

**四、项目对应关系**

| 开源项目 | 对应模块 | 参考功能 | 研究重点 |
|---|---|---|---|
| OWASP ZAP | 安全测试 | 被动/主动扫描、代理、REST API、Automation Framework | 安全告警采集与流水线集成 |
| OWASP Juice Shop | 外部/靶场应用 | 固定挑战、可控场景、Docker 隔离 | 泛化验证的授权目标 |
| Browser Use | 泛化探索 | 域名限制、敏感数据脱敏、prompt-injection 检测 | 越权/注入防护思路 |
| Stagehand | 泛化定位 | accessibility tree、iframe/shadow DOM | 页面变化适应 |

**五、具体学习内容**：ZAP docs（passive/active scanning、automate、API）；Juice Shop companion guide；Browser Use 安全约束字段；Stagehand v4 observe 文档。

**六、可以借鉴的设计**：ZAP 代理被动采集 + REST/Automation Framework 集成（B/C）；Browser Use 的 prompt_injection_detected / 域名限制 / 脱敏字段（C）；Stagehand accessibility tree 适配（B）。

**七、不应照搬的内容**：**OWASP ZAP 安全扫描 ≠ UI 测试诊断**（任务书边界）；ZAP alert 不直接当 UI bug 证据；主动扫描属攻击须授权；不对无 Ground Truth 的外部应用宣称诊断准确率；不开放任意域名；不以文档审查代替安全测试；SLO/并发/资源按实测确定（P10/P11）。

**八、借鉴方式**：ZAP B/C、Juice Shop D、Browser Use C、Stagehand B。

**九、后续开发落地**：1～2 个授权外部应用验证、安全测试、性能/稳定性实测、威胁模型、恢复手册、P10/P11 确认。

**十、研究状态**：官方核实完成；R2（Browser Use/Stagehand）、R6（ZAP/Juice Shop）深化。

---

## 阶段 13：全链路集成、最终验收与交付

**一、自身目标**（v1.1）：在各里程碑持续集成基础上，完成最终端到端回归、部署验证、演示和资料归档（阶段 13 不是第一次全链路集成）。

**二、对应模块**：完整源码、Docker Compose 部署、TeamFlow、测试内核、TestCase/Oracle、Task/Run/Step、Explorer、Compiler、Event/Evidence、Graph、Diagnosis、Active Diagnosis、Repair、UI-FaultBench、Vue 工作台、C01～C12 Schema、测试集与报告、十个最终演示场景。

**三、推荐开源项目**：全生态综合运用——Playwright/pytest（内核）、Allure/ReportPortal（报告）、Temporal（可靠性思想）、Inspect AI/AgentLab（评测）、OpenTelemetry/Langfuse（可观测）、Docker Compose（部署）。

**四、项目对应关系**

| 开源项目 | 对应模块 | 参考功能 | 研究重点 |
|---|---|---|---|
| Playwright + pytest | 测试内核 | 确定性执行、测试框架 | 回归与 E2E |
| Allure + ReportPortal | 报告/工作台 | 结果聚合、历史、失败分类 | 最终报告与展示 |
| OpenTelemetry + Langfuse | 可观测 | Trace/指标/LLM 观测 | 全链路可解释 |
| Inspect AI + AgentLab | 评测 | Benchmark 与实验报告 | 能力量化证据 |
| Temporal | 可靠性（思想） | Durable Execution | 长任务恢复验证 |

**五、具体学习内容**：综合前面各阶段学习成果，重点核对三级 CI 门禁与十个最终场景的证据链。

**六、可以借鉴的设计**：全链路可观测 + 结构化报告 + 可复现评测的组合（B）。

**七、不应照搬的内容**：不以页面数量/代码行数/Agent 数量作为验收标准（v1.1 明确以证据为准）；不把研究完成当批准采用；所有依赖引入须经评审。

**八、借鉴方式**：综合 A/B/C/D（按各项目既有归类）。

**九、后续开发落地**：最终回归、发布包、演示脚本、十个场景执行证据、已知限制清单、G08 门禁一致性核验。

**十、研究状态**：待各阶段完成后集成；本轮仅建立映射框架。

---

## 跨阶段项目映射总表（多对多）

> **口径说明**：本表的"借鉴方式"是**项目总体口径**（与决策台账 PRJ-xx 一致，反映该项目对 TraceGuard 的主要借鉴定位）。各阶段正文"八、借鉴方式"是**阶段内口径**——同一项目在不同阶段可能只借用其某一个侧面（如 LangGraph 总体为 A/B，在阶段 3 仅借 Checkpoint 恢复思路记为 C；ReportPortal 总体 B/C，在阶段 7 仅作失败分类对照记为 D/C）。两者是"总体定位"与"阶段用途"的关系，不构成矛盾。引入决定（是否作为依赖）另见决策台账，与借鉴类型是两个独立维度。

| 开源项目 | 官方仓库 | 对应阶段 | 对应模块 | 学习主题 | 借鉴方式 | 状态 |
|---|---|---|---|---|---|---|
| Agentic Test Explorer | srbarrios/agentic-test-explorer | 2,5,6,7 | Explorer/Compiler/证据对照 | Brain/Hands 分离、Action Tape→spec、Supervisor 路由 | B/C/D | 源码级完成 R1 |
| TestZeus Hercules | test-zeus-ai/testzeus-hercules | 5,6,9,10,11 | 多工具 Agent/报告/healing | Planner 计划、AgentRegistry、报告打包、失败分类 | C/D（AGPL 禁复制） | 源码级完成 R1 |
| Browser Use | browser-use/browser-use | 5,12 | Explorer/泛化安全 | REPL+CDP 架构、judge 结构化输出、安全字段 | B/C | 官方核实，R2 深化 |
| Stagehand | browserbase/stagehand | 5,6,12 | Explorer/Locator/泛化 | act/observe/extract、Action 重放、accessibility tree | B/C | 官方核实，R2 深化 |
| Playwright | microsoft/playwright | 0,2,13 | 执行内核 | Locator/自动等待/Trace/网络/并行 | A | 官方核实，R3 深化 |
| pytest | pytest-dev/pytest | 0,2,13 | 测试框架/CI | Fixture/Scope/Hook/Plugin | A | 官方核实，R3 深化 |
| OpenTelemetry Specification | open-telemetry/opentelemetry-specification | 0,4,13 | 可观测/Event 模型 | Trace/Span/Event/Context/Resource | B | 官方核实；SDK/Collector 为独立仓库，R4 条件性评估 |
| Langfuse | langfuse/langfuse | 4,7,11,13 | LLM 可观测/Eval | OTel trace、Dataset/Experiments、成本延迟 | B（注意 EE 许可） | 官方核实，R4 深化 |
| Allure Report | allure-framework/allure2 | 2,3,13 | 报告数据模型 | TestCase/Step/Attachment/历史/重试 | B/C | 官方核实，R4 深化 |
| ReportPortal | reportportal/reportportal | 3,7,8,13 | 工作台/失败分类 | Launch/Test Item/历史/Automatic Analysis | B/C（Quality Gate 付费） | 官方核实，R4 深化 |
| Temporal | temporalio/temporal | 3,9,10,13 | 编排/可靠性（思想） | Durable Execution/Retry/Event History | B（不替换 Celery） | 官方核实，R4 深化 |
| Healenium | healenium/healenium | 6,10 | Locator 稳定性/自愈 | 成功 locator 基线、候选匹配、反馈 | C | 官方核实，R5 深化 |
| LangGraph | langchain-ai/langgraph | 3,5,9,10 | 工作流/Checkpoint/审批 | StateGraph/条件边/Checkpoint/HITL | A/B（按需，RV-03 独立评审） | 官方核实，R5 深化 |
| Inspect AI | UKGovernmentBEIS/inspect_ai | 7,9,11,13 | 评测框架 | Task/Dataset/Solver/Scorer | B | 官方核实，R5 深化 |
| Neo4j | neo4j/neo4j | 8 | 图建模（条件性） | 属性图/Cypher/GDS | B/C（待 P12，GPLv3） | 官方核实，R5 深化 |
| BrowserGym | ServiceNow/BrowserGym | 1,11 | 任务/评测环境 | AbstractBrowserTask/Episode/Reward/Action Space | B/C（非管理平台） | 官方核实，R6 深化 |
| AgentLab | ServiceNow/AgentLab | 11 | 实验管理 | Study/并行/复现日志/AgentXray | B/C | 官方核实，R6 深化 |
| OWASP Juice Shop | juice-shop/juice-shop | 1,12 | 被测应用/靶场 | 可控场景/种子数据/自愈数据库 | D | 官方核实，R6 深化 |
| OWASP ZAP | zaproxy/zaproxy | 12 | 安全测试 | 被动/主动扫描/API/自动化集成 | B/C（≠UI诊断） | 官方核实，R6 深化 |

## 防止错误映射的边界清单（须在映射中长期保留）

| 易混淆点 | 正确边界 |
|---|---|
| Playwright ≠ Explorer Agent | Playwright 是浏览器自动化基础/确定性执行内核，不是自主探索 Agent |
| Browser Use ≠ 正式测试执行内核 | Browser Use 是浏览器 Agent，用于探索，不替代确定性测试执行 |
| Langfuse LLM Trace ≠ 业务 Testing Evidence | 前者是 LLM/Agent 可观测，后者是测试证据，数据模型与可信边界不同 |
| ReportPortal 失败分类 ≠ TraceGuard 根因诊断 | 前者打标分类，后者多假设+证据+确认规则 |
| Healenium Locator Healing ≠ 完整安全自愈 | 前者只做定位器修复，后者含 Oracle/断言保护、沙箱、审批、回归、晋升 |
| BrowserGym ≠ 测试管理平台 | 前者是 Web Agent 研究与评测环境，不是测试管理/诊断平台 |
| OWASP ZAP 安全扫描 ≠ UI 测试诊断 | 前者产出安全漏洞告警，不判定 UI 功能/交互/视觉正确性 |
| Temporal ≠ 替换 Celery | 仅作工作流编排研究参考，不改 v1.1 既定技术选型 |
| Neo4j ≠ 必须采用 | Evidence Graph 可用 PostgreSQL 关系表实现，是否引入待 P12 实测 |
| LangGraph Checkpoint ≠ 业务状态权威 | PostgreSQL 才是业务状态权威，Checkpoint 仅工作流恢复信息 |

