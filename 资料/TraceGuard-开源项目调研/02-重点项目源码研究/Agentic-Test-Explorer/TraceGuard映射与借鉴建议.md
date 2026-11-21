# Agentic Test Explorer → TraceGuard 映射与借鉴建议

> 本文档是 ATE 源码级研究的收口，把已核实机制与 TraceGuard v1.1 正式设计逐项对照。
> 依据：本目录源码研究 5 份文档 + TraceGuard v1.1 路线图/总体架构/核心技术设计。
> 纪律：开源项目的功能来自实际源码；TraceGuard 一侧只引用 v1.1 正式术语，不擅自修改设计。

## 一、逐维度对照表

| 对比维度 | ATE 实现（源码已核实） | TraceGuard 原设计（v1.1） | 差异 | 借鉴建议 |
|---|---|---|---|---|
| Agent 组织方式 | 单 Supervisor 集中路由，`AgentState.next_agent` 选择下一 worker；所谓 Swarm 无 Agent 间直接任务传递（`graph_base.py:468/547/643`） | 四类核心 Agent（Explorer / Test Generation / Diagnosis / Repair）+ 有状态工作流；Agent 是能力层不是事实层 | ATE 是路由式单 Supervisor，无诊断/修复 Agent，且 Agent 输出直接决定结果 | 借鉴 Supervisor 集中路由与结构化 `next_agent` 编排；不照搬"Agent 自报结果即事实" |
| 浏览器控制 | Playwright Engine + JSON Intent，Agent 只发意图，`_dispatch` 确定性执行；Action Tape 落盘（`engine.py`） | Playwright 负责正式确定性执行；Explorer 通过 Browser Use + Playwright Adapter + Tool Gateway | ATE 的 Brain/Hands 分离思路与 TraceGuard"Agent 是能力层"高度一致；但 ATE 无独立 Tool Gateway 权限校验 | **重点借鉴** Brain/Hands 分离：LLM 产出结构化意图、确定性层执行并审计；TraceGuard 需再加 Tool Gateway 权限/预算校验（阶段 5） |
| 状态管理 | `AgentState`（TypedDict）+ SQLite Checkpoint（langgraph-checkpoint-sqlite）+ langmem 长期记忆（`memory.py`、`graph_base.py:70`） | PostgreSQL 为业务状态权威；LangGraph Checkpoint 仅作工作流恢复信息；业务状态不由模型文本决定 | ATE 把探索记忆与 verdict 混在 Agent 状态/文本里，无独立权威业务存储 | 借鉴 Checkpoint + 语义记忆的持久化**形态**；TraceGuard 必须把业务事实落到 PostgreSQL，Checkpoint 只做恢复（阶段 4/5 边界） |
| 任务输入 | Mission / 自然语言任务 + PR 分析（`pr_analyzer.py`） | TestIntent（C01）→ Explorer；TestIntent 有需求来源与审核状态 | ATE 无 TestIntent 结构化契约、无需求来源与审批 | 借鉴 PR 驱动触发思路；TraceGuard 用 C01 TestIntent 契约承接，AI 候选不自动成为正式预期 |
> **Action Tape 可信度边界（源码核实）**：ATE 的 Tape 内存表示为可变 `List`，`_append_tape()` 以文件追加模式写 JSONL、写盘异常仅 warn；源码未发现哈希链、数字签名、篡改检测或不可变存储（源码注释中的 "immutable" 属设计意图表述）。因此 Tape 只是**按正常执行路径追加的操作日志**，既不等于防篡改证据，也不等于 TraceGuard 的正式可信 Evidence。TraceGuard 须自行实现原始证据保留、来源标识、完整性校验、派生关系与审计机制。

