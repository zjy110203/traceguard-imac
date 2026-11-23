# RUN-005 决策包（Decision Package）

> 状态：**NEEDS_DECISION**。本文件只汇总候选方案、影响与阻塞关系，**不冻结任何决策**。
> 未获批准前，Oracle 冲突契约与正式 Repair 权限模型保持现状，未做任何改动。

## 1. 本轮必须由负责人拍板的两项

### P03 Oracle 多来源冲突裁决

- 详细分析：`docs/decisions/P03-oracle-arbitration.md`（策略 A/B/C/D 对比、冲突样例、测试设计）
- **推荐：策略 D — 域内权威规则 + 跨域冲突升级人工**
  - 域内：`business_state` 只认独立状态查询；`ui` 只认浏览器观测；`api` 只认接口响应（同域不跨源投票）。
  - 跨域冲突（如 UI 成功 vs 业务 count=0）→ `INCONCLUSIVE` + `MANUAL_REVIEW`，不自动判 PASS/FAIL。
  - 与现有实现最接近（策略 A 的超集），可增量落地，且不引入未校准权重。
- 影响面：`packages/evidence/diagnosis/engine.py` 的确认规则、断言评估 `verdict.py`、`docs/契约治理规范.md`。
- 未批准前的现状：多来源冲突仍按当前规则产生 `INCONCLUSIVE`，不改变。
- 需要测试：TF-DB-001 / TF-UI-001 / TF-API-001 三个真实冲突场景 + 新增跨域冲突单测（已有 3 例基线）。

### P08 Repair 审批角色与职责分离

- 详细分析：`docs/decisions/P08-repair-approval-roles.md`（角色、方案 1/2/3、审计缺口）
- **推荐：方案 2 — 职责分离 + 高风险双人审批**
  - `author` 提案/沙箱；`qa-lead` 审批；`platform-admin` 晋升/回滚；`agent:*` 只能生成候选。
  - 高风险（修改定位器以外、影响多步骤、涉及业务断言邻近字段）需两名不同角色审批。
- 影响面：`apps/api/.../repair_service.py` 的 `approve/promote` 校验、`docs/契约治理规范.md`、前端权限提示。
- 未批准前的现状：仍只校验"审批人不是 Agent"，不具备 RBAC；本轮未改。
- 与 LLM 修复的关系：LLM 只能生成候选，审批链不变（本轮已强制 `created_by=agent:llm-repair-planner`）。

## 2. 其他待确认事项状态

| 编号 | 事项 | 本轮状态 | 本轮动作 | 阻塞的工作 |
|---|---|---|---|---|
| P02 | Browser Use / Playwright 集成选择 | NEEDS_DECISION | 未做 PoC；现有自研 Explorer 继续使用 | 无（不阻塞本轮） |
| P04 | Diagnosis 确认规则 | NEEDS_DECISION | 未改确认规则；仅新增 LLM 候选（不升级） | 高精度诊断口径 |
| P05 | EIG 权重与归一化 | NEEDS_DECISION | 权重仍为启发式默认，`ScoreWeights.note` 标注；LLM 对 EIG 的上调被限制 ≤ +0.15 | Benchmark 多臂结论 |
| P06 | Probe 预算与风险等级 | NEEDS_DECISION | 实现 50 次/100 成本/60s，全部 `provisional=true`；按 Run/Diagnosis 隔离 | 预算正式冻结 |
| P07 | Event / Evidence 生命周期 | NEEDS_DECISION | 未实现自动删除；无归档执行 | 存储增长治理 |
| P09 | Benchmark 统计方案 | NEEDS_DECISION | 指标定义已存在；未跑多臂（见 Benchmark 报告） | 统计显著性结论 |
| P10 | 外部应用选择 | BLOCKED | 未选择任何真实外部站点 | 真实外部应用验收 |
| P11 | 并发 / SLO / 资源预算 | NEEDS_DECISION | 未压测，不编造 SLO | 性能门禁 |
| P12 | 专用图数据库必要性 | NEEDS_DECISION | 继续 SQL 图存储 | 无（当前无明确瓶颈） |

## 3. 本轮因待决策而**主动未做**的事

1. 未修改 Oracle 冲突契约（P03 未批）。
2. 未收紧/放宽正式 Repair 权限模型（P08 未批）。
3. 未把 provisional 预算数值写成"正式冻结"。
4. 未引入 Redis/Celery/MinIO/图数据库（无明确收益证据）。
5. 未自动删除任何历史数据（P07 未批）。
6. 未对接真实外部网站（P10 阻塞）。

## 4. 决策后可直接执行的落地清单

- P03 批准策略 D：新增 `cross_domain_conflict → INCONCLUSIVE/MANUAL_REVIEW` 规则 + 3 个冲突场景回归；
  更新 `契约治理规范.md` 与 `EVIDENCE-AND-DIAGNOSIS.md`。
- P08 批准方案 2：在 `repair_service.approve/promote` 增加角色校验与双人审批字段，
  增加"同一人不得既提案又审批"的拒绝测试与审计表。
- P06 批准预算数值：把 `DEFAULT_PROBE_BUDGET` 改为正式值并去掉 `provisional` 标记，
  同步修改预算测试期望。
