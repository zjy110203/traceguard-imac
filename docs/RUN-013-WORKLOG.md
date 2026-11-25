# RUN-013 工作日志

分支：`workbuddy/run013-production-reliability`
基线：`90f6f4e9e2178db9bd087af56fb3d8cf8d543ffe`（`workbuddy/run012-product-completion`）
原则：每个工作包独立提交；不删除旧测试、不弱化断言；NOT_RUN / BLOCKED 不写成 VERIFIED；
不修改 `main`、不自动合并 PR、不使用 `reset --hard` / `git clean` / 强制推送。

---

## WP0：RUN-012 交付证据修正与基线冻结

- **开始时间**：2026-11-25 23:05 CST
- **完成时间**：2026-11-25 23:25 CST

### 工作内容

1. 核对仓库状态：HEAD = `90f6f4e`，工作区干净；远端同名分支一致；`main` = `ac2876e` 未动。
2. 修正 `docs/RUN-012-FINAL-REPORT.md`：
   - 权威 HEAD 统一为 `90f6f4e`，标明代码交付 HEAD `7aa3ba5`；
   - WP0 测试口径改为可复现的 **39 passed**（14 新增 + 9 R1.2 + 16 基线 benchmark），保留「25 passed 为局部子集」说明；
   - WP4 改为 **30 条记录 = 29 PASS / 0 FAIL / 1 NOTE**（NOTE 为诊断 ID 记录项）；
   - 最终 CI 改为 run `45621959909`（head `90f6f4e`，backend/frontend 均成功），`45621143973` 保留为历史证据；
   - 提交序列补齐 `90f6f4e`。
3. 修正 `docs/RUN-012-WORKLOG.md`：WP0 测试数量与命令、WP4 记录口径、WP6 最终 CI 证据。
4. 修正 `docs/FEATURE-STATUS.md`：RUN-012 状态头 HEAD / CI / WP0 / WP4；全局观测权限表 CI 行；RUN-012 追加段的 PR Gate 行与 UI 闭环行。
5. 修正 `docs/RUN-012-WP4-BUSINESS-LOOP-UI.md`：29 passed → 30 条记录口径。
6. 新增 `docs/RUN-013-BASELINE.md`：基线、核心能力、已验证、未完整验证、BLOCKED、NEEDS_DECISION、本轮范围。

### 核对数据（原始 JSON / Actions）

| 数据源 | 结果 |
|---|---|
| `docs/assets/run012/wp4/ui/loop-acceptance.json` | `total=30 / passed=29 / failed=0`，`Counter: PASS=29, NOTE=1` |
| `docs/assets/run012/wp2/ui/workspace-acceptance.json` | `total=25 / passed=25 / failed=[]`（无需修正） |
| Benchmark 测试实跑 | 14 + 9 + 16 = **39 passed** |
| GitHub Actions run `45621959909` | head `90f6f4e`；`backend`、`frontend` 两 Job 均 `completed successfully` |
| 历史 False Pass | `docs/RUN-012-WP0-BENCHMARK-ERRATA.md`：PARTIAL / INFERRED_ONLY，保持 |

### 测试命令与结果

```bash
.venv/bin/python -m pytest tests/unit/test_benchmark_scenario_contract_run012.py \
  tests/unit/test_benchmark_v5_statistics_run011_r1_2.py tests/unit/test_benchmark.py -q
# 39 passed
```

### Commit

- `ebc2ca3` RUN-013 WP0: 修正 RUN-012 交付证据口径并冻结基线

### 未完成 / 下一步

- WP1：搭建 `TG_INLINE_WORKER=false` 的独立 API / Worker / TeamFlow 部署并跑真实 UI 全链路。


---

## WP1：独立 Worker 部署形态的真实 UI 全链路验收

- **开始/完成**：2026-11-25 23:05 → 23:21 CST

### 工作内容

1. `scripts/business_loop_ui_acceptance_run012.py` 支持 `--independent-workers` / `--lease-ttl` / `--faults`：
   `TG_INLINE_WORKER=false`，启动独立 Worker 进程；新增 `WorkerFleet`（SIGTERM/SIGKILL/SIGSTOP/SIGCONT）；
2. 新增 `scripts/business_loop_ui_acceptance_run013.py` 薄封装（2 Worker / TTL 6s / 故障注入）；
3. 新增 22 项 Worker 故障场景断言：启动失败、正常退出、执行中强杀、租约接管、
   旧 Worker 恢复、API 重启、客户端断连、双 Worker 并发幂等；
