# RUN-019 基线核查与功能差距分析

> 仓库：`https://github.com/zjy110203/traceguard-imac`（iMac 项目，非 Windows 本机仓库）
> 开发分支：`workbuddy/run019-product-completion`，起点 `73edc34679961eeec865bf882ef98fbdff723c60`
> 上游：`workbuddy/run018-diagnostic-closure` @ `73edc346`（与远端一致，无更新提交）
> `main`：`ac2876ecef779b0bed900bdf3b5361ce1d2fa3d6`；run018 ahead 151 / behind 0
> 核查时间：2026-11-27。核查方式：HEAD/远端/工作区核对 + 指定文档逐份阅读 + 源码逐文件核对。
> 历史文档的完成状态**未直接继承**；下列每条均按当前源码与归档证据重新判定。

## 0. 状态口径

| 状态 | 含义 |
|---|---|
| VERIFIED | 本轮或历史有真实执行证据（真实 API/DB/浏览器），且当前源码仍成立 |
| IMPLEMENTED | 代码存在但缺当前轮真实运行证据 |
| PARTIAL | 部分链路可用，存在明确缺口 |
| BLOCKED | 外部条件/凭据/授权阻塞 |
| NEEDS_DECISION | 需负责人决策，不擅自冻结 |
| NOT_IMPLEMENTED | 当前源码无此能力 |

补充维度（每项必须区分）：**后端 API / 服务层 / 数据模型 / 前端入口 / 前后端联调 / 真实数据验证 / 仅单测或 Mock / 仍缺失**。

## 1. WP0 基线核查结果

| 检查项 | 结果 |
|---|---|
| 仓库 remote | `origin = https://github.com/zjy110203/traceguard-imac.git` ✅ |
| 起始分支/HEAD | `workbuddy/run018-diagnostic-closure` @ `73edc346` ✅ |
| 本地工作区 | 无未提交修改，未丢弃任何成果 ✅ |
| 远端是否有更新 | `git fetch --all --prune` 后 run018 仍为 `73edc346` ✅ |
| RUN-019 分支 | 本地/远端均不存在；已从 `73edc346` 新建 ✅ |
| 既有 PR | 未合并 #1/#2；`gh` CLI/凭据不可用，未创建 PR ✅ |
| RUN-018 文档与代码关系 | 最新 HEAD `73edc346`，修复提交 `05a3639`，PR Gate #107 / Nightly #60 Success（指令给定；本地无凭据复核以 Actions 页面为准） |
| Nightly #60 LLM Smoke | Job Success 但 `real_llm_executed=false`、`DEEPSEEK_API_KEY` 缺失 → **BLOCKED**，不得记真实 LLM 通过 |

### 必读资料完成情况

`RUN-018-FINAL-REPORT.md`、`RUN-018-WORKLOG.md`、`RUN-018-WP5-DIAGNOSIS-FEEDBACK.md`、
`RUN-018-WP7-HOLDOUT-BENCHMARK.md`、`FEATURE-STATUS.md`、`README.md`、`.workbuddy-run/` 全部台账：已读。
发现文档口径偏差：`FEATURE-STATUS.md` 顶部仍停在 RUN-017，且历史段落保留
"14 视图 / 未实现 RBAC / PG 未验证"的旧口径（RUN-006~RUN-009 历史快照不得删除，
但必须在文件顶部增加 RUN-019 权威状态并明确历史段落适用范围）；
`RUN-018-WP7-HOLDOUT-BENCHMARK.md` §5 仍写"场景变体待执行"，与 §6 的 VERIFIED 冲突，需追加更正说明而非改写历史。

---

## 2. 核心资产管理（WP1）

### 2.1 Workspace 管理

