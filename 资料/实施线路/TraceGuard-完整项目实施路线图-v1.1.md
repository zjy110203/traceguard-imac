# TraceGuard｜完整项目实施路线图 v1.1

> 版本：v1.1\
> 状态：评审修订稿，待通过评审后冻结\
> 依据：总体架构基线 v1.1、实施路线 v1.0、本轮路线评审意见\
> 统一口径：五层建设路线、十四个实施阶段（0～13）、六个里程碑（M0～M5）、十二组核心契约（C01～C12）

------------------------------------------------------------------------

## 0. 修订说明

### 0.1 修订结论

本次保留 TraceGuard 的总体架构、项目定位、业务子系统边界和核心 Agent
数量，不推翻原设计，不新增业务子系统。重点修订实施顺序、首期范围、阶段职责、契约映射、工程准入和验收要求。

### 0.2 本次变更

  ----------------------------------------------------------------------------------------------------------------------------------
  编号                    修改项                  v1.1 修订
  ----------------------- ----------------------- ----------------------------------------------------------------------------------
  R01                     阶段数量                统一为十四个阶段（阶段 0～13）

  R02                     TeamFlow                首期缩小为登录/测试账户、Workspace、项目创建、列表、详情；编辑建议实现，删除可选

  R03                     实施顺序                阶段 5、6 提前建设 Explorer Agent 和 TestCase Compiler

  R04                     阶段 2/3                阶段 2 是本地同步确定性执行内核；阶段 3 是平台化异步调度

  R05                     Diagnosis               拆分 Symptom、Failure Domain、Failure Mechanism、Root Cause、Contributing
                                                  Factor、Flaky Characteristic、Diagnosis Status、Disposition

  R06                     Benchmark               阶段 1 建立数据记录基础；阶段 11 建设完整 Benchmark

  R07                     Security                阶段 0 建立安全基础；阶段 12 完成系统性安全审计

  R08                     阶段准入                每个阶段统一定义前置条件、输入、任务、非目标、依赖、交付、验收和退出条件

  R09                     Event                   明确至少一次投递、幂等消费、生产者实例内顺序，不承诺全局强排序

  R10                     RepairProposal          增加验证、审批、晋升、晋升失败和回滚状态

  R11                     技术版本                增加依赖锁定文件、兼容矩阵和验证记录

  R12                     外部应用                首轮只选 1～2 个明确授权的 Web 应用

  R13                     集成验收                各里程碑持续集成；阶段 13 是最终全量回归和发布验收
  ----------------------------------------------------------------------------------------------------------------------------------

### 0.3 保留的核心原则

-   Agent 是能力层，不是事实层。
-   Playwright 负责正式测试的确定性执行。
-   Oracle 独立于 Agent，正式预期必须有来源和审批。
-   Task 与 Run 分离；执行状态、测试判定、诊断状态和诊断结论分离。
-   Event 与 Evidence 分离；原始证据可追溯。
-   相关不等于因果；证据不足允许输出 INCONCLUSIVE。
-   自愈不得修改正式 Oracle 或削弱必需 Assertion。
-   Benchmark 和安全从项目早期开始准备，而不是最后补做。

------------------------------------------------------------------------

# 1. 项目定位与最终目标

## 1.1 项目定位

**TraceGuard：基于 Agent 的智能 Web UI 自动化测试与质量诊断平台。**

以专业 Web UI 自动化测试框架为工程底座，引入 Agent
自主探索和测试生成能力，并通过可信 Oracle、多源
Evidence、故障诊断和受保护的测试自愈，降低测试设计、执行分析和维护成本。

TraceGuard 不是单纯的浏览器 Agent Demo，也不是只做日志分析的 AIOps
系统。UI 自动化测试是核心业务，Agent
是提升测试生产和质量分析能力的智能层。

## 1.2 核心业务闭环

``` mermaid
flowchart TD
    A["TestIntent / 自然语言测试目标"]
    B["Explorer Agent"]
    C["Exploration Trace"]
    D["TestCase Compiler"]
    E["Candidate TestCase"]
    F["人工审核 TestCase 与 Oracle"]
    G["正式 TestCaseVersion"]
    H["Playwright 确定性执行"]
    I["Trusted Oracle 验证"]
    J["Event / Evidence"]
    K["Diagnosis / Evidence Graph"]
    L["Active Diagnosis / Probe"]
    M["Diagnosis + Disposition"]
    N["Repair Proposal"]
    O["沙箱验证 / 审批 / 回归"]
    P["新 TestCaseVersion"]

    A --> B --> C --> D --> E --> F --> G --> H --> I --> J
    J --> K --> L --> M
    M -->|"测试资产问题"| N
    M -->|"产品缺陷"| Q["Defect Record"]
    M -->|"环境问题"| R["Environment Handling"]
    M -->|"证据不足"| S["INCONCLUSIVE / 补充证据"]
    N --> O --> P
    P --> H
```

不是所有失败都进入修复。产品缺陷、测试资产问题、环境问题、Agent/平台问题和证据不足必须进入不同处置路径。

## 1.3 最终产品组成

  --------------------------------------------------------------------------------------------
  组成                                最终能力
  ----------------------------------- --------------------------------------------------------
  Web 工作台                          项目、测试资产、执行、证据、诊断、修复和实验管理

  UI 自动化内核                       Playwright、pytest、TestCase、Oracle、Assertion、Test
                                      Verdict

  执行编排平台                        Task、Run、Step、Worker、重试、取消、历史和实时事件

  Agentic Testing                     Explorer Agent、Exploration Trace、TestCase Compiler

  证据基础设施                        Event、Evidence、附件、来源、完整性和追溯

  诊断系统                            Diagnosis、Evidence Graph、Active Diagnosis、Probe

  安全自愈                            RepairProposal、断言保护、沙箱、审批、回归、晋升和回滚

  UI-FaultBench                       测试生成、诊断、自愈三类评测

  TeamFlow                            受控被测应用、故障注入和独立 Ground Truth
  --------------------------------------------------------------------------------------------

------------------------------------------------------------------------

# 2. 五层建设路线、十四阶段和六个里程碑

## 2.1 五层建设路线

  --------------------------------------------------------------------------------------------------------
  建设层次                         阶段                    建设重点
  -------------------------------- ----------------------- -----------------------------------------------
  第一层：UI 自动化基础设施        0～3                    工程环境、TeamFlow、Playwright 内核、执行平台

  第二层：测试平台与证据基础设施   4                       Event、Evidence、证据存储和追溯

  第三层：Agentic Testing          5～6                    Explorer Agent、TestCase Compiler

  第四层：智能诊断与安全自愈       7～10                   Diagnosis、Evidence Graph、Active
                                                           Diagnosis、Self-Healing

  第五层：评测与工程化交付         11～13                  UI-FaultBench、外部应用、安全可靠性、最终交付
  --------------------------------------------------------------------------------------------------------

## 2.2 十四个实施阶段

  -------------------------------------------------------------------------------------------------------------
  阶段                    名称                                 核心交付
  ----------------------- ------------------------------------ ------------------------------------------------
  0                       工程初始化与设计基线                 工程骨架、依赖锁定、CI、安全基础

  1                       TeamFlow 最小被测应用与故障注入      项目创建业务路径、Ground Truth、故障场景

  2                       专业 Web UI 自动化测试框架           本地同步执行内核、TestCase、Oracle、Playwright

  3                       任务调度、执行编排与测试管理工作台   Task/Run/Step、异步 Worker、SSE、执行历史

  4                       Event 与 Evidence 证据基础设施       事件可靠性、证据采集、存储和追溯

  5                       Explorer Agent 自主 Web 探索         自然语言目标到 Exploration Trace

  6                       TestCase Compiler 智能测试用例编译器 Trace 到可审核、可回放的 Candidate TestCase

  7                       基础故障分类与 Diagnosis v1          结构化故障诊断和处置分流

  8                       Evidence Correlation Graph           多源证据关联、支持/反驳关系

  9                       Active Diagnosis Planner             受控探针、信息增益、预算和停止条件

  10                      Oracle-Protected Self-Healing        安全修复、审批、回归、晋升和回滚

  11                      UI-FaultBench 完整 Benchmark         三类评测、对照实验、消融和报告

  12                      外部 Web 应用泛化、安全与可靠性强化  1～2 个授权外部应用、安全审计和性能验证

  13                      全链路集成、最终验收与交付           最终回归、发布包、演示和技术资料
  -------------------------------------------------------------------------------------------------------------

## 2.3 六个里程碑

  --------------------------------------------------------------------------------------------------------------------
  里程碑            对应阶段          核心交付                    退出目标
  ----------------- ----------------- --------------------------- ----------------------------------------------------
  M0                0～1              工程环境与 TeamFlow         被测应用可重复运行、重置、注入故障并查询 Ground
                                                                  Truth

  M1                2～3              专业 UI 自动化测试平台      TestCase 可通过平台创建、执行、判定和追溯

  M2                4～6              证据基础设施与 Agentic      自然语言目标生成审核后的正式测试资产并独立回放
                                      Testing                     

  M3                7～9              Evidence Graph 与智能诊断   失败可分类、关联证据并通过受控 Probe 补证

  M4                10                安全测试自愈                修复通过断言保护、审批、回归后可晋升和回滚

  M5                11～13            Benchmark、泛化和完整交付   核心能力可重复评测，系统通过安全、稳定性和最终验收
  --------------------------------------------------------------------------------------------------------------------

## 2.4 阶段依赖图

``` mermaid
flowchart TD
    P0["0 工程初始化"] --> P1["1 TeamFlow"]
    P1 --> P2["2 UI 自动化内核"]
    P2 --> P3["3 执行编排平台"]
    P3 --> P4["4 Event / Evidence"]
    P4 --> P5["5 Explorer Agent"]
    P5 --> P6["6 TestCase Compiler"]
    P4 --> P7["7 Diagnosis v1"]
    P6 --> P7
    P7 --> P8["8 Evidence Graph"]
    P8 --> P9["9 Active Diagnosis"]
    P6 --> P10["10 Self-Healing"]
    P9 --> P10
    P10 --> P11["11 UI-FaultBench"]
    P1 --> P11
    P11 --> P12["12 外部应用 / 安全 / 可靠性"]
    P12 --> P13["13 最终验收"]
```

图中表示主要依赖，不代表所有工作必须严格串行。契约、Mock、数据准备和前端原型可以提前开展，但正式集成必须满足阶段准入条件。

## 2.5 两条核心产品线

**智能测试生产线：**

``` mermaid
flowchart LR
    A["TestIntent"] --> B["Explorer Agent"]
    B --> C["Exploration Trace"]
    C --> D["TestCase Compiler"]
    D --> E["Candidate TestCase"]
    E --> F["Human Review"]
    F --> G["Trusted Oracle Binding"]
    G --> H["Formal TestCaseVersion"]
    H --> I["Playwright Replay"]
```

**智能质量诊断线：**

``` mermaid
flowchart LR
    A["Run / Event / Evidence"] --> B["Diagnosis v1"]
    B --> C["Evidence Graph"]
    C --> D["Active Diagnosis"]
    D --> E["Authorized Probe"]
    E --> F["New Evidence"]
    F --> G["Updated Diagnosis"]
    G --> H["Disposition"]
```

------------------------------------------------------------------------

# 3. 各阶段统一实施规格

每个阶段必须写清楚：

1.  目标；
2.  前置条件；
3.  输入；
4.  实施任务；
5.  明确不做；
6.  接口与契约依赖；
7.  交付物；
8.  验收标准；
9.  退出条件；
10. 风险与待确认事项。

