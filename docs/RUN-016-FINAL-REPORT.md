# RUN-016 最终报告

> 仓库：`traceguard-imac`｜分支：`workbuddy/run016-finalization`
> 起点：`73e4fe689cdd950334b000cfe45961c9c7adc383`（RUN-015 交付）
> 完成时间：2026-11-26（CST）
> 原则：只写实际发生的事；未跑的不写 PASS；被阻塞的保持 BLOCKED 并给出依赖。

---

## 0. 一句话结论

核心自动化测试、诊断、证据追溯、修复、审批、晋升、回归、回滚的闭环在 RUN-015 已经打通；
本轮把它推进到**可复现、可审计、可回收**的状态：修了 6 个真实缺陷（长稳口径、Artifact 无守卫、
Promotion 无恢复入口、Benchmark 效果靠 Verdict 推断、GT 路径可达、CI 证据可被掩盖），
并用真实 PG16 长稳、10 进程并发、58 项 UI E2E 提供了证据。
**没有宣称 False Pass 已解决**——测量口径已修正，但 23×2 新实验未启动（原因见 §8）。

---

## 1. 项目架构（当前实际）

```
apps/
  api/         FastAPI 组合根（Runtime）＋ 6 个路由域（system/auth/catalog/execution/evidence/intelligence/benchmark）
  workers/     独立 Worker 进程（claim → execute → submit，租约 + fencing）
  teamflow/    真实被测目标（含 27 类故障注入 + 独立状态查询 + Ground Truth）
  web/         Vue3 + Vite 工作台（15 个视图）
packages/
  core/        配置、错误、日志、安全（TargetPolicy / 出站代理 / 配额）
  contracts/   Pydantic 契约模型（22 个 Schema + 防漂移）
  execution/   RunPipeline（浏览器观察器、步骤、证据、oracle 判定）、TaskService（租约/提交）
  evidence/    Event Store、Evidence Manager、诊断引擎、探针、图谱、修复规则
  agents/      LLM 网关、编译器、顾问、healing 状态机
  persistence/ SQLAlchemy 模型 + 仓储（SQLite / PostgreSQL 双支持）
benchmark/     场景定义、评测、故障效果独立观测（本轮新增）
scripts/       长稳、并发、UI 验收、Benchmark、GC 等可复现入口
```

部署形态：单 API + N 独立 Worker + 独立 TeamFlow + PostgreSQL 16（生产形态）；
单机模式可开 `TG_INLINE_WORKER=true`（本轮验收一律关闭）。

## 2. 已实现的核心功能

| 能力 | 状态 | 说明 |
|---|---|---|
| 需求 → 用例编译（规则 + LLM） | VERIFIED | UI E2E ①-②、编译契约测试 |
| Oracle 绑定/审批/版本固定 | VERIFIED | UI E2E ③-⑥；缺 Oracle 不放行 |
| 真实浏览器执行（Playwright） | VERIFIED | 长稳 117+ Run 真实 Chromium |
| 租约 + Fencing + 终态冻结 | VERIFIED | PG16 专项 + 长稳故障注入 |
| Event / Evidence / Artifact 血缘 | VERIFIED | UI E2E ⑩；本轮新增产物守卫与巡检 |
| 诊断（规则 + 可选 LLM） | VERIFIED | UI E2E ⑬-⑭；LLM 未配凭据时如实提示 |
| Evidence Graph | VERIFIED | UI E2E ⑭a（35 节点渲染） |
| Active Probe | VERIFIED | UI E2E ⑭b（3 条建议可执行） |
| 修复提案 / 沙箱 / 审批 / 晋升 | VERIFIED | UI E2E ⑮-㉒ |
| 回归验证（独立 Run） | VERIFIED | UI E2E ㉒a；PG16 10 进程并发 13/13 |
| 回滚 | VERIFIED | UI E2E ㉓ |
| Benchmark 故障注入 + 诊断评测 | PARTIAL | 效果观测已独立化；23×2 新实验 NOT_RUN |
| SSE 实时事件 + 撤权 | VERIFIED | 上限 5s；恶意间隔撤权测试 17 项 |
| 权限（RBAC + Workspace 隔离） | VERIFIED | `/system/config` 脱敏 + 10 项专项 |