| 能力 | 状态 | 后端 | 前端 | 联调/真实数据 | 差距与证据 |
|---|---|---|---|---|---|
| Workspace 列表 | **IMPLEMENTED** | `GET /api/v1/workspaces`（`routers/catalog.py:60`）+ `list_workspaces()` | `workspace.ts` 启动时调用 | 真实 API；多 Workspace 切换浏览器 25/25（RUN-012） | 列表不返回 `created_by`，无法核验创建者 |
| Workspace 创建 | **PARTIAL** | `POST /workspaces`（`routers/catalog.py:70`） | 无前端入口 | 仅 service 层 fixture 使用，HTTP 路径无专项测试 | `created_by` 取请求体（默认 `human:admin`），未用 `trusted_actor()`，无审计 |
| Workspace 详情 | **PARTIAL** | service 有 `get_workspace()`（`persistence/catalog.py:138`），**无 HTTP 路由** | 无页面 | 无 | 需补 `GET /workspaces/{id}` |
| Workspace 基础信息编辑 | **NOT_IMPLEMENTED** | 无 PATCH/PUT | 无 | 无 | 需补，且 `default` 必须保护 |
| 成员及授权范围展示 | **PARTIAL** | `Principal.workspaces` 来自 `tg_user.workspaces` JSON（`auth.py:246`）；无成员表 | `UsersView` 仅创建时选择范围 | 无成员管理闭环 | 无 `tg_workspace_member`；可先基于用户 scope 反查展示，需 NEEDS_DECISION 是否引入成员模型 |
| 成员授权管理入口 | **PARTIAL** | `POST /auth/users/{id}/workspaces`（`routers/auth.py:193`）存在 | 无前端入口 | 集成测试 23 项中验证重新登录生效 | 前端缺入口；scope 修改未撤销会话（但 `get_session` 每次查库，下一请求生效） |
| Workspace 切换后刷新 | **VERIFIED** | 服务端 `scope_filter` 隔离 | `workspace.ts` generation + `<router-view :key>` 强制重挂载；并发序号防回填 | 真实浏览器 25/25（RUN-012） | 部分视图绕过 `wsPath()`：`TestCaseDetailView:139`、`RunsView:30`、`DiagnosesView:26`、`RepairsView:42` |
| 资产隔离 | **VERIFIED** | `Principal.workspace_allowed()` + `scope_filter`；跨空间 Task/Run/Probe/Repair/Evidence 拒绝 | 前端上下文 | 9+8+3 项隔离专项 | 保持 |
| 默认 Workspace 保护 | **PARTIAL** | 启动 `_ensure_default_workspace()`；空值归一为 `default`；无删除/改名 API | — | — | 无显式守卫分支；补编辑 API 时必须加保护 |
| 生命周期/删除 | **NEEDS_DECISION** | 无删除机制 | 无 | — | 按指令不擅自引入删除；需先核实引用与归档语义 |

**结论**：Workspace 是本轮第一缺口——**没有管理页面**，创建者身份可伪造且不审计，详情/编辑缺失。

### 2.2 用户与角色管理

| 能力 | 状态 | 后端 | 前端 | 联调/真实数据 | 差距与证据 |
|---|---|---|---|---|---|
| 用户创建/列表/启停 | **VERIFIED** | `routers/auth.py:121-190` | `UsersView.vue:52-112` | 集成 23 项 + 浏览器 | 保留 |
| 用户停用撤销会话 | **VERIFIED** | `revoke_all_sessions()`（`auth.py:178`） | 有入口 | 已测 | 保留 |
| Workspace scope 更新 API | **IMPLEMENTED** | `routers/auth.py:193-215` | 无入口 | 重新登录验证新 scope | 文档字符串称撤销会话，代码未调用；因 `get_session` 每次查库，实际下一请求即生效（不盲目加撤销） |
| 用户角色修改 API | **NOT_IMPLEMENTED** | 无 UPDATE roles 路径 | 无 | 无 | 需补 |
| 用户角色修改前端入口 | **NOT_IMPLEMENTED** | — | 无 | 无 | 需补 |
| Workspace scope 修改前端入口 | **NOT_IMPLEMENTED** | API 已有 | 无 | 无 | 需补 |
| 权限矩阵展示 | **VERIFIED** | `GET /auth/permissions` | `UsersView` 只读展示 | 有 | 保留，沿用现有矩阵不扩权 |
| 管理员变更保护 | **NOT_IMPLEMENTED** | `set_user_active()` 无 last-admin 守卫 | 无 | 无 | 需防：自提权、非管理员改角色、伪造审批主体、停用/降级最后一个管理员、并发绕过 |
| 用户操作审计 | **PARTIAL** | login/logout/password/user_created/user_active_changed/user_workspaces_changed 有 `record_audit()` | — | 审计链已测 | 角色变更新增操作需同步审计 |
| 请求体伪造主体 | **PARTIAL** | 用户管理走 `_require_platform_admin()`（真实 principal） | 无 | 已测 | 新增角色 API 必须同样使用服务端可信主体 |

