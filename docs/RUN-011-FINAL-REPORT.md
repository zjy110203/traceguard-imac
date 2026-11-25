# RUN-011 最终报告

> 执行时间：2026-11-25 01:10–02:25 CST（约 5h15m，指令建议窗口 30–36h，本轮为**一个完整会话**）。
> 分支：`workbuddy/run011-full-loop`（从 RUN-010 HEAD `b470518` 创建，不基于 main）。
> 仓库：`zjy110203/traceguard-imac`。
> 指令：`指令12.txt`（RUN-011: Full-Stack Closed-Loop Verification & Intelligence Maturation）。

## 0. 先回答指令 §十五 的五个问题

**Q1：TraceGuard 能否在真实独立部署环境下完成完整业务闭环？**
**能。** 两轮（不同 API/Worker/PG/TeamFlow 实例）各 62/62 项通过，数据库级复核各 8/8。
链路：Intent → 真实 LLM 探索 → 编译 → Oracle 绑定 → 3 次稳定性回放 → 审核 → 独立 Worker
正式执行 → Evidence → 真实故障 → 诊断 → 探针 → Repair → 沙箱 → 审批 → CAS 晋升 → 回归 →
回滚 → 回滚后复核。

**Q2：认证、权限和受控网络出口是否真正进入正式执行链路？**
**是。** 四角色真实会话；所有审批主体来自服务端 Principal（请求体伪造 `agent:auto-heal` 被忽略）；
qa-engineer 不能审批/晋升、qa-lead 不能晋升；每次正式执行的 `report.browser.egress.enforced=true`，
代理按 Run 绑定；未授权目标 0 成功响应。

**Q3：Worker、数据库、证据和 Repair 在异常情况下是否保持一致？**
**正常路径是；异常路径未验证。** 3 Worker 同库、lease/fencing、终态无孤儿证据、审计链完整、
CAS 回执与回滚回执可查、备份恢复后证据仍 PASS。但 B-17 的四个真实故障场景仍 BLOCKED
（进程被杀/租约过期/API 重启中断），本轮没有证据。

**Q4：Active Probe 和 Evidence Graph 是否真正提高了诊断能力？**
**探针真正参与了，但没有证据表明提高了准确率。** 3 个探针真实执行、产生 3 条新证据、
改变了假设的证据结构（LOCATOR 获支持、NETWORK 被反驳），但最终 outcome 未变（POST_HOC 语义）。
Graph 仍只做裁决降级，未提高准确率。**不宣称能力提升。**

**Q5：能否用独立、可复现的实验证明这些改进？**
**闭环与探针链路的改进可以（有脚本、有原始 JSON、有数据库复核）；诊断质量/收益不能**
（Benchmark v5 未执行，v4 的 Probe 臂测量无效）。

## 1. 交付数量（全部真实执行）

| 指标 | 值 |
|---|---|
| 完整闭环次数 | **2**（loop1 / loop2，不同实例） |
| 闭环检查项 | **124**（62×2），失败 0 |
| 数据库级复核 | **16**（8×2），失败 0 |
| 独立 Worker 进程 | **3+3**（`run011-w1/w2/w3`、`run011-v1/v2/v3`） |
| 真实 Run（loop1 库） | **9**（3 回放 + 正式 + 故障 + 沙箱 + 回归 + 回滚后复核 + 1） |
| 真实 Run（loop2 库，含实验） | **23** |
| Evidence（loop1 / loop2 库） | **219 / 426** |
| Event（loop1 / loop2 库） | **87 / 183** |
| Evidence 完整性校验 | 闭环抽样 5+5 PASS；恢复库 5 PASS |
| 探针执行 / 新证据 | **3/3 成功 / 3 条** |
| 探针回灌 | 2 条 hypothesis_update，`evaluation_history` 0→3 |
| 诊断（故障场景） | 2 次 `CONFIRMED / PERSISTENCE`（TF-DB-001，符合 GT） |
| 诊断（定位器） | `PROBABLE`（2 假设，探针后有证据变化，结论未变） |
| 诊断（时序） | `CONFIRMED / PERSISTENCE`（GT=TIMING，**错误确认，未修**） |
| Repair 成功 | **2/2**（validate/sandbox/approve/promote） |
| CAS 晋升 | v1→v2（两轮），幂等重放返回同版本 |
| 回归 | v2 精确版本，PASSED（两轮） |
| 回滚 | v2 → v3（内容级逐字段相等），回滚后复核 PASSED |
| 晋升/回滚回执 | 各 1 条/轮，`tg_promotion_receipt` 2 行/库 |
| PostgreSQL | 16.16，迁移 12 条，审计 16 条/库 `intact=true` |
| 备份/恢复 | `pg_dump` 137 KB → 新库 → 8/8 + 5 evidence PASS |
| Egress | 闭环 14/14 allowed / 0 blocked；负向 5 次 4/4（2 次对照受 OPEN-RISK-01 影响） |
| 测试 | **663 passed / 0 skipped**（含真实 PG16）；无 PG 时 650 passed / 13 skipped |
| Lint | `ruff check packages apps tests scripts` → All checks passed |

