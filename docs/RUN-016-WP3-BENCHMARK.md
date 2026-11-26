# RUN-016 WP3：Benchmark 可信度与 False Pass 测量修复

> 核心问题：`fault_effect_status` 由 **Run Verdict** 推断，等于用"被测对象的结论"
> 证明"故障是否生效"，False Pass 因此存在循环论证。

---

## 1. 修复前逻辑（源码定位）

`scripts/bench_pilot_run011_r1.py`：

```python
if activation_confirmed and verdict in {"FAILED", "NOT_EVALUATED"}:
    sample["fault_effect_status"] = "CONFIRMED"
```

`scripts/bench_scenario_contract.py::classify_record()`：

- 预期失败且 Run 失败 → `effect = INFERRED_FROM_VERDICT`；
- False Pass 判定允许 `activation == CONFIRMED or effect == CONFIRMED`；
- `is_confirmed_failing_sample()` 把 `INFERRED_FROM_VERDICT` 计入 False Pass 分母。

结果：**测试判 FAILED → 故障"生效" → 测试判 PASSED → False Pass**。同一份 Verdict 既当裁判又当证据。

## 2. 设计：四个概念彻底分离

| 概念 | 定义 | 本实现的来源 | 是否读 Test Verdict |
|---|---|---|---|
| Fault Activation | 故障注入器是否真的激活 | TeamFlow `GET /api/faults` 的 `active[]` 快照 | 否 |
| Fault Effect | 故障是否真的影响被测业务 | **独立业务探针**（见 §2.1） | 否 |
| Test Verdict | TraceGuard 自己的判定 | Run 文档 | —（只作为被观测对象记录） |
| Ground Truth | 离线评测标签 | 场景契约 / TeamFlow GT | 否（且执行链路禁止访问） |

### 2.1 独立探针（`benchmark/traceguard_benchmark/fault_effect.py`）

| 探针 | 做什么 | 判定 |
|---|---|---|
| `api_error` | `POST /api/projects` 发**空载荷**（健康时被 4xx 快速拒绝，不写数据） | 500/503/403 → 业务受影响；4xx 校验拒绝 → 正常 |
| `api_timeout` | 同上但 2.5s 短超时 | 超时 → 业务写入链路不可用 |
| `persistence` | 真实创建唯一名称项目 → `GET /api/state/projects` 是否包含 | 创建成功但查不到 → 持久化失败 |
| `state_lag` | 直接测 `GET /api/state/projects` 耗时 | ≥1500ms → 读取受显著影响 |

故障 → 探针映射（`FAULT_EFFECT_PROBES`）覆盖 API / 网络 / 持久化 / 时序 / 复合类；
**纯 UI / 定位器类（TF-UI-*、TF-LOC-*、TF-COMP-003）没有独立业务探针 → 一律 `UNKNOWN`**。
这是有意的：没有独立证据就不允许声称故障生效，更不允许据此判 False Pass。

### 2.2 样本字段（新增，保留历史）

```jsonc
{
  "benchmark_schema_version": "benchmark-fault-v2",
  "fault_activation_status": "CONFIRMED",
  "fault_activation_source": "teamflow:/api/faults",
  "fault_activation_timestamp": 1795670000.1,
  "fault_effect_status": "CONFIRMED",
  "fault_effect_source": "INDEPENDENT_TEAMFLOW_PROBE:persistence",
  "fault_effect_evidence_refs": ["tfp_1a2b3c4d5e6f7a8b"],
  "fault_effect_timestamp": 1795670002.9,
  "fault_effect_probes": [{"kind": "persistence", "impacted": true, "detail": {...}}],
  "run_execution_status": "COMPLETED",
  "run_verdict_observed": "PASSED"
}
```

- `benchmark_schema_version` 区分新旧：v1（历史，无该字段）保持原语义不重解释；
  v2 起效果必须有独立来源；
- 历史字段（`fault_state_snapshot`、`evidence_refs` 等）全部保留，不改写历史归档
  （`tests/unit/test_benchmark_fault_state_run015.py` 仍校验 RUN-014 归档 SHA 不变）。

### 2.3 False Pass 判定（fail-closed）

```
v2：expected=FAILED + verdict=PASSED
    ├─ effect=CONFIRMED 且有独立来源/证据 → FALSE_PASS（真 False Pass 样本）
    └─ 否则                              → FALSE_PASS_UNVERIFIED（不计入 False Pass）
```