### 2.3 TestIntent 管理

| 能力 | 状态 | 后端 | 前端 | 差距与证据 |
|---|---|---|---|---|
| 创建 / 列表 / 详情 | **PARTIAL** | `POST/GET /intents`、`GET /intents/{id}` | 列表 + `?id=` 过滤，无详情路由 | 详情信息展示不完整 |
| 提交评审 / 审核 / 探索 / 废弃 | **PARTIAL** | `POST /intents/{id}/status` | 有按钮 | 仅白名单枚举校验（`persistence/catalog.py:197-211`），无状态机，可 `DRAFT→APPROVED` 直接跳转 |
| 编辑已有需求 | **NOT_IMPLEMENTED** | 无 PUT/PATCH | 无 | 指令第一缺口 |
| 原始需求来源保留 | **PARTIAL** | `source` 字段存在 | 展示不完整 | 编辑时不得覆盖原始来源 |
| 与已有 TestCase 关系保护 | **NOT_IMPLEMENTED** | 无关联校验 | 无 | 编辑/废弃时需检查已编译用例引用 |
| 权限映射 | **PARTIAL** | 所有 `/intents` 写操作映射 `testcase.create`（`auth.py:89-90`） | 按钮无角色判定 | 创建权限天然获得审批权；需区分提交评审与审批权限 |
| 审计 | **NOT_IMPLEMENTED** | Intent 状态变更不写 `tg_audit_log` | — | 需补 |
| 完整生命周期 | **PARTIAL** | 创建→提交→审核→探索→编译→废弃链路 API 存在但无状态约束 | 前端有探索/编译入口 | 需加显式状态转换约束 |

### 2.4 TestCase 管理

| 能力 | 状态 | 后端 | 前端 | 差距与证据 |
|---|---|---|---|---|
| JSON 创建 | **IMPLEMENTED** | `POST /testcases`（`routers/catalog.py:204`），服务端 DRAFT/PENDING/v1 | 裸 JSON `<textarea>`（`TestCasesView.vue:28-38`） | 使用体验不完整 |
| 可视化步骤编辑器 | **NOT_IMPLEMENTED** | — | 无；详情页步骤只读表格 | 需新增/删除/修改/排序步骤、目标元素、输入值、描述、断言、Oracle 绑定、JSON 高级模式；支持 navigate/click/fill/select/press/assert/wait/api |
| 版本递增与不可变 | **IMPLEMENTED（流程级）** | `create_testcase()` 由 `latest_version+1`（`persistence/catalog.py:263`）；`TestCaseVersionRow.immutable=True` | 版本列表 + v1/v2 切换 | `immutable` 是标记非 DB 只读约束；`bind_oracles()`/`review_testcase()` 会改 doc，审核通过后锁绑定 |
| 创建已有用例新版本 | **PARTIAL** | 只能通过 `POST /testcases` 用同 testcase_id 新版本 | 无入口 | 需专用 `POST /testcases/{id}/versions` |
| 版本历史 / 内容 | **PARTIAL** | `GET /testcases/{id}/versions`、`/versions/{v}` | 详情页有列表/切换 | 保留 |
| 版本差异比较 | **NOT_IMPLEMENTED** | 无 diff API | 无（CodeDiff 仅用于 Repair） | 需补 |
| 审核状态 / 稳定性回放 | **VERIFIED** | review + stability gate（`catalog.py:393-827`）、`require_stability=Literal[True]` | 详情页有回放/审核面板 | 保留；注意 `compile_source` 客户端可控可绕过 EXEMPT_HUMAN_AUTHORED，属真实缺口需修 |
| Oracle 绑定 / 版本固定 | **VERIFIED** | `bind_oracles()` + Run 固定 `testcase_version_id` | 详情页可绑定 | 保留 |
| 审核/绑定 actor | **PARTIAL** | HTTP 层 `trusted_actor()` 有服务端校验 | 硬编码 `role:qa-lead`（`TestCasesView:85`、`Detail:192,240`） | 前端应改为使用当前可信主体或不传由服务端解析 |

