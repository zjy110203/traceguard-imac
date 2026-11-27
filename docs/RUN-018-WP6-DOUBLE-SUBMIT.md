# RUN-018 WP6：TF-TIME-003 双提交竞态独立观测

> 时间：2026-11-27
> 文件：`benchmark/traceguard_benchmark/double_submit.py`
> 测试：`tests/unit/test_double_submit_run018.py`（9 passed）

---

## 1. 背景

TF-TIME-003 是双提交竞态故障：
- 故障激活时，创建接口响应延迟 1.2s（`api.py` line 66）
- 前端 JavaScript 在 300ms 内丢弃第二次表单提交（`web.py` line 143）
- 结果：快速双击"创建"按钮时，第二次提交被静默丢弃

RUN-017 中 TF-TIME-003 仍为 **UNKNOWN**——因为没有专门的独立观测器。

## 2. 设计

### 2.1 观测器架构

`DoubleSubmitObserver` 使用 HTTP 直接模拟快速双提交（不依赖浏览器）：

```
健康基线（单次提交创建 1 个项目）
  → 故障激活确认（GET /api/faults）
  → 独立双提交（两个线程间隔 20ms 各发一次 POST）
  → 独立状态回读（GET /api/projects 查实际创建数量）
  → 数据唯一性判定（分类）
  → 清理（probe-cleanup）
```

### 2.2 为什么不用浏览器

浏览器层的 300ms 去重是**前端行为**（`web.py` 的 `lastSubmit.ts` 检查）。
用 HTTP 直接测试的是**服务端行为**：
- 两个 HTTP 请求都到达服务端
- 服务端实际创建了几条记录
- 这才是双提交竞态的本质

浏览器层去重可以用 `BrowserEffectObserver` 覆盖（已有）。

### 2.3 分类逻辑

| 观测结果 | 分类 | 状态 |
|---|---|---|
| 2 个请求成功，创建 1 个 | DOUBLE_SUBMIT_DEDUPED | CONFIRMED |
| 1 成 1 败，创建 1 个 | DOUBLE_SUBMIT_ONE_FAILED | CONFIRMED |
| 创建 2 个 | DUPLICATE_CREATED | NOT_TRIGGERED |
| 创建 0 个 | NO_DATA | UNKNOWN |
| 基线失败 | BASELINE_FAILED | UNKNOWN |
| 双提交都失败 | SUBMIT_FAILED | UNKNOWN |
| 创建 >2 个 | UNEXPECTED_COUNT | UNKNOWN |

## 3. 必须区分的场景

1. **用户只产生一次业务操作**（前端去重）→ 服务端创建 1 条
2. **两次提交但服务端正确去重** → 服务端创建 1 条
3. **服务端创建了重复数据** → 服务端创建 2 条（DUPLICATE_CREATED）
4. **第一次失败、第二次成功** → 服务端创建 1 条（ONE_FAILED）
5. **探针无法触发或判断** → UNKNOWN

## 4. 网络证据

每次观测记录完整的原始网络证据：
- `baseline_create`：基线创建的状态码和响应
- `double_submit_1` / `double_submit_2`：两个并发请求的状态码、耗时、响应
- `state_readback`：独立状态回读结果

## 5. 测试

9 项测试覆盖：
- 2 成 1 建 → CONFIRMED / DEDUPED
- 1 成 1 败 → CONFIRMED / ONE_FAILED
- 2 建重复 → NOT_TRIGGERED / DUPLICATE_CREATED
- 0 建 → UNKNOWN / NO_DATA
- 基线失败 → UNKNOWN / BASELINE_FAILED
- 3 建异常 → UNKNOWN / UNEXPECTED_COUNT
- 都失败 → UNKNOWN / SUBMIT_FAILED
- 非 TF-TIME-003 → NOT_APPLICABLE
- 数据结构完整性

**9 passed / 0 failed**

## 6. 不降低标准

- 不为降低 UNKNOWN 率强行判定
- 无法可靠确认时保留 UNKNOWN 并解释原因
- 清理失败不报告成功

## 7. 交付

