# RUN-005 差距分析：设计目标 vs 当前实现

- 对照基准：`资料/项目资料/*`（架构 v1.1）、`资料/实施线路/TraceGuard-完整项目实施路线图-v1.1.md`（阶段 0–13 + 最终验收清单）
- 实现基线：`bb5ec9a` + `7b5ce36`（RUN-005-CORE），285 passed / 5 skipped
- 判定口径：以**可复核证据**为准；代码存在但无运行证据记为 PARTIAL，不记为完成

---

## 1. 原始设计要求（摘要）

### 1.1 产品闭环（六步）

需求理解 → 自主探索与生成 → 确定性执行 → 证据采集与关联 → 故障诊断与处置 → 安全修复与回归；
修复后回到执行（新版本资产）并向需求侧反馈覆盖。

### 1.2 四项研究特色（必须能被评测）

1. TestCase Compiler：Trace → 稳定测试资产
2. Evidence Correlation Graph：多源关联是否改善解释
3. Active Diagnosis Planner：主动探针是否提高效率
4. Oracle-Protected Self-Healing：能否修资产而不削弱断言

### 1.3 十四阶段与六个里程碑

M0（0–1）工程与 TeamFlow → M1（2–3）执行平台 → M2（4–6）证据 + Agentic → M3（7–9）图与主动诊断 →
M4（10）安全自愈 → M5（11–13）Benchmark、泛化、交付。

### 1.4 已被冻结、不可协商的规则

| 规则 | 出处 |
|---|---|
| Test Verdict 聚合 8 条 + 四态；NOT_EVALUATED ≠ INCONCLUSIVE；execution_status 独立 | 路线图阶段 2 |
| 必需 Assertion 的 Oracle 不可用不得判 PASSED；空必需断言集非法 | 阶段 2 |
| Event：workspace 服务端注入、producer_instance 内序号、幂等键 producer 维唯一、同键异载荷拒绝、只追加 | 阶段 4 |
| Consumer Inbox：`(consumer_id,event_id)` 唯一、本地副作用同事务、外部副作用不确定不重试 | 阶段 4 |
| Probe：五项校验、模板/表白名单、脱敏、行数限制、审计；禁任意 SQL | 阶段 9 |
| Repair：不动 Oracle/断言、沙箱真跑、人工审批、CAS 原子晋升、VERSION_CONFLICT 不得直接重试 | 阶段 10 |
| CI：PR / Nightly / Release 三级；Nightly 关键失败阻断 Release；旧 Commit 不冒充新 RC | 阶段 13 + 验收清单 F |
| Agent 是能力层不是事实层；LLM 不得 CONFIRMED / 批 Oracle / 批 Repair | 全书 + 验收清单 E |

---

## 2. 当前实现对照（阶段 0–13）