4. 修复真实缺陷：旧 Worker 恢复后继续写证据。新增 `TaskService.lease_valid()` 与
   `RunPipeline._ensure_lease()`（步骤前/观测后/状态证据前同步校验）；租约丢失走 abandon：
   不发 `run.failed`、不提交结果；`submit_result` 的 stale token 同样 abandon。

### 测试结果

- 真实浏览器 + 独立 API/Worker/TeamFlow：**56 条记录 = 55 PASS / 0 FAIL / 1 NOTE**；
- 首次运行暴露 `W1-⑮` 证据重复（19→38），修复后复验 19→19；
- `tests/unit/test_tasks.py` **11 passed**（含新增 lease_valid 同步守卫）。

### 证据 / Commit

- `docs/RUN-013-WP1-INDEPENDENT-WORKER.md`
- `docs/assets/run013/wp1/ui/independent-worker-acceptance.json` + 12 张截图 + Worker 日志
- `3184b2d` RUN-013 WP1

---

## WP2：多 Workspace 并发一致性与安全深度验证

- **开始/完成**：2026-11-25 23:10 → 23:35 CST

### 工作内容

1. 修复 `/events/stream` 未鉴权/未按 Workspace 过滤（严重）：显式解析会话、`scope_filter`、
   `list_events_after(workspace_ids=...)` 下推 SQL；
2. 前端 `workspace.ts`：`loadSeq`/`switchSeq` 防旧响应回填；`workspaceParams` 禁止覆盖
   `workspace_id`；加载失败也递增 generation；
3. `App.vue`：身份变化/401 清空上下文；多标签页 `storage` 同步；
4. 新增 `tests/integration/test_workspace_concurrency_run013.py`（8 例：SSE 鉴权、
   跨空间 403、游标绕过、并发读取、授权撤销、健康计数隔离）；
5. 新增 `scripts/workspace_concurrency_acceptance_run013.py`（真实浏览器 10 项）。

### 测试结果

- API 专项：**8 passed**；
- 真实浏览器：**10 passed / 0 failed**（alice/bob/multi/admin、localStorage 篡改、
  6 个并发越权请求全 403、快速切换、多标签同步、390px 深色无溢出）。

### 证据 / Commit

- `docs/RUN-013-WP2-WORKSPACE-CONCURRENCY.md`
- `docs/assets/run013/wp2/ui/workspace-concurrency-acceptance.json` + 2 张截图
- `fbebfe7` RUN-013 WP2

---

## WP3：稳定性回放到审核的完整闭环

- **开始/完成**：2026-11-25 23:15 → 23:33 CST

### 工作内容

1. 审查 CatalogService / stability_gate / StabilityReplayService / 审核 CAS；
2. 修复缺口：回放后 Oracle 版本变更/撤销仍沿用旧 `STABLE_PASS`。
   新增 `CatalogService._replay_oracle_problems()`，审核事务内重新核验 Oracle 版本、
   审批状态与 Workspace；
3. 新增只读预检 `GET /api/v1/testcases/{id}/versions/{v}/stability-gate`（与审核同一规则）；
4. TestCase Detail 新增稳定性回放面板：状态/进度/成功失败数/结论/可晋升/Oracle/浏览器/
   报告来源/审核条件/problems，运行中 2s 自动刷新 + 手动刷新；
5. 新增 `scripts/stability_replay_acceptance_run013.py`：compiler 与 healed 各一条真实闭环，
   以及无回放/失败回放/未完成回放三类负向路径。

### 测试结果

- `tests/integration/test_stability_gate.py` **12 passed**（含 Oracle 改绑/撤销两例）；
- 真实浏览器：**15 passed / 0 failed**；
- 首次运行因测试用子串 `STABLE_PASS` 误匹配 `STABILITY_NOT_STABLE_PASS` 导致假通过，
  已改为按“稳定性结论”字段精确判定，并在报告中保留两次失败尝试。

### 证据 / Commit

- `docs/RUN-013-WP3-STABILITY-REPLAY.md`
- `docs/assets/run013/wp3/ui/stability-replay-acceptance.json`
- `bfa3f25` RUN-013 WP3

---

## WP4：60 分钟长稳与真实故障恢复（PostgreSQL 16）

- **开始/完成**：2026-11-25 23:41 → 00:42 CST

### 实验

- 真实 PG 16.16 独立库 + 独立 API（`inline=false`）+ 2 独立 Worker + 独立 TeamFlow + 真实 Chromium；
- 每 30s 一个真实 Run，用例 `tcase_bench_normal2`（确定性通过）；
- 故障计划：SIGTERM、SIGKILL、SIGSTOP/租约过期/接管、API 重启 ×2、DB 连接终止、SSE 断连续传、全部 Worker 重启。

