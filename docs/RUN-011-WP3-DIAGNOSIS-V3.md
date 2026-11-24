# RUN-011 WP3：Diagnosis v3 与 Evidence Graph 深化（PARTIAL）

> 执行时间：2026-11-25 01:40–02:10 CST。
> 状态：**PARTIAL** —— 修复了 2 个机制性缺陷并拿到真实证据，但"根因判断质量"没有达到
> v3 目标，5 类历史问题中有 3 类仍可复现。本轮不宣称诊断能力已提升。

## 1. RUN-010 五类问题的当前状态

| # | RUN-010 问题 | 本轮状态 | 证据 |
|---|---|---|---|
| 1 | 相似故障中 LOCATOR 与 NETWORK 混淆 | **机制已修，结论仍混淆** | 修复后 DOM 探针能给出 `SUPPORT LOCATOR`；但最终 outcome 仍 PROBABLE（POST_HOC 不能排除 NETWORK），未产生唯一根因 |
| 2 | 定位器波动缺乏机制特异性证据 | **已修** | `dom_snapshot` 依据"失败步骤目标不在 DOM"给出机制特异性证据（见 WP2 §2.3） |
| 3 | 时序故障缺少延迟复测 | **未实现** | `TF-DB-004` 仍被判 `CONFIRMED / PERSISTENCE`（GT=TIMING），无延迟复测探针 |
| 4 | 纯导航超时缺少失败证据落盘 | **PARTIAL** | 报告已包含每步 `target/value/observation`；但导航超时场景未单独构造真实实验 |
| 5 | Flaky 不能由单次结果定持久化根因 | **未改** | 规则仍用单 Run 证据；跨 Run 统计未接入诊断 |

## 2. 本轮修复（有回归测试）

### 2.1 LOCATOR vs UI_RENDERING 的机制特异性判定

问题：`dom_snapshot` 的判定只看 `assert` 步骤的期望 test_id；定位器在 click 阶段失败时
断言步骤根本没执行 → 期望集合为空 → 探针 `skipped`，不产生任何机制证据。

修复：
- 报告自包含 `step.target` / `assertion.expected`（`pipeline.py`）；
- `_targets_by_origin()` 拆分 `failed_step_targets` 与 `assertion_targets`；
- 失败步骤目标缺失 → `SUPPORT LOCATOR`，且不把"缺失"解释成渲染缺陷；
  断言元素存在 → `CONTRADICT UI_RENDERING`。

真实效果（WP2 实验）：
```
dom_snapshot SUCCEEDED
  SUPPORT    LOCATOR        "失败步骤的目标元素 ['create-submit'] 不在 DOM 中…"
  CONTRADICT UI_RENDERING   "DOM 已渲染期望元素，'未渲染'假设不成立"
```
回归测试：`test_dom_probe_uses_failed_step_target_when_assert_never_ran`。

### 2.2 业务状态缺口补齐

定位器失败时 `business_state` 断言只会是 `NOT_EVALUATED`，但旧逻辑不补 `http_state` 缺口，
"是否真正落库"这一 LOCATOR/PERSISTENCE 的关键判别证据缺失。
现在：无有效业务判定（PASSED/FAILED）时补 `http_state`（EIG 0.7）。
真实效果：本轮 plan 第 2 步执行 `http_state`，返回 `count=0`，反驳 UI_RENDERING。

## 3. 真实实验里暴露的**未修**问题

### 3.1 时序故障的错误确认（可复现）

场景：`tcase_bench_db004` + `TF-DB-004`（状态查询滞后 9000ms）。
连续多次真实执行，诊断均为：

```
outcome = CONFIRMED
disposition = PRODUCT_DEFECT
mechanism = PERSISTENCE
GT（评测端）= TIMING
```

原因：Run 作用域内 `business_state count=0` 是 TRUSTED 直接确认证据，规则据此 CONFIRMED；
而"延迟后数据最终落库"需要**延迟复测**才能观察到，当前没有该探针/规则。
这是 WP3 §3.4 要求的能力，**本轮未实现**，因此不宣称修好。

### 3.2 网络超时场景的结论不稳定

同一场景（`tcase_bench_net002` + `TF-NET-002`）两次真实执行：

| 次 | outcome | 备注 |
|---|---|---|
| 第 1 次 | `INCONCLUSIVE` | 无充分证据，弃权 |
| 第 2 次 | `PROBABLE` | 有方向性倾向 |

差异来自 LLM advisor 产生的候选假设（`llm:deepseek-flash`）进入假设集合，
而规则集合本身相同。**LLM 候选会让"概率性结论"在不同运行间漂移**，
这对 Benchmark 的可重复性是风险（WP4 必须固定 LLM 开关或分组统计）。

### 3.3 LLM 候选假设未去重

本轮多次真实诊断（同一场景）观察到 **2–5 个假设**，差异全部来自 LLM advisor：

| 来源 | 机制 | 某次运行的数量 |
|---|---|---|
| rule | NETWORK | 1 |
| rule | LOCATOR | 1 |
| llm:deepseek-flash | UI_RENDERING | 2（近似重复） |
| llm:deepseek-flash | LOCATOR | 1 |

（同场景另一次运行为 2 个假设，无 LLM 候选——**LLM 候选是否出现本身就不稳定**。）

影响：
- "独立来源支持数"被稀释（同一 evidence 同时支持多条同机制假设）；
- 前端/报告可读性下降；
- 不改变 CONFIRMED（LLM 永远不能确认），但会改变 PROBABLE 的排序。

处置：**未修**。需要"按机制 + 语义相似度合并候选"，属 WP3 §3.2 的假设竞争设计，
不能在缺少评测的情况下贸然改动。

### 3.4 派生证据的独立性计算

`GraphAdjudicator` 已实现 `duplicate_evidence` 消重（RUN-010），本轮未新增验证；
Benchmark v4 中 `conflict_detection_count=0/14` 的问题仍在。

## 4. 结论语义（保持不变）

| 结论 | 产生条件（现状） |
|---|---|
| `CONFIRMED` | 仅确定性规则 + TRUSTED 且 directly_confirming 证据；LLM 假设会被降级 |
| `PROBABLE` | 有方向性支持但缺少权威确认 |
| `INCONCLUSIVE` | 证据不足或冲突未裁决（允许弃权） |
| `REFUTED/CONTRADICTED` | 有 TRUSTED 反驳（POST_HOC 不触发） |

本轮没有放宽任何一条；WP2 的探针回灌全部以 POST_HOC 附着，未产生新的 CONFIRMED。

## 5. 结论

- **已交付**：机制特异性证据（LOCATOR vs UI_RENDERING）在真实链路上生效，并有回归测试。
- **未交付**：时序诊断（延迟复测）、Flaky 跨 Run 判断、假设去重、冲突检测率。
- **诚实评估**：诊断 v3 的"质量提升"没有达到可用证据支持的程度；本轮只完成了
  让证据"能到达判定层"的基础修复。后续应先做 WP4 的有效实验，再决定是否调整规则权重。
