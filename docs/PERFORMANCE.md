# 性能与可靠性报告（本机实测）

> **RUN-010 更新（2026-11-24）**：RUN-009 基线不变（1/2/4 Worker 12.37/23.67/32.74 runs/min）。
> 新增开销：每浏览器实例 1 个 egress 代理线程（localhost，延迟可忽略）；
> WP8 完整闭环 2 轮真实执行 ~14s；Benchmark v4 单场景 0.1~22.8s。

> **RUN-009 更新（2026-11-24）**：新增多 Worker 资源监控与吞吐基线（`docs/RUN-009-WP5-PERFORMANCE.md`）。
> 方法：`libproc.dylib` 真实采样（不使用 ps/proc），6 个 Run 一次性入队，隔离库 + 隔离端口。
> 1/2/4 Worker：吞吐 **12.37 / 23.67 / 32.74 runs/min**，队列等待 12.43/5.35/2.19s，
> Worker+Chromium RSS 合计约 354/685/1176 MB，完成率 6/6；4W 边际收益递减。
> 下方 RUN-002 历史数据保留，不与本轮混用。

环境：macOS / Apple Silicon，Python 3.13.12，SQLite（本地文件），Chromium 159，单机模式（API + 内嵌 Worker）。
测量时间：2026-11-22。数值为本机实测，不代表生产容量规划。

## 1. API 延迟（10 次取中位数）

| 端点 | 中位数 | 最大 |
|---|---|---|
| `GET /system/health` | 9.2 ms | 32.3 ms |
| `GET /runs?limit=50` | 6.3 ms | 18.3 ms |
| `GET /evidence?limit=100` | 6.1 ms | 13.5 ms |
| `GET /testcases` | 1.4 ms | 4.9 ms |
| `GET /diagnoses?limit=50` | 1.3 ms | 12.2 ms |

说明：`/system/health` 含数据库 `SELECT 1` 与计数查询；证据列表含 JSON 反序列化与附件元数据。

## 2. 执行性能

| 指标 | 值 |
|---|---|
| 浏览器启动（进程内首次） | 约 300–400 ms |
| 单 Run（8 步，含截图/DOM/ARIA/trace） | 中位 3.3 s；p90 5.6 s |
| 单 Run 最大值（历史） | 34.3 s（含等待/超时路径，8 个采样异常场景） |
| 证据产出 | 12–28 份 / Run（约 0.4–1.2 MB） |
| Benchmark B（4 场景 × 3 次执行 + 诊断 + 图 + 探针） | 约 12 s |

## 3. 事件写入

| 指标 | 值 |
|---|---|
| 顺序追加 | 300 条 / 1.52 s ≈ **198 条/秒** |
| 并发（12 线程） | 序号无重复、幂等键无重复（单测） |

说明：每条事件一次事务 + 序号查询，SQLite 下 ~5 ms/条。生产建议：
批量提交、PostgreSQL + 连接池、或按 Run 维度异步写队列。

## 4. 诊断与图

| 指标 | 值 |
|---|---|
| 单 Run 规则诊断 | 约 31 ms（含证据反序列化） |
| 证据图构建（40 节点/39 边） | 约 20–40 ms |
| 探针（HTTP 状态查询） | < 20 ms |

## 5. 可靠性验证

| 场景 | 方法 | 结果 |
|---|---|---|
| 旧 Worker 提交被拒绝 | 单测：租约过期后新 Worker 接管，旧 token 提交 | `STALE_FENCING_TOKEN` |
| 租约过期回收 | `reap_expired_runs` → QUEUED，Worker 重新领取 | 单测 + `/queue/reap` |
| 进程重启恢复 | 关闭 Database 后重新打开，事件/证据/目录仍可读 | 单测（两个库文件） |
| 独立 Worker | `traceguard_worker.main --once` 执行真实 Run | COMPLETED / PASSED / 8 步 / 25 证据 |
| 浏览器资源释放 | 同进程连续执行 4 个 Run | 无 "Sync API inside asyncio loop"；进程退出无残留 |
| 幂等 | 重复 Task 键、重复事件键、重复晋升键 | 均返回已有结果，无副作用 |
| 证据完整性 | 篡改磁盘产物后读取 | 报 integrity 错误，不返回被污染内容 |
| 并发事件 | 12 线程同时 append | 序号唯一、幂等成立 |
| 故障注入可重复 | `RESET_DATA` 清理 + 重新注入，同一用例连跑 | 结果一致（PASSED 场景连续两次 PASSED） |

## 6. 已知瓶颈与风险

1. **SQLite 写入串行**：事件密集型场景吞吐受限（~200 条/秒）；1000 条以上事件的 Run 会明显变慢。
2. **心跳已实现但未压力验证**：执行期间每 ttl/3 续租，被接管会停止后续步骤；尚未做「长任务 + 双 Worker 竞争」压测。
3. **前端轮询**：执行中每 2.2–2.5 s 轮询；未实现 SSE 推送，页面多开时请求量线性增长。
4. **证据表增长**：无归档/分区；本地库运行一周后需手工清理（未实现自动保留策略）。
5. **单进程内嵌 Worker**：API 与执行争抢 GIL；高并发需拆分进程（已支持独立 Worker）。
6. **未做压测**：无 100+ 并发 API、无多 Worker 竞争压测、无内存增长长稳测试。

## 7. 建议的下一步

1. 事件批量写入 + PostgreSQL；
2. Worker 心跳与执行超时看护；
3. SSE / WebSocket 替代轮询；
4. 证据保留策略（按 workspace / 时间 / 大小）；
5. 引入 k6/Locust 做 API 压测，形成容量基线。

## RUN-008 补充（2026-11-24）

- CPU/RSS 采集仍为 `source=liveness_only`（沙箱禁止 `ps`、无 `/proc`），不伪造资源数字。
- 4 Worker 并发实测：峰值并行 Run 数 4，24.22s 完成 4 Run，24/24 检查（14 项业务 + DB 完整性）。
- 真实 SIGKILL 故障恢复：执行 2.77s 后击杀，接管延迟见 `worker-fault-recovery.json`；17/17 检查。
- 60 分钟长稳与 Benchmark v3 本轮实验运行中；结论以 `docs/RUN-008-FINAL-REPORT.md` 定稿为准。
