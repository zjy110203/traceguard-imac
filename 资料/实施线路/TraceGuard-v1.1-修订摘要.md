# TraceGuard 实施路线 v1.1 修订摘要

## 修订结论

本轮评审意见整体采纳，并对 Diagnosis 维度、RepairProposal
状态机和阶段集成策略作了工程化补充。

## 核心调整

1.  统一为五层建设路线、十四个实施阶段（0～13）、六个里程碑（M0～M5）。
2.  TeamFlow
    首期收敛到登录/测试账户、Workspace、项目创建、列表、详情；编辑建议实现，删除可选；任务、评论、活动记录、搜索和复杂权限后置。
3.  阶段顺序调整为：0～4 工程与证据基础；5 Explorer；6 Compiler；7
    Diagnosis v1；8 Evidence Graph；9 Active Diagnosis；10
    Self-Healing；11 Benchmark；12 外部泛化、安全与可靠性；13 最终验收。
4.  阶段 2 只建设本地同步确定性执行内核；阶段 3 建设 Task/Run
    平台化异步调度。
5.  Diagnosis 拆分 Symptom、Failure Domain、Failure Mechanism、Root
    Cause、Contributing Factor、Flaky Characteristic、Diagnosis
    Status、Disposition。
6.  Benchmark 从阶段 1 开始积累场景、Ground Truth 和实验元数据，阶段 11
    建成完整系统。
7.  安全基础从阶段 0 落地，阶段 12 做完整审计与强化。
8.  Event
    采用至少一次投递、幂等消费、生产者实例内序号；不承诺跨生产者全局强排序。
9.  RepairProposal 增加
    PROMOTING、PROMOTION_FAILED，明确拒绝、重新提交、晋升和回滚语义。
10. 外部应用首轮控制在 1～2 个有授权的目标。
11. 所有阶段增加统一准入、输入、非目标、接口依赖、交付、验收和退出条件。
12. 阶段集成从各里程碑持续开展；阶段 13 是最终全量回归和发布验收。

## 保留不变

-   TraceGuard 项目定位和五层建设结构；
-   十四阶段总量、六个里程碑和 C01～C12；
-   Agent 是能力层，不是事实层；
-   Playwright 负责正式确定性执行；
-   Oracle 独立于 Agent；
-   Event 与 Evidence 分离；
-   相关不等于因果；
-   自愈不得修改正式 Oracle 或削弱必需 Assertion；
-   模块化单体 + 独立 Worker 的总体架构方向。

## 状态

本路线 v1.1 为评审修订稿。通过项目评审后，方可标记为正式冻结基线。

## 本轮修订（F01～F13，v1.1 收尾修订）

本轮为定向收尾修订：保留总体架构、五层建设路线、十四个阶段、六个里程碑和
C01～C12，未新增业务子系统、Agent、阶段或基础设施；仅修复数据语义、契约关联、执行可靠性、验收规则和文档一致性问题。

