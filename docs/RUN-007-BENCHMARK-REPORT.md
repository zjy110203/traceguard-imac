# RUN-007 UI-FaultBench v3 多臂实验报告

- 时间：2026-11-23（GMT+8）
- 数据集：`teamflow-faultbench-v2`（30 个固定场景，GT 未改写），版本标识 `v3-experiment`
- 环境：TeamFlow `127.0.0.1:8021`；TraceGuard API `:8020`（`TG_INLINE_WORKER=0`）+ 独立 Worker `run007-w1..w4`；SQLite
- 执行方式：所有 Run 走 `{"sync": false}` 入队 + 轮询，**由独立 Worker 真实执行**（`api_inline_worker=false`）
- GT 隔离：GT 只在评测进程内读取（`benchmark/traceguard_benchmark/scenarios.py`），不通过任何 API 暴露
- 原始数据：`docs/assets/run007/benchmark/benchmark-v3.json`、`samples.jsonl`（38 次执行原始观测）
- 命令：`.venv/bin/python scripts/bench_run007.py`（用时 **631.0s**）

> 读法：所有比率都给分子/分母。PASSED 预期场景不参与诊断计分；`NOT_EVALUATED` 不是 0 分，单列。

---

## 1. 执行事实

| 项 | 值 |
|---|---|
| 场景数 | 30（其中 GT 标注为故障的 19 个用于诊断计分） |
| 执行次数 | **38**（30 场景 + Flaky 2 场景各重复 5 次） |
| 失败样本 | **19**（verdict = FAILED / NOT_EVALUATED 且有报告） |
| Verdict 分布 | PASSED 13 / FAILED 20 / NOT_EVALUATED… 见下表（按场景） |
| 执行者 | `run007-w1..w4` 轮换，`fencing_token=1`，`attempt=1` |
| 总耗时 | 631s（中位单场景 ≈ 5–9s，慢场景 NET 14–16s） |

### 1.1 Verdict 与数据集预期不一致的样本（全部保留，未删）

| 场景 | 预期 | 实际 | 说明 |
|---|---|---|---|
| TF-LOC-001 | FAILED | **NOT_EVALUATED** | 定位器故障 = 步骤执行中断 → 状态机输出 `NOT_EVALUATED`（产品规范如此，见 E2E D 基线） |
| TF-LOC-002 | FAILED | **NOT_EVALUATED** | 同上 |
| TF-COMP-003 | FAILED | **NOT_EVALUATED** | 同上 |

这三条不计为"漏检"，也不算"通过"；它们说明**"执行中断"与"断言失败"在 Verdict 语义上是两类**，
数据集 `expected_verdict` 的写法与产品状态机存在口径差异（需在下一轮数据集版本中统一）。

## 2. 四臂定义（同一批失败样本、同一份 GT）

| 臂 | 定义 | 输入 |
|---|---|---|
| **A 传统基线** | 只看 UI 断言结果（`kind=ui` 的失败即判 `UI_RENDERING`） | Run 报告 |
| **C 规则** | 规则诊断引擎（`rule-v1`，`use_llm=false`） | Run 报告 + 证据 |
| **D 证据图** | 规则结果 + 证据图来源增强（当证据同时含 `browser` 与 `api` 且结论为 `PROBABLE` 时升级为 `CONFIRMED`） | + Evidence Graph |
| **E Ordered Probe** | 规则诊断 → 只读探针闭环（真实执行）→ 重新诊断 | + 探针执行结果 |

## 3. 主指标（全部带分子/分母）

| 指标 | A 传统基线 | C 规则 | D 证据图 | E Ordered Probe |
|---|---|---|---|---|
| Primary Cause Accuracy | **3/19 = 15.8%** | **10/19 = 52.6%** | **10/19 = 52.6%** | **10/19 = 52.6%** |
| Diagnosis Coverage（给出确定结论） | 19/19 = 100% | 13/19 = 68.4% | 13/19 = 68.4% | **14/19 = 73.7%** |
| Selective Accuracy（有结论样本内） | 3/19 = 15.8% | 10/13 = 76.9% | 10/13 = 76.9% | 10/14 = 71.4% |
| False Pass Rate（持久化故障被判通过） | **4/4 = 100%** | **0/4 = 0%** | 0/4 = 0% | 0/4 = 0% |
| Macro-F1（8 类，缺失预测计 FN） | 0/8 = 0.075 | 2/8 = 0.3056 | 2/8 = 0.3056 | 2/8 = 0.2992 |
| Probe Count / 失败样本 | 0 | 0 | 0 | **49/19 = 2.58** |
| Evidence Coverage / 失败样本 | — | **548/19 = 28.84** | 同 C | 同 C（+探针新增证据） |
| Time to Diagnosis | — | mean **10.73s** / median **12.15s**（n=15/19，仅计成功返回的调用） | 同 C | 同 C |

**结论（限定在本数据集）**：

1. 传统基线在持久化故障上 **100%（4/4）假通过**——这正是本项目存在的理由；
   规则诊断把它降到 **0/4**。
2. 主因准确率从 **15.8% → 52.6%**（+7 例），但**覆盖率只有 68.4%**：
   规则引擎选择"证据不足就不下结论"（`INCONCLUSIVE`），这不是错，但必须与 selective accuracy 同看。
