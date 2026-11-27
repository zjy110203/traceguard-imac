# RUN-020 工作包（可直接执行的任务卡）

> 配套设计：`docs/RUN-020-DESIGN.md`。本文件把设计拆成**可派工、可验收**的任务卡。
> 约定：
> - 每个任务卡都有 **输入 / 输出文件 / 依赖 / 验收命令 / DoD**，验收命令必须能直接跑；
> - 标 **`READY`** 的任务无外部依赖，本轮或下一轮即可开工；
> - 标 **`BLOCKED-EXT`** 的任务需要外部条件（Secret / Actions 权限 / 预算 / 人工评审），
>   未满足时**保持阻塞，不伪造**；
> - 任务编号即提交前缀（如 `RUN-020 WP1-T3: ...`），一个任务一个提交，便于回滚与评审。

## 0. 任务总览与依赖

```
M0（已完成）
 └─ T-000 审计/准入/设计/工作包                                     [DONE]

M1 Provide 基座
 ├─ WP1-T1 ProviderAdapter 抽象            READY
 ├─ WP1-T2 FakeAdapter + 三态 provenance   READY   ← T1
 ├─ WP1-T3 RetryPolicy                     READY   ← T1
 ├─ WP1-T4 StructuredValidator + schemas   READY   ← T1
 ├─ WP1-T5 CallAudit(DB v15 + JSONL)       READY   ← T1,T4
 ├─ WP1-T6 summary/calls API               READY   ← T5
 ├─ WP1-T7 FallbackPolicy 统一             READY   ← T2,T3
 └─ WP1-T8 测试 + 真实 smoke               READY   ← T1..T7（smoke 需本地凭据）

M2 Compiler
 ├─ WP2-T1 app_profile                     READY
 ├─ WP2-T2 RequirementParser               READY   ← T1
 ├─ WP2-T3 PageObjectResolver              READY   ← T1
 ├─ WP2-T4 StepGenerator/OracleProposer    READY   ← T2,T3
 ├─ WP2-T5 AmbiguityDetector(阻断语义)     READY   ← T2,T3
 ├─ WP2-T6 可执行性验证(Replay×2)          READY   ← T4
 ├─ WP2-T7 API 端点 + 权限                 READY   ← T5,T6
 ├─ WP2-T8 holdout v1（≥40 条）            READY   ← T1
 └─ WP2-T9 评测脚本 + 六指标               READY   ← T5..T8

M3 Diagnosis
 ├─ WP3-T1 证据簇化                        READY
 ├─ WP3-T2 MultiFaultAdjudicator           READY   ← T1
 ├─ WP3-T3 弃权原因 + 主因排序             READY   ← T2
 ├─ WP3-T4 难例规则 D1–D7                  READY   ← T1,T2
 ├─ WP3-T5 holdout v3（≥56 样本）          READY
 ├─ WP3-T6 指标扩展                        READY   ← T5
 └─ WP3-T7 评测脚本 + 分层报告             READY   ← T3..T6

M4 Repair
 ├─ WP4-T1 PatchContract                   READY
 ├─ WP4-T2 locator 修复策略                READY
 ├─ WP4-T3 断言修复 + Oracle 兼容          READY   ← T1
 ├─ WP4-T4 自动拒绝 + 误修复回滚标记       READY   ← T1,T2,T3
 ├─ WP4-T5 repair bench + 四指标           READY   ← T2,T3,T4
 └─ WP4-T6 反例测试（越权/弱化/回滚）      READY   ← T1..T4

M5 Benchmark 统一
 ├─ WP5-T1 registry + manifest 守卫        READY
 ├─ WP5-T2 FailureArchive（只追加）        READY   ← T1
 ├─ WP5-T3 迁移 v16 + sample 表            READY   ← T1
 ├─ WP5-T4 report renderer                 READY   ← T1,T3
 ├─ WP5-T5 统一入口脚本                    READY   ← T1..T4
 └─ WP5-T6 CI 实验 job + 禁删 holdout 检查 READY-部分（dispatch 需权限）

M6 发布准入
 ├─ REL-1 CI Secret 配置                   BLOCKED-EXT（需负责人）
 ├─ REL-2 Reliability Gate dispatch        BLOCKED-EXT（需 Actions 权限）
 └─ REL-3 PR 创建                          BLOCKED-EXT（需授权）
```

