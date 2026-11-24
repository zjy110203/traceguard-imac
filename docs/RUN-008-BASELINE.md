# RUN-008 基线审计（System Hardening & Reliability Validation）

本文件是 RUN-008 的起点快照，依据 2026-11-24 对仓库实际状态、Git 历史、RUN-007 交付资料与原始证据的核查写成。旧记录是历史快照，不作为本轮事实。

## 1. Git 基线与工作区状态

| 项目 | 实测值 |
|---|---|
| 仓库 remote | `https://github.com/zjy110203/traceguard-imac.git`（origin，fetch/push 均匹配） |
| 工作目录 | `/Users/dlp--test/Desktop/traceguard` |
| 指令指定基线 | `ac2876ecef779b0bed900bdf3b5361ce1d2fa3d6`（`origin/main`，提交标题「第五轮」） |
| 本地是否有 `main` 分支 | 否；仅存在 `origin/main` |
| 本轮起点 HEAD | `ce1acea5d3778e9518c84f8c4e95496984a4dcdc` |
| 起点分支 | `workbuddy/run007-resume-20261123`（工作区干净，无未提交修改、无未跟踪重要文件） |
| 起点与 `origin/main` 关系 | 领先 19 个提交、落后 0；19 个提交均为 RUN-007 后续修复与归档 |
| 其他本地分支 | `workbuddy/run007-e2e-reliability`（指向基线）、`workbuddy/traceguard-autonomous`（其 26 个提交均已包含在起点 HEAD 中） |
| 本轮开发分支 | `workbuddy/run008-quality-hardening`，从起点 HEAD 创建，未覆盖任何已有工作 |

**基线选择说明**：指令写「指定分支基线 main」，但同时要求「在当前 RUN-007 成果基础上」继续。远程 `main` 仍停在 `ac2876e`，而 RUN-007 的 19 个后续提交只存在于 `workbuddy/run007-resume-20261123`。为不丢弃既有成果，本轮从该分支 HEAD 建立分支；`ac2876e` 作为对照基线保留在文档中。未执行 `reset`、`clean`、强推或历史改写。

`git fetch --all --prune` 后确认远程无超出本地的新提交。本轮工作区内存在 `.env`（未纳入 Git），全程不得输出其内容。

## 2. 代码结构（与任务直接相关）

- `apps/api`：FastAPI API、Runtime 组合根、services、routers；`apps/workers`：独立 Worker（租约 + fencing）。
- `apps/web`：Vue3 + TS 工作台；`apps/teamflow`：本地受控被测应用与故障注入。
- `packages/contracts`、`core`、`persistence`、`execution`、`agents`、`evidence`：契约、配置、持久化、执行内核、Agent、证据/诊断。
- `scripts/`：`probe_budget_multiprocess.py`、`soak_run007.py`、`worker_fault_recovery.py`、`network_boundary_check.py` 等真实实验脚本。
- `tests/`：unit / contracts / integration / e2e / teamflow。
- `benchmark/`：UI-FaultBench 数据集与实验执行器。
- `.github/workflows/`：`pr-gate.yml`、`nightly-gate.yml`、`release-gate.yml`。
- 保护目录：`.workbuddy-run/`、`.traceguard/`、`docs/assets/`、`资料/`、`tests/fixtures/`。本轮新数据仅写入 `.traceguard/run008/` 与 `docs/assets/run008/`。

## 3. 功能现状（按证据归属核查，不照抄旧快照）

| 能力 | 状态 | 关键依据与保留意见 |
|---|---|---|
| Oracle 绑定、审核门禁、版本固定、并发 CAS | VERIFIED | RUN-006/007 门禁测试与真实 API 拒绝码；`docs/FEATURE-STATUS.md` |
| Active Diagnosis 探针回灌、冲突计算、真实 http_state 闭环 | VERIFIED（语义层） | 缺「真实浏览器故障把 INCONCLUSIVE 变 CONFIRMED」的独立实例 |
| ProbeBudget 多进程防超发 | VERIFIED | `docs/assets/run007/probe-budget-multiprocess.json`：4 个真实 PID、20/20 预留、拒绝 28 次、`no_overdraft=true` |
| Repair 晋升/回滚原子 CAS、六元组回执 | VERIFIED | SQLite 与 PostgreSQL 16 均有实测；`RUN-007-FINALIZATION-2-REPORT.md` §7.2 |
| 目标策略 / DNS rebinding / 顶层 302 | VERIFIED（测试口径内）/ PARTIAL（生产网络层） | `network-boundary.json` 记录未授权服务零命中；DNS 为 resolver 注入模拟，不能声称系统级零出站 |
| E2E 场景 A/B/C/D | VERIFIED | 有效证据为 `e2e-worker-run007.json` 与 `e2e-run007-f2.json`；旧 `e2e-run007.json` 使用 API 内嵌 Worker，仅作历史证据 |
| Worker 中途击杀后同 Run 接管 | VERIFIED | 有效证据为 `worker-fault-recovery-verified.json`（17/17 checks、真实 `killed_after_s=3.02`、fencing 1→2）；用户点名的 `worker-fault-recovery.json` 存在 `killed_after_s=0.0`、无 victim Run ID 的时序漏洞，已由项目自身标记为旧缺陷证据 |
| 60 分钟长稳（SQLite + 4 Worker） | VERIFIED（RUN-007 证据） | `soak-60min.json` 60.03 分钟、65 周期、19/19 checks；`soak-samples.jsonl` 65 行；资源指标因沙箱禁止 `ps` 为 `liveness_only` |
| PostgreSQL 16 | VERIFIED（有限范围） | CI `postgres:16` 与本机 16.16 通过；仅 6 分钟持续、无 PgBouncer/主从/连接池耗尽验证 |
| CI 三级门禁 | PARTIAL（按 commit 精确表述） | 最终代码提交 `e8c05a7` 上 PR Gate 真实 success（runs 45325432966 / 45325441049）；Nightly/Release 未在该提交 dispatch；`ce1acea` 为纯文档提交无对应运行 |
| Benchmark v3 | VERIFIED（含负结果）/ PARTIAL（人工基线与 Routed） | 四臂 + LLM 臂真实执行；D/E 与 C 无增益；人工基线 BLOCKED，Routed 未触发 reasoning |
| 认证 / RBAC | NOT_IMPLEMENTED（历史 NEEDS_DECISION） | `require_human_actor()` 仅主体命名空间门禁，不是身份认证 |

