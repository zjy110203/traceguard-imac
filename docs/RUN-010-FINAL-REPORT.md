# RUN-010 最终交付报告：Core Integration & Intelligence Maturation

> 生成时间：2026-11-24 22:40 CST；**收尾复核更新：2026-11-25 00:30 CST（RUN-010-FINAL-CLOSEOUT）**。
> 状态枚举：IMPLEMENTED / VERIFIED / PARTIAL / BLOCKED / NEEDS_DECISION / NOT_IMPLEMENTED。
> 提交链：`8220479`（起点）→ `e43e0ca`(WP1) → `0e71267`(WP0/CI) → `651d1e6`(WP2) → `97d7901`(WP3)
> → `132c09c`/`3ac6725`(WP4) → `45898cc`/`94585fa`(WP5) → `48ae497`(WP6) → `e38ccac`/`1479982`(WP7)
> → `1500ad7`(WP8) → `f69c39b`(WP9) → `d4d59ba`(CLOSEOUT: CL-01/CL-02 修复 + WP4/WP6 报告) → `271ee42`(CL-03 PG 并发建表修复)。
> 仓库分支 `workbuddy/run008-quality-hardening`，PR #2 自动更新（open/HOLD，见 §7）。
>
> **本轮收尾对旧统计的更正（以最新 HEAD 真实证据为准）**：
> RUN-010 提交数 15 → **16**；改动文件 66 → **77**；测试总数 601 → **657**（收集）；
> E2E 用例 35 → **30**；PR #2 差异 691 文件/60 提交 → **693 文件/61 提交**。

## 1. 验收矩阵

| 工作包 | 状态 | 关键结果 |
|---|---|---|
| WP0 基线审计与 PR 管理 | **VERIFIED** | 基线与指令一致（8220479）；PR #2 保持 open；Nightly Gate success；Release Gate 暴露真实缺陷（Chromium 安装缺失）并修复 |
| WP1 认证产品化 | **VERIFIED** | 环境分级强制（production fail-closed）、登录/用户管理前端、DB 级限速、审计链跨进程安全、workspace 对象级授权；集成 21+9 用例 + 登录 E2E 2/2；**生产候选达成** |
| WP2 Egress 接入内核 | **VERIFIED / BLOCKED（宿主级）** | 代理与浏览器 1:1 生命周期绑定、fail-closed、Run 关联审计、策略单一事实源；未授权目标 0 请求（服务端收数）；宿主级隔离如实 BLOCKED（无 pf 权限） |
| WP3 幂等与状态机 | **VERIFIED / BLOCKED（B-17）** | 提交回执+冻结语义（幂等键重放不覆盖 verdict）、Repair 重复操作拒绝；B-17 保持 BLOCKED 未绕过 |
| WP4 诊断内核增强 | **VERIFIED（准确率无增益，如实记录）** | POST_HOC 时间对齐、Graph 裁决层进入主因裁决、403→AUTHORIZATION、Flaky 打标；13 个新用例全过；Benchmark v4：C/D 臂准确率同为 64.3%（9/14），graph_changed 2/14 → **参与裁决 ≠ 提升准确率**（见 `docs/RUN-010-WP4-INTELLIGENCE.md`） |
| WP5 Benchmark v4 真实实验 | **VERIFIED** | 14 场景×2 轮真实注入执行（此前仅定义）；C 臂主因准确率 **64.3%（selective 88.9%）** vs A 臂 7.1%；5 个缺口如实记录；各臂 `probes_per_sample=0`（探针未参与评测） |
| WP6 前端无障碍 | **VERIFIED** | 6 视图 **24 处**表单标签修复、axe-core 真实审查 **13 路由 0 violations / 0 无标签输入**、对比度修复（4.95:1 / 6.01:1）；未覆盖范围见 `docs/RUN-010-WP6-FRONTEND.md` §7 |
| WP7 供应链治理 | **VERIFIED / NEEDS_DECISION（LICENSE）** | lock 完整性修复（44 项）、pytest-cov/mypy 约束冲突修复、CI 锁定安装、vue-tsc 3.x 升级（audit 3→0）；LICENSE 建议待负责人决策 |
| WP8 完整集成验收 | **VERIFIED（附 CL-01 缺陷）** | 进程内完整闭环 3/3（Intent→诊断全流程真实执行、四角色 RBAC、egress 强制、审计链）；全量回归 **644 passed / 13 skipped**；该模块暴露 CL-01 进程退出挂死缺陷（已修） |
| WP9 最终交付 | **VERIFIED** | 本报告 + 全部文档 + 状态文件更新 |
| CLOSEOUT 交付治理 | **VERIFIED** | CL-01/CL-02 真实缺陷修复、WP4/WP6 报告补齐、PR #2 标题与描述更新、三处统计更正、CI 复验（§6） |