| 测试用例生成 | Action Tape → `reproduction_*.spec.ts` 线性回放脚本（`generate_playwright_spec`，engine.py:565-611，snapshots 为 no-op） | TestCase Compiler：Trace Normalizer→Action Extraction→Locator Resolver→Assertion Constructor→Oracle Binding→Stability Validation→人工审核→正式版本 | ATE 只生成"可回放脚本"：`extract_text` 仅生成 `expect(_t.length).toBeGreaterThan(0)` 弱检查，不验证目标 Bug 是否复现、不验证业务预期；无独立断言构造、无 Oracle 绑定、无稳定性验证、无人工审核晋升 | **重点借鉴** Tape→spec 的确定性转写；TraceGuard 必须补齐 Compiler 的断言/Oracle/稳定性/审核链（阶段 6），不能把脚本当正式 TestCase |
| Oracle | 无独立 Oracle；bug 判定与 PASS/FAIL 靠 LLM 文本自述；`analyze_visual_state` 未接入主图 | Trusted Oracle（C03）独立于 Agent，正式预期有来源和审批；Oracle 不可用不得判 PASSED | 根本性差异：ATE 无确定性判定，存在 False Pass 风险 | **不采用** ATE 的判定方式；TraceGuard 坚持 Oracle 独立 + 确定性 Verdict（阶段 2 冻结规则） |
| Assertion | 无结构化 Assertion 级结果；spec 回放无重现断言 | Assertion 级 PASSED/FAILED/INCONCLUSIVE/NOT_EVALUATED + Test Verdict 聚合（已冻结） | ATE 缺失整个断言判定维度 | 不借鉴；TraceGuard 自行实现（阶段 2） |
| 执行确定性 | Agent 每步由 LLM 决策；回放脚本为线性重放，snapshot 为 no-op | 正式执行脱离 Agent、Playwright 确定性回放；候选需稳定性验证后才晋升 | ATE 探索可执行但"测试"不脱离 LLM 判定 | 借鉴确定性执行层设计；TraceGuard 探索（阶段5）与正式执行（阶段2）严格分离 |
| 证据采集 | Action Tape（jsonl）、bug summaries、截图落 `report_<thread>/` | Event/Evidence（C05/C06）分层：Raw/Normalized/Derived，来源与完整性可追溯、脱敏 | ATE 有轨迹留痕但非结构化 Event，无来源可信度/完整性/派生关系 | 借鉴"动作留痕"；TraceGuard 升级为 Event/Evidence 基础设施（阶段 4），不得用 LLM 摘要替代原始证据 |
| 故障诊断 | Bug Catalog 由 LLM 提取与分类 | Diagnosis（C08）八维模型 + 多 Hypothesis + 支持/反驳证据 + 确认规则 | ATE 是 LLM 直接判定 bug，无多维模型、无假设、无双向证据 | 借鉴 bug 发现与记录；诊断维度完全按 TraceGuard v1.1 自建（阶段 7） |
| 修复机制 | 无 | Oracle-Protected Self-Healing（C11）+ 断言保护 + 沙箱 + 审批 + 回归 + CAS 晋升 | ATE 无修复 | 不借鉴（缺失） |
| 历史记录 | SQLite Checkpoint + langmem 语义记忆（按 thread_id） | 业务历史在 PostgreSQL + 独立 Event 追加 + Graph；记忆与业务状态分离 | ATE 记忆与 verdict 耦合 | 借鉴语义记忆形态；TraceGuard 保持历史只追加、记忆不覆盖事实 |
| Benchmark | 无 | UI-FaultBench（C12）三类评测 + A~E 对照 + 消融 | ATE 无量化评测 | 不借鉴（缺失） |
| 人工审批 | 无 | TestCase/Oracle 审核、Repair 审批 | ATE 无审批环节 | 不借鉴；TraceGuard 强制人工审核不可绕过 |

## 二、可以借鉴的设计（按可借鉴程度排序）

