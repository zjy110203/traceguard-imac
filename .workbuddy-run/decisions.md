# 技术决策与待确认事项

- 更新：2026-11-21 23:45

## 已决策（本运行内可自主决定，不改变对外契约）

| 编号 | 决策 | 理由 | 影响 |
|---|---|---|---|
| A-01 | 项目根目录锁定为 `/Users/dlp--test/Desktop/traceguard` | 唯一 TraceGuard 目录，无歧义 | 所有代码仅在此目录 |
| A-02 | 初始化 Git 仓库并建分支 `workbuddy/traceguard-autonomous` | 当前非仓库，无用户改动需保护 | 本地提交，不推送远程 |
| A-03 | 阶段0/1 用 SQLite 作为默认本地库，SQLAlchemy 抽象，可切 PostgreSQL | Docker/psql 不可用，且规则禁止改系统环境 | Docker Compose 仍产出 PG 配置；本环境验收用 SQLite，明确记录限制 |
| A-04 | 依赖锁定用 `requirements*.txt` + `pip freeze > constraints` | uv/poetry 不可用，禁止装系统级工具 | 可复现，非 uv.lock |
| A-05 | 前端用 Vite + Vue3 + TS，npm 装项目内依赖 | npm registry 可达 | node_modules 不入库 |
| A-06 | 阶段0 权威依据：架构基线 v1.1 + 路线图 v1.1（含已冻结 G/F 项） | 用户指令要求以正式批准最新版为准；未找到冻结记录故不声称冻结 | 待确认项保留 BLOCKED |
| A-07 | 运行记录放 `.workbuddy-run/`，不写密钥 | 用户指令要求 | — |

## 待确认（不得自行决定，依赖任务的标记 BLOCKED）

| 编号 | 事项 | 来源 | 最晚确认阶段 | 当前状态 | 是否阻塞 |
|---|---|---|---|---|---|
| P03 | Oracle 多来源优先级、可信等级与冲突裁决细则 | 路线图 7.2 | 阶段2 冲突场景验收前 | 待确认 | 不阻塞基础四态聚合（已冻结兜底：无法裁决→INCONCLUSIVE） |
| P08 | Repair 审批角色 | 路线图 7.2 | 阶段10 | 待确认 | 阶段10 审批实现前阻塞；先做不依赖角色的部分 |
| P-OTHER | 路线图 v1.1 是否正式冻结 | 用户指令三 | 项目评审 | 未发现批准记录 | 不阻塞开发，禁止声称已冻结 |
| P-EXT | 外部 Web 应用授权 | 用户指令 2.6 / 阶段12 | 阶段12 | 未授权 | 阶段12 外部实测 BLOCKED，先做本地与文档 |

## 冲突记录
- 暂未发现文档间字段冲突。若后续发现，登记到本文件并保留待确认，不自行选择方案。

## RUN-002 决策

1. **持久化统一到单一 Database**（SQLite 默认，PG 方言兼容）：不再保留 execution.db 独立库；
   迁移 v4 修复 Step 主键跨 Run 覆盖。
2. **Worker 形态**：API 内嵌 Worker 为单机默认；独立 Worker 共用同一套 TaskService/Pipeline。
   不引入 Celery/Redis（无必要复杂度，且本机无 Redis）。
3. **浏览器渠道优先级**：内置 Chromium → msedge → chrome；显式失败即报错，不静默降级。
4. **诊断 5xx 优先于下游症状**：接口失败导致的未落库不再作为独立根因。
5. **Benchmark workspace 隔离**：实验运行与生成资产写入 `benchmark` workspace，避免污染主工作台。
6. **不做的事**：不伪造 LLM 数值；不实现 S3 适配器并声称 MinIO 完成；不冻结 P03/P08。

---

## RUN-005 新增待决策（NEEDS_DECISION）

- P03：推荐策略 D（域内权威 + 跨域冲突升级），未冻结。
- P08：推荐方案 2（职责分离 + 高风险双人审批），未冻结。
- P05/P06：EIG 权重与预算数值仍为 provisional。
- P09：Benchmark 统计方案（指标已定义，未跑多臂）。
- P11：无压测，不提出 SLO。
- 依赖漏洞处置：vite 是否升级到 8（破坏性）或豁免 dev-only 并定期限。
详见 `docs/RUN-005-DECISION-PACKAGE.md`。