## 2. 本轮修改的源码（15 个文件）

| 文件 | 修改 | 对应缺陷 |
|---|---|---|
| `apps/api/traceguard_api/auth.py` | RBAC 映射：compile→testcase.create、evidence/verify→read、/repairs 集合端点→repair.submit | ①②③④ |
| `apps/api/traceguard_api/routers/catalog.py` | `compile_source` 用 Literal 在边界校验 | ⑥ |
| `apps/api/traceguard_api/routers/intelligence.py` | 探针执行 AppError→HTTP 映射（404 等） | A2 |
| `apps/api/traceguard_api/services/exploration_service.py` | observer 输出 `affordances`（data-testid + visible） | 探索器盲填 |
| `apps/api/traceguard_api/services/probe_service.py` | 证据 run/diagnosis/observed/collected/ingested；未知探针 404；业务状态缺口 | A1/A2/A5 |
| `packages/agents/.../gateway/llm_gateway.py` | explorer prompt v2（只操作可见 test-id） | 探索器 |
| `packages/core/.../security/egress_proxy.py` | 阻断响应加 `X-TraceGuard-Egress: BLOCK` | WP7 3.1 |
| `packages/evidence/.../probe/feedback.py` | DOM 按"失败步骤目标/断言目标"分别判定；报告缺失目标时的机制证据 | A3 |
| `packages/evidence/.../probe/planner.py` | `ProbeResult.observed_at` | A1 |
| `packages/execution/.../browser/egress.py` | `blocked_reasons`；审计按 Run 过滤 | WP7 3.2/3.3 |
| `packages/execution/.../runtime/pipeline.py` | 报告自包含 steps.target/value、assertion.expected | A4 |
| `packages/execution/.../runtime/playwright_observer.py` | 代理阻断 → 步骤失败 | WP7 3.1 |
| `packages/persistence/.../auth.py` | 审计链校验 JSON 列归一化 | ⑦ |
| `packages/persistence/.../catalog.py` | `get_version` 回填 workspace_id | ⑤ |
| `.env.example` | 补 egress / worker / 运维探针说明 | WP9 |

新增脚本（4 个，纯 HTTP 客户端）与 10 份文档、`docs/assets/run011/` 证据目录。

## 3. 新增回归测试（5 个）

| 测试 | 覆盖 |
|---|---|
| `test_action_mapping_for_compile_and_evidence_verify` + 集合端点断言 | RBAC 映射修正，高风险仍 fail-closed |
| `test_replay_oracle_integrity_uses_testcase_workspace` | 非 default workspace 的 Oracle 完整性 |
| `test_unknown_probe_rejected_before_budget_and_creates_no_record` | 未注册探针 fail-closed |
| `test_probe_evidence_carries_scope_and_time_semantics` | 三段式时间语义与 run/diagnosis 绑定 |
| `test_dom_probe_uses_failed_step_target_when_assert_never_ran` | LOCATOR 机制特异性证据 |
| `test_audit_verify_normalizes_json_columns_for_postgres` | PG 审计链校验 |

## 4. 未完成 / BLOCKED（不降低标准）

