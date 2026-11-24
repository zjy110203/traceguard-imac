# RUN-010 WP6 实施报告：前端可访问性（Frontend Accessibility）

> 生成时间：2026-11-24 CST（RUN-010-FINAL-CLOSEOUT 补齐）。代码提交：`48ae497`。
> 证据口径：axe 结论取自提交内的原始审查文件 `docs/assets/run010/wp6/axe-review.json`（真实 Chromium 运行输出，
> 未做人工修饰）；构建/类型检查结论取自 CI `PR Gate / frontend` 在最新 HEAD 的真实运行。
> 本轮收尾**没有重跑 axe**，因此不新增任何 axe 数字。

## 1. 结论摘要

| 项 | 结果 | 证据 |
|---|---|---|
| 表单标签修复 | 6 个视图、24 处 `label[for]`/`aria-label` 关联 | `git show 48ae497 -- apps/web/src/views` |
| axe-core 自动化审查 | **13 条业务路由，0 violations，0 个无标签输入** | `docs/assets/run010/wp6/axe-review.json`（`total_violations = 0`） |
| 对比度 | `--text-3` 提至 `#657183`（白底 4.95:1）、`--sidebar-muted` 提至 `#9aa6bd`（深底 6.01:1） | `apps/web/src/style.css` + axe `color-contrast` 规则 0 违规 |
| 登录 E2E | 2 个真实 Chromium 用例（登录全流程、viewer 无管理菜单） | `tests/e2e/test_login_ui_run010.py`；本地通过，CI 见 §6 |
| TypeScript / Vite 构建 | `vue-tsc --noEmit && vite build` 通过 | CI `PR Gate / frontend`（`npm run build`）在 `f69c39b` 上 success |
| RUN-009 BLOCKED 项（无自动化无障碍审查） | **解除** | axe-core 4.14.1 引入 devDependencies |

## 2. 六个视图的表单标签修复

| 视图文件 | 新增 label/aria | 具体修复 |
|---|---|---|
| `IntentsView.vue` | 5 | 搜索框 `aria-label`；标题/描述/Owner/候选断言 4 个 `label[for]`+`id` |
| `TestCasesView.vue` | 6 | 搜索与筛选控件 `aria-label`；表单字段补 `label[for]` |
| `OraclesView.vue` | 7 | Oracle ID / 关联断言 / 预期行为 / 需求引用 / 判定规则 / 审批人 全部 `label[for]` |
| `RunsView.vue` | 3 | 筛选下拉 `label[for]` + 搜索 `aria-label` |
| `DiagnosesView.vue` | 2 | 结论筛选 `<select id="diag-outcome" aria-label>`、Benchmark 包含开关 `aria-label` |
| `DiagnosisDetailView.vue` | 1 | 处置审核人 `<label for="disp-reviewer">` + `id` |

合计 **24 处**。修复方式是"可见 `<label for>` → 控件 `id`"，仅搜索/筛选这类视觉上不需要标签的
控件使用 `aria-label`（避免为了过审查塞入不可见文本）。

## 3. axe-core 自动化审查

- 引擎：`axe-core@4.14.1`（npm devDependency），由 `scripts/ui_review_run010.py` 在真实 Chromium
  中注入运行（登录态真实建立，不是静态 HTML 分析）；
- 规则集（13 条）：`label`、`button-name`、`image-alt`、`heading-order`、`th-has-data-cells`、
  `select-name`、`empty-table-header`、`aria-valid-attr-value`、`aria-allowed-attr`、`color-contrast`、
  `link-name`、`listitem`、`definition-list`；
- 额外校验：`label_check`（统计每条路由的输入控件数并列出未关联标签的控件）。

### 13 条路由的实际覆盖与结果

| 路由 | 路径 | violations | pass | 输入控件 | 无标签输入 |
|---|---|---|---|---|---|
| login | `/login` | 0 | 8 | 0 | 0 |
| dashboard | `/` | 0 | 8 | 0 | 0 |
| intents | `/intents` | 0 | 8 | 1 | 0 |
| testcases | `/testcases` | 0 | 10 | 2 | 0 |
| oracles | `/oracles` | 0 | 9 | 1 | 0 |
| runs | `/runs` | 0 | 8 | 3 | 0 |
| explorer | `/explorer` | 0 | 8 | 4 | 0 |
| models | `/models` | 0 | 8 | 0 | 0 |
| diagnoses | `/diagnoses` | 0 | 8 | 2 | 0 |
| repairs | `/repairs` | 0 | 7 | 1 | 0 |
| benchmark | `/benchmark` | 0 | 6 | 0 | 0 |
| system | `/system` | 0 | 8 | 0 | 0 |
| users | `/users` | 0 | 8 | 0 | 0 |
| **合计** | **13 路由** | **0** | **104** | **14** | **0** |

