# RUN-013 基线冻结说明

> 生成日期：2026-11-25（CST）
> 仓库：`zjy110203/traceguard-imac`
> 基线提交：`90f6f4e9e2178db9bd087af56fb3d8cf8d543ffe`
> 基线分支：`workbuddy/run012-product-completion`
> 本轮分支：`workbuddy/run013-production-reliability`
> 本轮主题：生产形态验证、全链路可靠性强化与产品成熟度提升

---

## 1. 基线确认

| 项 | 值 |
|---|---|
| 本地 HEAD | `90f6f4e9e2178db9bd087af56fb3d8cf8d543ffe`（与基线一致） |
| 远端同名分支 | `90f6f4e`（`git ls-remote` 核对一致） |
| `main` | `ac2876e`（RUN-007 遗留提交；本轮不修改、不合并） |
| 工作区 | 干净（无未提交修改、无未跟踪文件） |
| 提交链 | `44cfb46` → `57a5ea7` → `1ac36e5` → `b49116c` → `04b83c9` → `f64f0d7` → `7aa3ba5` → `90f6f4e` |

`90f6f4e` 只追加 CI 证据记录，无代码变更；代码交付 HEAD 为 `7aa3ba5`。
RUN-012 分支相对 `main` 历史较长，本轮**不重写历史、不强制推送、不做未经批准的合并**。

## 2. RUN-012 交付修正（WP0）

| 项 | 原记录 | 核对后 | 依据 |
|---|---|---|---|
| 最终 HEAD | `f64f0d7` / `7aa3ba5` 混用 | **`90f6f4e`**（代码交付 `7aa3ba5`） | `git rev-parse HEAD` |
| WP0 专项测试 | 25 passed（14 + 9 + 2 子集） | **39 passed**（14 + 9 + 16，命令可复现） | `pytest` 实跑 |
| WP4 UI 验收 | 29/29 全部通过 | **30 条记录 = 29 PASS / 0 FAIL / 1 NOTE** | `loop-acceptance.json`：`total=30 / passed=29 / failed=0` |
| 最终 CI | run `45621143973`（`7aa3ba5`） | run **`45621959909`**（`90f6f4e`，backend/frontend 均成功）；`45621143973` 保留为代码交付 HEAD 历史证据 | GitHub Actions run 页面 |
| False Pass 历史口径 | 0/37 | **保持 PARTIAL / INFERRED_ONLY**，不升级为 VERIFIED | `RUN-012-WP0-BENCHMARK-ERRATA.md` |

## 3. 已有核心能力（RUN-012 结束态）

- **执行内核**：Intent → TestCase → Oracle → 审核 → Run → Diagnosis → Repair → Sandbox →
  Approval → Promotion → Regression → Rollback 的完整状态机；CAS、幂等键、租约与 Fencing Token。
- **独立 Worker**：`run_worker.sh` 正式入口，支持多 Worker 消费、租约续期与接管；API 可选内联 Worker。
- **TeamFlow**：独立故障注入与目标站点进程，真实浏览器（Playwright）观测。
- **证据体系**：Events / Evidence / Artifact / Audit Chain，SSE 推送与续传。
- **多 Workspace**：服务端 `scope_filter` / 对象级授权；前端统一 Workspace 上下文（`apps/web/src/workspace.ts`）。
- **AI 顾问**：Compiler / Diagnosis / Probe / Repair 四类 LLM 调用点，Model Gateway、预算与 fallback。
- **Benchmark v5**：23 场景显式契约、False Pass 严格口径、历史归档只追加 Errata。
- **部署形态**：SQLite（默认开发）与 PostgreSQL 16（生产候选），Alembic 迁移。
- **前端**：Vue 3 + Vite，15 个视图，真实 Cookie + CSRF 认证。

## 4. 已验证功能（可回读证据）