## 阶段 0：工程初始化与设计基线

**目标：**
建立可复现、可协作、可持续集成的工程环境，并落实基础工程、安全和版本治理。

**前置条件：** 总体架构 v1.1 已评审；仓库和开发环境可用。

**输入：** 总体架构、C01～C12 契约地图、技术选型、安全要求和开发规范。

**实施任务：**

1.  建立仓库和建议目录：`apps/api`、`apps/web`、`apps/teamflow`、`apps/workers`、`packages/contracts`、`packages/core`、`packages/execution`、`packages/agents`、`packages/evidence`、`benchmark`、`tests`、`infra`、`docs`。
2.  建立 FastAPI、Vue 3 + TypeScript 基础工程。
3.  建立 PostgreSQL 连接、迁移和健康检查。
4.  建立统一配置、环境变量、结构化日志和错误处理。
5.  建立 Lint、Format、Type Check、Unit Test 和 CI。
6.  建立技术版本锁定：Python、Node.js、FastAPI、Vue、Playwright、pytest、Browser
    Use、LangGraph、PostgreSQL、Redis、Celery、Docker 等。
7.  建立兼容矩阵、锁定文件、升级记录和回滚策略。
8.  通过官方兼容资料和最小 PoC
    验证关键组合；不凭空填写未经验证的版本号。
9.  落实密钥不入库、最小权限、基础身份认证、日志脱敏、文件访问边界和审计。
10. 建立 C01～C12 Schema 目录和版本治理规范。

**基础设施启用：**

  阶段   必需基础设施
  ------ ------------------------------
  0      PostgreSQL、基础 Docker 环境
  3      Redis、Celery
  4      MinIO 或兼容对象存储
  后续   按实际模块增加

Redis、Celery、MinIO 可预留配置，但不应成为阶段 0 的硬性阻塞项。

**明确不做：**
不开发完整业务模块；不提前拆微服务；不强制启用暂时无使用场景的基础设施。

**契约依赖：** 建立 C01～C12 目录、版本和校验规范；建立统一 API
错误响应约定。

**交付物：**
工程仓库、目录说明、技术选型表、版本锁定文件、兼容矩阵、Docker
基础配置、前后端基础服务、数据库迁移、CI、安全基线和启动文档。

**验收标准：** 前后端可启动；PostgreSQL 可连接并迁移；Lint、Type
Check、Unit Test 可运行；CI 有效；敏感配置不入库；新环境可按文档复现。

**退出条件：** 工程环境可复现，基础 CI、安全基线和依赖版本记录完整。

------------------------------------------------------------------------

## 阶段 1：TeamFlow 最小被测应用与故障注入

**目标：** 建立足以支撑首期 UI 自动化和诊断闭环的受控 Web
应用，不建设完整项目协作 SaaS。

**前置条件：** 阶段 0 环境可用，数据库和基础服务可用。

**首期范围：**

  功能             首期要求
  ---------------- ----------------
  登录与测试账户   必须，最小实现
  Workspace        必须，最小实现
  项目创建         必须
  项目列表         必须
  项目详情         必须
  项目编辑         建议实现
  项目删除         可选
  任务管理         后续扩展
  评论和活动记录   暂不实现
  复杂角色权限     后续扩展
  搜索系统         暂不实现

**核心业务路径：**

``` mermaid
flowchart TD
    A["登录测试账户"] --> B["进入 Workspace"]
    B --> C["项目创建页面"]
    C --> D["填写表单"]
    D --> E["提交创建"]
    E --> F["页面显示成功"]
    F --> G["项目列表出现"]
    G --> H["独立业务状态查询"]
    H --> I["核对持久化状态"]
```

**必须实现：**

1.  独立业务状态查询，不能只依赖 UI 证明业务成功。
2.  可重复初始化和清理测试数据。
3.  可控、可重复、可清理的故障注入。
4.  Ground Truth 独立于被测页面和 Agent 权限。
5.  Benchmark 场景元数据从本阶段开始记录。

**首批故障：**

  ID            故障           示例
  ------------- -------------- ------------------------
  TF-API-001    API 异常       创建接口返回 500
  TF-NET-001    网络超时       请求延迟或超时
  TF-DB-001     持久化失败     事务回滚或写入失败
  TF-UI-001     UI 表现异常    成功提示缺失或错误显示
  TF-TIME-001   异步时序异常   列表刷新延迟或竞态

每个场景记录
scenario_id、fault_mechanism_id、注入配置、预期影响、TeamFlow
版本、数据种子、Ground Truth 引用和清理方法。

**明确不做：** 不开发完整
SaaS；不做复杂权限矩阵；不实现任务、评论、搜索；不允许 Agent 读取 Ground
Truth。

**契约依赖：** TeamFlow API、业务状态查询、测试数据初始化/清理、Fault
Injection、评测侧 Ground Truth；C12 基础元数据。

**交付物：** TeamFlow 最小 Web、项目创建/列表/详情、最小认证和
Workspace、业务 API、独立状态查询、数据重置、五类故障注入、Ground Truth
和场景文档。

**验收标准：**
正常创建可重复；列表/详情与业务状态一致；五类故障可注入和清理；Ground
Truth 不暴露给 Agent；数据可重置。

**退出条件：** 核心业务路径稳定，至少五类故障可重复实验，Ground Truth
隔离通过。

------------------------------------------------------------------------

## 阶段 2：专业 Web UI 自动化测试框架

**目标：** 交付独立可运行的 Playwright + pytest
确定性测试执行内核，不建设完整异步平台。

**前置条件：** TeamFlow 核心页面和测试数据可用。

**实施任务：**

1.  建立
    TestIntent、TestCase、TestCaseVersion、TestStep、TestDataRef、AssertionSpec、Precondition、Postcondition
    和 CleanupPolicy。
2.  建立 Browser Manager、Browser Context、Page Manager、Locator
    管理、Action Executor、Assertion Executor。
3.  采集 Screenshot、Trace、Network；Video 按需启用。
4.  集成
    pytest：Discovery、Fixture、Parameterization、Hooks、数据管理和报告。
5.  建立 Oracle Manager。每个正式 Assertion 声明 Primary
    Oracle、Supporting Oracle、预期行为、判定规则、版本和来源。
6.  AI 可以提出候选断言，但不能自行批准正式 Oracle。
7.  建立 Assertion 级结果：PASSED、FAILED、INCONCLUSIVE、NOT_EVALUATED。
8.  按下方冻结规则建立确定性 Test Verdict 聚合；Oracle
    不可用、证据缺失或冲突无法裁决时不得伪装为 PASSED。
9.  本地同步执行：加载 TestCase 和 Oracle、创建执行上下文、Playwright
    执行、Assertion 评估、Verdict 聚合、生成 Run/Step 报告。

**状态边界：**
`execution_status`、`test_verdict`、`diagnosis_status`、`diagnosis_outcome`
分离。

**Test Verdict 聚合规则（本轮冻结）：**

正式 TestCase 至少应有一个有效的必需 Assertion。不允许空的必需 Assertion
集合通过空集合逻辑自动得到 PASSED。确有不依赖 Assertion
的验证场景时，必须通过单独定义的验证模式处理，不得绕过正常 TestCase 约束。

Assertion 级状态保留：PASSED、FAILED、INCONCLUSIVE、NOT_EVALUATED。

  条件                                             Test Verdict
  ------------------------------------------------ --------------
  至少一个必需 Assertion 明确失败                   FAILED
  所有必需 Assertion 通过且执行正常、Oracle 有效    PASSED
  无明确失败，但部分必需 Assertion 无法裁决         INCONCLUSIVE
  没有任何必需 Assertion 得到有效评估               NOT_EVALUATED

补充规则：

1.  Optional Assertion 不决定最终 Test Verdict，但保留独立结果。
2.  必需 Assertion 的 Oracle 不可用时不得判为 PASSED。
3.  多个 Oracle 冲突且无法依据正式规则裁决时判为 INCONCLUSIVE。
4.  执行中断但已有部分必需 Assertion 结果时，按已知失败和未完成情况处理。
5.  已知必需 Assertion 失败时，不得因后续执行中断而掩盖失败。
6.  未完成的执行不能仅因已完成部分断言全部通过就自动判为 PASSED。
7.  NOT_EVALUATED 与 INCONCLUSIVE 必须区分：前者表示没有任何必需 Assertion 得到有效评估，后者表示已有有效评估但无法裁决。
8.  `execution_status` 与 `test_verdict` 永远独立，不由其中一方推导另一方。

上述第 3 条是已冻结的兜底规则，不依赖 7.2 中的 P03：在 Oracle 多来源细则确认前，无法裁决的冲突一律按 INCONCLUSIVE 处理，不得擅自设定 Primary Oracle 覆盖 Supporting Oracle，也不得自行设定未经讨论的可信等级。P03 只负责多来源优先级、可信等级与复杂冲突的裁决细则，不阻塞本节基础规则的冻结。

Task 与多个 Run 的关系：每次重试产生新的 Run，每个 Run 有独立 Test
Verdict；Task 层不得通过简单覆盖方式丢失历史 Run。若提供 Task 级汇总，必须明确标注为展示汇总，不得冒充单次
Run 的 Test Verdict。

合法样例：

-   所有必需 Assertion 通过、执行正常完成且 Oracle 有效 → PASSED；
-   一个必需 Assertion 失败 → FAILED；
-   必需 Assertion 部分未评估、其余通过且无失败 → INCONCLUSIVE；
-   所有必需 Assertion 均未评估 → NOT_EVALUATED；
-   Oracle 不可用 → 全部必需 Assertion 的 Oracle 均不可用时取 NOT_EVALUATED，部分不可用且其余通过、无失败时取 INCONCLUSIVE；任何情况下不得 PASSED；
-   Oracle 冲突且无法依据正式规则裁决 → INCONCLUSIVE；
-   Optional Assertion 失败但必需 Assertion 全部通过 → PASSED，Optional 结果单独保留。

非法样例：

-   没有必需 Assertion 的 TestCase → 非法，不得通过空集合逻辑得到 PASSED；
-   必需 Assertion 失败却因后续执行中断而判为 PASSED → 非法；
-   未完成执行但已通过部分断言即判为 PASSED → 非法。

**明确不做：** 不建设 Celery 分布式调度；不引入 Browser Use；不让 Agent
直接决定测试结果；不原地覆盖 TestCaseVersion。

**契约依赖：** C01、C02、C03、C04 初始版本。

**交付物：** Playwright 内核、pytest 集成、TestCase/Version、Oracle
Manager、Assertion Evaluator、Verdict Aggregator、本地 Run/Step
和测试样例。

**验收标准：** 10～20 条 TeamFlow
用例；覆盖正常、异常、表单、列表、权限边界和网络场景；TestCase 可脱离
Agent 独立执行；Oracle 独立验证；Test Verdict
聚合规则及合法/非法样例可执行验证，NOT_EVALUATED 与 INCONCLUSIVE
可区分，`execution_status` 与 `test_verdict` 独立；历史 Run 不覆盖；重复执行可核对。

**退出条件：** 不依赖 Agent 和异步平台即可完成确定性 UI 测试闭环。

------------------------------------------------------------------------

## 阶段 3：任务调度、执行编排与测试管理工作台

**目标：** 将阶段 2
的执行内核接入平台，形成持久化、异步、可追踪的测试执行系统。

**前置条件：** 阶段 2 执行内核稳定，C04 初始结构明确。

**对象边界：**

-   Task：逻辑测试任务；
-   Run：一次实际执行尝试；
-   Step：Run 内的步骤；
-   Assertion Result：单个预期的判定结果；
-   Test Verdict：每个 Run 的判定结果，与 Run 的 `execution_status` 独立。

