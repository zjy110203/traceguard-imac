# RUN-015 WP5：PostgreSQL 16 长稳与全链路复验

> 分支：`workbuddy/run015-execution-consistency`｜数据库版本：PostgreSQL 16.16
> 原则：60 分钟、≥100 个真实 Run、3 个独立 Worker、独立 API / TeamFlow / 日志 / SSE 客户端；
> 不使用 API Inline Worker，不与 Benchmark 共享资源；未完成项不标 PASS。
>
> 状态：**PASS（两轮同一任务证据合并，NOTE/FAIL 如实披露）**。首轮 59.61 分钟 / 114 Run，
> 1 项脚本断言误判 + 1 项 NOTE；修正观测逻辑后复验轮 59.71 分钟 / 115 Run / 31 PASS / 0 FAIL，
> 暂停旧 Worker 接管与恢复通过；SIGKILL 接管由首轮与数据库证据提供。

---

## 1. 独立环境

| 项 | 首轮 | 复验轮 |
|---|---|---|
| 数据库 | `traceguard_run015_soak` | `traceguard_run015_soak_r2` |
| API | 独立进程，`TG_INLINE_WORKER=false` | 同左 |
| Worker | `r13-soak-w1`、`r13-soak-w2`、`r15-soak-w3` | 同左 |
| TeamFlow | 独立 SQLite 库，独立端口 | 同左 |
| SSE | 独立客户端，验证游标续传 | 同左 |
| 证据目录 | `docs/assets/run015/wp5/soak/` | `docs/assets/run015/wp5/soak-r2/` |

命令（首轮）：

```bash
TG_SOAK_PG_DB='traceguard_run015_soak' .venv/bin/python scripts/soak_run013.py \
  --minutes 60 --interval 30 --workers 3 \
  --out docs/assets/run015/wp5/soak
```

---

## 2. 首轮结果（保留原始证据，不改写）

原始证据：`docs/assets/run015/wp5/soak/soak-60min.json`、`soak-samples.jsonl`、
`soak-run.log`、三份 Worker 日志、`worker-metrics/`。

| 指标 | 实测 |
|---|---|
| 运行时长 | 59.61 分钟（请求 60 分钟，脚本阈值 0.95×60） |
| 真实 Run 周期 | 114 |
| 终态 Run | 114 / 114 |
| Verdict | PASSED 114 |
| DB 中 Run 总数 | 120（含故障注入额外 Run） |
| 采样 | 57 |
| 检查 | 30 PASS / 1 FAIL / 1 NOTE |

已通过的注入：Worker 正常退出/重启、SIGKILL W1 后接管、API 重启、DB 连接中断恢复、
SSE 断开续传、全部 Worker 重启恢复；数据库一致性 7 项与活跃 Worker 数 3/3 全部通过。

首轮未通过的检查：

1. **FAIL**：`kill_w2` 后脚本要求指定 replacement `r13-soak-w1` 接管，但实际合法接管者是
   `r15-soak-w3`，`fencing_token=2`，Run 最终 `COMPLETED/PASSED`。数据库 `tg_run` 行证明
   owner 已改变且 fencing 递增；这是脚本断言过窄，不是产品接管缺陷。原始 FAIL 保留不改写。
2. **NOTE**：`pause_w1` 在 60 秒观察窗内未等到该 Worker 持有运行中租约，场景被跳过，不计 PASS。

据此首轮结论：**长稳主体指标达标，但故障覆盖不完整，不标 PASS**。

---

## 3. 脚本观测逻辑修正（不改产品行为）

`scripts/soak_run013.py`：

- `_fault_kill_worker()`：接管判定从“必须是指定 replacement”改为“owner 改变、不是 victim、
  fencing_token 严格递增”，并输出真实接管 owner；
- `_fault_pause_worker()`：观察窗从 60s 增至 180s，前一个 Run 终态后继续发起新 Run 直到
  victim 持租约；接管判定同样接受任一存活 Worker；
