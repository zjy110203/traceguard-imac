# TraceGuard Agent 与核心技术设计

-   架构基线：v1.1
-   状态：能力边界和研究方向基线；算法细节、模型配置和参数待实验确定

## 1. Agent 设计总原则

TraceGuard 的 Agent 是能力层，不是事实层。

Agent 可以理解、探索、规划、推理、提出候选方案和解释证据。

Agent 不可以： - 自行批准正式测试预期； - 自行修改权威业务状态； -
自行宣布测试通过； - 自行宣布根因已确认； - 绕过权限网关调用工具； -
自行修改 Ground Truth； - 直接修改正式 TestCase； - 通过删除或弱化
Assertion 制造通过。

## 2. 四类核心 Agent

  ------------------------------------------------------------------------------------
  Agent                   主要职责                             主要输出
  ----------------------- ------------------------------------ -----------------------
  Explorer Agent          浏览器探索、页面理解、业务路径发现   Exploration
                                                               Trace、候选场景

  Test Generation Agent   提出测试步骤、断言和测试数据         Candidate
                                                               TestCase、Candidate
                                                               Assertion

  Diagnosis Agent         分析证据、提出假设、解释冲突         Hypothesis、诊断建议

  Repair Agent            提出测试资产修复方案                 Repair Proposal、Diff
                                                               建议
  ------------------------------------------------------------------------------------

Agent 与系统模块不是一一对应关系。Compiler、Verifier、Probe Engine
是确定性模块，不因调用模型就自动成为 Agent。

## 3. Explorer Agent

输入： - TestIntent； - 被测应用入口； - 授权浏览器上下文； -
允许使用的工具； - 探索预算。

能力： - 页面观察； - 元素识别； - 自然语言目标分解； - 交互探索； -
页面状态比较； - 候选路径记录； - 异常现象记录。

输出： - Exploration Trace； - 页面状态快照； - 操作步骤； - Locator
候选； - 候选 Assertion； - 未解决问题。

探索成功不等于测试通过，也不等于候选用例可以晋升。

## 4. Test Generation Agent

职责： - 根据 TestIntent 提出候选测试场景； - 建议操作步骤； -
建议测试数据； - 提出 Assertion； - 建议 Oracle 观测方式； -
解释需求与测试步骤的映射。

限制： - 不得自行批准 Oracle； -
不得把页面成功提示直接等同于业务持久化； - 不得把探索轨迹原样晋升为正式
TestCase； - 不确定预期必须显式标记。

## 5. Diagnosis Agent

职责： - 阅读结构化 Evidence； - 提出候选 Hypothesis； -
总结支持证据； - 总结反驳证据； - 识别证据冲突； - 提出候选 Probe； -
解释诊断不确定性。

限制： - 不得读取受保护 Ground Truth； -
不得把相关关系表述为因果事实； - 不得把 Probe 失败当成目标故障证据； -
不得绕过 Probe Registry 和权限检查； - 不得自行把 PROBABLE 改成
CONFIRMED。

## 6. Repair Agent

职责： - 根据已批准的测试资产问题提出修复建议； - 提出 Locator 更新； -
提出测试步骤调整； - 生成候选 Diff； - 解释修改原因和影响范围。

限制： - 不修改业务产品代码； - 不修改 Oracle 预期； - 不直接写入正式
TestCase； - 不删除或弱化 Assertion； - 不执行任意 Shell 命令； -
不访问批准范围之外的文件。

## 7. 核心技术一：TestCase Compiler

### 7.1 问题定义

Agent 的探索轨迹是动态交互记录，不等于稳定测试。Compiler
负责把探索结果转换成可审核、可重复执行的结构化 TestCase。

### 7.2 编译流程

``` mermaid
flowchart TD
    A["Exploration Trace"]
    B["Trace Normalization"]
    C["Action Extraction"]
    D["Locator Resolution"]
    E["Assertion Construction"]
    F["Oracle Binding"]
    G["Stability Validation"]
    H["Repeated Replay"]
    I["Human Review"]
    J["TestCaseVersion Promotion"]

    A --> B --> C --> D --> E --> F --> G --> H --> I --> J
    G -->|"失败"| K["Reject / Revise"]
    H -->|"失败"| K
    I -->|"拒绝"| K
```

检查： - 操作目标是否明确； - Locator 是否唯一或满足策略； - Locator
是否依赖脆弱 DOM 位置； - Assertion 是否对应需求； - Oracle
是否有可信来源； - 测试数据是否可重复； - 前置条件是否明确； -
后置清理是否完整； - 重复回放是否稳定。

研究问题： - 自然语言需求如何映射测试步骤； - 页面语义和 Locator
如何结合； - 如何评估 Locator 稳定性； - 如何识别无效或过弱断言； -
如何自动发现测试数据依赖； - 如何评价候选用例质量。

