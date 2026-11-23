# RUN-004-FE UI 验收报告

- 日期：2026-11-23
- 浏览器：Chromium 159.0.8154.6（Playwright 真实浏览器，headless）
- 服务：隔离 API `http://127.0.0.1:8021`，临时数据库与用量文件副本位于 `/tmp/traceguard-run004fe`
- 数据隔离：使用现有数据的**副本**，未连接或修改用户业务库；Explorer 编译入口在副本上操作
- 机读结果：`docs/assets/run004-fe/verify-results.json`（53/53 passed，console_errors=0）

## 1. 页面与截图

| 页面 | 路由 | 截图 | 目检结论 |
| --- | --- | --- | --- |
| 质量指挥中心 | `/` | `dashboard.png` | 5 指标卡、真实时间分桶趋势、四态环图、失败类型、队列、待办分区清晰；无重叠/竖排 |
| Agent Studio | `/explorer` | `explorer.png` | 三栏布局完整；engine/prompt/LLM 调用可见；空态友好 |
| 模型运行中心 | `/models` | `models.png` | 配置/成本/趋势/分布/最近调用齐全；成本明确标注应用侧估算 |
| 执行中心 | `/runs` | `runs.png` | 筛选与列表正常，状态徽标统一 |
| 测试需求 | `/intents` | `intents.png` | 卡片列表与状态流转正常 |
| 测试资产 | `/testcases` | `testcases.png` | 表格与操作列正常 |
| Oracle 契约 | `/oracles` | `oracles.png` | 新增契约说明条，审批状态清晰 |
| Benchmark | `/benchmark` | `benchmark.png` | 多组对比、CI、样本不足提示可见 |
| 修复中心 | `/repairs` | `repairs.png` | 7 阶段生命周期与 Diff 正常 |
| 诊断中心 | `/diagnoses` | `diagnoses.png` | 指标卡与记录表清晰，状态不只靠颜色 |
| 系统与浏览器 | `/system` | `system.png` | 真实健康状态与产物列表可见 |
| Run 详情 | `/runs/:id` | `run-detail.png` | 步骤时间线 + 右侧详情面板 + Oracle 状态清晰 |
| 诊断详情 | `/diagnoses/:id` | `diagnosis-detail.png` | 推理链路与 LLM 顾问状态可见 |
| 移动端总览 | `/`（390×844） | `mobile-dashboard.png` | 抽屉导航可操作，卡片单列，文字可读 |
| 深色主题总览 | `/` | `dashboard-dark.png` | 卡片、图表、状态徽标在深色下可读 |

## 2. 交互验收（53 项）

通过的检查分组：

1. 渲染：11 个主要路由均可加载并出现统一页头。
2. 导航与搜索：全局搜索返回真实数据，Esc 可关闭；点图表可跳转到带筛选条件的执行列表。
3. Dashboard：四态图例为 4 项、趋势显示样本量、环图图例可点击下钻。
4. Agent Studio：`engine=llm:deepseek` 与 `deepseek:...explorer_planner@v1` 来自真实 API；LLM/兜底计数可见；页面已无“规则实现/不是 LLM”硬编码；步骤详情与编译产物可见。
5. Run 详情：进入详情、证据 Tab、Evidence Graph 控制条可用。
6. Diagnosis：6 段推理链路、LLM 顾问调用状态、LLM 候选标识可见。
7. Repair：7 阶段生命周期、Diff（代码审查式或结构化回退）可见。
8. Benchmark：对比图与验证状态（已验证/样本不足/BLOCKED）可见。
9. Model Operations：真实成本/调用数据可见，响应中无 API Key。
10. System：真实健康状态可见。
11. 主题与移动端：主题切换生效；390px 菜单可见、无横向溢出、抽屉可开可关。

## 3. 响应式验证

| 视口 | 横向溢出 | 导航入口 |
| --- | --- | --- |
| 1440×900 | 无 | 侧栏 + 顶部搜索 |
| 1280×800 | 无 | 侧栏 + 顶部搜索 |
| 1024×768 | 无（修复 Benchmark 表格撑破网格后） | 图标侧栏 |
| 768×1024 | 无 | 抽屉按钮 |
| 390×844 | 无 | 抽屉按钮，触控目标 ≥40px |
| 360×800 | 无 | 抽屉按钮，触控目标 ≥40px |

检查覆盖 `/`、`/explorer`、`/models`、`/runs`、`/repairs`、`/benchmark`。

## 4. 可访问性

- 状态统一使用颜色 + 图标 + 文字，不只靠颜色表达。
- 所有交互元素有 `:focus-visible` 高亮；按钮/输入有明确 `aria-label` 或可读文本。
- 支持键盘 Esc 关闭搜索与移动抽屉；主题切换按钮有 aria-label。
- `prefers-reduced-motion` 下禁用动画与过渡。
- 深色主题在 12 条路由上检查：背景、卡片、标题文字颜色正确，无浅色面板残留。

## 5. 目检中发现并修复的问题

1. 环图中心数字文字拦截了扇区点击 → 为 `.donut-value/.donut-label` 增加 `pointer-events: none`；同时验收脚本改用图例按钮作为主要点击入口。
2. 1024px 下 Benchmark 实验列表表格宽度撑破网格，产生 127px 横向溢出 → 为 `.bench-grid > *` 增加 `min-width: 0` 并限制面板体宽度。
3. 验收脚本最初在骨架屏阶段截图、把“证据 ↗”步骤按钮误当 Tab 点击，已修正等待条件与选择器；修正后 53/53 通过。

## 6. 限制

- 本轮没有在浏览器中重新执行 Probe / Approval / Promotion / Rollback 的完整写链路（属 RUN-003 已验收范围，且会改变业务状态）。
- 深色主题未对每一个弹窗逐张截图；这些元素使用与设计系统一致的 token。
- `verify-results.json` 中的 `base` 指向隔离服务端口 8021，仅用于本轮验收复现，不代表生产端口。
