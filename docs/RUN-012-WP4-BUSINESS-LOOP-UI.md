# RUN-012 WP4：核心业务 UI 闭环

**日期**：2026-11-25 20:44 CST
**分支**：`workbuddy/run012-product-completion`

## 1. 目标

打通并验证 **Intent → TestCase → Oracle → 审核 → 执行 → 诊断 → Repair → 晋升 → 回滚** 全链，
每一步 UI 点击后立即通过服务端 API 回读真实数据，不以按钮提示代替事实。

## 2. 审查发现的缺口与修复

### 2.1 Oracle 绑定无 UI 入口（阻断审核门禁）

- **事实**：`POST /testcases/{id}/versions/{v}/oracle-bindings` 早已存在，但前端只展示 `assertion.oracle_id`，**没有任何绑定控件**；
  而审核门禁 `_oracle_gate()` 要求全部必需断言绑定「已审批 + 同 Workspace」的 Oracle，否则 412 `ORACLE_BINDING_INCOMPLETE`。
  结论：UI 上无法完成一次合法审核，只能绕过或放弃。
- **修复**：`TestCaseDetailView.vue` 新增绑定控件（按 `assertion_id` 过滤可选 Oracle，含版本与审批状态）、保存绑定按钮、门禁说明文案；
  已 APPROVED 版本不可改绑（服务端 `APPROVED_VERSION_CONTENT_IMMUTABLE` 语义），UI 同步禁用。

### 2.2 版本列表接口结构与前端类型不匹配（断言表格整体不渲染）

- **事实**：`GET /testcases/{id}/versions` 返回 `{testcase_id, version, review_status, immutable, doc:{...}}`，
  断言与步骤在 `doc` 内；前端直接把每行当 `TestCaseVersion` 用，导致 `current.assertions` 为 `undefined`，
  **断言表格、步骤轨迹、绑定控件全部不渲染**（真实浏览器验证：`select 数量=0`）。
- **修复**：前端在 `load()` 中把 `doc` 展开到顶层（`{...doc, ...row}`），并让 `current` 在版本未命中时回退到服务端 `latest`。

### 2.3 被测目标地址被硬编码（后端配置形同虚设）

- **事实**：
  - 前端 `TestCasesView.vue` 创建用例时写死 `run_config.base_url = 'http://127.0.0.1:8001'`；
  - 后端 `pipeline.py` 对 `application == "teamflow"` 硬编码 8001，忽略 `TG_TEAMFLOW_BASE_URL`；
  - `playwright_observer.py` 默认 base_url 同样硬编码。
  结论：自定义端口的被测目标永远打不到，`/system/config` 暴露的 `teamflow_base_url` 与真实执行不一致。
- **修复**：后端两处默认值统一改为读 `Settings.teamflow_base_url`；前端改为从 `/system/config` 读取，
  **不写死端口**（读取失败则省略 `base_url`，由服务端解析）。

### 2.4 缺失的真实操作入口

| 入口 | 状态 | 修复 |
|---|---|---|
| 稳定性回放（compiler/healed 用例审核前置） | 后端有 `POST /testcases/{id}/versions/{v}/stability-replay`，前端无入口 | `TestCaseDetailView.vue` 审核卡新增「启动稳定性回放」按钮 + 门禁说明 |
| LLM 修复候选 | 后端有 `POST /repairs/llm-suggest`，前端无入口 | `RepairsView.vue` 提案弹窗新增「LLM 建议」按钮（填入首个候选，仍需人工确认） |

### 2.5 UX 缺陷：按钮不可达（验收中发现）

初版把「LLM 建议」放在页头，但它依赖弹窗内已选目标用例，而弹窗遮挡页头 → 按钮永远点不到。
已移入提案弹窗页脚（与「创建提案」并列），语义与可达性一致。

## 3. 真实浏览器闭环验收

新增 `scripts/business_loop_ui_acceptance_run012.py`：
**真实 Chromium + 真实 API（认证 + CSRF）+ 真实 TeamFlow（独立进程与独立库）+ 真实 Worker（消费队列）+ 真实浏览器执行**。

**结果：30 条记录 = 29 PASS / 0 FAIL / 1 NOTE**（`docs/assets/run012/wp4/ui/loop-acceptance.json`，
`total=30 / passed=29 / failed=0`）。NOTE 为「诊断 ID」记录项（仅记录 `diagnosis_id`，非断言），
不能表述为「29 条记录全部通过」。