**关键路径**：WP1-T1 → T4 → T5 → T7 → WP2-T9 / WP3-T7 / WP4-T5 → WP5-T5。
M2/M3/M4 可在 WP1-T3 完成后并行（互不依赖）。

---

## M0：审计与准备（本轮）

### T-000 最终审计 + 发布准入 + RUN-020 设计

| 项 | 内容 |
|---|---|
| 状态 | **DONE（本轮）** |
| 产出 | `docs/RUN-020-PREPARATION-AUDIT.md`、`docs/RUN-020-RELEASE-ADMISSION.md`、`docs/RUN-020-DESIGN.md`、`docs/RUN-020-WORKPACKAGES.md`、`docs/RUN-020-PR-DESCRIPTION.md` |
| 附带修复 | README / FEATURE-STATUS / FINAL-REPORT / GAP-CLOSURE 的 CI 编号回填（#115/#68 @ `564b21c`） |
| 附带证据 | `docs/assets/run020-prep/llm/llm-smoke-20261127.json`（11/11 REAL_PROVIDER） |
| 代码改动 | **无** |

---

## M1：Provider 基座（WP1）

### WP1-T1 ProviderAdapter 抽象

| 项 | 内容 |
|---|---|
| 目标 | 把"具体 DeepSeek 类"改成"协议 + 适配器"，调用方零改动；为第二个 provider 留位 |
| 输入 | 现有 `gateway/llm_gateway.py::DeepSeekProvider` / `ProviderRegistry` |
| 输出文件 | 新增 `gateway/providers/{__init__,base,deepseek}.py`；修改 `llm_gateway.py`（转发兼容层） |
| 关键实现 | `ProviderAdapter` Protocol（`chat/chat_json/health/capabilities/name`）；`ProviderRegistry.register(name, adapter)`；`build_provider(settings)` 按 `TG_LLM_PROVIDER` 选择；旧 `DeepSeekProvider` 保留为 `DeepSeekAdapter` 的别名（不破坏既有 import） |
| 依赖 | 无 |
| 验收 | `pytest -q tests/unit/test_llm_provider_contract.py`；既有 `tests/unit/test_llm_gateway.py` 全绿 |
| DoD | 旧调用点（explorer/compiler/diagnosis/probe/repair/smoke）不改一行即可运行；`grep -r "DeepSeekProvider(" apps packages` 仅出现在 provider 包内 |
| 规模 | 1.5 人日 |

### WP1-T2 FakeAdapter + 三态 provenance

| 项 | 内容 |
|---|---|
| 目标 | 让"假调用"永远可识别，且无法在生产配置下启用 |
| 输出文件 | 新增 `gateway/providers/fake.py`；`gateway/audit.py` 的 `Provenance` 枚举 |
| 关键实现 | `FakeAdapter` 只在 `TG_LLM_FAKE=1` 且 `TG_ENV in {test, dev}` 时可实例化，否则抛 `LLMError("rejected")`；每次返回带 `provenance=FAKE_PROVIDER`；生产 settings 下 `build_provider("fake")` 直接失败 |
| 验收 | `pytest -q tests/unit/test_llm_provider_contract.py -k fake`；新增反例：生产配置导入 FakeAdapter → 抛错 |
| DoD | 三态（RULE/FAKE/REAL）在审计与 `/system/llm/summary` 中都可分列；FAKE 记录不进入任何 release 证据过滤结果 |
| 规模 | 0.5 人日 |

### WP1-T3 RetryPolicy（Retry-After / 抖动 / 分类）

| 项 | 内容 |
|---|---|
| 目标 | 6 类错误语义可复现，重试不放大故障 |
| 输出文件 | 新增 `gateway/retry.py`；修改 `llm_gateway.py` 使用策略 |
| 关键实现 | 分类：`rate_limit/server/network/timeout/rejected/invalid_json/budget_exhausted`；`Retry-After` 优先（秒/HTTP-date）；`min(2^n, 8)` 全抖动；`timeout` 最多 1 次；`rejected` 不重试 |
| 验收 | `pytest -q tests/unit/test_llm_retry_policy.py`（httpx `MockTransport` 覆盖 6 类 + Retry-After） |
| DoD | 每种错误的 `retry_count` 与总耗时符合设计表；无无限重试路径 |
| 规模 | 1 人日 |

### WP1-T4 StructuredValidator + schemas

