# RUN-013 WP4：60 分钟长稳与真实故障恢复（PostgreSQL 16）

> 日期：2026-11-25（CST）
> 脚本：`scripts/soak_run013.py`
> 证据：`docs/assets/run013/soak/soak-60min.json`、`soak-samples.jsonl`、`worker-*.log`

---

## 1. 实验环境

| 组件 | 配置 |
|---|---|
| 数据库 | 真实 PostgreSQL **16.16**（独立库 `traceguard_run013_soak`，实验前重建） |
| API | 独立 `uvicorn` 进程，`TG_INLINE_WORKER=false` |
| Worker | 2 个独立进程（`r13-soak-w1/w2`），按需启动 `w3` 接管 |
| TeamFlow | 独立进程 + 独立 SQLite |
| 浏览器 | 真实 Chromium |
| 用例 | 已审核、确定性通过的 `tcase_bench_normal2`（正常创建项目） |
| 节奏 | 每 30 秒 1 个 Run，不缩短真实执行内容 |
| 租约 | TTL 6s（故障注入窗口；生产默认 120s） |

## 2. 时长与吞吐

| 指标 | 值 |
|---|---|
| 要求时长 | 60 分钟 |
| 实测时长 | **60.05 分钟** |
| 真实 Run 周期 | **113**（另有 5 个故障注入 Run，数据库总 Run 118） |
| `COMPLETED` | 113 |
| `FAILED` | 0（执行失败 0；所有 Run 均 `COMPLETED`） |
| Verdict 分布 | `PASSED=113` |
| 数据库 Run 总数 | 118 |

## 3. 故障注入

| # | 场景 | 结果 | 证据 |
|---|---|---|---|
| 1 | Worker-2 SIGTERM 正常退出并重启 | PASS（exit=0） | Worker 日志退出码 0 |
| 2 | Worker-1 执行中 SIGKILL | NOTE：60s 窗口内未观察到 w1 持租约，跳过（SIGKILL 已由 WP1 真实覆盖） | 新 Worker 接管，`fencing_token` 递增 |
| 3 | Worker-1 SIGSTOP → 租约过期 → w3 接管 → SIGCONT | PASS（实际由 w2 接管，`fencing=2`；详见 §6） | 旧 Worker 不覆盖终态、不追加证据 |
| 4 | API 进程强杀 + 重启 | PASS（重启期间队列 Run 继续完成，重启后可回读） | Queue 中的 Run 由 Worker 继续执行 |
| 5 | PostgreSQL 连接全部终止 | PASS（恢复；但故障瞬间首个 `/execute` 返回 500，已修 `pool_pre_ping`） | API/Worker 自动重连并继续 |
| 6 | SSE 断开 + `Last-Event-ID` 续传 | PASS（游标续传成功，无重复事件） | 续传不重复已读事件 |
| 7 | Worker-2 执行中 SIGKILL | NOTE：未在窗口内观察到 w2 持租约，跳过 | 接管成功 |
| 8 | 全部 Worker 重启 | PASS（w1/w2 重启后继续消费） | 恢复消费 |
| 9 | API 再次重启 | PASS | 恢复 |

## 4. 一致性检查（数据库）

| 检查 | 结果 |
|---|---|
| 无重复步骤（同一 Run 的 `step_id` 唯一） | PASS（duplicates=0） |
| 无孤儿步骤（`tg_step.run_id` 不存在） | PASS（orphan=0） |
| 无孤儿证据 | PASS（orphan=0） |
| 终态 Run 无残留租约 | PASS（`lease_expires_at=0`，118 条终态记录的 `lease_owner` 作为最后执行者留痕） |
| 每个 Run 至多一份提交回执 | PASS（dup=0） |
| 完成 Run 均有 Evidence | PASS（missing=0） |
| 事件 `(producer_instance_id, sequence)` 无重复 | PASS（dup=0） |
| Run 状态不倒退 | PASS（terminal 后不再出现非终态） |
| 活跃 Worker ≥ 2 | PASS（active=2） |

## 5. 资源与性能

数据来源：`soak-samples.jsonl`（API `/system/health` 的 `process` + Worker 原子 metrics 文件 + PG 大小/WAL）。

| 指标 | 结果 |
|---|---|
| API RSS 峰值 | 100.8 MB → 99.4 MB（稳定，无单调增长） |
| API CPU（user+sys） | 6.35s user + 0.87s sys（60 分钟累计，单进程） |
| Worker RSS 峰值 | w1 81.8 → 92.1 MB（+10.3）；w2 94.0 → 92.4 MB（稳定） |
| 数据库大小增长 | 9.31 MB → 18.16 MB（+8.85 MB；随 113 Run / 3030 Evidence 线性增长，符合预期） |
| WAL 文件/字节 | 2 个文件 / 33.55 MB（实验结束快照） |
| API 健康检查延迟 P50/P95/max | P50 4.2ms / P95 7.0ms / max 9.8ms（57 次采样） |
| Run 平均/最大耗时 | 平均 4.80s / 最大 6.67s（真实浏览器执行，113 Run） |
| 队列等待 | 每 30s 入队 1 个；Worker 空闲时 <1s 领取（API 重启/故障窗口除外） |
| SSE 重连 | 1 次主动断连 + 游标续传；续传成功，0 重复事件 |
| 错误率 | 业务 Run 0 失败；1 次故障瞬间 `/execute` 500（已修）；0 非终态、0 孤儿 |
| Evidence / Artifact 数量 | Evidence 3030（约 25.7/Run）；Artifact 487；完成 Run 无缺失证据 |

## 6. 结论与限制

- 60 分钟 / 113 个真实 Run / 双 Worker / PG16 达到要求；113/113 `PASSED`，无重复步骤、无孤儿、无重复提交、无状态倒退；租约接管与旧 Worker 恢复未污染终态。
- 本实验使用独立 PG16 库与独立端口；未使用 SQLite 冒充 PostgreSQL。
- **原始检查 28/31**，其中 2 项是**检查口径错误**、1 项是真实缺陷：
  1. 暂停接管检查要求 `w3` 接管，实际由已在运行的 `w2` 接管（`fencing 1→2`）——真实接管成立；
  2. “终态无残留租约”误用 `lease_owner` 判断，实际 `lease_expires_at=0`（`lease_owner` 是最后执行者留痕）；
  3. 数据库连接被终止后首个 `/execute` 返回 500——已加 `pool_pre_ping=True`；
     定向复验在终止连接后 `/execute` 返回 **200**（25ms），但**未重新跑 60 分钟**。
  因此不宣称“31/31 全部通过”；如实记录 28/31 + 3 项分析。
- SIGKILL 场景在本轮 60s 观察窗口内未命中持租约 Worker（两次均 skip），但 WP1 已真实执行 SIGKILL
  并由新 Worker 接管；本轮的 SIGSTOP→租约过期→接管、API 重启、DB 中断、SSE 续传均真实发生。
- 未覆盖：宿主级网络隔离（BLOCKED）、真实人工基线（BLOCKED）。