| 阶段 | 状态 | 已有证据 | 主要缺口 |
|---|---|---|---|
| 0 工程初始化 | **PARTIAL** | 目录骨架、配置/日志/错误、契约 C01–C12 + schema 导出、CI 三工作流、`.env` 不入库 | **PostgreSQL 非默认且未本地验证**；**无基础身份认证**；Docker compose 未运行验证；Redis/Celery/MinIO 声明但未使用 |
| 1 TeamFlow + 故障注入 | **VERIFIED** | 30 场景、10 类机制、控制 API、状态查询、GT 隔离（evaluator token） | 无"浏览器环境不可用"场景；数据重置为按场景种子，无全库一键重置 |
| 2 执行内核 | **PARTIAL** | Playwright 真跑、四态判定、Oracle 单引用、证据采集、Verdict 测试 | **未集成 pytest**（Discovery/Fixture/Param/Hooks）；Oracle 缺 Primary/Supporting 结构；视频配置存在但未产出视频证据 |
| 3 执行编排 | **PARTIAL→VERIFIED（核心）** | Task/Run/Step、租约 + fencing、取消/超时/重试、真实 SSE、工作台 | 用 **DB 队列 + 线程 Worker** 替代 Redis/Celery（设计明示 Celery Worker），未决策；死信队列未实现 |
| 4 Event/Evidence | **PARTIAL** | 序号原子分配、幂等/冲突审计、Inbox 状态机与事务、证据不可变 + SHA-256 + 脱敏 | **无 Outbox / 生产者高水位**；缺口检测无"确认窗口"；**无保留/过期/清理**（P07）；系统级事件边界未落地 |
| 5 Explorer | **PARTIAL** | 自研 Explorer 真实探索、ToolGateway、Trace 字段齐全、预算 | 未用 Browser Use / LangGraph（P02 待决策）；页面理解深度有限（无结构化解构/候选断言输出） |
| 6 Compiler | **PARTIAL** | 规则编译 + LLM 三模式 + 来源/hash/版本记录 + 稳定性回放服务 | **Oracle Binder 缺失**（恒 None）；**Static Validation 阶段无独立实现**；**compile → 回放 → 审核未编排成流水线**；Trace Normalizer 简化 |
| 7 Diagnosis v1 | **VERIFIED** | 八维分离、多假设、支持/反驳、Flaky 特征、CONFIRMED 规则、Disposition 路由 | 确认门槛按类别的参数未冻结（P04）；LLM 候选未参与排序 |
| 8 Evidence Graph | **PARTIAL** | 节点/边类型齐全（11 类）、不伪造因果、API + 前端可视化 | **无 ProbeExecution 节点/边**；Hypothesis 更新后图不重建；无 temporal 窗口参数化 |
| 9 Active Diagnosis | **PARTIAL（关键缺口）** | 五项校验、探针注册、EIG、预算隔离与持久化、真实执行闭环 | **探针证据未回灌 Hypothesis / 未重跑诊断**（流程 J 步缺失）；Probe Registry 字段不全；缺 screenshot/console/db 探针；**无"诊断前后增益"实测** |
| 10 Self-Healing | **PARTIAL** | 状态机、Diff 白名单、断言/Oracle 保护、CAS 晋升、回滚、沙箱真跑、LLM 候选 | **无 RegressionCampaign**（06 §8 状态机未实现）；回归率/接受率无实测；审计仅文档字段无独立表 |
| 11 UI-FaultBench | **PARTIAL** | 30 场景 GT 隔离、A/C/D/E + LLM 组代码、指标定义、Wilson 支持 | 实验上限 15/30；**E 组预测未使用探针结果**；缺 Time to Diagnosis、Repair Regression Rate；**消融非真实关模块**；数据集 split 未打标（Agent Generated 集为空） |
| 12 外部泛化/安全/可靠性 | **BLOCKED** | 安全 13 项复查、npm audit 记录、SSE/Inbox/并发修复 | **无授权外部应用**（P10）；60 分钟长稳未跑；多 Worker 未复验 |
| 13 最终验收 | **PARTIAL** | 10 项场景中 01–06、08 有真实证据；UI 30/30；三级 CI 文件 | **07 浏览器不可用、09 探针越权、10 修复全链**缺场景化证据；发布包/部署验证未做 |

---

## 3. 已经真正达标、可以对外讲的部分

1. **可信判定**：四态 + 8 条聚合规则 + Oracle 缺失不判 PASSED，有单测与真实 False Pass 场景（TF-DB-001）。
2. **执行对象边界**：Run 不覆盖、重试新 Run、fencing 拒绝旧代次（历史证据 + 本轮回归）。
3. **事件可靠性**：多进程序号唯一、幂等返回同一事件、同键异载荷拒绝并审计、跳号不判丢失。
4. **消费可靠性**：Inbox 六条验收（重复、多消费者、崩溃恢复、事务失败、外部不确定、可追溯）本轮全部有测试。
5. **探针安全边界**：五项校验、白名单、脱敏、行数限制、审计、预算隔离（本轮修复）。
6. **诊断不越权**：多假设、支持/反驳、LLM 只加候选、CONFIRMED 只由规则 + 权威证据产生。
7. **修复不削弱测试**：Diff 白名单、断言保持、Oracle 完整性、CAS、回滚（历史 + 回归）。
8. **实时通道**：真实 SSE + 前端降级 + at-least-once 去重，不再伪保活。

---

## 4. 需要完善（有基础但不达标）

### P0（不补就等于核心闭环断了）

| # | 问题 | 现状 | 建议动作 | 验收方式 |
|---|---|---|---|---|
| P0-1 | **探针证据不回灌诊断** | `plan_and_probe()` 产出证据即结束；`diagnose()` 不消费 probe 证据；Benchmark E 组预测与 D 组相同 | 新增 `HypothesisUpdater`：把 probe 证据按 gap→hypothesis 映射写入 `supporting/contradicting`，再调用诊断引擎重算 `outcome`；`probe_plan` 记录 `diagnosis_before/after` | 同一 Run：探针前 INCONCLUSIVE → 探针后 CONFIRMED 或仍 INCONCLUSIVE（有明确理由）；E 组预测必须在有探针时改变 |
| P0-2 | **Evidence Graph 缺探针节点** | `graph_service` 只建 Run/Step/Event/Evidence/Assertion/Hypothesis | 增加 `ProbeExecution` 节点 + `evaluates/supports` 边；探针证据标 `source_trust=TRUSTED` | 图查询能看到 probe 节点与它支撑的 hypothesis；边有建立依据 |
| P0-3 | **Benchmark 不完整** | 上限 15 场景；E 组名不副实；缺 Time to Diagnosis / Repair Regression Rate；消融靠比较预测字典 | 上限提到 30；E 组先跑 probe-plan 再诊断；补 `time_to_diagnosis_ms` 埋点；消融改为真实开关（关图/关探针/关预算/关断言保护） | 一次 30 场景 × A/C/D/E 完整跑；报告含分子分母与 CI；消融有真实差异 |

