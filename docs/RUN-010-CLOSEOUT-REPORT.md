# RUN-010 最终交付收尾与独立验收报告（RUN-010-FINAL-CLOSEOUT）

> 执行时间：2026-11-24 23:36 → 2026-11-25 00:40 CST。
> 分支：`workbuddy/run008-quality-hardening`；仓库：`zjy110203/traceguard-imac`。
> 基线 HEAD：`f69c39b05c7c6ce975902c3fc636a3b6a961f152`（与 `origin` 一致，工作区干净）。
> 本轮性质：交付质量治理（**不是**新一轮功能开发）。未启动 RUN-011，未扩大功能范围。

## 0. 一句话结论

本轮**完成**。RUN-010 的代码、测试证据、交付文档三者已对齐；发现并修复 2 个真实 P1 缺陷
（CL-01 CI 挂死、CL-02 登录 E2E 环境依赖），补齐 WP4/WP6 独立报告，更正 4 处过期统计，
更新 PR #2 标题与描述；三级 CI 门禁在**同一 SHA `d4d59ba`** 上全部真实通过。
仍存在 4 类 BLOCKED 未解除，**维持 HOLD、不合并**。

## 1. 基线核查（WP0）

| 检查项 | 指令预期 | 实测 | 结论 |
|---|---|---|---|
| 当前分支 | `workbuddy/run008-quality-hardening` | 同 | ✅ |
| 本地 HEAD | `f69c39b…` | `f69c39b05c7c6ce975902c3fc636a3b6a961f152` | ✅ |
| 与 origin 关系 | 一致 | 一致（`git status`：up to date） | ✅ |
| 工作区 | 干净 | `nothing to commit, working tree clean` | ✅ |
| PR #2 head | 最新 | `f69c39b` | ✅ |
| origin/main | —— | `ac2876e`，为 `merge-base`（ahead 61 / behind 0） | ✅ 无领先 main 的新提交 |
| 其他成员未提交修改 | 无 | 未发现（工作区干净、无未跟踪的业务文件） | ✅ |
| PR #1 | 保持原状 | open，未动 | ✅ |

未执行任何 `reset --hard` / `clean` / 强推 / 历史改写；未合并 PR。

## 2. 统计更正（本轮最重要的治理产出）

旧文档中的数字与最新 HEAD 不一致，已全部按真实证据重算：

| 项 | 旧值 | **实测值（本轮）** | 依据 |
|---|---|---|---|
| RUN-010 提交数（`8220479..`） | 15 | **16**（收尾前）/ **17**（含 `d4d59ba`） | `git rev-list --count` |
| RUN-010 改动文件 | 66 | **77**（收尾前）/ **79**（含 `d4d59ba`） | `git diff --name-only \| wc -l` |
| RUN-010 行数 | +7,736 / −336 | **+7,932 / −336**（收尾前）/ **+8,314 / −336** | `git diff --shortstat` |
| 全量测试 | 601 passed / 13 skipped | **657 收集 → 644 passed / 13 skipped / 0 failed** | 本地 72.36s + CI 77.39s |
| E2E 用例 | 35 | **30** | `pytest tests/e2e --collect-only`（8+5+5+4+3+3+2） |
| PR #2 差异 | 691 文件 / 60 提交 | **693 文件 / 61 提交**（`f69c39b`）/ **695 / 62**（`d4d59ba`） | `git diff --shortstat origin/main...HEAD` |
| 诊断测试 | "13 个新用例"（未给结果） | **13 passed**（`test_intelligence_run010.py`） | 真实运行 |
| WP6 标签修复 | "6 视图"（未给数量） | **6 视图 / 24 处** | `git show 48ae497 -- apps/web/src/views` |

> 601 → 644 的差异来源：旧数字是 WP8 之前快照，未计入 WP8 闭环与后续用例；
> 本轮以 `pytest --collect-only` 的 657 为准（644 passed + 13 skipped）。

## 3. 本轮实际完成的内容（A）

### 3.1 补齐缺失的实施报告（WP1）

