# RUN-013 最终报告：生产形态验证、可靠性强化与产品成熟度

> 日期：2026-11-25（CST）｜ 分支：`workbuddy/run013-production-reliability`
> 基线：`90f6f4e9e2178db9bd087af56fb3d8cf8d543ffe`（RUN-012 交付 HEAD）
> 代码交付 HEAD：`3fc5289`（其后仅 docs-only 收口提交）
> 原则：已完成工作包自动推进；BLOCKED / NOT_RUN 如实标注，不把未执行写成 PASS。

---

## 1. 项目基线

| 项 | 值 |
|---|---|
| 仓库 | `zjy110203/traceguard-imac` |
| 基线提交 | `90f6f4e`（`workbuddy/run012-product-completion`） |
| 本轮分支 | `workbuddy/run013-production-reliability` |
| `main` | `ac2876e`，未修改、未合并 |
| RUN-012 状态 | WP0~WP6 完成；代码交付 `7aa3ba5`，CI 证据 `90f6f4e` |

## 2. RUN-012 修订结果

| 项 | 原记录 | 修正后 |
|---|---|---|
| 权威 HEAD | `f64f0d7` / `7aa3ba5` 混用 | `90f6f4e`（代码 `7aa3ba5`） |
| WP0 测试 | 25 passed（局部子集） | **39 passed**（14+9+16，命令可复现） |
| WP4 UI | 29/29 | **30 条记录 = 29 PASS / 0 FAIL / 1 NOTE** |
| 最终 CI | run `45621143973`（`7aa3ba5`） | run **`45621959909`**（`90f6f4e`，两 Job 成功） |
| False Pass | PARTIAL | 保持 PARTIAL / INFERRED_ONLY |

详见 `docs/RUN-013-BASELINE.md`、`docs/RUN-012-FINAL-REPORT.md`。

## 3. 独立 Worker 验收

- 部署：独立 API（`TG_INLINE_WORKER=false`）+ 2 独立 Worker + 独立 TeamFlow + SQLite + 真实 Chromium；
- 业务链：Intent → TestCase → Oracle → 绑定 → 审核 → Run → 诊断 → Repair → Sandbox →
  审批 → Promotion → Rollback，每步服务端回读；
- 结果：**56 条记录 = 55 PASS / 0 FAIL / 1 NOTE**；
- 故障：启动失败、SIGTERM、SIGKILL、租约接管、SIGSTOP/恢复、旧 Worker 过期提交、
  API 重启、客户端断连、双 Worker 并发，共 22 项断言通过；
- 修复：旧 Worker 恢复后重复写 Evidence（19→38）；写证据前同步 `lease_valid` 校验，
  租约丢失走 abandon（不写证据/不发事件/不提交）。复验 19→19。

详见 `docs/RUN-013-WP1-INDEPENDENT-WORKER.md`。

## 4. Workspace 并发与权限验收

- 修复 `/events/stream`：原先不在认证中间件覆盖范围，匿名可订阅、可跨 Workspace（含游标绕过）；
  现显式会话解析 + `scope_filter` + SQL `workspace_ids` 过滤；
- 前端：修复快速切换竞态、`workspace_id` 参数覆盖、换用户残留、多标签不同步、401 清理；
- API 专项 **8 passed**；真实浏览器 **10/10**：
  alice/bob 隔离、localStorage 篡改回落、6 个并发越权请求全 403、
  授权撤销后下一请求 403、12 次快速 A/B 切换无回填、多标签同步、390px 深色无溢出；
- `/system/health` 计数改为 Workspace 过滤 + SQL COUNT，修复全局规模泄露与 1000 截断。

详见 `docs/RUN-013-WP2-WORKSPACE-CONCURRENCY.md`、`docs/RUN-013-WP6-SYSTEM-REVIEW.md`。

## 5. Stability Replay 完整闭环

