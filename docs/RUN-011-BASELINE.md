# RUN-011 基线审计（WP0）

> 审计时间：2026-11-25 01:10 CST。执行体：WorkBuddy（RUN-011）。
> 指令来源：`指令12.txt`（RUN-011: Full-Stack Closed-Loop Verification & Intelligence Maturation）。
> 仓库：`https://github.com/zjy110203/traceguard-imac`；工作目录 `/Users/dlp--test/Desktop/traceguard`。

## 1. Git 基线

| 项 | 指令要求 | 实际值 | 结论 |
|---|---|---|---|
| 仓库 | traceguard-imac | `origin = https://github.com/zjy110203/traceguard-imac.git` | ✅ |
| main | `ac2876ecef779b0bed900bdf3b5361ce1d2fa3d6` | `ac2876ecef779b0bed900bdf3b5361ce1d2fa3d6` | ✅ 一致 |
| RUN-010 分支 | `workbuddy/run008-quality-hardening`，HEAD `b470518…` | 本地与 `origin` 均为 `b470518cb13b9428925a739badb3b62b30a97a9b` | ✅ 一致且同步 |
| merge-base(HEAD, main) | —— | `ac2876e`（HEAD 完全包含 main，纯前进） | ✅ |
| 工作区 | 干净 | `git status` 无改动 | ✅ |
| RUN-011 分支 | `workbuddy/run011-full-loop` | 从 `origin/workbuddy/run008-quality-hardening` 创建，HEAD=`b470518` | ✅ |

未执行 `reset --hard` / `clean` / 强推 / 历史改写；未合并任何 PR。

## 2. PR 状态与统计交叉核对

本机无 `gh` CLI，且**不读取任何凭据**（不使用 `git credential fill` 导出 token），因此 GitHub API 侧
状态无法在本轮直接查询。可本地复核的部分已复核：

| 项 | 指令给定（PR #2 API 统计） | 本地重算 `git diff ac2876e...b470518` | 结论 |
|---|---|---|---|
| commits | 65 | **65**（`git rev-list --count`） | ✅ 一致 |
| changed files | 696 | **696** | ✅ 一致 |
| 行数 | +76,771 / −919 | **+76,771 / −919** | ✅ 一致 |

结论：指令中的 PR 统计与当前 HEAD 一致，**不是过期数字**；PR #2 保持 open（本轮未合并、未修改）。

### 2.1 发现的过期/错误统计（指令 §0.1 要求交叉核对）

RUN-010 CLOSEOUT 报告 §6 写「`probes_per_sample`（所有臂）= 0」，但归档产物
`docs/assets/run010/benchmark/benchmark-v4.json` 的实际值是：

| 指标（round0/round1 相同） | CLOSEOUT 报告 | 归档 JSON 实际 | 判定 |
|---|---|---|---|
| `probes_per_sample[A/C/D]` | 0 | **0.0000**（0/14） | ✅ 一致 |
| `probes_per_sample[E/F/G]` | 0（"所有臂"） | **0.9286**（13/14） | ❌ 报告错误 |
| `probe_feedback_rate[F/G]` | 未列 | **0.4615**（6/13） | 报告缺失 |

但更关键的问题不是数字，而是**这些"探针"没有真正被执行**：

- `probes_used_service`（真实 `ProbeService.plan_and_probe` 执行数）在 14 个样本中是 **0 或 1**；
- E/F/G 臂的 `probes_used` 来自 `_probe_signals_from_replays()` —— 从**已落库的 Run 证据**重建
  `ProbeSignal`，属于"二次解读已有证据"，不是"新执行探针并产生新证据"；
- 因此 RUN-010 v4 的 Probe 消融**测量无效**，`primary_cause_correct[F_minus_G]=0/14` 也不能解释为
  "时间对齐无价值"。

本轮 WP2 的修复与重新验证见 `docs/RUN-011-WP2-ACTIVE-PROBE.md`。

## 3. 环境基线（本机实测）

| 项 | 值 | 证据 |
|---|---|---|
| Python | 3.13.12（`.venv`） | `python -V` |
| Node | 22.22.2 | `node -v` |
| Playwright Chromium | 159.0.8154.6（bundled） | `/system/browser?probe=true` |
| PostgreSQL | **16.16**（Postgres.app 16，`/tmp/traceguard-f2/pgdata`，端口 55432） | `SHOW server_version` |
| Docker / brew / psql（系统） | 均不存在 | `which` 全部 not found |
| LLM | DeepSeek（项目 `.env`，`TG_LLM_PROVIDER=deepseek`） | 探索/诊断真实调用，见 WP1 证据 |
| 前端 | `apps/web/dist`（正式构建产物，2026-11-24 21:55） | `GET /` 返回 SPA |

PG 的获取方式沿用 RUN-007 记录（Postgres.app DMG 内二进制 + `/tmp` 隔离实例），**不是系统服务**；
本轮在其上新建独立数据库，不复用开发/历史数据。

## 4. 本轮开始时的真实状态（RUN-010 收尾结论摘要）

- 已完成：认证/RBAC、BrowserManager+Egress、Task 终态幂等、Diagnosis v2、Evidence Graph、
  Benchmark v4（14 场景 × 2 轮）、前端 13 路由 axe、供应链治理、**进程内**完整业务闭环。
- RUN-007 完成过「独立 API + 4 Worker + SQLite」的 E2E，但 **RUN-010 架构（认证 + Egress + Repair
  幂等 + 诊断 v2）从未在独立部署拓扑下跑通完整闭环**。
- RUN-010 WP8 的闭环是**进程内 TestClient**（`tests/e2e/test_full_loop_run010.py`），不满足本轮
  §1.1「禁止用 TestClient 作为最终验收」。
- Benchmark v4 的 Probe 臂无效（见 §2.1）。
- 长期 BLOCKED：B-17（Worker 四真实故障场景）、宿主级网络隔离、Benchmark LLM 臂、人工基线、
  LICENSE（NEEDS_DECISION）。

## 5. 本轮工作分支与文件所有权

- 分支：`workbuddy/run011-full-loop`（从 RUN-010 分支创建，不基于 main）。
- 新增脚本（本轮验收执行体，均为纯 HTTP 客户端，不 import 应用代码）：
  - `scripts/full_loop_run011.py`：独立部署完整闭环（62 项检查/轮）
  - `scripts/verify_db_run011.py`：直接读 PG 复核（8 项检查）
  - `scripts/probe_lab_run011.py`：Active Probe 真实链路实验室（10 项检查）
  - `scripts/network_negative_run011.py`：未授权目标阻断（4 项检查）
- 证据目录：`docs/assets/run011/`。
- 本轮修改的源码范围见 `docs/RUN-011-FINAL-REPORT.md` §变更清单。

## 6. 基线结论

1. 起点正确：`b470518` + PR #2 未动 + main 未变。
2. 本轮必须补齐的第一目标（独立部署完整闭环）在开始时**确实缺失**，不是"已经做过只是没写文档"。
3. RUN-010 报告存在 1 处统计错误（Probe 指标）与 1 处测量口径问题（探针未真实执行），
   本轮已修正并在 WP2 重新做真实实验。
