# TraceGuard 产品设计规范（PRODUCT-DESIGN）

> 版本：v1.0（RUN-003-A）
> 本文档是前端设计与实现的一致性基线。视觉升级不改变任何业务契约与安全边界。

## 1. 产品定位

TraceGuard 是面向研发与 QA 团队的 **Agentic Web UI 测试与质量情报平台**：真实浏览器执行、
业务事实校验（Oracle）、证据可追溯、证据驱动诊断与 Oracle 保护的自我修复。

一句话定位：**"测试结果的每一句话都要有证据"** —— 视觉设计必须持续强化"可追溯、可验证、不夸大"的产品气质。

## 2. 用户角色与核心场景

| 角色 | 核心场景 | 高频页面 |
|---|---|---|
| QA 工程师 | 执行用例、查看失败原因、附证据提缺陷 | 执行中心、Run 详情、诊断中心 |
| 测试负责人 | 审核用例/Oracle/修复、分流处置、监控通过率 | 总览、修复中心、Oracle 契约 |
| 平台/研发 | 维护 Worker、浏览器、队列健康；排查平台自身问题 | 系统与浏览器、执行中心 |
| 算法/平台评估 | Benchmark 对照实验、指标口径核对 | Benchmark |

## 3. 设计调研结论

调研对象与可借鉴要点（2026-11-22，基于各产品公开文档与界面模式的研究，不复制任何界面）：

| 参照 | 借鉴点 | 在 TraceGuard 的落点 |
|---|---|---|
| Linear | 高信息密度下的克制：细边框、小字号阶梯、状态色仅作点缀 | 表格密度、badge 体系、圆角 6–8px |
| Datadog | 指标卡（label/value/hint 三段式）+ 多列栅格 + 时间范围选择 | MetricCard、总览栅格 |
| Grafana | 时间序列图与阈值色带；图例即数据 | 结论趋势 sparkline、分布条 |
| Sentry | 问题分流视角：待处理 → 归因 → 处置 | 诊断中心"待诊断/待审批"聚合 |
| LangSmith | Agent Trace 的逐步输入输出展示（run tree） | 探索动作序列、工具调用审计 |
| Playwright Trace Viewer | 左侧步骤时间线 + 右侧上下文详情（截图/网络/Console 同屏） | Run 详情"步骤 + 详情面板"双栏 |

明确不照搬：Datadog 的多租户复杂导航、Grafana 的自由拖拽仪表盘（超出当前需求且成本高）。

## 4. 信息架构

```
工作台        总览（质量指挥中心）
  ├─ 测试需求（TestIntent）
  ├─ 测试资产（TestCase）
  ├─ Oracle 契约
  ├─ 执行中心（Run 列表）
  └─ Agent 探索与编译（Explorer/Compiler）   ← RUN-003 新增
质量闭环      诊断中心 → 诊断详情
  ├─ 修复中心（Self-Healing）
  └─ Benchmark（UI-FaultBench）
运行          系统与浏览器
详情层        Run 详情 / 诊断详情 / TestCase 详情（面包屑进入）
```

导航分组对应业务闭环阶段：**准备（资产）→ 执行 → 归因（诊断）→ 修复 → 评估（Benchmark）**。

## 5. 统一设计规范

### 5.1 设计方向

专业开发者工具 × 现代 Observability 平台。克制、精确、高密度但留白充足；
**禁止**大面积渐变、发光、玻璃拟态等装饰效果；动效仅用于状态反馈（150–250ms）。

### 5.2 颜色（Design Tokens）

全部颜色走 CSS 变量（`src/style.css` `:root`），禁止组件内硬编码业务色。

| Token | 语义 | 值（浅色） |
|---|---|---|
| `--bg` | 应用底色 | #f6f7f9 |
| `--surface` | 卡片/面板 | #ffffff |
| `--surface-2` | 次级面板/表头 | #fbfcfd |
| `--border` / `--border-strong` | 边框 | #e3e6ea / #cfd5dc |
| `--text` / `--text-2` / `--text-3` | 文字三级 | #101828 / #475467 / #8a94a6 |
| `--brand` | 主色（操作/链接/选中） | #2f5bea |
| `--pass` / `--fail` / `--warn` | 结论三色 | #067647 / #b42318 / #b54708 |
| `--incon` | INCONCLUSIVE 专用 | #7a5af8（紫色，与 warn 橙区分） |