| # | 检查项 | 结果 |
|---|---|---|
| ① | UI 创建需求 → 服务端真实落库 | PASS |
| ② | UI 新建用例 → 服务端 PENDING | PASS |
| ③ | UI 新建已审批 Oracle → 服务端落库（approved=True） | PASS |
| ④ | 用例详情页提供 Oracle 绑定控件 | PASS |
| ⑤ | UI 保存绑定 → 服务端 `oracle_bindings` 落库 | PASS |
| ⑥ | UI 审核通过 → 服务端 APPROVED（Oracle 门禁未绕过） | PASS |
| ⑦ | UI 执行 → 产生真实 Run | PASS |
| ⑧ | Run 由真实 Worker 执行到终态（COMPLETED） | PASS |
| ⑨ | 执行使用真实浏览器（`observer_mode=playwright`），非降级 | PASS |
| ⑩ | Run 产出真实步骤轨迹与证据（steps=5, evidence=19） | PASS |
| ⑪ | 向真实被测目标注入故障 TF-UI-002（sticky） | PASS |
| ⑫ | 故障注入后真实 Run 失败（verdict=FAILED） | PASS |
| ⑬ | 失败 Run 的诊断入口可用 | PASS |
| ⑭ | UI 诊断 → 服务端生成 Diagnosis | PASS |
| ⑮ | 修复中心提供 LLM 建议入口 | PASS |
| ⑯ | 未配置 LLM 凭据时如实提示「修复规划器已禁用」，不伪造成功 | PASS |
| ⑰ | UI 新建修复提案 → 服务端落库 | PASS |
| ⑱ | 提案初始状态 VALIDATED（规则校验通过） | PASS |
| ⑲ | 清除 sticky 故障后沙箱真实执行 → `sandbox_status=PASSED`（真实 Run） | PASS |
| ⑳ | UI 提交审批 → 服务端 PENDING_APPROVAL | PASS |
| ㉑ | UI 审批 → 服务端 APPROVED（qa-lead，`repair.approve`） | PASS |
| ㉒ | UI 晋升 → 服务端 PROMOTED，`promoted_version=2`（platform-admin，`repair.promote`） | PASS |
| ㉓ | UI 回滚 → 服务端 ROLLED_BACK 且 `rollback_record` 留痕（platform-admin，`repair.rollback`） | PASS |

### 验收中验证到的权限与门禁事实

- `oracle.create` / `oracle.bind` / `testcase.review` / `repair.approve` 归 qa-lead 与 platform-admin；
  `repair.promote` / `repair.rollback` 仅 platform-admin —— 脚本按真实权限矩阵分配角色，未越权尝试。
- 沙箱验证会**真实重新执行**候选版本（独立 `testcase_id` + `sandbox_only` 标记），失败即 `sandbox_status=FAILED`，
  审批链被服务端门禁挡住；本轮通过一次**语义等价**的定位器修复使沙箱真实 PASSED，再走完审批→晋升→回滚。
- 诊断按钮对 PASSED 用例禁用（设计如此）；失败 Run 才能诊断 —— 已用真实故障注入覆盖。

## 4. 回归

- 全量：`738 passed, 16 skipped`（PG 专项需 `TG_TEST_POSTGRES_URL`，WP1 已在真实 PG16 验收）；
- `ruff check` / `ruff format --check` 通过；`npm run build`（vue-tsc + vite）通过；
- 一次 `test_migration_v8.py::test_v8_migration_is_idempotent_when_reapplied_concurrently` 偶发失败：
  该用例依赖 `threading.Barrier(timeout=10)` 的并发建表时序，单独跑通过、后续两次全量均通过，判定为**基线时序敏感**（非本轮改动引入）。

## 5. 残留与边界

- 稳定性回放入口已接入，但 compiler/healed 用例的「回放→审核」完整成功路径需要多轮真实浏览器执行（耗时较长），本轮只验证入口存在与可点击；
- LLM 建议在本环境无凭据，按设计如实提示「已禁用」，真实模型输出路径未在本轮验证（WP5 覆盖）；
- 探索编译（compiler 来源）用例的稳定性门禁成功路径仍需完整回放，未纳入本轮。