| 项 | 内容 |
|---|---|
| 目标 | 模型输出必须"结构正确"，错误显式，不做尽力修复 |
| 输出文件 | 新增 `gateway/schemas.py`、`gateway/validation.py` |
| 关键实现 | pydantic 模型：`ExplorerPlan`、`CompileAdvice`、`DiagnosisHypotheses`、`ProbePlan`、`RepairPatch`（`extra="forbid"`）；`chat_json(schema_id=...)`；失败回问一次（仅回传 `validation_error` 摘要，不回传 GT） |
| 验收 | `pytest -q tests/unit/test_llm_structured_output.py`：缺字段/多字段/类型错/嵌套错 4 类各 1 例 |
| DoD | 非法输出 0 被静默接受；`invalid_json` 率可在审计中统计 |
| 规模 | 1.5 人日 |

### WP1-T5 CallAudit（DB v15 + JSONL 双写）

| 项 | 内容 |
|---|---|
| 目标 | 每次真实调用有权威记录，可按 Run/Agent 查询，且不可绕过 |
| 输出文件 | 新增 `packages/persistence/.../llm_audit.py`；修改 `db.py`（v15）；新增 `gateway/audit.py`；修改 `llm_gateway.py` |
| 关键实现 | `tg_llm_call`（字段见设计 §5.2.4）；JSONL 镜像；写失败 → `LLMError("audit_failed")`（fail-closed）；`request_fingerprint` 只存哈希 |
| 验收 | `pytest -q tests/integration/test_llm_audit_persist.py`：3 次调用 3 行；字段完整；注入失败 sink → 返回 audit_failed |
| DoD | SQLite/PG16 双跑通过；迁移幂等（重复启动不重复建表） |
| 规模 | 2 人日 |

### WP1-T6 summary / calls API

| 项 | 内容 |
|---|---|
| 目标 | 成本、延迟、错误率、三态分布可视、可查 |
| 输出文件 | 修改 `apps/api/.../routers/system.py`；新增 `apps/api/.../services/llm_service.py` |
| 关键实现 | `GET /system/llm/summary?day=&agent=&model=` 返回 `p50/p95`、`error_rate`、`cost`、`provenance_breakdown`、`budget_remaining`；`GET /system/llm/calls?run_id=&agent=&limit=`（不含 prompt 明文） |
| 验收 | `pytest -q tests/integration/test_llm_audit_persist.py -k summary`；手工 `curl` 两次结果一致 |
| DoD | 分页上限 200；非授权用户 403（沿用 `system.observe`） |
| 规模 | 1 人日 |

### WP1-T7 FallbackPolicy 统一

| 项 | 内容 |
|---|---|
| 目标 | LLM 失败时回落规则，且"回落"在产物里可见 |
| 输出文件 | 新增 `gateway/fallback.py`；修改 explorer/compiler/diagnosis/probe/repair 五处调用点 |
| 关键实现 | `FallbackPolicy.decide(error_kind) -> RULE`；产物写 `provenance=RULE` + `fallback_reason`；同一 Run 的 fallback 次数计入审计 |
| 验收 | `pytest -q tests/unit/test_llm_fallback.py`：6 类错误各产生一次回落且标记正确 |
| DoD | 0 处"把 fallback 写成 LLM 成功"；`/system/llm/summary` 的 `fallback_count` 与实际一致 |
| 规模 | 1 人日 |

### WP1-T8 测试汇总 + 真实 smoke

| 项 | 内容 |
|---|---|
| 目标 | 基座可交付：单测 + 集成 + 真实调用 |
| 验收命令 | `pytest -q tests/unit/test_llm_*.py tests/integration/test_llm_audit_persist.py`；`.venv/bin/python scripts/llm_smoke.py`（需本地凭据，期望 11/11） |
| DoD | 既有 1144 passed 不下降；`ruff check/format` 通过 |
| 阻塞 | smoke 需本地 `DEEPSEEK_API_KEY`（本机已具备，见审计 §7.3） |
| 规模 | 1 人日 |

---

## M2：Compiler（WP2）

### WP2-T1 `app_profile` 模型与加载

| 项 | 内容 |
|---|---|
| 目标 | 给编译器一份"可信的页面/元素地图"，禁止凭空造 locator |
| 输出文件 | 新增 `packages/agents/.../compiler/app_profile.py`；示例 `benchmark/datasets/app-profiles/teamflow-v1.json` |
| 关键实现 | pydantic 模型（`profile_id/version/base_url/auth/routes/locator_map/business_api`）；`locator_map` 值必须是 `LocatorSpec` 允许的策略；版本不可变，加载时校验 |
| 验收 | `pytest -q tests/unit/test_app_profile.py`：非法策略/空 value/重复 key 各 1 例 |
| DoD | profile 变更需新版本号；编译产物记录 `profile_id@version` |
| 规模 | 1 人日 |