## 3. 各功能实际完成程度

- **IMPLEMENTED 且 VERIFIED**：上表标 VERIFIED 的 14 项，均有本轮或历史真实证据，
  且本轮做了回归（895 passed）。
- **PARTIAL**：Benchmark（测量链已修，实验样本仍为历史 v1 口径）。
- **BLOCKED**：B-17 Worker 补充故障注入、宿主级网络隔离、外部真实站点、人工诊断基线、
  LLM 四臂计费实验、LLM Smoke 真实调用。
- **NOT_RUN**：23×2 新 Benchmark 实验、GitHub Actions 三级门禁、Release Gate、多 API 实例压测。
- **NEEDS_DECISION**：INCONCLUSIVE 重试策略、LICENSE、PR #1/#2 合并。

## 4. 核心业务闭环是否打通

**是**。本轮 UI E2E（独立 API + 2 独立 Worker + 真实 Chromium + 真实 TeamFlow）：
需求 → 用例 → Oracle → 审核 → 执行 → 步骤/证据 → 故障 → 失败 → 诊断 → 证据图 → 探针建议 →
修复提案 → 沙箱 → 审批 → 晋升 → 回归 → 回滚，**58 PASS / 0 FAIL**，15 张截图。

## 5. PostgreSQL 16 验证结果

- 版本：PostgreSQL 16.16（本机 55432，用户 `traceguard`；环境重建过程见 `RUN-016-BASELINE.md`）。
- PG 专项测试：`test_postgres_platform.py` + `test_postgres_workspace_run012.py` → 11 passed（此前因无 PG 被 skip）。
- Fencing PG 专项：4 项（事务原子性、重启/接管语义、伪造 owner、过期写证据）全通过。
- 长稳：见 §6（严格墙钟 ≥60 分钟，真实 PG16）。
- Regression 多进程：10 个 OS 进程 + PG16，13/13 判定通过。

## 6. 长稳结果（WP1-A）

## 6. 长稳结果（WP1-A）

| 项 | 实测 |
|---|---|
| 数据库 | PostgreSQL 16.16（`traceguard_run016_soak`） |
| 拓扑 | 独立 API（inline worker=false）+ 3 独立 Worker + 独立 TeamFlow + 真实 Chromium |
| 开始 / 结束 | 2026-11-26 18:12:34 / 19:14:42（CST） |
| **真实墙钟** | **62.14 分钟**（要求 ≥60，无容差） |
| Run | **113**（全部 COMPLETED / PASSED） |
| 数据库 Run 总数 | 181（含故障注入额外 Run） |
| 检查 | **31 PASS / 0 FAIL / 1 NOTE** |
| 数据库一致性 | 重复步骤/孤儿步骤/孤儿证据/残留租约/重复回执/缺证据/重复事件序号 全为 0 |
| 产物一致性 | 729 文件 ↔ 729 登记，哈希全匹配，孤儿 0 |

故障场景：SIGTERM 重启、**两次 SIGKILL 接管（本轮首次真正触发）**、API 重启、
PG 连接中断恢复、SSE 断连续传、全部 Worker 重启恢复 → 7 项 PASS。
SIGSTOP 暂停场景因脚本未重新拉起 victim 而 NOTE（**不计 PASS**），
但暂停语义由 UI E2E 的真实 SIGSTOP + 接管 + 旧 Worker 不可覆盖（W1-⑫~⑯）独立验证。

第一次长稳因"故障探测改为并发"与用例 `RESET_DATA` 冲突被中止（54 个假 FAILED），
证据保留在 `soak-aborted-concurrent-probe.json`，属脚本缺陷而非产品缺陷。


## 7. Worker 并发与 Fencing 可靠性

