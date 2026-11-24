# RUN-009 WP1 完整前端业务验收与交互完善报告

- 任务：WP1（前端业务验收），负责人 general-purpose-1
- 基线 HEAD：`cb80591`（脚本运行期 `git rev-parse --short HEAD` 记录值）
- 执行方式：真实浏览器（Playwright Chromium）+ 真实 HTTP API + 真实 SQLite 隔离库；**无 Mock、无伪造截图、无复用其他页面截图**
- 结论：**PARTIAL** —— 功能与路由覆盖全部 VERIFIED；等价无障碍规则发现 6 个视图存在真实前端缺陷（未改共享源码，详见第 6 节）；axe-core 自动化审查 **BLOCKED**

---

## 1. 状态矩阵

| 维度 | 状态 | 说明 |
| --- | --- | --- |
| 隔离运行环境 | IMPLEMENTED / VERIFIED | TeamFlow `127.0.0.1:8011`、API `127.0.0.1:8020`、Web(Vite dev) `127.0.0.1:5175`；独立 DB `.traceguard/run009/wp1/traceguard.db`、`.traceguard/run009/wp1/teamflow.db`；未占用 8000/8042/8050/5173 |
| 真实 Run / Diagnosis 数据 | IMPLEMENTED / VERIFIED | 真实 API 执行 3 条用例，产生 3 个 Run、2 个 Diagnosis、3 份证据图；证据见 `docs/assets/run009/wp1/env/api-data-evidence.json` |
| 14 路由清单生成 | IMPLEMENTED / VERIFIED | 脚本直接解析 `apps/web/src/router.ts`，得到 14 条业务路由，显式排除 1 条通配路由；清单见 `route-manifest.json` |
| 14 路由 + 证据图子视图覆盖 | IMPLEMENTED / VERIFIED | 15 视图 × 4 断点 × 2 主题 = 120 项检查；0 skip；逐视图独立截图 |
| 四态 / 刷新 / 直接 URL / 前进后退 / 交互 | IMPLEMENTED / VERIFIED | Loading/Empty/Error/Success 四态真实截图；每视图均执行 reload、直接 URL、back/forward、安全真实交互 |
| 键盘可访问性基础 / ARIA / 表格可读性 | IMPLEMENTED / VERIFIED | 每视图执行 Tab 焦点检查与内置等价规则审查；发现问题见第 6 节 |
| axe 自动化无障碍审查 | **BLOCKED** | 隔离环境未安装 `axe-core` npm 包 / `axe` CLI，未联网注入 CDN 脚本；已用内置等价规则替代，但不等同于 axe 报告 |
| 整体结论 | **PARTIAL** | 功能全部通过；无障碍存在真实缺陷；axe 项目 BLOCKED |

---

## 2. 环境与方法

1. **端口预检**：启动前逐一确认 8000/8011/8020/8042/8050/5173/5175 均无监听，未复用其他 agent 的服务。
2. **服务启动**（日志在 `docs/assets/run009/wp1/env/`）：
   - TeamFlow：`scripts/run_teamflow.sh`，`TG_TEAMFLOW_PORT=8011`、`TG_TEAMFLOW_DB=sqlite:///./.traceguard/run009/wp1/teamflow.db`
   - TraceGuard API：`scripts/run_api.sh`，`TG_API_PORT=8020`、`TG_DATABASE_URL=sqlite:///./.traceguard/run009/wp1/traceguard.db`、`TG_TEAMFLOW_BASE_URL=http://127.0.0.1:8011`、`TG_LLM_PROVIDER=''`（关闭外部 LLM，走规则引擎，避免产生真实调用成本）、`TG_ARTIFACT_DIR=.traceguard/run009/wp1/artifacts`
   - Web：Vite dev `5175`，使用本 WP 自建配置 `docs/assets/run009/wp1/env/vite.config.mjs`（root 指向真实 `apps/web` 源码，`/api`、`/events` 代理到 8020）；**未修改仓库原有 `apps/web/vite.config.ts` 或 `apps/web/src/**`**