## 2. 本阶段真正新增的功能

1. **认证产品闭环**（RUN-009 只有后端）：登录页、用户管理页、改密码、401 全局跳转、
   菜单/路由权限、环境分级启动强制（production 关认证/不安全 Cookie/TTL 超限即启动失败）；
2. **受控出站代理正式集成**：BrowserManager 1:1 绑定、默认强制、fail-closed、
   审计带 run_id/worker_id、运行报告内可核验（`report.browser.egress`）；
3. **Task 提交回执幂等**：`tg_run_submission`（v13）+ 确定性幂等键 + 终态冻结语义；
4. **诊断 v2（strategy_version=v2）**：POST_HOC 时间对齐、TIMING 新假设生成、
   Graph 裁决层（去重/来源独立/时间窗/完整性降权/冲突弃权）、403/401→AUTHORIZATION、
   Flaky 弃权规则、NOT_EVALUATED 方向性诊断；
5. **Benchmark v4 真实执行框架**：故障注入器/场景准备器/评测端令牌隔离/8 实验臂
   （含真实消融 D 与时间对齐对照 G）；
6. **axe-core 自动化无障碍审查**（RUN-009 BLOCKED 项解除）；
7. **依赖一致性测试**（lock 与声明范围自动防冲突）。

## 3. 修复的真实缺陷

| 缺陷 | 发现方式 | 影响 |
|---|---|---|
| `record_audit` dict 参数 raw SQL 绑定 500 | WP1 测试 | SQLite/PG 下审计写入失败 |
| 登录限速进程内计数可被重启/多进程绕过 | WP1 审查 | 安全边界失效 → DB 原子计数修复 |
| `_effective_auth_enabled` 缓存测试泄漏 | 随机顺序回归 | 测试隔离缺陷 |
| `EgressProxy.serve()` 二次绑定覆盖监听 socket | WP2 集成（端口错位日志） | 代理端口与审计不一致 |
| `--host-resolver-rules=MAP * ~NOTFOUND` 触发 CONNECT 升级+直连回退风险 | WP2 实测 bisect | 网络安全路径破坏 |
| Release Gate 缺 Chromium 安装步骤 | WP0 触发 CI | 浏览器 E2E 全部 error（fail-closed 正确，安装步骤修复） |
| `submit_result` 同 token 重复终态覆盖 verdict/report | WP3 契约 | 结果可被静默改写 → 回执+冻结修复 |
| 事后探针错误反驳/排除 Run 时假设 | WP4 误差分析 | 正确假设被误杀 → POST_HOC 降级+TIMING 生成 |
| `ended_at` 在非终态提交被覆盖 | WP3 | 时间线数据失真 |
| business_state 断言 INCONCLUSIVE（rule 格式）/Oracle 未绑定 → NOT_EVALUATED | WP8 测试 | 闭环验证暴露，已固化在测试 |
| **CL-01**：`EgressProxyController` 用 `asyncio.to_thread(stop_evt.wait)` 占用线程池工作线程；代理先于 `stop()` 退出时该线程永久阻塞 → 解释器退出时 `concurrent.futures._python_exit` join 挂死 | 收尾复核（CI Nightly 45min 超时 + 本地 faulthandler 线程栈复现） | **P1**：Nightly backend-full 与 Release full-verification 在 pytest 打印结果后挂死至超时；修复后进程正常退出（14s） |
| **CL-02**：登录 E2E 依赖 `apps/web/dist`，但 Nightly/Release 不构建前端 | 收尾复核（CI 日志 2 failed：`/runs` 未跳转 `/login`、`#login-username` 超时） | 2 个真实 E2E 在 CI 必失败（本地因已有 dist 通过）；两处 workflow 补构建步骤 |
| **CL-03**：`_apply_migrations()` 首条 `CREATE TABLE IF NOT EXISTS` 无 SAVEPOINT/重试保护；PG 并发启动时在 `pg_type` 唯一索引冲突 | 收尾复验（`da2fbc6` Nightly postgres 任务：4 线程并发建表 3 个 `IntegrityError`） | **P1**：多 Worker 同时启动指向同一 PG 库时随机崩溃；改为 PG 事务级 advisory lock 串行化 + SAVEPOINT 容忍重复 DDL |