- 全链路逐条复核（claim / renew / mark_running / step / evidence / event / submit / FINALIZING / 终态冻结），
  结论：守卫在**同一事务**内，PG 上用"行锁 + 锁后时钟"，旧 Worker 无法在租约过期/被接管后写入。
- 新增 **AST 审计测试**：`RunPipeline._emit` 每处调用必须显式传 `fence=`，
  未来新增事件写入若漏传会直接失败。
- 本轮修复的唯一写入缺口：`ArtifactStore.put()` 原先不受 Fencing 约束（且非原子落盘），
  已加 `fence/run_id` + 原子写 + 失败回滚。
- 语义边界（已固化为测试）：**进程重启（同 token、租约未过期）允许继续写；
  被接管（token 递增）后旧 token 永久失效。**
- 长稳故障注入覆盖：Worker 正常退出/重启、SIGKILL 接管、SIGSTOP 暂停接管、
  API 强杀重启、DB 连接中断恢复、SSE 断连续传、全部 Worker 重启恢复。

## 8. Regression 的可靠性与恢复能力

- 并发唯一性：**10 个独立操作系统进程**（PID 89856–89865）+ 独立 Runtime/连接 + 同步屏障，
  共同请求同一个 PROMOTED 提案 → 唯一 Claim / 唯一 Run / 唯一 History / 唯一回执，13/13 PASS。
- 恢复：新 Runtime（等价重启）恢复到原 Run；重复请求不新建；失败请求不破坏赢家状态。
- Promotion 失败恢复：新增 `promotion_recovery_plan()` + `promote_recover()`，
  先判断"是否已实际晋升"，再校验 Oracle/人工审批/版本漂移，人工 confirm 后以新幂等键重试，
  且不重复创建版本（7 项测试通过）。
- INCONCLUSIVE 重试：**只出决策稿**（`RUN-016-REGRESSION-RETRY-DECISION.md`），
  服务端仍 `retry_allowed=false` / `NEEDS_DECISION`。

## 9. Benchmark 的可信程度

- 修复前：`fault_effect_status = CONFIRMED if activation and verdict in {FAILED,...}` → 循环论证。
- 修复后：效果来自**独立业务探针**（API 错误/超时、持久化落库、状态查询耗时），
  纯 UI/定位器类故障一律 `UNKNOWN`；v2 样本 False Pass 必须有独立证据，否则
  `FALSE_PASS_UNVERIFIED`（不计入分子/分母）。
- GT 隔离：执行链路访问 `/api/ground-truth` 被 `TargetPolicy` 阻断（`GROUND_TRUTH_PATH_BLOCKED`）。
- **诚实结论：False Pass 测量口径已可信，但样本量未更新**——23×2 未跑（LLM 臂 BLOCKED、
  需要独占窗口），因此不宣称 False Pass 已解决。

## 10. 前端功能完成情况

- 15 个视图全部可访问；核心 13 条流程在真实浏览器中逐条验证（58 PASS / 0 FAIL）。
- 修复的真实产品问题：`/system/config` 对普通用户暴露宿主路径/数据库方言/Worker 标识（已脱敏）。
- `vue-tsc --noEmit` 通过；`npm run build` 通过。
- 未覆盖：多浏览器/多分辨率；INCONCLUSIVE 重试入口（依赖决策）。

## 11. CI 与全量测试

- 全量：**895 passed / 0 failed / 0 skipped**（`TG_TEST_POSTGRES_URL` 指向真实 PG16，原始日志 `docs/assets/run016/wp5/full-pytest.log`）。
- Ruff：`packages apps scripts tests benchmark` 全绿。
- 前端：typecheck + build 通过。
- GitHub Actions：**NOT_RUN**（本机无 `gh`、无 token；未读取任何凭据）。
- CI 口径修复：Nightly 汇总拆列（Job 结果 / 真实证据），Release Gate 强制
  `llm-smoke-status=PASSED` 且 `real_llm_executed=true`，BLOCKED 不得放行。