| 文档 | 内容 | 状态 |
|---|---|---|
| `docs/RUN-010-WP4-INTELLIGENCE.md` | 诊断 v2 架构、Hypothesis 生成/更新、Evidence Graph 数据流、GraphAdjudicator 裁决逻辑、时间窗与来源独立性、POST_HOC、403/AUTHORIZATION、Flaky、13 项测试与真实结果、Benchmark v4 图实验结果、已知缺陷 | **新增** |
| `docs/RUN-010-WP6-FRONTEND.md` | 6 视图 24 处标签修复、axe-core 审查、13 路由覆盖表、0 violations 原始证据路径、登录 E2E、TS/Vite 构建、未覆盖范围 | **新增** |

两份报告均以提交内原始证据为准；**未补造任何测试结果**。WP4 报告明确写出
"Graph 改变 2/14 样本输出，但未带来准确率提升"，并指出 `conflict_detection_count=0/14`、
`probes_per_sample=0` 两个限制，避免把"参与裁决"写成"提升准确率"。

### 3.2 发现并修复的真实缺陷（P1）

**CL-01：出站代理控制器导致 pytest 进程在退出阶段挂死**

- 触发：CI Nightly `backend-full` 在 14:46:21 打印 `2 failed, 642 passed, 13 skipped in 99.85s`
  后无任何输出，直到 15:29:18 被 45 分钟 job 超时取消（`##[error]The operation was canceled`）；
  Release `full-verification` 同一现象，60 分钟超时。
- 定位：本地用 faulthandler 在 `pytest_unconfigure` 阶段抓全线程栈，得到调用链
  `threading._shutdown → concurrent.futures.thread._python_exit → Thread.join`，
  同时 `egress-proxy` 线程仍在 `asyncio` 事件循环中；根因是
  `EgressProxyController._run` 用 `asyncio.to_thread(self._stop_evt.wait)` 占据默认线程池的
  工作线程，当代理先因 `serve` 结束而退出、`stop()` 未被调用时该线程永久阻塞，
  解释器退出时 join 该非 daemon 线程 → 挂死。
- 修复（`d4d59ba`）：改用轮询协程等待停止信号（不占线程），并在 `finally` 中置位停止事件、
  调用 `loop.shutdown_default_executor()`。
- 验证：修复前 `pytest tests/e2e/test_full_loop_run010.py` 打印 `3 passed` 后 **>180s 不退出**；
  修复后 **14s 退出 rc=0**；全量 72.36s 正常结束；CI Nightly backend-full 由超时变为 **2m29s success**。
- 未删除任何历史证据：失败运行 `45417381107` / `45417391553` 保留在 GitHub。

**CL-02：登录 E2E 在 CI 缺前端产物**

- 现象：CI 有 2 个失败：`/runs` 未跳转 `/login`（`assert '/login' in 'http://127.0.0.1:33741/runs'`）、
  `#login-username` 定位超时。
- 根因：登录 E2E 依赖单端口 SPA（`apps/web/dist`），`create_app()` 仅在 `DIST_DIR.exists()`
  时挂载前端路由；仓库不含 dist，Nightly/Release 的 backend 任务没有构建步骤 → `/runs` 无 SPA；
  本地因 `apps/web/dist` 已存在而通过（环境依赖未显式化）。
- 修复：`nightly-gate.yml`（backend-full）与 `release-gate.yml`（full-verification）
  增加 `actions/setup-node@v4` + `npm ci` + `npm run build` + `test -f dist/index.html`（fail-closed）。
- 未放宽任何断言、未跳过任何用例。

**CL-03：PostgreSQL 并发建表在系统表唯一索引上撞车（收尾复验中新发现）**

- 现象：在 `da2fbc6`（仅文档提交）上重跑 Nightly，`postgres` 任务
  `PostgreSQL platform tests` 失败：4 线程并发 `create_schema()` 中 3 个抛
  `IntegrityError: duplicate key value violates unique constraint "pg_type_typname_nsp_index"`
  （`Key (typname, typnamespace)=(tg_schema_migration, 2200) already exists`）。
  同一任务在 10 分钟前的 `d4d59ba` 上是通过的 → **间歇性**，且与本次文档提交无关。
