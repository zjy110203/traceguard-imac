# RUN-007 前端 UI 验收报告

- 时间：2026-11-23（GMT+8）
- 形式：**真实 Chromium 逐页截图 + 人工看图复核**（不是只跑脚本看绿灯）
- 证据：`docs/assets/run007/ui/`（79 张 PNG）+ `docs/assets/run007/ui/ui-review.json`
- 覆盖：**15 个视图 / 14 个分组 × 4 断点（1440 / 1024 / 768 / 390）+ Dark@1440 + 四态**
- 结论：**75 项检查全部通过**（内容缺失 0、横向溢出 0、console 错误 0、页面异常 0、暗色未生效 0）

> 口径说明：自动化只证明"关键内容出现、无横向溢出、无 console error、暗色真的生效"，
> **不等于**视觉美观已验收。下面第 3 节是人工看图后的判断与已修问题。

---

## 1. 覆盖矩阵

| # | 视图 | 路径 | 1440 | 1024 | 768 | 390 | Dark@1440 | 人工复核要点 |
|---|---|---|---|---|---|---|---|---|
| 01 | 质量总览 Dashboard | `/` | ✅ | ✅ | ✅ | ✅ | ✅ | 指标卡数值真实；执行队列面板显示「独立 Worker / API Worker ID / 数据库 dialect / 服务 ok」 |
| 02 | 测试需求 Intents | `/intents` | ✅ | ✅ | ✅ | ✅ | ✅ | 状态与计数一致，空态有引导 |
| 03 | 测试资产 TestCases | `/testcases` | ✅ | ✅ | ✅ | ✅ | ✅ | 版本、审核状态、Oracle 绑定可见 |
| 04 | 用例详情 | `/testcases/{id}` | ✅ | ✅ | ✅ | ✅ | ✅ | 步骤/断言/Oracle 分区；审核门禁提示可见 |
| 05 | Oracle 契约 | `/oracles` | ✅ | ✅ | ✅ | ✅ | ✅ | 审批状态与版本清晰 |
| 06 | 执行中心 Runs | `/runs` | ✅ | ✅ | ✅ | ✅ | ✅ | 筛选栏窄屏整齐（本轮修复）；状态卡与表格一致 |
| 07 | Run 详情 | `/runs/{id}` | ✅ | ✅ | ✅ | ✅ | ✅ | **「执行归属」条**：Worker / attempt / fencing token / 队列状态 / 租约到期 |
| 08 | 证据图 Evidence Graph | `/runs/{id}`（证据图 Tab） | ✅ | ✅ | ✅ | ✅ | ✅ | 全图可见（本轮修复裁切）、图例可点、节点计数 46/57、适应画布 |
| 09 | Agent Studio | `/explorer` | ✅ | ✅ | ✅ | ✅ | ✅ | 探索轨迹与工具网关审计 |
| 10 | 模型运行 | `/models` | ✅ | ✅ | ✅ | ✅ | ✅ | LLM 配置与统计 |
| 11 | 诊断中心 | `/diagnoses` | ✅ | ✅ | ✅ | ✅ | ✅ | 结论/处置/置信度 |
| 12 | 诊断详情 | `/diagnoses/{id}` | ✅ | ✅ | ✅ | ✅ | ✅ | 假设、冲突、探针回灌、证据引用 |
| 13 | 修复中心 | `/repairs` | ✅ | ✅ | ✅ | ✅ | ✅ | 状态机（提案→沙箱→审批→晋升→回归→回滚） |
| 14 | Benchmark | `/benchmark` | ✅ | ✅ | ✅ | ✅ | ✅ | 多臂指标与样本量 |
| 15 | 系统与浏览器 | `/system` | ✅ | ✅ | ✅ | ✅ | ✅ | 健康、数据库、Worker、浏览器能力 |

## 2. 四态（真实触发，不是静态假数据）

