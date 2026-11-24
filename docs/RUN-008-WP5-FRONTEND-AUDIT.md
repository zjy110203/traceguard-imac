# RUN-008 WP5：前端 14 视图审查与修复

## 1. 审查范围与结论

`apps/web/src/views/` 共 **14 个视图**（`router.ts` 14 条组件路由一一对应；旧文档的「15 视图」
是把 RunDetail 的证据图 Tab 单列）。基于源码逐视图建立验收矩阵，覆盖：真实 API 数据、
关键交互、加载/空/错误态、Evidence 联动、SSE、1440/1024/768/390 响应式、深色模式。
完整矩阵见 `docs/assets/run008/wp5/ui/acceptance.json` 与本报告附表。

## 2. 已修复的真实问题

| # | 原问题 | 修复 | 验证 |
|---|---|---|---|
| 1 | 全局搜索把 `intent_id` 链到不存在的 `/intents/:id`，点击后跳首页 | `App.vue` 改为 `/intents?id=<id>`；`IntentsView.vue` 读取 query 并真实过滤（含 watch 路由变化） | 真实浏览器：href、落点 URL、意图可见三项全通过 |
| 2 | `DiagnosisDetailView` 对证据/探针计划/建议 `.catch(()=>null/[])` 静默吞错，显示「无数据」误导 | 累计 `partialError` 并在页面顶部 `alert-line` 明确提示失败项 | 类型检查/构建通过 |
| 3 | `RepairsView.onCaseChange()` 无 try/catch，切换用例失败会未捕获 rejection | 加 catch、清空版本/步骤、`notifyError` | 类型检查/构建通过 |
| 4 | `SystemView.probeBrowser()` 在 `load()` 吞错后仍按旧值提示「浏览器可用/不可用」 | `load()` 返回成功标志；失败走 `notifyError`，不显示误导结论 | 类型检查/构建通过 |
| 5 | `RunDetailView` 错误卡片硬编码浅色 `#fecdca/#7a271a`，深色下不可读 | 改用 `var(--fail-border)` / `var(--fail)` | 深色主题像素断言通过 |
| 6 | `scripts/ui_acceptance.py` 用 `emulate_media(color_scheme=dark)` 验深色，主题实际由 `tg-theme` 驱动 → **恒真假证据** | 改为 `add_init_script` 写 `tg-theme=dark` + `data-theme` 与 body 亮度断言 | 实测 `data-theme=dark`、`rgb(13,18,28)`、lum=19.7 |
| 7 | `events.ts` SSE 连续 error 时每次 `stopPolling()+startPolling()` 重置计时器，轮询可能永不触发 | 仅调用幂等的 `startPolling()`，不重建计时器 | 类型检查/构建通过 |

未修复的低优先级项：`RunsView` 仅在有 active Run 时轮询，外部新建 Run 需手动刷新（不影响正确性）。

## 3. 真实验收证据

| 套件 | 命令 | 结果 | 证据 |
|---|---|---|---|
| 14 视图 ×断点 ×主题 | `scripts/ui_review_run007.py --base-url http://127.0.0.1:5173` | **60 checks 全过**：content 0 fail、overflow 0 fail、console 0 fail、pageerror 0、dark 0 fail（48 light + 12 dark），64 张截图 | `docs/assets/run008/wp5/ui/` |
| 旧验收脚本（修复后） | `scripts/ui_acceptance.py` | **30 passed / 0 failed**（含真实 Run 数据） | `docs/assets/run008/wp5/ui-legacy2/` |
| 针对性回归 | 真实 Chromium（搜索路由/深色/390px/页面错误） | **4/4 通过** | `docs/assets/run008/wp5/fix-verification.json` |
| 类型与构建 | `npm run typecheck && npm run build` | 通过，2198 modules | `logs/run008/web-typecheck-wp5.log`、`web-build-wp5.log` |

所有验收在隔离环境执行：TeamFlow `8050`、API `8000`、Vite `5173`，隔离库
`.traceguard/run008/wp5/`，未触碰长稳专用端口 `8041/8042` 与数据。

## 4. 披露

- 60/60 是**渲染/溢出/控制台/主题**层面的自动检查，不等于所有业务交互路径已被脚本覆盖；
  批量执行、证据链接、静默失败等路径本次通过源码审查 + 定点浏览器验证，未逐条自动化。
- 旧脚本此前「深色通过」是假证据，已修复；历史 RUN-007 报告中的该条不应再被引用为深色证明。