- 审查 10 项不变量；修复“回放后 Oracle 改版/撤销仍可批准”；
- 审核事务内重新核验 Oracle 版本、审批状态、Workspace；
- 新增只读预检 `GET /testcases/{id}/versions/{v}/stability-gate`；
- UI 新增回放面板：状态/进度/成功失败/结论/可晋升/Oracle/浏览器/报告来源/审核条件/problems；
- 真实浏览器 **15/15**：compiler 与 healed 正向闭环、无回放/失败回放/未完成回放负向路径；
- 门禁测试 **12 passed**（含 Oracle 改绑/撤销）。

详见 `docs/RUN-013-WP3-STABILITY-REPLAY.md`。

## 6. 60 分钟长稳结果

- 环境：真实 PostgreSQL 16.16 独立库 + 独立 API（`inline=false`）+ 2 独立 Worker
  + 独立 TeamFlow + 真实 Chromium；每 30s 一个真实 Run；
- **60.05 分钟 / 113 个真实 Run（另 5 个故障 Run）/ 113 PASSED / 0 失败**；
- 数据库一致性：0 重复步骤、0 孤儿步骤、0 孤儿证据、0 重复提交、0 缺失证据、0 重复事件、0 状态倒退；
- 资源：API RSS 100.8→99.4 MB（稳定），Worker 81.8→92.1 / 94.0→92.4 MB，DB 9.31→18.16 MB（随证据线性增长）；
- API 健康延迟 P50 4.2ms / P95 7.0ms；Run 平均 4.80s / 最大 6.67s；
- 原始检查 28/31：2 项为检查口径错误（实际接管由 w2 完成、`lease_owner` 为留痕字段），
  1 项为 DB 中断瞬间 `/execute` 500（已加 `pool_pre_ping`，定向复验 200）。
- **不宣称 31/31**；详见 `docs/RUN-013-WP4-SOAK.md`。

## 7. 故障恢复结果

| 场景 | 结果 |
|---|---|
| Worker 正常退出/重启 | PASS |
| SIGSTOP → 租约过期 → 接管 → SIGCONT | PASS（实际由 w2 接管，`fencing 1→2`；旧 Worker 恢复后不覆盖终态、证据 25→25） |
| API 强制终止 + 重启 | PASS（队列 Run 继续完成，重启后回读） |
| PostgreSQL 连接全部终止 | PASS（API/Worker 恢复；首个 `/execute` 曾 500，已修 `pool_pre_ping`） |
| SSE 断开 + 游标续传 | PASS（无重复事件） |
| 全部 Worker 重启 | PASS |
| SIGKILL 专项 | 60s 观察窗口未命中持租约 Worker（2 次 skip）；WP1 已真实覆盖 SIGKILL + 接管 |

未出现：重复执行、重复提交、永久卡死、状态倒退、Evidence 丢失、旧 Worker 越权提交。

## 8. AI 真实模型验证状态

- DeepSeek 真实 Smoke **11/11**（连通性、Flash/Pro、JSON、超时、截断、成本、脱敏）；
- 诊断顾问 6 场景：结构合法率 1.0、Schema 1.0、证据支持 1.0、无效 0、重复 0、
  Fallback 0、归一化采纳率 0.833（规则 0.833）、平均 3005ms、6 次 $0.001116；
- n=6，仅回归/相对比较，不外推总体准确率；
- Compiler/Probe/Repair 三类顾问未单独真实 Smoke，保持 IMPLEMENTED / 未验证真实质量。

详见 `docs/RUN-013-WP5-AI-BENCHMARK.md`。

## 9. Benchmark 新实验结果

| 项 | 状态 | 说明 |
|---|---|---|
| 23 场景 × 2 轮 | **NOT_RUN** | 60 分钟长稳占用同一宿主与隔离执行环境；未并行启动以免污染 |
| 历史归档 | 未修改 | `docs/assets/run011-r1/benchmark/v5-2rounds.json` 哈希不变 |
| 历史 False Pass | **PARTIAL / INFERRED_ONLY** | 保持 RUN-012 Errata 口径 |
| FakeProvider | 与真实 Provider 分组 | 不混算 |