| 状态 | 触发方式 | 截图 | 人工确认 |
|---|---|---|---|
| Loading | `page.route` 拦截 `/api/v1/**` 延迟 4s | `state-loading-runs.png` | 骨架屏显示，无空白闪烁 |
| Empty | `/runs?verdict=INCONCLUSIVE`（真实 0 结果） | `state-empty-runs.png` | 空态文案 + 引导存在 |
| Error | 打开不存在的 Run | `state-error-missing-run.png` | 错误提示明确，不白屏 |
| Success | Dashboard 真实数据 | `state-success-dashboard.png` | 指标/图表均渲染 |

## 3. 人工看图发现的问题与修复

### 3.1 证据图被裁切（已修）

- 现象：`08-run-evidence-graph` 首轮截图里节点超出画布右/下边界，右侧节点被切掉，
  本质是 SVG 使用固定 `viewBox` 且容器 `max-height: 520px`。
- 修复：`GraphView.vue` 引入 `NODE_W/NODE_H` 常量与 `fit()` 自适应缩放
  （`watch([graph, contentW, contentH], fit, {immediate: true})`），新增「适应画布」按钮，
  缩放下限 0.35；`style.css` 画布高度改为 `height: clamp(360px, 56vh, 560px)`。
- 复核：修复后截图显示 46 节点 / 57 边全部在画布内，可读性正常。

### 3.2 390px 筛选栏挤压错位（已修）

- 现象：`06-runs-390` 首轮截图里"包含 Benchmark 运行"复选框被拉满整行、文字被推到最右，
  "18 条"与下拉框挤在同一行换行错位。
- 根因：`.filter-bar input { flex: 1 1 140px }` 把 `input[type=checkbox]` 也拉宽了。
- 修复：新增 `.filter-check` 专用类（`inline-flex`，`justify-content: flex-start`），
  复选框 `flex: 0 0 auto; width/height: 16px`；`@media (max-width: 560px)` 下
  卡片头换行、筛选控件各占一行、`.actions` 独占一行右对齐。
- 复核：修复后 390px 下无横向溢出（`scrollWidth == clientWidth == 390`），
  筛选栏三行整齐，文字紧邻复选框。

### 3.3 暗色截图此前并非真暗色（已修，重要）

- 现象：本轮新增暗色断言后发现 **15 个 dark 检查全部未通过**：前端主题由
  `localStorage['tg-theme']` 决定，而不是 `prefers-color-scheme`，
  因此只设 `color_scheme="dark"` 得到的其实是**浅色页面被标成 dark**。
- 修复：`ctx.add_init_script` 写入 `localStorage['tg-theme']`；
  新增 `_theme_is_dark()` 断言（body 背景亮度 < 0.35 且文字亮度高于背景），
  不再"只信标签"。
- 复核：修复后 15/15 暗色检查通过；`01-dashboard-dark.png` 人工确认背景为深色、
  卡片边框与文字对比度正常、图表配色在深色下可读。
- **意义**：如果没有这条断言，本轮会交付一份"声称深色已验收、实际没截到深色"的假证据。

## 4. 后端配合改动（让界面有真实内容）

| 文件 | 改动 |
|---|---|
| `DashboardView.vue` | 拉 `/system/health`，明确区分「独立 Worker / 内嵌 Worker」，显示 API Worker ID、数据库状态与 dialect |
| `RunDetailView.vue` | 新增「执行归属」条（`Worker` / `attempt` / `fencing token` / 队列状态 / 租约到期或已释放） |
| `types.ts` | `Run` 增加 `lease_expires_at?: number` |
| `intelligence.py` | `diagnose` 支持 `use_llm` 参数（Benchmark 规则臂口径纯净） |

## 5. 未做 / 限制（不冒充完成）

1. **无自动化可访问性（a11y）审计**：未接 axe-core，未做对比度批量计算、键盘焦点顺序验证。
2. **移动端表格仍偏密**：390px 下表格退化为紧凑列表（可读但信息密度高），
   未做卡片化重排；这是设计取舍，不是缺陷，但确实不如桌面版舒适。
3. **截图是全页长图**：长页面（如 390 的 Runs）在高 DPI 下文字偏小，人工复核依赖放大查看。
4. **未做真实慢网/断网**：loading 态用 4s 人工延迟模拟，未做 3G 限速与离线重试界面验证。
5. **浏览器只有 Chromium**：未在 Firefox / WebKit 上验证。