### WP2-T2 RequirementParser

| 项 | 内容 |
|---|---|
| 目标 | 把自然语言需求拆成实体/动作/期望/未知槽位 |
| 输出文件 | 新增 `compiler/requirements.py` |
| 关键实现 | 规则优先（关键词 + 结构），LLM 可选增强；输出 `RequirementGraph`（含 `source_span` 便于追溯）；未知槽位必须显式列出 |
| 验收 | `pytest -q tests/unit/test_compiler_requirements.py`：8 条样例（含缺字段/缺阈值/多实体） |
| DoD | 不产生"未标注来源"的实体/断言；`unknowns` 非空时必须进入歧义检测 |
| 规模 | 2 人日 |

### WP2-T3 PageObjectResolver

| 项 | 内容 |
|---|---|
| 目标 | 把动作映射到 `app_profile` 中的页面与元素 |
| 输出文件 | 新增 `compiler/page_objects.py` |
| 关键实现 | 路由匹配（精确 + 参数化）；元素查找（语义名 → locator_map key）；未命中 → `Ambiguity(MISSING_LOCATOR / AMBIGUOUS_ROUTE)`；优先级沿用 `role > test_id > label > placeholder > css > xpath` |
| 验收 | `pytest -q tests/unit/test_page_object_resolver.py`：命中/未命中/多义 3 类 |
| DoD | 0 个"自动生成 css/xpath"的兜底路径（必须来自 profile 或已见元素） |
| 规模 | 2 人日 |

### WP2-T4 StepGenerator + OracleProposer

| 项 | 内容 |
|---|---|
| 目标 | 生成可执行步骤与有来源的断言 |
| 输出文件 | 新增 `compiler/step_generator.py`、`compiler/oracle_proposer.py` |
| 关键实现 | 步骤按动作序列展开（navigate → 操作 → 提交 → 断言）；断言必须带 `expected` 与来源（`business_api` / `ui_text` / `intent`）；断言强度下限：至少 1 条业务状态或 1 条关键 UI 结果，禁止只断言"页面可打开" |
| 验收 | `pytest -q tests/unit/test_step_generator.py`：4 类页面 × 2 类断言 |
| DoD | 生成的 `TestCaseVersion` 通过既有契约校验；`assertion_id` 稳定可复现 |
| 规模 | 2.5 人日 |

### WP2-T5 AmbiguityDetector（阻断语义）

| 项 | 内容 |
|---|---|
| 目标 | 缺业务事实时**主动问**，而不是猜 |
| 输出文件 | 新增 `compiler/ambiguity.py` |
| 关键实现 | 6 类阻断 + 1 类非阻断（设计 §6.2.3）；`CompileQuestion` 带 `code/question/options/blocking`；`POST /compile` 遇到未答阻断 → 409 `COMPILE_BLOCKED_BY_AMBIGUITY` |
| 验收 | `pytest -q tests/unit/test_compiler_ambiguity.py`：6 类各 1 例 + 「全部回答后放行」1 例 |
| DoD | 阻断问题 0 被忽略；问题清单可按 intent 查询 |
| 规模 | 2 人日 |

### WP2-T6 可执行性验证（Replay × 2）

| 项 | 内容 |
|---|---|
| 目标 | 生成结果"真的能跑"，否则不产候选 |
| 输出文件 | 修改 `apps/api/.../services/exploration_service.py`（或新增 `compiler_service.py`）；复用 `stability_service` |
| 关键实现 | 隔离 workspace → 创建两次独立 Run → 均 `COMPLETED+PASSED` 且 locator 全解析 → `executable=true`；失败返回证据引用 |
| 验收 | `pytest -q tests/integration/test_nl_compile_flow.py -k executable`（Fake Provider + 真实 TeamFlow） |
| DoD | 0 个未经验证就创建的候选；失败原因可读（step_id + 错误类型） |
| 规模 | 2.5 人日 |

### WP2-T7 API 端点 + 权限

