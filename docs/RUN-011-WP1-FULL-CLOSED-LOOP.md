# RUN-011 WP1：真实独立部署下的完整业务闭环

> 执行时间：2026-11-25 01:30–01:40 CST（两轮）。
> 分支：`workbuddy/run011-full-loop`；HEAD 见 `RUN-011-FINAL-REPORT.md` §提交。
> 执行体：`scripts/full_loop_run011.py`（**纯 HTTP 客户端**，不 import 应用代码、不使用 TestClient、
> 不直接改数据库状态）。
> 原始证据：
> - `docs/assets/run011/full-loop/loop1.json`（62/62）、`loop1-db.json`（8/8）
> - `docs/assets/run011/full-loop/loop2.json`（62/62）、`loop2-db.json`（8/8）

## 0. 一句话结论

在 **独立 API 进程 + 3 个独立 Worker 进程 + PostgreSQL 16.16 + 真实 Chromium + 强制出站代理 +
认证开启** 的拓扑下，两轮完整业务闭环（Intent → 探索 → 编译 → Oracle → 稳定性回放 → 审核 →
正式执行 → Evidence → 故障诊断 → Active Probe → Repair → 人工审批 → CAS 晋升 → 回归 → 回滚 →
回滚后复核）**各 62/62 项通过**，且数据库级复核 **各 8/8 项通过**。
两轮使用**不同的 API/Worker/数据库/TeamFlow 实例与端口**，Worker 实际参与者不同
（loop1: w1/w2/w3；loop2: v1/v2/v3）。

## 1. 真实运行拓扑

| 组件 | loop1 | loop2 | 独立性证据 |
|---|---|---|---|
| TeamFlow（业务目标服务） | 独立进程 `:8111`，SQLite `.traceguard/run011/loop1/teamflow.db` | 独立进程 `:8112`，`loop2/teamflow.db` | `/api/state/projects` 各自独立 |
| TraceGuard API | 独立 uvicorn 进程 `:8110`，`TG_WORKER_ID=run011-api` | `:8120`，`run011-api2` | `/system/health.worker.inline_running=false` |
| Worker | 3 个独立 OS 进程：`run011-w1/w2/w3` | 3 个：`run011-v1/v2/v3` | Run 的 `lease_owner` 仅出现在 Worker ID 列表中 |
| PostgreSQL | `traceguard_run011_l1`，16.16 | `traceguard_run011_l2`，16.16 | 独立数据库；迁移各自 12 条 |
| Chromium | 159.0.8154.6（bundled） | 同 | `/system/browser?probe=true` → `available=true` |
| Egress | `TG_EGRESS_ENFORCED=true`，Run 报告 `report.browser.egress.enforced=true` | 同 | 每 Run 独立代理端口（如 `:56698` / `:57379`） |
| 前端 | `apps/web/dist` 单端口托管 | 同 | `GET /` 200 且含 `id="app"` |
| 认证 | `TG_AUTH_ENABLED=true`，CSRF 强制 | 同 | 未认证 `GET /runs` → 401 |

启动顺序（可复现）：
1. `PG16: initdb/pg_ctl`（沿用 RUN-007 的 `/tmp/traceguard-f2` 实例，端口 55432）；
2. 新建数据库 → `scripts/auth_bootstrap.py` 建 4 个账户（viewer / qa-engineer / qa-lead / platform-admin）；
3. 启动 TeamFlow → API（`TG_INLINE_WORKER=0`）→ 3 个 Worker（同时启动，**并发建表/迁移**）；
4. 执行 `full_loop_run011.py`。

本轮 API + 3 Worker **同时**对空库执行 `create_schema()`：迁移记录 12 条、版本唯一，
未出现 RUN-010 CL-03 的 `pg_type` 唯一索引冲突（该修复在真实并发启动下有效）。

## 2. 认证 / RBAC / 身份来源（服务端 Principal）

