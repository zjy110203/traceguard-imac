# RUN-018 WP1：CI 基础质量门禁修复

> 时间：2026-11-27（RUN-018-R1 轮）
> 状态：**PARTIAL**（本地全部通过；最终以最新 HEAD 的真实 CI 结果为准）

---

## 0. 基线与历史脉络

- 基线 HEAD：`7674842` → 本轮提交：`2f26686`（Ruff）→ `0e69f44`（SIGKILL 确定性）→ 本文档提交（readiness + Summary）。
- RUN-017 遗留 B-28：Nightly ui-acceptance 连续失败（#29~#33），本地通过、CI 失败，因无 `gh` CLI/凭据无法读取 CI 日志。
- 本轮指令给出的事实：PR Gate #85 Ruff 16 errors；Nightly #37 Backend Full 1 FAIL + UI Acceptance 8 FAIL（Run 始终 QUEUED、lease_owner 空、fencing_token=0）。
- 本轮同样无 gh 凭据，GitHub API 匿名访问被限流（403），无法下载 `ui-acceptance-evidence` 工件；采用**结构性修复 + 本地复现**替代日志定位。

## 1.1 Ruff（16 errors → 0）

PR Gate #85 的 16 项 Ruff 错误全部修复，未关闭任何规则、未全局忽略：

| 类型 | 文件 | 修复方式 |
|---|---|---|
| I001 ×5 | worker main.py / alerts.py / test_alert_engine / test_probe_contract / test_state_read_tristate | `ruff check --fix` 自动排序 |
| F401 ×4 | double_submit.py(uuid) / test_gc_concurrency(json, pytest) / test_double_submit(pytest) / test_probe_cleanup_security(pytest) | 删除真正未使用的 import |
| F541 ×2 | double_submit.py:303,304 | 去掉无占位符 f-string |
| F821 ×1 | monitoring.py:269 `ArtifactStore` 未定义 | `TYPE_CHECKING` 下类型导入，避免循环依赖 |
| F841 ×3 | test_artifact_gc_concurrency_run018.py | `scan` 无用调用删除；两处 `collect_garbage` 结果补充 delete_row 断言 |

同时 `ruff format` 对齐 17 个文件（`format --check` CI 步骤同门禁）。
修复后 RUN-018 相关 100 项测试全部通过（提交 `2f26686`）。

## 1.2 多进程 SIGKILL 测试（确定性通过）

原缺陷：`attempts=4 + delay=0.01` 下子进程约 0.04s 完成，父进程固定 `sleep(0.6)`
后检查存活 → `killed_pid = 0` → 断言失败（非确定性）。

修复方案（`scripts/probe_budget_multiprocess.py`，提交 `0e69f44`）：

1. 目标子进程完成第一次有效预算预留后写出 **READY 证据文件**（pid / reserved / ready_at）；
2. 子进程进入明确等待状态（轮询 release 文件，最长 120s）；
3. 父进程轮询 READY 文件（60s 超时 → 确定性失败，不 SKIP）；
4. 父进程确认子进程仍存活（`proc.is_alive()`）；
5. 父进程执行真实 `SIGKILL`；
6. kill 前回读共享数据库记录 `probes_used_before_kill`；
7. 其余子进程在 kill 之后启动并继续工作；
8. 断言 `probes_used >= probes_used_before_kill > 0`（预算未重置）+ 存活子进程继续扣减；
9. 报告保存真实 `killed_pid`、`killed_exitcode`（必须 -9）、`killed_reserved`、最终数据库快照。

**关键实现细节**：不与会被 SIGKILL 的进程共享任何 `multiprocessing` 锁——
实测首次实现中，被杀进程持有 mp Event 的 POSIX 信号量导致父进程
`Event.set()` 永久死锁（faulthandler 定位），改为纯文件轮询同步。
`killed_exitcode == -9` 断言证明真实进程被杀死。测试 3/3 确定性通过（约 8s）。

## 1.3 Nightly UI Acceptance：独立 Worker 不领取 Run

Nightly #37 事实：两个 Worker 进程已启动、API 内嵌 Worker 关闭、Run 始终
QUEUED、lease_owner 空、fencing_token=0、无步骤与 Evidence。

**根因分析**（无 CI 日志，结构性定位 + 本地复现）：