同一 Task 可以产生多个 Run。每次重试或再次执行都必须保留独立
Run，不覆盖历史；Task 层汇总只是展示，不得冒充单次 Run 的 Test Verdict。

**实施任务：**

1.  Task 创建、查询、取消和状态管理。
2.  Run 排队、分发、运行、完成、失败、取消和超时状态。
3.  Redis Broker、Celery Worker、队列隔离和并发限制。
4.  Worker 心跳、租约续租、超时、取消、重试、死信和资源回收。
5.  Task 提交幂等；重复投递不得产生不可控副作用。
6.  Worker 崩溃后按持久化状态恢复或终止；接管前校验 Run 所有权与执行代次。
7.  重试创建新 Run。
8.  SSE 实时推送。
9.  工作台：项目、TestCase、Task、Run、Step、Assertion、报告和历史。

**Run 执行租约与执行代次（Fencing）：**

1.  每个 Run 绑定执行租约 Lease：Worker 获取 Run 后写入租约所有者与到期时间，通过心跳续租。
2.  租约过期由持久化状态判定；过期后可接管执行权，接管必须伴随单调递增的执行代次（Fencing Token 或等效代次）。
3.  只有当前有效执行代次的 Worker 允许提交 Run 状态和正式结果；写入时校验代次，旧代次提交被拒绝。
4.  Worker 失去执行权后不得继续提交有效结果；恢复时必须先检查当前 Run 所有权与代次，不一致则停止本地提交。
5.  首期以 PostgreSQL 持久化状态、租约和幂等提交为基础，不实现复杂分布式事务。

**副作用边界：** Fencing Token 只保护受其校验的写入操作，不能自动撤销已经发送到浏览器或外部系统的副作用。若旧 Worker 是否已执行某个业务动作无法确认，则不得直接无条件接管并重复执行；应先隔离或重置测试数据，必要时将原 Run 标记为结果不确定（用既有状态加说明或派生记录表达，不新增 Run 状态），重试必须创建新的 Run。

**故障注入测试：** 心跳中断导致租约过期、旧 Worker 延迟提交被拒绝、接管前后副作用确认、Worker 恢复时所有权校验。

**明确不做：** 不重写 Playwright 内核；不合并 Task 和
Run；不承诺跨所有生产者全局强排序；不以 Fencing Token 代替副作用隔离。

**契约依赖：** C02、C03、C04；预留 C05 事件生产接口。

**交付物：** Task Service、Execution Orchestrator、Celery Worker、Run
状态机、租约与执行代次、重试/取消/超时/恢复、故障注入测试、SSE 和工作台。

**验收标准：** 前端创建 Task；Worker 调用本地执行内核；一个 Task
可产生多个 Run；实时进度有效；取消、超时、重试和 Worker
恢复可验证；租约过期后旧 Worker 提交被拒绝且重试创建新 Run；故障注入用例覆盖副作用边界；历史不覆盖。

**退出条件：** 本地执行内核已平台化，Task/Run/Step
生命周期和异常分支通过测试。

------------------------------------------------------------------------

## 阶段 4：Event 与 Evidence 证据基础设施

**目标：** 建立统一事件和证据基础设施，为 Explorer、Diagnosis、Graph 和
Benchmark 提供可追溯数据。

**前置条件：** Run/Step 稳定，执行日志和截图可获取。

**Event Envelope 首期字段：**

``` json
{
  "event_id": "evt_x",
  "context_refs": {
    "workspace_id": "ws_x",
    "project_id": "proj_x",
    "task_id": "task_x",
    "run_id": "run_x",
    "exploration_id": null,
    "probe_execution_id": null,
    "experiment_id": null,
    "trace_id": "trace_x",
    "span_id": "span_x",
    "request_id": "req_x"
  },
  "producer_id": "playwright",
  "producer_instance_id": "worker-instance-x",
  "sequence": 12,
  "event_type": "network.request.failed",
  "occurred_at": "2026-11-17T10:00:00Z",
  "observed_at": "2026-11-17T10:00:01Z",
  "idempotency_key": "worker-instance-x:12",
  "schema_version": "1.0.0",
  "payload": {}
}
```

`context_refs` 字段说明：

  字段                  类型          语义                 必填
  --------------------- ------------ -------------------- ------
  workspace_id          string       所属 Workspace       是
  project_id            string/null  所属 Project         否
  task_id               string/null  关联 Task            否
  run_id                string/null  关联 Run             否
  exploration_id        string/null  关联 Explorer 探索   否
  probe_execution_id    string/null  关联 ProbeExecution  否
  experiment_id         string/null  关联 Benchmark 实验  否
  trace_id              string/null  分布式追踪关联       否
  span_id               string/null  当前追踪 Span        否
  request_id            string/null  请求关联             否

`workspace_id` 的必填约束适用于 Workspace 作用域内的业务事件；平台级系统事件的边界见下方说明，不得通过虚构 Workspace 归属来满足该约束。

**Event 上下文关联规则：**

1.  `run_id` 不再是所有事件的强制关联字段；按事件实际来源填写适用关联，一个 Event 可同时关联多个上下文对象。
2.  `workspace_id` 必填，且只能由服务端可信上下文（已认证主体、Token 或由其派生的 Task/Run 归属）注入；禁止从不可信事件 `payload` 自行决定授权 Workspace。
3.  不同上下文对象的关联必须可追溯：每条关联记录其来源与写入方式（服务端注入 / 采集器绑定 / 消费者解析）。
4.  关联对象不存在或无法解析时保留原始关联值并标记为 `unresolved`，不得自动解释为已验证关系，也不得静默丢弃。
5.  事件消费者必须能识别事件实际携带的上下文类型（依据 `context_refs` 中出现的字段），未知关联对象不得被解释为已验证关系。
6.  `context_refs` 只表示关联，不表示因果；因果判定另见阶段 8。
7.  原始 Event 不可变原则保持不变；关联更正只通过新事件或派生记录表达。

**Workspace 作用域边界：** 首期 C05 主要规范 Workspace 作用域内的业务事件。这类 Event 的 `workspace_id` 必填，且必须由服务端可信上下文注入；不允许从不可信 `payload` 中直接获取 Workspace 授权归属。

**平台级系统事件边界：**

1.  平台级系统遥测、基础设施运行日志和全局健康事件（如 Worker 健康状态、全局调度器运行事件、平台维护事件、全局服务运行异常），不因为进入统一可观测体系就自动绑定 Workspace。
2.  这类信息首期沿用独立的系统级遥测和访问控制机制，不纳入 C05 的 Workspace 业务事件规范。
3.  不得通过填入虚假 Workspace ID 满足 C05，不得增加默认 Workspace，也不得允许客户端自行指定授权 Workspace。
4.  如果未来确实需要把平台级系统事件统一纳入 Event Store，必须单独定义 system-level scope、身份来源和访问控制规则。
5.  在完成对应契约评审前，不得放宽 C05 的 `workspace_id` 必填约束，不得将系统级事件错误归属到普通用户 Workspace，也不得擅自扩展 C05 为全平台通用事件协议。

**Event 可靠性规则：**

-   首期采用至少一次投递、消费端幂等；
-   原始事件只追加，不原地修改；
-   修正通过新事件或派生记录表达；
-   允许乱序到达；
-   使用 Outbox 或等效机制降低业务提交与事件发布不一致；
-   不追求复杂 exactly-once。

**序号（sequence）语义：**
仅采用"producer_instance 内单调递增"一种语义，不另设 event stream 序号，避免双重语义冲突。

1.  唯一性范围为 `(producer_instance_id, sequence)`；序号由该实例内并发安全的原子分配器（数据库序列或事务内分配）产生，禁止并发写入同一序号。
2.  不同 producer_instance / 不同 producer 之间不保证全局顺序，也不保证跨实例序号可比。
3.  `sequence` 只提供其定义范围内的顺序信息，不自动表示业务因果关系。
4.  Worker 重启后生成新的 `producer_instance_id` 并从新序列空间重新计数，旧序列空间不复用。

该序号不承诺连续无间断；合法跳号与缺口判断见下方说明。

**幂等键（idempotency_key）语义：**

1.  在事件创建时生成，重试与重复投递时保持不变；唯一性范围为 producer 维度（建议 `(producer_id, idempotency_key)` 唯一约束）。
2.  重复投递由唯一约束识别：已存在则确认消费结果，不重复产生业务副作用。
3.  同一幂等键对应不同 payload 时判定为冲突，拒绝写入并记录审计，不静默覆盖原事件。
4.  去重记录与 Event 生命周期一致保存；具体保留期属于待确认事项，见 7.2。

**Consumer Inbox（消费端幂等记录）：**

采用 Consumer Inbox 或等效机制，保障消费者在至少一次投递条件下实现业务处理幂等。生产者侧的 `idempotency_key` 只能解决生产者重复投递，无法覆盖"消费者已执行业务操作、但在消息确认前崩溃、消息重新投递"的场景，因此消费者必须有独立的去重与处理状态记录。

基本要求：

1.  每个消费者维护独立的消费去重记录，以 `(consumer_id, event_id)` 作为消费唯一性依据。
2.  同一个 Event 可以被多个不同消费者分别处理，彼此处理状态互不覆盖。
3.  同一个消费者重复收到已成功处理的 Event 时，不得重复执行本地业务副作用。
4.  消费记录与本地业务副作用应在同一个数据库事务中提交。
5.  处理失败时不得出现"消费记录显示成功但业务副作用未提交"的不一致状态。
6.  消费者崩溃后重新收到事件时，应能根据持久化消费记录正确恢复。
7.  消费记录属于 Event 消费基础设施，不得写入原始 Event，也不得新增为 Event Envelope 字段。

事务边界：

-   情况 A（本地数据库副作用）：消费记录写入、本地业务数据更新与消费成功状态提交必须在同一个数据库事务中完成；事务失败时整体回滚，避免部分提交。
-   情况 B（外部系统副作用）：若需调用外部 API、浏览器或其他无法纳入同一数据库事务的系统，不得宣称数据库事务能保证外部操作原子性；应通过下游幂等键、Outbox、补偿操作或等效机制控制重复副作用；无法确认外部操作是否成功时不得盲目重复执行，并保留可追溯处理记录。

范围约束：不引入新的消息中间件，不将至少一次投递改为 exactly-once 承诺，不重构 Event Envelope，不新增独立业务子系统。Consumer Inbox 属于 Event 消费基础设施，不属于新的业务 Agent；具体表结构和字段在阶段 4 详细设计时冻结，本次只冻结行为约束与事务语义。

**追踪、时间与关联：**

-   `trace_id` 用于分布式追踪关联，`span_id` 标识追踪单元，`request_id` 用于请求关联；三者都是关联线索，不是因果依据。
-   `occurred_at` 表示事件实际发生时间，`observed_at` 表示系统观察到该事件的时间，二者不得互换。
-   不得仅凭时间接近、相同 `trace_id` 或相同 `request_id` 自动生成 `direct_causal` 关系。

**事件缺口检测：**

1.  消费者按 `(producer_instance_id, sequence)` 检查序号缺口，不跨生产者实例检查。
2.  发现缺口时先标记为"待确认"：可能是合法未使用序号、在途延迟事件或真实丢失。
3.  结合 `observed_at` 与可配置确认窗口区分延迟与丢失；窗口具体值待确认，不在本轮冻结。
4.  生产者重启后以新的 `producer_instance_id` 开启新序列空间，缺口检测按新空间重新开始。
5.  缺失事件记录为"缺口 / 疑似缺失"并保留证据，不得直接标记为永久丢失。

