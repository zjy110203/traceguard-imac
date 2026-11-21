# TraceGuard 自主开发进度

> 本目录为长期自主运行的持久化记录。每次中断后先读 `resume.md`，再读本文件与 `phase-status.md`。
> 严禁在此保存密钥、Token、密码或真实凭据。

- 最后更新：2026-11-21 23:45 (GMT+8)
- 运行编号：RUN-001
- 目标：基于路线图 v1.1 与架构基线 v1.1，从当前真实进度交付到完整 TraceGuard
- 目标时长：30h（最长 36h，以平台允许为限）

## 真实起点（只读审查结论）

- 项目根目录：`/Users/dlp--test/Desktop/traceguard`（唯一 TraceGuard 目录，无歧义）
- 根目录内容：仅 `资料/` 与 `资料.zip`（40MB）。**没有任何源代码、没有 Git 仓库。**
- 权威资料：
  - `资料/项目资料/00～10`（11 份设计文档，文档集 v1.1.0，架构基线 v1.1）
  - `资料/实施线路/TraceGuard-完整项目实施路线图-v1.1.md`（1821 行）
  - `资料/实施线路/TraceGuard-v1.1-修订摘要.md`（含 F01～F13、G01～G08 修订）
  - `资料/TraceGuard-开源项目调研/`（Agentic-Test-Explorer、TestZeus-Hercules 调研与借鉴台账）
- Git 状态：非 Git 仓库。工作区无用户未提交修改需要保护。
- 实际开发进度：**阶段 0 尚未开始（NOT_STARTED）**。全 14 阶段均为 NOT_STARTED。

## 路线图冻结状态（重要）

- 路线图 v1.1 原文明确标注为"评审修订稿，通过项目评审后方可标记为正式冻结基线"。
- 未检索到任何"正式批准/冻结"记录。**不声称路线图已正式冻结。**
- 但已冻结的设计语义（G01～G08 / F01～F13 中明确"已冻结"部分）作为实施依据：
  - 阶段 2 的 Test Verdict 四态聚合规则 + 8 条补充规则；
  - Event 至少一次投递、`workspace_id` 必填且服务端可信注入；
  - 三级 CI 门禁职责划分；Repair 沿用原状态机。
- 12 项待确认事项（P01～P12，路线图 7.2）保持待确认，尤其 P03（Oracle 多来源裁决）、P08（Repair 审批角色）。

## 已完成工作

1. ✅ 只读审查：范围、安全边界、资料、代码、Git 状态。
2. ✅ 建立长期运行记录目录与基线文件。
3. ✅ **阶段 0 完成**（ACCEPTED*）：Git 仓库+分支、目录骨架、FastAPI/Vue3 骨架、统一配置/日志脱敏/错误/健康检查、C01-C12 共22个 JSON Schema、契约测试、CI 三级门禁配置、安全基线、依赖锁定（requirements.lock.txt + package-lock.json）。测试 22/22 通过；API 真实 HTTP 启动验证成功。
4. ✅ **阶段 1 完成**（ACCEPTED）：TeamFlow 最小被测应用（登录/Workspace/项目创建列表详情编辑）、独立业务状态查询、测试数据重置/种子、五类故障注入（TF-API/NET/DB/UI/TIME-001）、Ground Truth 隔离（403 验证）、场景元数据。测试 9/9 通过；curl 真实业务闭环验证成功（含 TF-DB-001 假成功被独立状态揭穿）。
5. ✅ **M0 里程碑达成**。全项目累计 31 测试通过，lint/format 全绿。

## 当前阶段

- 阶段 0、1：已完成并提交（Git: c9d3a45、fc1397c）。
- 阶段 2：专业 Web UI 自动化测试框架 —— 下一步开始。

## 下一步

- 阶段 2：Playwright + pytest 确定性执行内核。先做 Playwright 安装与浏览器启动 PoC（本机网络慢，需后台安装）。
- 建立 TestCase/Version、Oracle Manager、Assertion Evaluator、Verdict 四态聚合（已冻结规则），10-20 条 TeamFlow 用例。
- 验证 Test Verdict 合法/非法样例可执行、NOT_EVALUATED 与 INCONCLUSIVE 可区分、execution_status 与 test_verdict 独立。

## 环境约束（真实）

- Python 3.13.12、Node 22.22.2、npm 10.9.7、git 2.39.5
- **Docker 不可用、psql 不可用、uv/poetry 不可用**（不改系统环境）
- PyPI 与 npm registry 可联网（已验证 fastapi=0.165.0、vue=3.5.46 可查）
- 决策：阶段 0/1 采用项目内 venv + SQLite（通过 SQLAlchemy 抽象，可平滑切 PostgreSQL）；Docker Compose 配置照常产出但不作为本环境验收硬依赖，记录为限制。
