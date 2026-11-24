# RUN-009 WP5：资源监控与性能基线

> 状态：IMPLEMENTED / VERIFIED（本机 macOS，真实采样）；样本量小，不做统计显著性宣称。
> 生成时间：2026-11-24。权威起点 `cb80591`。
> 证据：`docs/assets/run009/wp5/perf-summary.json`、`perf-{1,2,4}w.json`、`perf-{1,2,4}w-samples.jsonl`
> （33 行真实采样帧）、`resource-self-test.jsonl`、隔离库 `.traceguard/run009/wp5/perf-*/`。

## 1. 采样方法（不使用 ps / /proc / psutil）

RUN-008 B-13 已确认沙箱禁止 `ps` 且 macOS 无 `/proc`；本轮未绕过，也未读取 `.env`。

- 进程 CPU/RSS：`ctypes` 调用 Apple 系统库 `/usr/lib/libproc.dylib` 的
  `proc_listpids` + `proc_pidinfo(PROC_PIDTBSDINFO/PROC_PIDTASKINFO)`；
  CPU% = 两次采样累计 CPU 纳秒差 / 墙钟差 × 100（多核可 >100%）；
  RSS = `pti_resident_size` 字节换算 MB。
- Chromium：从 API/Worker PID 沿父子关系找后代进程，按名称匹配 Chromium/headless_shell；
  浏览器实例数 = 非浏览器子进程的根进程数。
- API 延迟：负载生成器客户端侧 wall-clock（POST 执行 + 轮询 GET），记录 P50/P95/P99。
- SQLite：只读连接 + `os.stat` 获取文件/WAL/SHM 大小、`journal_mode`、`busy_timeout`、
  `synchronous`、迁移版本、表行数，以及 `tg_event` / `tg_evidence` 最近 500 行真实查询耗时。
- Evidence 存储：目录大小统计（内容寻址 `.traceguard/artifacts`）。
- 子进程环境：显式白名单（PATH + 非敏感 TG_* 变量），**不复制父进程完整环境**（B-17 禁令）。

## 2. 多 Worker 性能（6 个 Run 一次性入队）

隔离环境：API `:8030`、TeamFlow `:8012`、独立库 `.traceguard/run009/wp5/perf-{n}w-*/traceguard.db`；
负载用例 `tcase_tf_create_normal`（真实 Chromium 执行，全 COMPLETED，无 4xx/5xx）。

| 配置 | 墙钟 | 吞吐 | 队列等待均值/最大 | Run 完成率 | API P50/P95/P99 | Worker CPU% 均值 | Worker RSS 均值 | Chromium RSS 均值 | 浏览器实例峰值 |
|---|---:|---:|---|---:|---|---:|---:|---:|---:|
| 1 Worker | 29.10 s | **12.37 runs/min** | 12.43 / 24.29 s | 6/6 | 2.08 / 4.16 / 6.54 ms | 0.06 | 83.8 MB | 269.9 MB | 1 |
| 2 Worker | 15.21 s | **23.67 runs/min** | 5.35 / 10.33 s | 6/6 | 1.90 / 3.72 / 5.71 ms | 0.12 | 164.1 MB | 520.9 MB | 2 |
| 4 Worker | 11.00 s | **32.74 runs/min** | 2.19 / 6.13 s | 6/6 | 1.86 / 5.30 / 17.17 ms | 0.25 | 290.2 MB | 885.4 MB | 4 |

解读（与指令要求一致，不做过度宣称）：

- **吞吐随 Worker 数量真实提升**（1→2 约 +91%，2→4 约 +38%），收益来自浏览器执行并行化；
  但 4 Worker 时边际收益明显下降，说明瓶颈开始从单 Worker 执行转向调度/启动开销。
- **RSS 与 Worker 数近似线性增长**（Worker 进程 + Chromium 合计：1W ≈ 354 MB → 4W ≈ 1176 MB）；
  横向扩容前必须把内存预算纳入约束。
- 队列等待随 Worker 增加而下降（12.43s → 2.19s），符合预期；API 侧 P50 稳定在 2ms 左右，
  P99 在 4W 时 17ms，未出现明显 API 瓶颈。
- 8 Worker 未执行：资源约束下 6 个样本已能看出边际收益递减，继续加 Worker 只会增加内存与噪声。

## 3. 数据库与证据存储观测

- SQLite 全程 `journal_mode=wal`、`busy_timeout=30000`、`synchronous=NORMAL`；6 Run 后
  DB ≈ 1.01 MB、WAL ≈ 4.13 MB、`tg_event=78` / `tg_evidence=168` / `tg_step=48`；
  4W 配置结束后 WAL 4.29 MB，未见 WAL 无限增长。
- 最近 500 行 Event / Evidence 查询耗时（真实执行）均在毫秒级；本轮未出现需要引入 Redis、
  S3 或专用队列的证据——**不为架构复杂度增加依赖**。
- 长事务 / PG 并发 / 锁等待：本机无 PostgreSQL（历史已知边界，CI PG16 作业已覆盖平台级用例）；
  本轮未观察到 SQLite 写竞争失败（非 2xx = 0），但未做高强度并发写压力测试，不宣称已验证极端锁竞争。

## 4. 状态汇总

| 项目 | 状态 |
|---|---|
| libproc 真实 CPU/RSS 采样（API/Worker/Chromium） | IMPLEMENTED / VERIFIED |
| API P50/P95/P99 与队列等待 | VERIFIED |
| 1/2/4 Worker 吞吐与内存基线 | VERIFIED（n=6/配置） |
| SQLite WAL/文件增长与查询探针 | VERIFIED |
| PostgreSQL 并发/长事务/锁等待 | PARTIAL（本机无 PG，CI 覆盖平台级） |
| 8 Worker | NOT_RUN（资源约束，边际收益已可见递减） |
| Redis / S3 / 专用队列引入 | NEEDS_DECISION（当前实测无必要） |
