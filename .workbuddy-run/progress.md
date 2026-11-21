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

## 当前阶段

- 阶段 0：工程初始化与设计基线 —— 即将开始（IN_PROGRESS 见 current-task.md）

## 下一步

- 初始化 Git 仓库（项目内）并建立开发分支 `workbuddy/traceguard-autonomous`。
- 生成技术兼容矩阵与依赖锁定（基于真实 PoC 与官方索引，不编造）。
- 搭建后端 FastAPI / 前端 Vue3 / contracts / core / teamflow 骨架并开始编码。

## 环境约束（真实）

- Python 3.13.12、Node 22.22.2、npm 10.9.7、git 2.39.5
- **Docker 不可用、psql 不可用、uv/poetry 不可用**（不改系统环境）
- PyPI 与 npm registry 可联网（已验证 fastapi=0.165.0、vue=3.5.46 可查）
- 决策：阶段 0/1 采用项目内 venv + SQLite（通过 SQLAlchemy 抽象，可平滑切 PostgreSQL）；Docker Compose 配置照常产出但不作为本环境验收硬依赖，记录为限制。