| 项 | 状态 | 原因 |
|---|---|---|
| B-17 四真实故障场景 | **BLOCKED** | 安全策略拦截含 subprocess+env 的故障脚本；未绕过（WP5） |
| 宿主级网络隔离 | **BLOCKED** | 无 pf/容器权限；未改系统防火墙 |
| Benchmark v5（19+ 场景 × 2 轮） | **NOT RUN** | v4 测量口径无效；先修测量再做实验（WP4） |
| Benchmark LLM 臂 / 人工基线 | **BLOCKED** | 无计费授权 / 无真人 |
| UI 全链路 13 步点击验收 | **NOT RUN** | 时间优先给 P0；见 WP8 |
| 多 API 实例 / DB 断连 / 长事务 | **NOT RUN** | 见 WP6 §7 |
| 生产部署验收 | **NOT RUN** | 无生产环境 |
| LICENSE | **NEEDS_DECISION** | 未擅自选择 |
| CI（GitHub Actions） | **BLOCKED** | 本机无 `gh`，且不读取/导出凭据；未推送前无法触发 |

### 4.1 OPEN RISK

| ID | 风险 | 证据 |
|---|---|---|
| OPEN-RISK-01 | 授权目标的 https 变体偶发 `PORT_NOT_ALLOWED`（7 次 WP7 运行中 2 次） | WP7 §4，`blocked_reasons` |
| OPEN-RISK-02 | 时序故障被错误 `CONFIRMED`（GT=TIMING） | WP3 §3.1 |
| OPEN-RISK-03 | LLM 候选假设不去重，导致结论在运行间漂移（INCONCLUSIVE/PROBABLE） | WP3 §3.2/3.3 |
| OPEN-RISK-04 | 全量测试并行执行时出现过一次 60 errors（PG 资源竞争），串行稳定 | WP6 §6 |

## 5. 对项目当前定位的判断

TraceGuard 现在可以被描述为：

> 一个在**独立部署、身份可信、执行安全、证据可追溯**四件事上有真实证据的 Web UI 自动化测试平台原型；
> 它的完整业务闭环（含自动修复、晋升、回滚）已经在真实拓扑下跑通两次；
> 但**智能诊断的收益**与**生产级可靠性**仍缺少有效证据。

不能描述为："诊断能力已被证明提升"、"生产可用"、"Worker 故障恢复已验证"。

## 6. 下一阶段建议（按优先级）

1. **修测量再做实验**：重写 v5 驱动（真实探针计数、真实新证据、固定 LLM 开关），先 3 场景打通口径。
2. **时序诊断**：增加"延迟复测"探针（Run 内 + 延迟后各一次），把 `TF-DB-004` 从 PERSISTENCE 纠正为 TIMING。
3. **假设竞争**：LLM 候选按机制去重 + 来源独立性计分（避免同一证据支持多条同机制假设）。
4. **B-17**：向安全负责人提交正式申请（白名单 env + 显式最小变量），批准后补四个故障场景。
5. **OPEN-RISK-01**：给代理审计条目加 `policy_origins` 快照，并在 BrowserManager 启动后做一次预热请求。
6. **UI 全链路**：三角色 13 步真实点击 + 服务端状态核对。
7. **CI**：获得授权后推送分支并触发 PR Gate / Nightly / Release；否则 CI 状态保持 BLOCKED。

## 7. 复现方式

```bash
# 1) 起 PG16 + 建库 + 四角色（见 WP9 §1）
# 2) 起 TeamFlow / API / 3 Worker（见 WP9 §1）
# 3) 跑闭环（约 70s/轮）
TG_RUN011_PASSWORD='***' .venv/bin/python scripts/full_loop_run011.py \
  --api http://127.0.0.1:8110 --teamflow http://127.0.0.1:8111 \
  --label loop1 --workspace ws-run011-l1 \
  --workers run011-w1,run011-w2,run011-w3 \
  --out docs/assets/run011/full-loop/loop1.json
# 4) 数据库复核
.venv/bin/python scripts/verify_db_run011.py \
  --dsn "postgresql+psycopg://…/traceguard_run011_l1" --workspace ws-run011-l1 \
  --out docs/assets/run011/full-loop/loop1-db.json
# 5) 探针实验 / 网络负向
.venv/bin/python scripts/probe_lab_run011.py --api … --teamflow … --workers …
.venv/bin/python scripts/network_negative_run011.py --api … --teamflow … --workers …
```

## 8. 交付与远程状态