状态语义（全局唯一，不得混用）：
- **PASSED**=绿 · **FAILED**=红 · **INCONCLUSIVE**=紫 · **NOT_EVALUATED**=灰 · 执行中/等待=橙
- 侧栏为唯一深色区块（#0f1729），形成空间锚点；内容区恒为浅色。
- 深色主题：token 已预留 `data-theme="dark"` 切换面，默认浅色；在实现质量达标后开放切换（当前仅浅色发布，避免半成品主题）。

### 5.3 字体与字号阶梯

- 正文 `-apple-system / PingFang SC / Microsoft YaHei`，基准 **13.5px**，行高 1.55。
- 阶梯：h1 20 / h2 16 / h3 14 / 正文 13.5 / 次要 12.5 / 辅助 11.5；`letter-spacing: -0.01em`（标题）。
- 数字与 ID 一律等宽字体（`--mono`），12px，保证对齐与复制可用。

### 5.4 间距 / 圆角 / 阴影

- 间距基数 4px：卡片内 12–14，卡片间 12，区块间 14–16。
- 圆角：卡片 8（`--radius`）、控件 6、badge 20（胶囊）、弹窗 10。
- 阴影两级：`--shadow`（卡片常态）/ `--shadow-lg`（浮层）。禁止多层叠加阴影。

### 5.5 图标系统

统一 Unicode 符号集（`.ico`，16px 槽位）：◈ 总览 / ◎ 需求 / ▤ 资产 / ◇ Oracle /
▷ 执行 / ⇄ 探索 / ◐ 诊断 / ⟲ 修复 / ▩ Benchmark / ⚙ 系统。
不引入图标库运行时依赖；若未来需要线性图标，评估 Iconify 离线按需构建方案。

## 6. 组件规范

| 组件 | 职责 | 约定 |
|---|---|---|
| `AppShell`（App.vue） | 侧栏 + 顶栏 + 内容区 | 侧栏 218px；<820px 隐藏侧栏 |
| `MetricCard` | label/value/hint 三段指标卡 | tone: pass/fail/warn/neutral |
| `StatusBadge` | 全部状态展示的唯一出口 | 按 5.2 语义映射，禁止页面自造颜色 |
| `AsyncBlock` | loading / error / empty 三态容器 | 所有数据页必须包裹 |
| `Sparkline`（新增） | SVG 迷你趋势线 | 只接受真实序列数据 |
| `GraphView` | Evidence Graph 分层布局 | 悬停高亮邻接；节点点击弹详情 |
| `EvidenceGallery` | 证据网格 + 预览 | 图片缩略图；非图片显示类型徽标 |
| `JsonBlock` | JSON 展示 | 深色代码块，等宽 12px |
| `ModalShell` | 弹窗骨架 | 含 backdrop 点击关闭、Esc 关闭 |
| `ToastHost` | 全局轻提示 | 成功深灰/失败红；4s 自动消失 |

## 7. 页面交互规范

- **数据真实性**：所有指标来自 API 实时数据；无数据显示 EmptyState，禁止占位假数。
- **轮询**：执行中 2–5s 静默刷新（不闪 loading）；总览 5s；侧栏健康 15s。
- **主操作**：每页至多一个 primary 按钮；危险操作用 danger 且二次确认。
- **导航**：面包屑 `run.meta.crumb`；表格行点击进入详情；详情页可返回列表。
- **反馈**：异步操作用 toast；失败 toast 必须带后端错误消息（不吞错）。

## 8. 关键页面设计说明

### 8.1 质量指挥中心（总览）
第一屏回答四个问题：**现在质量如何？**（通过率+四态分布）/ **在跑什么？**（执行中+队列）/
**哪里坏了？**（失败类型分布）/ **要我处理什么？**（待诊断、待审批修复）。
布局：4 指标卡 → [结论趋势图 | 四态分布] → [失败类型分布 | 最近执行] → 待办区。

