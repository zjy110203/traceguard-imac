# RUN-012 最终报告：产品可用性收口

> 执行日期：2026-11-25（CST）｜ 分支：`workbuddy/run012-product-completion`
> 基线：`0ca128f`（RUN-011-R1.2）｜ 本轮 HEAD：`f64f0d7`
> 范围：**不推翻既有功能、不改写历史证据**；聚焦「上一轮留下的产品可用性缺口」——
> Benchmark 口径可信度、CI 可信度、多 Workspace 产品化、全局统计权限、
> 核心业务 UI 闭环、AI 可靠性。

---

## 0. 结论速览

| 工作包 | 主题 | 状态 | 核心证据 |
|---|---|---|---|
| WP0 | Benchmark 场景契约与 False Pass 口径 | ✅ 完成 | 专项 23 passed；原始归档哈希不变 + Errata |
| WP1 | CI 触发规则与 PostgreSQL 16 验收 | ✅ 完成 | PR Gate **真实触发并成功**（run `45578101070`）；PG16 专项 16 passed |
| WP2 | 多 Workspace 前端上下文 | ✅ 完成 | 真实浏览器 **25/25**（2 空间 × 3 角色 + admin + 伪造攻击 + 4 分辨率深色） |
| WP3 | 全局统计权限与真实聚合 | ✅ 完成 | 专项 **11 passed**（含 522 条 >500 截断阈值回归） |
| WP4 | 核心业务 UI 全链闭环 | ✅ 完成 | 真实浏览器闭环 **29/29**（真实 Worker + 真实 TeamFlow + 真实故障注入） |
| WP5 | AI 可靠性升级 | ✅ 完成 | 专项 **22 passed**（诊断顾问此前零覆盖） |
| WP6 | 质量门禁与最终交付 | ✅ 完成 | 全量 **760 passed**；PR Gate 全绿；PG16 16 passed；长稳 20/20 周期 |

**变更规模**：82 个文件，+6521 / −592 行；新增 6 个工作包文档、3 个专项测试文件、2 个真实浏览器验收脚本。

---

## 1. 本轮修复的产品缺陷

### 1.1 Benchmark 口径（WP0）

- **问题**：正向控制靠异常字符串 `"412 + run PASSED"` 识别；`False Pass` 依赖 `limit=500` 列表长度推断；
  23 个场景的 `expected_run_outcome` 无显式契约。
- **修复**：新增 `scripts/bench_scenario_contract.py`（场景 → 期望结果 → 记录分类的显式契约），
  重构分类与 False Pass 口径，保留完整执行上下文；新增 14 例契约测试。
- **历史证据保护**：`docs/assets/run011-r1/benchmark/v5-2rounds.json` 哈希未变，
  口径变更以 `docs/RUN-012-WP0-BENCHMARK-ERRATA.md` 追加说明，不改写原始归档。

### 1.2 CI 可信度（WP1）

- **问题**：PR Gate 的分支触发规则不含本轮分支 → 分支推送不会触发 CI；Ruff 格式门禁有大量未格式化文件 → CI 必红。
- **修复**：分支触发规则补齐；安全专项纳入门禁；格式化全仓。
- **真实验证**：推送后 PR Gate **两次真实触发并成功** ——
  WP1 推送后 run `45578101070`（head SHA `57a5ea7`，`conclusion=success`）；
  **最终交付 HEAD** run `45621143973`（head SHA `7aa3ba5`，2026-11-25 13:12:01Z → 13:13:08Z，`conclusion=success`）。
- **PG16 验收**：本地 PostgreSQL 16.16 上跑通平台/一致性/Workspace 专项 **16 passed**。

### 1.3 多 Workspace 产品化（WP2）

- **问题**：前端 14 处 `workspace_id=default` 硬编码，**没有任何 Workspace 选择器**；
  服务端按 Workspace 过滤后，前端仍显示全部空间的混合数据。
- **修复**：新增 `apps/web/src/workspace.ts` 统一上下文（服务端授权列表、偏好校验、失效回落、generation 缓存清理），
  15 个视图全部接入；`App.vue` 增加切换控件与 `:key` 强制重挂载；390px 溢出修复。
- **验收**：真实 Chromium **25/25**；伪造切换、localStorage 篡改、伪造请求 workspace_id 均被服务端拒绝。

### 1.4 全局统计越权与计数失真（WP3）