## 4. 测试与验证汇总

- 全量（单元+契约+集成+E2E）：收集 **657**，本地 **644 passed / 13 skipped / 0 failed，72.4s**
  （Python 3.13；skip 全部为本地无 PG 的 parity/platform 用例）；
  收尾修复前同一套件在 CI（Python 3.11）为 642 passed / 2 failed / 13 skipped（2 failed = CL-02）；
- E2E：**30 个真实 Chromium 用例**（egress 代理 8、egress 集成 5、目标策略 5、执行链 4、
  WP8 闭环 3、真实浏览器 3、登录 2）；收尾修复后子集回归 16 passed（egress×2 + WP8 闭环）；
- WP4 专项：`tests/unit/test_intelligence_run010.py` **13 passed**；
  `tests/integration/test_active_diagnosis_feedback.py` **11 passed**；
- axe：13 路由 **0 violations**；TypeScript + build 在 CI `PR Gate / frontend` 通过；
- Benchmark：14 场景 × 2 轮独立原始 JSONL（`docs/assets/run010/benchmark/`）；
- CI：见 §6（PR Gate / Nightly / Release 的最新真实状态与本轮 CL-01/CL-02 修复后复验）。

## 5. 性能与安全

- 性能：RUN-009 基线（1/2/4 Worker）不变；本轮新增开销为每浏览器实例 1 个代理线程
  （localhost 代理，延迟可忽略）；WP8 闭环 2 轮执行 ~14s；
- 安全审计：认证生产候选（fail-closed 链路+对象级授权+DB 限速+审计链并发安全）；
  用户态三层网络防护（route guard+代理+策略）实测有效；宿主级隔离 BLOCKED；
  npm audit 0；pip-audit 0（RUN-009）；SBOM 与 lock 一致。

## 6. 未完成事项与生产风险（如实）

| 项 | 状态 | 原因 | 建议 |
|---|---|---|---|
| B-17 Worker 四真实故障场景 | BLOCKED | AgentDR 拦截未解除，未绕过 | 安全负责人批准受信任执行路径后补做 |
| 宿主级网络隔离 | BLOCKED | 无 pf/防火墙权限（实测 Permission denied） | 容器/防火墙环境复测；勿外推用户态结论 |
| Benchmark LLM 臂 / 人工基线 | BLOCKED | 无计费授权 / 无真人 | 小样本授权后补；保持独立 |
| LICENSE | NEEDS_DECISION | 不擅自选法律条款 | 负责人在 4 选项中决策（WP7 报告） |
| Benchmark 5 个场景缺口 | PARTIAL | LOCATOR 优先级/时序延迟复测/纯超时证据落盘 | WP5 报告 §4 给出改进方向 |
| PG 运行时验证 | PARTIAL | 本地无 PG（测试 skip） | CI Service Container 已覆盖 |
| WP8 首版被 AgentDR 拦截 | 已处置 | subprocess+env 字典模式（B-20） | 进程内替代实现已验证；恢复指引见 resume.md |
| CL-01 代理线程池挂死 | **已修复（d4d59ba）** | 见 §3；CI 由 45/60 分钟超时变为 2m29s 正常结束 | 保留失败运行记录，不删除 |
| CL-02 登录 E2E 缺前端产物 | **已修复（d4d59ba）** | 两处 workflow 补 `npm ci && npm run build` + dist fail-closed 校验 | 本地不依赖隐式产物 |
| CL-03 PG 并发建表冲突 | **已修复（271ee42）** | PG advisory lock 串行化 + SAVEPOINT；CI postgres 任务复验通过 | 本机无 PG，仅 SQLite 侧回归 |