**Sequence 缺口的合法来源与判断规则：**

1.  `sequence` 表示生产者实例内的顺序信息，不承诺序号连续无间断。
2.  PostgreSQL 普通 Sequence 的取值分配通常不随事务回滚而回滚；事务回滚、进程中断、序号分配失败等情况都可能产生合法跳号，即使没有 Event 丢失也可能观察到缺口。
3.  观察到序号缺口时，不能仅凭缺失数字认定 Event 丢失。
4.  必须区分合法跳号、在途延迟、尚未确认的事件和真实丢失。
5.  缺口判断应结合生产者实例状态、事件持久化记录、已提交事件范围和可用的生产者进度信息。
6.  如果无法获得足够证据，继续保留"待确认 / 疑似缺失"状态，不得伪造结论。
7.  不得仅因为等待了固定时间就认定 Event 永久丢失。
8.  不同 `producer_instance` 之间仍然不进行全局序号比较。

**关于生产者高水位：** 可在阶段 4 详细设计中考虑 Producer Checkpoint 或已提交事件高水位，用于辅助判断事件是否仍在途。本轮不要求新增正式 C05 字段：如果确实需要引入，应先作为内部进度记录或派生状态设计，明确其可信来源，明确它表示已分配、已提交还是已发布，不得把已分配序号直接当作已成功发布的事件，并在阶段 4 详细设计时再决定是否需要正式契约扩展。不得因此重构当前 Sequence 设计。

不将时间接近解释为因果关系。

**Evidence 分类：**

  类型                  示例
  --------------------- -------------------------------
  Raw Evidence          截图、HAR、原始日志、响应快照
  Normalized Evidence   规范化网络事件、DOM 摘要
  Derived Evidence      规则计算结果、模型分析片段

派生 Evidence 必须保留来源、生成规则或模型版本，不得冒充原始事实。

**实施任务：** Event Collector、Event Store、Outbox/Consumer Inbox 幂等消费、`context_refs` 绑定与 Workspace 注入、序号与缺口检测、Evidence
Manager、对象存储、SHA-256、来源、脱敏、检索、过期和清理。

**明确不做：** 不实现完整 Graph；不以 LLM
摘要替代原始证据；不承诺全局强排序。

**契约依赖：** C04、C05、C06。

**交付物：** Event/Evidence
Schema、Collector、Store、Manager、对象存储适配、查询 API 和 Evidence
Viewer。

**验收标准：** Run/Step/Event/Evidence
可关联；Event 可按 `context_refs` 关联多种上下文对象且 `workspace_id` 来源可信；重复投递不产生重复业务结果；同键异载荷冲突被拒绝；序号缺口、乱序和实例重启可识别且缺口不被误判为永久丢失；原始事件不可静默修改；证据完整性和脱敏有效。

Consumer Inbox 验收场景：

1.  同一消费者重复收到相同 Event，只产生一次本地业务副作用；
2.  两个不同消费者收到同一个 Event，都能独立完成处理；
3.  消费者处理过程中崩溃，恢复后不会重复提交已成功的业务副作用；
4.  本地事务失败时，消费成功记录不会错误提交；
5.  外部副作用调用失败或结果不确定时，不会无条件重复执行；
6.  消费记录可追溯到原始 Event。

Workspace 边界验收场景：

1.  Workspace 业务事件必须具有可信 `workspace_id`；
2.  客户端伪造 `workspace_id` 时，服务端不得接受其作为授权依据；
3.  平台级系统事件不会被强制归属到虚假 Workspace；
4.  系统级遥测与 Workspace 业务事件的访问边界清晰；
5.  后续如需统一 Event Store，有独立契约扩展路径。

Sequence 缺口验收场景：

1.  合法跳号不会被直接判定为 Event 丢失；
2.  事务回滚产生的序号缺口不会导致错误告警；
3.  在途延迟事件可以继续等待确认；
4.  真实缺失事件能够保留疑似缺失记录及判断依据；
5.  不同 `producer_instance` 的序号不会被错误比较；
6.  无法确定缺口原因时，系统保留不确定状态，不伪造结论。

**退出条件：** 事件和证据可持久化、检索、校验、恢复和追溯。

------------------------------------------------------------------------

## 阶段 5：Explorer Agent 自主 Web 探索

**目标：** 根据自然语言 TestIntent 自主探索 TeamFlow，生成结构化
Exploration Trace。

**前置条件：** TeamFlow、Playwright、Event/Evidence 和 Tool Gateway
基础可用。

**技术职责：**

-   Browser Use：浏览器探索和操作决策；
-   LangGraph：有状态探索工作流；
-   Playwright Adapter：受控浏览器能力和页面观测；
-   Tool Gateway：权限、参数、域名和预算检查；
-   Event/Evidence：保存探索过程。

Browser Use 与 Playwright 的会话集成方式必须通过 PoC
验证，不假定可以无条件共享同一浏览器上下文。

**探索工作流：**

``` mermaid
flowchart TD
    A["TestIntent"] --> B["Explorer Agent"]
    B --> C["Page Observation"]
    C --> D["Page Understanding"]
    D --> E["Action Planning"]
    E --> F["Tool Gateway"]
    F --> G["Browser Action"]
    G --> H["New Page State"]
    H --> I["Exploration Trace"]
    I --> J{"目标完成或预算耗尽？"}
    J -->|"继续"| B
    J -->|"结束"| K["Exploration Summary"]
```

**首批场景：**
登录、页面访问、页面理解、表单填写、按钮操作、页面跳转、项目创建、列表核对和详情查看。

**Trace 至少记录：**
exploration_id、test_intent_ref、application_version、browser_version、agent_version、prompt_version、tool_version、action_sequence、page_state_ref、event_refs、evidence_refs、stop_reason。

**安全约束：**
只访问批准域名；页面内容是非可信数据；页面不能授予工具权限；默认禁止任意文件访问；凭证不进入普通
Prompt/日志；创建操作限定在可清理测试数据；设置步骤、时间和模型调用预算。

**明确不做：** 不把探索成功视为测试通过；不直接晋升正式 TestCase；不批准
Oracle；不开放任意网络、文件或 Shell。

**契约依赖：** C01、C05、C06。

**交付物：** Explorer Agent、Workflow、Browser Adapter、Tool Gateway
集成、Exploration Trace、报告和前端过程展示。

**验收标准：**
自然语言目标可探索；关键动作有记录；截图、页面状态和事件可查；预算生效；越权被拒绝；页面注入式指令不能提升工具权限。

**退出条件：** Agent 能在授权范围内自主探索并生成完整、结构化、可追溯的
Trace。

------------------------------------------------------------------------

## 阶段 6：TestCase Compiler 智能测试用例编译器

**目标：** 把 Exploration Trace 转换为可审核、可独立回放、具有可信
Oracle 绑定的 Candidate TestCase。

**前置条件：** 执行内核、Event/Evidence、Explorer 和 Oracle Manager
可用。

**编译流水线：**

``` mermaid
flowchart TD
    A["Exploration Trace"] --> B["Trace Normalizer"]
    B --> C["Action Extraction"]
    C --> D["Locator Resolver"]
    D --> E["Assertion Constructor"]
    E --> F["Oracle Binding"]
    F --> G["Static Validation"]
    G --> H["Repeated Replay"]
    H --> I["Human Review"]
    I --> J["Formal TestCaseVersion"]
    G -->|"失败"| X["Reject / Revise"]
    H -->|"失败"| X
    I -->|"拒绝"| X
```

**核心模块：**

-   Trace Normalizer：规范化动作、去除无关操作、保留必要前置条件；
-   Action Extractor：识别业务动作和页面状态；
-   Locator Resolver：优先 role、accessible
    name、label、placeholder、test id；经验证 CSS；XPath 有限兜底；
-   Assertion Constructor：生成候选断言，标记不确定预期；
-   Oracle Binder：绑定 Primary/Supporting Oracle 和需求来源；
-   Stability Validator：检查
    Locator、步骤、断言、数据、前置条件、清理策略和回放稳定性。

**来源与版本记录：**
source_exploration_id、source_trace_hash、compiler_version、prompt_version、model_version、locator_strategy_version、reviewer、approval_record、resulting_testcase_version。

**候选稳定性晋升规则：**

候选 TestCase 必须经过约定的稳定性验证策略；验证次数和通过标准根据场景及风险配置，本轮不统一固定次数。

1.  每次回放保留独立 Run，不覆盖历史。
2.  记录每次回放的 Test Verdict（PASSED / FAILED / INCONCLUSIVE / NOT_EVALUATED）、失败原因和执行环境差异。
3.  稳定性结果必须与 `compiler_version`、`browser_version`、候选 TestCase 版本关联。
4.  识别偶发成功与重复执行不一致；不稳定候选用例不得直接晋升正式 TestCaseVersion。
5.  无法确认稳定性的用例保留候选状态，等待人工处理。
6.  不得为提高通过率而降低必需 Assertion 的严格程度。
7.  重复失败不得一律解释为 Flaky，必须区分：确定性失败、环境故障、测试数据问题、不稳定表现、证据不足。

**外部应用规则：** 没有业务状态接口时，可用 UI Oracle 验证 UI
行为，但不能宣称业务持久化已验证。

**明确不做：** 不将 Trace 直接作为正式测试；不自动批准
Oracle；不绕过审核；不覆盖正式 TestCaseVersion；不把重复失败一律当作
Flaky；不以降低必需 Assertion 严格度换取通过率。

**契约依赖：** C01、C02、C03、C04、C05、C06。

**交付物：** Compiler、Normalizer、Action Extractor、Locator
Resolver、Assertion Constructor、Oracle Binder、Stability
Validator、候选用例审核和回放报告。

**验收标准：** 候选步骤可追溯到 Trace；Locator 可解析；Assertion
有预期来源；Oracle 审批不能绕过；候选用例可脱离 Explorer
独立回放；稳定性验证策略与结果可追溯，不稳定候选不晋升；重复失败分类可验证；审核通过才晋升正式版本。

**退出条件：** 完成 TestIntent → Explorer → Trace → Compiler → Candidate
→ Review → Formal TestCaseVersion → Playwright Replay 闭环。

------------------------------------------------------------------------

## 阶段 7：基础故障分类与 Diagnosis v1

**目标：** 建立基于 Run、Oracle、Event 和 Evidence 的结构化诊断基线，为
Graph 和主动诊断提供对照基础。

**Diagnosis 维度：**

  ---------------------------------------------------------------------------------------------
  维度                    回答的问题              示例
  ----------------------- ----------------------- ---------------------------------------------
  Symptom                 发生了什么              项目列表未出现新项目

  Failure Domain          属于哪个领域            TEST_ASSET / ENVIRONMENT / PRODUCT 等（见下）

  Failure Mechanism       通过什么机制表现        UI_RENDERING / TIMING / API 等（见下）

  Root Cause              根本原因                数据库事务回滚

  Contributing Factor     促成因素                数据库连接异常

  Flaky Characteristic    是否不稳定              多次执行结果不一致

  Diagnosis Status        结论可信程度            CONFIRMED、PROBABLE、INCONCLUSIVE

  Disposition             如何处置                TEST_ASSET_ISSUE / PRODUCT_DEFECT 等（见下）
  ---------------------------------------------------------------------------------------------

以上八个维度相互独立：Symptom 是观察到的现象，Failure Domain 是故障所属领域，Failure
Mechanism 是故障表现机制，Root Cause 是根本原因，Contributing Factor
是促成因素，Flaky Characteristic 是稳定性特征，Diagnosis Status
是诊断可信程度，Disposition 是最终处置方式。不得把这些维度混成一个故障分类字段；示例只引用本节正式候选枚举，不再使用未定义简写。