### 2.5 Oracle 管理

| 能力 | 状态 | 后端 | 前端 | 差距与证据 |
|---|---|---|---|---|
| 创建 / 版本递增 / 详情 | **IMPLEMENTED** | `POST/GET /oracles`（支持 `?version=`） | 列表 + 创建（可勾选同步审批） | 前端从未调用详情 API，行不可点击 |
| Oracle 详情页面 | **NOT_IMPLEMENTED** | API 已有 | 无路由/页面 | 需补 |
| 历史版本 / 新建版本 | **PARTIAL** | `create_oracle()` 自动递增；无独立版本列表 API | 无 | 创建路径缺跨 workspace ID 复用校验（`catalog.py:1251-1288`） |
| 版本差异比较 | **NOT_IMPLEMENTED** | 无 | 无 | 需补 |
| 审批 / 审批记录 | **PARTIAL** | 仅创建时携带 `approved_by`；无独立审批 API；`approval_history` 已写 | 勾选"同步审批"，硬编码 `role:qa-lead` | 需补独立审批 API 与记录展示 |
| 关联 TestCase / 引用关系 | **NOT_IMPLEMENTED** | 绑定信息在 TestCase 版本 doc，无反向查询 | 无 | 需补反向关联查询与展示 |
| Agent/LLM 禁止审批 | **VERIFIED** | 契约 `approved_by_agent: Literal[False]`、`require_human_actor()`、`_resolve_oracle()` 三处 | — | 必须保留 |
| 跨 Workspace 绑定拒绝 | **VERIFIED** | `_resolve_oracle():587-591` | — | 创建路径需补齐 |

### 2.6 WP1 联合验收

**NOT_RUN**。指令要求的 18 步真实闭环（Workspace→用户→角色→授权→需求→编辑→评审→审核→探索→用例→新版本→Oracle→Oracle 新版本→绑定→审核→回放→Run）当前无端到端专项脚本；
现有 `business_loop_ui_acceptance_run012/run017` 覆盖 Intent→执行→诊断→修复→回归→回滚，但不含资产管理新增环节。

---

## 3. 智能诊断（WP2）

