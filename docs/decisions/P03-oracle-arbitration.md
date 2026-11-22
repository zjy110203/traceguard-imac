# P03 决策提案：Oracle 多来源冲突裁决细则（待评审，未冻结）

> 状态：**NEEDS_REVIEW**。本文只提出候选方案、风险与测试设计，不构成正式冻结规则。
> 需要项目负责人批准后才能写入契约并标记为冻结。

## 1. 问题

同一断言可能同时获得多个来源的观测：

| 来源 | 例子 | 可信度 |
|---|---|---|
| 页面 UI | 出现"项目创建成功"提示 | 可被前端 bug 欺骗 |
| 接口响应 | `POST /api/projects` 返回 200 | 可被事务回滚欺骗 |
| 独立业务状态查询 | `GET /api/state/projects` 的 count | 当前最接近业务事实 |
| 数据库只读探针 | 直接查表（当前探针不支持任意 SQL） | 强，但权限与耦合成本高 |
| 后端日志 | 请求日志 / 异常栈 | 辅助，非权威 |

冲突示例（已真实复现）：

- UI=成功、接口=200、业务状态 count=0（TF-DB-001）；
- UI=无提示、业务状态 count=1（TF-UI-001）；
- 接口=500、UI=错误提示、业务 count=0（TF-API-001）。

## 2. 候选策略

### 策略 A：来源优先级（简单可解释）

```
business_state_query > api_response > ui_observation
```

- 按断言 `kind` 选择权威来源：`business_state` 只认独立状态查询；`ui` 只认浏览器观测。
- 冲突不跨域比较（UI 断言失败不否定业务成功）。
- 优点：确定、可实现、与现有实现一致。
- 风险：状态查询本身可能滞后（最终一致），需要一致性窗口；"权威"是假设，需可审计。

### 策略 B：加权投票

对各来源赋权，冲突时加权求和。权重需实验校准。

- 优点：可表达"多数证据"。
- 风险：**权重不可解释**、可能掩盖真实矛盾、需要大规模标注数据（当前 n=3，不可行）。
- 结论：现阶段不推荐。

### 策略 C：保守冲突（冲突即不确定）

任一断言存在两个 TRUSTED 来源互相矛盾 → 该断言 `INCONCLUSIVE`，Disposition=`NEEDS_MORE_EVIDENCE`，
必须补探针后才能定论。

- 优点：不制造假结论，符合"允许 INCONCLUSIVE"的原则。
- 风险：可能长期停留在不确定（需要停止条件与升级路径）。

### 策略 D：域内优先级 + 跨域冲突升级（推荐）

- 域内：`business_state` 断言 → 独立状态查询为权威；`ui` 断言 → 浏览器观测为权威；
  `api`/`network` 断言 → 网络观测为权威。
- 跨域：若两个不同域的 TRUSTED 证据对**同一业务事实**给出相反结论
  （如 UI 成功 vs 业务 count=0），不互相覆盖，而是：
  1. 生成 `PERSISTENCE`/`API` 等根因假设（按 5xx 等机制证据排序）；
  2. 断言本身仍按域内规则判定；
  3. Verdict 由断言聚合，不由"多数来源"决定。
- 一致性窗口：对最终一致的业务，允许在 `consistency_window_ms` 内重查一次；
  两次结果不一致 → `INCONCLUSIVE`（记录两次证据）。
- 优点：既保留域内确定性，又显式暴露跨域矛盾；与现有实现兼容。
- 风险：需要定义"同一业务事实"的映射（断言 → 状态字段），否则跨域比较会误判。

## 3. 契约影响（若批准）

新增/调整：

1. `OracleSpec.evaluation_rule` 增加 `authoritative_source` 与 `on_conflict`（`FAIL` / `INCONCLUSIVE` / `ESCALATE`）。
2. `Run` 记录 `consistency_window_ms` 实际等待时间与重查次数。
3. `AssertionResult` 增加 `sources: [{type, trust, value}]`，保留冲突全貌。
4. 版本升级为 `SCHEMA_VERSION=0.2.0`，旧数据可读（缺省字段）。

## 4. 测试设计（批准后补齐）

| 编号 | 场景 | 期望 |
|---|---|---|
| T1 | 业务断言：UI 成功 + count=0 | FAILED，证据含两者，根因 PERSISTENCE |
| T2 | UI 断言：业务成功 + 无提示 | FAILED（UI 域），业务断言 PASSED |
| T3 | 两次状态查询结果不同（模拟最终一致） | INCONCLUSIVE，Disposition=NEEDS_MORE_EVIDENCE |
| T4 | 5xx + 业务 count=0 | 根因 API，业务断言 FAILED，不生成 PERSISTENCE 根因 |
| T5 | Oracle 未审批 | NOT_EVALUATED（现有行为） |
| T6 | 状态查询超时 | INCONCLUSIVE，不得降级为 PASSED |

## 5. 待确认问题

1. 业务事实 → 状态字段的映射由谁维护（Oracle 作者 or 平台）？
2. `consistency_window_ms` 的默认值与上限？
3. 冲突升级是否需要人工工单（而非自动 Disposition）？
4. 数据库只读探针是否纳入权威来源（涉及权限与耦合）？

## 6. 建议

采纳 **策略 D**，并将本文件转为正式决策记录（ADR）后再修改契约。在批准前：
- 保持当前实现（域内规则 + 5xx 优先），不写入新的冻结语义；
- 现有 6 条测试（见 §4 T1/T2/T4/T5/T6 的等价场景）已在 `test_diagnosis_rules.py` 与
  Benchmark B 中覆盖，可作为评审依据。