| 检查 | 结果 | 证据 |
|---|---|---|
| 未认证访问业务 API | 401 `UNAUTHENTICATED` | loop*.json `auth` 段 |
| 四角色真实登录（DB 会话 + Cookie + CSRF） | 4/4 | `/auth/me` 返回服务端身份 |
| viewer 创建 Intent | 403 `PERMISSION_DENIED` | 同上 |
| qa-engineer 审核用例 | 403 `testcase.review` | B1 前的负向检查 |
| qa-engineer 审批 Repair | 403 `repair.approve` | F5 |
| qa-engineer 执行种子/系统配置 | 403 `system.configure` | seed 阶段 |
| qa-lead 执行高风险晋升 | 403 `repair.promote` | F7 |
| platform-admin 读取用户 / 执行晋升 / 回滚 | 200 | F8/G4 |
| **请求体伪造 reviewer** | 无效：审批记录 `reviewer=user:qa-lead`，请求体声明 `agent:auto-heal` 被忽略 | B6 |
| Agent 代替人类审批 | 不可能：`agent:*` 不在角色集合，且 `repair.approve` 只授予 qa-lead/admin | F5/F6 |
| 审批与晋升是两个审计事件 | `auth.approve`（repair.approve）与 `repair.promote` 分别入库；`tg_promotion_receipt` 只记晋升/回滚 | F8 + DB |

## 3. 完整业务链路（逐阶段）

### 阶段 A：需求与探索（真实 LLM）

| 项 | loop1 | loop2 |
|---|---|---|
| Intent | `intent_1795541786128b6c6796f7f8e` | `intent_179554194553664ca39d0baf5` |
| 探索引擎 | `llm:deepseek`（真实 API 调用，HTTP 200 × 5） | `llm:deepseek` |
| 动作数 / 停止原因 | 5 / 模型自行 `stop`（"页面已显示创建成功提示"） | 5 / 同 |
| 轨迹内容 | navigate `/` → `/projects` → click `nav-new` → fill `project-name` → click `create-submit` | 同（另有 read_page） |
| 编译 | 规则编译 → `tcase_…@1`，`review_status=PENDING` | 同 |

> 说明：本轮修复了探索器的一个真实缺陷（见 §6-①）：此前 LLM 只能看到页面文本，
> 会凭猜测 `fill` 一个尚未渲染的输入框（`waiting for get_by_test_id("project-name")` 超时），
> 轨迹无法编译为可执行用例。修复后开放**结构化可定位元素清单**（data-testid + visible），
> 两轮均一次探索成功并自行停止。

### 阶段 B：测试生成与治理

| 检查 | 结果 |
|---|---|
| 未回放就审核 | 412 `STABILITY_REPLAY_REQUIRED`（门禁拒绝） |
| Oracle 审批 + 绑定到**具体断言** | 通过，`oracle_version=1` 钉在断言上（`oracle_bindings`） |
| 稳定性回放 3 次真实 Run | `STABLE_PASS`，`promotable=true`，`worker_consumed=true` |
| 回放 Run 的执行者 | loop1: w2/w1/w3；loop2: v1/v2/v3（`claimed_by` 全部为声明 Worker） |
| 审批 | `APPROVED`，`reviewer=user:qa-lead`（请求体声明 agent 无效） |
| 审批后 Oracle 绑定保留 | 是（B7） |

### 阶段 C：正式执行

| 项 | loop1 | loop2 |
|---|---|---|
| Run | `run_1795544003583a58bb2f8b846` | `run_17955441357738bf0fdcb45ac` |
| 执行者 / fencing | `run011-w2` / token 0→1 | `run011-v1` / token 0→1 |
| 终态 / 判定 | COMPLETED / **PASSED** | COMPLETED / **PASSED** |
| `report.browser.egress` | `enforced=true`，total=14 allowed=14 blocked=0，代理 `http://127.0.0.1:64734` | `enforced=true`，14/14/0，代理 `:65321` |
| Evidence | 23 条（browser/network/api） | 23 条 |
| Evidence 完整性重算 | 抽样 5 条 `integrity_check=PASS`（服务端存储摘要重算） | 同 |
| Event | 唯一 event_id，序号连续（按 Run 事件排序后 `1..N`） | 同 |
| 业务状态 | 用例 `cleanup_policy=RESET_DATA`，执行后 `count>=1`（真实落库） | 同 |

### 阶段 D：真实故障与诊断

- 注入方式：在 TeamFlow 受控测试环境**真实激活** `TF-DB-001`（UI 成功但持久化被回滚），
  通过 `/api/faults/TF-DB-001/activate`，不修改 Verdict、不在测试代码里伪造 FAILED。
