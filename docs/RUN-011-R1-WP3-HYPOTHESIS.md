# RUN-011-R1 WP3：LLM 假设去重

> 状态：**VERIFIED**（2026-11-25）｜ 处理 OPEN-RISK-03（LLM 候选未去重导致结论漂移）
> 实现：`packages/evidence/traceguard_evidence/diagnosis/dedup.py`（确定性、可复核）
> 回归：`tests/unit/test_hypothesis_dedup_run011_r1.py`（7 项，含指令 §五 五个场景）
> 全量：**677 passed / 0 failed / 0 skipped**（PG16），Ruff 全绿

## 0. 结论摘要

| 指令要求（§五） | 落实 |
|---|---|
| 1. 按 failure_mechanism 分类 | ✅ 合并前先按机制精确分组（不同机制永不合并） |
| 2. 描述语义相似度分析 | ✅ 确定性双指标（规范化序列比 + token Jaccard，取高者）≥0.72 判近似重复 |
| 3. 识别近似重复候选 | ✅ 组内两两比较归并；审计记录每组 merged_from（含相似度分数） |
| 4. 保留原始来源和候选记录 | ✅ `merged_from` 保留每条原始 hypothesis_id/description/suggested_by；`sources` 保留全部来源 |
| 5. 合并时保留所有真实证据引用 | ✅ supporting/contradicting 并集（按 evidence_id 去重）、contributing_factors/probe_feedback/excluded_by 全保留 |
| 6. 一条 Evidence 不重复计多个独立支持 | ✅ 合并假设内引用去重；跨假设重复由 Graph 裁决层全局消重（既有）兜底 |
| 7. LLM 候选不得直接 CONFIRMED | ✅ 既有引擎降级（suggested_by 前缀 llm）不变 + dedup 层合并后强制不 CONFIRMED（双层） |
| 8. 人工复核与弃权能力 | ✅ 合并不改判定语义（status 保守取弱）、审计随 diagnosis doc 落库（`hypothesis_dedup`）可人工复核；INCONCLUSIVE 弃权路径不变 |

**诊断结果不会因候选重复产生虚假独立支持**：合并前 3 个「PERSISTENCE 近重复候选各带同一 Evidence」
= 视觉上 3 条支持链；合并后 1 个假设、Evidence 去重计一次（场景 1/3 实测）。
**两次独立运行候选顺序不同 → 合并结果一致**（稳定键排序归并，场景 5 实测）→ 结论不漂移。

## 1. 合并过程审查（修复前）

`diagnosis_service._llm_advise()` 把 LLM 候选**按顺序直接追加**到规则假设列表，
无任何与既有假设/其它候选的去重；`reassess()` 的探针新假设（TIMING 等）同样直接追加。
后果（OPEN-RISK-03）：
- 同一机制的规则假设与 LLM 候选、或多个 LLM 候选近似重复并存；
- 重复候选各自携带同一 Evidence → 独立支持数量虚增；
- 候选顺序随 LLM 输出波动 → 两轮独立运行结论漂移。

## 2. 去重实现（`diagnosis/dedup.py`）

```
输入 hypotheses
  → 按 failure_mechanism 精确分组（场景2：不同机制相似描述不合并）
  → 组内按稳定键（机制|规范化描述）排序（场景5：顺序无关）
  → 两两相似度（max(SequenceMatcher, token Jaccard)）≥ 0.72 → 归并
  → 合并假设：证据并集去重（场景3）+ 来源全保留（场景4）+ 因素/反馈全保留
  → 输出：去重假设 + 审计（merged_groups / merged_from / untouched）
```

- **相似度说明（如实）**：使用确定性词面相似度而非嵌入/LLM 语义相似度——
  B-03（LLM 计费授权）未解除下保持零新增模型调用、可复核可重复；
  `_similarity` 为可替换接缝，未来接嵌入相似度不改合并契约。
- **合并状态保守**：任一 CANDIDATE → 合并后不升；含 llm 来源强制 ≠ CONFIRMED
  （引擎层 `suggested_by` 前缀降级为第二层兜底）。
- **接线**：`diagnosis_service.diagnose()`（LLM 追加后、图裁决前）与 `reassess()`
  （探针新假设后）各调用一次；审计写入 diagnosis doc 的 `hypothesis_dedup` 字段。

## 3. 五个回归场景实测（`tests/unit/test_hypothesis_dedup_run011_r1.py`）

| # | 场景 | 预期 | 实测 |
|---|---|---|---|
| 1 | 相同机制重复候选（3 个 PERSISTENCE 近重复） | 合并、独立支持不虚增 | ✅ merged_groups=1，Evidence 去重计一次 |
| 2 | 不同机制相似描述（PERSISTENCE vs TIMING 同文案） | 不合并 | ✅ 输出 2 个假设，merged_groups=0 |
| 3 | 相同 Evidence 被多个候选引用（e1/e2 + e2/e3） | 合并后 e1/e2/e3 各计一次 | ✅ len(ids)==len(set(ids)) |
| 4 | 规则与 LLM 同机制相似描述 | 合并、来源全保留 | ✅ sources={rule, llm:deepseek-flash}，merged_from 双记录 |
| 5 | 两次独立运行候选顺序不同 | 合并结果一致 | ✅ 反序输入 → 同 kept id、同证据集、同组数 |
| + | LLM 来源合并候选 | 不得 CONFIRMED | ✅ 合并后 status≠CONFIRMED，[dedup] 留痕 |
| + | 相似度阈值方向性 | 近似高分/无关低分 | ✅ ≥0.72 / <0.5 |

## 4. 与 WP1/WP2 的关系

- WP1（T5/T6）已保证「同一 Evidence 引用归属可校验、跨 Run 不误引」；
  WP3 在**假设维度**消重，两者互补：一条 Evidence 支持两个**不同机制**假设是合法竞争
  解释（不合并），但不得在**同一机制的重复候选**里被计成多条独立支持链。
- WP2 的 TIMING 新假设（rule:probe-timing）进入 reassess 后同样过 dedup，
  不会与既有 TIMING 候选堆积。

## 5. 边界与如实声明

1. 相似度是**词面近似**而非深度语义：字面差异大但语义相同的极端写法可能不合并
   （保守方向——宁可留两个候选供人工复核，不误合并丢失信息）。
2. 合并不跨机制：`LOCATOR` 与 `NETWORK` 即使描述高度相似也保留两个假设
   （指令回归场景 2 的明确要求；机制是诊断的第一分类轴）。
3. 本 WP 未运行真实 LLM 产生新候选（B-03 计费授权 BLOCKED）：合并层面对
   「规则 + 伪造 LLM 标注候选」的处理已由场景 4 覆盖；真实 LLM 臂属 WP4 Benchmark 范围。
4. 跨假设的 Evidence 计数由 Graph 裁决层全局消重负责（`seen_ids`），
   本 WP 不重复实现该层，只保证合并后假设内不重复计数。
