# RUN-018 WP2：独立观测 UNKNOWN 语义修复

> 时间：2026-11-27
> 文件：`benchmark/traceguard_benchmark/browser_effect.py`、`benchmark/traceguard_benchmark/fault_effect.py`
> 测试：`tests/unit/test_state_read_tristate_run018.py`（37 passed）
> 注：后台 agent 的文档 Write 被安全策略拦截，本文件由主线基于已验证代码独立撰写。

---

## 1. 问题

### 1.1 BrowserEffectObserver `_persisted()`

原 `_persisted()` 仅检查 `projects` 列表，不检查 HTTP 状态：

```
独立状态接口 500 / 403 / 连接失败
  → projects 不存在
  → persisted=false
  → 误判为业务没有落库
  → CONFIRMED（错误）
```

### 1.2 state_consistency `_probe_state_consistency()`

原代码 `if not direct.get("ok")` 可能判定 ACCEPTED_NOT_PERSISTED，忽略 HTTP 状态：
- 403 / 500 / 超时 / 连接失败时，直接判"未持久化"
- 实际是读取不可用，不是数据不存在

## 2. 修复：三态语义

### 2.1 ReadVerdict 枚举

```python
class ReadVerdict(str, Enum):
    PRESENT = "PRESENT"    # HTTP 200 + ok=true + 记录存在
    ABSENT  = "ABSENT"     # HTTP 200 + ok=true + 权威状态确认不存在
    UNKNOWN = "UNKNOWN"    # 500/403/超时/连接失败/非法 JSON/缺字段
```

### 2.2 判定规则

| 条件 | 判定 |
|---|---|
| HTTP 200 + ok=true + 合法 projects 列表 + 记录存在 | PRESENT |
| HTTP 200 + ok=true + 权威状态明确"不存在" | ABSENT |
| HTTP 500 / 403 / 503 | UNKNOWN |
| 连接失败 / 超时 | UNKNOWN |
| 非法 JSON / 缺少 ok / 缺少 projects | UNKNOWN |

### 2.3 分类器约束

UNKNOWN 时**不得进入**：
- MISLEADING_SUCCESS_ON_FAILURE
- SILENT_IGNORE
- OPERATION_FAILED_ERROR_SURFACED

原因：读取失败 ≠ 业务没有落库。

### 2.4 `_persisted()` 签名变更

```python
# 修改前
def _persisted(state, name) -> bool

# 修改后
def _persisted(state, name) -> tuple[ReadVerdict, str]
```

返回三态 + 观测失败原因（如 `STATE_READ_HTTP_500`、`STATE_READ_MALFORMED`）。

### 2.5 state_consistency 延迟复测

```python
STATE_CONSISTENCY_RETEST_S = 12.0  # 必须显著大于 TF-DB-004 的 9000ms
```

- 立即读取 + 延迟复测，区分"有效读取"与"不可用读取"
- 只有有效权威读取才能证明数据不存在
- 清理失败 → UNKNOWN

## 3. 测试覆盖（37 项）

| 场景 | 预期 | 状态 |
|---|---|---|
| 正常 200 + 存在 | PRESENT | ✅ |
| 正常 200 + 不存在 | ABSENT | ✅ |
| HTTP 403 | UNKNOWN | ✅ |
| HTTP 500 | UNKNOWN | ✅ |
| HTTP 503 | UNKNOWN | ✅ |
| 连接拒绝 | UNKNOWN | ✅ |
| 超时 | UNKNOWN | ✅ |
| 非 JSON | UNKNOWN | ✅ |
| 缺少 projects | UNKNOWN | ✅ |
| 缺少 ok | UNKNOWN | ✅ |
| 读取期间服务重启 | UNKNOWN | ✅ |
| 清理失败 | UNKNOWN | ✅ |
| 延迟读取最终恢复 | 按恢复后状态判定 | ✅ |

**37 passed / 0 failed**

## 4. 保留的证据

- 截图、DOM、网络记录、清理记录在**任何路径**下都保留
- 完整原始状态码和错误原因（`network_error_reason()`）

## 5. 不做的事

- 不通过把 500 改成 200 来修测试
- 不弱化断言
- 不把 UNKNOWN 降级为 CONFIRMED

## 6. 交付

- 代码：`browser_effect.py` + `fault_effect.py`（三态改造完成）
- 测试：`tests/unit/test_state_read_tristate_run018.py`（37 passed）
- 状态：**VERIFIED**（代码 + 测试完整）
