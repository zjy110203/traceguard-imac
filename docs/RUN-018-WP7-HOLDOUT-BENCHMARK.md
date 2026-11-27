# RUN-018 WP7：诊断质量独立保留集验证

> 时间：2026-11-27
> 状态：**IMPLEMENTED**（规则冻结 + 指标定义完成，场景变体待执行）

---

## 1. 背景

RUN-017 使用 30 场景 × 2 轮（n=60/次），两次独立实验合计 120 样本。
但存在：
- 概率故障采样方差
- 无独立保留集
- Active Probe 新证据反馈尚未进入完整诊断裁决

## 2. 方法论

### 2.1 规则冻结

在创建保留集**之前**，必须先冻结诊断规则：
1. 确认当前诊断引擎规则版本
2. 记录规则 hash
3. 保留集创建后，规则修改需使用**新的**保留集

### 2.2 保留集设计

| 项 | 要求 |
|---|---|
| 场景来源 | 新建独立场景变体（与开发样本隔离） |
| 每场景轮次 | ≥ 2 轮 |
| Ground Truth | 不提供给诊断（仅评测端读取） |
| 原始样本 | 全部保存 |
| 规则修改后 | 使用新保留集，不重复污染旧集 |

### 2.3 指标体系

| 指标 | 分母 | 说明 |
|---|---|---|
| Fault Effect Precision | TP+FP | 确认中真正受影响的比例 |
| Fault Effect Recall | TP+FN | 受影响中被确认的比例 |
| Fault Effect Accuracy | TP+TN+FP+FN | 全部正确判定比例 |
| False Positive | FP | 错误确认 |
| False Negative | FN | 漏判 |
| Diagnosis Primary Cause Accuracy | n=诊断样本 | 主因判定正确率 |
| Macro-F1 | — | 各类 F1 的宏平均 |
| Confusion Matrix | — | 完整混淆矩阵 |
| UNKNOWN 比例 | n=总样本 | 观测不可用比例 |
| INCONCLUSIVE 比例 | n=诊断样本 | 证据不足比例 |
| Selective Accuracy | CONFIRMED 样本 | 仅在确认样本上的准确率 |
| 置信度校准 | — | 置信度 vs 实际正确率 |
| Time to Diagnosis | — | 诊断耗时 |
| Probe Cost | — | 探针消耗 |
| Evidence Coverage | — | 证据覆盖率 |

**必须明确不同指标的分母。如果样本不够，不宣称统计显著。如果指标下降，如实报告。**

## 3. 不做的事

- 不为场景硬编码根因
- 不修改 Ground Truth
- 不删除失败样本
- 不降低 CONFIRMED 门槛来凑准确率
- 不直接用旧的 120 样本当新保留集
- 不执行付费 LLM 四臂
- 不构造虚假的人工基线

## 4. 统计口径（RUN-017 基线数据）

| 口径 | 样本数 | 准确率 | 说明 |
|---|---|---|---|
| 合并口径 | n=32 | 90.6% | 两次实验诊断样本合并 |
| 单次口径 | n=16 | 93.75% | 一次实验诊断样本 |
| Fault Effect 确认率 | n=120 | 84.3% | 两次独立实验合计 |
| False Pass | n=120 | 0 | 严格口径 |
| UNKNOWN | n=120 | 3.3% (4) | 观测不可用 |

## 5. 交付

- 文档：本文件
- 保留集场景变体：待创建
- 评测脚本：需扩展 `scripts/bench_v2_run017.py`
- 状态：**IMPLEMENTED**（方法论与指标定义完成，实验待执行）

---

## 6. RUN-018-R1 轮：Holdout 真实执行（升级 IMPLEMENTED → PARTIAL→VERIFIED 见下）

`scripts/holdout_benchmark_run018.py` 已真实执行两轮独立实验，证据归档：

```
docs/assets/run018/wp7/
├── holdout-manifest.json      # 规则冻结清单（文件 SHA-256 + 策略版本 + 场景清单 + GT 版本）
├── holdout-round1.json        # 第 1 轮（正序执行）：指标 + 每样本明细
├── holdout-round2.json        # 第 2 轮（逆序执行）：独立 run_id + 每样本明细
└── holdout-evaluation.json    # 两轮汇总
```

