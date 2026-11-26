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