3. **数据产生**：`docs/assets/run009/wp1/env/prepare_data.py` 调用 `POST /api/v1/system/seed`、`POST /api/v1/testcases/{id}/execute {sync:true}`、`POST /api/v1/runs/{id}/diagnose`、`POST /api/v1/runs/{id}/graph`。真实返回：
   - `run_1795507968187ef5b9265075f` / `tcase_tf_create_normal` → **PASSED**
   - `run_17955079735698a9a2f1ad1da` / `tcase_tf_false_pass` → **FAILED** → `diagnosis_179550798160984c0529483a2`（CONFIRMED / PRODUCT_DEFECT）
   - `run_1795507978452d3699c8ceda3` / `tcase_tf_api_error` → **FAILED** → `diagnosis_1795507981613c2dc407cf03c`（CONFIRMED / PRODUCT_DEFECT）
   - 证据图节点/边：53/70、51/56、38/46
   - 这直接解决了 RUN-008 `ui-review.json` 中 `run_id`/`diagnosis_id` 为空导致 `07-run-detail`、`08-run-evidence-graph`、`12-diagnosis-detail` 被 skip 的问题；本轮三者均使用真实实体 ID 取得独立证据。
4. **验收脚本**：`scripts/ui_review_run009.py`，命令：
   ```bash
   .venv/bin/python scripts/ui_review_run009.py \
     --web-url http://127.0.0.1:5175 --api-url http://127.0.0.1:8020 \
     --out docs/assets/run009/wp1/ui
   ```

---

## 3. 14 路由 + 证据图子视图覆盖清单

`14` 条业务路由来自 `router.ts` 解析；`/:pathMatch(.*)*` 为唯一排除项。表中 `FAIL` 仅表示**等价无障碍规则发现问题**，功能状态均为 `PASS`。完整逐检查项数据在 `route-coverage.json` 的 `coverage` 数组（120 条）。

| 视图 | 路由 | 综合 | 功能 | 无障碍 | 1440 Light 截图（示例） | 390 Dark 截图（示例） |
| --- | --- | --- | --- | --- | --- | --- |
| 01-dashboard | `/` | PASS | PASS | PASS | `docs/assets/run009/wp1/ui/shots/01-dashboard-1440-light.png` | `docs/assets/run009/wp1/ui/shots/01-dashboard-390-dark.png` |
| 02-intents | `/intents` | FAIL | PASS | FAIL | `docs/assets/run009/wp1/ui/shots/02-intents-1440-light.png` | `docs/assets/run009/wp1/ui/shots/02-intents-390-dark.png` |
| 03-testcases | `/testcases` | FAIL | PASS | FAIL | `docs/assets/run009/wp1/ui/shots/03-testcases-1440-light.png` | `docs/assets/run009/wp1/ui/shots/03-testcases-390-dark.png` |
| 04-testcase-detail | `/testcases/:id` | PASS | PASS | PASS | `docs/assets/run009/wp1/ui/shots/04-testcase-detail-1440-light.png` | `docs/assets/run009/wp1/ui/shots/04-testcase-detail-390-dark.png` |
| 05-oracles | `/oracles` | FAIL | PASS | FAIL | `docs/assets/run009/wp1/ui/shots/05-oracles-1440-light.png` | `docs/assets/run009/wp1/ui/shots/05-oracles-390-dark.png` |
| 06-runs | `/runs` | FAIL | PASS | FAIL | `docs/assets/run009/wp1/ui/shots/06-runs-1440-light.png` | `docs/assets/run009/wp1/ui/shots/06-runs-390-dark.png` |
| 07-run-detail | `/runs/:id` | PASS | PASS | PASS | `docs/assets/run009/wp1/ui/shots/07-run-detail-1440-light.png` | `docs/assets/run009/wp1/ui/shots/07-run-detail-390-dark.png` |
| 08-explorer | `/explorer` | PASS | PASS | PASS | `docs/assets/run009/wp1/ui/shots/08-explorer-1440-light.png` | `docs/assets/run009/wp1/ui/shots/08-explorer-390-dark.png` |
| 09-models | `/models` | PASS | PASS | PASS | `docs/assets/run009/wp1/ui/shots/09-models-1440-light.png` | `docs/assets/run009/wp1/ui/shots/09-models-390-dark.png` |
| 10-diagnoses | `/diagnoses` | FAIL | PASS | FAIL | `docs/assets/run009/wp1/ui/shots/10-diagnoses-1440-light.png` | `docs/assets/run009/wp1/ui/shots/10-diagnoses-390-dark.png` |
| 11-diagnosis-detail | `/diagnoses/:id` | FAIL | PASS | FAIL | `docs/assets/run009/wp1/ui/shots/11-diagnosis-detail-1440-light.png` | `docs/assets/run009/wp1/ui/shots/11-diagnosis-detail-390-dark.png` |
| 12-repairs | `/repairs` | PASS | PASS | PASS | `docs/assets/run009/wp1/ui/shots/12-repairs-1440-light.png` | `docs/assets/run009/wp1/ui/shots/12-repairs-390-dark.png` |
| 13-benchmark | `/benchmark` | PASS | PASS | PASS | `docs/assets/run009/wp1/ui/shots/13-benchmark-1440-light.png` | `docs/assets/run009/wp1/ui/shots/13-benchmark-390-dark.png` |
| 14-system | `/system` | PASS | PASS | PASS | `docs/assets/run009/wp1/ui/shots/14-system-1440-light.png` | `docs/assets/run009/wp1/ui/shots/14-system-390-dark.png` |
| 07-run-detail-evidence-graph（子视图） | `/runs/:id` + 证据图 Tab | PASS | PASS | PASS | `docs/assets/run009/wp1/ui/shots/07-run-detail-evidence-graph-1440-light.png` | `docs/assets/run009/wp1/ui/shots/07-run-detail-evidence-graph-390-dark.png` |