- 根因：PostgreSQL 的 `CREATE TABLE IF NOT EXISTS` **不是原子操作**：并发会话可能同时通过
  "不存在"检查，随后在 `pg_type` 唯一索引上冲突。`create_schema()` 对 `create_all` 有重试，
  但 `_apply_migrations()` 里第一条 `CREATE TABLE IF NOT EXISTS tg_schema_migration`
  没有被 SAVEPOINT + 重复错误容忍包住，异常直接抛给调用方。
- 影响：多 Worker 同时启动指向同一 PG 库时，启动期可能随机崩溃（生产风险，虽非数据损坏）。
- 修复：`_apply_migrations()` 在 PostgreSQL 下先取事务级 advisory lock
  （`pg_advisory_xact_lock`，commit 自动释放）串行化建表/迁移；首个建表语句包 SAVEPOINT
  并容忍重复 DDL 错误（SQLite/兜底路径）。
- 局限性（如实）：本机无 PG，**只做了 SQLite 侧回归（644 passed / 13 skipped）**，
  PG 并发路径由 CI Service Container 复验；advisory lock 的跨版本行为未在 PG 15/17 上验证。

### 3.3 PR #2 更新（WP2）

- 标题：`RUN-007/008: 质量加固、长稳验证与 Benchmark v3 集成（RUN-009 审查）`
  → **`TraceGuard: RUN-007~RUN-010 Quality, Security & Intelligence Integration`**
- 描述重写为 10 节：项目交付范围、RUN-010 八工作包、HEAD 与 CI、测试证据、Benchmark v4 指标、
  BLOCKED 项、LICENSE、PG 与宿主级验证边界、暂不合并原因与两级验收条件、文档索引。
- 措辞约束已遵守：BLOCKED 未写成 PASS；契约测试明确标注"≠ 真实进程故障测试"；
  应用层代理隔离明确"不可外推为宿主级"；Benchmark 明确"未做显著性检验"；
  未宣称生产环境验证完成。
- 操作方式：GitHub REST API `PATCH /pulls/2`（HTTP 200），**未合并、未改历史**。

### 3.4 代码缺陷与 CI 修复范围声明

按指令 §8「发现必须修复的 P0/P1 缺陷先报告再处置」，本轮处置的两个缺陷属
**CI 可靠性与测试环境**范畴，改动面最小：

- `packages/execution/…/browser/egress.py`：仅替换"等待停止信号"的实现 + 关闭默认执行器，
  代理语义、fail-closed 行为、审计字段不变；
- 两处 workflow：仅新增构建步骤与产物校验，未删除或放宽任何检查。

未修改业务契约、未改 AgentDR 策略、未新增 Agent/Repair 能力、未重构。

## 4. 每个 WP 的交付物与测试证据（B）

| WP | 交付物 | 测试证据（真实） |
|---|---|---|
| WP0 | `docs/RUN-010-BASELINE.md` | 基线与指令一致；Release Gate 缺陷（缺 Chromium）修复 |
| WP1 | `docs/RUN-010-WP1-AUTH-PRODUCT.md`；迁移 v12；`auth_policy.py`；登录/用户视图 | 集成 21 + RBAC 9；登录 E2E 2（本地 + CI 修复后） |
| WP2 | `docs/RUN-010-WP2-EGRESS-INTEGRATION.md`；`egress.py` | egress 代理 8 + egress 集成 5；未授权目标 0 请求；宿主级 BLOCKED |
| WP3 | `docs/RUN-010-WP3-WORKER-REPAIR.md`；迁移 v13 | 幂等 8 用例；B-17 仍 BLOCKED |
| WP4 | `docs/RUN-010-WP4-INTELLIGENCE.md`（新） | **13 passed**；feedback 集成 **11 passed**；Benchmark 见 §6 |
| WP5 | `docs/RUN-010-WP5-BENCHMARK.md`；`docs/assets/run010/benchmark/*` | 14 场景 × 2 轮真实注入；C 臂 9/14 |
| WP6 | `docs/RUN-010-WP6-FRONTEND.md`（新）；`docs/assets/run010/wp6/axe-review.json` | **13 路由 0 violations / 14 输入 0 无标签** |
| WP7 | `docs/RUN-010-WP7-SUPPLY-CHAIN-DEPLOYMENT.md` | lock 44 项一致；npm audit 0；LICENSE NEEDS_DECISION |
| WP8 | `docs/RUN-010-WP8-INTEGRATION-ACCEPTANCE.md` | 闭环 3/3（本次子集回归 16 passed 含此项） |
| WP9 | `docs/RUN-010-FINAL-REPORT.md`、`docs/RUN-010-PR-REVIEW.md` | 本轮已更新统计 |
| CLOSEOUT | 本报告 | 见 §5 |

