# Event、Evidence 与 Diagnosis 说明

## 1. Event（C05）

只追加。强制两条唯一性：

| 约束 | 语义 |
|---|---|
| `(producer_instance_id, sequence)` | 同一生产者实例内序号严格唯一，跨实例各自计数 |
| `(producer_id, idempotency_key)` | 生产者维度幂等；重复投递返回已有事件 |

- `workspace_id` 由服务端注入，payload 中同名字段被剔除（防越权）。
- 同键异载荷：写入 `tg_event_conflict` 并抛 `CONFLICT`，不静默覆盖。
- 缺口检测只标"待确认"，不推断丢失。
- `context_refs` 仅表示关联，**不表示因果**；图的因果边需要显式依据。

Run 执行产生的事件：`run.started`、`step.completed`、`assertion.evaluated`、`run.completed` / `run.failed`。

## 2. Evidence（C06）

| 维度 | 取值 | 说明 |
|---|---|---|
| `kind` | raw / normalized / derived / diagnostic | 派生必须指向 `raw_ref` 并记录规则 |
| `source_type` | browser / network / api / backend_log / database / agent / probe | 来源可追溯 |
| `source_trust` | TRUSTED / UNTRUSTED / UNKNOWN | 与完整性独立 |
| `integrity_check` | PASS / FAIL / NOT_RUN | **持久化内容**完整性（服务端读回重算），不是来源内容比对 |

- 入库前递归脱敏（password/token/secret/authorization/cookie/key）。
- 大对象（截图/DOM/trace）存内容寻址 artifact，证据只存引用。

### 2.1 三种摘要语义（RUN-007-FINALIZATION-2）

脱敏使"入库内容"与"原始来源 payload"必然不同，摘要在语义上必须分开：

| 字段 | 覆盖对象 | 用途 |
|---|---|---|
| `sha256` | 脱敏**前**原始来源 payload | 来源可追溯/可比对（调用者提供原始内容时） |
| `stored_content_sha256` | 脱敏**后**实际持久化内容 | 服务端存储完整性校验（读回重算） |
| `attachment_sha256` | ArtifactStore 落盘字节 | 附件篡改检测 |

- `POST /evidence/{id}/verify` 默认**不需要**调用者提交内容：服务端读取持久化记录与真实内容，
  重算 `stored_content_sha256` 比对，附件调用 `ArtifactStore.read()` 重算字节摘要；
- 兼容旧请求：若仍传 `content`，结果只放在 `legacy_source_check`，
  绝不与存储完整性合并成一个 PASS；
- 历史行没有 `stored_content_sha256` → 如实 `NOT_RUN`（无法回溯证明），不冒充 PASS；
- 派生证据校验 `raw_ref` 存在、是 raw、同工作区、有 `derivation_rule`；
- 篡改磁盘文件后读取会报 `integrity check failed`；库内内容被改动 → `STORED_CONTENT_MISMATCH`。

## 3. Evidence Graph（C07）

节点：Run / Step / Event / Evidence / Assertion / Hypothesis / ProbeExecution / Source。
边：`parent_child`、`emitted_by`、`derived_from`、`correlated_by_*`、`same_execution_context`、
`temporal`、`supports`、`contradicts`、`evaluates`。

- 每条边带 `basis`（建立依据），前端展示时省略 `same_execution_context` 噪声边。
- `direct_causal` 必须带可信因果证据，本版本不生成。
- 图快照持久化（`tg_doc kind=graph, id=graph_<run_id>`），可按 Run 复现。

实测一次失败 Run：40 节点 / 39 边（25 Evidence、12 Event、8 Step、2 Assertion、1 Run），
其中 36 条为同上下文关联 —— 这正是"关联不等于因果"的体现。

## 4. Diagnosis（C08）

### 4.1 八个维度独立

Symptom / Failure Domain / Failure Mechanism / Root Cause / Contributing Factor /
Flaky Characteristic / Diagnosis Status / Disposition。

### 4.2 确定性规则产出假设

`DiagnosisService` 从真实证据生成 Hypothesis，再由 `DiagnosisEngine` 裁决：

| 观察 | 假设 | 是否可确认 |
|---|---|---|
| 业务断言失败 + UI 断言通过 + 无 5xx | PRODUCT / PERSISTENCE（False Pass） | 是（独立状态查询 TRUSTED） |
| UI 断言失败 + 业务通过 | PRODUCT / UI_RENDERING | 是（浏览器证据） |
| 存在 5xx 网络证据 | PRODUCT / API | 是（网络证据），并将"未落库"记为贡献因素 |
| 请求失败 / 超时 | ENVIRONMENT / NETWORK | 有失败证据时确认 |
| 定位器超时 | TEST_ASSET / LOCATOR | 否（只能 SUPPORTED，需补 DOM 证据） |
| 未捕获 JS 异常 | PRODUCT / UI_RENDERING | 否（除非 UI 断言同时失败） |
| 同用例历史结论不一致 | Flaky 特征 | 不进入根因 |

确认规则（`ConfirmationRule`）：至少一条 `TRUSTED` 且 `directly_confirming` 的支持证据，且无
`TRUSTED` 反驳证据。LLM 提出的假设只能从 CANDIDATE 开始。

### 4.3 已修复的口径问题

初始实现中，"业务断言失败"总是生成 PERSISTENCE 满足，导致接口 500 场景被误判为持久化缺陷。
修正：存在 5xx 时不再生成 PERSISTENCE 根因，而把"业务未落库"作为 API 根因的贡献因素。
Benchmark B 的首选根因准确率因此从 0.667 提升到 1.000（3 个故障样本）。

### 4.4 Disposition（C10）

与 Diagnosis 分离，按已确认根因的域分流：

| 确认域 | Disposition | 路由 |
|---|---|---|
| PRODUCT | PRODUCT_DEFECT | product-backlog（产品修复，不由自愈改产品代码） |
| TEST_ASSET | TEST_ASSET | repair-candidate（可进入 Repair，需审批） |
| TEST_EXECUTION | TEST_EXECUTION | retry-scheduler |
| ENVIRONMENT | ENVIRONMENT | infra-oncall |
| 无确认根因 | NEEDS_MORE_EVIDENCE | probe-planner（补证据，不得直接修复） |

## 5. Active Diagnosis / Probe（C09）

五项校验（任一失败即拒绝并审计）：权限 → 资源范围（Workspace/任意 SQL/模板白名单）→ 风险 →
预算（次数/成本/时间）→ 参数 Schema。

本版本探针：`http_state`（独立状态查询）、`network_trace`（读已落库观测）、
`dom_snapshot`（读已落库 DOM）、`backend_log`（本地日志尾部，脱敏）。

探针结果登记为 `source_type=probe` 的 raw evidence，`source_trust=TRUSTED`，可被诊断引用。

## 6. 当前局限

1. 根因分类为规则驱动，覆盖 TeamFlow 的 4 类机制；更细粒度需扩充规则与数据集。
2. Flaky 只做"历史结论不一致"判定，未实现多次执行的统计显著性。
3. 探针建议使用启发式 EIG 评分（权重未校准，已在代码与文档标注）。
4. 未接入 LLM 推理；`INCONCLUSIVE` 是正常且诚实的结果。