- 结果（两轮一致）：`test_verdict=FAILED`，独立 Worker 执行，业务状态 `count` 未增加；
  诊断 `outcome=CONFIRMED`、`disposition=PRODUCT_DEFECT`、4 个假设（2 条规则 + 2 条 LLM 候选）；
  证据图 54 节点 / 69 边（loop1）与 54 / 73（loop2），其中 **28 个 Evidence 节点全部指向本 Run 的真实 evidence_id**。
- 诊断证据引用可追溯（`hypotheses[].evidence_ids` 是 Run 证据的子集）。

### 阶段 E：主动探针（本轮行为）

`TF-DB-001` 的诊断在首诊即 `CONFIRMED`，探针循环**如实返回 `STOP_CONFIRMED`**、0 次执行：
这是设计语义（已确认根因不再消耗预算），不是"探针没实现"。
真实探针执行与回灌在专门实验（WP2）中验证，见 `docs/RUN-011-WP2-ACTIVE-PROBE.md`。

### 阶段 F：Repair → 审批 → CAS 晋升

| 项 | loop1 | loop2 |
|---|---|---|
| 基线用例/版本 | `tcase_bench_loc001@1`（已审批） | 同 |
| 基线失败 | `NOT_EVALUATED`（定位器失效，步骤中断） | 同 |
| 修复提案 | 步骤 4 `test_id=create-submit` → `role=button:创建` | 同 |
| Diff / 断言保护 / Oracle 完整性 | `VALIDATED` / `PASS` / `PASS` | 同 |
| 沙箱执行 | `PASSED`，`executed_by=run011-w1`，fencing≥1 | `PASSED`，`run011-v3` |
| qa-engineer 审批 | 403 | 403 |
| qa-lead 审批 | `APPROVED`，`approver=user:qa-lead` | 同 |
| platform-admin CAS 晋升 | `PROMOTED`，v1→**v2** | `PROMOTED`，v1→v2 |
| 幂等重放（同键） | 返回 v2，**未新建版本** | 同 |
| 回执 | `tg_promotion_receipt`：`kind=PROMOTE, base=1, target=2` | 同 |

### 阶段 G：回归与回滚

| 项 | loop1 | loop2 |
|---|---|---|
| 回归 Run | `PASSED`，`testcase_version_id=tcase_bench_loc001@2` | 同 |
| 回归执行者 | `run011-w3` | `run011-v1` |
| 回滚 | `ROLLED_BACK`，`restored_version=3`（新版本承载旧内容），`target=tcase_bench_loc001:v2` | 同 |
| 内容级恢复 | 步骤/断言/Oracle 绑定三者的**逐字段比较**均相等；`latest_version==3` | 同 |
| 回滚回执 | `kind=ROLLBACK, base=2, target=3` | 同 |
| 回滚后复核 | 晋升版本再次执行 `PASSED` | 同 |
| 版本序列 | v1（基线）→ v2（晋升）→ v3（回滚恢复） | 同 |

> 回滚语义说明：版本内容不可变，因此"恢复旧内容"会生成**新版本**（v3），
> `restored_version` 不等于 `base_version`。验收以**内容级逐字段相等 + 版本指针**为准，
> 不以"只看指针"通过。

## 4. 追溯链（可从 API 与数据库双向复核）

loop1：

```
intent_1795543991789f06dee35f92a
→ tcase_17955439984472ae9b57bab85
→ tcase_17955439984472ae9b57bab85@1
→ oracle cand_a_create_ui@1
→ run_1795544003583a58bb2f8b846
→ evd_179554400772182f7d8890bee（23 条证据之一）
→ diagnosis_17955440238638dac12dcef3d
→ probeplan_1795544023…（STOP_CONFIRMED，0 执行）
→ repair_17955440367266686639fd04f
→ promotion receipt: base=1 / target=2
→ promoted v2
→ regression run run_17955440414220bc23d0b5dcb（v2，PASSED）
→ rollback receipt: base=2 / target=3
```

loop2 对应 ID（略，见 `loop2.json` 的 `ids` 与 `phases.trace.chain`）。

**每个引用都能重新读取**：`phases.trace.readable` 全部为 `true`（intent / testcase / version /
run / evidence / diagnosis / repair / probe_plans）。

## 5. 数据库级独立复核（`verify_db_run011.py`，只读 SQL）

两轮均 8/8：