3. **D 与 C 完全相同**：证据图增强在本批样本上**没有产生任何额外命中**——
   说明当前的图增强条件（`browser`+`api` 双来源且 `PROBABLE`）触发面太窄，属于**未验证的能力**，不能宣称"图增强有效"。
4. **E 比 C 多覆盖 1 例（13→14），但主因命中数不变、Macro-F1 略降（0.3056→0.2992）**：
   探针平均执行 2.58 次/样本，代价明确、收益未在本批样本上体现。

## 4. Flaky 场景（重复 5 次，保留每次原始 verdict）

| 场景 | 5 次 verdict | 判定 |
|---|---|---|
| TF-FLAKY-001 | FAILED, PASSED, PASSED, PASSED, PASSED | **不稳定（1 失败 / 4 通过）** → 需按 Flaky 处理，不得当作确定性失败 |
| TF-FLAKY-002 | FAILED × 5 | **确定性失败**（5/5） |

不取平均、不删样本；Flaky 结果不并入确定性场景的准确率。

## 5. 自愈三指标（可修复场景：TF-LOC-001/002）

| 指标 | 值 | 说明 |
|---|---|---|
| Assertion Preservation Rate | **2/2 = 100%** | 两个提案均保持必需断言不变（`PASS`） |
| Repair Validation Success Rate | **1/2 = 50%** | LOC-001 沙箱 `PASSED`（由 `run007-w2` 执行）；LOC-002 沙箱 `FAILED` |
| Repair Regression Pass Rate | **0/1 = 0%** | LOC-001 晋升后回归 `NOT_EVALUATED` |

**必须如实说明的异常**：LOC-001 的沙箱真实通过，但 `/promote` 返回中**没有 `promoted_version`**，
随后回归为 `NOT_EVALUATED`，回滚接口因"提案缺少 `promoted_version`"返回 **412**。
这与 E2E 场景 D（沙箱→审批→晋升 v2→回归 PASSED→回滚）**不一致**，说明定位器类候选的
"沙箱通过 → 晋升"路径存在**未定位的断点**。本轮**不宣称自愈在 Benchmark 上闭环**，也不修改指标口径来美化。

## 6. LLM 对照臂（真实调用，按实际模型记录）

独立文件：`llm-arm-flash-routed.json`、`llm-arm-pro.json`（不并入 A/C/D/E，口径不同）。

| 臂 | 子集 | LLM 候选主因准确率 | 最终诊断准确率 | 实际模型 | LLM 调用 |
|---|---|---|---|---|---|
| Flash（路由未升级） | 19 个故障场景 | **8/19 = 42.1%** | 10/19 = 52.6% | `deepseek-flash` ×12，无候选 ×7 | 22 次 |
| Pro | 8 个故障场景 | **5/8 = 62.5%** | 6/8 = 75.0% | `deepseek-v4-pro` ×8 | 8 次 |
| 同子集对比（DB-001/002/004、UI-001/002/003、API-001/002） | 8 | Flash **5/8** vs Pro **5/8** | — | — | — |
| Routed（reasoning 升级） | — | **PARTIAL** | — | 本轮 `models_seen` 仅 `deepseek-flash`，**未触发升级**，无法与 Flash 分离 | — |

说明：
- LLM 假设的 `status` 被设计为 `CANDIDATE`（不能 `CONFIRMED`），所以这里评的是"LLM 候选机制是否命中 GT"，
  不是"LLM 是否定了根因"——后者由安全约束禁止。
- 7 个"无候选"样本包含 `NOT_EVALUATED` 无法诊断（4 个）与 LLM 未产出建议（3 个），**全部计入分母**，不剔除。
- 成本：本轮 LLM 调用合计 30 次，应用侧估算成本 < 0.01 USD（日预算 1 USD，`budget_exceeded=false`）。

## 7. 与上一轮（RUN-005）的比较

| 项 | RUN-005 | RUN-007 本轮 |
|---|---|---|
| v3 多臂 | **未执行（BLOCKED，仅说明成本理由）** | **已执行**：30 场景 / 38 次执行 / 四臂 + 两 LLM 臂 |
| 执行方式 | — | 独立 Worker 队列（`sync:false`），非 API 内嵌 |
| 数字 | 无（拒绝在未做实验时给数字） | 全部带分子/分母，附样本量 |

## 8. 限制（必须随结论一起引用）

1. **单数据集、单机**：30 个固定场景不是随机抽样；Wilson 区间只能描述该数据集上的不确定性，不能外推总体能力。
2. **每场景 1 个计分样本**（Flaky 另计 5 次），置信区间很宽；不做显著性宣称。
3. **D 臂无增益、E 臂增益未体现**：不能写成"证据图/探针提升了准确率"。
4. **自愈链路未闭合**（见 §5），"自愈成功率"不可对外宣称。
5. **人工基线 BLOCKED**：无真实工程师参与，不产生人工对照数字。
6. **Routed 臂不可分离**：本轮未触发 reasoning 升级。
7. LLM 臂子集与全量不可直接比较；`flash` 19 场景 vs `pro` 8 场景的准确率**不能横向比大小**，
   同子集对比（5/8 vs 5/8）才可以。
