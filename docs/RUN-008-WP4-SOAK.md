# RUN-008 WP4：60 分钟真实长稳实验报告

## 1. 执行事实

| 项 | 值 |
|---|---|
| 起止（UTC） | `2026-11-24T05:07:20Z` → `2026-11-24T06:07:28Z` |
| 真实时长 | **60.14 分钟**（目标 60 分钟，无中断、无重跑） |
| 采样周期 | 60 秒；**66 个周期 / 66 条采样**，cycle 0→65 唯一且时间单调 |
| 架构 | 独立 API `:8042`（`TG_INLINE_WORKER=0`）+ 4 个独立 Worker PID `75549/75550/75551/75552` + 真实 TeamFlow `:8041` + SQLite + Chromium/Playwright |
| 用例 | `tcase_tf_create_normal`（真实 `sync=false` 入队 + 独立 Worker 执行 + 轮询） |
| 后台任务 | `BZ1Hp8`，一次启动完成，未重复启动、未覆盖采样、未改动运行库 |

## 2. 结果（23/23 checks 全真）

`docs/assets/run008/soak-60min.json`：`passed=true`，`checks` 无一项失败。

| 类别 | 检查结果 |
|---|---|
| 时长/周期 | `duration_reached`、`cycles_reached` 通过 |
| 执行归属 | `api_inline_worker_off`（inline_worker_seen=0）、`no_worker_death` |
| API/业务 | `api_health_ok_all_cycles`、`no_api_errors`(0)、`no_unexpected_failed`(0)、`no_not_evaluated`(0)、`all_runs_passed`(66/66)、`no_run_timeouts`(0) |
| 外部依赖 | `teamflow_healthy_all_cycles`（每周期真实 `ok is True` + count） |
| 数据完整性 | `no_runs_without_report`(0)、`no_duplicate_events`(0)、`no_orphan_steps`(0)、`no_runs_without_evidence`(0)、`no_unrecovered_leases`(0)、`db_integrity_ok`(quick_check=ok)、`db_counts_from_current_instance`、`db_counts_readable` |
| SSE | `sse_received_events`、`sse_resume_verified` |
| 进程 | `process_metrics_source_recorded`、`api_survived` |

最终库计数：runs 66、tasks 66、events 858、evidence 1848、steps 528、
库 6.48 MB / WAL 3.99 MB；所有 run verdict = PASSED。

SSE 明细：66/66 周期收到事件，0 周期无事件；心跳 158；`Last-Event-ID` 续传
`resume_ok=41` / `resume_no_new=25`；重复 event_id 0。

## 3. 采样自校验

`docs/assets/run008/soak-samples.jsonl` 66 行：
- 66/66 `test_verdict=PASSED`、`execution_status=COMPLETED`、`teamflow_ok=true`；
- cycle 唯一且 `since_start_s` 单调；
- 4 个 Worker 全程存活；API PID `75506` 存活；
- 资源字段 `cpu_pct/rss_mb` 为 `null`、`source=liveness_only` —— 沙箱禁止 `ps` 且无 `/proc`，
  **不伪造 0 或估算值**。

## 4. 披露

- 66 个周期多于目标 60：脚本按 `minutes*60/interval` 计算目标并以墙钟为终止条件，
  最后一分钟额外完成若干周期；原始采样全部保留，不截取、不挑选。
- 长稳期间未做故障注入（避免污染 TeamFlow 状态与周期判定），故障恢复证据来自 WP3 独立实验。
- 资源指标仅存活探测，不能据此声称 CPU/内存稳定性。