### 结果

- **60.05 分钟 / 113 个真实 Run（+5 故障 Run）/ 113 PASSED / 0 失败**；
- 0 重复步骤、0 孤儿步骤/证据、0 重复提交、0 缺失证据、0 重复事件、0 状态倒退；
- API RSS 100.8→99.4 MB、Worker 81.8→92.1 / 94.0→92.4 MB、DB 9.31→18.16 MB；
- API 健康延迟 P50 4.2ms / P95 7.0ms；Run 平均 4.80s；
- 原始检查 **28/31**：2 项为检查口径错误（接管实际由 w2 完成；`lease_owner` 是留痕），
  1 项为 DB 中断瞬间 `/execute` 500；已加 `pool_pre_ping=True`，定向复验 200（25ms）。
- 两次 SIGKILL 因 60s 窗口未命中持租约 Worker（skip）；WP1 已真实覆盖 SIGKILL。

### 修改文件

- 新增 `scripts/soak_run013.py`、`docs/RUN-013-WP4-SOAK.md`、`docs/assets/run013/soak/`；
- `packages/persistence/traceguard_persistence/db.py`：非 SQLite 引擎 `pool_pre_ping=True`；
- `TG_WORKER_METRICS_DIR` 与 Worker/API 资源快照；
- `Http.raw` 支持非 JSON 成功响应（SSE）。

### Commit

- `e2ceff7` RUN-013 WP4

---

## WP5：AI 真实能力与 Benchmark 科学性

- **开始/完成**：2026-11-25 23:20 → 23:40 CST

### 工作内容

1. 真实 DeepSeek Smoke（合法 `.env` 凭据，未打印/未导出）：11/11 passed；
2. 新增 `scripts/ai_quality_eval_run013.py`：6 个诊断场景，规则组 vs LLM 组同输入，
   Ground Truth 不进入模型；统计结构合法率、Schema、证据支持、无效/重复、采纳率、
   Fallback、延迟、Token、成本；
3. Benchmark 23×2 未在长稳期间并行执行，记录 NOT_RUN 与原因；历史归档未修改。

### 测试结果

- 真实 Provider：11/11；
- 质量评估：结构 1.0、Schema 1.0、证据支持 1.0、无效 0、重复 0、Fallback 0、
  归一化采纳率 0.833（规则 0.833）、平均 3005ms、6 次 $0.001116；
- 统计限制：n=6，不外推。

### 证据 / Commit

- `docs/RUN-013-WP5-AI-BENCHMARK.md`
- `docs/assets/run013/wp5/ai-quality-eval.json`
- `.workbuddy-run/llm-smoke-results.json`
- `3852796` RUN-013 WP5


---

## WP6：系统级代码审查与最终交付

- **开始/完成**：2026-11-25 23:35 → 2026-11-26 00:55 CST

### 审查与修复

1. `/system/health` 计数改为 Workspace 过滤 + SQL COUNT，修复全局规模泄露与 1000 截断；
2. 部署文档新增第 9 节（拓扑/健康检查/优雅退出/备份恢复/迁移/日志/生产清单），更正 PG 旧描述；
3. API/架构文档补 SSE 授权、健康计数、stability-gate；
4. 后端逐项审查（认证/授权/CAS/租约/证据/审计/SSE/诊断/Repair），前端 15 视图抽查；
5. 保留风险：SSE 撤销不主动断旧连接、`/queue/stats` 全局可见、Artifact 元数据兜底。

### 质量门禁

- 全量 pytest：**771 passed / 16 skipped**（16 skip 为 PG 专项）；
- 真实 PG16 专项：**16 passed**；
- 安全专项：**66 passed**；
- Ruff check / format：All checks passed / 229 files formatted；
- 前端 `npm run build`：built；
- `git diff --check`：通过。

### CI

- 代码交付 HEAD `3fc5289` 推送后 PR Gate 真实成功：run **`45658081853`**，
  `backend` / `frontend` 均 `completed successfully`；
- Nightly / Release Gate 仍未触发（NOT_RUN，无凭据）。

### 证据 / Commit

- `docs/RUN-013-FINAL-REPORT.md`、`docs/RUN-013-WP6-SYSTEM-REVIEW.md`、
  `docs/DEPLOYMENT.md`、`docs/FEATURE-STATUS.md`、`README.md`
- `3fc5289` RUN-013 WP6
