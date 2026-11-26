# RUN-014 WP4：单一业务资产全血缘 UI 验收

> 环境：真实 API（认证 + CSRF，`TG_INLINE_WORKER=false`）+ 2 个独立 Worker 进程 +
> 真实 TeamFlow（独立库）+ 真实 Chromium（Playwright）＋ SQLite
> 结果：**40 条记录 = 40 PASS / 0 FAIL**（`docs/assets/run014/wp4/full-loop.json`，13 张截图）
> 脚本：`scripts/full_loop_run014.py`（复用 RUN-012/013 的真实进程编排与故障注入）

---

## 1. 与 RUN-013 的差异

RUN-013 WP1 的 56 条记录是"Intent → 手工建用例 → …"，Explorer/Compiler 单独验证，
Regression 不存在（晋升后直接回滚）。本轮用**同一组业务资产**串完整链路，
并把 Regression 与"回归失败→人工回滚"纳入同一条链。

## 2. 链路与证据（全部服务端回读）

| # | 阶段 | 完成方式 | 关键回读 | 结果 |
|---|---|---|---|---|
| ① | Intent | 真实 UI 点击创建 | `/intents` 匹配 1 条 | PASS |
| ② | Explorer | `POST /intents/{id}/explore`（真实浏览器 + 工具网关） | 3 个动作、stop=目标完成 | PASS |
| ③ | Exploration Trace | 网关审计 | 每动作 `allowed=true` | PASS |
| ④ | Trace 回读 | `GET /explorations/{id}` | `intent_ref` = 同一 Intent | PASS |
| ⑤ | Explorer 页面 | UI | 页面渲染出该 exploration | PASS |
| ⑥ | Compiler | `POST /explorations/{id}/compile` | PENDING 候选、`compiler-v1`、`rule_only` | PASS |
| ⑥a | 编译告警 | 服务端 | `NO_BUSINESS_STATE_API` 如实保留 | PASS |
| ⑦ | Candidate 断言 | `GET versions/1` | 1 条断言、intent_ref 一致 | PASS |
| ⑧ | Oracle + Binding | API（真实门禁） | 全断言绑定已审批 Oracle | PASS |
| ⑨ | 回放门禁负向 | `POST review` 期望 412 | `STABILITY_REPLAY_GATE_FAILED` | PASS |
| ⑩ | Stability Replay | 真实 3 轮独立 Run | status=COMPLETED / conclusion=STABLE_PASS | PASS |
| ⑪ | 回放结论 | 服务端 | records=3、promotable=true | PASS |
| ⑫ | Human Review | UI 点击"审核通过" | review_status=APPROVED | PASS |
| ⑬ | Oracle 固定 | `GET versions/1` | `oracle_version=1` 已固定 | PASS |
| ⑬ | Run（正向） | UI 执行 + 独立 Worker | COMPLETED/PASSED、observer=playwright、owner=r14-w1、fencing=1 | PASS |
| ⑭ | 步骤 + 证据 | 服务端 | steps=4、evidence=15 | PASS |
| ⑮ | 故障 Run | 注入 TF-UI-002 | verdict=FAILED | PASS |
| ⑯ | Diagnosis | UI 点击"诊断" | diagnosis 挂在同一 Run | PASS |
| ⑰a | Active Probe | `POST /probes/execute`（http_state） | SUCCEEDED、1 条证据、execution_id 可回读 | PASS |
| ⑰b | 证据回灌 | `GET /diagnoses/{id}` | hypotheses=1、evidence_refs=3 | PASS |
| ⑰ | Probe Plan | `POST /runs/{id}/probe-plan` | 真实计划；诊断已 CONFIRMED → `STOP_CONFIRMED` | PASS |
| ⑲ | Repair Proposal | 带 diagnosis_id + source_run_id | VALIDATED，血缘一致 | PASS |
| ⑳ | Sandbox | UI 点击"沙箱真实执行" | PASSED（真实 Run） | PASS |
| ㉑ | 提交审批 | UI | PENDING_APPROVAL | PASS |
| ㉒ | 人工审批 | UI（qa-lead） | APPROVED | PASS |
| ㉓ | Promotion | UI（admin） | PROMOTED、v2、`check=CAS_OK` | PASS |
| ㉔ | 晋升≠回归 | 服务端 | regression_status=NOT_STARTED | PASS |
| ㉕ | Regression | UI 点击"运行回归验证" | PASSED、verdict=PASSED、版本 `tcase@2` | PASS |
| ㉖ | 回归证据 | 服务端 | evidence=15、run 可回读 | PASS |
| ㉗ | 生命周期 UI | UI 文本 | 七段全部来自服务端状态 | PASS |
| ㉘ | Rollback | UI（admin） | ROLLED_BACK、restored=v3、regression_stale=true | PASS |
| ㉙ | 负向：沙箱 | API | 提案 #2 沙箱 PASSED | PASS |
| ㉚ | 负向：Regression | 晋升后注入真实故障 | FAILED、`REGRESSION_ASSERTION_FAILED` | PASS |
| ㉛ | 负向：人工回滚 | API（admin） | ROLLED_BACK、restored=v5、证据保留 | PASS |
| ㉜ | 负向：门禁回读 | `GET stability-gate` | problems 可回读（未审批 Oracle 不入门禁） | PASS |