## 5. 最新 CI 结果（C）

**SHA `d4d59ba874550dc8b961eb13269d7f56811d8a75`（含 CL-01/CL-02 修复）**

| 门禁 | Workflow Run ID | 链接 | 结论 | 关键行 |
|---|---|---|---|---|
| PR Gate（push） | 45433891889 | https://github.com/zjy110203/traceguard-imac/actions/runs/45433891889 | **success** | backend + frontend |
| PR Gate（pull_request） | 45433916576 | https://github.com/zjy110203/traceguard-imac/actions/runs/45433916576 | **success** | — |
| Nightly Gate | 45433925669 | https://github.com/zjy110203/traceguard-imac/actions/runs/45433925669 | **success（5/5 job）** | `644 passed, 13 skipped in 77.39s`；backend-full 2m29s |
| Release Gate | 45434549738 | https://github.com/zjy110203/traceguard-imac/actions/runs/45434549738 | **success（4/4 job）** | `644 passed, 13 skipped in 67.71s`；`NIGHTLY_OK id=45433925669 head_sha=d4d59ba…` |

附注（如实）：

- Nightly `security` 的 `npm audit`：`{"info":0,"low":0,"moderate":0,"high":0,"critical":0}`；
- Nightly `llm-smoke`：**BLOCKED（缺 Secret）**，gate 以 warning 放行，该证据**不得**用于 Release 能力放行；
- Release `nightly-attestation`：`head_sha` 严格等于 `d4d59ba` 才通过（不接受旧 commit 冒充）；
- 修复前对照（`f69c39b`）：Nightly `45417381107` cancelled（45min 超时）、Release `45417391553`
  cancelled（60min 超时，另因无成功 Nightly 导致 attestation failure）。

**失败日志摘要（`f69c39b`，已修复，留档）**

```
FAILED tests/e2e/test_login_ui_run010.py::test_login_page_and_full_login_flow
  AssertionError: http://127.0.0.1:33741/runs   (未跳转 /login)
FAILED tests/e2e/test_login_ui_run010.py::test_viewer_role_sees_no_admin_menu
  playwright TimeoutError: Page.fill: waiting for locator("#login-username")
2 failed, 642 passed, 13 skipped, 3 warnings in 99.85s
→ 之后 pytest 进程不再退出，14:46:21 起静默，15:29:18 被 job 超时取消
```

## 6. Benchmark v4 关键指标（真实、不宣称显著）

| 指标（两轮一致） | 值 |
|---|---|
| C 臂主因准确率 / 选择性准确率 | **64.3%（9/14）** / **88.9%（8/9）** |
| A 臂（原始报告）主因准确率 | 7.1%（1/14） |
| D 臂（规则 + 图消融） | **64.3%（9/14）**（与 C 相同） |
| `graph_changed_prediction_count` | 2/14 |
| `conflict_detection_count` | 0/14 |
| `probes_per_sample`（所有臂） | 0 |
| `primary_cause_correct[F_minus_G]` | 0/14 |

样本量 14，未做统计显著性检验；E/F/G 臂因无真实探针而不构成有效消融。

## 7. 仍未完成的事项（D）

