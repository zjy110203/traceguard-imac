# TraceGuard 测试环境与 UI-FaultBench 评测方案

-   架构基线：v1.1
-   状态：双环境策略和评测方向已形成共识；数据集和指标细节待冻结

## 1. 评测体系目标

UI-FaultBench 用于证明 TraceGuard 的核心技术是否有效。

评测不能只展示 Agent 能打开网页、点击按钮或生成代码，而应回答： -
测试生成是否正确和稳定； - 测试预期是否可信； - 证据关联是否有帮助； -
故障诊断是否准确； - 主动探针是否降低排查成本； -
自愈是否保持原始断言并避免回归。

## 2. 双环境策略

``` mermaid
flowchart TB
    TG["TraceGuard"]
    subgraph TEAM["TeamFlow：受控实验环境"]
        T1["业务 UI"]
        T2["API"]
        T3["独立业务状态"]
        T4["日志 / Trace"]
        T5["故障注入"]
        T6["Ground Truth"]
    end

    subgraph EXT["External Web Apps：外部应用"]
        E1["真实页面结构"]
        E2["多样交互"]
        E3["有限观测能力"]
        E4["泛化验证"]
    end

    TG --> TEAM
    TG --> EXT
```

### 2.1 TeamFlow

TeamFlow 是受控被测应用和实验环境，不是 TraceGuard 的产品主体。

建议业务范围： - 用户与权限； - Workspace； - Project； - Task； -
Comment； - Search； - API； - 独立业务状态查询； - 后端日志和 Trace； -
故障注入接口。

TeamFlow 必须能够独立记录实际业务状态，避免用被测 UI 自己证明 UI 正确。

### 2.2 External Web Apps

用于验证： - 页面探索泛化； - Locator 适应能力； - UI 自动化执行； -
证据采集能力； - 受限证据下的分析能力。

如果外部应用没有独立可信 Ground Truth： - 可以评价探索成功率； -
可以评价操作完成率； - 可以评价证据采集完整度； - 可以开展人工复核； -
不应直接宣称根因诊断准确率。

## 3. 故障注入设计

  类别            示例
  --------------- ----------------------------------
  Product Logic   创建逻辑错误、权限判断错误
  Persistence     数据库事务失败、数据未写入
  API             5xx、错误响应、响应字段异常
  Network         超时、断连、请求失败
  UI              元素缺失、文本变化、页面结构变化
  Timing          加载延迟、异步竞争
  Test Asset      Locator 失效、测试数据错误
  Agent           错误点击、错误目标识别
  Environment     浏览器异常、服务不可用

每个故障注入样本应有故障机制 ID、注入配置、适用场景、预期影响、Ground
Truth、清理方法和可重复性说明。

## 4. 三类 Benchmark

### 4.1 Test Generation Benchmark

目标：评价 Agent 能否生成可审核、可重复执行的测试资产。

候选指标： - Requirement Interpretation Accuracy； - Scenario
Coverage； - Assertion Correctness； - Locator Validity； - TestCase
Promotion Rate； - Replay Stability； - Human Review Effort。

### 4.2 Diagnosis Benchmark

目标：评价证据分析、故障分类、根因识别和主动诊断。

候选指标： - Macro-F1； - Primary Cause Accuracy； - False Pass Rate； -
Selective Accuracy； - Diagnosis Coverage； - Probe Count； - Time to
Diagnosis； - Evidence Coverage。

### 4.3 Self-Healing Benchmark

目标：评价测试资产修复的有效性和安全性。

候选指标： - Repair Validation Success Rate； - Assertion Preservation
Rate； - Repair Acceptance Rate； - Repair Regression Rate； - Repair
Time； - Rollback Rate。

## 5. Trusted Test Set 与 Agent Generated Test Set

``` mermaid
flowchart LR
    A["Trusted Test Set"]
    B["人工编写 / 审核 TestCase"]
    C["已批准 Oracle"]
    D["Diagnosis Benchmark"]

    E["Agent Generated Test Set"]
    F["Agent 探索"]
    G["TestCase Compiler"]
    H["人工审核 / 回放"]
    I["Test Generation Benchmark"]

    A --> B --> C --> D
    E --> F --> G --> H --> I
```

Trusted Test Set 用于隔离验证诊断系统，避免测试生成错误污染诊断评测。

Agent Generated Test Set 用于独立评价测试生成质量。

最终可以进行端到端实验，但必须分别报告测试生成和诊断环节的误差。