### 8.2 浏览器执行中心 / Run 详情
左：步骤时间线（可点击）；右：所选步骤详情面板（截图、网络请求、Console、观察数据）。
失败步骤红色标记并支持"跳到证据"。断言表支持跳转到对应证据 ID。

### 8.3 Agent 探索与编译中心（新增）
列表（探索状态/动作数/编译产物）+ 详情抽屉（动作序列逐步输入输出、ToolGateway 审计、
编译候选与警告）。**诚实标注**：页面不得把 engine 标记当成每一次动作都由 LLM 决策。

RUN-004-FE 更新：RUN-003-G 已真实接入 DeepSeek，页面改为依据 API 返回的 `engine`、
`prompt_version`、`llm_stats.llm_calls / fallback_calls / last_error` 动态展示 model/prompt
版本，并区分“LLM 决策 / 规则决策 / LLM 失败后的规则兜底”。Compiler 仍为规则编译器。
实现与验收见 `docs/RUN-004-FE-IMPLEMENTATION-REPORT.md`。

### 8.4 智能诊断与证据分析中心
假设卡片（状态/域/机制/支持与反对证据）+ GraphView 增强（节点点击详情）。
CONFIRMED 只能由确定性规则产生，UI 需标注"判定引擎：rules-v1"。

### 8.5 Self-Healing 修复中心
生命周期列（提案→沙箱→审批→晋升→回归→回滚）+ Diff 查看 + 审批具名表单。
所有越权/失败保持后端错误码透出。

### 8.6 UI-FaultBench
实验组别卡 + 指标表（分子/分母/Wilson CI）+ 样本明细。BLOCKED 组显示阻塞原因，不显示数值。

## 9. 响应式规范

- 断点：1240px（bench-grid 收合）/ 1150px（split 收合）/ 1100px（c4→c2）/ 820px（单列+侧栏隐藏）。
- 表格在窄屏容器内横向滚动（`.scroll-x`），不做卡片化重排（信息密度优先）。
- 触控目标 ≥ 32px（按钮 sm 保持 26px 高，仅限桌面密集表格内使用）。

## 10. 可访问性

- 颜色对比度：正文 ≥ 4.5:1（当前 token 均满足）；状态不得仅靠颜色区分（badge 同时有文字）。
- 焦点可见：全局 focus ring（`0 0 0 3px rgba(47,91,234,.12)` + 边框变色）。
- 语义化：表格用 th/td、按钮用 button、导航用 nav+a；弹窗支持 Esc。
- 图片有 title/alt 等价文本（截图缩略图带步骤说明）。

## 11. 技术选型与取舍（RUN-003-A 实际决策）

| 候选 | 决定 | 理由 |
|---|---|---|
| shadcn-vue + Tailwind | **不引入** | 现有 CSS token 体系完整；引入需重写全部页面 class，违背渐进式升级；收益主要是开发体验而非用户价值 |
| Apache ECharts | **不引入** | 总览仅需趋势线/分布条/环形占比，内联 SVG（<300 行）可高质量实现；避免 ~1MB 运行时依赖 |
| Vue Flow | **不引入** | GraphView 分层布局已满足语义图需求；本轮做交互增强（点击详情/高亮）而非换库 |
| Motion (animation lib) | **不引入** | CSS transition 足够；动效量少且固定 |
| Iconify | **不引入（暂）** | Unicode 符号集覆盖当前 10 个导航图标；无打包负担 |
| Pinia | 保留 | 已在依赖中，路由状态轻量使用 |
| 新增运行时依赖 | **0 个** | 六页面升级全部以现有技术栈实现 |

结论：本轮为**零依赖产品化升级**。设计质量来自 token 体系、信息架构与交互打磨，而非新工具。

## 12. 实现与验收对照

- 设计 token / 组件：`apps/web/src/style.css`、`apps/web/src/components/`
- 新页面：`apps/web/src/views/ExplorerView.vue`（路由 `/explorer`）
- 升级页面：Dashboard / RunDetail / Runs / Diagnoses / Repairs / Benchmark / System
- 验收：`npm run typecheck` + `npm run build` + Playwright E2E 真实页面交互 + 截图验收
  （见 docs/RUN-003-UI-ACCEPTANCE.md）