新增 `RecordClass.FALSE_PASS_UNVERIFIED`，并在 `is_confirmed_failing_sample()` 中排除，
避免"未证实"混进分子。

另加一条降级规则：v2 样本声称 `CONFIRMED` 但 `fault_effect_source` 为空/非独立来源
→ 强制降级 `UNKNOWN`，置信度标 `EFFECT_WITHOUT_INDEPENDENT_EVIDENCE`。

## 3. Ground Truth 与模型隔离

| 检查项 | 结论 |
|---|---|
| GT 是否进入诊断 Prompt | 否。`packages/agents` / `apps/api` 中无任何 `gt_mechanism` / `ground_truth` 读取 |
| 评测端是否用 GT | 是，仅 `benchmark/evaluation.py`（`ground_truth` 映射，注释明确"评测侧持有，不暴露给 Agent"） |
| 执行链路能否访问 GT 接口 | **修复前可以**（TeamFlow host 已授权，`/api/ground-truth/{id}` 可达）→ **本轮阻断** |
| 阻断方式 | `TargetPolicy.deny_path_prefixes = ("/api/ground-truth", "/ground-truth")`，命中返回 `GROUND_TRUTH_PATH_BLOCKED`；`check()` 与 `resolve()` 都生效 |
| 评测脚本是否受影响 | 否。评测脚本用自身 urllib 直连，不走 TargetPolicy |

## 4. 测试

`tests/unit/test_benchmark_fault_effect_run016.py`（9 项，含真实本地 HTTP 服务模拟 TeamFlow）：

- API 500 → CONFIRMED；健康 422 → NOT_TRIGGERED；
- 落库失败（201 但状态查不到）→ CONFIRMED，证据带 `API_ACCEPTED_BUT_NOT_PERSISTED`；
- 状态滞后 1.6s → CONFIRMED（按实测耗时，不按 verdict）；
- 纯定位器故障 → `UNKNOWN` + `NO_INDEPENDENT_PROBE`；
- 接口层面**不接受 verdict 参数**（传了直接 TypeError）；
- GT 路径在任何情况下被阻断。

`tests/unit/test_benchmark_false_pass_v2_run016.py`（6 项）：

- v2 有独立证据 → `FALSE_PASS`；
- v2 只有激活 → `FALSE_PASS_UNVERIFIED`；
- v2 声称 CONFIRMED 无证据 → 降级 `UNKNOWN`；
- v2 Run FAILED 不再推断 effect；
- v1 历史语义不变；
- 正向控制不会被标成 effect CONFIRMED。

历史回归：`test_benchmark*.py` 共 41 项通过（含 RUN-015 归档 SHA 校验）。

## 5. 新一轮实验的启动条件与结论

任务要求：只有新的 Fault Effect 观测逻辑完成并通过专项测试后，才启动新实验；
若独立观测无法建立，**不得**启动 23×2 实验，更不能宣称 False Pass 已解决。

本轮状态：

- 独立观测**已建立**（4 类探针 + 证据引用 + 单测 15 项通过）；
- 23×2 完整实验**未启动**（NOT_RUN），原因：
  1. 需要专用时间窗与独占 TeamFlow/Worker 拓扑，本轮窗口被 P0 长稳与多进程并发验收占用；
  2. LLM 臂因"不擅自读取或使用凭据"保持 **BLOCKED**，四臂对比无法成立；
  3. 在缺少 LLM 臂与人工基线的情况下跑"两轮 23 场景"只能得到规则臂单臂数据，
     不足以支撑与历史实验的**口径一致对比**。
- 因此本轮**不宣称 False Pass 已解决**，只宣称"测量口径已修正、且新口径下
  False Pass 必须有独立证据"。

## 6. 剩余限制

- `persistence` 探针在健康环境会真实创建一个 `r16-probe-*` 项目（副作用已在样本中标记
  `side_effect=true`）；若未来场景断言精确项目数，需要在探针后调用 TeamFlow reset。
- `state_lag` 的 1500ms 阈值是工程判断，不是产品 SLA；如需严格口径应改为"分位数 + 基线差"。
- 纯 UI 类故障（TF-UI-*、TF-LOC-*）在新口径下永远是 `UNKNOWN`，需要独立的浏览器级观测
  （录屏/DOM 差异）才能升级为 CONFIRMED——本轮未实现。
