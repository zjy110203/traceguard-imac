# RUN-014 基线（WP0）

> 核查时间：2026-11-26（CST）｜ 基线提交：`85f18c1`（`workbuddy/run013-production-reliability`）
> 结论：以 RUN-013 最新 HEAD 为唯一开发基线，新建 `workbuddy/run014-product-closure`；
> `main` 未修改、未合并任何 PR。

---

## 1. Git 与基线一致性

| 项 | 实测值 |
|---|---|
| 当前分支（核查时） | `workbuddy/run013-production-reliability` |
| HEAD | `85f18c1b7c71fb2ee1214e78d7a955a3ece8651d` |
| 与 `origin` 同步 | 一致（`origin/workbuddy/run013-production-reliability` 同 SHA） |
| `main` | `ac2876e`，未修改 |
| 工作区 | clean（无未提交改动） |
| 代码交付 HEAD（RUN-013 报告） | `3fc5289`；其后仅 docs-only 收口提交 `85f18c1` |
| 本轮分支 | `workbuddy/run014-product-closure`（从 `85f18c1` 创建） |

## 2. 现有功能状态（RUN-013 交付口径）

- **已验证**：独立 Worker 部署（API 内嵌 Worker 关闭 + 2 Worker）、Workspace 隔离与并发、
  Stability Replay 门禁、PostgreSQL 16 长稳（60 分钟 / 113 Run）、DeepSeek 真实 Smoke 11/11、
  SSE 事件流 Workspace 授权、审核/晋升 CAS 幂等、证据不可变与完整性校验。
- **未完整验证**：Repair 晋升后的回归验证（**不存在该阶段**）、SSE 活跃连接撤权、
  全局统计接口的角色边界（`system/llm/summary` 未纳入 `system.observe`）、
  业务全链单资产血缘（RUN-013 的 56 条记录中 Explorer/Compiler 是分开验证的）、
  Benchmark v5 23×2（NOT_RUN）、AI 质量样本 n=6。
- **测试基线**：RUN-013 记录全量 **771 passed / 16 skipped**（skip 均为未配置 PG 的专项）；
  PG16 专项 16 passed；Ruff 全绿。

## 3. 当前架构（不变）

```
apps/web (Vue3 + Vite)  ──HTTP/SSE──▶  apps/api (FastAPI, 组合根 runtime.py)
                                            │
                    ┌───────────────────────┼────────────────────────┐
                    ▼                       ▼                        ▼
        packages/persistence      packages/execution        packages/agents
        (SQLAlchemy 模型/仓储)     (TaskService/Pipeline)     (Explorer/Diagnosis/
                    │                       │                  Probe/Healing/LLM)
                    ▼                       ▼                        ▼
              SQLite / PostgreSQL    独立 Worker 进程          DeepSeek（可关闭）
```

- Run 生命周期：`QUEUED → PREPARING → RUNNING → FINALIZING → COMPLETED/FAILED`，
  租约 `lease_owner + lease_expires_at + fencing_token`；
- Repair 生命周期：`VALIDATED → PENDING_APPROVAL → APPROVED → PROMOTING → PROMOTED → ROLLED_BACK`
  （沙箱状态独立；**回归阶段在 RUN-014 前不存在**）。

## 4. 审查发现的缺陷（本轮修复对象）

| 域 | 缺陷 | 严重度 | 处理 |
|---|---|---|---|
| Repair | 前端把 Promotion 与 Regression 用同一条件判断，`promotion_record` 非空即完成（含 `VERSION_CONFLICT`） | P0 | WP1 修复 |
| Repair | 晋升后没有真实 Regression 阶段 | P0 | WP1 实现 |
| Repair | 列表展示虚构的 `base+1` 版本 | P2 | WP1 修复 |
| Lease | `lease_valid` / `renew_lease` 不检查 `lease_expires_at` | P0 | WP2 修复 |
| Lease | Step/Evidence/Event 写入与租约校验不在同一事务（TOCTOU） | P0 | WP2 修复 |
| Lease | `submit_result` 不校验租期；中间态提交提前释放租约 | P1 | WP2 修复 |
| SSE | 活跃连接不重新核验会话/授权 | P0 | WP3 修复 |
| RBAC | `/system/llm/summary`（全局摘要）未纳入 `system.observe` | P1 | WP3 修复 |
| 统计 | `llm/summary` 用 `limit=1000/500` 遍历，超限静默截断 | P0 | WP3 修复 |
| Artifact | 下载授权与内容读取分两个事务，元数据缺失时跳过对象级授权 | P1 | WP3 修复 |
| 文档 | RUN-013 WP6 把已按 Workspace 过滤的 `/queue/stats` 写成"全局队列" | P1 | WP3 修订 |
| Repair | `_step_index` 对 `step_index=None` 调 `int(None)`，`run_config.*` 提案经 HTTP 创建即 500 | P0（WP4 实测发现） | WP4 修复 |

## 5. 安全风险与生产阻塞

| 项 | 状态 |
|---|---|
| B-17 Worker 四场景故障注入 | **BLOCKED**（历史阻塞：AgentDR 拦截；未尝试绕过） |
| 宿主级网络隔离 | **BLOCKED**（需容器/VM 能力） |
| 未经授权的外部真实站点 | **BLOCKED**（未访问） |
| 真实人工诊断基线 | **BLOCKED**（无人工评审资源） |
| GitHub Actions 凭据 | 本机无 `gh` CLI；仅能通过公开 API 读取运行状态（不读取 Secrets） |
| LLM 四臂成本实验 | 未授权额外计费臂；本轮只做规则/探针两臂 + 诊断质量 n=32 |

## 6. RUN-014 工作包计划

| WP | 内容 | 关键产出 |
|---|---|---|
| WP1 | Repair Regression 真实闭环 | `docs/RUN-014-WP1-REGRESSION.md` |
| WP2 | Lease / Fencing 原子性 | `docs/RUN-014-WP2-LEASE-FENCING.md` |
| WP3 | SSE 撤权 + 全局接口 RBAC + 统计口径 | `docs/RUN-014-WP3-SECURITY.md` |
| WP4 | 单资产全血缘 UI 验收 | `docs/RUN-014-WP4-FULL-UI-LOOP.md` |
| WP5 | Benchmark v5 + AI 质量 | `docs/RUN-014-WP5-BENCHMARK-AI.md` |
| WP6 | 可靠性回归与发布门禁 | `docs/RUN-014-FINAL-REPORT.md` |
| WP7 | 文档与恢复状态 | 本文件 + WORKLOG + 状态文件 |

## 7. 负责人待决策

1. `PROMOTION_FAILED` 后是否允许"重新发起晋升"（状态机允许，但无 API/UI 入口）。
2. `/system/config` 是否收紧到 `system.observe`（当前对 read 开放，不含密钥）。
3. LICENSE 选择（历史遗留）。
4. PR #1 / #2 的集成策略（本轮未动）。