| 项 | 状态 | 说明 / 阻塞原因 |
|---|---|---|
| B-17 Worker 四真实故障场景 | **BLOCKED** | 安全拦截未解除，未绕过；契约测试 4/4 ≠ 真实进程故障 |
| 宿主级网络隔离 | **BLOCKED** | 无 pf/防火墙权限；仅验证应用层代理 |
| Benchmark LLM 臂 | **BLOCKED** | 无计费授权 |
| 人工基线 | **BLOCKED** | 无真人评测 |
| LICENSE | **NEEDS_DECISION** | 4 个候选已给出建议，待负责人选定 |
| Benchmark v4 五个场景缺口 | **PARTIAL** | LOCATOR 优先级、时序延迟复测、纯超时证据落盘等 |
| PostgreSQL 本地验证边界 | **PARTIAL** | 本地无 PG → 13 个 skip；仅 CI Service Container（PG 16）覆盖 |
| 无障碍键盘/读屏/动效/缩放 | **NOT_IMPLEMENTED** | 见 WP6 报告 §7 |
| 生产环境部署与运维验证 | **NOT_IMPLEMENTED** | 本轮无生产环境 |

## 8. PR 合并条件（E）

**代码可以合并的条件**（工程质量，当前已满足）：

1. 同一 commit 三级门禁全绿 —— ✅ `d4d59ba`；
2. 无已知 P0/P1 未修复缺陷 —— ✅（CL-01/CL-02 已修）；
3. 无凭据入库、无断言弱化、无历史改写、未绕过安全拦截 —— ✅；
4. 文档与代码一致、统计可复核 —— ✅（本报告 §2）。

**项目达到生产使用标准的条件**（当前**不**满足）：

1. B-17 真实故障矩阵通过；
2. 宿主级（容器/防火墙）网络策略验证通过；
3. PostgreSQL 生产参数下的全量回归（连接池、迁移回滚、备份恢复）；
4. Benchmark LLM 臂 + 人工基线补齐，形成可比较的能力结论；
5. LICENSE 决策完成，依赖与 SBOM 治理常态化；
6. 灰度/回滚/监控/告警可用性验证。

> **不得因为所有 CI 通过就认为项目达到生产标准。** 上述 1–6 均未完成。

## 9. 变更文件清单与 SHA-256（本轮收尾）

见 §11（提交后计算，逐文件）。分类：

| 类别 | 文件 |
|---|---|
| 代码修复 | `packages/execution/traceguard_execution/browser/egress.py` |
| CI 配置 | `.github/workflows/nightly-gate.yml`、`.github/workflows/release-gate.yml` |
| 新增文档 | `docs/RUN-010-WP4-INTELLIGENCE.md`、`docs/RUN-010-WP6-FRONTEND.md`、`docs/RUN-010-CLOSEOUT-REPORT.md` |
| 更新文档 | `docs/RUN-010-FINAL-REPORT.md`、`docs/RUN-010-PR-REVIEW.md`、`docs/RUN-010-WP8-INTEGRATION-ACCEPTANCE.md`（统计更正）、`docs/TraceGuard-持续开发任务台账.md`（统计更正） |
| 状态台账 | `.workbuddy-run/progress.md`、`phase-status.md`、`test-results.md`、`resume.md`、`blockers.md`、`current-task.md` |

## 10. Git 提交记录与远程同步

| 提交 | 内容 | 推送 |
|---|---|---|
| `d4d59ba` | CL-01/CL-02 修复 + WP4/WP6 报告 | ✅ `f69c39b..d4d59ba` |
| 文档收尾提交 | 本报告 + FINAL-REPORT/PR-REVIEW/台账更新 | ✅（见 §11 头部） |

- 分支：`workbuddy/run008-quality-hardening`；远程 `origin/workbuddy/run008-quality-hardening` 同步；
- PR #2 保持 **open**，head 自动跟随；PR #1 未动；
- 无 `reset --hard` / `clean` / 强推 / 历史改写 / PR 合并。

## 11. 交付校验表