## 10. 系统代码审查结果

- 后端：认证/授权、Workspace 过滤、对象级权限、CAS/幂等、租约/Fencing、事件/证据、审计链、
  SSE、诊断/Repair/Sandbox/Promotion/Rollback 逐项审查；
- 发现并修复 4 个真实缺陷（SSE 鉴权、旧 Worker 写证据、Oracle 回放失效、健康计数泄露）；
- 保留风险：SSE 撤销不主动断旧连接、`/queue/stats` 全局运维可见、Artifact 元数据缺失兜底；
- 前端 15 视图：加载/空/失败/权限/Workspace/搜索/SSE/证据/深色/移动端抽查，未发现“只 Toast 不落库”的按钮。

详见 `docs/RUN-013-WP6-SYSTEM-REVIEW.md`。

## 11. 前端产品化改进

- 用例详情稳定性回放面板（替代仅按钮 + Toast）；
- 审核条件可视化（服务端 problems）；
- Workspace 多标签同步、身份变化清理、竞态防护；
- 390px 深色无横向溢出。

## 12. PostgreSQL 16 结果

真实 PostgreSQL **16.16** 独立库：平台 / 一致性 / Workspace 专项 **16 passed**；
另有 60 分钟长稳（113 Run）与数据库连接中断恢复验证。SQLite 全量回归不受影响。

## 13. CI 真实运行证据

分支 `workbuddy/run013-production-reliability` 已推送至 `3fc5289`，PR Gate **真实触发并成功**：

- run **`45658081853`**，head SHA `3fc5289`，`backend` 与 `frontend` 两个 Job 均
  `completed successfully`（2026-11-26 00:52 CST 前后）；
- 触发规则已加入 `workbuddy/run013-*`（此前不会触发）；
- 最终报告/证据提交会再触发一次 PR Gate；代码交付 HEAD 的 CI 证据为上述 run。
- Nightly / Release Gate 未触发（需 `workflow_dispatch` 与凭据）：**NOT_RUN**。

## 14. 所有测试统计

| 门禁 | 结果 |
|---|---|
| 全量 pytest | **771 passed, 16 skipped**（skip 均为 PG 专项，已在真实 PG16 单独跑通） |
| PostgreSQL 16 专项 | **16 passed** |
| 安全专项（审核门禁 / 全局观测 RBAC / 认证 RBAC / 指标权限 / Workspace 隔离） | **66 passed** |
| WP1 独立 Worker UI | 56 条记录 = 55 PASS / 0 FAIL / 1 NOTE |
| WP2 API 并发/授权 | 8 passed |
| WP2 真实浏览器 | 10 passed / 0 failed |
| WP3 门禁 | 12 passed |
| WP3 真实浏览器 | 15 passed / 0 failed |
| WP5 真实 LLM Smoke | 11/11 |
| WP5 AI 质量评估 | n=6（见 WP5 报告） |
| Ruff check / format | All checks passed / 229 files formatted |
| 前端 `npm run build` | built（vue-tsc + vite） |
| git diff --check | 通过 |

## 15. 修改文件清单

本轮共 **76 个文件，+20007 / −122**（含证据截图/日志）。按类别：

