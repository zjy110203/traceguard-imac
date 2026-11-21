# TestZeus Hercules → TraceGuard 映射与借鉴建议

> 本文档是 Hercules 源码级研究的收口，把已核实机制与 TraceGuard v1.1 正式设计逐项对照。
> 依据：本目录源码研究 5 份文档 + TraceGuard v1.1 路线图/总体架构/核心技术设计。
> 纪律：开源功能来自实际源码；TraceGuard 一侧只引用 v1.1 正式术语；**AGPL-3.0 是硬约束**。

## 〇、首要风险提示：AGPL-3.0

Hercules 根目录 LICENSE 为 **GNU AFFERO GENERAL PUBLIC LICENSE Version 3**（`pyproject.toml` 未声明 SPDX 但 LICENSE 明确）。AGPL-3.0 第 13 条对网络交互用户提供源码披露义务。结论：
- TraceGuard **不得复制、移植、衍生 Hercules 源码**进入正式代码库，尤其不能把 runner/agent/assertion 相关实现直接搬用。
- 只能作为**架构设计学习**（借鉴方式 B/D），或在理解其机制后用 TraceGuard 自有设计**独立重新实现**（借鉴方式 C）。
- 是否可引入任何形式依赖须法律评估；当前台账标记"不采用/待法律评估"。

## 一、逐维度对照表

| 对比维度 | Hercules 实现（源码已核实） | TraceGuard 原设计（v1.1） | 差异 | 借鉴建议 |
|---|---|---|---|---|
| Agent 组织方式 | HighLevelPlanner（生成 plan / next_step / is_assert / is_passed）+ 多个 nav agent（browser/api/sql/mcp）+ AgentRegistry 注册（`agents/`、`agent_registry.py`） | 四类核心 Agent + Agent Registry/Workflow；Agent 输出是候选信息，不自动成为事实 | Hercules 由 Planner 同时承担计划与判定，角色耦合；无独立 Assertion Agent | 借鉴 Planner 生成结构化执行计划 + Agent 注册机制；不照搬"计划者即判定者" |
| Planner 计划生成 | Planner 输出 JSON：plan/next_step/terminate/final_response/is_assert/assert_summary/is_passed/target_helper（`high_level_planner_agent.py:72,186`） | 无"单一规划者决定 verdict"设计；Explorer/Compiler/Verifier 各司其职 | Hercules 把测试判定字段塞进规划输出 | 借鉴"结构化计划 + 下一步动作"；判定字段必须剥离，交 TraceGuard 确定性 Verifier |
| Executor 执行步骤 | executor_nav / browser_nav / api_nav / sql_nav / mcp_nav 等执行具体步骤 | Explorer Agent 探索 + Playwright Adapter 执行 + Tool Gateway | Hercules 工具型 Agent 丰富（API/SQL/MCP），但执行与判定同源 | 借鉴多工具 Agent 的组织形态；TraceGuard 只读 Probe 须经 Probe Registry + 权限/预算校验（阶段 9） |
| Assertion 判定 | **无独立 Assertion Agent**；`_assertion_node`（`simple_hercules.py:1386`）直接信任 Planner 自报 is_passed；确定性判定靠默认注册的 API 断言 `assert_json_path`、opt-in（默认关）的 UI 断言 `assert_ui` + 报告期 JUnit 断言账本覆盖（FAILED/ERROR 可推翻 Planner PASS；零断言 PASS 仅标记不判红） | Assertion 级四状态 + Test Verdict 聚合（已冻结）；Oracle 独立于 Agent；正式 TestCase 至少一条必需 Assertion | 根本差异：Hercules 默认判据是 LLM 自报，弱证据；TraceGuard 要求确定性、Oracle 驱动 | **不采用**其默认判定；TraceGuard 坚持阶段 2 冻结规则。可借鉴"显式 assert 工具调用"的**思路**（学习参考） |
| Agent 状态流转 | StateGraph 在 `simple_hercules.py:1447` 定义；`state_handler.py` 负责 prompt 状态处理（不是 StateGraph 所在）；节点含 plan/execute/assertion | LangGraph 管理有状态工作流；Checkpoint 仅恢复信息；业务状态在 PostgreSQL | Hercules 把 verdict 存进 AgentState | 借鉴状态机/节点编排形态；业务状态与判定结果按 TraceGuard 所有权边界落库 |
| 浏览器封装 | playwright_manager + get_interactive_elements + browser_assist_tools + accessibility tree | Browser Manager/Context/Page/Locator + Playwright Adapter | 都有 Playwright 封装层 | 借鉴元素获取/accessibility tree 辅助观测；注意 Hercules 锁定 playwright<=1.49（旧），TraceGuard 版本须按 P01 实测确定 |
| 工具失败处理 | 各 tool 返回错误，Planner 决定下一步；已接入 `progress_stall_count`/`_PLANNER_STALL_LIMIT`/nav 轮次上限，`detect_llm_loops.is_agent_stuck_in_loop` 有实现但本快照未发现调用点 | Tool Gateway 失败拒绝并审计；Probe 五项校验；执行中断按已知失败处理 | Hercules 失败处理依赖 LLM 判断，无强制审计边界 | 借鉴循环检测思路；TraceGuard 失败必须留审计记录、按确定性规则处理 |
| 结果结构化/报告 | post_process_responses + reporting（bundle.py 解析 JUnit XML 生成失败 bundle/截图）+ markdown_report + gherkin_generator | Test Verdict + Run/Step 报告 + Event/Evidence + 工作台 | Hercules 有较完整报告链，但源于自报 verdict | 借鉴"测试报告产物打包（JUnit XML + 截图 bundle + Markdown）"的结构化形态（C 类，独立实现） |
| Gherkin 支持 | gherkin_generator / gherkin_helper 从场景生成 Gherkin | 无 Gherkin 要求；用 TestIntent/TestCase/Oracle 契约 | 输入表示不同 | 借鉴"自然语言场景→结构化测试描述"思路；TraceGuard 用 C01/C02，不引入 Gherkin 依赖 |
| 证据可信度 | 默认配置下 UI 断言/observer/历史多关闭；verdict 主要靠 LLM 自报；JUnit 结果文件可留痕 | 原始证据保留、来源/完整性/派生关系、完整性校验、脱敏；不靠模型摘要 | Hercules 默认弱证据、可 False Pass | 不照搬默认配置；TraceGuard 按阶段 4 建强证据链 |
| 自愈 | healing/（failure_classifier + heal_proposer + selector_store）提供失败分类与 selector 修复建议 | Oracle-Protected Self-Healing：断言保护 + 沙箱 + 审批 + 回归 + CAS 晋升；禁止削弱断言 | Hercules healing 无 Oracle 保护/审批/回归门禁 | 借鉴"失败分类 + selector 候选 + 存储"的局部机制（C 类）；TraceGuard 必须补断言保护与审批回归（阶段 10） |
| 人工审批 | 无 | Repair 审批、TestCase/Oracle 审核 | Hercules 缺失 | 不借鉴；TraceGuard 强制审批 |
| Benchmark | coverage / normalized_prd / test_plan 有测试覆盖与计划能力，但无对照实验体系 | UI-FaultBench A~E 对照 + 消融 | Hercules 无量化 Benchmark | 不借鉴（缺失） |