生产风险：认证默认关闭的开发模式若误用于生产——已由 `auth_policy.py` 启动期
fail-closed 兜底（production 环境未开认证直接启动失败）。

## 6.1 最新 CI 结果（最终代码 HEAD `271ee42`）

| 门禁 | 运行 | 结论 | 证据 |
|---|---|---|---|
| PR Gate（push） | [#45437828070](https://github.com/zjy110203/traceguard-imac/actions/runs/45437828070) | **success** | backend + frontend |
| PR Gate（pull_request） | [#45437848652](https://github.com/zjy110203/traceguard-imac/actions/runs/45437848652) | **success** | — |
| Nightly Gate | [#45437859966](https://github.com/zjy110203/traceguard-imac/actions/runs/45437859966) | **success（5/5 job，2m45s）** | backend-full **644 passed / 13 skipped，70.42s**；postgres 16.16；npm audit 0；LLM smoke BLOCKED（缺 Secret，warning 不阻断） |
| Release Gate | [#45438576538](https://github.com/zjy110203/traceguard-imac/actions/runs/45438576538) | **success（4/4 job）** | full-verification **644 passed / 13 skipped，79.04s**；`NIGHTLY_OK id=45437859966 head_sha=271ee423…` |

对照（修复前，留档不删除）：`f69c39b` 上 Nightly `45417381107` cancelled（45min 超时）、
Release `45417391553` cancelled（60min 超时）；`da2fbc6` 上 Nightly `45436579639` 因
CL-03（PG 并发建表唯一索引冲突）失败，已修复并在 `271ee42` 复验通过。
**所有最终 CI 结论对应 `271ee42`。**

## 7. GitHub 交付与 PR 状态

- PR #2（`workbuddy/run008-quality-hardening` → `main`）自动包含 RUN-007~010 全部提交，保持 **open/HOLD**；
- **PR #2 标题与描述已于 2026-11-25 00:16 CST 更新**为 `TraceGuard: RUN-007~RUN-010 Quality,
  Security & Intelligence Integration`，覆盖交付范围、WP 成果、CI 结果、Benchmark 指标、
  BLOCKED 项、LICENSE、PG/宿主级边界与暂不合并原因；
- PR #1 保持 open 未动（其祖先已含于 PR #2）；未 reset/clean/强推/改写历史；
- 合并决策：RUN-009 报告的 HOLD 条件中「认证未产品化」「用户态出口未接正式路径」
  已在 RUN-010 解决；B-17 与宿主级隔离仍未解除 → 维持 HOLD，待负责人决策；
- 完整差异审查见 `docs/RUN-010-PR-REVIEW.md`，收尾与独立验收见 `docs/RUN-010-CLOSEOUT-REPORT.md`。

> **CI 全绿 ≠ 达到生产标准。** 代码可合并条件与生产使用条件在两份报告中分列，不得混用。

## 8. 下一阶段建议

1. 争取 B-17 安全批准 → 补 Worker 真实故障矩阵；
2. 容器化部署 + 宿主级网络策略复测 WP2 场景；
3. 诊断 v3：延迟复测探针（时序场景）、LOCATOR 优先级、失败导航证据落盘；
4. LICENSE 决策 + vue-tsc 升级后的 Nightly 复验；
5. PostgreSQL 真实环境一轮全量回归。