| 项 | 内容 |
|---|---|
| 目标 | 三个端点可用，权限与既有矩阵一致 |
| 输出文件 | 修改 `apps/api/.../routers/intelligence.py` |
| 关键实现 | `POST /intents/{id}/compile-draft`、`POST /intents/{id}/compile`、`GET /intents/{id}/compile-questions`；权限 `testcase.create`；候选走 `create_trusted_candidate(source=compiler, origin=compiler_flow)` |
| 验收 | `pytest -q tests/integration/test_nl_compile_flow.py`；RBAC：viewer 403、qa-engineer 201、qa-lead 可审核 |
| DoD | 响应含 `provenance`、`llm_meta`、`questions[]`；不返回 GT |
| 规模 | 1.5 人日 |

### WP2-T8 Compiler Holdout v1（≥40 条）

| 项 | 内容 |
|---|---|
| 目标 | 独立、冻结、可复现的编译评测集 |
| 输出文件 | `benchmark/datasets/compiler-holdout-v1/{cases.jsonl,manifest.json,README.md}`；新增 `benchmark/traceguard_benchmark/compiler_holdout.py` |
| 关键实现 | 40 条覆盖 6 页面 × 4 动作 × 3 断言；每条含 `expected_steps`（规范化）、`expected_assertions`、`ambiguities[]`；GT 只在评测端可读（token） |
| 验收 | `pytest -q tests/integration/test_holdout_manifest_guard.py -k compiler`；篡改 1 字节 → 拒绝 |
| DoD | manifest 记录版本 + 每条 SHA-256；holdout 不出现在 `docs/` 与 prompt 中 |
| 规模 | 3 人日（数据编写为主） |

### WP2-T9 评测脚本 + 六指标

| 项 | 内容 |
|---|---|
| 目标 | 一条命令产出可引用的报告 |
| 输出文件 | 新增 `scripts/compiler_holdout_run020.py`；`benchmark/.../compiler_metrics.py` |
| 关键实现 | 步骤正确率（LCS 对齐）、Locator 有效率、Oracle 正确率、可执行率、人工修改率（审核 diff）、错误生成率；输出 JSON + Markdown |
| 验收 | `python scripts/compiler_holdout_run020.py --arm rule --repeats 1 --budget-usd 0`；`--arm llm` 需凭据 |
| DoD | 报告含样本级明细与失败归档；负结果照实写 |
| 规模 | 2 人日 |

---

## M3：Diagnosis（WP3）

### WP3-T1 证据簇化（HypothesisBuilder）

| 项 | 内容 |
|---|---|
| 目标 | 多故障不被压成一条"复合"假设 |
| 输出文件 | 新增 `packages/evidence/.../diagnosis/hypothesis_builder.py`；修改 `diagnosis_service.py` |
| 关键实现 | 以 `(step_id, 机制, 时间窗)` 聚簇；同簇证据合并、跨簇独立；每簇生成一个假设，带 `evidence_refs` |
| 验收 | `pytest -q tests/unit/test_hypothesis_builder.py`：单故障 1 簇 / 双故障 2 簇 / 重复证据 1 簇 |
| DoD | 簇与证据一一可追溯；不改变既有 6 类规则的触发条件 |
| 规模 | 2 人日 |

### WP3-T2 MultiFaultAdjudicator

| 项 | 内容 |
|---|---|
| 目标 | 多根因并存时可分别裁决，冲突时不强行择一 |
| 输出文件 | 新增 `diagnosis/multifault.py`；修改 `graph/adjudication.py`（独立性计数） |
| 关键实现 | 互斥机制校验（同一证据不得支撑两条互斥机制）；独立支持计数；`authority_confirmed/refuted`；冲突进 `unresolved_conflicts` |
| 验收 | `pytest -q tests/unit/test_multifault_adjudication.py`：双根因/冲突/重复计数 3 类 |
| DoD | 多根因 exact match 可计算；0 处"证据重复计数提置信度" |
| 规模 | 3 人日 |

### WP3-T3 弃权原因 + 主因排序

| 项 | 内容 |
|---|---|
| 目标 | 弃权可解释、主因可复算 |
| 输出文件 | 新增 `diagnosis/abstention.py`；修改 `engine.py`（排序规则）、`diagnosis_service.py`（落库） |
| 关键实现 | 5 类 `abstain_reason`；主因排序规则（独立支持数 → 机制优先级），写进文档 + 单测 |
| 验收 | `pytest -q tests/unit/test_abstention_reasons.py`；`test_primary_cause_ordering.py` |
| DoD | `INCONCLUSIVE` 不再是唯一桶；排序规则改动必须改测试（防事后调参） |
| 规模 | 2 人日 |