## 二、可以借鉴的设计

1. **Planner 生成结构化执行计划（B 架构借鉴）**：plan/next_step/target_helper 的结构化输出，可作为 Agentic Testing 中"规划与执行分离"参考；判定字段须剥离。
2. **多工具型 Agent 注册机制（B 架构借鉴）**：AgentRegistry + browser/api/sql/mcp nav agent 的组织，参考其"按工具能力组织 Agent"，但 TraceGuard 的工具访问必须经 Tool Gateway/Probe Registry 授权（阶段 5/9）。
3. **测试报告产物结构化打包（C 局部实现）**：JUnit XML + 失败截图 bundle + Markdown 报告的产物组织，可独立重实现用于 TraceGuard 报告层（阶段 3/13），不得复制代码。
4. **healing 的失败分类 + selector 候选存储（C 局部实现）**：failure_classifier / selector_store 的机制思路，用于 TraceGuard Locator 更新候选；必须外加 Oracle/Assertion 保护、沙箱、审批、回归、CAS 晋升。
5. **循环防护（B/C 已接入机制 + D 未接入函数）**：`simple_hercules.py` 的 `progress_stall_count`/`_PLANNER_STALL_LIMIT=3` 与 nav 最大轮次（`browser_nav_max_chat_round=10`）已接入主链路，可借鉴其"无进展即终止 + 轮次上限"思路（C，独立实现）；`detect_llm_loops.is_agent_stuck_in_loop` 有实现但本快照未发现调用点，仅作设计参考（D），不得描述为已工作的核心机制。

