# RUN-011-R1 WP7：B-17 安全阻塞 — 正式安全批准申请材料

> 状态：**BLOCKED（保持）** — 未获项目负责人安全批准前不实施、不重试、不换工具规避。
> 本文件是提交给项目负责人的**批准申请材料**，不是实施记录。
> 依据：AgentDR 安全拦截（B-17，`info_theft_steal_env_keys`，RUN-008 两次拦截记录见
> `.workbuddy-run/blockers.md`）；RUN-011-R1 本轮**零次**触碰被拦截操作。

## 1. 背景与拦截回顾

| 项 | 内容 |
|---|---|
| 被拦截操作 | `Write` 创建 `scripts/worker_fault_matrix_run008.py`（Worker 四故障场景矩阵脚本） |
| 拦截原因 | `info_theft_steal_env_keys`：源码含 `subprocess.Popen` + `env={**os.environ, ...}` 模式（父进程完整环境可能含真实凭据，整体传给子进程被判定为凭据外传特征） |
| 第二次尝试 | 按授权改为显式最小环境白名单（无 `**os.environ`）重写，**仍被拦截**（2026-11-24 13:31） |
| 处置 | 按指令不再重试、不改写参数规避、不拆分写入、不换工具；4 个补充故障场景标 BLOCKED |
| RUN-011-R1 本轮 | **未执行任何被拦截或等价操作**；本文件仅整理批准所需信息 |

## 2. 申请实施的能力（最小测试范围）

目标：补齐 Worker 故障恢复矩阵的 4 个真实场景（B-17），与已通过的
`worker-fault-recovery.json`（17/17）和 `worker-concurrency.json`（4 Worker 并行）互补：

| # | 场景 | 验证点 |
|---|---|---|
| 1 | Worker 领取 Run 后立即退出（SIGKILL 于 RUNNING 中） | 租约到期后其它 Worker 可重新领取；fencing_token 防止双写 |
| 2 | 租约过期（TTL 内不续租） | lease_expires_at 到期回收；attempt 递增；无证据丢失 |
| 3 | API 进程重启（Worker 在跑） | Worker 续租/领取不中断；Run 状态最终一致 |
| 4 | Worker 全量重启（3 个同时杀 + 重启） | 队列无死信；恢复后所有 QUEUED/RUNNING Run 终态可判定 |

**范围限定**：仅 TraceGuard 自有进程（API/Worker，本地 127.0.0.1）；
仅本项目数据库（`traceguard_run011_*` 测试库）；不触达任何外部服务。

## 3. 需要的进程操作

| 操作 | 目标 | 说明 |
|---|---|---|
| 启动 | `.venv/bin/python -m traceguard_worker.main`（独立 OS 进程 ×3） | 与 RUN-011 已验证拓扑相同 |
| 终止 | 对**本轮自启**的 Worker 进程发 SIGKILL / SIGTERM | 只杀本轮脚本记录了 PID 的进程；不按名字批量杀 |
| 重启 | 同一启动命令重新拉起 | 验证恢复语义 |
| 观测 | 读 `/api/v1/queue/stats`、`/api/v1/runs/{id}`（HTTP） | 不读进程环境、不读 /proc/environ |

**明确不做**：不 ps/grep 回显含凭据命令行；不读 `/proc/*/environ`；不枚举系统进程。

## 4. 需要的环境变量（显式白名单，不含任何真实凭据）

脚本构造子进程环境时**只允许**以下键（其余一律不传，包括 PATH 也只给最小值）：

| 键 | 值来源 | 敏感性 |
|---|---|---|
| `TG_DATABASE_URL` | 本地测试库 DSN（`postgresql+psycopg://traceguard@127.0.0.1:55432/...`，无密码） | 非敏感 |
| `TG_ARTIFACT_DIR` | `.traceguard/run011-r1/.../artifacts` | 非敏感 |
| `TG_TEAMFLOW_BASE_URL` | `http://127.0.0.1:8111` | 非敏感 |
| `TG_INLINE_WORKER` | `0` | 非敏感 |
| `TG_WORKER_LEASE_TTL_S` | 测试值（如 `5`，缩短租约以便复现过期） | 非敏感 |
| `TG_WORKER_POLL_INTERVAL_S` | 测试值 | 非敏感 |
| `TG_LOG_JSON` | `1` | 非敏感 |
| `TG_WORKER_ID` | `run011r1-fault-*` | 非敏感 |
| `PATH` | `/usr/bin:/bin` 最小集 | 非敏感 |
| `PYTHONPATH` | 项目 packages 路径 | 非敏感 |

**绝不包含**：`DEEPSEEK_API_KEY`、`TG_DEEPSEEK_API_KEY`、`GITHUB_TOKEN`、
`AWS_*`、`SSH_*` 与任何 `*SECRET*/*TOKEN*/*KEY*/*PASSWORD*`；
不使用 `**os.environ`、`dict(os.environ)`、`os.environ.copy()`。

## 5. 隔离条件

- 数据库：独立测试库（本轮创建的 `traceguard_run011_r1_*` 或新建 `*_fault` 库），不碰任何既有库；
- 网络：仅 127.0.0.1（API/TeamFlow/PG 本地端口），不发起外部连接；
- 文件系统：产物只写 `.traceguard/run011-r1/` 与 `docs/assets/run011-r1/`；
- 进程：只操作脚本自己 `Popen` 记录了 PID 的子进程；
- 时间窗：单场景 ≤ 2 分钟，总窗 ≤ 15 分钟，避免与其它任务争资源。

## 6. 可验证的负向测试（批准后先跑这些）

| # | 负向断言 | 预期 |
|---|---|---|
| N1 | 脚本运行前后 `env` 输出不含任何 `*KEY*/*TOKEN*/*SECRET*` 值 | 0 命中 |
| N2 | 子进程 `/proc/<pid>/environ`（本机 macOS 用 `ps -p -o command` 不适用；改为脚本内 assert）只含 §4 白名单键 | 键集相等 |
| N3 | 未授权目标（`http://127.0.0.1:9555`）在故障场景中依旧零成功响应 | 与 WP5 负向口径一致 |
| N4 | 故障场景全部结束后 DB 中无孤儿 RUNNING Run | 0 孤儿 |
| N5 | 审计哈希链完整 | `intact=true` |

## 7. 清理与恢复方式

1. 场景结束即 `SIGTERM`→`SIGKILL` 兜底收掉本轮全部子进程（按 PID 列表）；
2. 清理本轮测试数据（DELETE 本轮 workspace 行 / drop 本轮专用库）；
3. 产物 JSON 落 `docs/assets/run011-r1/worker-fault/`（只含运行证据，不含环境值）；
4. 恢复现场：Worker 以标准配置重启（长租约、正常轮询）；
5. 全量测试回归（663+）确认无残留影响。

## 8. 当前状态与请求

- **状态**：BLOCKED。以上材料齐备，等待项目负责人**书面批准**（批准范围=§2–§7）。
- 批准前：不实施、不重试被拦截写入、不换工具、不拆分操作、不导出环境密钥。
- 已有替代证据（不依赖 B-17）：`docs/assets/run008/wp3/worker-fault-recovery.json`
  （SIGKILL 中途恢复 17/17）与 `worker-concurrency.json`（4 Worker 并行）——
  B-17 的 4 个补充场景仍是缺口，如实保持 BLOCKED，不冒充通过。