| 编号 | 修订主题 | 主要落地位置 | 修订内容 |
| --- | --- | --- | --- |
| F01 | Event 上下文关联 | 阶段 4、C05、阶段 8 | Envelope 改用 `context_refs` 关联 Workspace / Project / Task / Run / Exploration / ProbeExecution / Experiment / Trace / Span / Request；`run_id` 不再必填，`workspace_id` 必填且只能由服务端可信上下文注入；关联对象不存在保留为 unresolved；消费者可识别上下文类型；Graph 仅依据已校验关联建边 |
| F02 | Event 顺序、幂等与缺口 | 阶段 4、C05、阶段 8 | 序号唯一范围固定为 `(producer_instance_id, sequence)` 且并发安全分配，重启换新实例并从新序列空间计数；幂等键创建时生成、重试不变、producer 维度唯一、同键异载荷拒绝；trace / span / request 仅作关联线索；`occurred_at` 与 `observed_at` 分离；缺口先标记待确认并区分延迟与丢失，确认窗口值待确认 |
| F03 | Diagnosis 枚举统一 | 阶段 7、C08、C10 | 示例表改用正式枚举 TEST_ASSET / TEST_EXECUTION、UI_RENDERING、AUTHORIZATION 等；明确八个维度相互独立，不混成单一故障分类字段 |
| F04 | 多 Hypothesis 与根因 | 阶段 7、C08、阶段 8 | Diagnosis 支持多个 Hypothesis，各自独立状态与双向证据；未达确认规则不得成为 Root Cause；允许无已确认根因；多个已确认根因不强制压缩；LLM 不得自行宣布 CONFIRMED；确认门槛按故障类别制定且仍待确认 |
| F05 | Test Verdict 聚合 | 阶段 2、C03、C04 | 正式 TestCase 至少一条有效必需 Assertion，空集不得自动 PASSED；冻结四状态聚合表与 8 条补充规则；Optional 不决定 Verdict；执行中断不掩盖已知失败；Task 与多 Run 汇总规则；补充 7 个合法与 3 个非法样例 |
| F06 | Worker Lease 与 Fencing | 阶段 3、C04 | 增加租约、心跳续租、过期判定、执行权接管、单调递增执行代次与写入校验；旧 Worker 不得提交有效结果；副作用不能由 Token 自动撤销，无法确认时隔离或重置数据、标记结果不确定并新建 Run 重试；增加故障注入测试 |
| F07 | Probe 数据访问边界 | 阶段 9、C09、Tool Gateway | 只读不等于无风险；查询模板 / 表 / 字段白名单、Workspace / Project 约束、脱敏、行数与数据量限制、超时与资源限制、调用审计、输出来源记录；Backend Log Read 限范围 / 时间窗口 / 字段；Database Read 首期仅用授权只读模板，禁止 Agent 任意 SQL；执行前五项校验，失败拒绝并审计 |
| F08 | Candidate 稳定性晋升 | 阶段 6 | 约定稳定性验证策略（次数与门槛按场景及风险配置，本轮不统一固定）；每次回放保留独立 Run；记录 Verdict 与环境差异并关联 compiler / browser / 候选版本；不稳定候选不晋升，无法确认保持候选；区分确定性失败、环境故障、数据问题、不稳定表现与证据不足；不降低必需 Assertion 严格度 |
| F09 | CI 三级门禁 | 5.2（阶段 0、11、13 的原累积安排保留） | 拆分为 PR Gate、Nightly Gate、Release Gate，明确各自检查项与四条门禁规则；PR 不跑完整 Benchmark，Nightly 失败可定位用例与环境，Release 关键失败项不得忽略放行 |
| F10 | Repair 版本并发保护 | 阶段 10、C11 | 增加 base / candidate / target 版本与 `promotion_record`；晋升前校验当前正式版本等于 `base_testcase_version`，不一致则阻断、标记重新验证或冲突处理、重新生成提案，不覆盖他人版本；TestCaseVersion 只追加，回滚留痕；Oracle 不可经 RepairProposal 修改；沿用原状态机 |
| F11 | 待确认事项台账 | 7.2 | 12 项改为统一表格，含最晚确认阶段、责任角色（待分配）、当前状态与是否阻塞阶段；阻塞按实际依赖判断，未确定角色不编造人员 |
| F12 | EIG 公式修正 | 阶段 9 | 清理导出样式代码，改为标准 LaTeX；定义 EIG / C / R 与三个权重；首期使用可解释启发式评分，归一化、权重与参数仍待确认；EIG 为估计量而非已校准概率 |
| F13 | 修订摘要同步 | 本文件 | 追加本轮 F01～F13 记录，保留原有修订历史 |

**本轮未冻结的事项：** 待确认事项（见路线图 7.2）仍保持待确认，包括技术版本、Browser
Use / Playwright 集成方式、Oracle 冲突裁决、Diagnosis 确认门槛、EIG 权重、Probe
预算、Event / Evidence 生命周期、Repair 审批角色、Benchmark 规模与统计方案、外部应用选择、SLO
与资源预算、专用图数据库必要性。本轮只补充责任落点与最晚确认阶段，未将其中任何一项改动为既定参数。

