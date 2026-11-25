# RUN-013 WP1：独立 Worker 部署形态的真实 UI 全链路验收

> 日期：2026-11-25（CST）｜ 分支：`workbuddy/run013-production-reliability`
> 证据：`docs/assets/run013/wp1/ui/independent-worker-acceptance.json`
> 脚本：`scripts/business_loop_ui_acceptance_run013.py`（复用 RUN-012 闭环，强制独立 Worker）

---

## 1. 部署形态

| 组件 | 形态 |
|---|---|
| API | 独立 `uvicorn` 进程，`TG_INLINE_WORKER=false` |
| Worker | 2 个独立 `traceguard_worker.main` 进程（`r13-w1` / `r13-w2`） |
| TeamFlow | 独立进程 + 独立 SQLite |
| 数据库 | 独立 SQLite（WAL + busy_timeout），独立 Artifact 目录 |
| 浏览器 | 真实 Chromium（Playwright 159.0.8154.6） |
| 认证 | 真实 Cookie + CSRF；三角色（qa-engineer / qa-lead / platform-admin） |
| 租约 | TTL 6s（为故障注入缩短；生产默认 120s） |

服务端配置回读 `worker.inline=false`，API 不启动内嵌执行器；所有 Run 均由独立进程 claim。

## 2. 业务闭环结果（真实 UI + 服务端回读）

完整链路：Intent → TestCase → Oracle → 绑定 → 审核 → Run → 独立 Worker →
Diagnosis → Repair → Sandbox → 人工审批 → Promotion → Rollback。

- 真实故障 `TF-UI-002` 触发 `FAILED` Run，诊断与修复链继续；
- 沙箱在故障清除后真实复跑 `PASSED`；
- `promoted_version=2`，回滚留痕；
- 每步均通过 API 回读服务端状态，不是仅看 Toast。

**结果：56 条记录 = 55 PASS / 0 FAIL / 1 NOTE**（NOTE 为「诊断 ID」记录项）。

关键断言：

| 断言 | 结果 |
|---|---|
| API 不启动内嵌 Worker（`worker.inline=false`） | PASS |
| Run 由独立 Worker 领取，`lease_owner=r13-w*`，`fencing_token>0` | PASS |
| 真实浏览器执行（`observer_mode=playwright`） | PASS |
| 步骤轨迹与证据可回读 | PASS |
| Oracle 门禁、审核、晋升、回滚未被绕过 | PASS |

## 3. 故障场景（真实进程）

| 场景 | 结果 | 证据 |
|---|---|---|
| Worker 启动失败（数据库不可达） | PASS | 非零退出，API 与队列不受影响 |
| Worker 正常退出（SIGTERM） | PASS | 退出码 0，可重启继续消费 |
| 执行中 SIGKILL | PASS | 进程 `-9`，API 不假报成功（Run 保持 RUNNING/NOT_EVALUATED） |
| 租约到期 + 新 Worker 接管 | PASS | `r13-w2` 接管，`fencing_token 1→2` |
| 旧 Worker 恢复（SIGSTOP→SIGCONT） | PASS | 已被 `r13-w3` 接管；旧 Worker 不覆盖终态、不追加证据 |
| 过期提交 | PASS | 旧 Worker 进程存活但提交被 Fencing 拒绝，仅记录 `lease_lost_abandon` |
| API 强制终止 + 重启 | PASS | Worker 不依赖 API 继续消费；重启后回读终态 |
| 客户端未收到响应 | PASS | 重启后回读已完成 Run，无重复业务结果 |
| 双 Worker 并发 4 个 Run | PASS | 4/4 终态；步骤唯一，无双重提交 |

## 4. 验收中发现并修复的真实缺陷

### 4.1 旧 Worker 恢复后重复写证据（高）

**现象**：首次验收 `W1-⑮` 失败——旧 Worker 被 SIGSTOP、租约被新 Worker 接管并完成后，
恢复执行的旧 Worker 仍继续写入 19 条重复 Evidence（19→38），虽然提交被 Fencing 拒绝。

**根因**：`RunPipeline` 只在**步骤开始时**检查异步心跳的 `heartbeat.lost`；
心跳线程每 `ttl/3` 才更新一次，旧 Worker 恢复后可在标记丢失前跑完全部步骤，
`record_step` 按 `run_id:step_id` 幂等，但 Evidence 是追加写入，导致重复。

**修复**（提交内）：

1. `TaskService.lease_valid(run_id, worker_id, fencing_token)`：同步校验 owner + token + 非终态；
2. `RunPipeline._ensure_lease()`：步骤开始、观测返回后、状态证据写入前各校验一次；
3. 租约丢失时走 **abandon 路径**：不再发 `run.failed` 事件、不再提交结果，直接返回
   `LEASE_LOST`；
4. `submit_result` 的 `STALE_FENCING_TOKEN` 同样按 abandon 处理，避免向已接管 Run 追加完成事件；
5. 单元测试：`tests/unit/test_tasks.py::test_lease_valid_is_synchronous_guard_for_stale_worker`。

**复验**：第三次运行 `W1-⑮` PASS（Evidence 19→19），全量 55 PASS / 0 FAIL。

## 5. 结论与边界

- 独立 Worker 部署形态下的真实 UI 全链路**已验证**（SQLite）。
- PostgreSQL 16 下的双 Worker 长稳见 WP4。
- 本工作包未覆盖：宿主级网络隔离（BLOCKED）、真实人工基线（BLOCKED）。