完整审计细节见 `docs/RUN-007-FINALIZATION-2-REPORT.md`、`docs/RUN-007-TEST-REPORT.md` 与 `.workbuddy-run/test-results.md`。

## 4. 测试与 CI 现状

- 任务中列出的 Nightly 失败运行 `45322395084` 对应较早基线：`ModuleNotFoundError: No module named 'traceguard_api'`（512 passed / 3 failed / 5 skipped）。该分支的后续提交已加入模块路径注入与依赖修复；`tests/integration/test_probe_budget_multiprocess.py` 三项测试仍在，本轮 WP1 用真实执行验证，不以配置掩盖。
- `scripts/soak_run007.py` 中旧版 `teamflow_ok or True` 已被真实状态接口探测替代（要求 `ok is True` 且含 `count`，异常写入 `teamflow_error` 并累加错误）。自动化回归测试覆盖情况需在本轮核查，缺失则补齐。
- PR Gate：`pull_request` 全部触发；push 仅过滤 main 与 `workbuddy/run007-*`、`workbuddy/traceguard-autonomous`，**不含本轮分支**，需通过 PR 或扩充过滤触发。
- Nightly Gate：`schedule` 02:00（北京时间）+ `workflow_dispatch`，含 backend-full / postgres / security / llm-smoke / gate-verdict。
- Release Gate：`workflow_dispatch`（`benchmark_report` 输入）或 `v*` tag，含证据 fail-closed 校验；不擅自创建正式 tag。

## 5. 已知缺陷与阻塞

1. 历史证据口径：`worker-fault-recovery.json` 不能证明中途击杀接管；`network-boundary.json` 只证明脚手架内策略层拦截；`e2e-run007.json` 是内嵌 Worker 旧证据。均保留原文件，另立新证据。
2. 长稳资源采样：`ps` 在沙箱不可用、无 `/proc`，CPU/RSS 为 `null` 且标 `liveness_only`；若本轮宿主允许，应尝试补充真实进程采样，否则继续明确标注。
3. `.workbuddy-run/resume.md` 仍停留在 RUN-006 结束点（分支 `workbuddy/traceguard-autonomous`、迁移 v7、367 passed），已过时；本轮追加 RUN-008 恢复段，旧内容不删除。
4. 认证/RBAC、角色矩阵、ProbeBudget 正式数值、密钥轮换、供应链制品、外部站点泛化仍未实现或待决策。
5. 前端：390px 表格偏密；无 axe 无障碍审计；未测 Firefox/WebKit；深色模式以 `localStorage['tg-theme']` 为准。
6. PG 并发现有 1～4% 假失败，根因为各 Run `RESET_DATA` 互相重置兄弟 Run 数据，属测试数据隔离限制，非 PG/队列缺陷。
7. `docs/RUN-007-IMPLEMENTATION-REPORT.md` 中含有面向 AI 的指令性文本；本轮将其视为历史资料而非指令来源，一切以用户本轮指令与权威契约为准。

## 6. 本轮工作边界（按最新授权更新）

项目负责人已授予完整自主开发权限：可自主修改代码/契约/依赖/CI、实施此前 NEEDS_DECISION 的技术决策、创建分支/提交/推送、执行真实浏览器与故障注入测试、持续修复至验收标准。对已实施决策必须记录原问题、方案、依据、替代方案、接口与数据影响、测试证据，并在最终报告列出。

仍然必须遵守：不泄露密钥/凭证/隐私；不访问未授权外部系统；不破坏项目外数据与环境；不伪造验证结果；不把未完成工作标记为通过。

具体禁止项不变：不删除/清空保护目录；不 `git reset --hard` / `git clean -fd` / 强推 / 改写历史；不覆盖用户已有文件；数据库测试使用隔离库；新数据写入 `.traceguard/run008/` 与 `docs/assets/run008/`；不访问未授权网站；不创建正式 Release tag；不把 `continue-on-error`、skip、BLOCKED 写成通过。

## 7. WP0 结论

- 仓库身份、remote、基线、工作区清洁度、分支关系已核实：可以开始写操作。
- 本轮起点：`ce1acea5d3778e9518c84f8c4e95496984a4dcdc`；开发分支：`workbuddy/run008-quality-hardening`。
- 进入 WP1：以真实执行验证 P0 修复状态，补齐回归测试，不做重复修复。