| 能力 | 状态 | 事实与证据 |
|---|---|---|
| Run→Evidence→Diagnosis→Hypotheses→Probe→Probe Evidence→Graph→Revision 链路 | **VERIFIED** | 全链路源码存在，RUN-018 WP5 真实 TeamFlow 2/2，revision 链可追溯 |
| Probe Contract / Evidence Trust / Time Alignment / Graph Adjudicator / Revision / Budget / Workspace 隔离 | **VERIFIED** | 实现位置见 `RUN-018-WP5` 与源码；本轮不得建立旁路 |
| API / NETWORK 诊断 | **VERIFIED** | RUN-018 Holdout Recall 1.0/1.0，`confirming=True` 存在（`diagnosis_service.py:230/278`） |
| PERSISTENCE 诊断证据覆盖 | **PARTIAL** | `count==0` 仅 SUPPORT、`confirming=False`（`feedback.py:329-343`）；单次读取不得确权，需建立"接受请求→业务标识→权威状态→延迟复测→排除 5xx/网络"完整独立证据链 |
| TIMING 诊断 | **PARTIAL** | `rule:probe-timing` 存在但 POST_HOC/UNKNOWN 封顶（`feedback.py:354-372`）；需区分永久失败/暂时延迟/延迟恢复/超窗，且即时+延迟+最终读取绑定同一实体、用真实时间戳判定 |
| LOCATOR 诊断 | **PARTIAL** | DOM 证据均 `confirming=False`（`feedback.py:454-534`）；需补齐 DOM/ARIA/可见性/role/name/test-id/匹配结果/用户操作成功/业务落库的独立观测，区分测试资产问题与真实产品缺陷 |
| CONFIRMED 门槛 | **VERIFIED（需扩展规则而非降低门槛）** | `engine.py:184-196` 要求 TRUSTED + `directly_confirming` 且无 TRUSTED 反驳；后三类目前结构上无法进入 CONFIRMED |
| Ground Truth 隔离 | **VERIFIED** | `GroundTruthStore` token、`target_policy` 路径阻断、`_strip_ground_truth()`；Holdout 的 GT 仅在评测端读取 |
| RUN-018 原始 Holdout | **VERIFIED（不可变）** | 24 变体 × 2 轮，manifest + 6 文件 SHA-256 + 策略版本；弃权 66.7%、Selective Accuracy 1.0、Primary Cause Accuracy 0.5、三类 Recall 0；不得修改样本或降阈值 |
| 新独立 Holdout | **NOT_IMPLEMENTED** | 需新增 6+6+6+4+4+4=30 变体 ×2 轮 ≥60 样本，独立 dev 样本先行、规则冻结后执行，保存清单/哈希/每样本结果/两轮/混淆矩阵/P/R/F1/Selective Accuracy/Coverage/弃权率/证据覆盖/耗时/成本 |

**关键事实**：三类 Recall=0 是 `confirming=True` 在这些类别所有路径缺席的**结构性结果**，不是采样方差；提升必须新增可信证据路径并走完整反馈链，不得修改旧样本或降低确认门槛。

---

## 4. 前端 16 视图（WP3）

| 视图 | 状态 | 主要缺口 |
|---|---|---|
| 1 质量总览 | IMPLEMENTED | 报告缺失仅计数无跳转 |
| 2 测试需求 | PARTIAL | 无详情页；无编辑；操作无角色判定 |
| 3 测试资产 | PARTIAL | 裸 JSON 创建；审核人硬编码；执行按钮不按 `can('run.execute')` |
| 4 用例详情 | PARTIAL | 步骤只读；无新建版本；无版本 diff；actor 硬编码；`/runs` 查询脱离 workspace |
| 5 Oracle 契约 | PARTIAL | 无详情/历史/审批记录/反向关联；actor 硬编码 |
| 6 执行中心 | IMPLEMENTED | 无新建执行按钮（入口分散）；"包含 Benchmark"脱离 workspace |
| 7 Run 详情 | IMPLEMENTED | SSE 未传 workspace；无权限判定 |
| 8 Agent Studio | IMPLEMENTED | LLM/兜底标签由 engine 前缀推断，非逐动作权威字段 |
| 9 模型运行中心 | IMPLEMENTED | 设计上只读 |
| 10 诊断中心 | IMPLEMENTED | 无发起诊断入口 |
| 11 诊断详情 | IMPLEMENTED | 部分 GET 未带 workspace；审核人可编辑默认值 |
| 12 修复中心 | IMPLEMENTED | 审批/晋升/回滚未按 `can()` 隐藏；actor 默认值 |
| 13 Benchmark | IMPLEMENTED | 无 workspace 参数；无详情下钻 |
| 14 系统与运行环境 | PARTIAL | **无 Workspace 管理**；seed 是真实写操作（保留） |
| 15 运行监控与告警 | PARTIAL | metrics 未传 workspace；按钮无 `can()`；**未进入任何 UI/a11y 脚本清单** |
| 16 全局导航与搜索 | PARTIAL | 静态入口缺 5 视图；实体搜索仅 runs/testcases/intents/diagnoses，缺 Oracle/Repair/探索/探针/用户 |
| 用户与角色（第 17 路由条目，adminOnly） | PARTIAL | 无角色/范围修改入口；仅路由守卫 + 一个菜单隐藏，其余按钮不做 `can()` |

