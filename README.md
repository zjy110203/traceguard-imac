# TraceGuard

Agentic Web UI Testing & Quality Intelligence Platform（智能 Web UI 自动化测试与质量情报平台）

> 架构基线 v1.1 · 实施路线图 v1.1（评审修订稿）· 由 WorkBuddy 自主工程任务持续开发

## 项目状态

本项目按五层建设路线、14 个实施阶段（0～13）、6 个里程碑（M0～M5）、C01～C12 契约体系交付。
各阶段真实状态见 [`.workbuddy-run/phase-status.md`](.workbuddy-run/phase-status.md)，当前任务见
[`.workbuddy-run/current-task.md`](.workbuddy-run/current-task.md)。

**重要：** 路线图 v1.1 原文标注为"评审修订稿，通过项目评审后方可标记为正式冻结基线"。
当前仓库未发现正式批准/冻结记录，因此不声称路线已冻结。已冻结的设计语义（Test Verdict
四态聚合、Event 服务端注入 workspace_id、三级 CI 门禁、Repair 生命周期）作为实施依据；
其余待确认事项见 [`.workbuddy-run/decisions.md`](.workbuddy-run/decisions.md)。

## 架构原则（不可改变）

1. Agent 是能力层，不是事实层。
2. Playwright 负责正式测试的确定性执行。
3. Oracle 独立于 Agent，正式 Oracle 必须来源可追溯并经审批。
4. Task 与 Run 分离；重试产生新 Run，不覆盖历史。
5. Execution Status / Test Verdict / Diagnosis Status / Diagnosis Outcome 四者分离。
6. Event 与 Evidence 分离；原始 Evidence 不得被派生结果冒充。
7. 相关性 ≠ 因果性；证据不足允许 INCONCLUSIVE。
8. Self-Healing 不得删除必需 Assertion、不得修改正式 Oracle。

## 目录结构

```
apps/
  api/            FastAPI 平台入口
  web/            Vue 3 + TypeScript 工作台
  teamflow/       受控被测应用（最小 Web + 业务 API + 故障注入）
  workers/        Celery Worker（阶段3启用）
packages/
  contracts/      C01～C12 JSON Schema 与契约测试
  core/           配置、日志、错误、通用领域基础
  execution/      Playwright 确定性执行内核（阶段2）
  agents/         Explorer / Compiler / Diagnosis Agent（阶段5+）
  evidence/       Event / Evidence / Graph（阶段4+）
benchmark/        UI-FaultBench（阶段11）
tests/            unit / contracts / integration / e2e
infra/            Docker Compose、CI、部署配置
docs/             设计、运维、报告（docs/FINAL 为最终交付报告）
资料/             权威设计资料（只读依据，不入库）
.workbuddy-run/   长期自主运行记录（进度/决策/测试/安全/恢复）
```

## 本地运行环境

见 [`docs/quickstart.md`](docs/quickstart.md)（阶段0 产出后补充完整命令）。

当前机器约束（真实记录）：Docker 与 PostgreSQL 客户端不可用，因此阶段0/1 默认使用项目内
venv + SQLite（经 SQLAlchemy 抽象，可平滑切换 PostgreSQL）；Docker Compose 配置仍会产出，
但不作为本机验收的硬依赖。

## 开发规范

- 分支：`workbuddy/traceguard-autonomous`（本地开发，不推送远程）
- 提交：阶段性本地提交，禁止提交密钥、禁止强制推送、禁止改写既有历史
- 测试：所有验收数字来自真实测试日志，禁止 Mock 冒充真实运行
