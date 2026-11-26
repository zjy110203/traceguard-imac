# RUN-018 基线核查与状态修订

> 时间：2026-11-27 00:40 CST
> 分支：`workbuddy/run018-diagnostic-closure`（起点 `c807284`）

---

## 1. 基线确认

| 项 | 值 |
|---|---|
| 仓库 | `zjy110203/traceguard-imac` |
| 起点 HEAD | `c807284f4e12e83453f32b22657814555990deff` |
| 分支 | `workbuddy/run018-diagnostic-closure` |
| 上游分支 | `workbuddy/run017-product-maturity` |
| main | `ac2876e`（不修改） |

## 2. CI 状态核查（2026-11-27）

### PR Gate

| Run | head_sha | 结果 | 说明 |
|---|---|---|---|
| **#81** | `c807284` | **success** (2m28s) | RUN-017 最终 HEAD |
| #80 | `feae308` | success (2m43s) | |
| #79 | `f34f6b4` | cancelled (30s) | 被后续 push 取消 |
| #78 | `4c2f851` | success (2m20s) | |
| #77 | `2d35998` | success (2m29s) | |
| #76 | `1315a2f` | success (2m24s) | |
| #75 | `1faebec` | success (2m22s) | |
| #74 | `8a0fe48` | failed (2m15s) | 旧契约断言，已修 |

### Nightly Gate

| Run | head_sha | 结果 | 说明 |
|---|---|---|---|
| **#33** | `c807284` | **failed** (22m11s) | ui-acceptance 失败 |
| #32 | `feae308` | failed (23m38s) | ui-acceptance 失败 |
| #31 | `f34f6b4` | failed (11m54s) | ui-acceptance 失败 |
| #30 | `4c2f851` | failed (11m48s) | ui-acceptance 失败 |
| #29 | `2d35998` | failed (11m52s) | ui-acceptance 失败 |
| #28 | `1315a2f` | failed (33m48s) | ui-acceptance 失败 |
| #27 | `1faebec` | failed (39m33s) | job 超时 |
| #26 | `8a0fe48` | failed (4m57s) | 旧契约断言 |

### 关键结论

1. **PR Gate #81 @ `c807284` = success**：最终 HEAD 的快速门禁全绿
2. **Nightly #33 @ `c807284` = failed**：同一 HEAD 的 Nightly 仍失败（ui-acceptance）
3. **B-28 未闭环**：Nightly ui-acceptance 从 #26~#33 全部失败，本地 35/35 通过
4. **不能把旧 HEAD 的成功结果当成新 HEAD 的成功结果**

## 3. 统计口径修订

### 3.1 Benchmark 口径

| 口径 | 样本数 | 说明 |
|---|---|---|
| 单次 Benchmark | 60 样本 | 30 场景 × 2 轮 |
| 两次独立实验 | 120 样本 | 独立执行两次 |

### 3.2 诊断准确率

| 指标 | 值 | 分母 | 口径 |
|---|---|---|---|
| 诊断主因准确率 | 90.6% | n=32 | **合并口径**（两次实验的诊断样本合并） |
| 诊断主因准确率 | 93.75% | n=16 | **单次口径**（一次实验的诊断样本） |
| Fault Effect 确认率 | 84.3% | n=120 | 两次独立实验合计 |
| False Pass | 0 | n=120 | 严格口径 |

### 3.3 关键区分

- **CONFIRMED share**（确认率）：被独立观测确认为受影响的样本比例
- **Precision / Recall / Accuracy**：需要 Ground Truth 对照的分类指标
- 两者不可混淆

## 4. 已知问题清单（RUN-018 需修复）

| 编号 | 问题 | 工作包 |
|---|---|---|
| P-01 | RUN-017 FINAL-REPORT 未正确记录 PR Gate #81 | WP0 |
| P-02 | WP1-CI 文档缺 Nightly #32/#33 | WP0 |
| P-03 | current-task/resume 引用旧 Nightly #30 | WP0 |
| P-04 | FEATURE-STATUS Benchmark 摘要口径不一致 | WP0 |
| P-05 | PR3 描述写 15 视图，实际 16 路由 | WP0 |
| P-06 | WP1 标 DONE 但 B-28 未闭环 | WP0 |
| P-07 | BrowserEffectObserver `_persisted()` 不检查 HTTP 状态 | WP2 |
| P-08 | state_consistency 忽略 HTTP 状态 | WP2 |
| P-09 | probe-cleanup SQL LIKE 通配符注入 | WP3 |
| P-10 | Artifact GC 并发竞态 | WP4 |
| P-11 | Active Probe 未进入诊断反馈闭环 | WP5 |
| P-12 | TF-TIME-003 无独立观测 | WP6 |
| P-13 | 诊断无独立保留集 | WP7 |
| P-14 | 缺运行监控与告警 | WP8 |
| P-15 | UI 无障碍验收不完整 | WP9 |
| P-16 | 运维能力待完善 | WP10 |

## 5. 历史分支

全部保留，不删除：
- `workbuddy/run016-finalization`
- `workbuddy/run015-execution-consistency`
- ...（共 12 个历史分支）

## 6. 不做的事

- 不修改 main
- 不删除历史分支
- 不覆盖未提交修改
- 不把 PARTIAL 改成 VERIFIED
