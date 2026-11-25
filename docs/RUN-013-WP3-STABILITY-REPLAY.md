# RUN-013 WP3：稳定性回放到审核的完整闭环

> 日期：2026-11-25（CST）｜ 证据：`docs/assets/run013/wp3/ui/stability-replay-acceptance.json`
> 脚本：`scripts/stability_replay_acceptance_run013.py`（15 passed / 0 failed）
> 门禁测试：`tests/integration/test_stability_gate.py`（12 passed）

---

## 1. 审查结论（逐项）

| 指令 §5.2 要求 | 现状 | 结论 |
|---|---|---|
| 回放使用正确 TestCase 版本 | `version_ref = testcase_id@version`，`replay_problems` 校验 ref 一致 | 已满足 |
| Oracle 版本固定 | 回放记录 `oracle_versions`，审核时 `_pin_oracle_versions` | 已满足 |
| 记录浏览器/环境 | `environment` + `browser_versions` + 每 Run `observer_mode` | 已满足 |
| 样本数量正确 | `samples_expected/recorded`，`repeats` 1..10 | 已满足 |
| 不足样本不能 PASSED | 未完成 Run → 结论 `None`；`validate_stability` 用期望样本数 | 已满足 |
| `NOT_EVALUATED` 不能转通过 | `TERMINAL_VERDICTS` 不含 `NOT_EVALUATED`，门禁拒绝 | 已满足 |
| 过期回放不能用于新版本 | `STABILITY_REPLAY_VERSION_MISMATCH` | 已满足 |
| **Oracle 修改后必须重新核验** | **缺口**：回放记录 `oracle_complete=true`，审核时不比对当前绑定版本 | **本轮修复** |
| 并发审核不覆盖新修订 | `review_revision` 行级 CAS | 已满足 |
| 回放证据可回读 | `/stability-replays/{id}` + `review_history.stability_evidence` | 已满足 |

## 2. 修复：Oracle 版本变更后的回放失效

**问题**：回放以 Oracle v1 跑出 `STABLE_PASS` 后，用户在审核前把断言改绑到 Oracle v2；
旧逻辑只信回放文档里的 `oracle_complete`，会**用 v1 的结论批准 v2 的业务预期**。

**修复**：

1. `CatalogService._replay_oracle_problems()`：审核事务内逐断言比对
   `回放固定版本` vs `本次审核固定版本`，并重新查询 Oracle 行（存在/已审批/同 Workspace）；
2. `_stability_gate()` 与 `_best_replay()` 统一应用；
3. 新增只读预检 `GET /api/v1/testcases/{id}/versions/{v}/stability-gate`，
   与审核事务使用同一规则，供 UI 展示“审核条件是否满足”；
4. 回归测试：
   - 回放后改绑 Oracle v2 → `STABILITY_ORACLE_VERSION_CHANGED`，审核 412；
   - 回放后撤销 Oracle 审批 → `ORACLE_NOT_APPROVED`，审核 412。

## 3. UI 产品化：回放状态面板

TestCase Detail 原先只有「启动稳定性回放」按钮 + Toast，无法看到结果。本轮新增面板：

- 回放状态、样本进度（recorded/expected）；
- 成功 / 失败 / 未完成数量；
- 稳定性结论（STABLE_PASS / DETERMINISTIC_FAILURE / ...）；
- 可晋升标记；
- 关联 Oracle 与版本、Oracle 完整性；
- 浏览器版本、报告 ID、创建/完成时间；
- 审核条件（满足/未满足）与服务端 problems 列表；
- 运行中每 2 秒自动刷新，另有「刷新」按钮。

## 4. 真实浏览器验收（15/15）

环境：独立 API + 2 独立 Worker + 真实 TeamFlow + 真实 Chromium。

| 路径 | 结果 |
|---|---|
| 无回放 → 审核 412 `STABILITY_REPLAY_REQUIRED` | PASS |
| UI 空状态真实展示 | PASS |
| compiler 候选：3 次真实回放 → UI 3/3 + STABLE_PASS | PASS |
| UI 显示通过 3 / 失败 0 / Oracle 版本 | PASS |
| UI 审核条件“满足” | PASS |
| UI 审核通过，`review_history.stability_policy=ENFORCED` | PASS |
| healed 候选：3/3 + STABLE_PASS + UI 审核通过 | PASS |
| 真实故障（TF-UI-002）回放 → `DETERMINISTIC_FAILURE` | PASS |
| 失败回放 → 审核 412 `STABILITY_NOT_STABLE_PASS` | PASS |
| 回放未完成 → 审核 412 `STABILITY_REPLAY_NOT_COMPLETED` | PASS |

## 5. 剩余边界

- 伪造回放报告：回放文档只由服务端 `StabilityReplayService` 写入，无通用 Doc 写 API；
  未单独构造“直接改库伪造”测试（改库不属于客户端可执行路径）。
- 并发审核：已有 CAS 单测/集成覆盖，本轮未重复构造 100 并发；保留原测试证据。
- 回放面板的自动刷新与手动刷新均验证可用；断网时面板显示为空状态，不伪造结论。