每个视图实际有 **8 张主题/断点截图**（1440/1024/768/390 × light/dark）+ 1 张刷新截图 + 1 张交互截图，共 150 张；加 4 张状态截图共 **154 张**。已校验 15 个视图的 1440-light 截图 SHA-256 两两不同，证据图与 Run 详情截图哈希亦不同（分别为 `19d43ca03080…` 与 `33d6af20d38a…`），不存在以其他页面截图代替。

---

## 4. 检查项与结果

| 检查项 | 覆盖 | 结果 |
| --- | --- | --- |
| 直接 URL 打开 | 15 视图 × 8 组合 = 120 次 | 全部命中预期 pathname 与 `document.title`；0 重定向到通配页 |
| 4 断点 | 1440/1024/768/390 | 横向溢出 `overflow_px` 全部为 0；无页面异常 |
| Light / Dark | 每断点双主题 | 暗色主题均验证为真实渲染生效（非仅标记） |
| 页面刷新 | 15 视图（1440 light reload） | 15/15 路由身份与内容断言通过 |
| 前进后退 | 15 视图（`/` → 目标 → back → forward） | 15/15 回到预期 pathname |
| 真实交互 | 15 视图 | 15/15 完成（搜索框输入 / 刷新按钮 / Tab 切换 / 版本查看 / 生成证据图等），并记录触发的 API 请求数与 DOM 变化 |
| 键盘可访问性基础 | 15 视图 × 8 组合 | Tab 均到达可见且有可访问名的聚焦元素 |
| ARIA / 表格可读性 | 15 视图 × 8 组合 | 按钮可访问名、图片 alt、表头、tabindex、h1 等规则执行；发现 6 视图表单控件缺标签（第 6 节） |
| 四态 | Loading/Empty/Error/Success | 全部截取；Empty 空态提示可见=true，Error 错误提示可见=true |
| console 错误 | 每页采集 | 修复初始 ID 映射脚本缺陷后，最终 0 条非忽略 console 错误 |
| axe-core | 尝试本地包/CLI | **BLOCKED**（详见第 7 节） |

关键实证截图：
- 证据图子视图：`07-run-detail-evidence-graph-1440-light.png`，可见 38 节点 / 46 边真实图、证据节点与 Trusted Oracle 独立状态查询。
- 诊断详情：`11-diagnosis-detail-1440-light.png`，可见 `diagnosis_1795507981613c2dc407cf03c`、CONFIRMED / PRODUCT_DEFECT、1 条已确认假设、处置与引用证据。
- 错误态：`state-error-missing-run.png`，错误提示可见。

---

## 5. 产物文件