## 6. Diagnosis 对照实验 A～E

  组别   方法                           目的
  ------ ------------------------------ -------------------
  A      Playwright 原始报告            传统自动化基线
  B      单次 LLM 分析                  普通 LLM 分析基线
  C      规则驱动分类                   确定性规则基线
  D      多源 Evidence + Graph + LLM    评价证据关联价值
  E      D + Active Diagnosis Planner   评价主动探针增益

D 与 E 应尽可能使用相同的输入测试集、模型版本、模型配置和基础证据。E
的额外能力是主动选择探针。

## 7. 主要指标定义

### 7.1 Macro-F1

多标签根因场景下，按类别计算
F1，再计算宏平均。需要明确标签映射、未知类别和多根因处理规则。

### 7.2 Primary Cause Accuracy

\[ Accuracy\_{primary} =
`\frac{\text{主要根因 Top-1 判断正确的样本数}}`{=tex}
{`\text{具有有效主要根因标签的样本数}`{=tex}} \]

### 7.3 False Pass Rate

\[ FPR\_{pass} = `\frac{\text{错误判定为 PASSED 的故障测试数}}`{=tex}
{`\text{Oracle 明确要求失败的有效故障测试数}`{=tex}} \]

INCONCLUSIVE 不得计作 PASSED。应同时报告
Coverage，避免通过大量输出不确定来掩盖判定能力不足。

### 7.4 Selective Accuracy

\[ SA = `\frac{\text{给出确定诊断且正确的样本数}}`{=tex}
{`\text{所有给出确定诊断的样本数}`{=tex}} \]

需要同时报告 Diagnosis Coverage。CONFIRMED 和 PROBABLE 应分开统计。若
PROBABLE 用于非破坏性建议，应单独标记，不能宣传为已确认根因。

### 7.5 Diagnosis Coverage

\[ Coverage = `\frac{\text{得到目标等级诊断的样本数}}`{=tex}
{`\text{有效诊断样本总数}`{=tex}} \]

目标等级是 CONFIRMED 还是 CONFIRMED + PROBABLE，需要在实验协议中明确。

### 7.6 Time to Diagnosis

从有效失败被系统识别到诊断结果形成的时间。需要统一计时起点、终点，并区分模型等待、探针执行和人工介入时间。

### 7.7 Repair 指标

-   Repair Validation Success
    Rate：通过既定沙箱和回归验证的候选修复比例。
-   Assertion Preservation Rate：原始断言保持有效的比例。
-   Repair Acceptance Rate：获批准修复数 / 已审核修复数。
-   Repair Regression Rate：引入回归问题的已验证修复数 / 已验证修复数。

指标分母、排除条件和失败样本处理需要在正式评测协议中冻结。

## 8. 消融实验

  消融对象                      研究问题
  ----------------------------- --------------------------
  移除 Evidence Graph           证据关联是否提高诊断质量
  移除 Oracle Protection        测试可信度是否下降
  移除 Active Diagnosis         诊断效率和准确性变化
  移除 Probe Budget             是否产生过多无效探针
  移除 Assertion Preservation   自愈是否更容易削弱测试

## 9. 数据集隔离与防泄漏

数据集划分不能只按 Run 随机拆分。

建议按故障机制、场景变体、应用版本、测试数据种子、业务对象和运行时间批次隔离。

Ground Truth 应由独立数据域保存，Agent 运行环境不应获得直接读取权限。

## 10. 实验控制变量

-   模型版本；
-   模型参数；
-   Prompt 版本；
-   工具版本；
-   浏览器版本；
-   TeamFlow 版本；
-   TestCase 版本；
-   Oracle 版本；
-   故障注入配置；
-   探针预算；
-   时间预算；
-   重复次数；
-   随机种子。

需要报告均值、波动和适当的统计置信区间，不能只展示一次最好的结果。

## 11. Benchmark 数据对象

建议对象： - Dataset； - DatasetVersion； - BenchmarkCase； -
GroundTruth； - Experiment； - ExperimentRun； - Baseline； -
EvaluationMetric； - EvaluationResult； - Artifact Reference。

## 12. 待确认事项

-   首批故障机制和覆盖范围；
-   Ground Truth 生成和审核责任；
-   数据集版本和隔离策略；
-   Macro-F1 标签体系；
-   PROBABLE 是否计入特定指标；
-   统计置信区间和重复次数；
-   Benchmark 资源预算；
-   外部应用人工复核方法；
-   修复回归的范围定义。