## 8. 核心技术二：Evidence Correlation Graph

目标：把
UI、DOM、网络、API、业务状态、日志和探针结果组织为可追溯证据网络。

节点： - Run； - Step； - Event； - Evidence； - Assertion； -
Hypothesis； - ProbeExecution； - Source。

关系： - parent_child； - emitted_by； - derived_from； -
correlated_by_trace_id； - correlated_by_request_id； -
same_execution_context； - temporal； - supports； - contradicts； -
evaluates。

建议记录来源独立性字段： - source_id； - source_family； -
independence_group； - derived_from； - provenance； -
integrity_check； - source_trust； - freshness。

多个证据如果依赖同一个底层来源，不能简单按独立票数累计。

证据冲突时检查： 1. Assertion 的 Primary Oracle； 2. Supporting Oracle
的职责； 3. 一致性等待窗口； 4. 数据源依赖关系； 5. 证据新鲜度； 6.
来源完整性和可信度； 7. 是否存在无法解释的冲突。

不能用简单多数投票替代权威来源规则。关键冲突未解决时允许 INCONCLUSIVE。

## 9. 核心技术三：Active Diagnosis Planner

目标：根据当前 Hypothesis
和证据缺口，选择信息价值高、成本可控、风险可接受的下一步探针。

``` mermaid
flowchart TD
    A["Failure Triage"]
    B["Generate Hypotheses"]
    C["Evidence Gap Analysis"]
    D["Candidate Probe Generation"]
    E["Permission / Risk Gate"]
    F["Probe Scoring"]
    G["Probe Execution"]
    H["Evidence and Hypothesis Update"]
    I{"Stop Condition?"}
    J["Diagnosis Result"]

    A --> B --> C --> D --> E
    E -->|"拒绝"| C
    E -->|"允许"| F --> G --> H --> I
    I -->|"否"| C
    I -->|"是"| J
```

建议评分函数：

\[
Score(p)=w_I`\widehat{EIG}`{=tex}(p)-w_C`\widehat{C}`{=tex}(p)-w_R`\widehat{R}`{=tex}(p)
\]

其中 EIG 是预期信息增益，C 是执行成本，R
是操作风险，带帽变量是归一化评分，权重通过实验校准。

未经校准的 EIG 只能作为启发式评分，不应声称为准确概率。

硬性策略： - 权限检查先于评分； - 未授权探针禁止执行； -
高风险写操作默认禁止； - 只读探针优先； -
有副作用的探针必须定义清理策略； -
每次诊断设置探针数量、时间和资源预算； - ProbeExecution 失败独立记录。

停止条件： - 关键假设满足确认规则； - 高价值安全探针耗尽； -
预算耗尽； - 时间上限达到； - 关键证据冲突无法解决； -
继续执行探针的边际收益不足。

诊断结果：CONFIRMED、PROBABLE、INCONCLUSIVE。

## 10. 核心技术四：Oracle-Protected Self-Healing

目标：修复测试资产，同时保证原始测试预期和断言不被削弱。

``` mermaid
flowchart TD
    A["Repair Proposal"]
    B["Generate Diff"]
    C["File Scope Validation"]
    D["Assertion Preservation"]
    E["Locator / Static Validation"]
    F["Sandbox Replay"]
    G["Regression Validation"]
    H["Human Approval"]
    I["Promote New TestCaseVersion"]
    J["Rollback if Needed"]

    A --> B --> C --> D --> E --> F --> G --> H --> I --> J
    C -->|"失败"| X["Reject"]
    D -->|"失败"| X
    E -->|"失败"| X
    F -->|"失败"| X
    G -->|"失败"| X
```

保护要求： - 原始 Oracle 不可被 Repair Agent 修改； - 原始 Assertion
不得被无理由删除； - 不得通过放宽断言制造通过； -
文件写入限定在批准范围； - 所有修改有 Diff； -
候选修复在隔离沙箱执行； - 通过相关回归； - 需要有效审批； -
保留旧版本和回滚记录。

Repair Proposal 生命周期与沙箱验证状态分开保存。

## 11. Agent 工作流技术建议

建议使用 LangGraph 管理有状态 Agent 工作流，但： - PostgreSQL
是业务状态权威来源； - LangGraph Checkpoint 是工作流恢复信息； - Celery
负责异步任务分发； - Agent 通过 Tool Gateway 调用受控工具； - Agent
输出经过结构化校验和策略检查。

## 12. 核心技术验证问题

  技术                必须回答的问题
  ------------------- ---------------------------------------
  TestCase Compiler   是否比直接生成脚本更稳定、更可审核？
  Evidence Graph      是否比简单日志拼接更有诊断价值？
  Active Diagnosis    是否比一次性 LLM 分析更省时、更准确？
  Self-Healing        是否提高修复成功率且不削弱断言？

这些问题必须通过 UI-FaultBench 验证，不能只靠演示。
