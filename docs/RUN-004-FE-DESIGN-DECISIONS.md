# RUN-004-FE 前端产品化重构：设计决策

- 运行编号：RUN-004-FE
- 日期：2026-11-23
- 仓库：`https://github.com/zjy110203/traceguard-imac`
- 分支：`workbuddy/traceguard-autonomous`（保留本地分支与未提交修改，不切换、不推送）
- 基线：RUN-003 / RUN-003-G，222 项后端测试通过
- 文档性质：本轮设计与实施决策记录。RUN-002 / RUN-003 历史报告保持原样。

## 1. 审计结论

### 1.1 工程基线

| 项 | 审计结果 |
| --- | --- |
| 前端 | Vue 3.5 + TypeScript 5.6 + Vue Router 4 + Vite 5；13 个视图、9 个组件；`pinia` 已安装但未使用 |
| 样式 | 单文件 `style.css`（404 行），token 已存在但未系统化；深色 token 预留但未启用；≤820px 直接隐藏侧栏 |
| 图标 | Unicode 字符（◈ ◎ ▤ 等），无统一图标库 |
| 图表 | 仅 `Sparkline.vue`（自研 SVG），无 ECharts / Vue Flow |
| API | `src/api.ts` 只有通用客户端；业务端点散落在各视图；`/api/v1/system/*` 无 LLM 汇总端点 |
| LLM 真实数据 | `TokenUsageTracker` 写入 `.traceguard/llm_usage.jsonl`，只有 `usage_for_date()` 单日聚合；无 by_agent / by_model / daily series 查询 |
| 测试 | 后端约 222 项通过；前端无测试框架；RUN-003 有 7 张真实浏览器截图（人工验收） |

### 1.2 必须修复的真实性问题

1. `ExplorerView.vue:2-3, 138-141, 226` 硬编码“规则实现（rule-based-v1），不是 LLM”与“（规则实现）”后缀，与 RUN-003-G 的真实 DeepSeek 接入冲突。
2. Explorer 未消费后端已返回的 `engine`、`llm_stats.llm_calls/fallback_calls/last_error`、`model_version`，无法区分真实 LLM 决策、规则决策与 LLM 失败后的规则兜底。
3. Explorer 审计标题取 `gateway_audit.slice(0, 30)`，JsonBlock 渲染全量，计数与内容不一致；`intents.slice(0, 6)` 导致其余需求无法发起探索；已编译探索无法重新编译。
4. `App.vue:79` 顶栏写死“Playwright · 真实浏览器”，与 `/system/browser` 的真实可用性无关。
5. `IntentsView.vue` 的 `lastCompiled` 是本地 ref，刷新即消失，属于假状态。
6. `DiagnosisDetailView.vue:125` 只以 `by llm:...` 文本展示 LLM 候选，未标记 `CANDIDATE`；`llm_advisor.called/skipped/added` 不可见。
7. `TestCaseDetailView.vue:36` 用 `startsWith(id)` 匹配执行历史，可能把 `tc_1` 与 `tc_10` 混淆。
8. 移动端隐藏侧栏且无替代导航；触控目标、图表对比度、 prefers-reduced-motion 未处理。

### 1.3 后端能力缺口

- 无 `/api/v1/system/llm/summary`，模型运行中心无法获得真实 provider / 调用 / token / 成本 / 延迟 / 分 agent 分布。
- `/api/v1/system/config` 不暴露 LLM 配置；`configured`（静态配置）与 `connected`（真实 `/models` 连通性）尚无接口区分。

## 2. 依赖选型

| 候选 | 结论 | 理由 |
| --- | --- | --- |
| shadcn-vue | 不引入 | 需要复制大量组件源码并绑定 Tailwind 主题，当前 13 个视图已有业务结构，全量迁移成本高；本轮用统一 token + 本地组件层可达到同等一致性 |
| Naive UI | 不引入 | 组件主题与自研 token 需二次对齐，且会改变表单/表格默认交互；迁移收益低于风险 |
| PrimeVue | 不引入 | 体量与主题体系偏重，本轮需要的是统一设计语言而非替换全部控件 |
| **@lucide/vue** | **引入** | 官方 Vue 包，`peerDependencies: vue >=3.0.1`，树摇后只打包使用到的图标；替代 Unicode 作为主要功能图标，提升专业工具辨识度。`lucide-vue-next@1.0.0` 已标记 deprecated，故使用官方 `@lucide/vue@1.77.0` |
| Apache ECharts | 不引入 | 本轮只需趋势、四态分布、指标差异三类图；自研 SVG 可直接绑定真实统计口径、样本量与点击下钻，避免约 1MB 运行时与命令式生命周期 |
| Vue Flow | 不引入 | `GraphView.vue` 已有节点点击/邻接高亮；本轮在其上增加筛选、缩放、平移、定位即可满足需求，换库不产生业务价值 |