- `fleet.alive()` 只证明进程存活，不证明 Worker 已可消费任务。#37 的失败形态
  （进程在、队列不动）正是这一观测盲区：Worker 启动失败/挂起/连错库时，
  业务链路照常推进，Run 永远 QUEUED，直到各步骤超时。
- 本地 3 次完整 core profile 验收全部通过（§1.5），业务链路与 Worker 领取逻辑本身健康。

修复（`scripts/business_loop_ui_acceptance_run012.py`）：

1. 新增 `WorkerFleet.ready()`：通过 `tg_worker_heartbeat` 校验——Worker ID 存在、
   PID 与进程匹配、mode=standalone、status ∈ {IDLE, RUNNING}、心跳新鲜（≤10s）、
   数据库归属一致（能读到心跳 ⇒ 连接的共享库正确）。
2. 主链路：先 alive（20s）→ 再 ready（45s）；失败 → 返回码 3，并对每个 Worker
   调用 `WorkerFleet.log_tail()` 保留最近 40 行 stderr/stdout 证据。不使用固定延时，
   不启用 API 内嵌 Worker 绕过。
3. 故障段 W1-③ / W1-⑤ / 双 Worker 并发段（r13-w4）同样升级为 readiness 检查。

## 1.4 UI Job Summary 历史报告读取错误

原缺陷：`sorted(files)[-1]` 在含历史提交证据的目录里读到 `run017-ui-full-loop.json`
（历史）而非本次的 `run017-ui-core.json`（字典序 core < full）。

修复：

1. `business_loop_ui_acceptance_run012.py` 新增 `--run-id` / `--profile` 参数，
   报告 JSON 写入 `run_id` / `profile` / `git_head`（CI 用 `GITHUB_SHA`，本地 `git rev-parse HEAD`）。
2. `business_loop_ui_acceptance_run017.py` 支持 `TG_UI_ACCEPTANCE_OUT` /
   `TG_UI_ACCEPTANCE_RUN_ID` 环境变量覆盖输出目录。
3. `nightly-gate.yml`：验收与 Summary 统一使用
   `docs/assets/run018/ci/${{ github.run_id }}/ui`（本次 run 专属目录）；
   Summary 只读该目录；目录为空 → 显示 **NOT_PRODUCED**，绝不回退历史报告。
4. artifact 名改为 `ui-acceptance-evidence-${{ github.run_id }}`，
   上传本次报告、截图、Worker 日志与 `_runtime` 运行态。
5. Summary 明确包含 run_id / profile / HEAD / total / PASS / FAIL / NOTE。
6. 历史 RUN-017 证据（`docs/assets/run017/wp5/ui`）保持不变；
   `docs/assets/run018/ci/**` 运行态目录加入 `.gitignore`，不入库。

## 1.5 本地 UI 验收（3 次，全部通过）

| 轮次 | 输出 | total | PASS | FAIL | 独立 Worker 领取 | fencing |
|---|---|---|---|---|---|---|
| local1 | `docs/assets/run018/ci/local1/ui/run017-ui-core.json` | 37 | 36 | 0 | r13-w1 | 1 |
| local2 | `docs/assets/run018/ci/local2/ui/run017-ui-core.json` | 37 | 36 | 0 | r13-w2 | 1 |
| local3 | `docs/assets/run018/ci/local3/ui/run017-ui-core.json` | 37 | 36 | 0 | r13-w1 | 1 |

（total=37 = 36 PASS + 1 NOTE「诊断 ID」记录项。）

每次均验证：诊断生成、Repair 提案（VALIDATED）、Sandbox 真实执行（PASSED）、
Approval、Promotion（版本 2）、Regression（真实回归 Run PASSED）、Rollback（留痕）；
故障注入 TF-UI-002 真实生效与清除。

**本地通过不代表 GitHub Actions 通过。最终以最新 HEAD 的真实 CI 结果为准
（核对每个 Job 的 head_sha，见 RUN-018-FINAL-REPORT.md）。**

## 遗留与边界

- 无法读取 Nightly #37 CI 工件（无 gh CLI/凭据，匿名 API 403 限流）；
  readiness 门禁保证同类故障下次 CI 会带完整 Worker 日志在业务链路前快速失败。
- SIGKILL 测试的 CI Linux `os.kill` 语义与本地 macOS 一致（`exitcode == -9`），
  最终需 CI 实跑确认。
