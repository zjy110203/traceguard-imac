# RUN-014 WP5：Benchmark v5 真实重跑与 AI 质量验证

> 两个实验**严格隔离**：Benchmark 用独立 SQLite 库 + 独立 TeamFlow 库 + 独立端口 + 3 独立 Worker；
> AI 质量评估不依赖该环境（纯模型调用）。所有原始归档只读，本轮结果写入新的 `run014` 目录。

---

## 1. Benchmark v5：23 场景 × 2 轮

环境（`scripts/bench_v5_run014.py` 编排）：

| 项 | 值 |
|---|---|
| API | 独立进程，`TG_INLINE_WORKER=false`，认证开启 |
| Worker | 3 个独立进程（`r14b-w1/2/3`），租约 60s |
| 被测目标 | 真实 TeamFlow（独立库、独立端口） |
| 数据库 | 独立 SQLite（不与他人共享） |
| LLM | 关闭（本臂只评规则/探针链路，不与 LLM 臂混算） |

结果（`docs/assets/run014/wp5/v5-2rounds.json`，240 KB，exit=0）：

| 指标 | 结果 |
|---|---|
| 场景 × 轮次 | **23 × 2 = 46 次真实执行** |
| 产出失败样本的场景 | 19 / 23（覆盖 0.8261；4 个为正控/未触发故障） |
| 诊断样本 | 38 |
| 正控记录 | 8 |
| 合同检查 | 5/5 PASS（场景契约完整、无未知故障状态、样本量≥19、两轮独立、探针全 ID 绑定） |
| 记录分类 | `EXPECTED_FAILURE_CONFIRMED`=38、`NORMAL_POSITIVE_CONTROL`=8，其余 0 |
| **False Pass** | **0 / 38（rate=0.0）**，证据级别 **PARTIAL / INFERRED_ONLY** |
| Primary Cause Accuracy（规则臂 C） | **0.6842** |
| 探针事后回灌臂（F） | **0.7368**（+5.3pp） |
| Macro-F1（C / F） | 0.5828 / 0.6100 |
| Selective Accuracy（C / F） | 0.875 / 0.875 |
| 探针执行成功率 | **1.0**（真实执行且全 ID 绑定） |
| 探针回灌成功率 | **1.0** |
| 诊断耗时 | mean 14.6 ms / max 64.6 ms（n=38） |
| 弃答率（F） | 0.1053 |
| 探针时间对齐 | `ALIGNED=0 / POST_HOC=56 / UNKNOWN=56`（112 条） |

### 诚实口径（必须随数字一起读）

1. **False Pass 0/38 只能标 PARTIAL**：历史归档没有 `fault_state` 快照，故障生效由 Verdict 推断；
   升级为 VERIFIED 需要 `fault_activation_status` / `fault_effect_status` 落库（本轮未改历史归档）。
2. `H_llm_advisor` 是**混合臂**（LLM 成功 + 规则 Fallback），不得当作 LLM 单独准确率；
   `G` 只是时间关系标 UNKNOWN，不是"无对齐"的因果对照。
3. 23 个场景中 4 个未产出失败样本（正控/未触发），Coverage 0.8261 已如实记录，未用通过样本填充。
4. 本轮没有执行 LLM 四臂（成本与隔离要求），规则/探针两臂为真实结果。

## 2. AI 质量评估：n=32，8 域分层

脚本：`scripts/ai_quality_eval_run014.py`；结果：`docs/assets/run014/wp5/ai-quality-n32.json`。
规则组与 LLM 组使用**同一份输入**（symptom + failed_steps + evidence），GT 只在测试端打分。

### 2.1 主指标

