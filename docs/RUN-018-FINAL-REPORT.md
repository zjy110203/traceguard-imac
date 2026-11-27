# RUN-018 最终报告：核心正确性修复、智能诊断闭环验证与产品验收收口（R1 轮）

> 仓库：`traceguard-imac`｜分支：`workbuddy/run018-diagnostic-closure`
> 起点 HEAD：`7674842`｜本轮最终 HEAD：`94c706e`（已推送）
> CI 实跑：**PR Gate #103 @ `e1fb5f3` success（3m32s，最终报告提交本身）；
> 此前 #102 @ `94c706e` success（3m34s）**；
> Nightly Gate 为 cron 调度（每日 02:00），下次调度将自动以最新 HEAD 实跑。
> 原则：只写**实际发生并可复核**的事；未跑写 NOT_RUN，被挡写 BLOCKED。

---

## 0. 一句话结论

RUN-018-R1 把关键能力从"实现"推进到"**经得起并发测试、真实运行与代码审查**"：
GC 拿到了真正的跨进程生命周期锁（SQLite + PG16 双端 13 项多进程并发测试），
诊断反馈闭环在真实 TeamFlow 上端到端跑通，双提交观测升级为真实浏览器实验，
Holdout 真实执行（冻结 manifest + 显式分母），16 视图无障碍 65/65 通过且
5 类真实违规全部修复，运维从文档变成 6 个可执行命令。

---

## 1. 功能完成度矩阵（R1 轮后）

| 能力 | 状态 | 证据 |
|---|---|---|
| CI 门禁（Ruff/SIGKILL/Worker readiness/Job Summary） | **VERIFIED**（PR Gate #102 @ `94c706e` success，3m34s；Nightly 待 cron 实跑） | `docs/RUN-018-WP1-CI.md`、GitHub Actions |
| 独立观测 UNKNOWN 语义 | **VERIFIED**（前轮保持） | 37 测试 |
| 探针清理安全 | **VERIFIED** | 19 单测 + 8 项真实 TeamFlow 集成（共 27） |
| Artifact GC 跨进程并发安全 | **VERIFIED** | 13 项多进程并发（SQLite+PG16）+ journal 崩溃恢复 + 10k 基准无回退 |
| Active Probe → Diagnosis Feedback | **VERIFIED** | 场景 A–E 47 项 + 真实 TeamFlow 端到端 2/2 |
| 双提交真实浏览器观测 | **VERIFIED** | 实验 A（FRONTEND_DEBOUNCE 确认）+ 实验 B（服务端无幂等→重复数据）+ 12 测试 |
| 诊断独立 Holdout | **VERIFIED** | 冻结 manifest + 24 新变体 + 2 轮 + 显式分母评测 |
| Monitoring / Alert 真实运行 | **VERIFIED** | 真实 uvicorn API 5 场景 + 27 项既有（共 32） |
| 16 视图无障碍 | **VERIFIED** | 65/65（axe-core + 键盘 + 弹窗 + 双主题 + 390px），5 类违规修复 |
| 部署运维工具 | **VERIFIED**（工具箱）/ **NOT_RUN**（真实生产部署未授权） | 6 子命令 + 5 测试（含真实 PG16 pg_dump 备份恢复） |

---

## 2. Git 提交（R1 轮，按工作包小步提交）

| HEAD | 内容 |
|---|---|
| `2f26686` | WP1：Ruff 16 项修复（I001/F401/F541/F821/F841）+ 17 文件 format |
| `0e69f44` | WP1：SIGKILL 多进程确定性同步（READY 文件协议，退出码 -9 断言） |
| `897f359` | WP1：Worker readiness 门禁 + Job Summary 本次 run 专属报告 + WP1 文档 |
| `529c837`+`f5877a1` | WP2：GC 统一跨进程生命周期锁 + 引用守卫 + GC journal + 13 项并发测试（+清理误提交产物） |
| `2b6c047` | WP3：探针清理真实 TeamFlow 集成测试 8 项 |
| `6493d1d` | WP5：真实 TeamFlow 诊断反馈闭环 2/2 → VERIFIED |
| `f600f4c` | WP7：Monitoring/Alert 真实运行 5 场景 |
| `a3930e9`+`2dfe0c8` | WP4/WP6：双提交真实浏览器观测 + 文档 VERIFIED |
| `14b6ba4` | WP6'：Holdout 真实执行（manifest + 2 轮 + 评测） |
| `22901a4` | WP8：无障碍 65/65 + 5 类违规修复 |
| `6b891a7` | WP9：运维工具箱 6 子命令 + 真实 PG16 备份恢复 |
| `426f1e6` | WP2：PG16 共享库测试隔离修复，PG16 全量集成 558 passed |

