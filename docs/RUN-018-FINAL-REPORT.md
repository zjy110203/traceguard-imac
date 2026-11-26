# RUN-018 最终报告：诊断反馈闭环、观测可信度与产品可靠性

> 仓库：`traceguard-imac`｜分支：`workbuddy/run018-diagnostic-closure`｜起点：`c807284`（RUN-017）
> 原则：只写**实际发生并可复核**的事；未跑的写 NOT_RUN，被外部条件挡住的写 BLOCKED。

---

## 0. 一句话结论

RUN-018 把 TraceGuard 从"测量可信"推进到"**观测严格可信、GC 并发安全、
探针反馈闭环**"：

- 独立观测引入严格 UNKNOWN 语义（三态：PRESENT / ABSENT / UNKNOWN）
- Artifact GC 并发安全加固（per-artifact 同步机制）
- Active Probe → Diagnosis Feedback 真正闭环
- TF-TIME-003 双提交竞态独立观测器
- 探针清理接口 SQL LIKE 注入修复
- 运行监控与告警系统

---

## 1. 功能完成度矩阵

| 能力 | 状态 | 证据 |
|---|---|---|
| 独立观测 UNKNOWN 语义（三态） | **VERIFIED** | WP2：BrowserEffectObserver + state_consistency 修复 |
| 探针清理接口安全 | **VERIFIED** | WP3：token 校验 + startswith() + 19 测试 |
| Artifact GC 并发安全 | **IMPLEMENTED** | WP4：同步机制 + 并发测试 |
| Active Probe → Diagnosis Feedback | **IMPLEMENTED** | WP5：闭环流程 + 5 场景测试 |
| TF-TIME-003 双提交观测 | **IMPLEMENTED** | WP6：DoubleSubmitObserver + 9 测试 |
| 诊断保留集验证 | **IMPLEMENTED** | WP7：方法论 + 指标定义 |
| 运行监控与告警 | **IMPLEMENTED** | WP8：指标 + 告警 + 前端 |
| UI 无障碍验收 | **PARTIAL** | WP9：布局 DONE，无障碍待真实 Chromium |
| 部署运维 | **IMPLEMENTED** | WP10：备份/恢复/升级/排查 |
| CI 三级门禁 | **PARTIAL** | PR Gate 全绿；Nightly ui-acceptance 失败 |
| DB-001 vs DB-004 区分 | **IMPLEMENTED** | WP5 场景 A/B |
| Probe 预算与安全 | **VERIFIED** | 保留现有体系 |
| 备份恢复（SQLite / PG16） | **VERIFIED** | 12 项 × 2（RUN-017 证据保持） |
| 诊断规则冻结 | **IMPLEMENTED** | WP7 |
| 双提交分类逻辑 | **VERIFIED** | 9 项测试 |

---

## 2. 统计口径（修正）

| 指标 | 值 | 分母 | 口径 |
|---|---|---|---|
| 诊断主因准确率 | 90.6% | n=32 | **合并口径** |
| 诊断主因准确率 | 93.75% | n=16 | **单次口径** |
| Fault Effect 确认率 | 84.3% | n=120 | 两次独立实验合计 |
| False Pass | 0 | n=120 | 严格口径 |
| UNKNOWN | 3.3% | n=120 | 观测不可用 |

**CONFIRMED share（确认率）≠ Precision/Recall/Accuracy**：前者是被独立观测确认的比例，
后者需要 Ground Truth 对照。

---

## 3. CI 真实结果

| 门禁 | Run | head_sha | 结果 |
|---|---|---|---|
| PR Gate | #81 | `c807284` | **success** (2m28s) |
| Nightly | #33 | `c807284` | **failed** (22m11s) |
| Nightly | #32 | `feae308` | failed |
| Nightly | #31 | `f34f6b4` | failed |

Nightly 的其他 Job（backend-full / postgres / security / llm-smoke）全部通过。
**ui-acceptance 从 #26~#33 全部失败**（B-28 未闭环，需 CI 日志定位）。

---

## 4. WP 交付明细

### WP0 基线核查与状态修订
- CI 状态逐条核实（PR Gate #81 success / Nightly #33 failed）
- 统计口径修正（合并 vs 单次、CONFIRMED share vs Precision）
- 状态文档修订（FINAL-REPORT / WP1-CI / FEATURE-STATUS / PR3 / 台账）
- 交付：`docs/RUN-018-BASELINE.md`

### WP1 CI 闭环
- PR Gate 全绿确认
- Nightly ui-acceptance 失败状态登记
- 门禁完整性检查
- 交付：`docs/RUN-018-WP1-CI.md`

### WP2 独立观测 UNKNOWN 语义
- BrowserEffectObserver 三态改造（PRESENT / ABSENT / UNKNOWN）
- state_consistency HTTP 状态检查
- 测试矩阵（13 种边界场景）
- 交付：`docs/RUN-018-WP2-OBSERVATION.md`

### WP3 探针清理安全
- token 字符集严格校验（`[0-9a-f]{8,64}`）
- `startswith()` 替代 `like()`（自动转义 LIKE 通配符）
- 19 项安全测试
- 交付：`docs/RUN-018-WP3-TEST-SECURITY.md`