- `scripts/ui_review_run009.py`（新建，验收脚本）
- `docs/RUN-009-WP1-FRONTEND-REPORT.md`（本报告）
- `docs/assets/run009/wp1/ui/route-coverage.json`（主证据：逐路由状态 + 截图路径 + 120 项检查明细）
- `docs/assets/run009/wp1/ui/route-manifest.json`（14 路由一一对应清单）
- `docs/assets/run009/wp1/ui/shots/*.png`（154 张真实浏览器截图）
- `docs/assets/run009/wp1/env/`：
  - `vite.config.mjs`（隔离 Web 配置）
  - `prepare_data.py`（真实 API 造数脚本）
  - `api-data-evidence.json`（API 调用与返回证据）
  - `teamflow.log` / `api.log` / `web.log` / `ui-review-run009.log`

未执行 `git commit`、`git push`、`git reset`、`git clean`；未修改 `apps/web/src/**`、既有 `docs/assets`、其他 WP 文档。其他 agent 并行修改的文件不在本报告范围。

---

## 6. 发现的真实前端缺陷（未改源码，请 team-lead 转交前端 owner）

等价自动化无障碍规则在 **6 个视图**发现表单控件缺少可访问标签（仅有 placeholder 或无关联 `<label>`），与断点/主题无关，全部 8 组合均复现。按 WCAG 4.1.2 / 3.3.2 属于真实缺陷：

| 视图 | 缺陷元素（截取自证据） | 数量 |
| --- | --- | --- |
| 02-intents | `<input placeholder="搜索需求" ...>` 无 `aria-label` / 关联 `<label>` | 1 |
| 03-testcases | `<input placeholder="搜索用例标题 / ID" ...>`；`<select>`（全部状态）无标签 | 2 |
| 05-oracles | `<input placeholder="搜索 Oracle / 预期行为" ...>` 无标签 | 1 |
| 06-runs | `<select>`（全部结论）、`<select>`（全部执行状态）无标签 | 2 |
| 10-diagnoses | `<select>`（全部结论）无标签 | 1 |
| 11-diagnosis-detail | 处置区 `<input>`（审核人）无标签：源码为 `<label>审核人</label>` + `<input>`，`<label>` 未用 `for`/嵌套关联 | 1 |

建议修复方向（未在本次实施）：为每个 input/select 添加 `aria-label` 或将 `<label for>` 与 `id` 正确绑定。缺陷明细保存在 `route-coverage.json` 各路由条目的 `a11y_issues` 与 `breakpoints.*.a11y.no_label_fields` 字段。

---

## 7. Skip / BLOCKED 披露

| 项目 | 状态 | 原因 |
| --- | --- | --- |
| 15 个视图（14 路由 + 证据图子视图） | **0 skip** | 真实 Run / Diagnosis / Testcase ID 已通过 API 产生，全部执行 |
| `/:pathMatch(.*)*` | 设计性排除 | 通配重定向路由不是业务视图，`router.ts` 解析时显式排除，共排除 1 条 |
| axe-core / axe CLI 自动化审查 | **BLOCKED** | 隔离环境 `apps/web/node_modules`、全局 npm、`.venv` 均无 axe（已检索）；未联网注入 CDN 以保证证据可复现。已用脚本内置等价规则替代并在报告中披露，不声称通过 axe |
| LLM 顾问 | 主动关闭（非 BLOCKED） | 设置 `TG_LLM_PROVIDER=''` 走确定性规则引擎，避免真实密钥调用与成本；诊断 Outcome 仍为 CONFIRMED |
| 前端缺陷修复 | 未实施 | 按任务约束只写报告、不改共享源码 |

---

## 8. 复现要点

```bash
# 1) 端口检查（启动前）
for p in 8011 8020 5175; do lsof -nP -iTCP:$p -sTCP:LISTEN; done

# 2) 启动（环境变量按第 2 节，日志写入 docs/assets/run009/wp1/env/）
bash scripts/run_teamflow.sh            # TG_TEAMFLOW_PORT=8011
bash scripts/run_api.sh                 # TG_API_PORT=8020
apps/web/node_modules/.bin/vite --config docs/assets/run009/wp1/env/vite.config.mjs

# 3) 真实造数 + 验收
.venv/bin/python docs/assets/run009/wp1/env/prepare_data.py
.venv/bin/python scripts/ui_review_run009.py --web-url http://127.0.0.1:5175 --api-url http://127.0.0.1:8020 --out docs/assets/run009/wp1/ui
```

服务在证据产出后已停止（见交付回复），不残留后台占用。