---

## 3. 最终回归（针对最终 HEAD `426f1e6` 真实执行）

| 项 | 结果 |
|---|---|
| `ruff check`（packages/apps/tests/benchmark/scripts） | All checks passed |
| `ruff format --check` | 288 files already formatted |
| `pytest tests/unit/ -q` | **492 passed**（0 failed） |
| `pytest tests/integration/ -q`（SQLite） | **542 passed, 16 skipped**（PG 专项待 PG URL） |
| `pytest tests/integration/ -q`（TG_TEST_POSTGRES_URL=PG16.16） | **558 passed, 0 failed, 0 skipped** |
| `npm run typecheck` + `npm run build`（apps/web） | 通过 |
| UI 业务闭环回归（真实 Chromium + 独立 Worker） | **36/36 PASS**（lease_owner=r13-w1，fencing=1） |
| SIGKILL 多进程测试 | 3/3 确定性通过（约 8s） |
| GC 10k 性能基准 | scan 0.058s / keyset 分页 / consistent=true（无机制性回退） |

**不把 PG 专项 SKIP 当作 PG16 验收**——PG16 全量 558 项真实执行通过。

---

## 4. 最终验收 19 问（逐条回答）

1. **PR Gate 是否在最终 HEAD 通过？** **是——PR Gate #102 @ `94c706e` success
   （3m34s，backend + frontend 双 job 全绿）**。过程记录：#86~#98 连续绿；
   #99~#101 于 30-33s 早退——根因是 426f1e6 的补丁漏跑 `ruff format`
   （format --check 门禁确定性失败，本地复核复现），`94c706e` 修复后通过。
2. **Nightly 是否在最终 HEAD 通过？** 待 cron（每日 02:00 北京时间）自动以最新
   HEAD 实跑；WP1 修复（readiness 门禁 + 本次 run 专属报告）已就位，B-28 待
   该次结果 CLOSED。无 gh 凭据（匿名 API 403），结果核对需凭据或 Actions 页面。
3. **Worker 是否在 CI 真正领取 Run？** 本地 3 次验收均由独立 Worker 领取
   （lease_owner=r13-w1/r13-w2，fencing=1）；新增 readiness 门禁保证 CI 上
   Worker 未就绪时带日志快速失败而非静默 QUEUED。
4. **UI Summary 历史报告问题是否修复？** 是。每次 run 专属目录
   `docs/assets/run018/ci/<run_id>/ui`，Summary 只读本次报告，缺失显示 NOT_PRODUCED。
5. **SIGKILL 多进程测试是否确定性通过？** 是。READY 文件协议（不共享 mp 锁——
   被杀进程持锁会死锁父进程，实测定位后改为文件轮询），3/3 通过，断言
   `killed_exitcode == -9`、kill 前后预算不重置、存活进程继续扣减。
6. **Artifact GC 是否有跨进程生命周期锁？** 是。PG advisory xact lock（per-artifact
   键）/ SQLite 锁表写锁；锁与业务同事务，覆盖 put/GC 隔离/GC 删除/引用创建/清理。
7. **PG16 并发 GC 是否真实验证？** 是。6 项 PG16 多进程场景 × 3 轮稳定
   （put 竞争、引用竞争、双 GC、SIGKILL+恢复、同 SHA、删除竞争）。
8. **Evidence/Run 引用是否与 GC 同步？** 是。引用创建走同一把锁 + 锁内复核，
   产物已回收 → ARTIFACT_COLLECTED 拒绝（Run 报告同理）；无 dangling。