**Failure Domain 首期候选：**
PRODUCT、TEST_ASSET、TEST_EXECUTION、ENVIRONMENT、AGENT、PLATFORM、UNKNOWN。

**Failure Mechanism 首期候选：**
API、NETWORK、PERSISTENCE、UI_RENDERING、LOCATOR、TIMING、AUTHORIZATION、BROWSER、TEST_DATA、UNKNOWN。

Failure Domain 和 Failure Mechanism 是不同维度，不应合并为一个枚举。

**Flaky 规则：** Flaky
是稳定性特征，不是根因。需要重复执行、环境控制和结果差异证据；一次超时不能直接判定为
Flaky。

**Diagnosis Status：**

-   CONFIRMED：有该故障类别要求的权威证据、可追溯来源、无未解决关键反证，并通过确定性确认规则；
-   PROBABLE：有较强支持，但缺少权威确认或关键证据仍不足；
-   INCONCLUSIVE：证据不足、关键冲突无法裁决或数据缺失。

确认门槛按故障类别制定，不由 LLM
自行决定，也不使用所有故障统一的证据条数阈值；具体门槛仍为待确认事项（见 7.2），本轮不编造概率或评分参数。

**多 Hypothesis 与根因结构：** 一个 Diagnosis
可包含多个候选 Hypothesis，结构如下：

-   Diagnosis
    -   Hypothesis A：假设内容 / 支持证据引用 / 反驳证据引用 / 当前状态
    -   Hypothesis B：假设内容 / 支持证据引用 / 反驳证据引用 / 当前状态
    -   Final Root Cause：已确认根因或暂时为空

规则：

1.  一个 Diagnosis 可以包含多个 Hypothesis，每个 Hypothesis 有独立状态和证据引用。
2.  支持证据与反驳证据均应保留，不得只记录支持侧。
3.  未达到确认规则的 Hypothesis 不得自动成为 Root Cause；LLM 不得自行将候选 Hypothesis 宣布为 CONFIRMED。
4.  证据不足时允许没有已确认 Root Cause，此时 Diagnosis Status 取 INCONCLUSIVE。
5.  多个根因确实存在且分别满足确认规则时应分别保留，不强制压缩为一个根因。
6.  Hypothesis 状态独立维护（首期候选：CANDIDATE / SUPPORTED / CONTRADICTED / CONFIRMED / EXCLUDED，转换细则属后续详细设计），不与 Diagnosis Status 或 Disposition 混用。

**Disposition 首期候选：**
PRODUCT_DEFECT、TEST_ASSET_ISSUE、TEST_EXECUTION_ISSUE、ENVIRONMENT_ISSUE、AGENT_ISSUE、PLATFORM_ISSUE、NEEDS_MORE_EVIDENCE、MANUAL_REVIEW。

**明确不做：** 不将 Flaky 当根因；不让 LLM 宣布 CONFIRMED；不将候选
Hypothesis 直接当作根因；不强制合并多个已确认根因；不合并 Diagnosis 和
Disposition；不让所有失败进入 Repair。

**契约依赖：** C04、C05、C06、C08、C10、C12。

**交付物：** Diagnosis Service/Agent、Hypothesis 模型、Failure
Domain/Mechanism 分类、确认规则、Disposition Router、报告和初始评测集。

**验收标准：**
多维诊断、多 Hypothesis（独立状态与双向证据引用）、未确认假设不成为根因、多根因分别保留、不确定性、重复执行特征和处置分流均可验证；示例与正式枚举取值一致。

**退出条件：** Diagnosis v1 能形成可解释基线，并可用独立 Ground Truth
评测。

------------------------------------------------------------------------

## 阶段 8：Evidence Correlation Graph

**目标：** 将 Run、Step、Event、Evidence、Assertion、Hypothesis 和 Probe
组织为可查询、可追溯的关系图。

**节点：**
Run、Step、Event、Evidence、Assertion、Hypothesis、ProbeExecution、Source。Graph
节点从上游 Event 的 `context_refs` 读取关联来源，Context
本身不单独建为节点类型，避免与既有对象重复定义。

**边：**

  关系                       语义
  -------------------------- ----------------
  parent_child               结构层级
  emitted_by                 事件来源
  derived_from               派生来源
  correlated_by_trace_id     Trace 关联
  correlated_by_request_id   Request 关联
  same_execution_context     同一执行上下文
  temporal                   时间关系
  supports                   支持假设
  contradicts                反驳假设
  evaluates                  用于评估断言

`direct_causal`
仅在存在可信直接因果依据和明确规则时使用。时间接近、相同页面或相似日志不能自动建立因果边。

**上游 Event 到 Graph 的关联输入：** 阶段 4 的 `context_refs`
为 Graph 提供可追溯的关联来源。`correlated_by_trace_id`、`correlated_by_request_id`
与 `same_execution_context` 只能依据事件中已存在且通过校验的 `trace_id`、`request_id`、`run_id` /
`task_id` 等关联字段建立；未通过校验的关联保留为 unknown，不生成确定性边。每条边必须记录建立依据（来源字段、规则、版本），未知关联对象不得被自动解释为已验证关系。

**Hypothesis 与根因节点语义：** Hypothesis
是独立节点，携带自身状态与证据引用（见阶段 7）；`supports` / `contradicts`
分别指向支持与反驳证据，二者均予保留。Final Root Cause
只能由满足确认规则的 Hypothesis 派生，Graph 不自动把候选 Hypothesis 升级为根因，也不强制将多个已确认根因压缩为一个。

**实施任务：** Node/Edge Schema、Correlation Service、Graph
Query、关联规则、来源独立性、派生关系、冲突分析和可视化。

首期用 PostgreSQL
关系表实现，不预先引入专用图数据库；只有查询复杂度和性能测量证明有必要时再评估。

**明确不做：**
不把相关关系称为因果；不以时间接近替代可信关联；不覆盖原始 Evidence。

**契约依赖：** C05、C06、C07、C08。

**交付物：** Graph Schema、Correlation Service、Query
Service、关联规则、Graph Visualization。

**验收标准：** Request 级证据可关联；Run/Step
可关联；Event 的 `context_refs` 可支撑 Graph 关联且未知关联不生成确定性边；原始和派生证据可区分；同源派生不被当作独立佐证；支持/反驳可展示；缺少标识时保留未知；每条边可追溯依据。

**退出条件：** 证据关系可查询、可解释、可追溯，并为 Diagnosis
提供结构化上下文。

------------------------------------------------------------------------

## 阶段 9：Active Diagnosis Planner

**目标：** 在已有 Evidence Graph 和 Hypothesis 基础上，主动选择授权
Probe，补充高价值证据并更新诊断。

**前置条件：** Diagnosis v1、Evidence Graph、Tool Gateway
和受控只读查询可用。

**Probe Registry 字段：**
probe_id、input_schema、target_scope、permission_required、read_only、side_effects、estimated_cost、estimated_duration、risk_level、timeout、retry_policy、output_schema、cleanup_policy。

**首期只读 Probe：** DOM Snapshot、Screenshot、Network Read、Console
Read、API Read、Backend Log Read、受控 Database
Read。所有查询必须限定资源和字段范围。

**Probe 数据访问安全边界：** 只读不等于无风险，Probe
必须满足最小权限和最小数据访问原则。沿用现有 Tool Gateway 与 Probe
Registry，不新增安全子系统。

控制要求：

-   查询模板白名单；数据库表和字段白名单；
-   Workspace / Project 范围约束；
-   敏感字段脱敏；
-   返回行数限制与返回数据量限制；
-   查询超时与资源消耗限制；
-   每次 Probe 调用审计；
-   Probe 输出来源记录（probe_id、查询模板版本、参数、时间与调用者）。

Backend Log Read：必须限制查询范围、时间窗口、字段和敏感数据。Database
Read：首期禁止 Agent 直接生成并执行任意 SQL，只能使用预先定义、经过授权的只读查询模板。

Probe 执行前必须依次完成五项校验：权限校验、资源范围校验、风险检查、预算检查、参数 Schema
校验。任何校验失败都必须拒绝执行并留下审计记录。

**流程：**

``` mermaid
flowchart TD
    A["Evidence Graph"] --> B["Hypothesis Manager"]
    B --> C["Evidence Gap"]
    C --> D["Candidate Probe"]
    D --> E["Authorization Check"]
    E --> F["Risk / Budget Check"]
    F --> G["Probe Scoring"]
    G --> H["Probe Engine"]
    H --> I["New Evidence"]
    I --> J["Hypothesis Update"]
    J --> K{"Stop Condition"}
    K -->|"继续"| C
    K -->|"停止"| L["Updated Diagnosis"]
```

**探针价值函数：**

\[
Score(p)=w_I\widehat{EIG}(p)-w_C\widehat{C}(p)-w_R\widehat{R}(p)
\]

-   `EIG`：预期信息增益估计；
-   `C`：执行成本估计；
-   `R`：风险估计；
-   `w_I`、`w_C`、`w_R`：对应权重参数。

首期使用可解释启发式评分。归一化方式、权重和具体参数仍为待确认事项（见
7.2），需通过后续实验确定；此处 EIG 是估计量，不得描述为已经校准的真实概率。

**停止条件：**
达到确认规则；无高价值安全探针；达到数量/时间/成本预算；关键冲突无法解决；边际信息价值不足；权限条件不再满足。

**明确不做：** 不允许任意 SQL、任意内网访问、无预算探测或无限循环；不越过
Workspace / Project 边界读取；Probe 结果不能直接等同根因。

**契约依赖：** C07、C08、C09、C06。

**交付物：** Probe Registry、Probe Engine、Permission
Gateway、Planner、EIG 启发式评分、Hypothesis Update、预算和报告。

**验收标准：**
识别证据缺口；选择授权探针；越权与越界读取被拒绝且留有审计；任意 SQL 被拒绝；脱敏、行数/数据量、超时和资源限制有效；新证据可入图且来源可记录；假设可更新；预算后停止；不确定时输出
INCONCLUSIVE；可比较主动诊断前后的准确性、时间和 Probe Count。

**退出条件：** 主动诊断在权限和预算内补证，并有可量化增益评测。

------------------------------------------------------------------------

## 阶段 10：Oracle-Protected Self-Healing

**目标：** 让 AI 提出测试资产修复候选，通过断言保护、Oracle
完整性、沙箱验证、审批和回归后安全晋升。

**首期允许：** Locator
更新、等待条件调整、测试步骤修正、测试数据引用修正、非业务语义的自动化配置调整。

**禁止：** 修改产品业务代码、修改正式 Oracle、删除必需
Assertion、无依据降低断言严格程度、覆盖正式
TestCaseVersion、自动发布业务代码。

**RepairProposal 状态机：**

``` mermaid
stateDiagram-v2
    [*] --> DRAFT
    DRAFT --> VALIDATING
    VALIDATING --> VALIDATION_FAILED
    VALIDATING --> VALIDATED
    VALIDATION_FAILED --> DRAFT: 新修订后重新提交
    VALIDATED --> PENDING_APPROVAL
    PENDING_APPROVAL --> REJECTED
    PENDING_APPROVAL --> APPROVED
    APPROVED --> PROMOTING
    PROMOTING --> PROMOTED
    PROMOTING --> PROMOTION_FAILED
    PROMOTION_FAILED --> APPROVED: 可恢复技术故障且重试条件满足
    PROMOTED --> ROLLED_BACK
    REJECTED --> [*]
    ROLLED_BACK --> [*]
```