### WP3-T4 难例规则 D1–D7

| 项 | 内容 |
|---|---|
| 目标 | 7 类难例各有确定性判定路径 |
| 输出文件 | 修改 `diagnosis_service.py`（规则）、`multifault.py` |
| 关键实现 | 见设计 §7.2 表；每类给"确认条件"与"弃权条件"，不得用症状当根因 |
| 验收 | `pytest -q tests/integration/test_multi_root_diagnosis.py`（7 类各 1 例，真实 Run） |
| DoD | 每类至少 1 条真实复现；False Pass = 0 |
| 规模 | 4 人日 |

### WP3-T5 Diagnosis Holdout v3（≥56 样本）

| 项 | 内容 |
|---|---|
| 目标 | 多标签、分层（合成/真实）、冻结 |
| 输出文件 | `benchmark/datasets/diagnosis-holdout-v3/*`；新增 `benchmark/.../diagnosis_holdout.py` |
| 关键实现 | 7 类 × 4 条 × 2 轮 = 56；`expected_root_causes[]`（含主因标记）；`source: synthetic|real`；与 v1/v2 完全隔离 |
| 验收 | `pytest -q tests/integration/test_holdout_manifest_guard.py -k diagnosis` |
| DoD | 不修改 RUN-018/RUN-019 旧集；manifest 校验拒绝篡改 |
| 规模 | 4 人日 |

### WP3-T6 指标扩展

| 项 | 内容 |
|---|---|
| 目标 | 六项指标全部可算 |
| 输出文件 | 修改 `benchmark/.../evaluation.py`；新增 `diagnosis_metrics.py` |
| 关键实现 | 多标签 Recall/Precision/Macro-F1；False Pass；False Positive；Abstention（按原因）；Primary Cause Accuracy；Multi-root Exact Match |
| 验收 | `pytest -q tests/unit/test_metrics_multilabel.py`（手算样例对照） |
| DoD | 指标与手算一致；0 分母有显式语义（返回 `None` 而非 0） |
| 规模 | 2 人日 |

### WP3-T7 评测脚本 + 分层报告

| 项 | 内容 |
|---|---|
| 输出文件 | 新增 `scripts/diagnosis_holdout_run020.py` |
| 验收 | `python scripts/diagnosis_holdout_run020.py --arm rule --repeats 2`；`--arm llm` 需凭据 |
| DoD | 报告分 `synthetic` / `real` 两栏；含失败样本与 `abstain_reason` 分布 |
| 规模 | 1.5 人日 |

---

## M4：Repair（WP4）

### WP4-T1 PatchContract

| 项 | 内容 |
|---|---|
| 目标 | LLM 只能改"允许的路径"，且不能弱化断言 |
| 输出文件 | 新增 `healing/patch_contract.py` |
| 关键实现 | 路径白名单（设计 §8.2.2）；`MAX_CHANGES=5`；禁止项枚举；`evidence_refs` 必填 |
| 验收 | `pytest -q tests/unit/test_patch_contract.py`：越权路径/删断言/放宽阈值/跨用例 4 类各 1 例 |
| DoD | 违规补丁 0 进入沙箱；错误码固定（`PATCH_CONTRACT_VIOLATED`） |
| 规模 | 1.5 人日 |

### WP4-T2 locator 修复策略

| 项 | 内容 |
|---|---|
| 输出文件 | 新增 `healing/locator_repair.py` |
| 关键实现 | DOM 变化 → 按优先级重解析；只能从 `app_profile` 或已见元素派生；`stability_score` 不降；不跨页面 |
| 验收 | `pytest -q tests/unit/test_locator_repair.py`：test_id 失效/元素移动/重名 3 类 |
| DoD | 修复后沙箱可解析；0 个"猜 css"路径 |
| 规模 | 2.5 人日 |

### WP4-T3 断言修复 + Oracle 兼容

| 项 | 内容 |
|---|---|
| 输出文件 | 新增 `healing/assertion_repair.py` |
| 关键实现 | 仅允许 `expected` 值更新（类型一致）；Oracle 版本不允许改；不兼容 → `ORACLE_INCOMPATIBLE` 拒绝 |
| 验收 | `pytest -q tests/unit/test_assertion_repair.py`：值更新/类型变更拒绝/Oracle 冲突 3 类 |
| DoD | 断言 kind/阈值不可变（有测试） |
| 规模 | 2 人日 |