新增运行时依赖只有一个：`@lucide/vue`。保留 Vue / Vue Router / TypeScript / Vite；`pinia` 原样保留（避免无谓删除既有依赖），仍不启用。零新增依赖不是目标，可维护性才是目标。

## 3. 设计系统

### 3.1 气质与布局

定位：专业开发者工具 + 现代 Observability 平台 + AI Agent 工作台。采用浅色为主、紧凑信息密度、单一强调色（靛蓝）、语义色严格绑定业务状态。

AppShell：

- 侧栏：品牌区、分组导航（工作台 / 质量闭环 / 运行）、Lucide 图标、当前项高亮、可折叠、待办计数、底部 DB / Worker / 浏览器连接状态。
- 顶栏：面包屑（由 route meta 生成）、Workspace、环境标识、后端健康、模型状态、全局搜索、主题切换、移动端抽屉按钮。
- 内容区：统一 `PageHeader`（标题 + 说明 + 操作），统一卡片间距与表格样式。

禁止：大面积渐变、玻璃拟态、无意义发光、每块区域都套相同卡片、装饰性动态图表、假实时动画。

### 3.2 Token 与状态语义

在 `:root` 统一定义：背景、表面、边框、文本、Primary、Success、Danger、Warning、Inconclusive、Neutral，以及间距、圆角、阴影、字体阶梯、`--sidebar-*`。

状态语义不可变：

| 状态 | 颜色 | 附加标识 |
| --- | --- | --- |
| PASSED | 绿 | 文字 + Check 图标 |
| FAILED | 红 | 文字 + X 图标 |
| INCONCLUSIVE | 紫 | 文字 + HelpCircle 图标 |
| NOT_EVALUATED | 灰 | 文字 + Minus 图标 |
| QUEUED / RUNNING | 橙 | 文字 + 动态可禁用图标 |

状态不得只靠颜色表达；`StatusBadge` 是唯一出口。深色主题 token 完成覆盖后提供切换入口，默认浅色，并持久化到 `localStorage`。

### 3.3 组件体系

新增/统一：`AppShell`（在 App.vue 内组合）、`PageHeader`、`MetricCard`、`StatusBadge`、`DataTable`（表格样式统一）、`FilterBar`、`SearchInput`、`Timeline`、`EvidenceCard`、`AsyncBlock`（loading/error/empty 三态）、`DetailDrawer`、`ConfirmDialog`、`JsonBlock`、`CodeDiff`、`ChartContainer`、`TrendChart`、`DonutChart`、`BarChart`、`ToastHost`、`Breadcrumb`。

组件只负责展示与交互；所有统计口径由视图层基于真实 API 数据计算，并在 UI 标注口径与样本量。

## 4. 页面设计

### 4.1 Dashboard（P0）

四层信息架构：关键指标 → 趋势与分布 → 执行队列与 Worker → 待办与事件。

- 通过率口径：`PASSED / (PASSED + FAILED + INCONCLUSIVE)`，明确排除 `NOT_EVALUATED`，显示分母。
- 趋势：按 Run 的 `created_at` 真实时间桶（近 7 日）分桶计算，不是累计计数包装；每桶显示样本量。
- 四态分布 / 失败类型分布：带图例、样本量、点击下钻到对应筛选后的列表。
- 待办：待诊断（FAILED / INCONCLUSIVE 且无诊断）、待审批修复（PENDING）、执行中任务。
- 所有数字来自 `/runs`、`/testcases`、`/diagnoses`、`/repairs`、`/explorations`、`/queue/stats`。

### 4.2 Agent Studio（P0）

三栏：探索任务列表 / Agent 执行轨迹 / 当前步骤详情 + 编译产物。