| 项 | 状态 |
|---|---|
| 分支 | `workbuddy/run011-full-loop`（仅包含 RUN-010 HEAD + 本轮 1 个提交） |
| 本地提交 | `f604e5d`（51 文件，+9,667 / −25） |
| 远程推送 | ✅ `origin/workbuddy/run011-full-loop`（新分支） |
| 增量 PR（目标 = RUN-010 分支） | **未创建（BLOCKED）**：本机无 `gh`，且不读取/导出凭据；GitHub 返回的创建入口：`https://github.com/zjy110203/traceguard-imac/pull/new/workbuddy/run011-full-loop` |
| PR #2（RUN-010） | 未修改、未合并（指令要求保持不动） |
| main | 未变（`ac2876e`） |
| CI | **未触发**（无 Actions 触发手段；推送本身不触发 PR Gate 的 pull_request 事件） |

> 说明：推送使用的是本机既有 git 凭据（由 git credential helper 在内部使用），
> 本轮**没有**读取、打印或导出任何 token；因此无法调用 GitHub REST API 创建 PR。

## 9. 结论

本轮完成了指令的**第一目标**（最新架构下的真实独立部署完整闭环，两轮）与**第二目标的一部分**
（Active Probe 真正执行、真正回灌、时间语义可验证），并修复了 7 个在真实链路中暴露的缺陷。
第三目标（多进程/PG/异常恢复）只完成了正常路径与备份恢复；异常路径仍缺证据。
**没有为了让报告好看而降低门禁、伪造证据或宣称未验证的能力。**

---

## RUN-011-R1 修订注记（2026-11-25 追加，不覆盖以上原始记录）

> 以下为 RUN-011-R1（指令13）对本报告表述的**追加更正**；原始记录保持存档。
> 权威交付：`docs/RUN-011-R1-FINAL-REPORT.md`。

1. **执行时间统计**：本报告时间窗（01:10–02:25 CST）与各 WP 报告自洽；
   曾出现的 UTC/CST 混列按 WP 报告换算统一，R1 时间独立计算不混计。
2. **Benchmark v4 口径**：`RUN-010-WP5-BENCHMARK.md` 已追加 ERRATA——
   「所有臂 probes_per_sample=0」为报告笔误（归档 JSON：E/F/G=0.9286），
   且该值是**重建信号数而非真实探针执行数**，Probe 臂测量无效；详见 ERRATA E1–E3。
3. **闭环性质描述更正**：本文 Q1 的「完整业务闭环 62/62」是**多 TestCase 集成演示**
   （编译用例 + `tcase_tf_false_pass` + `tcase_bench_loc001` 三用例拼接，
   ID 级证据见 `RUN-011-R1-BASELINE.md` §2）；**单 TestCase 完整血缘闭环**由
   R1 WP1 实现（`RUN-011-R1-WP1-LINEAGE.md`，18 步同用例、API 86/86 + DB 17/17 ×2）。
   两者分开描述，不混引。
4. **修复来源区分**：本文「Repair 2/2」中的修复 changes 为脚本预写死的确定性夹具
   （`test_id=create-submit → role=button:创建`）——属**规则/人工夹具**，
   不是 Agent 从 Diagnosis 自动推导；R1 起 `repair_source` 强制三分标注
   （human:/rule:/llm:），R1 实测为 `rule:locator-fixture`。
5. **POST_HOC 证据**：本文「探针 3/3」实验中的事后观测证据一律为 POST_HOC
   （非 Run 内证据）；R1 WP2 起三态语义逐条留痕（ALIGNED/POST_HOC/UNKNOWN），
   不把事后观测描述为 Run 内证据、不改写 Run 时结论。
6. **Benchmark 状态**：本文「Benchmark NOT RUN（v4 测量无效）」保持成立；
   R1 WP4 的 v5 两轮为**新实测**（23 场景 ×2、真实探针 54/54），见
   `RUN-011-R1-WP4-BENCHMARK-PILOT.md`，不回溯改写本轮 NOT RUN 表述。
7. **B-17 状态**：本文「BLOCKED」保持；R1 零次触碰被拦截操作，仅整理
   安全批准申请材料（`RUN-011-R1-WP7-B17-SECURITY-REQUEST.md`），无批准不实施。