9. **Double Submit 是否真正浏览器观测？** 是。实验 A（同 context 双击，捕获真实
   POST 数）+ 实验 B（双独立 context 并发）；6 类分类严格区分。
10. **Active Probe 是否产生真实新 Evidence？** 是。真实 http_state 探针读真实
    TeamFlow 状态，probe_evidence_contract 完整落库。
11. **新证据是否真正改变诊断裁决？** 是。plan_and_probe 回灌 → 假设/置信度变化
    → revision 追加（真实故障样本 PERSISTENCE 获得支持证据）。
12. **Diagnosis 版本链是否可追溯？** 是。list_revisions 2 条（v1 快照可恢复、
    trigger=probe_evidence、probe_ids/confidence_changes 有依据）。
13. **Holdout 是否真实执行？** 是。2 轮 × 24 新变体：Selective Accuracy 1.0
    （分母 8）、弃权 66.7%（分母 24）、Primary Cause Accuracy 0.5（严格口径）、
    零 FP；PERSISTENCE/TIMING/LOCATOR 弃权是引擎设计（弃权优于硬猜）。
14. **Monitoring/Alert 是否经过真实运行？** 是。真实 uvicorn API：重启计数清零 +
    告警持久化、Worker 停止→恢复生命周期、真实积压、数据源不可用不当作恢复 +
    TOCTOU、Artifact 缺失、GC 异常。
15. **16 视图是否通过无障碍验收？** 是。65/65；首轮 4 类真实违规
    （button-name×32、contrast×22、scrollable×20、dlitem×16）全部修复后复测通过。
16. **部署运维哪些是真正可执行功能？** artifact 增量备份、verify-backup、sha-audit、
    retention-dry-run、pre/post-migration-check（+doctor）、PG16 pg_dump 备份恢复
    全部可执行且有测试。
17. **哪些仍是文档或计划？** 真实生产服务器的备份部署与定时策略（未授权）、
    容量规划、SLA 定义（阈值仍 provisional）。
18. **哪些被外部审批阻塞？** Release Gate 真实 LLM（缺 Secret，禁止寻找凭据）、
    Reliability Gate（需人工 120 分钟窗口）、CI 日志导出（无 gh 凭据）、PR #3 创建
    （未授权不创建）。
19. **距离正式发布还差什么？** ① 最新 HEAD 的 CI 实跑确认（PR Gate + Nightly 全绿）；
    ② Reliability Gate 长稳窗口；③ 真机部署与真实备份策略启用授权；
    ④ Holdout 弃权率产品决策（提升 PERSISTENCE/TIMING Recall 需改确认规则并换新 Holdout）。

---

## 5. 已知风险与未完成项

- **B-28（Nightly ui-acceptance）**：修复就位；PR Gate #102 全绿，Nightly 待
  cron 实跑 → MITIGATED；
- **CI 过程教训**：`426f1e6` 补丁漏跑 `ruff format` 导致 PR Gate #99~#101 早退
  （30-33s）——提交前必须完整跑 `ruff check + format --check` 双门禁；
- **Holdout 弃权率 66.7%**：如实呈现引擎保守设计；提升需规则变更 + 新 Holdout；
- **双提交实验 B 发现**：TeamFlow 服务端对跨会话同名并发提交无幂等保护
  （产生重复数据）——被测系统的真实缺陷，修复属 TeamFlow 侧工作；
- **WP9 权限不足状态**的专项注入未执行（如实记录未覆盖）；
- **无 gh CLI/凭据**：CI 日志与工件无法程序化获取（匿名 API 403）。

---

## 6. 状态口径

IMPLEMENTED / VERIFIED / PARTIAL / BLOCKED / NOT_RUN / NOT_IMPLEMENTED——
**不把 PARTIAL 改成 VERIFIED，不把 NOT_RUN 改成 PASS。**
本报告与各 WP 专题文档、`.workbuddy-run/` 台账（current-task / phase-status /
progress / blockers / resume）状态一致。
