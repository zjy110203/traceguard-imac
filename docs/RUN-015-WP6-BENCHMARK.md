# RUN-015 WP6：Benchmark False Pass 证据质量收口

> 分支：`workbuddy/run015-execution-consistency`｜状态：脚本增强完成；新一轮 23×2 **NOT_RUN**（资源隔离），False Pass 仍 **PARTIAL**

---

## 1. 事实基线

RUN-014 的 23×2 已经真实执行（46 次），但归档缺少可独立复核的 `fault_state` 快照：
`fault_effect_status` 仍为 `INFERRED_FROM_VERDICT`，因此 False Pass 只能标 PARTIAL。
本轮**不修改历史归档**，只增强下一次实验的记录能力。

本轮原始归档 SHA-256（已写入单元测试冻结）：

| 文件 | SHA-256 |
|---|---|
| `docs/assets/run014/wp5/v5-2rounds.json` | `f3c3c6b86235019d5f12bf2dcc78e390…bb976e` |
| `docs/assets/run014/wp5/ai-quality-n32.json` | `299172cfef833a3820b5e72b33788902…f00932a` |

## 2. 本轮脚本增强（不改 GT / 不改 Prompt）

`scripts/bench_pilot_run011_r1.py` 的记录新增：

- `fault_activation_timestamp`：客户端观测到真实 `GET /api/faults` 激活状态的时刻
  （TeamFlow API 不提供服务端时间戳，文档明确标注为客户端采样）；
- `fault_effect_timestamp`：Run 终态被观测到的时刻；同时保留原始 `run_ended_at`；
- `fault_effect_status`：仅当**激活快照已 CONFIRMED 且真实 Run verdict 为 FAILED/NOT_EVALUATED** 时才置 `CONFIRMED`，
  激活未确认为 `NOT_TRIGGERED`，其余 `UNKNOWN`。不使用 GT 反推，不把答案写入模型 Prompt；
- `evidence_refs`：诊断返回中 `supporting` / `contradicting` / `evidence_refs` 的真实 ID 并集；
- 原有 `run_id`、`diagnosis_id`、`fault_activation_status`、`fault_state_snapshot` 保持不变。

`scripts/bench_v5_run011_r1.py::enrich_record()` 以 `dict(sample)` 开头，新字段可穿透分类输出；
`classify_record()` 对带结构化证据的记录给出 `STRUCTURED_EVIDENCE` 置信度，不再只能靠 verdict 推断。

`scripts/bench_v5_run014.py` 新增 `--out`：新一轮实验写到
`docs/assets/run015/wp6/v5-2rounds`，绝不覆盖 RUN-014 归档；默认路径保持兼容。

新增单元测试 `tests/unit/test_benchmark_fault_state_run015.py`：
- 8 个必需字段（run_id / diagnosis_id / 两个状态 / 两个 timestamp / evidence_refs / fault_state_snapshot）在 `enrich_record()` 后完整保留；
- 历史归档 SHA-256 冻结，任何回改即失败。

## 3. 新一轮 23×2 为什么没有执行

指令要求 Benchmark 与 60 分钟长稳**不得并行占用同一套资源**。本轮 WP5 的
PG16 长稳（独立 API + 3 Worker + TeamFlow）在后台持续运行至本报告写成时仍未结束，
因此没有启动 23×2 重跑；不拿局部 schema 测试冒充完整实验。

**False Pass 继续标记 PARTIAL / INFERRED_ONLY**，不升级为 VERIFIED。

待长稳结束后，可在独立目录执行（本轮未授权 LLM 四臂，故保持规则臂 + LLM 关闭）：

```
.venv/bin/python scripts/bench_v5_run014.py --out docs/assets/run015/wp6/v5-2rounds
```

该命令跑完后再按新字段独立统计 False Pass、覆盖率与 C/F 两臂；未完成前不得改口径。

## 4. AI 评估边界

- RUN-014 `n=32`（8 域 × 4）结果保留为历史回归基线：规则 0.9688 / LLM 0.8750（细粒度域准确率）；
- 语料是人工构造分层样本，**不能外推**为通用 LLM 准确率，也不作为公平能力比较；
- 没有人工标注，不伪造人工基线；
- 未获额外计费授权，不运行 LLM 四臂成本实验；
- Probe Planner 生产默认仍关闭。

## 5. 证据

`docs/assets/run015/wp6/fault-state-tests-raw.txt`：
`pytest tests/unit/test_benchmark_fault_state_run015.py tests/unit/test_benchmark_scenario_contract_run012.py tests/unit/test_benchmark_v5_statistics_run011_r1_2.py -v`
→ **25 passed**，历史归档 SHA-256 未变。