状态规则：

1.  验证失败后修改内容应形成新的提案修订记录。
2.  REJECTED 对当前修订为终态；重新提出应创建新修订或新提案。
3.  APPROVED 不代表已经晋升。
4.  PROMOTING 必须有幂等保护。
5.  PROMOTION_FAILED 不得伪装为 PROMOTED。
6.  PROMOTED 后回滚必须记录目标版本、原因和操作者。
7.  正式 Oracle 不属于 RepairProposal 可修改范围。
8.  晋升前必须执行版本并发校验，校验不通过按冲突处理，不得晋升。

**晋升版本并发保护：**

RepairProposal 记录以下版本字段：

-   `base_testcase_version`：修复所基于的正式版本；
-   `candidate_testcase_version`：候选修复版本；
-   `promotion_target_version`：预期晋升目标；
-   `promotion_record`：实际晋升记录（含目标版本、时间、操作者、校验结果）。

正式晋升前必须校验当前正式 TestCaseVersion 是否仍等于提案的 `base_testcase_version`。若不一致：

1.  阻止直接晋升；
2.  将提案标记为需要重新验证或冲突处理（沿用既有状态表达，不新增 Repair 状态）；
3.  要求重新生成修复提案或形成新的提案修订；
4.  不得覆盖其他成员已经发布的版本。

**原子晋升与 Compare-And-Swap 规则：**

正式晋升必须将以下操作作为一个原子操作：

1.  读取当前正式 TestCaseVersion；
2.  校验当前正式版本等于提案的 `base_testcase_version`；
3.  校验候选版本及提案修订身份有效；
4.  追加创建新的不可变 TestCaseVersion；
5.  更新 TestCase 的正式版本指针；
6.  写入 `promotion_record`。

以上操作必须通过数据库事务、行级锁、条件更新（Compare-And-Swap）或等效机制保证一致性，不得把版本检查与正式版本指针更新拆成两个独立步骤。

并发冲突规则：

-   同一个 TestCase 的并发晋升必须串行化，或由版本条件更新保证只有一个提案成功；
-   只有一个提案可以基于同一个正式版本成功晋升；
-   失败提案不得覆盖已经晋升的新版本；
-   版本校验失败时不得创建并发布一个错误的正式版本；
-   不得出现正式版本指针已更新但 `promotion_record` 未能正确记录的部分成功状态；
-   晋升操作必须具备幂等保护，避免重复请求创建多个正式版本。

版本冲突时：阻止晋升；记录 `VERSION_CONFLICT` 等冲突原因（优先使用现有失败原因、记录字段或派生记录表达，不新增状态枚举）；保留原正式版本；要求重新验证或形成新的提案修订；不允许简单重试旧提案并覆盖新版本。

### 版本冲突与晋升重试边界

**A. 普通技术故障**

当 PROMOTION_FAILED 的原因属于可恢复技术故障时：

1.  可以按照既有状态机进行重试；
2.  必须满足晋升操作的幂等要求；
3.  必须重新确认正式版本和提案身份仍然有效；
4.  不得因为重试而重复创建正式版本；
5.  不得产生部分晋升状态。

**B. VERSION_CONFLICT**

当 PROMOTION_FAILED 的原因属于 VERSION_CONFLICT 时：

1.  禁止直接对原提案进行无条件重试；
2.  禁止简单将原提案重新设置为 APPROVED 后再次晋升；
3.  必须重新读取当前正式 TestCaseVersion；
4.  必须重新检查候选修复与当前正式版本之间的差异；
5.  必须重新执行 Assertion Preservation 和 Oracle Integrity 检查；
6.  根据变化影响重新执行必要的沙箱验证和回归测试；
7.  必须形成新的提案修订或新的 RepairProposal；
8.  新提案必须基于当前正式 TestCaseVersion 更新 `base_testcase_version`；
9.  必须重新满足验证和审批条件后才能晋升；
10. 原冲突提案及其失败记录必须保留，不得通过覆盖历史记录消除冲突。

**C. 统一约束**

VERSION_CONFLICT 是版本并发冲突，不是普通瞬时技术故障；不允许通过重置状态、跳过审批或绕过版本校验恢复晋升。不新增 RepairProposal 状态枚举，不改变 TestCaseVersion 只追加、不原地修改的规则，不改变正式 Oracle 保护规则；所有新晋升仍然必须遵守原子晋升与 CAS 规则。

状态机约束：沿用现有 RepairProposal 状态机，不为版本并发保护新增状态。继续保留 APPROVED 不等于 PROMOTED、PROMOTING 必须有幂等保护、PROMOTION_FAILED 不得伪装为 PROMOTED、版本冲突需重新验证或进入现有冲突处理路径、TestCaseVersion 只追加不原地修改、回滚保留完整记录。

版本规则：TestCaseVersion 只追加、不原地修改；回滚通过恢复或重新指定历史有效版本实现，回滚记录完整保留；正式 Oracle 不允许通过 RepairProposal 修改。

**Assertion Preservation 不只比较文本，还要检查：**

-   assertion_id；
-   Assertion 语义；
-   Primary Oracle 绑定；
-   Supporting Oracle；
-   required 属性；
-   evaluation_rule；
-   覆盖范围；
-   预期来源。

**明确不做：** 不自动修改
Oracle；不删除必需断言；不直接覆盖正式版本；不用旧提案覆盖新版本；不允许 Agent
自批自修；未经回归不晋升。

**契约依赖：** C02、C03、C04、C08、C10、C11。

**交付物：** Repair Advisor、Proposal Service、Diff Generator、Assertion
Preservation、Oracle Integrity Checker、Sandbox Validator、Approval
Manager、Regression Manager、Version Promotion/Rollback。

**验收标准：** Locator
修复有效；无须修复时不产生无意义变更；删除断言/修改 Oracle
被拒绝；沙箱或回归失败不晋升；拒绝保留原版本；晋升失败可恢复；晋升后可回滚；版本并发冲突被阻断且不覆盖他人版本，回滚记录完整。

原子晋升验收场景：

1.  单提案正常晋升成功；
2.  两个提案基于相同正式版本同时晋升，只允许一个成功；
3.  失败提案不会覆盖成功提案的新版本；
4.  事务中断不会产生部分晋升；
5.  重复晋升请求不会重复创建正式版本；
6.  冲突提案能够被识别并进入重新验证或冲突处理流程：`VERSION_CONFLICT` 不能直接重试原提案，必须基于当前正式版本形成新的提案修订或新提案并重新完成验证与审批；普通可恢复技术故障可以使用原有幂等重试机制；
7.  正式版本指针、TestCaseVersion 和 `promotion_record` 保持一致。

**退出条件：** 修复只能产生候选新版本，并经验证、审批和回归；Oracle 与
Assertion 语义受到保护。

------------------------------------------------------------------------

## 阶段 11：UI-FaultBench 完整 Benchmark

**目标：** 形成覆盖测试生成、故障诊断和安全自愈的可重复评测体系。

**三类评测：**

  --------------------------------------------------------------------------------------------------------
  方向                    对照方法                          核心指标
  ----------------------- --------------------------------- ----------------------------------------------
  Test Generation         人工编写、单次 LLM、Explorer +    Requirement Interpretation、Scenario
                          Compiler                          Coverage、Assertion Correctness、Locator
                                                            Validity、Replay Stability、Review Effort

  Diagnosis               A 原始报告、B 单次 LLM、C 规则、D Macro-F1、Primary Cause Accuracy、False Pass
                          多源 Evidence + Graph、E D +      Rate、Selective Accuracy、Coverage、Probe
                          Active Diagnosis                  Count、Time to Diagnosis

  Self-Healing            人工修复、隔离环境无保护          Repair Validation Success、Assertion
                          LLM、规则保护、Oracle-Protected   Preservation、Acceptance、Regression、Repair
                                                            Time、Rollback
  --------------------------------------------------------------------------------------------------------

无保护修复只可作为隔离实验对照，不得晋升正式资产。

**数据集隔离：**
按故障机制、场景变体、应用版本、数据种子和实验批次隔离；Trusted Test Set
与 Agent Generated Test Set 分开；Ground Truth 不向 Agent
开放；数据集版本不可静默变更。

**实验元数据：**
model_version、prompt_version、tool_version、agent_version、compiler_version、TeamFlow_version、testcase_version、oracle_version、browser_version、test_data_seed、dataset_version、experiment_config、runtime_environment。

**指标要求：**
所有指标定义统计单位、分子、分母、排除条件、缺失值处理、多根因处理和波动/置信区间。INCONCLUSIVE
不得计为 PASSED；Selective Accuracy 同时报告 Coverage。

**消融实验：** 移除 Evidence Graph、Active Diagnosis、Oracle
Protection、Probe Budget、Assertion Preservation。

**契约依赖：** C01～C11 相关对象、C12 Benchmark、Ground Truth
管理和指标计算服务。

**交付物：** Dataset Manager、Ground Truth Manager、Experiment
Manager、Baseline Runner、Evaluation Engine、Ablation
Runner、指标定义、实验报告和 Benchmark 工作台。

**验收标准：** 三类 Benchmark 可执行；诊断 A～E
可复现；元数据完整；数据集隔离有效；指标口径明确；结果可追溯到 Run 和
Evidence。

**退出条件：** 核心能力有可重复、可追溯、可解释的量化结果。

------------------------------------------------------------------------

## 阶段 12：外部 Web 应用泛化、安全与可靠性强化

**目标：** 验证 TeamFlow
之外的适应能力，并完成系统性安全、性能和可靠性验证。

**外部应用规模：** 首轮只选 1～2 个明确授权、可稳定访问的 Web
应用。优先覆盖不同交互特点，不要求一次支持五类应用。

**泛化评价：** 页面理解、Locator 适应、操作执行、TestCase
编译、重复回放、页面变化适应和证据采集完整度。

没有独立 Ground Truth
的外部应用，只评价操作和生成质量，不宣称根因诊断准确率。

**安全强化：** SSRF、DNS Rebinding、Redirect Security、Prompt
Injection、跨 Workspace、文件访问、凭证保护、浏览器和 Worker
隔离、出站限制、资源配额、取消恢复、证据脱敏、数据保留和审计。

**性能可靠性：** 多 Task 并发、多浏览器、Event 写入、Evidence
附件、Graph 查询、多 Agent 并发、长时间运行、Worker
重启、对象存储异常、Redis/数据库短暂不可用。

SLO、并发和资源上限根据实测确定，不预先编造数字。

**明确不做：** 不要求首轮支持大量外部应用；不对无 Ground Truth
的应用宣称诊断准确率；不开放任意域名；不以文档审查代替安全测试。

**契约依赖：** Tool Gateway、Browser Adapter、C04～C12、身份权限和审计。

**交付物：**
外部应用验证报告、安全报告、性能报告、稳定性报告、威胁模型、安全配置、恢复手册和已知限制。

**验收标准：** 1～2
个授权应用完成验证；关键安全测试通过；越权访问被阻断；凭证不泄漏；Worker
恢复符合设计；性能和资源限制有实测结果。

**退出条件：** 外部泛化、安全、性能和可靠性均有可核查报告。

------------------------------------------------------------------------

## 阶段 13：全链路集成、最终验收与交付

**目标：**
在各里程碑持续集成的基础上，完成最终端到端回归、部署验证、演示和资料归档。

**注意：阶段 13 不是第一次全链路集成。** 阶段 0 起建立
CI，每个里程碑都做集成验收；本阶段负责完整产品最终验证和发布。

**最终演示流程：**