- **问题**：`/system/metrics` **无 Workspace 过滤** —— 任意登录用户可拿到全局 runs/evidence/intents 计数，
  可推断其他空间的业务规模与 Run verdict 分布；计数靠 `list_runs(limit=500)` 长度推断，>500 即失真。
  `/queue/stats` 同样问题。
- **修复**：两处改走 `scope_filter()` + SQL `COUNT(*)`/`GROUP BY`；response 显式声明 `scope` 与 `counting` 口径；
  前端 SystemView 展示统计范围与计数口径。
- **验收**：专项 11 例，含**造 522 条 intents** 验证计数不再被 500 截断。

### 1.5 核心业务 UI 无法闭环（WP4）

审查发现的真实缺陷（均由真实浏览器验收暴露）：

| # | 缺陷 | 影响 |
|---|---|---|
| 1 | **Oracle 绑定无 UI 入口** | 审核门禁要求必需断言绑定已审批 Oracle，但前端只能展示不能绑定 → UI 上**无法完成一次合法审核** |
| 2 | **版本列表接口结构与前端类型不匹配** | `GET /testcases/{id}/versions` 把断言/步骤放在 `doc` 内，前端未展开 → **断言表格、步骤轨迹、绑定控件整体不渲染** |
| 3 | **被测目标地址被硬编码** | 前端写死 `127.0.0.1:8001`，后端按 application 硬编码 → `TG_TEAMFLOW_BASE_URL` 配置**完全失效**，自定义端口打不到 |
| 4 | **稳定性回放无入口** | compiler/healed 用例审核前置条件在 UI 上无法触发 |
| 5 | **LLM 修复建议无入口** | 后端能力前端不可达 |
| 6 | **按钮不可达（UX）** | LLM 建议放页头但依赖弹窗内选择 → 被弹窗遮挡永远点不到（验收中发现，已移入弹窗） |

- **修复**：逐项补齐（Oracle 绑定控件 + 稳定性回放 + LLM 建议；版本 `doc` 展开；目标地址配置化）。
- **验收**：**29/29 真实浏览器闭环** —— Intent → TestCase → Oracle → 绑定 → 审核 → 执行 → 故障注入 → 诊断 →
  修复 → 沙箱 → 审批 → 晋升 → 回滚，每步服务端回读；真实 `observer_mode=playwright`、真实 Worker 消费队列、
  真实故障 TF-UI-002 产生 FAILED Run、真实沙箱复跑 PASSED、`promoted_version=2`、回滚留痕。

### 1.6 AI 可靠性（WP5）

- **问题**：四类 LLM 调用点中，**Diagnosis 顾问是唯一同时缺 Schema 校验、重试、fallback 标注、追踪与测试**的：
  返回裸 list、`LLMError` 直接冒泡，「调用失败」与「模型没给候选」无法区分，且**零测试覆盖**。
- **修复**：新增结构化 `DiagnosisAdvice`（called/fallback/error/model/dropped/attempts/warnings）、
  Schema 校验、有限重试（仅 `invalid_json`，且不与 provider 层重复计费）、
  fallback 标注、**无真实证据支撑的假设整条丢弃**（防凭空猜测）。
- **验收**：专项 22 例（顾问层 17 + 服务调用层 5），后者验证 LLM 失败时**规则诊断结论照常**、
  LLM 候选固定 `CANDIDATE` 永不 `CONFIRMED`、只追加不改写规则假设。

---

## 2. 质量门禁结果（WP6）

| 门禁 | 结果 |
|---|---|
| `ruff check`（packages/apps/tests/benchmark/scripts） | ✅ All checks passed |
| `ruff format --check` | ✅ 223 files already formatted |
| 单元 + 契约测试（PR Gate） | ✅ **375 passed** |
| 快速服务集成（PR Gate） | ✅ **51 passed** |
| 安全专项（审核门禁 / 全局观测 RBAC / 指标权限 / 认证 RBAC） | ✅ **57 passed** |
| PostgreSQL 16 专项（平台 / 一致性 / Workspace） | ✅ **16 passed**（真实 PG 16.16，独立库） |
| **全量回归** | ✅ **760 passed, 16 skipped**（16 skip 均为 PG 专项，已在真实 PG16 上单独跑通） |
| 前端类型检查 + 构建（vue-tsc + vite） | ✅ built |
| 长稳（soak） | ✅ **20/20 周期**，22 项检查全绿 |

### 长稳证据（`docs/assets/run012/wp6/soak-6min.json`）