## 本轮修订（G01～G06，工程逻辑收口）

本轮是工程规则补充，不是总体架构调整。仅依据上一轮路线评审发现的六项问题做定向修订；TraceGuard 项目定位、五层建设路线、十四个实施阶段、六个里程碑、C01～C12 契约体系、原有业务闭环，以及各阶段的主要职责与建设顺序均保持不变。

| 编号 | 修改事项 | 修改章节 | 修改结果 |
| --- | --- | --- | --- |
| G01 | P03 与 Verdict 冻结状态对齐 | 阶段 2、7.2、第 4 章 C03、第 8 章 | 已修订 |
| G02 | Consumer Inbox 消费端幂等 | 阶段 4、C05、第 8 章 | 已修订 |
| G03 | Repair 原子版本晋升 | 阶段 10、C11、第 8 章 | 已修订 |
| G04 | Nightly 与 Release 准入 | 5.2、第 8 章 | 已修订 |
| G05 | Workspace 与系统级事件边界 | 阶段 4、C05、第 8 章 | 已修订 |
| G06 | Sequence 合法跳号与缺口判断 | 阶段 4、C05、第 8 章 | 已修订 |

**状态区分：**

-   已完成文档修订：G01～G06 六项规则已写入路线图相应章节，最终验收清单与 C03 / C05 / C11 契约映射已同步。
-   已冻结的设计语义：阶段 2 基础 Test Verdict 聚合规则（含 Oracle 冲突无法裁决时判 INCONCLUSIVE 的兜底规则）；Event 至少一次投递、`workspace_id` 必填且由服务端可信上下文注入；三级门禁的职责划分；Repair 沿用原状态机。
-   后续阶段详细设计：Consumer Inbox 具体表结构与字段（阶段 4）；Producer Checkpoint / 已提交事件高水位是否引入（阶段 4）；原子晋升的具体数据库实现机制（阶段 10）；平台级系统事件统一纳入 Event Store 所需的 system-level scope 契约扩展。
-   仍需团队确认：7.2 全部 12 项待确认事项，尤其 P03（Oracle 多来源优先级、可信等级与冲突裁决细则，阶段 2 完成冲突场景验收前）与 P08（Repair 审批角色）；责任角色未定的仍为"待分配"。
-   尚未实现：文档修订完成不代表 Consumer Inbox、原子晋升、Release 门禁、Workspace 边界控制等功能已经实现。本轮不涉及代码开发、部署或任何实现工作。

### 本轮修订（G07～G08，文档一致性收尾）

本轮属于最后两项工程规则收尾，仅修正文档内部一致性，不改变总体架构、五层建设路线、十四个阶段、六个里程碑、C01～C12，也不改变 G01～G06 已完成的修订内容。

| 编号 | 修改事项 | 修改章节 | 修改结果 |
| --- | --- | --- | --- |
| G07 | CI 流程图与三级门禁规则一致性 | 5.2、第 8 章 | 已修订 |
| G08 | VERSION_CONFLICT 与普通晋升重试边界 | 阶段 10、第 4 章 C11、第 8 章 | 已修订 |

**状态区分：**

-   已完成文档修订：G01～G08 均已完成文档层面的修订，路线图与修订摘要保持一致。
-   已冻结的设计语义：G07 的三级门禁职责、Nightly 失败分级、Release 准入与风险豁免有效期规则；G08 的普通技术故障可按条件幂等重试、VERSION_CONFLICT 必须基于当前正式版本重新验证并重新审批、不得直接重试旧提案。
-   后续阶段详细设计：CI 门禁具体执行脚本和阻断实现；RepairProposal 冲突处理、提案修订及审批的具体工程实现。
-   尚未实现：本轮仍然只修改文档，没有实现 CI 自动阻断、RepairProposal 状态控制、数据库 CAS 或其他功能；不得将文档修订完成描述成代码功能已经实现。