``` mermaid
flowchart TD
    A["登录 TraceGuard"] --> B["选择 TeamFlow"]
    B --> C["输入自然语言目标"]
    C --> D["Explorer 探索"]
    D --> E["查看 Exploration Trace"]
    E --> F["Compiler 生成 Candidate"]
    F --> G["审核 TestCase / Oracle"]
    G --> H["创建 Task / Run"]
    H --> I["Playwright 执行"]
    I --> J["Oracle 判定"]
    J --> K["查看 Event / Evidence"]
    K --> L["查看 Evidence Graph"]
    L --> M["Diagnosis / Probe"]
    M --> N["Disposition"]
    N --> O["Repair Proposal"]
    O --> P["断言保护 / 沙箱 / 审批 / 回归"]
    P --> Q["晋升或回滚"]
    Q --> R["最终报告"]
```

**最终验收场景：**

  编号   场景                  重点
  ------ --------------------- ----------------------------------
  01     正常创建项目          UI 自动化和 Oracle 通过
  02     UI 成功但持久化失败   Primary Oracle 识别失败
  03     API 500               API 证据和诊断
  04     网络超时              Network 证据关联
  05     Locator 变化          测试资产问题识别
  06     异步时序异常          重复执行和不稳定特征
  07     浏览器环境不可用      环境问题处置
  08     多源证据冲突          支持/反驳与不确定性
  09     Probe 越权请求        权限网关阻断
  10     测试脚本修复          断言保护、审批、回归、晋升和回滚

**交付物：**

1.  TraceGuard 完整源代码；
2.  Docker Compose 部署环境；
3.  TeamFlow 最小被测应用；
4.  Playwright/pytest 测试内核；
5.  TestCase 与 Oracle 管理；
6.  Task/Run/Step 执行平台；
7.  Explorer Agent；
8.  TestCase Compiler；
9.  Event/Evidence 基础设施；
10. Evidence Graph；
11. Diagnosis；
12. Active Diagnosis Planner；
13. Repair/Self-Healing；
14. UI-FaultBench；
15. Vue 工作台；
16. C01～C12 Schema 与样例；
17. 自动化测试集；
18. 部署、使用和运维文档；
19. Benchmark 实验报告；
20. 安全、性能和可靠性报告；
21. 演示脚本；
22. 已知限制和后续演进清单。

**验收标准：**
所有核心模块可部署；业务闭环可重复演示；版本可追溯；Task/Run/Step
正确；Event/Evidence 完整；Diagnosis 有依据；Probe 受控；Repair 不修改
Oracle 或削弱断言；Benchmark 可重复；外部、安全、性能报告完整；CI
和回归有效。

**退出条件：**
十个最终场景均有执行证据；未通过项有缺陷记录和处置结论；发布版本、依赖、Schema、实验数据和文档版本一致。

------------------------------------------------------------------------

# 4. C01～C12 契约落地映射

契约渐进冻结，不要求阶段 0
一次性冻结全部字段。每阶段冻结当前实现需要的稳定语义，后续通过向后兼容版本演进。

  ------------------------------------------------------------------------
  契约              名称              首次落地 /        后续使用
                                      主要冻结阶段      
  ----------------- ----------------- ----------------- ------------------
  C01               TestIntent        阶段 2            阶段 5
                                      建立基础结构      扩展探索输入

  C02               TestCase          阶段 2            阶段 6
                                      建立执行结构      扩展编译来源

  C03               Oracle            阶段 2 建立       阶段 6
                                      Assertion-Level   绑定候选断言
                                      Oracle            

  C04               Execution         阶段 2 定义本地   阶段 3 扩展
                                      Run/Step          Task/Worker

  C05               Event             阶段 4            阶段 5、7、9

  C06               Evidence          阶段 4            阶段 5～11

  C07               Evidence Graph    阶段 8            阶段 9、11

  C08               Diagnosis         阶段 7            阶段 8、9、11

  C09               Probe             阶段 9            阶段 11、12

  C10               Disposition       阶段 7            阶段 10、11

  C11               Repair            阶段 10           阶段 11、13

  C12               Benchmark         阶段 1            阶段 11
                                      建立场景/Ground   完整冻结评测契约
                                      Truth 基础记录    
  ------------------------------------------------------------------------

每份契约投入开发前，至少完成对象定义、字段类型和必填约束、枚举和联合约束、JSON
Schema、合法/非法样例、状态转换、生产者/消费者、版本兼容、错误处理和自动化契约测试。

**本轮冻结的关键契约语义（细则见对应阶段）：**

-   C03 Oracle / C04 Execution：Assertion 级状态为 PASSED / FAILED / INCONCLUSIVE / NOT_EVALUATED；正式 TestCase 至少一条有效的必需 Assertion；Test Verdict 按阶段 2 聚合规则产生，`execution_status` 与 `test_verdict` 独立；Oracle 多来源优先级、可信等级与复杂冲突裁决细则属 7.2 的 P03，未确认前按阶段 2 的 INCONCLUSIVE 兜底规则处理；Run 租约、心跳续租与执行代次校验见阶段 3。
-   C05 Event：`run_id` 非必填，改用 `context_refs` 关联多种上下文；首期规范 Workspace 作用域内业务事件，`workspace_id` 必填且只能由服务端可信上下文注入，平台级系统事件沿用独立遥测与访问控制，不虚构 Workspace 归属；序号唯一范围为 `(producer_instance_id, sequence)`，不承诺连续，合法跳号不得判为丢失；幂等键在 `(producer_id, idempotency_key)` 维度唯一且重试不变，消费端另以 `(consumer_id, event_id)` 通过 Consumer Inbox 去重，两者不得混用；缺口先标记待确认，不直接判永久丢失；详见阶段 4。
-   C07 Evidence Graph：关联边只能由已校验的 `context_refs` 与明确规则建立，未知关联保留为 unknown；`direct_causal` 需可信直接因果依据；详见阶段 8。
-   C08 Diagnosis / C10 Disposition：Failure Domain、Failure Mechanism、Disposition 以阶段 7 正式候选枚举为准，不使用未定义简写；Diagnosis 可含多个 Hypothesis，Final Root Cause 允许为空；详见阶段 7、阶段 8。
-   C09 Probe：首期仅允许经过授权的只读查询模板，禁止 Agent 生成并执行任意 SQL；执行前完成五项校验并留审计；详见阶段 9。
-   C11 Repair：晋升前校验 `base_testcase_version` 与当前正式版本一致；版本校验、新版本追加、正式指针更新与 `promotion_record` 写入须通过 CAS 或等效机制原子执行；冲突记录 `VERSION_CONFLICT` 并走重新验证或冲突处理，不覆盖已发布版本；TestCaseVersion 只追加，不新增 Repair 状态；详见阶段 10。VERSION_CONFLICT 不允许直接重试原提案修订；必须基于当前正式版本重新检查、重新验证，形成新的提案修订或新提案，并重新满足审批条件后才能晋升。普通可恢复技术故障才可以按既有幂等重试规则处理。

------------------------------------------------------------------------

# 5. 横向工程要求

## 5.1 测试体系

  类型                   验证内容
  ---------------------- ----------------------------
  Unit Test              模块内部逻辑
  Contract Test          C01～C12
  Integration Test       模块间交互
  E2E Test               真实业务闭环
  Fault Injection Test   故障识别和处置
  Agent Evaluation       探索、生成、诊断、修复质量
  Security Test          权限、隔离、凭证和工具安全
  Performance Test       并发、延迟、吞吐和资源
  Recovery Test          Worker、存储和队列异常恢复

## 5.2 持续集成与阶段集成

CI 分为三个执行等级，按触发时机与门禁强度分层：

``` mermaid
flowchart TD
    A["代码提交 / PR"] --> PR["PR Gate"]

    PR -->|"失败"| X["阻断合并，修复后重跑 PR"]
    PR -->|"通过"| M["合并主干"]

    M --> N["Nightly Gate：每日 / 主干集成"]

    N -->|"通过"| R["Release Gate：里程碑 / 正式发布"]
    N -->|"失败"| C{"失败级别判断"}

    C -->|"关键失败"| B["阻断对应 Release；修复后重新运行 Nightly"]
    B --> N

    C -->|"非关键失败"| W["正式风险豁免评估"]

    W -->|"未批准 / 已过期"| B
    W -->|"批准且仍有效"| R

    R -->|"关键项失败"| Z["阻断发布，修复后重新验收"]
    R -->|"通过"| D["发布验收"]
```

**PR Gate（每次提交或合并请求）：** Lint、Format Check、Type Check、Unit
Test、Contract Test、必要的快速 Integration Test、基础安全检查。

**Nightly Gate（每日定时或主干集成）：** 完整 Integration Test、完整
E2E、故障注入、扩展 Security Test、Recovery Test、必要的 Agent
Evaluation。

**Release Gate（里程碑验收和正式发布）：** 完整回归、Benchmark、性能测试、安全测试、数据集和实验报告复核、部署验证、版本和契约一致性检查。

门禁规则：

1.  不是每次 PR 都执行完整 Benchmark；
2.  PR Gate 必须保持快速可用，不得因执行时间过长失去可用性；
3.  Nightly 失败必须能够追溯具体用例和执行环境；
4.  Release Gate 的关键失败项不得通过简单忽略方式放行。
5.  PR Gate 任一必需检查失败时必须阻断对应 PR 合并；修复后必须重新执行 PR Gate，不得以 Nightly 或 Release Gate 通过替代。

**Nightly 失败与 Release 准入规则：**

A. Nightly 失败处理

1.  Nightly 失败必须生成可追溯记录，记录失败用例、代码版本、执行环境、日志和影响范围。
2.  必须对失败进行阻断级别判断；关键失败必须阻断对应 Release。
3.  不得仅通过标记 Flaky、忽略用例或重跑成功来直接消除原始失败记录。

B. Release Gate 准入

1.  Release Gate 必须检查当前 Release Candidate 对应的相关测试结果；未解决的关键失败不得直接放行。
2.  PR Gate 通过不能替代 Nightly Gate 和 Release Gate。
3.  旧版本或其他 Commit 的 Nightly 通过结果不能直接作为当前 Release Candidate 的完整通过证据；若 Release Candidate 与已验证 Commit 不一致，应对相关测试重新验证。

C. 风险豁免规则

允许对非关键问题进行正式风险豁免，但必须记录：豁免原因、影响范围、相关失败记录、风险说明、责任人、审批人或授权评审角色、有效期限、后续修复要求。责任角色尚未明确时使用"待分配"或"待确认"，不得填写人员姓名。豁免必须具备有效期限，不得成为永久忽略失败的手段。未获批准或已超过有效期限的豁免不得作为放行依据；关键安全、数据完整性、核心契约或业务正确性问题不得通过普通豁免绕过。

D. 门禁最终责任

Release Gate 是最终发布放行门禁；PR Gate 负责快速反馈，Nightly Gate 负责持续集成验证，三者不能相互替代。

门禁验收场景：

1.  Nightly 发现关键失败，Release 被阻断；
2.  Nightly 发现非关键问题，具备完整风险豁免记录后才可放行；
3.  使用旧 Commit 的成功结果不能冒充当前 Release Candidate 的测试结果；
4.  PR Gate 通过但 Nightly 存在未解决关键失败时，不允许发布；
5.  已豁免问题具有有效期限和追踪记录；
6.  Release Gate 关键失败不能通过简单忽略方式放行。