**总体**：未发现前端本地 Mock 或假成功数据；所有写操作指向真实 `/api/v1/*`。前端无 JS/TS 单测（`package.json` 无 test script）。
**真实浏览器覆盖**：`ui_a11y_run018.py` 65/65 的 65 个 cell = 15 业务视图 + 登录 × 2 主题 × 2 断点 + 1 弹窗，**不等于 16 业务视图全覆盖**（monitoring 缺失）。

---

## 5. 完整业务闭环与质量门禁（WP4/WP5）

| 项 | 状态 | 说明 |
|---|---|---|
| 真实执行内核 / 独立 Worker / PG16 / TeamFlow / Evidence 落盘 | VERIFIED（RUN-018） | 542 integration SQLite、558 PG16、UI 36/36 |
| 完整闭环（需求→…→回滚） | PARTIAL | 现有 36/36 覆盖执行/诊断/修复，不含本轮新增资产管理与版本更新场景 |
| 多 Workspace / 多角色 / 权限撤销 / 版本更新 / 历史引用固定 | PARTIAL | 各自有专项测试，但缺少统一的新闭环脚本 |
| 真实 LLM Smoke | BLOCKED | 缺 Secret，禁止寻找凭据；Nightly #60 不得记真实 LLM 通过 |
| Reliability Gate 120 分钟窗口 | NOT_RUN | 无授权独立环境，保留 NOT_RUN |
| PR Gate / Nightly Gate | VERIFIED（给定） | #107 / #60 Success @ `73edc346`；本轮最终 HEAD 需重新核对 |
| 无 gh CLI | BLOCKED | CI 日志/工件无法程序化导出；用 Actions 页面核对 |

---

## 6. 文档口径需修正项（WP6）

1. 修复提交 SHA `05a3639`、最新 HEAD `73edc34679961eeec865bf882ef98fbdff723c60`；
2. PR Gate #107 / Nightly #60 对最新 HEAD 真实通过；Nightly #60 LLM Smoke 实际 BLOCKED；
3. PostgreSQL 16 全量 558 passed 保留正确口径；B-28 CLOSED；
4. B-24/B-25/B-26/B-27 与 Holdout 质量问题按真实情况更新（NOT_RUN/BLOCKED/OPEN）；
5. README 旧 RUN-008 / 14 视图 / 未实现 RBAC / PG 未验证信息纠正，历史段落不删除；
6. `RUN-018-WP7` §5 "待执行"与 §6 VERIFIED 冲突，追加 Errata 说明。

---

## 7. 本轮执行优先级

1. **WP1**：Workspace 管理页 + created_by/审计；用户角色 API + 前端 + last-admin 保护；TestIntent 编辑/状态机/权限/审计；TestCase 可视化步骤编辑器 + 新版本 + diff；Oracle 详情/历史/审批/反向关联 + 跨 workspace 创建校验。
2. **WP2**：在不降低门槛、不改旧 Holdout 的前提下补 PERSISTENCE/TIMING/LOCATOR 证据链，冻结新规则后跑独立 Holdout。
3. **WP3**：补齐缺失前端入口、workspace 参数、角色 UI、全局搜索、Monitoring a11y 覆盖。
4. **WP4/WP5**：全链路真实回归 + 质量门禁；BLOCKED/NOT_RUN 如实记录。
5. **WP6**：文档与台账统一。

> 本文件是 RUN-019 的**起点分析**，后续每个 WP 完成后在对应专题文档中记录实际实现程度；
> 若某项未达验收标准，保持 PARTIAL/BLOCKED/NOT_RUN，不在本文件中提前改为 VERIFIED。