- 6.14 分钟 / 20 周期 / 2 独立 Worker / API 内联 Worker 关闭；
- 20 个 Run 全部 `PASSED` + `COMPLETED`，0 API 错误、0 NOT_EVALUATED、0 超时；
- 0 Worker 死亡、0 重复事件、0 孤儿步骤、0 无证据 Run、0 未回收租约；
- DB `quick_check=ok`，260 events / 560 evidence / 160 steps；
- SSE 20 周期全部有事件（resume 9 次 / 无新事件 11 次），0 重复 ID。

---

## 3. 本轮新增的可回读证据

| 类别 | 位置 |
|---|---|
| 工作包报告 | `docs/RUN-012-WP0-BENCHMARK-ERRATA.md`、`WP1-CI-POSTGRES.md`、`WP2-WORKSPACE-UI.md`、`WP3-GLOBAL-METRICS.md`、`WP4-BUSINESS-LOOP-UI.md`、`WP5-AI-RELIABILITY.md` |
| 工作日志 | `docs/RUN-012-WORKLOG.md` |
| 场景契约快照 | `docs/assets/run012/wp0/scenario-contracts.json` |
| 多 Workspace 验收 | `docs/assets/run012/wp2/ui/workspace-acceptance.json` + 截图 |
| 业务闭环验收 | `docs/assets/run012/wp4/ui/loop-acceptance.json` + 12 张截图 |
| 长稳证据 | `docs/assets/run012/wp6/soak-6min.json` + `soak-samples.jsonl` |
| 新增测试 | `tests/unit/test_benchmark_scenario_contract_run012.py`（14）、`tests/integration/test_system_metrics_workspace_run012.py`（11）、`tests/unit/test_llm_diagnosis_advisor_run012.py`（17）、`tests/unit/test_diagnosis_llm_advisor_run012.py`（5）、`tests/integration/test_postgres_workspace_run012.py`（3） |
| 新增脚本 | `scripts/bench_scenario_contract.py`、`scripts/workspace_ui_acceptance_run012.py`、`scripts/business_loop_ui_acceptance_run012.py` |

---

## 4. 仍然受阻 / 明确未覆盖（不伪造通过）

| 项 | 状态 | 原因 |
|---|---|---|
| Nightly / Release Gate 真实触发 | **NOT_RUN** | 需 `workflow_dispatch` 与凭据，本机无；**PR Gate 已在最终 HEAD 真实成功**（run `45621143973`，head SHA `7aa3ba5`） |
| 23×2 真实 Benchmark 重跑 | **NOT_RUN** | 独立部署环境与成本约束；WP0 已修正口径并以 Errata 记录历史状态 |
| 真实 LLM 模型输出路径 | **NOT_RUN** | 本环境无凭据；契约与边界已由离线 FakeProvider 覆盖 22 例 |
| 真实用户/人工基线对比 | **BLOCKED** | 需要人工评审资源 |
| 宿主级网络隔离 | **BLOCKED** | 需宿主机/容器级能力，非用户态可解 |
| compiler/healed 用例「稳定性回放→审核」成功路径 | **部分覆盖** | 入口已接入且可点击；完整多轮回放成功路径未纳入本轮 |
| B-17（Worker 四场景故障注入） | **BLOCKED** | 历史遗留项，未在本轮解除 |

---

## 5. 提交序列

```
7aa3ba5 RUN-012 WP6: 质量门禁、长稳验收与最终报告交付
f64f0d7 RUN-012 WP5: 诊断顾问 Schema 校验、重试/Fallback 与追踪，补充离线测试
04b83c9 RUN-012 WP4: 打通核心业务 UI 闭环（Oracle 绑定/回放/LLM 建议 + 目标地址配置化）
b49116c RUN-012 WP3: 全局统计 Workspace 过滤与 SQL 真实聚合计数
1ac36e5 RUN-012 WP2: 打通多 Workspace 前端上下文与切换隔离
57a5ea7 RUN-012 WP1: 修复 CI 触发与格式门禁，完成 PG16 真实验收
44cfb46 RUN-012 WP0: 重构 Benchmark 场景契约与 False Pass 口径
```

基线 `0ca128f` 未被改写；未使用 `reset --hard` / `clean` / 强制推送；历史证据文件哈希保持不变；
未读取或导出任何真实凭据。

**最终 CI 证据**：分支 `workbuddy/run012-product-completion` 已推送至 `7aa3ba5`，
PR Gate **真实触发并成功** —— run `45621143973`，`conclusion=success`
（https://github.com/zjy110203/traceguard-imac/actions/runs/45621143973）。