阶段能力累积安排继续保留：阶段 0 建立 CI；阶段 1 增加 TeamFlow
集成；阶段 2 增加 UI E2E；阶段 4 增加 Event/Evidence
可靠性；阶段 5 增加 Agent Evaluation；阶段 7 增加 Diagnosis
Benchmark；阶段 10 增加 Repair 安全回归；阶段 11 将 Benchmark
纳入正式质量门禁；阶段 13 做全量回归。

## 5.3 技术栈与启用阶段

  技术                 职责                       启用阶段
  -------------------- -------------------------- --------------
  Python               后端、测试框架、Agent      0
  FastAPI              后端 API                   0
  Vue 3 + TypeScript   工作台                     0
  PostgreSQL           权威业务数据               0
  Docker Compose       开发和集成环境             0
  Playwright           UI 自动化执行              2
  pytest               测试框架                   2
  Redis                Broker/缓存                3
  Celery               异步调度                   3
  SSE                  实时状态推送               3
  MinIO                Evidence 附件              4
  OpenTelemetry        技术日志、指标和 Trace     3～4
  Browser Use          自主浏览探索               5
  LangGraph            Agent 工作流               5 起按需使用
  LLM                  候选生成、诊断和修复建议   5 起逐步引入

Browser Use、Playwright 和 LangGraph 的组合必须通过 PoC
验证，不以单独可用推断组合兼容。

------------------------------------------------------------------------

# 6. 阶段准入与退出管理模板

每个阶段的详细任务必须按以下模板编制：

  项目       要求
  ---------- ----------------------------------------------
  阶段目标   本阶段解决什么问题
  前置条件   上阶段必须通过什么
  输入       依赖模块、接口、数据和文档
  实施任务   具体开发内容
  非目标     明确本阶段不做什么
  接口依赖   需要其他模块提供什么
  交付物     代码、Schema、测试、文档、报告
  验收标准   自动化或人工如何验证
  退出条件   什么证据证明可以进入下一阶段
  风险       可能问题和应对
  状态       未开始、进行中、阻塞、待评审、已验收、未通过

阶段状态统一：

-   NOT_STARTED；
-   IN_PROGRESS；
-   BLOCKED；
-   READY_FOR_REVIEW；
-   ACCEPTED；
-   REJECTED。

只有 ACCEPTED 才能作为阶段完成依据。局部任务完成不等于阶段整体完成。

------------------------------------------------------------------------

# 7. 风险与待确认事项

## 7.1 主要风险

  风险                    影响                 控制
  ----------------------- -------------------- ------------------------------
  TeamFlow 范围膨胀       拖慢主线             首期限定项目创建路径
  Playwright 内核不稳定   上层返工             阶段 2 独立验收
  Task/Run 混淆           历史不可追溯         明确对象和状态所有权
  Event 丢失或重复        证据不完整           至少一次投递、幂等和缺口检测
  Agent 越权              数据和环境风险       Tool Gateway、最小权限和隔离
  Oracle 不可信           False Pass           独立预期来源和审批
  诊断分类混乱            根因和特征混淆       多维 Diagnosis 模型
  证据关联过度            错误因果判断         关系类型和来源约束
  Probe 无预算            成本和风险失控       权限、风险、预算和停止条件
  自愈削弱断言            测试失去价值         Assertion Preservation
  Benchmark 泄漏          结果虚高             数据集和 Ground Truth 隔离
  外部应用缺少预期        诊断准确率不可验证   限制评价结论范围

## 7.2 待确认事项

  ID    待确认事项                        最晚确认阶段   责任角色   当前状态   是否阻塞阶段
  ----- -------------------------------- ------------- ---------- ---------- ------------------------
  P01   技术版本和兼容矩阵                 阶段 0         待分配     待确认     是（阶段 0 交付前）
  P02   Browser Use / Playwright 集成方式  阶段 5         待分配     待确认     否（阶段 5 内 PoC 确认）
  P03   Oracle 多来源优先级、可信等级与冲突裁决细则  阶段 2         待分配     待确认     是（阶段 2 完成 Oracle 多来源冲突场景验收前）
  P04   Diagnosis 确认规则                 阶段 7         待分配     待确认     否（阶段 7 内确认）
  P05   EIG 计算和权重                     阶段 9         待分配     待确认     否（首期启发式可推进）
  P06   Probe 预算和风险等级               阶段 9         待分配     待确认     是（阶段 9 Probe 启用前）
  P07   Event / Evidence 生命周期         阶段 4         待分配     待确认     否（阶段 4 内确认）
  P08   Repair 审批角色                    阶段 10        待分配     待确认     是（阶段 10 首次晋升审批前）
  P09   Benchmark 数据集规模和统计方案     阶段 11        待分配     待确认     否（阶段 11 内确认）
  P10   外部 Web 应用选择                  阶段 12        待分配     待确认     是（阶段 12 启动前）
  P11   并发、SLO 和资源预算               阶段 12        待分配     待确认     否（阶段 12 实测确定）
  P12   是否需要专用图数据库               阶段 8 实测后  待分配     待确认     否（阶段 8 实测后评估）

责任角色尚未确定的统一填写"待分配"，不预设人员姓名。当前状态继续保持待确认，除非原文已有明确决定。是否阻塞按实际依赖判断："是"表示该阶段关键交付启动或规则冻结前必须有结论；其余项可在阶段内以保守默认值或实测推进，但确认前不得冒充已冻结参数。

**P03 冻结边界说明：** 基础 Test Verdict 聚合规则已在阶段 2 冻结，包括 Oracle 不可用不得判 PASSED、多 Oracle 冲突且无法依据正式规则裁决时判 INCONCLUSIVE、Optional Assertion 不决定最终 Verdict、已知必需 Assertion 失败不被后续执行中断掩盖、未完成执行不自动判 PASSED，以及 `execution_status` 与 `test_verdict` 独立。P03 不再阻塞上述基础规则的冻结，仅负责 Oracle 多来源细则：Primary 与 Supporting Oracle 的优先关系、不同来源 Oracle 的可信等级、同一来源多个 Oracle 结果冲突时的处理、哪些冲突可由预定义规则裁决、哪些必须交人工审核，以及结果缺失、过期或来源不可验证时的处理细则。不得擅自确定 Primary Oracle 一定覆盖 Supporting Oracle，也不得自行设定未经讨论的可信等级。相关细则必须在阶段 2 完成 Oracle 多来源冲突场景验收前确认。

------------------------------------------------------------------------

# 8. 最终验收清单

最终验收以证据为准，不以页面数量、代码行数或 Agent 数量为准。

### A. 工程基础

-   [ ] 前后端和基础设施可按文档部署；
-   [ ] 依赖版本和兼容矩阵完整；
-   [ ] CI、日志、配置和基础安全有效；
-   [ ] 数据库迁移和环境初始化可重复。

### B. TeamFlow 与测试内核

-   [ ] TeamFlow 核心业务路径可重复运行；
-   [ ] 故障注入和 Ground Truth 隔离有效；
-   [ ] TestCase / TestCaseVersion 可管理；
-   [ ] Oracle 可独立验证；
-   [ ] Playwright / pytest 可独立执行；
-   [ ] Verdict 与 Execution Status 分离；
-   [ ] Test Verdict 聚合规则及合法/非法样例通过验证；
-   [ ] NOT_EVALUATED 与 INCONCLUSIVE 可区分。

> 注：Oracle 多来源优先级、可信等级与复杂冲突裁决细则属 7.2 的 P03，须在阶段 2 完成冲突场景验收前确认。"Test Verdict 聚合规则通过验证"仅指已冻结的基础规则，不代表 P03 已完成。

### C. 执行与证据

-   [ ] Task / Run / Step 边界明确；
-   [ ] 重试产生独立 Run，Task 层汇总不冒充单次 Run Verdict；
-   [ ] Worker 取消、超时和恢复有效；
-   [ ] Worker Lease / 执行代次校验生效，旧 Worker 提交被拒绝；
-   [ ] Event 幂等和顺序语义通过测试；
-   [ ] Consumer Inbox 覆盖重复投递、崩溃恢复与外部副作用不确定场景；
-   [ ] Event 可按 `context_refs` 关联多种上下文且 `workspace_id` 来源可信；
-   [ ] Workspace 业务事件具有可信 `workspace_id`，平台级系统事件不被强制归属虚假 Workspace；
-   [ ] 序号缺口与幂等键冲突按规则处理，不被误判为永久丢失；
-   [ ] 合法跳号与真实缺失可区分，不跨 `producer_instance` 比较序号；
-   [ ] Evidence 来源和完整性可追溯。

### D. Agentic Testing

-   [ ] Explorer Agent 能在授权范围内探索；
-   [ ] Exploration Trace 完整；
-   [ ] Compiler 生成 Candidate TestCase；
-   [ ] Candidate 可独立回放；
-   [ ] Candidate 稳定性验证与晋升规则可追溯，不稳定候选不晋升；
-   [ ] Oracle 审批不可绕过；
-   [ ] 正式 TestCaseVersion 有审核记录。

### E. 诊断与自愈

-   [ ] Diagnosis 多维结果完整且枚举取值一致；
-   [ ] 多 Hypothesis 保留支持/反驳证据，未确认假设不成为根因；
-   [ ] CONFIRMED 有明确证据规则，证据不足时输出 INCONCLUSIVE；
-   [ ] Evidence Graph 不伪造因果；
-   [ ] Active Diagnosis 受权限和预算约束；
-   [ ] Probe 模板白名单、字段白名单、脱敏、限额和审计有效；
-   [ ] RepairProposal 状态转换完整；
-   [ ] Assertion 和 Oracle 得到保护；
-   [ ] 修复支持审批、晋升和回滚，版本并发冲突被阻断且不覆盖他人版本；
-   [ ] 原子晋升保证同一正式版本只有一个提案成功，版本指针、TestCaseVersion 与 `promotion_record` 一致；
-   [ ] VERSION_CONFLICT 不会被当作普通技术故障直接重试；冲突提案必须基于当前正式版本重新验证并重新满足审批条件。

### F. Benchmark 与交付

-   [ ] 测试生成 Benchmark 可重复；
-   [ ] 诊断 A～E 对照可重复；
-   [ ] 自愈 Benchmark 可重复；
-   [ ] 数据集隔离和 Ground Truth 保护有效；
-   [ ] 1～2 个授权外部应用完成验证；
-   [ ] 安全、性能和可靠性报告完整；
-   [ ] PR / Nightly / Release 三级 CI 门禁按定义执行，关键失败项不被忽略放行；
-   [ ] PR Gate 失败能够阻断合并，修复后重新执行 PR Gate；
-   [ ] Nightly 关键失败阻断 Release，非关键豁免具备完整记录与有效期限；
-   [ ] 旧 Commit 结果不冒充当前 Release Candidate 的验证结果；
-   [ ] 十个最终演示场景有执行证据；
-   [ ] 发布包、部署文档和已知限制完整。

------------------------------------------------------------------------

# 9. 后续推进顺序

1.  评审并确认本路线 v1.1；
2.  编制《阶段 0：工程初始化与开发准入规格》；
3.  完成技术版本 PoC、兼容矩阵和工程初始化；
4.  按 M0 完成 TeamFlow 最小被测应用；
5.  按 M1 完成专业 UI 自动化测试平台；
6.  按 M2 完成 Event/Evidence、Explorer 和 Compiler；
7.  依次完成诊断、Graph、主动探针和自愈；
8.  完成 Benchmark、外部泛化、安全强化和最终交付。

**最终原则：每个阶段都交付可运行、可验证、可追溯的工程成果。TraceGuard
的价值不在于堆叠 Agent 或技术名词，而在于把 UI
自动化测试、可信预期、证据驱动诊断和安全自愈真正连接成一条可重复验证的产品闭环。**