| 指标 | 规则组 | LLM 组 |
|---|---|---|
| Domain Accuracy（原始标签） | — | 0.7500 |
| Domain Accuracy（细粒度 8 域归一，默认口径） | **0.9688** | **0.8750** |
| Domain Accuracy（RUN-013 粗粒度口径，仅对比） | — | 0.8750 |
| Macro-F1（细粒度） | **0.9683** | **0.6119** |
| Macro-F1（粗粒度） | — | 0.6481 |
| 结构合法率 | — | 1.0000 |
| Schema 通过率 | — | 1.0000 |
| 证据支持率 | — | 1.0000 |
| 无效候选率 | — | 0.0000 |
| 重复候选率 | — | 0.0000 |
| Fallback 率 | — | 0.0000 |
| False Positive 样本 | — | 4 / 32 |
| 平均延迟 / P95 | — | 3590.5 ms / **5320 ms** |
| Token | — | 10,216 in / 13,734 out |
| 估算成本 | — | **$0.006515**（+顾问 smoke $0.000991） |
| 日预算 | — | $1.00（本轮累计 $0.007151） |

### 2.2 分类表现（LLM 细粒度）

| 域 | Precision | Recall | F1 |
|---|---|---|---|
| TIMING | 1.00 | 0.50 | 0.667 |
| NETWORK | 0.333 | 0.25 | 0.286 |
| UI | 0.667 | 1.00 | 0.800 |
| LOCATOR | 1.00 | 1.00 | 1.000 |
| PERSISTENCE | 0.667 | 0.50 | 0.571 |
| ENVIRONMENT | 0.000 | 0.00 | 0.000 |
| API | 0.400 | 1.00 | 0.571 |
| JS | 1.00 | 1.00 | 1.000 |

**结论（不外推）**：在这套 n=32 的分层样本上，确定性规则的域判定明显优于 LLM；
LLM 的强项是 LOCATOR / JS（1.0），弱项是 ENVIRONMENT（全部被判成 API/NETWORK）与 NETWORK
（被 API 抢走）。LLM 单候选首选口径 0.875，但因为每域只有 4 例，Macro-F1 对小类错误极敏感，
**不能解读为"LLM 准确率 87.5%"**。规则基线包含显式关键词表，对合成语料天然占优，二者不是公平的
"通用能力"比较，只能作为回归基线。

### 2.3 四类顾问真实 Smoke

| 顾问 | called | fallback | 产出 | 延迟 | 结论 |
|---|---|---|---|---|---|
| Diagnosis Advisor | ✅ 32 次 | 0 | 32 组候选，证据支持 1.0 | 3.6s avg | 真实质量见 §2.1 |
| Compiler Advisor | ✅ | 否 | 2 步（1 步被白名单丢弃）、0 断言，5 条如实告警 | 4.3s | 可用；缺 DOM 时不臆造 UI 断言 |
| Probe Planner | ✅ | 否 | 3 个候选（`http_state` 首选，全部命中已注册探针） | 2.9s | 可用；**生产默认仍关闭**，仅在本次进程内直接调用顾问类 |
| Repair Planner | ✅ | 否 | 2 条受限变更 + 2 条风险告警（未改断言） | 11.9s | 可用；修复提案仍走校验/沙箱/审批 |

四个顾问都**不能**绕过权限、预算、Oracle 门禁与人工审批（对应约束由 WP1/WP3 测试覆盖）。

## 3. 与 RUN-013 的对比

| 项 | RUN-013 | RUN-014 |
|---|---|---|
| Benchmark 23×2 | NOT_RUN（长稳占用环境） | **真跑：46 次执行 / 38 样本** |
| AI 诊断样本 | n=6 | **n=32（8 域 × 4）** |
| 顾问 Smoke | 仅 Diagnosis | **Diagnosis / Compiler / Probe / Repair 四类** |
| 归一化口径 | 单一粗粒度 | **原始 + 细粒度 + 粗粒度三套并列** |
| 成本 | $0.001116（6 次） | $0.007506（32+3 次） |

## 4. 限制与未做

1. Benchmark 的 False Pass 证据级别仍为 PARTIAL（缺 fault_state 快照）；未回改历史归档来"美化"。
2. 未执行 LLM 四臂（成本、隔离与授权要求）；`H_llm_advisor` 只是混合臂。
3. AI 评估语料为**人工构造的分层样本**，不是线上真实缺陷分布；n=32 仅能支撑相对比较。
4. Probe Planner 的"可用"是顾问层 smoke；生产开关仍为 false，未擅自改默认值。
5. 未做跨模型对比（只有 DeepSeek 配置）。