`fixed_views_recheck`（6 个 RUN-009 登记缺标签的视图逐一复验）：intents / testcases / oracles /
runs / diagnoses / users —— 未关联标签的输入全部为空列表。

### 0 violations 的原始证据

- 文件：`docs/assets/run010/wp6/axe-review.json`
- 关键字段：`"engine": "axe-core (npm)"`、`"routes": 13`、`"total_violations": 0`
- 说明：`/login` 记录中保留 5 条 `console_errors`（401 Unauthorized，未登录探活请求的预期结果），
  该噪声未被过滤或改写，如实保留。

## 4. 对比度修复

| Token | 修改前 | 修改后 | 对比度 |
|---|---|---|---|
| `--text-3`（正文次级文字，白底） | `#7c8798` | `#657183` | **4.95:1**（AA 正文 4.5:1 达标） |
| `--sidebar-muted`（侧栏说明文字，深底） | `#7d8aa1` | `#9aa6bd` | **6.01:1** |

axe 的 `color-contrast` 规则在 13 路由均为 0 违规，与上述数值一致。
深色主题下 `--text-3` 单独取 `#8b98ac`（未改动）。

## 5. 登录 E2E 结果

`tests/e2e/test_login_ui_run010.py`（真实 Chromium + 真实 uvicorn + 真实认证，`TG_AUTH_ENABLED=true`）：

| 用例 | 断言内容 | 本地结果 |
|---|---|---|
| `test_login_page_and_full_login_flow` | 未登录访问 `/runs` → 跳 `/login`；`label[for=login-username]` 存在；错误口令有提示；正确口令进入工作台；`/auth/me` 真实用户；会话过期 → 401 全局跳转；退出登录回登录页 | PASSED |
| `test_viewer_role_sees_no_admin_menu` | viewer 登录后管理菜单不可见 | PASSED |

本地全量回归中该模块 2 个用例均通过（见收尾报告 §5）。**CI 侧另有环境差异**：CI 此前未构建
前端产物，导致这两个用例在 Nightly/Release 中失败（详见 §6 与收尾报告 CL-02）。

## 6. TypeScript 与 Vite 构建结果

- 脚本：`"build": "vue-tsc --noEmit && vite build"`（`apps/web/package.json`），`vue-tsc@^3.3.14`；
- CI 证据：`PR Gate / frontend` 任务在 `f69c39b`（最新 HEAD）上 **success**，执行内容即
  `npm ci` + `npm run build`，说明类型检查与打包均通过；
- 本地：`apps/web/dist` 存在（`index.html` + `assets/`），登录 E2E 依赖该产物（见 CL-02）。

## 7. 未覆盖的无障碍检查范围（如实）

1. **键盘可达性**：Tab 顺序、焦点陷阱（弹窗/抽屉）、Esc 关闭、焦点可见性未做自动化断言；
2. **屏幕阅读器语义**：`aria-live` 区域（加载/错误提示播报）、表格排序状态播报、动态新增行播报未验证；
3. **缩放与重排**：200% 缩放、400% 重排、横竖屏切换未纳入 axe 范围（仅 RUN-007 做过 4 断点溢出检查）；
4. **动效**：`prefers-reduced-motion` 未检查；
5. **表单错误关联**：`aria-describedby` / `aria-invalid` 未覆盖（当前仅保证标签关联）；
6. **动态状态**：axe 只审查"当时渲染出的 DOM"；loading / empty / error / 权限受限等状态下的
   无障碍属性未逐一审查；
7. **深色主题全覆盖**：仅在浅色主题下跑满 13 路由；
8. **对比度**：只修了被 axe 命中的两个 token，未做全量色彩系统审计。

上述范围未做即未做，不以"13 路由 0 violations"外推为"整体无障碍合规"。

## 8. 与 WP1 的联动

- `/login` 与 `/users` 两条路由由 WP1（认证产品化）引入，本轮一并纳入 axe 审查与标签修复；
- 前端权限（菜单/路由级）与后端 RBAC 一致：viewer 不渲染管理菜单（E2E 覆盖），
  但**服务端仍强制鉴权**，前端隐藏不作为安全边界。
