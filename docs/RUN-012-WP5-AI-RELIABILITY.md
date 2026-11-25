# RUN-012 WP5：AI 可靠性升级

**日期**：2026-11-25 20:57 CST
**分支**：`workbuddy/run012-product-completion`

## 1. 审查结论

四类 LLM 调用点逐一核查：

| 组件 | Schema 校验 | 重试 | Fallback 标注 | 追踪字段 | 离线测试 |
|---|---|---|---|---|---|
| Compiler 顾问 | ✅ 步骤/断言白名单 | ✅（`chat_json` 层） | ✅ `fallback` | ✅ | ✅ 已有 |
| Probe 规划顾问 | ✅ 探针/模板/参数白名单 | ✅ | ✅ `fallback` | ✅ called/fallback/error/dropped/selections | ✅ 已有 |
| Repair 顾问 | ✅ 变更路径白名单 | ✅ | ✅ `fallback` | ✅ | ✅ 已有 |
| **Diagnosis 顾问** | ❌ 无 | ❌ 无（异常冒泡） | ❌ 无 | ❌ 无 | ❌ **完全无覆盖** |

**核心问题**：Diagnosis 顾问是唯一「调用了模型但没有可用候选时会被当作成功」的路径——
`advise.suggest()` 返回裸 list，`LLMError` 直接冒泡到服务层被吞成 `skipped`，
既无法区分「模型没给出候选」与「调用失败」，也没有任何可回读的追踪字段。

## 2. 修改内容

### 2.1 `packages/agents/traceguard_agents/gateway/llm_gateway.py`

- 新增 `DiagnosisAdvice` 数据类：`candidates / called / fallback / error / model / dropped / attempts / warnings`，
  与其它三类顾问的返回契约对齐。
- `LLMDiagnosisAdvisor.suggest()` 改为返回 `DiagnosisAdvice`：
  - **Schema 校验**：顶层必须对象；`hypotheses` 必须列表；条目必须对象；`description` 非空；`failure_domain` 在白名单内；
    越界条目直接丢弃并计入 `dropped`；
  - **重试**：`invalid_json` 有限重试 1 次（`DIAGNOSIS_JSON_RETRIES`），鉴权/限流/网络等错误**立即回退不重试**；
    重试由本层控制（向 provider 传 `json_retries=0`），避免与 provider 层重试叠加导致重复计费；
  - **Fallback 标注**：无任何可用候选时 `fallback=True` 且 `error` 明确，
    **绝不把「调用过但无结果」伪装成「有 AI 建议」**；
  - **防虚构**：引用不存在的 `evidence_id` 一律剔除并留痕；
    **完全没有真实证据支撑的假设整条丢弃**（防止凭空猜测污染候选集）；
  - **追踪**：`attempts / dropped / error / model / warnings` 全部可回读。
- 新增常量 `MAX_DIAGNOSIS_HYPOTHESES = 3`、`DIAGNOSIS_JSON_RETRIES = 1`。

### 2.2 `apps/api/traceguard_api/services/diagnosis_service.py`

- 调用层适配新契约，`llm_advisor` 段落新增字段：
  `fallback / error / attempts / dropped / warnings`；`fallbacks` 列表显式记录回退事实。
- 回退时写结构化日志 `llm_advisor_fallback`（含 error_kind / attempts / dropped）。
- **确定性诊断不受影响**：LLM 失败或回退时，规则引擎的假设与结论照常产出。

### 2.3 既有测试同步

`tests/unit/test_llm_gateway.py` 的 3 个诊断顾问用例按新契约更新（结构化返回 + 显式 fallback）。

## 3. 测试

新增两个离线测试文件（FakeProvider，不访问真实模型）：

**`tests/unit/test_llm_diagnosis_advisor_run012.py`（17 例）** —— 顾问层

- Schema：合法假设保留、缺 `hypotheses`、非列表、条目非对象、域越界、描述缺失、置信度越界钳制、非数值置信度归零、超 3 条截断；
- 防虚构：虚构 evidence_id 被剔除、无真实证据的假设整条丢弃；
- 重试与回退：`invalid_json` 重试 1 次后回退、非 JSON 错误不重试、重试后恢复成功、失败必标 fallback、provider 层不重复重试；
- 追踪：`model / attempts` 可回读。

**`tests/unit/test_diagnosis_llm_advisor_run012.py`（5 例）** —— 服务调用层

- LLM 失败 → 诊断文档记录 `fallback=true / fallbacks=[rate_limit]`，**规则诊断结论照常**；
- LLM 候选固定 `status=CANDIDATE`、描述带 `[LLM 候选]` 前缀，**永不 CONFIRMED**；
- LLM 候选只追加、不改写规则假设；
- `use_llm_advisor=False` 时不调用顾问（Benchmark 规则组口径纯净）；
- 未配置凭据时 `llm_advisor=None`，不伪装成调用过。

**结果**：
- WP5 专项：**22 passed**；
- 全量：**760 passed, 16 skipped**（PG 专项需 `TG_TEST_POSTGRES_URL`，WP1 已真实验收）；
- `ruff check` / `ruff format --check` 通过。

## 4. 残留与边界

- 本环境无 LLM 凭据，**真实模型输出路径未在本轮验证**（离线 FakeProvider 覆盖了契约与边界）；
- 重试次数固定为 1（覆盖围栏/截断等常见问题），未做指数退避——provider 层已有 `llm_max_retries` 承担网络级重试；
- `DiagnosisAdvice.warnings` 目前只回传前 10 条，避免诊断文档膨胀。
