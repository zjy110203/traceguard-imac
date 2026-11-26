# RUN-017 WP2：Benchmark 独立观测体系与资格实验

> 时间：2026-11-26 20:20–20:50 CST
> 代码：`benchmark/traceguard_benchmark/{fault_effect,browser_effect}.py`
> 实验入口：`scripts/bench_v2_run017.py`
> 原始样本：`docs/assets/run017/wp2/samples.jsonl`（60 条，逐条落盘）
> 指标：`docs/assets/run017/wp2/metrics-recomputed.json`

---

## 1. 先说结论（可核对的数字）

| 指标 | 结果 |
|---|---|
| 样本 | **60**（30 场景 × 2 轮：28 故障场景 + 2 正常对照 → 56 故障样本 + 4 对照） |
| Fault Activation 覆盖率 | **100%**（56/56 故障样本，来源 `teamflow:/api/faults`） |
| 有效效果样本 | 54（CONFIRMED 45 + NOT_TRIGGERED 9） |
| UNKNOWN | **2**（3.3%），全部是 TF-TIME-003（无独立探针，如实保留） |
| Fault Effect 确认率 | **83.3%**（45/54，Wilson 95% CI 71.3%–91.0%） |
| False Pass（严格口径） | **0** |
| False Negative | **0** |
| 测试失败但业务未受影响 | 5（LOC-001/002 ×2 + FLAKY-002×1，见 §6） |
| 诊断主因准确率（CONFIRMED 口径） | **93.75%**（15/16，CI 71.7%–98.9%），Macro-F1 **0.941** |
| 诊断方向性准确率（含 PROBABLE） | **83.3%**（25/30，CI 66.4%–92.7%），Macro-F1 **0.747** |
| 探针成本 | 单样本中位 **10.2 s**，60 样本合计 **733 s** |

**口径说明**：本轮是**规则组单臂资格实验**，不含 LLM 臂与人工基线，
不与历史 v1 数据混算；UNKNOWN 不计入已确认故障效果。

---

## 2. RUN-016 探针的真实缺陷（本轮修复）

RUN-016 声称"4 类独立探针已建立"，但用真实 TeamFlow 验证后，**四类里三类测错了对象**：

| 缺陷 | 机制 | 真实后果 | 修复 |
|---|---|---|---|
| `api_error` 用空载荷 `POST {}` | FastAPI 在进入路由函数**之前**返回 422，注入的 500/403 永不执行 | API 类故障在真实环境**永远**被判 `NOT_TRIGGERED` | 改用合法载荷（探针自己的数据，随后清理）+ 授权基线 |
| `api_timeout` 只在 `status=0` 时判受影响 | 快速 5xx 被当成"没影响" | 注入 503 被误判为无效果 | 纳入 5xx；用无副作用的 GET 读接口 |
| `persistence` 用 `name in 状态响应文本` | `/api/state/projects` 只返回 `keys`，不含 name | **健康环境也判"未持久化"**，并留下 `r16-probe-*` 数据 | 按 key 精确回读 + 探针专用 Workspace + 自清理 + 清理失败即 UNKNOWN |
| `state_lag` 用响应耗时 | TF-DB-003/004 服务端**不 sleep**，只是返回 `count-1`（值过期） | 滞后类故障永远测不到 | 拆成 `read_latency`（TF-TIME-001/002）与 `state_consistency`（TF-DB-003/004） |
| `TF-API-003/004` 映射到创建接口探针 | 故障作用于**更新**接口 | 假阴性 | 新增 `api_update` 探针 |
| `TF-SEC-002` 映射到创建接口探针 | 故障作用于**列表读取** | 假阴性 | 新增 `read_authz` 探针 |

> 这些缺陷在 mock 单测里全部看不出来（mock 服务不做 Pydantic 校验、不做路由前置校验），
> 只有在真实 TeamFlow 上才会暴露。本轮新增
> `tests/integration/test_fault_effect_teamflow_run017.py`（14 项）专门守这条线。

## 3. 探针清单（真实语义）

