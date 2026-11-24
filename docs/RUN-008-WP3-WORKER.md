# RUN-008 WP3：Worker 并发与故障恢复

- 本文件只记录真实 OS 进程 / 真实 SQLite / 真实 TeamFlow 的实测结果。
- 原始证据：`docs/assets/run008/wp3/`；执行日志：`logs/run008/worker-*.log`。
- 所有测试使用隔离库 `.traceguard/run008/wfr*/`，不触碰正式库。

## 1. 已验证（VERIFIED）

### 1.1 四 Worker 并发（`worker-concurrency.json`）

- 新增脚本 `scripts/worker_concurrency_run008.py`（未提交，属保留的未提交修改之一）。
- `--runs 4 --procs 4`：4 个独立 Worker OS 进程，各自 PID 不同且全程存活。
- **峰值并行 Run 数 = 4**（`peak_parallel_runs`），说明多 Run 真正并行执行而非串行。
- 14/14 checks 通过，`passed=true`：单 Run 单 owner、fencing token 有效、无重复 step/event、
  无孤立 step、SQLite `quick_check` 正常、全部 Run 有报告与证据。
- 耗时 24.22s。

### 1.2 执行中途真实 SIGKILL 故障恢复（`worker-fault-recovery.json`）

- 使用既有 `scripts/worker_fault_recovery.py`，4 个独立 Worker，租约 TTL 6s。
- 真实信号：`killed_pid=80665`（`wfr-w1`），在 Run 已执行 **2.77s** 后由 `os.kill(SIGKILL)` 击杀；
  非"最终只看 COMPLETED"的伪验证。
- 接管证据：同 Run、新 owner、`fencing_token` 严格递增、`attempt` 不变；旧 Worker 的陈旧提交被拒绝。
- 17/17 checks 通过，`passed=true`；无重复 event、无孤立 step、终态报告齐备。
- **披露**：该证据 JSON 内 `round` 字段仍为 `RUN-007-RESUME`（脚本硬编码标签），
  但文件是**本轮 RUN-008 真实重新执行**生成的原始产物；未改写原始证据。

## 2. BLOCKED / 跳过（B-17）

原计划补充的 4 个故障场景：

| 场景 | 状态 | 说明 |
|---|---|---|
| `post_claim_exit`（领取后、提交前退出） | **BLOCKED / 跳过** | 见下方安全拦截 |
| `lease_expiry`（人为令租约过期后接管） | **BLOCKED / 跳过** | 同上 |
| `api_restart`（执行中 SIGKILL API 并重启） | **BLOCKED / 跳过** | 同上 |
| `fleet_restart`（全部 Worker 退出后新集合接管） | **BLOCKED / 跳过** | 同上 |

**拦截原因**：拟创建的 `scripts/worker_fault_matrix_run008.py` 中 3 处以
`{**os.environ, ...}` 构造子进程环境，把父进程完整环境变量（可能含凭据）整体传给
会访问本地服务的子进程，触发 BuddySentinel `info_theft_steal_env_keys`。
文件**未创建**，无凭据被读取/打印/外传。

**处理**：不重试、不改参数规避、不改安全策略；按负责人指令记录阻塞并跳过。
已确认替代实现（仅传 8 个 `TG_*` 白名单变量，不需要真实凭据）记录在
`.workbuddy-run/blockers.md` B-17，留待后续受信任流程实施。

**不重复实现**：陈旧 Worker 提交拒绝已由 1.2 的 `stale_write_rejected` 严格验证，
补充矩阵不覆盖该项。

## 3. 结论

WP3 核心能力（多 Run 并行、单 Run 单 owner、fencing/租约接管、执行中途 SIGKILL 恢复）
已由本轮真实进程证据验证；补充故障矩阵 4 场景因安全边界限制**明确跳过**，不计为通过。