### P1（影响"设计一致性/可信度"）

| # | 问题 | 建议动作 |
|---|---|---|
| P1-1 | Oracle 单引用，缺 Primary/Supporting | 契约先扩展 `primary_oracle_id` + `supporting_oracle_ids`（结构扩展不等于 P03 裁决），默认行为不变 |
| P1-2 | Probe Registry 字段/类型不全 | 补 `input_schema/output_schema/target_scope/side_effects/retry_policy/cleanup_policy`；新增 screenshot / console_read / 受控 database_read 三类探针 |
| P1-3 | 编译链路未成流水线 | 把 `compile → static validation（locator/assertion schema）→ stability replay → human review` 串成一个编排；补 Oracle Binder（只提案不批准） |
| P1-4 | 未集成 pytest | 提供"生成 pytest 模块 + 本地运行"的导出通道，或明确决策"不采用 pytest，以自研 runner 为准"并记录架构偏差 |
| P1-5 | Redis/Celery 未使用 | 做出架构决策：单机 DB 队列（现状）还是 Celery；若维持现状，从 compose/文档中移除"已使用"暗示 |
| P1-6 | 无认证/RBAC | 最小本地认证 + 角色字段（author/qa-lead/platform-admin），先拦截 Repair 审批与晋升 |
| P1-7 | PostgreSQL 未验证 | 在 CI Nightly 实跑 parity（已配置），并把 `TG_DATABASE_URL` 作为生产默认文档化 |
| P1-8 | 无 Outbox/高水位/缺口窗口 | 增加 producer checkpoint 表 + 缺口确认窗口参数；先内部派生状态，不扩展 C05 契约 |
| P1-9 | 无保留/清理（P07） | 只做"保留策略预览 + Dry Run + 归档评估"，不自动删除 |

### P2（质量与证据补强）

- 60 分钟长稳 + 4 Worker 故障恢复重跑（照 `RUN-005-SECURITY-RELIABILITY.md` 第 3 节清单）。
- 07/09/10 三个最终场景补场景化证据（浏览器不可用、探针越权、修复全链）。
- 视频证据接入（录屏 → artifact → 证据链）。
- 独立审计表（Repair 审批/晋升/回滚）。

---

## 5. 需要补充（当前完全空白）

1. **RegressionCampaign**：06 文档定义了状态机（PLANNED → RUNNING → PASSED/FAILED → ARCHIVED），代码零实现；修复后的"回归"目前是单次 sandbox run + 人工判断。
2. **Agent Generated Test Set**：08 要求 Trusted 与 Agent 生成集分离；当前 `BenchmarkCase.split` 字段存在但 30 场景未打标，生成集为空。
3. **外部应用泛化**（阶段 12，P10）：未选应用、未做授权，全部 BLOCKED。
4. **基础身份认证 + 角色矩阵**：阶段 0 验收要求"基础身份认证"，当前无登录、无 token、无权限校验（只有"审批人不能是 agent"这一条字符串检查）。
5. **部署验证**：compose 有 PG/Redis/MinIO，但从未启动验证；"新环境按文档复现"未做过。
6. **人工基线**（Benchmark）：无真实参与者，持续 BLOCKED，不得用 0 分替代。

---

## 6. 明确**不要**补的（资料已排除，补了是浪费）

- 自动修改被测产品业务代码 / 自动开 PR
- 无审核修改正式 TestCase / Oracle
- 无约束 Agent Swarm、全面微服务化
- 强制图数据库（P12 未证明瓶颈）
- 完整 SaaS 多租户、原生移动 App 自动化
- 用 Redis/Celery/MinIO "丰富技术栈"而无使用场景

---

## 7. 建议的下一轮顺序（按"闭环完整度"而非工作量）

1. **P0-1 + P0-2**：探针证据回灌诊断 + 图上探针节点 —— 这一步做完，"主动诊断提高效率"才第一次成立。
2. **P0-3**：30 场景 × A/C/D/E（先无 LLM，分两批跑）+ Time to Diagnosis 埋点。
3. **P1-1 + P1-2**：Oracle 结构 + 探针注册表补齐（为后续 P03/P06 决策铺路）。
4. **P1-3**：编译流水线串起来（含 Oracle Binder 提案、静态校验、回放门槛）。
5. **P1-5 + P1-6 + P1-7**：三个架构/工程决策（队列、认证、PG）——需要负责人拍板，先出决策包。
6. **P2 长稳与 07/09/10 场景**：证据补齐后再做阶段 13 的最终验收。
