# RUN-016 WP1-A：PostgreSQL 16 严格 60 分钟长稳

> 数据库：PostgreSQL 16.16（本机 55432，用户 `traceguard`，库 `traceguard_run016_soak`）
> 拓扑：独立 API（`TG_INLINE_WORKER=false`）+ 3 个独立 Worker + 独立 TeamFlow + 真实 Chromium
> 口径：**真实墙钟 ≥ 60 分钟，无任何容差**

---

## 1. 相对 RUN-015 的口径修正

| 项 | RUN-015 | RUN-016 |
|---|---|---|
| 时长判定 | `duration_min >= minutes * 0.95` | `wall_clock_minutes >= required_minutes`（严格） |
| 时长来源 | 首个 Run 创建 → 最后一个 Run 结束（漏掉准备与收尾） | 长稳循环开始 → 循环结束的真实墙钟 |
| 请求时长 | 60 分钟 | 62 分钟（预留余量），验收下限 60 分钟 |
| 证据标记 | 硬编码 `RUN-013 WP4` | `--workpackage/--run-tag` 参数化 |
| 故障观察窗 | 60s | 240s，且失败后串行重试直到目标 Worker 持租约 |

命令行（可复现）：

```bash
TG_SOAK_PG_DB=traceguard_run016_soak .venv/bin/python scripts/soak_run013.py \
  --minutes 62 --min-minutes 60 --interval 30 --workers 3 \
  --out docs/assets/run016/wp1/soak --workpackage "RUN-016 WP1" --run-tag run016
```

## 2. 结果

| 指标 | 实测 |
|---|---|
| 开始（本地时间） | 2026-11-26T18:12:34+0800 |
| 结束（本地时间） | 2026-11-26T19:14:42+0800 |
| **真实墙钟时长** | **62.139 分钟**（要求 ≥60） |
| Run 跨度 | 61.72 分钟 |
| 真实 Run 周期 | **113**（要求 ≥100） |
| 终态 Run | 113 / 113 |
| Verdict | PASSED 113 / FAILED 0 |
| 数据库 Run 总数 | 181（含故障注入额外 Run） |
| 采样 | 56 |
| 检查 | **31 PASS / 0 FAIL / 1 NOTE** |

原始证据：`docs/assets/run016/wp1/soak/soak-62min.json`、`soak-samples.jsonl`、
`soak.log`、三份 Worker 日志、`worker-metrics/`。

## 3. 数据库一致性（服务端 SQL 直查）

| 检查 | 结果 |
|---|---|
| 无重复步骤 | 0 |
| 无孤儿步骤 | 0 |
| 无孤儿证据 | 0 |
| 终态 Run 无残留租约 | 0 |
| 每个 Run 至多一份提交回执 | 0 重复 |
| 完成 Run 均有证据 | 0 缺失 |
| 事件序号无重复 | 0 |
| 活跃 Worker | 3 / 3 |
| 状态倒退 / 重复业务结果 | 无 |

产物一致性（WP1-C 巡检，同一库）：729 个文件 ↔ 729 条登记，哈希全匹配，
孤儿 0、缺文件 0。

## 4. 故障场景执行情况

| 场景 | 结果 | 证据 |
|---|---|---|
| Worker-2 正常退出（SIGTERM）+ 重启 | **PASS** | `exit=0`，随后继续消费 |
| Worker-1 执行中 SIGKILL → 接管 | **PASS** | `after_kill=PREPARING`，`owner=r13-soak-w2`，`fencing=2`，终态 COMPLETED |
| Worker-2 执行中 SIGKILL → 接管 | **PASS** | `after_kill=RUNNING`，`owner=r13-soak-w1`，`fencing=2`，终态 COMPLETED |
| Worker-1 暂停（SIGSTOP）→ 租约过期 → 接管 | **NOTE（未触发）** | 见 §5 |
| API 重启 | **PASS** | Run 终态可回读，Worker 继续消费 |
| PostgreSQL 连接中断恢复 | **PASS** | API + Worker 继续，Run COMPLETED |
| SSE 断开 + 游标续传 | **PASS** | 不重复已读事件 |
| 全部 Worker 重启后恢复消费 | **PASS** | w1/w2 restarted |

### 4.1 SIGKILL 场景（修复后首次真正触发）

RUN-015 复验轮的两次强杀均为 NOTE（"60s 内未观察到租约"）。本轮将观察窗拉到 240s，
并在"被别的 Worker 抢到"时等待当前 Run 结束后重试，两次强杀**都真实发生并完成接管**。

### 4.2 暂停场景为什么仍然是 NOTE（如实说明）

`pause_w1` 在 51 次尝试中始终没等到 `r13-soak-w1` 持租约。根因（已定位）：

- 前面的 `kill_w1` 把 w1 SIGKILL 后，脚本只 `fleet.start(replacement)`，
  而 replacement（w2）本来就在运行 → **victim 没有被重新拉起**；
- 后面的 `pause_w1` 针对的进程早已死亡，自然永远等不到租约。

处理：
1. 已修复脚本（强杀后按需重新拉起 victim，见 `_fault_kill_worker`），供后续运行使用；
2. **不把该 NOTE 计为 PASS**；
3. 暂停语义的真实证据不依赖本场景，另有独立来源：
   - `tests/integration/test_worker_fencing_run014.py::test_paused_worker_cannot_write_after_takeover`；
   - UI 全链路 E2E 的 `W1-⑫~⑯`：真实 SIGSTOP → 租约过期 → 第三个 Worker 接管 →
     旧 Worker SIGCONT 后不能覆盖状态、不产生重复步骤/证据、过期提交被 Fencing 拒绝
     （`docs/assets/run016/wp4/ui/run016-ui-full-loop.json`，全部 PASS）。

## 5. 一次被中止的长稳（保留证据，不隐藏）

第一次长稳运行 42m57s 后被主动中止：脚本的故障探测被改成"抢不到就立即再发一个 Run"，
导致 Run 并发；用例 `cleanup_policy=RESET_DATA` 会在每次 Run 前 `POST /api/test/reset`，
并发 Run 互相清空 TeamFlow 测试数据 → `business_state` 计数断言假失败（54 个 FAILED）。

- 证据：`docs/assets/run016/wp1/soak-aborted-concurrent-probe.json`（按分钟 verdict 分布）；
- 结论：**脚本缺陷，不是产品缺陷**；
- 处理：恢复串行探测后重启，得到 §2 的干净结果。

## 6. 结论

- 严格 60 分钟（实测 62.14）达成，无容差；
- 113 个真实 Run 全部合法终态，数据库一致性 8 项全过；
- 已批准的故障场景中 7 项有真实执行证据，1 项（SIGSTOP）因脚本缺陷未触发，
  保持 NOTE 并由其它独立证据覆盖；
- **判定：PASS（1 项 NOTE 如实披露，不计为 PASS）**。
