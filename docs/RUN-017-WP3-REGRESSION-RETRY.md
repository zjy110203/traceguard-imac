# RUN-017 WP3：Regression INCONCLUSIVE 人工授权重试（方案 A）

> 决策依据：`docs/RUN-016-REGRESSION-RETRY-DECISION.md`（方案 A：人工授权的**新 Attempt**）
> 代码：`apps/api/traceguard_api/services/repair_service.py`、`routers/intelligence.py`、`auth.py`
> 前端：`apps/web/src/views/RepairsView.vue`、`types.ts`
> 测试：`tests/integration/test_regression_retry_run017.py`（15 项，全通过）

---

## 1. 语义（不可变历史）

```
INCONCLUSIVE --(人工授权, confirm=true)--> attempt N+1（新 Run）
                                          ├── 旧 Run / Verdict / Evidence 不动
                                          ├── 旧 history 追加式保留
                                          └── 新结论只写在新 Attempt 上
```

- 每个 `(proposal, promoted_version)` 最多 **2 次 Attempt**（首次 + 1 次重试）；
- 禁止自动重试；禁止原地重置 Run；禁止覆盖历史结论；
- `regression_run_id` 始终指向**最新 Attempt**，历史在 `regression_attempts[]`。

## 2. 原因码门禁（fail-closed）

| 文档内的 `regression_failure_reason` | 对外原因码 | 是否允许重试 |
|---|---|---|
| `REGRESSION_EVIDENCE_MISSING` | `EVIDENCE_MISSING` | ✅ |
| `REGRESSION_RUN_TIMED_OUT` | `EXECUTION_TIMEOUT` | ✅ |
| `REGRESSION_RUN_FAILED` / `REGRESSION_RUN_CANCELLED` | `RUN_ABORTED` | ✅ |
| `REGRESSION_ENVIRONMENT_DRIFT` | `ENVIRONMENT_DRIFT` | ✅（需环境自检通过） |
| `REGRESSION_ORACLE_UNAVAILABLE` | 同名 | ❌ |
| `REGRESSION_ASSERTION_FAILED` | `FAILED` | ❌ |
| `REGRESSION_VERDICT_NOT_EVALUATED` / 其它/空 | `UNKNOWN` | ❌ |

映射函数 `regression_reason_code()` 对未知值一律落到 `UNKNOWN`（不可重试），
新增枚举不会因为"没登记"而意外变成可重试。

## 3. 授权链（三层，缺一不可）

1. **路由层 RBAC**：`POST /api/v1/repairs/{id}/regression-retry` → `repair.regression`
   （`PERMISSIONS` 里只有 `qa-lead` 与 `platform-admin`）；
2. **主体校验**：`require_human_actor(actor)` —— `agent:*` / `llm:*` 一律拒绝；
3. **显式确认**：`confirm=true`，缺省拒绝（`REGRESSION_RETRY_REQUIRES_CONFIRM`）。

额外门禁：

- 正式版本必须仍等于 `promoted_version`（否则 `BLOCKED_VERSION_DRIFT`）；
- 旧结论 `STALE` 拒绝；
- Oracle 必须仍可用（缺失属配置问题，重跑同样缺）；
- `ENVIRONMENT_DRIFT` 会做一次只读 `/health` 检查，不健康直接拒绝
  （`ENVIRONMENT_NOT_RECOVERED`）。

## 4. 幂等与并发

- 认领键：`repair-regression:{proposal_id}:v{promoted_version}:attempt{N}`；
  与 Run 同事务插入（`create_run_once` + 主键 CAS），**并发点击只会产生一个新 Run**；
- 文档 CAS（`_save(expected_version=...)`）：输家回到赢家的 Run，不覆盖状态；
- `idempotency_key` 写入授权记录：同一 key 的重复请求（含新 Attempt 已完成后再点）
  直接返回当前文档，不报错、不重复创建。

## 5. 数据字段

```jsonc
{
  "regression_attempt_no": 2,
  "regression_attempts": [
    {"attempt": 1, "run_id": "run_a", "status": "INCONCLUSIVE",
     "reason_code": "EVIDENCE_MISSING", "authorized_by": "", "authorized_at": "..."},
    {"attempt": 2, "run_id": "run_b", "parent_run_id": "run_a",
     "status": "PASSED", "authorized_by": "human:qa-lead", "authorized_at": "..."}
  ],
  "regression_parent_run_id": "run_a",
  "regression_retry_allowed": false,
  "regression_retry_reason_code": "EVIDENCE_MISSING",
  "regression_retry_decision": "ALLOWED_WITH_AUTHORIZATION",
  "regression_retry_authorizations": [
    {"actor": "human:qa-lead", "reason_code": "EVIDENCE_MISSING",
     "from_run_id": "run_a", "to_run_id": "run_b", "idempotency_key": "retry-1"}
  ]
}
```

历史文档缺少这些字段时按 `attempt_no=1`、`retry_allowed=false` 解释，**不回写历史**。

## 6. 前端

`RepairsView` 回归卡片新增：

- Attempt 编号 / 上限 / 剩余次数；
- 重试资格（服务端 `regression_retry_allowed`，不可重试时显示阻塞原因）；
- 血缘：`regression_parent_run_id` 可点击回看上一 Run；
- Attempt 历史表（# / Run / 状态 / 原因码 / 授权人 / 时间）；
- 仅当 `regression_retry_allowed=true` 才渲染"重新验证（人工授权 · 新 Attempt）"，
  点击走二次确认 + 填写说明；原因码由服务端判定并回显，前端不可改。

提交后展示**新的 Run**，旧 Run 的链接与证据仍可访问。

## 7. 测试（15 项，全部通过）

| # | 用例 | 结果 |
|---|---|---|
| 1 | 正常 INCONCLUSIVE 授权重试（新 Run / Attempt 2 / 血缘） | PASS |
| 2 | 缺少 `confirm` 拒绝 | PASS |
| 3 | Agent 主体拒绝 | PASS |
| 4 | RBAC 只允许 qa-lead / platform-admin | PASS |
| 5 | Oracle 缺失不可重试 | PASS |
| 6 | FAILED 拒绝重跑 | PASS |
| 7 | STALE 拒绝 | PASS |
| 8 | 最大次数限制 | PASS |
| 9 | 并发 5 次点击只产生 1 个新 Attempt | PASS |
| 10 | 重复请求幂等（同一 Run） | PASS |
| 11 | 重启后 Attempt/授权历史可恢复 | PASS |
| 12 | 旧 Evidence / 报告不被覆盖 | PASS |
| 13 | 旧 history 项不被改写 | PASS |
| 14 | 版本漂移阻断 | PASS |
| 15 | `ENVIRONMENT_DRIFT` 需环境自检通过 | PASS |

```bash
.venv/bin/python -m pytest tests/integration/test_regression_retry_run017.py -q
# 15 passed
```

## 8. 边界与未覆盖

- **没有自动重试**：任何"再跑一次"都必须有人类授权记录；
- **次数上限硬编码 2**（与决策稿一致）；提高到 3 需要新的决策与审计口径；
- `ENVIRONMENT_DRIFT` 的自检只覆盖"被测目标健康"，不覆盖宿主机端口/磁盘等宿主级问题；
- 前端未做"剩余次数为 0 时隐藏作者信息"的细节打磨（保留只读表格，便于审计）；
- 真实浏览器下的"重试 → 新 Run 完成"链路在 WP6 UI 验收中覆盖（同一 API 契约）。