| 检查 | 说明 |
|---|---|
| 所有 Run 的 `lease_owner` 均非 API 进程 | loop1 三个 Worker 各 1 个 Run；loop2 同理 |
| 终态 Run 均有 Evidence（无孤儿 Run） | `LEFT JOIN` 计数为 0 |
| Event `(producer_instance_id, sequence)` 全局唯一 | 0 重复 |
| Event 均可追溯到 Run | `context_refs->>'run'` 非空 |
| 审计 seq 唯一且连续 1..N | loop1 16 条、loop2 16 条 |
| 定位器用例存在多版本（晋升 + 回滚留痕） | v1/v2/v3 |
| CAS 晋升回执与回滚回执均已落库 | `tg_promotion_receipt` 2 行/轮 |
| 每条回执绑定 base/target 版本 | `target > base` |

## 6. 本轮在闭环中**发现并修复**的真实缺陷

| # | 缺陷 | 影响 | 修复 | 回归测试 |
|---|---|---|---|---|
| ① | LLM 探索器只看页面文本，会 `fill` 未渲染的输入框 | 探索轨迹不可编译，阶段 A 直接断链 | observer 增加结构化 `affordances`（data-testid + visible）；prompt 升 v2 并约束"只能操作可见 test-id" | 两轮真实探索均成功（loop1/loop2） |
| ② | RBAC 动作映射错误：`POST /explorations/{id}/compile` 落到 `system.configure` | qa-engineer 无法编译自己的探索（403） | 映射到 `testcase.create`（语义修正，不放宽） | `test_action_mapping_for_compile_and_evidence_verify` |
| ③ | 同上：`POST /evidence/{id}/verify` 落到 `system.configure` | 工程角色无法自证证据未被篡改 | 映射到 `read` | 同上 |
| ④ | 同上：`POST /repairs`（集合端点，无尾斜杠）落到 `system.configure` | 无法创建修复提案 | 映射到 `repair.submit` | 同上（新增集合端点断言） |
| ⑤ | `get_version()` 不回填 `workspace_id` | 非 default Workspace 的用例 Oracle 完整性恒为 `ORACLE_WORKSPACE_MISMATCH` → `STABLE_PASS` 但 `promotable=false` | 从行属性回填 workspace_id/testcase_id/version | `test_replay_oracle_integrity_uses_testcase_workspace` |
| ⑥ | `POST /testcases` 对非法 `compile_source` 抛 500 | API 契约错误（应 422） | API 边界用 `Literal["human","compiler","healed"]` | 真实 422（loop 失败记录保留） |
| ⑦ | PostgreSQL 下审计链校验把所有带 before/after 的条目判为 broken | 多进程 PG 拓扑无法自证审计完整性 | 校验前把 JSON 列还原为写入时的规范化字符串 | `test_audit_verify_normalizes_json_columns_for_postgres` |

> 这些缺陷都不是"为了让流程跑通而放宽门禁"：①②③④ 是语义映射修正（viewer 仍然被拒、
> 高风险晋升仍只给 platform-admin），⑤⑥ 是契约/数据归属修正，⑦ 是校验口径修正。

## 7. 明确未完成 / 不作为通过项

| 项 | 状态 | 说明 |
|---|---|---|
| Probe 在 WP1 闭环中真实执行 | **未执行（STOP_CONFIRMED）** | 设计语义正确；真实探针链路由 WP2 单独验证，不在此冒充 |
| 宿主级网络命名空间隔离 | **BLOCKED** | 本机无 pf/容器权限；仅验证应用层三层防护 |
| B-17 四个 Worker 故障场景 | **BLOCKED** | 与 RUN-008/009/010 相同的安全拦截，未绕过（见 WP5） |
| 生产环境部署验收 | **NOT_RUN** | 本机非生产环境 |
| 前端 UI 全链路点击验收 | **NOT_RUN（本轮）** | 见 WP8 |

## 8. 结论

TraceGuard 在**真实独立部署**（认证 + 独立 API + 3 独立 Worker + PG16 + 真实 Chromium +
强制代理）下，能够完成两轮**可复现、可追溯、可由数据库独立复核**的完整业务闭环；
拒绝、审批、晋升、回归、回滚的权限边界与审计留痕均来自服务端 Principal 与数据库事实，
不依赖前端按钮反馈或请求体声明。