| 探针 | 覆盖故障 | 判定规则（摘要） | 副作用 |
|---|---|---|---|
| `api_error` | API-001/002、SEC-001、FLAKY-002、COMP-001 | 5xx→CONFIRMED；403 且基线证明授权操作可用→CONFIRMED；2xx→NOT_TRIGGERED；4xx/连接失败→UNKNOWN | 在探针 Workspace 创建临时项目并清理 |
| `api_update` | API-003/004 | 创建→PUT→回读；PUT 5xx→CONFIRMED；200→NOT_TRIGGERED | 同上 |
| `api_timeout` | NET-001/002 | 5xx→CONFIRMED；超时且基线快→CONFIRMED；快速拒连→UNKNOWN | 无（GET 读接口） |
| `persistence` | DB-001/002/005、FLAKY-001 | 创建→按 key 回读→重名写入；接受但未落库→CONFIRMED；重名未落库→CONFIRMED；慢但落库→NOT_TRIGGERED | 探针 Workspace + 自清理 |
| `read_latency` | TIME-001/002、COMP-002 | 5+5 采样比较 p95（≥1500ms 且 ≥3× 基线 且增量 ≥800ms） | 无 |
| `state_consistency` | DB-003/004 | 写入→立刻读状态→按 key 直读→12s 内重读；立刻读缺失但直读存在→CONFIRMED（STALE_READ） | 探针 Workspace + 自清理 |
| `read_authz` | SEC-002 | 基线 200，故障后 403→CONFIRMED | 无 |
| `browser` | UI-001~004、LOC-001/002、COMP-003 | 见 §4 | 通过真实 UI 创建项目并清理（名称带随机 token） |

所有"需要基线"的探针在缺少基线时一律 `UNKNOWN`（fail-closed）：
在故障已生效的环境里补采基线无法证明因果。

## 4. 浏览器级观测（本轮新增能力）

`BrowserEffectObserver` 用**独立 BrowserContext**执行一次真实用户操作：

```
打开 /projects/new → 按 label 填"项目名称" → 按 role/name 点"创建"
→ 等待跳转/提示（≤8s）→ 读 /api/state/projects 回读业务结果
→ 记录 DOM / ARIA / 截图 / 网络 / Console / 导航
```

隔离硬约束：

- 构造函数**不接受** Run verdict / Ground Truth / 诊断结果 / 模型预测；
- 操作只用 `get_by_label` / `get_by_role(name=...)`，**不复用**被测用例的 test_id；
  test_id 只用于回答"定位器资产是否变化"这个观测问题；
- 业务结果以 TeamFlow 权威状态接口为准，不以 Toast 文案作为成功证据；
- 每次观测创建的数据都带 `bp:<random token>:` 前缀，结束后定向清理。

分类结果（8 次真实故障注入验证，见 `tests/integration/test_browser_effect_run017.py`）：

| 场景 | 页面行为 | 业务回读 | 结论 |
|---|---|---|---|
| TF-UI-001 | 显示"创建失败" | 已落库 | CONFIRMED（用户无法判断结果） |
| TF-UI-002 | 无任何反馈、无请求 | 未落库 | CONFIRMED（静默忽略） |
| TF-UI-003 | 显示失败文案 | 已落库 | CONFIRMED（提示与事实矛盾） |
| TF-UI-004 | 成功提示延迟 3s | 已落库 | NOT_TRIGGERED（时序观察） |
| TF-LOC-001 | test_id 变了、按钮名不变 | 已落库 | **NOT_TRIGGERED（LOCATOR_ONLY_BREAKAGE）** |
| TF-LOC-002 | 输入框 test_id 变了 | 已落库 | NOT_TRIGGERED（同上） |
| TF-COMP-003 | 静默忽略 | 未落库 | CONFIRMED |

`TF-LOC-*` 正是指令 §4.5 要求区分的情形：**测试资产坏了，业务没坏**——
不能判成"业务功能失效"。

## 5. 实验结果（关键切片）

```
样本 60 = 28 故障场景 ×2 + 2 对照 ×2
```

| 切片 | 样本 | UNKNOWN | CONFIRMED | NOT_TRIGGERED | 确认率 | False Pass |
|---|---|---|---|---|---|---|
| 全部 | 60 | 2 | 45 | 9 | 83.3% | 0 |
| 23 场景子集（API/状态级探针） | 46 | 2 | 37 | 3 | 92.5% | 0 |
| 7 场景子集（浏览器观测） | 14 | **0** | 8 | 6 | 57.1% | 0 |

RUN-016 时这 7 个 UI/定位器场景**全部是 UNKNOWN**；本轮 0 UNKNOWN。
7 场景子集确认率低是**正确**的：LOC-001/002、UI-004 本来就不该影响业务。

