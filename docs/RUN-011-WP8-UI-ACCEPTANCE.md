# RUN-011 WP8：前端产品闭环体验（NOT RUN / PARTIAL）

> 状态：**PARTIAL**。本轮**没有**做完整的 UI 全链路点击验收（13 个业务步骤），
> 只做了"前端产物可访问 + 登录 E2E 通过"的间接验证。不宣称 UI 闭环已验收。

## 1. 本轮实际验证的内容

| 项 | 结果 | 证据 |
|---|---|---|
| Vue 正式构建产物由 API 单端口托管 | ✅ `GET /` 200 且含 `id="app"` | `full-loop/loop1.json` env 段 |
| 登录页真实浏览器登录（Chromium） | ✅ 2 个用例通过（`test_login_ui_run010.py`） | 全量测试 663 passed（含 e2e） |
| viewer 角色看不到管理员菜单 | ✅ 同上用例 | 同上 |
| 401 / 403 服务端语义 | ✅ 见 WP1 §2（API 层已实测） | loop*.json |
| SSE 实时更新 | ⚠️ 未在本轮手工验证 | 代码未改；`tests/integration/test_sse.py` 通过 |
| 13 路由 axe 审查 | ⚠️ 未重复执行（RUN-010 已 0 violations） | `docs/RUN-010-WP6-FRONTEND.md` |
| 完整业务交互（Intent→…→回滚）通过 UI 点击 | ❌ **未做** | 本轮无 UI 驱动脚本 |
| 深色模式 / 4 断点截图 | ❌ 未做 | 同上 |

## 2. 为什么没做

本轮把时间优先投给了指令的**第一目标**（真实独立部署闭环）与第二目标（Active Probe 真实链路），
这两项此前完全缺失。UI 全链路是 P2，且 RUN-010 已做过 13 路由 axe 与登录 E2E；
在没有新增前端改动的前提下，重复截图审查的边际价值低于把闭环做实。

## 3. 本轮对前端有影响的服务端契约变化

| 变化 | 前端需要跟进 |
|---|---|
| `report.steps[]` 新增 `target` / `value` | 无破坏（新增字段） |
| `report.assertion_outcomes[]` 新增 `description` / `expected` | 无破坏 |
| `report.browser.egress` 新增 `blocked_reasons` | 建议在 Run 详情展示"被阻断目标及原因" |
| `POST /probes/execute` 未知探针由 201+BLOCKED 改为 404 | 前端若调用需处理 404（当前前端未使用该入口） |
| `POST /testcases` 非法 `compile_source` 由 500 改为 422 | 前端表单应限制枚举值（当前只有三种） |

## 4. 结论

UI 侧本轮**没有新增证据**，也没有回归（构建产物可访问、登录 E2E 通过、TypeScript 构建未改）。
若要宣称"前端产品闭环验收完成"，仍需补：
1. 用真实浏览器按 qa-engineer / qa-lead / platform-admin 三种角色走完 13 个步骤；
2. 每步同时校验服务端状态（不能只看按钮反馈）；
3. 检查 401/403 的页面表现、SSE 实时刷新、Run 详情执行归属、Evidence Graph、诊断历史、
   Repair 状态、回滚结果。