### WP4-T4 自动拒绝 + 误修复回滚标记

| 项 | 内容 |
|---|---|
| 输出文件 | 修改 `apps/api/.../services/repair_service.py`、`healing/service.py` |
| 关键实现 | 沙箱失败 → `VALIDATION_FAILED`；断言弱化 → `REJECTED(ASSERTION_WEAKENED)`；晋升后 Regression FAILED → 自动 `ROLLED_BACK` + `MISREPAIR`；同类误修复 2 次 → 策略 `quarantine` |
| 验收 | `pytest -q tests/integration/test_repair_auto_reject_run020.py`（真实 Run 触发回滚） |
| DoD | 误修复 0 留在 PROMOTED；回滚幂等；回滚不删原版本 |
| 规模 | 3 人日 |

### WP4-T5 repair bench + 四指标

| 项 | 内容 |
|---|---|
| 输出文件 | 新增 `benchmark/.../repair_bench.py`、`scripts/repair_bench_run020.py` |
| 关键实现 | 样本：locator 变更/文案变更/时序不足 3 类 × 8；指标：成功率、误修复率、回归通过率、人工接受率 |
| 验收 | `python scripts/repair_bench_run020.py --arm rule --repeats 1` |
| DoD | 指标可算；人工接受率在无人评审时标 `NOT_MEASURED` |
| 规模 | 3 人日 |

### WP4-T6 反例测试

| 项 | 内容 |
|---|---|
| 验收命令 | `pytest -q tests/integration/test_repair_auto_reject_run020.py tests/unit/test_patch_contract.py` |
| DoD | 覆盖：越权改生产资产、删断言、放宽阈值、跨用例、Oracle 版本变更、回滚失败恢复 |

---

## M5：Benchmark 统一（WP5）

### WP5-T1 registry + manifest 守卫

| 项 | 内容 |
|---|---|
| 输出文件 | 新增 `benchmark/.../registry.py`；修改 `dataset.py`（DatasetRef） |
| 关键实现 | `DatasetRef(dataset_id, version, manifest_sha256)`；加载校验；`ExperimentSpec`（suite/arms/repeats/budget/seed）；缺 `rule` 臂 → 拒绝 |
| 验收 | `pytest -q tests/unit/test_benchmark_registry.py`：篡改 manifest/缺 rule 臂/版本缺失 3 类 |
| DoD | 校验在加载前完成，失败即退出（exit 2），不产生半份结果 |
| 规模 | 2 人日 |

### WP5-T2 FailureArchive（只追加）

| 项 | 内容 |
|---|---|
| 输出文件 | 新增 `benchmark/.../archive.py`；迁移 v16 的 `tg_bench_archive` |
| 关键实现 | 同 `(experiment, case, repeat)` 重复写入 → 409；删除接口不存在 |
| 验收 | `pytest -q tests/integration/test_holdout_manifest_guard.py -k archive` |
| DoD | 归档可导出（JSONL），不覆盖历史 |
| 规模 | 1 人日 |

### WP5-T3 迁移 v16 + sample 表

| 输出文件 | `db.py`（v16）、`packages/persistence/.../bench_samples.py` |
| 验收 | SQLite/PG16 双跑；`pytest -q tests/integration/test_bench_samples_persist.py` |
| DoD | 迁移幂等；索引命中（explain 检查） |
| 规模 | 1.5 人日 |

### WP5-T4 report renderer

| 输出文件 | 新增 `benchmark/.../report.py` |
| 关键实现 | 指标表 + CI + 成本/延迟 + 失败样本 + 负结果；模板不写死数字 |
| 验收 | `pytest -q tests/unit/test_report_render.py`（快照测试） |
| DoD | 报告可追溯到 `experiment_id` 与 HEAD |
| 规模 | 1.5 人日 |

### WP5-T5 统一入口脚本

| 输出文件 | 新增 `scripts/run020_benchmark.py` |
| 验收 | `python scripts/run020_benchmark.py --suite compiler --arms rule --repeats 1 --budget-usd 0` |
| DoD | 三个 suite 同一入口；预算超限即停并落 `budget_exhausted` |
| 规模 | 2 人日 |

### WP5-T6 CI 实验 job + 禁删检查

