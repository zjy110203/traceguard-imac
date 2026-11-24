# RUN-011 WP4：Benchmark v5 真实对照实验（NOT RUN）

> 状态：**NOT RUN（有意不执行）**。
> 原因：v4 的 Probe 臂测量口径无效；在修复"实验臂必须真正调用不同实现"之前运行 v5，
> 只会产出另一组不可解释的数字。本轮优先把闭环与探针链路做实，不为凑指标跑实验。
> 本文件说明：① v4 的问题定位；② v5 必须完成的改造；③ 执行 v5 的明确前置条件与步骤。

## 1. 为什么 v4 的 Probe 消融无效（已定位到代码）

`scripts/bench_run010.py`：

| 位置 | 问题 |
|---|---|
| `_probe_signals_from_replays()` | 从**已落库证据**重建 `ProbeSignal`（`http_state` 读 `api/state_query` 证据、`network_trace` 读已有 network 证据），并未执行探针 |
| `_arm_F()` / `_arm_E_no_feedback()` | `probes_used = len(signals)`，这是"重建了多少信号"，不是"执行了多少探针" |
| `ProbeService.plan_and_probe()` | 真实执行数记录在 `probes_used_service`，14 个样本为 **0 或 1**（多数样本首诊即 CONFIRMED → `STOP_CONFIRMED`） |
| 指标计算 | `probes_per_sample[F]` 取 `arm["probes_used"]`（重建信号数），与 `probes_used_service` 脱节 |

因此：
- v4 报告的 `probes_per_sample[E/F/G]=0.9286` **不是探针执行率**；
- `F_minus_G = 0/14` 不能解释为"时间对齐无价值"（两组输入都不是新执行探针的产物）；
- RUN-010 CLOSEOUT 报告写的"所有臂 probes_per_sample=0"与归档 JSON 不符（见 BASELINE §2.1）。

## 2. v5 必须完成的改造（按优先级）

1. **实验臂真正调用不同实现**
   - `F`：调用真实 `ProbeService.plan_and_probe()`，且**必须产生新证据**
     （`execution_id` 新、`evidence_id` 新、`observed_at` 在本次实验中）；
   - `E`：同一探针执行，但 `reassess()` 不被调用（证据只落库不回灌）；
   - `G`：真实探针 + `run_ended_at=0`（关闭时间对齐）；
   - `D`：同引擎 + 图裁决关闭（保持）；
   - `C`：规则引擎（保持）；
   - `A`：原始报告（保持）。
   - 禁止把"从旧证据重建的信号"计入任何臂的 `probes_executed`。

2. **必须记录三组计数并分别报告**
   - `probe_executions`（执行记录数）
   - `probe_evidence_new`（新证据数）
   - `probe_feedback_applied`（进入 `reassess` 并产生 update 的探针数）
   每个指标给出分子/分母/样本数（指令 §4.4）。

3. **场景补齐（14 → ≥19）**
   在 v4 的 14 个基础上新增 5 类（指令 §4.1）：
   LOCATOR 优先级、时序延迟复测、纯导航超时证据、证据来源冲突、Flaky 机制判断。
   每个场景必须有**真实故障注入**与独立 GT，不允许 `seeded=false`。

4. **LLM 隔离**
   本轮实测发现：同一场景两次诊断分别是 `INCONCLUSIVE` 与 `PROBABLE`，差异来自
   LLM advisor 候选（`llm:deepseek-flash`）。v5 必须二选一：
   - 固定 `use_llm_advisor=False` 做规则臂对比；或
   - LLM 臂单列，并记录每次调用的 provider/model/prompt_version。

5. **执行拓扑**
   建议使用本轮已验证的独立拓扑（API + 3 Worker + PG16 + 强制代理），
   而不是 `rt.execute_run()` 进程内执行；否则"真实部署下的收益"无法外推。

6. **Ground Truth 隔离**
   保持评测端令牌；新增场景的 GT 不得进入执行体可见的任何 API（v4 的做法可复用）。

## 3. 本轮已具备的前置条件

| 条件 | 状态 |
|---|---|
| 独立部署闭环可重复 | ✅ 两轮 62/62 |
| 探针真实执行 + 新证据 | ✅ WP2 实验室 3/3 执行、3 条新证据 |
| 时间对齐语义可验证 | ✅ observed/collected/ingested 三段式 |
| 未注册探针 fail-closed | ✅ 404、不产生记录 |
| 预算停止语义 | ✅ STOP_BUDGET_CLOSED |
| 可复现的故障注入 | ✅ TeamFlow `/api/faults/*`（26 个故障） |
| PG16 结果可复核 | ✅ `verify_db_run011.py` 8/8 |

## 4. 本轮**未**产出的东西（明确声明）

- 没有任何 v5 指标数字；
- 没有宣称 Graph 或 Probe 提高准确率；
- 没有宣称时间对齐有价值/无价值；
- 没有做统计显著性检验（本轮样本量为 0）。

## 5. 建议的下一步（按顺序）

1. 重写 `bench_run011.py`（不复用 `_probe_signals_from_replays`），先用 3 个场景打通
   "真实执行 → 新证据 → 回灌"的计数口径；
2. 用 5 个新场景验证口径（不评分）；
3. 场景补齐到 19 个后跑两轮，产出 `docs/assets/run011/benchmark/`；
4. 只有在 F/E/G 的 `probe_executions > 0` 且 `probe_evidence_new > 0` 的样本上才计算
   Probe 相关指标；其余样本标记 `PROBE_NOT_EXECUTED`，不参与分母。

> 结论：v5 不是"跑一下脚本"，而是"重做测量"。在测量口径修好之前，任何数字都是噪声。