### 血缘 ID（同一组资产，机器可读）

```json
{
  "intent_id": "intent_1795663387669aabf9e9281dc",
  "exploration_id": "exploration_1795663389653c3451d80abb4",
  "testcase_id": "tcase_1795663392051d7f31c519be4",
  "testcase_version": 1,
  "oracle_id": "oracle_r14_cand_a_create_ui",
  "replay_id": "replay_179566339230391339c40febb",
  "run_id": "run_17956634004514cac77a39a0e",
  "diagnosis_id": "diagnosis_17956634057499a9f0535a7b4",
  "probe_execution_id": "probeexec_1795663409342808def933d45",
  "repair_proposal_id": "repair_1795663409359ab334a042960",
  "sandbox_run_id": "run_179566341073939be8f4f31f7",
  "promotion_receipt": {"target_version": 2, "check": "CAS_OK"},
  "regression_run_id": "run_17956634257778b712b49f105",
  "rollback_receipt": {"restored_version": 3, "operator": "user:r14-admin"},
  "regression_run_id_failed": "run_1795663436166bdb9e47893cd"
}
```

## 3. 负向场景

| 场景 | 真实结果 |
|---|---|
| 无回放直接审核 | 412 `STABILITY_REPLAY_GATE_FAILED`（未绕过） |
| 真实故障 → Run | `verdict=FAILED`（保留失败状态） |
| 晋升后注入故障 → Regression | `FAILED`（`REGRESSION_ASSERTION_FAILED`），不标记通过 |
| 回归失败 → 人工回滚 | `ROLLED_BACK`，restored=v5，证据保留 |
| 未审批 Oracle | 绑定/门禁用真实问题列表拒绝 |
| Probe 预算已关闭后继续探测 | `BLOCKED`（真实拒绝，不伪造成功） |

## 4. 本轮发现并修复的真实缺陷

| # | 缺陷 | 影响 | 修复 |
|---|---|---|---|
| D7 | `_step_index()` 用 `"step_index" in change` 判断，HTTP 的 `model_dump()` 总带 `step_index=None` → `int(None)` | 任何 `run_config.*` / `test_data_refs` 修复提案经 HTTP 创建时 **500** | 改为 `change.get("step_index") is not None`，并补回归测试 |
| D8 | 前端生命周期把 Promotion/Regression 混为一个条件（WP1 已修） | 回归未执行也显示完成 | WP1 |
| D9 | `regression_run` 未记录生产 Run 的版本关联（WP1 已实现） | 无法回读"回归测的是哪个版本" | WP1 |

## 5. 诚实边界

1. Explore / Compile / Stability Replay / Probe Plan / Oracle 创建与绑定 / 负向链路通过**真实 API**
   调用（服务端没有对应 UI 控件）；其余人类决策点（Intent、审核、执行、诊断、沙箱、审批、
   晋升、回归、回滚）通过**真实 UI 点击**完成。
2. 探索轨迹的 `browser_version` 字段标为 `http-observer`（标签口径问题），但动作序列
   （navigate/fill/click by test_id + 落地 URL 复核）只能由真实浏览器产生；本报告不据此宣称
   "HTTP 降级"，也不掩盖标签不一致。
3. 用例候选由规则编译器生成（`llm_meta.mode=rule_only`，未配置 LLM 凭据），
   编译告警如实保留"只能验证 UI 行为，不能宣称持久化已验证"。
4. 负向"未审批 Oracle"用例只验证门禁可回读问题列表；未构造"审核中途 Oracle 被撤销"的时序，
   该场景由 RUN-013 WP3 的 12 条门禁测试覆盖。

## 6. 复现

```bash
.venv/bin/python scripts/full_loop_run014.py
# 产出：docs/assets/run014/wp4/full-loop.json + 13 张截图 + _runtime 数据库
```