| 项 | 内容 |
|---|---|
| 输出文件 | 修改 `.github/workflows/nightly-gate.yml`（或新增 `benchmark-gate.yml`）、新增 `scripts/ci_holdout_guard.py` |
| 关键实现 | ①`workflow_dispatch` 的实验 job（默认 `--budget-usd 0.5`，无 Secret 直接 BLOCKED）；②PR 检查：holdout 目录只允许新增，不允许修改/删除已有样本（`git diff --diff-filter=DM` → 失败） |
| 验收 | 本地模拟 PR 改动 → 检查失败；正常提交 → 通过 |
| DoD | 该 job **不会**静默通过（无 Secret 时显式 BLOCKED 并写 Job Summary） |
| 规模 | 1.5 人日 |
| 阻塞 | 真实 dispatch 需 Actions 权限（`BLOCKED-EXT`） |

---

## M6：发布准入（沿用 `RUN-020-RELEASE-ADMISSION.md`）

| 任务 | 内容 | 状态 |
|---|---|---|
| REL-1 | 配置仓库 Secret `DEEPSEEK_API_KEY`，重跑 Nightly，`llm-smoke` 产出真实证据 | **BLOCKED-EXT**（需负责人） |
| REL-2 | `workflow_dispatch` Reliability Gate（62/100/3），按 9 条硬条件验收 | **BLOCKED-EXT**（需 Actions 权限） |
| REL-3 | 用 `docs/RUN-020-PR-DESCRIPTION.md` 创建 PR（不自动合并） | **BLOCKED-EXT**（需授权） |

---

## 附录 A：提交与分支约定

- 分支：`workbuddy/run020-intelligence`（从 `564b21c` 切出）；
- 提交前缀：`RUN-020 WP1-T3: ...`；一个任务一个提交；
- 每个任务提交必须含：代码 + 测试 + 文档更新（至少一行 DoD 证据）；
- 禁止：跳过 hook、`--no-verify`、force push、改写历史；
- PR：按 M1/M2/M3/M4/M5 分 5 个 PR（每个 ≤ 400 文件），避免再次出现 2967 文件的巨型 PR。

## 附录 B：验收命令速查

```bash
# 单测（快）
.venv/bin/python -m pytest -q tests/unit -x

# WP1
.venv/bin/python -m pytest -q tests/unit/test_llm_*.py tests/integration/test_llm_audit_persist.py
.venv/bin/python scripts/llm_smoke.py

# WP2
.venv/bin/python -m pytest -q tests/unit/test_compiler_*.py tests/unit/test_page_object_resolver.py
.venv/bin/python -m pytest -q tests/integration/test_nl_compile_flow.py
.venv/bin/python scripts/compiler_holdout_run020.py --arm rule --repeats 1 --budget-usd 0

# WP3
.venv/bin/python -m pytest -q tests/unit/test_multifault_adjudication.py tests/unit/test_abstention_reasons.py
.venv/bin/python -m pytest -q tests/integration/test_multi_root_diagnosis.py
.venv/bin/python scripts/diagnosis_holdout_run020.py --arm rule --repeats 2

# WP4
.venv/bin/python -m pytest -q tests/unit/test_patch_contract.py tests/unit/test_locator_repair.py
.venv/bin/python -m pytest -q tests/integration/test_repair_auto_reject_run020.py
.venv/bin/python scripts/repair_bench_run020.py --arm rule --repeats 1

# WP5
.venv/bin/python -m pytest -q tests/unit/test_benchmark_registry.py tests/unit/test_metrics_multilabel.py
.venv/bin/python -m pytest -q tests/integration/test_holdout_manifest_guard.py
.venv/bin/python scripts/run020_benchmark.py --suite diagnosis --arms rule --repeats 1 --budget-usd 0

# 全量门禁
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check apps packages scripts tests
.venv/bin/python -m ruff format --check apps/api packages scripts tests
```

## 附录 C：本轮（M0）明确不做的事

| 不做 | 原因 |
|---|---|
| 不写业务代码 | 本轮定位是审计 + 设计；代码从 M1 开始，避免"边设计边改" |
| 不调 Prompt | M1 审计/校验落地前调 Prompt，无法归因 |
| 不跑 LLM 实验 | 数据集尚未建立（WP2-T8/WP3-T5/WP4-T5），跑了也没有可比口径 |
| 不改 CI 门禁语义 | 现有三级门禁刚全绿，先加 Job 不改判定规则 |
| 不合并 PR、不碰 main | 未授权 |