- 代码：`benchmark/traceguard_benchmark/double_submit.py`
- 测试：`tests/unit/test_double_submit_run018.py`（9 passed）
- 状态：**IMPLEMENTED**（需真实 TeamFlow 环境验证后升 VERIFIED）

---

## 8. RUN-018-R1 轮：真实浏览器独立观测（升级 IMPLEMENTED → VERIFIED）

上一轮的 HTTP 双线程观测只能验证服务端行为，无法回答"用户双击后前端到底发了
几个请求"。本轮在 `double_submit.py` 新增 `BrowserDoubleSubmitObserver`
（Playwright Chromium，真实浏览器）：

### 8.1 实验 A：同一 BrowserContext 快速双击

`_RequestCapture` 通过 `page.on("request"/"response")` 捕获真实发出的
`POST /api/projects`（URL / 状态码 / 耗时）：

- 两次 `click()` 无等待（真实双击节奏）；第二次点击因无故障时页面立即导航
  找不到按钮 → 如实记录 `second_click_note`（不视为观测失败，交由实验 B）；
- TF-TIME-003 激活时：**实测确认 FRONTEND_DEBOUNCE_EFFECTIVE**——
  用户双击 2 次、前端只发出 1 次 POST、业务只创建 1 条（expA_count=1）。
  Ground Truth（前端 300ms 去重窗口丢弃第二次提交）与独立观测一致 → CONFIRMED。
- 截图、Console 错误、请求数据全部落盘（`exp-a-<token>.png` + JSON）。

### 8.2 实验 B：两个独立 BrowserContext 并发提交

两个线程各持有独立 Playwright 实例 + 独立 context（独立会话），Barrier 对齐后
同时点击：

- **真实发现**：健康环境下两个独立上下文同名前缀提交全部 200，服务端
  **没有幂等/同名去重**，权威回读 2 条记录——分类 `DUPLICATE_CREATED`
  （status=CONFIRMED，真实重复数据）。
  这是本轮的诚实观测结论：TeamFlow 服务端对跨会话并发同名创建无幂等保护。
- 每上下文截图（`exp-b1/b2-<token>.png`）+ JSON 证据落盘。

### 8.3 分类映射（6 类，A/B 严格区分不合并）

| 分类 | 判定 | status |
|---|---|---|
| FRONTEND_DEBOUNCE_EFFECTIVE | 双击 2 次 → 前端只发 1 次 POST → expA 1 条 | fault_active ? CONFIRMED : NOT_TRIGGERED |
| SERVER_IDEMPOTENCY_EFFECTIVE | ≥2 请求全成功 → 只落 1 条 | NOT_TRIGGERED（幂等挡住 ≠ 故障） |
| DUPLICATE_CREATED | 落 ≥2 条 | CONFIRMED（真实重复数据） |
| ONE_REQUEST_FAILED | 一败一成 | UNKNOWN |
| BUSINESS_STATE_UNKNOWN | 权威回读失败 / 无法判定 | UNKNOWN（不得改写成正常） |
| OBSERVER_FAILED | 浏览器/基础设施失败 | UNKNOWN |

CONFIRMED 不只凭 HTTP 200：需 Ground Truth（TF-TIME-003 设计行为）与独立
权威状态回读共同支撑；实验按项目名前缀（expA/expB1/expB2）归属计数。

### 8.4 测试

- `tests/integration/test_double_submit_browser_run018.py`（3 项：实验 A 真实
  Chromium、实验 B 真实 Chromium + 清理真实性断言、分类映射不变量）；
- 既有 `tests/unit/test_double_submit_run018.py` 9 项全部保留通过；
- 合计 12 项 × 3 轮稳定。

### 8.5 边界声明

- HTTP 观测器保留（`DOUBLE_SUBMIT_OBSERVER:http_rapid_submit`）用于无浏览器
  环境的快速验证，不再作为前端双提交问题的完整验证；
- 清理使用 default workspace 显式 `allow_default`（token≥12 hex，与 WP3 契约
  一致），清理失败如实报告（断言 cleanup.ok）。
- 状态：**VERIFIED**（真实 TeamFlow + 真实 Chromium 端到端执行）。