| 交付要求 | 状态 |
|---|---|
| 1. RUN-010-CLOSEOUT-REPORT.md | ✅ 本文件 |
| 2. 更新后的 RUN-010-FINAL-REPORT.md | ✅ 统计更正 + CL-01/CL-02 + §6.1 CI |
| 3. 更新后的 RUN-010-PR-REVIEW.md | ✅ 差异重算 + 收尾修复审查 |
| 4. WP4 / WP6 独立报告 | ✅ 两份均为本轮新增（此前确实缺失） |
| 5. 更新后的项目状态台账 | ✅ progress / phase-status / test-results / resume / blockers / current-task |
| 6. 最新 CI 验证证据 | ✅ 4 个 workflow run + 日志摘要（§5） |
| 7. 修改文件清单及 SHA-256 | ✅ §12 |
| 8. Git 提交记录与远程同步 | ✅ §10 |

## 12. 变更文件清单与 SHA-256

计算方式：`shasum -a 256 <path>`（工作区最终内容，提交前计算）。本报告自身的哈希不列在表内
（自引用不可能自洽），其完整性由 git blob 哈希保证（见 `git show <sha>:docs/RUN-010-CLOSEOUT-REPORT.md | git hash-object --stdin`）。

| 文件 | 类别 | SHA-256 |
|---|---|---|
| `packages/execution/traceguard_execution/browser/egress.py` | 代码修复（CL-01） | `d8c89ca249887110a1a5c714ca7a4b0715fb49f038d59a0342426db79b1b2ee1` |
| `.github/workflows/nightly-gate.yml` | CI 修复（CL-02） | `60d3539bb739d10d4e9c90f4a56af65502f5d52ef47fb323e1b2a28d1074ca04` |
| `.github/workflows/release-gate.yml` | CI 修复（CL-02） | `61a5928a626cabdaac35134ae7164168bb81b66acd09f07a8c302083bdb4e08e` |
| `docs/RUN-010-WP4-INTELLIGENCE.md` | 新增（WP4） | `73fc0fc1f04becac5a22bb935ac25bfad72d335dc3c43c8718b7a81c1bcd2938` |
| `docs/RUN-010-WP6-FRONTEND.md` | 新增（WP6） | `9997190aaec70aa3e00970325a66e2a852ae92ef9c2108042d3cc4896914c566` |
| `docs/RUN-010-FINAL-REPORT.md` | 更新 | `f1e2ae61d4e744acfaf6543e7657d8c56bb3135f1a94b77e9eba9b2faba08ca7` |
| `docs/RUN-010-PR-REVIEW.md` | 更新 | `0d5e2615aec8ce349a0ab03bb13672becf7300e2312e48638c50c0f009ddc4c6` |
| `docs/RUN-010-WP8-INTEGRATION-ACCEPTANCE.md` | 更新（统计更正） | `1571473b443e7601ddaad3b7584640f9984418639329e8cb68c10ef7639daccd` |
| `docs/TraceGuard-持续开发任务台账.md` | 更新（统计更正） | `26b803863a1f011fda57aee640c928c54c5bfe117d085feafbe85b91c0c041a1` |
| `.workbuddy-run/progress.md` | 台账 | `2ebfd07adbdb1a57999b58ad06aed7f9c91986fa4a6b2085cd38b9b3d7005c19` |
| `.workbuddy-run/phase-status.md` | 台账 | `d610300fd57525237458e307eaafdf33fe807721223126fbdd742361965a0c1e` |
| `.workbuddy-run/test-results.md` | 台账 | `c1950771261971f5e6cd8066d5642c9149de5685535b0c3dfb036f7d62a63906` |
| `.workbuddy-run/resume.md` | 台账 | `f465969b3efc6731ed513afbef123512a7ae8bd3bdf77713d8195a07090dae34` |
| `.workbuddy-run/blockers.md` | 台账 | `73f461a8aafaddc5188a7d7cee3eab6509201b852b4a8f0bf0a96a3c4f1ba96f` |
| `.workbuddy-run/current-task.md` | 台账 | `cb863ca359aa6469553279ef810cb314f520af97379270bfe202791dda566d3a` |
