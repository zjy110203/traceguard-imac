# 当前任务（RUN-009 交付，2026-11-24）

> 历史标题「RUN-002 结束点」保留在下方。RUN-009 已完成，最终状态见 `docs/RUN-009-FINAL-REPORT.md`。

## RUN-009 收尾

- WP0/1/2/5/6/7 通过；WP3/WP4/WP8 PARTIAL（宿主级网络封锁、B-17 四场景、vue-tsc 升级如实 BLOCKED/NEEDS_DECISION）。
- 新增认证/RBAC、受控出站代理、14/14 路由验收、资源监控、Benchmark v4、供应链审计与完整交付报告。
- PR #2 保持 open/HOLD，合并决策记录在 `docs/RUN-009-REVIEW-REPORT.md` 与最终报告。

## 历史快照（RUN-002 结束点）

# 当前任务（RUN-002 结束点）

## 已完成

- 真实浏览器执行链路（Chromium + 证据 + Pipeline + Worker）
- 统一持久化层（事件/证据/产物/目录/迁移）
- 完整 REST API（81 端点）+ 9 页 Vue 工作台 + 单端口部署
- Explorer → Compiler → 审核 → 执行 → 诊断 → 图 → 探针 → 修复 → Benchmark 全链路
- 22 个契约 Schema 导出 + 防漂移测试
- 文档体系重写（架构/API/DB/Agent/Playwright/证据/自愈/Benchmark/安全/测试/部署/交付）

## 下一步建议

1. 接入 LLM 适配器，补 Benchmark 缺失基线；
2. 扩数据集（≥30 场景）+ Macro-F1 / Time to Diagnosis 埋点；
3. PG 实测、Worker 心跳续租、多实例压测；
4. P08 RBAC 落地；P03 契约调整（待批准）；
5. 前端 SSE 与图交互增强。

---

## RUN-004-FE 更新（2026-11-23，追加，不改写上方 RUN-002 历史记录）

- 已完成：前端产品化重构（统一设计系统 / AppShell / 14 视图）、Explorer 真实 LLM 信息展示、新增 `/models` 与只读 `GET /api/v1/system/llm/summary`。
- 验证：224 passed、ruff 全绿、typecheck/build 通过、真实 Chromium 53/53、六档断点无溢出。
- 证据：`docs/RUN-004-FE-IMPLEMENTATION-REPORT.md`、`docs/RUN-004-FE-UI-ACCEPTANCE.md`、`docs/assets/run004-fe/`。
- 上方“下一步建议”第 1 项（LLM 适配器 / Benchmark 基线）已由 RUN-003-G 完成；本轮不再重复。其余项状态未变。

---

## RUN-005-CORE 更新（2026-11-23，追加）

- 已完成（VERIFIED）：探针预算按 Run/Diagnosis 隔离与持久化；Active Diagnosis 真实闭环；
  真实 SSE（Last-Event-ID/过滤/心跳/轮询降级）；Inbox 持久化状态机 + 时间线投影真实消费者；
  Event Store 多进程序号原子分配。
- 已完成（实现 + 真实冒烟）：LLM 编译器（三模式）、LLM 探针规划、LLM 修复规划、三类可观测性。
- 已完成（编排/语义）：候选稳定性回放 F08（独立 Run + sandbox + Oracle 门槛 + 人工审核）。
- 已完成（工程）：CI 三级门禁工作流、前端自动化验收脚本（30/30）、PG parity 测试（本地 skip）。
- 未完成：Benchmark v3 多臂（BLOCKED）、60 分钟长稳（BLOCKED）、4 Worker 复验（PARTIAL）、
  PG 本地实测（BLOCKED）、P03/P08（NEEDS_DECISION）。

---

## RUN-006 更新（2026-11-23，追加）

- 已完成（VERIFIED）：Oracle 绑定/审核门禁/版本固定/主体类型校验/并发审核 CAS；
  探针证据回灌 + 动态重诊断 + 冲突真实计算 + 全 STOP_* 原因；预算故障安全与跨进程 CAS；
  晋升/回滚单事务原子 CAS；统一 TargetPolicy（Explorer + Runner + 请求级拦截）。
- 关键漏洞修复：`suggested_by == "llm"` 精确匹配导致 `llm:*` 假设可保留 CONFIRMED（已改为前缀 + 状态后降级）。
- 测试：285 → **367 passed / 5 skipped**；真实 Chromium E2E 11 项通过。
- 真实环境：正常/故障 Run、诊断、probe-plan、真实 http_state 回灌、图节点均已实测（`docs/assets/run006/`）。
- 未完成：WP2 全链 E2E（PARTIAL）、WP3 前端（NOT_IMPLEMENTED 本轮）、WP4 PG/Worker/长稳（BLOCKED）、
  WP5 CI 真实运行（BLOCKED）、WP6 Benchmark v3（NOT_IMPLEMENTED 本轮）。

---

## RUN-007-RESUME 更新（2026-11-23，追加）

- 分支：`workbuddy/run007-resume-20261123`（基于 `origin/main` `ac2876e`）
- 已完成（VERIFIED）：WP1 复核（163 passed）、迁移 v8 幂等/旧库升级、E2E 独立 Worker 队列（55 步 0 失败）、
  证据来源修正与图绑定校验、沙箱走队列（fail-closed）、Run 级状态缓存隔离、前端 15 视图验收（75/75）、
  Worker 故障恢复（17/17）、60 分钟长稳（65 周期/19 checks）、Benchmark 四臂 30 场景 + LLM 两臂。
- 已完成（IMPLEMENTED，未运行）：CI 三级门禁修复（PR 分支过滤、Nightly 去掩盖、Release 去 RUN-005 依赖 +
  Actions API 校验 Nightly `head_sha`）。**无 GitHub 凭据 → 未触发**。
- 阻塞：分支推送/PR（无 https 凭据、无 SSH key、无 token）；本机 PG16 运行时（无 Docker/psql）；
  人工诊断基线（无人力）；Routed 臂（未触发 reasoning 升级）。
- 未完成（如实登记）：Benchmark 中定位器场景的自愈"沙箱通过 → 晋升"断点未定位（回归 0/1）。
