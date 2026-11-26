# RUN-017 WP6：前端产品体验与核心流程验收

> 证据：`docs/assets/run017/wp5/ui/run017-ui-full-loop.json`（58 项全链路）、
> `docs/assets/run017/wp6/ui-matrix.json`（128 格矩阵）、`docs/assets/run017/wp6/ui/**`（截图）
> 脚本：`scripts/business_loop_ui_acceptance_run017.py`、`scripts/ui_matrix_run017.py`

---

## 1. 核心业务流程回归（真实 UI 点击 + 独立 Worker）

环境：独立 API（`TG_INLINE_WORKER=false`）+ 2 个独立 Worker 进程 + 真实 Chromium +
真实 TeamFlow + 真实 PostgreSQL/SQLite 资产。

**结果：58 PASS / 0 FAIL**（本地一次跑完，耗时约 1 分 45 秒）。

覆盖链路（UI 点击 → 服务端回读）：

| 段 | 关键检查 |
|---|---|
| ①–⑬ | Intent → 用例 → Oracle 审批/绑定 → 审核 → Run → 失败注入 → 诊断 → 证据图（35 节点）→ 探针建议（3 条可执行） |
| ⑭–⑯ | 未配置 LLM 凭据时**如实提示**"修复规划器已禁用"，不伪造成功 |
| ⑰–㉒ | 新建修复提案 → VALIDATED → 沙箱真实执行 PASSED → 提交审批 → 审批 → 晋升（v2）→ **真实回归 Run（PASSED）** |
| ㉓ | 回滚 → ROLLED_BACK 且留痕 |
| W1-①–㉒ | Worker 启动失败（DB 不可达）→ 非零退出且不消费任务；SIGTERM 正常退出；SIGKILL 后 API 不假报成功；租约到期后接管（fencing 递增）；旧 Worker 恢复后**不覆盖终态/不追加证据**；API 被杀后重启可回读终态；双 Worker 并发 4 个 Run 全部到终态且无重复步骤 |

> `⑭` 之后的"诊断 ID"一项记为 NOTE（仅打印 ID，不构成断言），与 RUN-016 口径一致。

## 2. 全视图 × 断点 × 主题矩阵（128 格）

- 视图：16 个路由（Dashboard / Intents / Explorer / TestCases / TestCase 详情 /
  Oracles / Runs / Run 详情 / Diagnoses / Diagnosis 详情 / Repairs / Benchmark /
  Models / System / Users / Login）；
- 断点：1440 / 1024 / 768 / 390；
- 主题：light / dark（通过 `localStorage['tg-theme']` + `data-theme` 真实切换）；
- 数据：矩阵运行前先真实执行一次 Run 与一次故障 Run + 诊断，
  因此列表与详情页都有**真实数据**（不是空壳）。

**结果：128 / 128 PASS**（无横向溢出、无控件越界、无 Console 错误）。

判定口径（两轮收紧后定稿，避免误报）：

1. 页面级横向溢出：`documentElement.scrollWidth - innerWidth > 2` → FAIL；
2. 控件越界：元素右边界超出视口 2px 以上，**且不在** `overflow-x:auto/scroll`
   的可滚动容器内 → FAIL（表格放在可横向滚动容器里是合理响应式做法）；
3. SVG 内部图元（`rect` 等）由 `<svg>` 自身裁剪，几何超出不算溢出——只看
   `<svg>` 元素自身的右边界；
4. Console `error` 记录但不作为独立 FAIL 项（本轮 0 条 error）。

> 第一版判定把"表格在滚动容器里"和"SVG 图元几何"误判为 28 处失败；
> 收紧口径后复测两次均为 128/128，结论稳定。

## 3. 本轮前端改动

### 3.1 Regression 重试入口（WP3）

Repair 详情页新增：Attempt 编号 / 上限 / 剩余次数、重试资格与阻塞原因、
父子 Run 血缘（可点击回看）、Attempt 历史表（状态 / 原因码 / 授权人 / 时间）、
"重新验证（人工授权 · 新 Attempt）"按钮（二次确认 + 填写说明）。

**修复了一个真实缺陷**：重试资格是服务端**计算字段**，只出现在
`GET /repairs/{id}/regression`；前端原先只加载 Repair 文档，
因此 `regression_retry_allowed` 永远为 undefined → 入口永远不出现。
现在选中提案时会合并回归状态接口的返回（失败时静默降级，不伪造可重试）。

### 3.2 体验一致性

- 回归卡片把"Attempt / 结论 / 证据 / Oracle / 失败原因 / 时效"分层展示，
  明确区分**首次验证**与**人工授权重试**；
- 重试按钮只在服务端允许时出现，失败原因（Oracle 缺失 / 版本漂移 / 次数用尽 /
  STALE）以服务端给出的 `regression_retry_block_reason` 原文展示；
- 未改动其它视图的交互（不为了美观重写前端）。

## 4. 尚未覆盖

- **暗色主题的对比度专项**（axe / 色彩对比度自动检查）未纳入本轮矩阵；
- **键盘可达性与屏幕阅读器**未做自动化验收；
- 真实浏览器矩阵在 CI 中由独立 `ui-acceptance` job 承载（手动触发 Reliability Gate
  之外，每日 Nightly 跑一次业务全链路；矩阵脚本本轮只在本地执行）；
- 移动端（390px）只验证布局不溢出，未验证触控交互。