1. **Brain/Hands 分离 + JSON Intent（高价值）**：Agent 只输出结构化动作意图，确定性 dispatch 层负责执行并落 Action Tape。与"Agent 是能力层不是事实层"灵魂原则一致，可直接指导阶段 5 Explorer Agent 的架构（借鉴方式 B：架构与设计）。
2. **Action Tape → Playwright spec 转写（高价值）**：把探索轨迹确定性转写为可回放脚本的机制，可作为阶段 6 TestCase Compiler "Action Extraction → 可回放步骤"的参考实现（借鉴方式 C：算法与局部实现）。注意 TraceGuard 仍需补断言、Oracle、稳定性与审核。
3. **Supervisor 集中路由 + 有界路由摘要（中价值）**：`next_agent` 结构化跳转、给 Supervisor 的精简路由 brief 控制 token，可作为 Agent Orchestration 多 Agent 编排参考（借鉴方式 B）。
4. **SQLite Checkpoint + 语义记忆持久化形态（中价值）**：langmem 语义记忆、按 thread 存探索记忆的思路，可作为 Explorer 上下文管理参考；但存储后端与权威边界须按 TraceGuard 改造（借鉴方式 C）。
5. **PR 驱动探索触发（低-中价值）**：pr_analyzer 从 PR 变更触发探索，可作为未来"变更驱动的回归探索"参考（借鉴方式 D：学习参考，非首期）。

## 三、不应照搬的内容（与 TraceGuard 架构/可信边界冲突）

1. **不照搬"LLM 自报 PASS/FAIL"作为测试判定**：ATE 无独立 Oracle，verdict 靠模型文本自述，会造成 False Pass，直接违反 TraceGuard"Playwright 负责确定性执行、Oracle 独立于 Agent、Test Verdict 由确定性规则产生"的冻结原则。
2. **不照搬"探索轨迹即测试"**：ATE 的 spec 是线性回放、snapshot 为 no-op、无重现断言；TraceGuard 明确"探索成功不等于测试通过""不得把 Trace 直接作为正式测试"（阶段 5/6 非目标）。
3. **不照搬记忆/Checkpoint 作为业务权威**：TraceGuard 业务状态以 PostgreSQL 为权威，LangGraph Checkpoint 仅为恢复信息。
4. **不照搬无权限网关的浏览器访问**：ATE 缺少 TraceGuard 要求的 Tool Gateway 权限、域名、预算校验，直接照搬会引入越权风险。
5. **不引入其 MIT 依赖栈作为 TraceGuard 运行时依赖**：ATE 强绑定 langchain-google-genai/vertexai/anthropic 等；TraceGuard 只借鉴架构思路，不整体引入。

## 四、借鉴方式归类汇总

| 借鉴点 | 类型 | 引入依赖 | 对应阶段/模块 |
|---|---|---|---|
| Brain/Hands 分离 + JSON Intent | B 架构借鉴 | 否 | 阶段 5 Explorer Agent |
| Action Tape → spec 确定性转写 | C 算法/局部实现 | 否（自行实现） | 阶段 6 TestCase Compiler |
| Supervisor 集中路由 + 有界摘要 | B 架构借鉴 | 否 | Agent Orchestration |
| 语义记忆/Checkpoint 持久化形态 | C 算法/局部实现 | 否（后端按 TraceGuard） | 阶段 4/5 上下文管理 |
| PR 驱动探索触发 | D 学习参考 | 否 | 后续扩展（非首期） |
| LLM 自报 verdict / 轨迹即测试 | 不采用 | — | 与冻结原则冲突 |

## 五、后续开发落地建议

- 阶段 2：以 ATE "无独立 Oracle 导致 False Pass"作为反面案例，写入 Verdict 设计评审材料。
- 阶段 5：Explorer Agent 采用"LLM 产出 JSON Intent + 确定性 dispatch + Action Tape 留痕"骨架，外加 Tool Gateway 权限/预算校验。
- 阶段 6：Compiler 的 Action Extractor/可回放步骤转写参考 `engine.py` 的 tape→spec 逻辑；断言构造、Oracle 绑定、稳定性验证按 v1.1 自建。
- 阶段 4：探索轨迹纳入 Event/Evidence，保留原始 tape，不允许模型摘要覆盖。

## 六、研究状态

源码级研究已完成（5 份源码文档 + 本映射文档），关键结论已交叉核验。借鉴建议为候选意见，须经团队评审，不构成对 v1.1 的修改。