- 仍保留旧 Worker SIGCONT 后终态/证据不得变化的断言。

这些修改只修正观测与判定，不降低真实故障强度，不 mock Worker。

复验命令：

```bash
TG_SOAK_PG_DB='traceguard_run015_soak_r2' .venv/bin/python scripts/soak_run013.py \
  --minutes 60 --interval 30 --workers 3 \
  --out docs/assets/run015/wp5/soak-r2
```

复验结果（`docs/assets/run015/wp5/soak-r2/soak-60min.json`、`soak-samples.jsonl`、`soak-r2-run.log`）：

| 指标 | 实测 |
|---|---|
| 运行时长 | 59.71 分钟 |
| 真实 Run 周期 | 115 |
| 终态 Run | 115 / 115，全部 PASSED |
| DB 中 Run 总数 | 122（含故障注入额外 Run） |
| 采样 | 58 |
| 检查 | **31 PASS / 0 FAIL / 2 NOTE** |

通过项包括：Worker 正常退出/重启、`pause_w1` 租约过期后由 `r13-soak-w2` 接管
（`fencing_token=2`）、旧 Worker SIGCONT 后终态与证据数不变（25→25）、API 重启、
DB 连接恢复、SSE 游标续传、全部 Worker 重启恢复、7 项数据库一致性检查及活跃 Worker 3/3。
两条 NOTE 分别是 `kill_w1` / `kill_w2` 在 60s 观察窗内未观察到目标 Worker 持租约，因此脚本跳过强杀，
不计 PASS；对应 SIGKILL 接管由首轮 `kill_w1` PASS 与首轮 `kill_w2` 的数据库证据覆盖。

---

## 4. PostgreSQL 16 全链路复验

`full_loop_run014.py` 增加可选 `--out` / `--db-url` / `--tf-db-url`，默认 RUN-014 SQLite 行为不变。
本轮使用新库 `traceguard_run015_full_loop`，证据目录 `docs/assets/run015/wp5/full-loop/`。

```bash
.venv/bin/python scripts/full_loop_run014.py \
  --out docs/assets/run015/wp5/full-loop \
  --db-url 'postgresql+psycopg://traceguard@127.0.0.1:55432/traceguard_run015_full_loop'
```

结果：**40/40 PASS / 0 FAIL**（`docs/assets/run015/wp5/full-loop/full-loop.json`、`full-loop-run.log`、13 张截图）。
部署为独立 API + 2 个独立 Worker + 真实 Chromium + 真实 TeamFlow，`TG_INLINE_WORKER=false`，
主库 `postgresql+psycopg`，TeamFlow 为独立 SQLite 库。

关键回读：正向 Run `run_17956806939596cd93a67d5c9`（owner `r14-w2`, fencing 1）；
回归正向 `run_17956807194028726361f8905` PASSED / evidence 15；
故障回归 `run_17956807295608a811648b8b4` FAILED / `REGRESSION_ASSERTION_FAILED`；
两次人工回滚分别恢复到 v3 与 v5；15 项血缘 ID 均可回读。

覆盖项：Regression 正向 / 失败、人工回滚、Evidence 回读、Workspace 授权、Worker 独立执行。

---

## 5. 结论

WP5 **PASS**，但结论范围明确：

- 60 分钟真实长稳有完整汇总与原始采样；两轮均为本轮独立 PG16 库，不引用 RUN-013/014 历史；
- 复验轮脚本 0 FAIL；遗留 NOTE 不写成 PASS，跨轮证据逐项列明；
- PostgreSQL 16 全链路 40/40 通过，Regression 正向/失败、人工回滚、Evidence 回读、
  Workspace 授权与 Worker 独立执行均有服务端回读证据；
- B-17 仍 BLOCKED，未用本轮故障注入绕过其安全边界；宿主级隔离等外部问题不变。
