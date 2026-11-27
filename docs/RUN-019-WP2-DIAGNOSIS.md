# RUN-019 WP2：智能诊断证据覆盖与根因识别升级

> 分支 `workbuddy/run019-product-completion`。
> 原则：**不改 RUN-018 原始 Holdout、不按样本 ID/场景名硬编码、不降低 CONFIRMED 门槛**。
> 所有新增确认证据仍必须满足 `source_trust=TRUSTED` + `directly_confirming=True` + Run 窗口内时间对齐。

## 1. 规则开发（先开发样本，后冻结）

开发样本先以单元测试形式落地并通过：`tests/unit/test_diagnosis_evidence_chain_run019.py`（6 项），
覆盖：永久持久化链、事后证据不得确权、延迟恢复链、定位器 DOM+业务链、缺业务状态时不得确权、
RUN-018 旧样本形态仍弃权。测试通过后才生成新 Holdout manifest 与冻结 SHA-256。

## 2. 新增证据链规则

实现位置：`apps/api/traceguard_api/services/diagnosis_service.py`
（辅助函数 `_parse_ts` / `_within_run` / `_state_chain`）。

| 类别 | 确认所需直接证据（缺一不可） | 拒绝情形 |
|---|---|---|
| PERSISTENCE | 请求被接受（2xx + 业务实体 ID）；同一实体 `state_query:immediate` count=0；同一实体 `state_query:delayed` count=0；所有证据 TRUSTED 且采集时刻在 Run 窗口内；无 API 5xx / 网络故障 | 单次 count=0；延迟复测在 Run 结束后（POST_HOC）；实体 ID 不一致 |
| TIMING | 同一实体即时 count=0、延迟 count>0；均在 Run 窗口内；无 5xx/网络故障 | 事后恢复冒充 Run 期间证据；永久缺失 |
| LOCATOR | 步骤定位超时；Run 窗口内 DOM 独立观测显示目标元素存在/可见（role/name/test-id/可见性）；同一实体业务状态 count>0；用户操作已被 2xx 接受；无 5xx/网络故障 | 仅有超时描述；业务未落库（此时是产品缺陷而非测试资产问题） |

三类规则都进入既有链路：
Run → Evidence → Diagnosis → Hypotheses → Active Probe → Probe Evidence → Graph Adjudication → Diagnosis Revision。
探针执行仍走 `ProbeService.execute`（Probe Contract、预算、Workspace 继承、证据落盘），
重诊断仍走 `DiagnosisService.reassess`（revision 追加、图裁决、时间三态）。

## 3. 新独立 Holdout

脚本：`scripts/holdout_benchmark_run019.py`
证据目录：`docs/assets/run019/wp2/`

| 项 | 值 |
|---|---|
| Holdout 版本 | `run019-holdout-v1`（与 RUN-018 完全隔离） |
| 变体数 | 30（PERSISTENCE 6 / TIMING 6 / LOCATOR 6 / API 控制 4 / NETWORK 控制 4 / ambiguous 控制 4） |
| 轮次 | 2（正序 + 逆序），共 **60 样本** |
| 规则策略版本 | `v3-run019` |
| 探针策略版本 | `probe-strategy-v2-run019` |
| Ground Truth 版本 | `1e6330f451e22b71` |
| 冻结规则 SHA-256 | `diagnosis_service.py=33fef057d4a01a13…`，engine/dedup/planner/feedback/contract/adjudication 见 manifest |
| 原始样本 | `holdout-samples.json`（完整 report/evidence/probes，不只保存标签） |
| GT 隔离 | GT 只在评测端读取；runtime diagnosis 未接收 GT；规则不读取 family/sample_id |

### 3.1 结果（两轮合并，n=60）

| 指标 | RUN-018 旧 Holdout（n=24/轮） | RUN-019 新 Holdout（n=60 两轮合并） |
|---|---:|---:|
| PERSISTENCE Recall | 0.0 | **1.0**（12/12） |
| TIMING Recall | 0.0 | **1.0**（12/12） |
| LOCATOR Recall | 0.0 | **1.0**（12/12） |
| API Recall | 1.0 | **1.0**（8/8，无回归） |
| NETWORK Recall | 1.0 | **1.0**（8/8，无回归） |
| ambiguous 正确弃权 | 4/4 | **8/8** |
| 弃权率 | 66.7% | **13.3%** |
| Selective Accuracy（分母=非弃权） | 1.0 | **1.0**（52/52） |
| Primary Cause Accuracy（分母=全部样本） | 0.5 | **0.8667**（52/60） |
| Macro-F1 | 1.0（仅 2 类有预测） | **0.8333**（6 类，含 INCONCLUSIVE 无 support 的 0 值） |
| 平均诊断耗时 | ~毫秒级 | 22.39 ms |
| Probe Cost | 按家族记录 | 合计 48.0（方案内确定性成本） |
| Evidence Coverage | 记录 | 88/100 |
| 评测错误样本 | 0 | **0** |

混淆矩阵对角线完整：`API 8/8`、`INCONCLUSIVE 8/8`、`LOCATOR 12/12`、`NETWORK 8/8`、
`PERSISTENCE 12/12`、`TIMING 12/12`，零跨类误判。

### 3.2 诚实口径

1. 新变体是**确定性合成样本**，覆盖规则路径与证据语义，**不等价于概率故障采样方差**；
2. 探针执行器在 Holdout 中为冻结的确定性实现（策略版本见 manifest），真实 HTTP/DOM 探针的
   端到端语义由 `tests/integration/test_diagnosis_feedback_real_run_run018.py` 的真实 TeamFlow 运行覆盖；
3. Macro-F1 0.8333 包含 `INCONCLUSIVE` 标签 support=0 的 0 值（正确性由 8/8 弃权与混淆矩阵表达）；
4. 旧 Holdout 归档未被改写；旧样本形态（无 entity_id、无 Run 窗口内 DOM）仍由专项测试验证不会误确认；
5. 若未来修改任何冻结规则文件，必须生成新的 manifest 与新 Holdout，不得复用本集结果。

## 4. 质量门禁

- `ruff check` / `ruff format --check`：通过；
- 诊断专项：新证据链 6 项 + 既有诊断 17 项 + RUN-018 反馈场景 10 项，共 **23 passed**；
- Ground Truth 路径阻断、`_strip_ground_truth()`、Agent 不得审批等既有安全门禁未改动。

## 5. 完成度

| 能力 | 状态 |
|---|---|
| PERSISTENCE 独立证据链 | VERIFIED |
| TIMING 时间关系与 POST_HOC 排除 | VERIFIED |
| LOCATOR 测试资产 vs 产品缺陷区分 | VERIFIED |
| 统一诊断反馈链接入 | VERIFIED |
| 新独立 Holdout（60 样本） | VERIFIED |
| 概率故障采样方差覆盖 | NOT_RUN（属 Reliability Gate） |