## 三、不应照搬的内容

1. **不照搬任何 Hercules 源码**：AGPL-3.0 强 copyleft + 第 13 条网络披露义务，与 TraceGuard 交付形态冲突；只能学设计、独立实现。
2. **不照搬"Planner 自报 is_passed 即通过"**：直接违反 TraceGuard Oracle 独立 + 确定性 Verdict 冻结原则，是 False Pass 风险源。
3. **不照搬无审批的自愈**：Hercules healing 无断言保护/审批/回归，与"修复不得削弱 Assertion"冲突。
4. **不照搬 playwright<=1.49 版本锁定**：版本须按 P01（技术版本与兼容矩阵）实测确定，不能继承旧锁定。
5. **不照搬默认关闭证据能力的配置**：TraceGuard 要求原始证据可追溯，不能依赖 LLM 自报。

> **AGPL-3.0 合规边界（法律解释修正）**：Hercules 采用 AGPL-3.0。其第 13 条规定的是——若**修改了 AGPL 程序**且修改版支持用户通过网络远程交互，须向这些远程用户提供获得修改版对应源码的机会。**不能简化为"所有使用 AGPL 软件的 SaaS 都自动公开整个宿主平台源码"**。需分别判断：单纯学习源码（不构成引入）、依赖引入（按 AGPL 处理并保留声明）、复制/修改形成衍生作品并分发或网络提供（触发相应源码义务）、是否与 TraceGuard 构成组合程序（逐案评估）。**工程决策不变**：不复制、不移植、不并入其源码，仅学设计；任何依赖引入先做法律评估。

## 四、借鉴方式归类汇总

> 口径说明：Hercules 在决策台账（PRJ-11）与跨阶段总表中的**总体借鉴类型为 C/D**（因 AGPL-3.0 强 copyleft，不引入、不复制代码，只借鉴机制并独立重实现）。下表细化到具体借鉴点：其中"结构组织方式"按 B 理解（仅作架构认识、不整体套用），"机制重实现"按 C，"反面案例"按 D；三者均属总体 C/D 的具体展开，与台账不矛盾。

| 借鉴点 | 类型 | 引入依赖 | 对应阶段/模块 |
|---|---|---|---|
| Planner 结构化执行计划 | B 架构借鉴 | 否 | Agentic Testing / Explorer |
| 多工具 Agent 注册组织 | B 架构借鉴 | 否 | Agent Orchestration / Probe |
| 报告产物结构化打包 | C 局部实现 | 否（独立重实现） | 阶段 3/13 报告层 |
| healing 失败分类+selector 候选 | C 局部实现 | 否（独立重实现） | 阶段 10 Self-Healing |
| 循环防护（已接入的 Planner stall/nav 轮次上限） | C 借鉴算法（独立实现） | 否 | 阶段 5/9 预算与停止 |
| `is_agent_stuck_in_loop`（有实现，未发现调用点） | D 学习参考 | 否 | 阶段 5/9 预算与停止（仅设计参考） |
| 全部源码复制/移植 | **不采用（AGPL）** | — | 法律与架构双重禁止 |
| Planner 自报 verdict / 无审批自愈 | **不采用** | — | 与冻结原则冲突 |

## 五、后续开发落地建议

- 阶段 5：参考 Planner 结构化计划，但让判定走独立 Verifier；避免角色耦合。
- 阶段 9：参考多工具 Agent 组织设计 Probe Registry，但强制五项校验 + 审计。
- 阶段 10：参考 healing 失败分类/selector 候选思路独立实现，叠加断言保护、Oracle 完整性、沙箱、审批、回归、CAS 晋升。
- 阶段 3/13：报告层参考 JUnit XML + 截图 bundle 结构，自建报告产物；不复制 bundle.py。
- 法务：将 Hercules 列入"待法律评估/不采用"台账，明确 AGPL-3.0 第 13 条风险。

## 六、研究状态

源码级研究已完成（5 份源码文档 + 本映射文档），关键结论（无独立 Assertion Agent、StateGraph 位置、AGPL-3.0）已交叉核验。借鉴建议为候选意见，须团队评审与法律评估，不构成对 v1.1 的修改。