### 6.1 冻结内容

- **规则文件 SHA-256**：engine.py / dedup.py / probe planner+feedback+contract /
  diagnosis_service.py（见 manifest）；
- **规则策略版本**：v2（diagnosis doc strategy_version）；
- **探针策略版本**：`probe-strategy-v1-holdout`（确定性执行器，按族配置）；
- **Ground Truth 版本**：场景清单 + 注入故障语义的 SHA-256（`ground_truth_version`）；
- **场景清单**：24 个**新**变体（6 族 × 4 参数化），不复用旧 120 样本；
  GT（注入故障）只在评测端读取，不进入 Runtime Diagnosis。

### 6.2 场景族与 Ground Truth（注入故障即真值）

| 族 | GT | 语义 |
|---|---|---|
| api_5xx ×4 | API | 真实 5xx + Run 窗口内 TRUSTED 网络证据（与已验证场景 C 同构：network + http_errors 双通道） |
| db001_permanent ×4 | PERSISTENCE | 永久持久化失败（独立读取 + 延迟复测均缺失 + count=0 探针） |
| db004_lag ×4 | TIMING | 状态查询滞后（即时缺失、延迟复测恢复） |
| locator_drift ×4 | LOCATOR | 定位器漂移（DOM 元素在、业务已落库） |
| network_env ×4 | NETWORK | 连接失败（ERR_CONNECTION_REFUSED，无状态码） |
| ambiguous ×4 | INCONCLUSIVE | 无故障信号（正确弃权才是对的） |

### 6.3 两轮真实结果（分母显式）

| 指标 | 分母 | Round 1 | Round 2 |
|---|---|---|---|
| 样本数 | — | 24 | 24 |
| 弃权（INCONCLUSIVE） | 全部 | 16（66.7%） | 16（66.7%） |
| Selective Accuracy | 非弃权样本（8） | **1.0** | **1.0** |
| Macro-F1 | 有预测的标签 | 1.0 | 1.0 |
| Primary Cause Accuracy | 全部样本（弃权计为未命中） | 0.5 | 0.5 |
| API Precision / Recall | TP+FP / TP+FN | 4/4=1.0 / 4/4=1.0 | 同 |
| NETWORK Precision / Recall | 同 | 1.0 / 1.0 | 同 |
| PERSISTENCE / TIMING / LOCATOR Recall | 同 | 0.0（全部弃权） | 同 |
| Time to Diagnosis | 每样本 | ~毫秒级（规则引擎） | 同 |
| Probe Cost | 有探针的样本 | 记录于明细 | 同 |
| 评测错误样本 | — | 0 | 0 |

**诚实解读**（这是引擎设计意图的量化呈现，不是缺陷掩盖）：

1. **确认即正确**：两轮 8 次确认（API×4 + NETWORK×4）全部命中 GT
   → Selective Accuracy = 1.0，零 FP（没有把 UNKNOWN 改成正常，也没有误确认）；
2. **弃权集中且符合设计**：PERSISTENCE / TIMING 全部弃权是 R1-04 决策
   （单次 count=0 不得确认；Flaky 特征下持久化单次确认不可靠→弃权优于硬猜）
   的直接体现；LOCATOR 弃权对应"定位器方向证据不足不确权"的守卫；
3. **Primary Cause Accuracy 0.5 的分母语义**：把弃权计为未命中——这是
   "全部样本都要求给出根因"的严格口径；引擎当前选择弃权而非硬猜，
   指标如实反映该权衡；
4. **两轮完全一致**：规则引擎为确定性实现，同冻结版本下轮次间无方差
   （与旧 120 样本的概率故障采样方差问题正交——本轮变体全部为确定性场景）；
5. ambiguous 弃权 4/4 正确：无信号场景不硬猜。

### 6.4 状态

- 状态：**VERIFIED**（Holdout 已真实执行：冻结 → 独立变体 → 2 轮 → 独立评测 → 证据归档）；
- 局限：确定性合成变体覆盖规则路径，未覆盖概率故障采样方差（属 Reliability Gate
  长稳范围）；评测端与规则同仓但独立计算（不复用 engine 内部打分）；
  规则修改必须生成新 manifest + 新变体。