| 能力 | 状态 | 证据 |
|---|---|---|
| 基础业务闭环（单元/集成） | VERIFIED | RUN-012 全量 `760 passed / 16 skipped` |
| PostgreSQL 16 专项 | VERIFIED | 真实 PG 16.16：平台/一致性/Workspace 16 passed |
| PR Gate CI | VERIFIED | run `45621959909`（`90f6f4e`，两 Job 成功） |
| 多 Workspace 前端上下文 | VERIFIED | 真实浏览器 25/25（RUN-012 WP2） |
| 全局统计权限与 SQL 聚合 | VERIFIED | 专项 11 passed（含 522 条回归） |
| 核心业务 UI 闭环 | VERIFIED | 30 条记录：29 PASS / 0 FAIL / 1 NOTE（内联 Worker 形态） |
| 诊断顾问 Schema/重试/Fallback | VERIFIED（离线） | 专项 22 passed |
| 短时长稳（6.14 分钟 / 20 周期） | VERIFIED（短时） | `docs/assets/run012/wp6/soak-6min.json` |

## 5. 已实现但未完整验证

| 能力 | 状态 | 缺口 |
|---|---|---|
| 独立 Worker 形态下的 UI 全链路 | PARTIAL | RUN-012 使用 `TG_INLINE_WORKER=true`，未证明独立 Worker 进程闭环 |
| 多 Workspace 高并发与授权撤销 | PARTIAL | 已有单浏览器顺序验收，缺并发/竞争/SSE 越权验证 |
| compiler/healed「稳定性回放 → 审核」成功路径 | PARTIAL | 入口可用，完整成功路径与负面路径未覆盖 |
| 60 分钟长稳与故障恢复 | NOT_RUN | 仅有 6 分钟短时证据 |
| AI 真实 Provider 路径 | NOT_RUN | 本机无合法凭据；离线 FakeProvider 已覆盖契约 |
| Benchmark v5 23×2 重跑 | NOT_RUN | 独立部署环境与成本约束 |
| Nightly / Release Gate | NOT_RUN | 需 `workflow_dispatch` 与凭据 |
| 前端 15 视图逐项操作审计 | PARTIAL | 已有 15 视图接入，未逐按钮核对服务端回读 |

## 6. BLOCKED

| 项 | 原因 | 解除条件 |
|---|---|---|
| B-17 Worker 四场景故障注入 | 历史阻塞，AgentDR 两次拦截 | 获得宿主级执行许可与白名单 |
| 宿主级网络隔离 | 需容器/宿主能力，非用户态可解 | 提供容器或 VM 级隔离环境 |
| 外部真实站点访问 | 未授权 | 提供书面授权清单与白名单 |
| 真实人工基线 | 需人工评审资源 | 安排独立评审人 |

## 7. NEEDS_DECISION

| 项 | 说明 |
|---|---|
| P03 Oracle 多来源冲突裁决 | 决策包已产出（`docs/decisions/P03-oracle-arbitration.md`），未冻结；本轮不改冲突契约 |
| P08 Repair 审批角色矩阵 | 决策包已产出（`docs/decisions/P08-repair-approval-roles.md`），未冻结；本轮不放宽/收紧审批模型 |

## 8. 本轮范围（RUN-013）

| 工作包 | 内容 | 主要产出 |
|---|---|---|
| WP0 | 交付证据修正与基线冻结 | 本文件 + RUN-012 三份权威文档修正 |
| WP1 | 独立 Worker 部署的真实 UI 全链路 | `scripts/*run013*.py`、独立 API/Worker/TeamFlow 验收证据 |
| WP2 | 多 Workspace 并发一致性与安全 | `tests/integration/test_workspace_concurrency_run013.py`、真实浏览器脚本 |
| WP3 | 稳定性回放到审核完整闭环 | compiler/healed 成功 + 负面路径用例与 UI 证据 |
| WP4 | 60 分钟长稳与故障恢复 | `scripts/soak_run013.py`、`docs/assets/run013/soak/`、WP4 报告 |
| WP5 | AI 真实能力与 Benchmark 科学性 | 质量指标、真实 Provider 状态、Benchmark 重跑结论 |
| WP6 | 系统审查与最终交付 | 部署运维手册、最终报告、FEATURE-STATUS 更新、CI 证据 |

**约束**：不突破人工审批、Oracle、稳定性回放、CAS、租约、Fencing Token、网络白名单与 Repair 血缘约束；
所有实验使用独立数据库、独立数据目录、独立端口与隔离夹具；不修改 `main`、不自动合并 PR。