| 类别 | 文件 |
|---|---|
| 执行内核 | `packages/execution/.../runtime/pipeline.py`、`tasks/service.py` |
| 持久化 | `packages/persistence/.../catalog.py`、`stores.py`、`db.py`、`models.py`（引用） |
| API | `apps/api/.../main.py`、`runtime.py`、`routers/system.py`、`routers/intelligence.py` |
| Worker | `apps/workers/traceguard_worker/main.py` |
| 配置 | `packages/core/traceguard_core/config.py` |
| 前端 | `apps/web/src/workspace.ts`、`App.vue`、`views/TestCaseDetailView.vue` |
| 脚本 | `scripts/business_loop_ui_acceptance_run012.py`、`run013.py`、`workspace_concurrency_acceptance_run013.py`、`stability_replay_acceptance_run013.py`、`soak_run013.py`、`ai_quality_eval_run013.py` |
| 测试 | `tests/unit/test_tasks.py`、`tests/integration/test_workspace_concurrency_run013.py`、`test_stability_gate.py` |
| CI | `.github/workflows/pr-gate.yml`（新增 `workbuddy/run013-*` 触发） |
| 文档 | `docs/RUN-013-*.md`、`README.md`、`FEATURE-STATUS.md`、`DEPLOYMENT.md`、`API.md`、`ARCHITECTURE.md` |
| 证据 | `docs/assets/run013/{wp1,wp2,wp3,soak,wp5}/`（JSON/截图/日志/采样） |

## 16. 所有提交 SHA

```
3fc5289 RUN-013 WP6: 健康计数隔离、部署运维手册与系统审查
e2ceff7 RUN-013 WP4: 60 分钟 PG16 长稳、故障恢复与连接池预探测
3852796 RUN-013 WP5: 真实 Provider Smoke 与 AI 质量量化评估
bfa3f25 RUN-013 WP3: Oracle 变更后的回放重核验与审核条件可视化
fbebfe7 RUN-013 WP2: SSE 事件流 Workspace 授权与前端并发上下文隔离
3184b2d RUN-013 WP1: 独立 Worker 部署真实 UI 全链路验收与租约失效写入防护
ebc2ca3 RUN-013 WP0: 修正 RUN-012 交付证据口径并冻结基线
```

基线 `90f6f4e` 未改写；未使用 `reset --hard` / `git clean` / 强制推送；`main` 未修改；
历史 RUN-012 证据文件未被覆盖（只追加修正与 Errata 引用）。

## 17. 剩余缺陷

1. SSE 授权撤销不主动断开已建立的长连接；下一次读取/重连才拒绝（连接级撤销未实现）。
2. `/queue/stats` 为全局队列状态，普通登录用户可读（不含业务内容，属运维观测）。
3. 长稳中两次 SIGKILL 场景因 60s 观察窗口未命中持租约 Worker，skip；SIGKILL 由 WP1 覆盖。
4. 数据库中断后首个 `/execute` 500 已修 `pool_pre_ping`，但未再跑 60 分钟复验。
5. Benchmark 23×2 未执行（长稳占用隔离环境），历史 False Pass 仍 PARTIAL。
6. AI 质量样本 n=6，不能外推；Compiler/Probe/Repair 未做真实模型 Smoke。
7. 前端 15 视图未逐按钮重审；本轮只改与 Workspace/回放相关的真实缺陷。

## 18. BLOCKED

| 项 | 原因 | 解除条件 |
|---|---|---|
| B-17 Worker 四场景故障注入 | 历史阻塞（AgentDR 拦截） | 宿主执行许可与白名单 |
| 宿主级网络隔离 | 需容器/VM 能力 | 提供隔离环境 |
| 外部真实站点 | 未授权 | 书面授权与白名单 |
| 真实人工基线 | 无人工评审资源 | 安排独立评审 |

## 19. NEEDS_DECISION

| 项 | 状态 |
|---|---|
| P03 Oracle 多来源冲突裁决 | NEEDS_DECISION（未改冲突契约） |
| P08 Repair 审批角色矩阵 | NEEDS_DECISION（未改审批模型） |

## 20. 下一阶段建议

1. 独立窗口执行 Benchmark 23×2，并保存 `fault_state` 真实快照；
2. 扩大 AI 质量样本（≥30）并统一域标签后比较规则/LLM；
3. 对 Compiler/Probe/Repair 做真实模型 Smoke 与成本上限评估；
4. SSE 授权撤销主动断开（连接级撤销）；
5. 生产化：容器化部署、宿主级网络隔离、集中日志与告警。