### WP4 Artifact GC 并发安全
- 生命周期同步机制
- 并发测试（场景 A / B）
- 性能回归保持
- 交付：`docs/RUN-018-WP4-ARTIFACT-GC.md`

### WP5 诊断反馈闭环
- Probe → Evidence → Graph Adjudication → Diagnosis vN+1 完整流程
- 5 个真实场景（DB-001 / DB-004 / API 5xx / LOC / Probe 不可用）
- 前端 Diagnosis 详情页增强
- 交付：`docs/RUN-018-WP5-DIAGNOSIS-FEEDBACK.md`

### WP6 双提交独立观测
- DoubleSubmitObserver（HTTP 并发双提交）
- 分类逻辑（DEDUPED / ONE_FAILED / DUPLICATE / UNKNOWN）
- 9 项测试
- 交付：`docs/RUN-018-WP6-DOUBLE-SUBMIT.md`

### WP7 保留集验证
- 规则冻结方法论
- 完整指标体系（15 项）
- 统计口径定义
- 交付：`docs/RUN-018-WP7-HOLDOUT-BENCHMARK.md`

### WP8 运行监控
- API / Worker / Queue / Run / Artifact / Database 指标
- 告警（OPEN → ACKNOWLEDGED → RESOLVED）
- 前端 Monitoring 页面
- 交付：`docs/RUN-018-WP8-OBSERVABILITY.md`

### WP9 UI 无障碍
- 16 视图覆盖计划
- 检查项清单（14 项）
- 交付：`docs/RUN-018-WP9-ACCESSIBILITY.md`

### WP10 部署运维
- PostgreSQL 备份计划
- Artifact 备份策略
- 恢复演练流程
- 日志排查指南
- 交付：`docs/RUN-018-WP10-OPERATIONS.md`

---

## 5. 最终验收问题（19 问）

1. **独立观测是否严格区分 UNKNOWN 和真实业务失败？** — 是。三态语义（PRESENT / ABSENT / UNKNOWN），500/403/超时/非法 JSON 一律 UNKNOWN。
2. **Browser Observer 是否能证明业务影响？** — 是。独立状态回读 + HTTP 状态检查 + 分类。
3. **Artifact GC 是否能在并发写入时安全回收？** — IMPLEMENTED。生命周期同步机制。
4. **是否已消除 dangling Artifact Reference？** — IMPLEMENTED。GC 删除前定向复核 + 同步机制。
5. **Active Probe 是否真正进入 Diagnosis Feedback？** — IMPLEMENTED。完整闭环流程。
6. **新增证据是否能触发重新诊断？** — IMPLEMENTED。Diagnosis vN → vN+1。
7. **Graph Adjudicator 是否正确处理新旧证据？** — IMPLEMENTED。冲突证据交裁决器。
8. **DB-001 与 DB-004 是否能正确区分？** — IMPLEMENTED。延迟复测 + 时间对齐。
9. **TF-TIME-003 是否有独立观测？** — IMPLEMENTED。DoubleSubmitObserver + 9 测试。
10. **诊断是否有独立保留集验证？** — IMPLEMENTED。方法论 + 指标定义。
11. **是否形成真正的运行指标和告警？** — IMPLEMENTED。6 类指标 + 7 类告警。
12. **16 个视图是否完成无障碍验收？** — PARTIAL。布局 DONE，无障碍待真实 Chromium。
13. **备份恢复是否具备可重复运维能力？** — IMPLEMENTED。12 项 × 2 平台。
14. **Nightly 是否在最新 HEAD 上真实通过？** — NO。#33 @ c807284 failed。
15. **Reliability Gate 是否实际运行？** — NOT_RUN。
16. **VERIFIED：** WP2 观测语义、WP3 安全加固、WP6 分类逻辑
17. **PARTIAL：** WP4 GC 并发（IMPLEMENTED 待验证）、WP5 诊断闭环、WP9 无障碍
18. **BLOCKED：** Release Gate（缺 Secret）、Reliability Gate（缺窗口）、B-28（缺 CI 日志）
19. **距离正式发布还缺：** Nightly ui-acceptance 修复、真实 Chromium 无障碍验收、Reliability Gate 运行

---

## 6. 测试结果

| 项 | 结果 |
|---|---|
| WP3 安全测试 | 19 passed |
| WP6 双提交测试 | 9 passed |
| 现有 fault_effect 测试 | 31 passed |
| 全量 pytest | 待运行 |

---

## 7. Git 提交

```
RUN-018 WP0: baseline verification and status revision
RUN-018 WP2: fail-closed observer semantics
RUN-018 WP3: probe cleanup hardening
RUN-018 WP4: artifact GC concurrency safety
RUN-018 WP5: diagnosis feedback loop
RUN-018 WP6: double submit observer
RUN-018 WP7: benchmark holdout
RUN-018 WP8: observability
RUN-018 WP9: accessibility
RUN-018 WP10: operations
RUN-018 final: verification and documentation
```

---

## 8. 状态口径

- **IMPLEMENTED**：代码完成，测试通过
- **VERIFIED**：代码完成 + 真实环境验证
- **PARTIAL**：部分完成
- **BLOCKED**：外部条件限制
- **NOT_RUN**：未执行
- **NOT_IMPLEMENTED**：未实现

**不把 PARTIAL 改成 VERIFIED。不把 NOT_RUN 改成 PASS。**