页头展示真实 `engine`、`model`、`prompt_version`、`llm_calls`、`fallback_calls`、`last_error`、耗时。决策来源分为三类并显式标记：

- `llm:<model>` 且无 fallback → 真实 LLM 决策
- `rule-based-v1` → 规则规划决策
- `fallback_calls > 0` 或 reason 含 `fallback(rule)` → LLM 失败后的规则兜底（不记为 LLM 成功）

`engine=llm:deepseek` 只表示配置了 LLM 规划器，不等于每个动作都是 LLM 决策。Compiler 仍为规则编译器，页面明确标注 `compiler_version` 与 `model_version`，不得包装为 LLM Compiler。

### 4.3 Model Operations（P0，新增 `/models`）

数据源：`GET /api/v1/system/llm/summary`（只读）。展示 provider / 默认模型 / 推理模型、今日调用与错误、输入/输出 token、估算成本与预算使用率、平均延迟、分 Agent / 分模型 / Flash-Pro 分布、规则兜底统计、Prompt Version、最近调用记录（脱敏聚合）。

安全边界：不返回 API Key、完整 Prompt、页面内容或凭证；成本标注“应用侧估算，非 DeepSeek 平台账单”；`configured` 与 `connected` 分开显示，配置成功不等于连通成功；无数据显示“暂无数据”。

### 4.4 Run / Diagnosis / Repair / Benchmark（P1）

- Run 详情：保留主从布局，增加失败步骤 → 证据定位、Assertion → Evidence 跳转、证据来源与可信等级、截图放大、关联诊断/图/事件/用例上下文。
- Diagnosis：症状 → 假设 → 支持/反驳证据 → 确定性规则验证 → 根因 → Disposition 的链路；LLM 候选标记 `CANDIDATE` + 来源 badge；`llm_advisor.called/skipped/added` 可见；Evidence Graph 增加节点筛选、缩放、平移、定位、边说明。
- Repair：Proposal → Validate → Sandbox → Approval → Promotion → Regression → Rollback 生命周期；Diff 具备代码审查体验；危险操作二次确认；前端不绕过后端权限。
- Benchmark：多组对比、指标差异、逐场景明细、样本分布、失败原因；严格区分已验证 / 未验证 / BLOCKED / 样本不足，`n<10` 标注样本不足，不做统计显著结论。

### 4.5 其余页面（P2）

Intents / TestCases / TestCaseDetail / Oracles / Runs / Diagnoses / System 统一接入 PageHeader、MetricCard、StatusBadge、FilterBar、AsyncBlock 与响应式表格。Oracle 页面解释 Oracle 定义、关联 Assertion、版本、审批状态、数据来源和是否参与正式判定。

## 5. API 变更

新增只读端点：`GET /api/v1/system/llm/summary`。

建议在 `TokenUsageTracker` 增加公开聚合方法 `summary()`，读取已有 JSONL，不修改记录格式、不改变业务判定，返回：

```
configured, connected, provider, default_model, reasoning_model,
calls, errors, input_tokens, output_tokens, estimated_cost_usd,
cost_is_estimated, budget_usd, budget_exceeded,
by_agent, by_model, latency_summary, daily_series,
fallback_calls, prompt_versions, generated_at, note
```

`connected` 不在此端点主动触发外部网络请求：默认返回 `"unknown"`，由前端展示“未探测”；真实连通性由 SystemView 的浏览器/模型自检给出，避免只读摘要变成外部调用。

## 6. 响应式与可访问性

断点：1440 / 1280 / 1024 / 768 / 390 / 360。≤1024px 侧栏折叠为图标条；≤820px 使用可操作抽屉，顶栏保留菜单按钮。触控目标 ≥40px；表格横向滚动；状态附加文字/图标；`focus-visible` 强化；遵守 `prefers-reduced-motion`；对比度 ≥4.5:1。

## 7. 验收口径

- `npm run typecheck`、`npm run build`、后端 `pytest`、`ruff check` 全绿，222 项测试不得被破坏。
- 真实 Chromium + Playwright 覆盖主要页面与详情路由；截图保存在 `docs/assets/run004-fe/` 并逐张目检。
- 测试数据隔离：使用临时数据库与临时 trace root，不改动用户现有业务数据。
- 文档与实际代码一致；历史 RUN-002 / RUN-003 报告不改写。