## 6. False Pass 与"测试失败但业务未受影响"

严格 False Pass 定义（本轮修正）：

```
观测 PASSED + 场景预期 FAILED + 效果 CONFIRMED + 本次 Run 的业务结果确实缺失
```

结果 **0 条**。过程中发现两类容易误算的样本：

1. **TF-DB-003 / UI-004 / API-003 / NET-001 等**：场景设计为"可容忍"，
   预期就是 PASSED，即使探针确认故障存在也**不是** False Pass；
2. **TF-FLAKY-001 r1**：探针 3 次尝试命中"接受但未落库"，但该 Run 自己的请求恰好成功
   （business_delta=+1）→ 测试通过是**正确**的，属采样差异，不是漏报。

另有 5 条"测试失败但业务未受影响"，是**测试资产/概率故障**问题，不是漏报：

| 样本 | 现象 | 解释 |
|---|---|---|
| TF-LOC-001/002 ×2 | 用例因 test_id 变化失败，用户流程正常 | 定位器资产问题（浏览器观测确认业务成功） |
| TF-FLAKY-002 r1 | 概率 500 未在该次触发，断言仍失败 | 期望失败但业务未受影响，属概率采样 |

## 7. 诊断准确性（与 WP7 共用样本）

| 口径 | n | 准确率 | 95% CI | Macro-F1 |
|---|---|---|---|---|
| CONFIRMED 主因 | 16 | **93.75%** | 71.7%–98.9% | 0.941 |
| 方向性（含 PROBABLE 首个 SUPPORTED） | 30 | **83.33%** | 66.4%–92.7% | 0.747 |

错误 5 条（全部有解释，不做样本级硬编码）：

| 样本 | GT | 预测 | 原因 |
|---|---|---|---|
| TF-DB-004 ×2 | TIMING | PERSISTENCE | Run 时签名与 TF-DB-001 完全相同（count=0）；无延迟复测时只能给 PROBABLE 的 PERSISTENCE |
| TF-TIME-003 ×2 | TIMING | LOCATOR | 双提交第二次点击时页面已跳转，证据表现为元素找不到 |
| TF-FLAKY-002 r1 | API | UI_RENDERING | 概率 500 未留下该次请求的 5xx 证据，只剩"成功提示缺失" |

> 这三类都是**证据不足**导致的合理误判，不是规则写错。要提升必须引入
> "延迟复测 / 请求级 5xx 对齐"，属于 WP7 的后续工作，本轮不为了指标硬编码。

## 8. 环境与可复现性

| 项 | 值 |
|---|---|
| TeamFlow | 独立进程 + 独立 SQLite（`run-env/teamflow.db`） |
| TraceGuard API | 独立进程，`TG_INLINE_WORKER=false` |
| Worker | 2 个独立进程 |
| 平台数据库 | SQLite（`run-env/platform.db`） |
| 浏览器 | 真实 Chromium 159（headless） |
| 重置 | 每个样本前清数据 + 清全部故障（含 sticky），单线程串行 |
| 运行时长 | 733.8 s |

复现：

```bash
.venv/bin/python scripts/bench_v2_run017.py --out docs/assets/run017/wp2 --rounds 2 --workers 2
# 只重算指标（不重跑）：
.venv/bin/python scripts/bench_v2_run017.py --recompute docs/assets/run017/wp2/samples.jsonl \
  --out docs/assets/run017/wp2 --diag-db docs/assets/run017/wp2/run-env/platform.db
```

## 9. 限制（必须一起读）

- **单臂**：没有 LLM 臂、没有人工基线，不宣称"规则优于 LLM"，也不宣称统计显著；
- **平台库是 SQLite**：本轮测的是"故障效果观测"而非数据库可靠性（PG16 由 WP4 长稳覆盖）；
- **TF-TIME-003 仍 UNKNOWN**：双提交竞态没有被单次提交的浏览器观测覆盖，
  没有为了凑覆盖率加一个不可信的探针；
- **诊断样本量小**（n=16/30）：CI 很宽，不能外推到"生产准确率"；
- **概率故障**（FLAKY-001/002）单轮结论天然有采样噪声，两轮已能看到差异；
- 探针会真实写入（虽然只在探针 Workspace 且自清理），实验中 `cleanup_ok` 已逐条记录。