## 12. 尚未解决的缺陷

| # | 问题 | 影响 | 状态 |
|---|---|---|---|
| 1 | 纯 UI/定位器类故障无法独立确认业务影响 | 这些场景在新口径下只能是 UNKNOWN，False Pass 无法统计 | 已知限制（需浏览器级观测，未实现） |
| 2 | `fenced_session` 不可重入（同 Run 自锁） | 误用会挂死线程 | 技术债（已在文档标注） |
| 3 | Artifact S3 后端无孤儿回收 | 仅本地后端可治理 | 未实现 |
| 4 | `scan()` 需全量扫 Evidence doc | 超大库巡检慢 | 已知性能限制 |
| 5 | 长稳用例使用 `RESET_DATA`，Run 必须串行 | 并发跑会产生假失败（已实证） | 脚本约束（已注释） |

## 13. BLOCKED 项目与外部依赖

| 项目 | 状态 | 需要什么才能推进 |
|---|---|---|
| B-17 Worker 补充故障注入 | BLOCKED | 安全负责人批准（方案已备） |
| 宿主级网络隔离 | BLOCKED | 宿主机安全策略授权 |
| 外部真实站点 | BLOCKED | 授权目标清单 |
| 人工诊断基线 | BLOCKED | 真实人工标注人力 |
| LLM 四臂计费实验 | BLOCKED | 明确授权使用凭据与预算 |
| LLM Smoke 真实执行 | BLOCKED | CI Secret 配置（本轮不读取凭据） |
| GitHub Actions 三级门禁 | NOT_RUN | `gh` 或 token + 推送权限 |
| Release Gate | NOT_RUN | 发布授权 |

## 14. 需要负责人决策的事项

1. INCONCLUSIVE 回归重试是否采纳"人工授权的新 Attempt"（决策稿已提交）。
2. LICENSE 选择。
3. PR #1 / #2 的合并时机（本轮未合并、未关闭）。
4. 23×2 Benchmark 新实验是否在专用窗口启动（需要 LLM 预算与人工基线）。
5. 是否为纯 UI 类故障投入浏览器级观测（录屏/DOM 差异）以提升 False Pass 覆盖率。

## 15. 下一阶段是否还有必要继续开发

**有必要，但重心应从"补功能"转为"补证据与收口"**：

- 必做：23×2 新口径实验（否则 Benchmark 仍是 PARTIAL）、B-17 批准后的故障矩阵、
  CI 真实运行（把本地门禁搬到 Actions）。
- 可选：浏览器级故障观测、Artifact S3 回收、`fenced_session` 重入防御。
- 不建议：继续扩大功能面；当前产品闭环已完整，继续加功能的边际收益低于把证据补齐。

---

## 附：证据索引

| 内容 | 路径 |
|---|---|
| 基线核对（12 问题逐项） | `docs/RUN-016-BASELINE.md` |
| 长稳 | `docs/RUN-016-WP1-SOAK.md`、`docs/assets/run016/wp1/soak/` |
| Fencing | `docs/RUN-016-WP1-FENCING.md`、`docs/assets/run016/wp1/fencing-tests.log` |
| Artifact | `docs/RUN-016-WP1-ARTIFACT.md`、`artifact-scan.json`、`artifact-tests.log` |
| Regression / Promotion | `docs/RUN-016-WP2-REGRESSION.md`、`docs/assets/run016/wp2/` |
| INCONCLUSIVE 决策稿 | `docs/RUN-016-REGRESSION-RETRY-DECISION.md` |
| Benchmark | `docs/RUN-016-WP3-BENCHMARK.md`、`tests/unit/test_benchmark_*_run016.py` |
| 权限 / SSE / 前端 | `docs/RUN-016-WP4-PRODUCT.md`、`docs/assets/run016/wp4/ui/` |
| CI | `docs/RUN-016-WP5-CI.md`、`docs/assets/run016/wp5/full-pytest.log` |
| 工作日志 | `docs/RUN-016-WORKLOG.md` |
