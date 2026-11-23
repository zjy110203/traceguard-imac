# RUN-006 决策请求（不擅自冻结）

- 时间：2026-11-23
- 规则：下列事项**保持原状态**，本轮只提供技术分析、测试证据与推荐方案；
  **不得**把 NEEDS_DECISION 自动改成 APPROVED。

## D-01（R1）身份认证与服务端鉴权路径

- 现状：无登录/会话/Token；`reviewer`、`approved_by`、`operator` 均为请求体声明。
  本轮新增"主体类型门禁"（拒绝 `agent:*` / `llm:*` / `system:*` 等），但**不验证身份真实性**。
- 影响：单人本地开发可控；多人/联网环境下审批与晋升记录不可信。
- 选项：
  - A. 最小本地认证（`TG_AUTH_TOKEN` + 中间件校验 + 主体从 token 派生），保留单机模式；
  - B. 接企业 SSO/OIDC（需外部基础设施）；
  - C. 维持现状并明确"仅单机可信"，把认证列入下一阶段。
- 推荐：**A**（可验证、无外部依赖、不引入伪 RBAC）。需负责人确认是否允许新增认证依赖与配置。
- 本轮状态：**NEEDS_DECISION**（未实现）。

## D-02（P08）Repair 审批角色矩阵

- 现状：仅要求"具名人类主体"，无角色区分；任何人都能审批任意风险等级的修复。
- 未决策内容：角色定义（author / qa-lead / platform-admin）、各角色可审批的风险等级、双人复核阈值。
- 本轮动作：`approve()` 改用统一主体校验，`approval_ref` 记录主体与时间；**未**引入角色权限。
- 推荐：先落地 `qa-lead` 可审批 L1（定位器/等待）修复，`platform-admin` 审批 L2/L3；双人复核仅在 L3 强制。
- 本轮状态：**NEEDS_DECISION**。

## D-03（P03）Oracle 多来源冲突裁决

- 现状：冲突一律 `unresolved_conflicts` → `MANUAL_REVIEW` + `STOP_CONFLICT_UNRESOLVED`，不做裁决。
- 本轮改动：把冲突从"硬编码 False"改为**真实计算并落库**，同时保证不自行裁决。
- 未决策内容：Primary/Supporting 结构、优先级规则、跨来源冲突的裁决主体。
- 推荐：保持"不自动裁决"，先积累冲突样本再定义规则。
- 本轮状态：**NEEDS_DECISION**。

## D-04（P06）ProbeBudget 正式数值

- 现状：50 次 / 100 成本 / 60 秒，全部标记 `provisional=true`，并在文档与 UI 中注明。
- 本轮改动：预算改为故障安全（读/写失败即阻断）+ 跨进程 CAS；**数值未改**。
- 未决策内容：正式上限、按风险等级的差异化预算、超时窗口起算方式。
- 推荐：先用 30 天真实运行数据校准，再冻结；在此之前保持 provisional。
- 本轮状态：**NEEDS_DECISION**。

## D-05 `vite` dev-only high 漏洞的处理方式

- 现状：`npm audit` 报告的 high 只能通过 semver-major 升级修复（dev-only 依赖链）。
- 影响：CI Nightly security job 会失败；不做无评估的 `npm audit fix --force`。
- 选项：
  - A. 迁移 `vite` 8（需评估插件兼容 + 重新构建前端 + 重跑 UI 验收）；
  - B. 显式豁免并设定到期日（记录风险接受人与复评时间）；
  - C. 仅 devDependency 且不进入产物 → 记录为"不阻断发布"，但 CI 明确区分 dev/prod 依赖。
- 推荐：**A**（独立工作包，含前端回归）；若时间不允许则 **B** 并写清到期日。
- 本轮状态：**NEEDS_DECISION**（未改动依赖）。

## D-06 队列实现（DB 队列 vs Celery）

- 现状：DB 队列 + 租约/fencing，单机 Worker 与内嵌 Worker 均可用；未使用 Redis/Celery。
- 未决策内容：是否引入 Celery、是否引入 Redis、多机部署时的任务分发语义。
- 推荐：维持 DB 队列（已有 fencing/幂等/重试语义与测试），除非出现明确吞吐瓶颈证据。
- 本轮状态：**NEEDS_DECISION**（维持现状）。

## D-07 PostgreSQL 运行时验证与 CI 触发

- 现状：parity 测试就绪但本机无 PG（5 项 skip）；CI 配置了 Service Container 但未真实运行。
- 需要：授权在 CI 手动触发一次 Nightly（含 PG），或提供本地 Docker。
- 影响：`VERSION_CONFLICT` / `review_revision` CAS 的行锁语义、迁移 v7 在 PG 上的行为**未实测**。
- 推荐：优先在 CI 实跑一次（成本低），再把 PG 作为生产默认文档化。
- 本轮状态：**BLOCKED**（需环境/授权）。

## D-08 长稳与多 Worker 复验的时间窗

- 现状：60 分钟长稳、4 Worker 故障恢复未在本版本执行。
- 需要：独立时间窗 + 允许启动 4 个独立 Worker 进程与 1 个 API。
- 推荐：长稳脚本化（已有指标采集点），在独立环境执行并归档原始 JSON。
- 本轮状态：**BLOCKED**。

## D-09 外部应用泛化（P10）

- 现状：仅本地 TeamFlow；外部站点未授权。
- 本轮动作：`TargetPolicy` 已支持显式授权额外 origin（`TG_TARGET_EXTRA_ORIGINS`），但默认拒绝。
- 需要：明确授权清单（域名、测试账号、允许的写操作范围）。
- 本轮状态：**BLOCKED**（保持未授权）。

## 汇总

| 编号 | 事项 | 状态 | 是否阻塞其他工作 |
|---|---|---|---|
| D-01 | 身份认证 | NEEDS_DECISION | 阻塞生产化与 P08 |
| D-02 | P08 角色矩阵 | NEEDS_DECISION | 阻塞 Repair RBAC |
| D-03 | P03 冲突裁决 | NEEDS_DECISION | 不阻塞（当前保守转人工） |
| D-04 | P06 预算数值 | NEEDS_DECISION | 不阻塞（provisional） |
| D-05 | vite 漏洞 | NEEDS_DECISION | 阻塞 CI Nightly 全绿 |
| D-06 | 队列实现 | NEEDS_DECISION | 不阻塞 |
| D-07 | PostgreSQL / CI 触发 | BLOCKED | 阻塞 PG 运行时结论 |
| D-08 | 长稳时间窗 | BLOCKED | 阻塞可靠性结论 |
| D-09 | 外部应用授权 | BLOCKED | 阻塞泛化实验 |
