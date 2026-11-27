# RUN-018 WORKLOG（执行日志）

## RUN-018-R2 轮（2026-11-27，B-28 真实闭环）

- **发现前轮收口口径不实**：Nightly Gate 不仅由每日 02:00 cron 触发，本分支
  `workbuddy/run018-diagnostic-closure` 的每次 push 也会触发。核查 Actions 页面
  发现 #56（e1fb5f3）、#57（e4c925b）均 **failed**，失败点同为
  `ui-acceptance` step 8 退出码 3——前轮报告写成"待 cron 实跑"是不准确的。
  同时补记：PR Gate #104 @ e4c925b 也是 success（3m39s），前轮只记到 #103。
- **让失败可诊断**（4fa09c8）：`Reporter.check()` 对每条 FAIL 输出
  `::error title=UI 验收失败::` annotation（本地为普通 stdout）。Nightly #58
  @ 4fa09c8 仍失败，但 annotation 直接给出根因：
  `ModuleNotFoundError: No module named 'traceguard_worker'`（两个独立 Worker
  退出码 1 → readiness 判定 `process_not_alive` → 脚本退出 3）。
- **根因**：`scripts/business_loop_ui_acceptance_run012.py` 的白名单 `_PATHS`
  漏了 `apps/workers`。本地 venv 的 `traceguard.pth` 注入了该路径，掩盖缺陷；
  CI 仅按 requirements 安装，没有该 .pth → 子进程 `-m traceguard_worker.main`
  找不到包。这是 B-28 自 RUN-017 起"本地通过、CI 失败"长期未定位的真正原因。
- **修复**（05a3639）：白名单 `_PATHS` 显式加入 `apps/workers`，不再依赖本地
  .pth；同步把 `ui-acceptance` job 的依赖安装改为优先 `requirements.lock.txt`
  （与 backend-full 同口径，失败才回退范围声明）。
- 本地全量复核：ruff check/format 全绿；515 unit passed；542 integration passed
  （SQLite，16 PG 专项 skipped）；前端 typecheck + build 通过；core UI 验收
  36/36（本地两次，修复前后各一次）。另用**无 traceguard.pth 的独立 venv**
  （模拟 CI 的依赖安装形态）复跑 core 验收，同样 36/36、退出码 0。
- **CI 实跑（代码 HEAD `05a3639`）**：PR Gate #106 success（3m18s）；
  Nightly Gate #59 **success（12m55s，全部 6 个 job 成功）**，ui-acceptance
  2m4s、无 FAIL annotation、证据工件 3.04 MB（失败时仅 133 KB）。B-28 CLOSED。
- 口径修正：R1 报告的"Nightly 待 cron 实跑"与"492 unit"均已按实测更正；
  PR Gate #104 @ e4c925b success 亦补记。

## RUN-018-R1 轮（2026-11-27，起点 7674842）

按时间序的真实工作记录（详细证据见各 WP 专题文档与 docs/assets/run018/）。

### WP1 CI 门禁
- 09:37 基线核查：HEAD 与远端一致（7674842）；工作区有前轮遗留的
  api_error_rate 阈值移除修改（保留并随后续提交入库）。
- Ruff 16 项修复：I001×5 自动排序、F401×4 删无用导入、F541×2、
  F821 monitoring.py 用 TYPE_CHECKING 导入 ArtifactStore（避免循环依赖）、
  F841×3 按指令判断后补断言/删除；17 文件 format 对齐；100 项相关测试通过（2f26686）。
- SIGKILL 测试：根因 = attempts(4)×delay(0.01) 子进程 0.04s 跑完，固定 sleep(0.6)
  后已退出 → killed_pid=0。首版用 mp.Event 同步 → 实测死锁
  （被 SIGKILL 的子进程持有 POSIX 信号量，父进程 Event.set() 永久阻塞，
  faulthandler 定位）→ 改为纯文件轮询协议（READY 文件 / release 文件 / 退出码 -9
  断言 / kill 前后 DB 回读）。3/3 确定性通过（0e69f44）。
- Worker readiness：Nightly #37 形态（进程存活、队列不动）源于 fleet.alive() 盲区。
  新增 WorkerFleet.ready()（tg_worker_heartbeat 校验：ID/PID/mode/status/新鲜度/
  库归属）+ 主链路 ready 门禁（45s，失败保留每个 Worker 日志尾部并返回 3）。
- Job Summary：报告写入 run_id/profile/git_head；CI 每次 run 专属目录
  docs/assets/run018/ci/<run_id>/ui；缺失显示 NOT_PRODUCED 不回退历史。
- 本地 UI 验收 3 次（local1-3）：36/36 × 3，独立 Worker 领取 + fencing=1。

### WP2 Artifact GC
- 设计统一生命周期锁：PG advisory xact lock（SHA-256 派生 per-artifact 键）/
  SQLite 锁表首语句写锁；锁与业务同事务。
- 首版 mp.Event 方案在 kill 场景死锁 → 与 WP1 同款教训，文件协议。
- 引用路径全部接入锁：put（含 fence 守卫与文件自愈）、Evidence._insert
  （art_* 引用锁内复核）、Run 报告提交（lock_and_verify_report_refs）。
- GC journal（intent→done→resolved）+ recover_journal() 幂等恢复。
- 13 项多进程并发测试（SQLite 7 + PG16 6）：8 类场景全过。
- 调试插曲：PG16 共享库（TG_TEST_POSTGRES_URL）下跨测试行残留——根因是
  setup 清表的 DELETE 遇到不存在的表使整个事务 aborted 全部回滚；
  改为 inspector 过滤真实存在的表。
- 10k 基准：scan 0.058s、51 条 keyset 查询、consistent=true（无 O(N×M) 回退）。
- 误提交 500 个基准产物文件 → 跟进提交清理（仅保留 JSON 报告）。

### WP3 探针清理
- 19 项既有测试保留；新增 8 项真实 TeamFlow 集成（授权矩阵 / 通配符拒绝 /
  作用域 / 幂等 / 业务数据隔离 / 生产 API 无清理入口静态验证）。

### WP4/WP6 双提交
- 新增 BrowserDoubleSubmitObserver：实验 A（同 context 双击，真实捕获 POST 数）
  → TF-TIME-003 下 FRONTEND_DEBOUNCE_EFFECTIVE（CONFIRMED，与 Ground Truth 一致）；
  实验 B（双独立 Playwright 实例 + Barrier 并发）→ 服务端无幂等，产生重复数据
  （真实发现，诚实分类 DUPLICATE_CREATED）。
- 第二次 click 在无故障时因导航跳转超时 → 如实记录 clicks_delivered，
  不视为观测失败。

### WP5 诊断反馈
- 真实 TeamFlow 端到端：TF-DB-001 注入 → 真实 HTTP 创建（ok 但 persisted=False）
  → 独立状态回读确认未落库 → Run + TRUSTED 证据 → 诊断 v1 → plan_and_probe
  （真实 http_state）→ 探针证据落库（契约完整、workspace 继承）→ 图更新
  （ProbeExecution 节点）→ revision 2 追加 → 探针不可用变体不污染 → 真实清理。

### WP6' Holdout
- 冻结 manifest（规则文件 SHA-256 + 策略版本 + GT 版本）；24 个新变体（6 族）；
  2 轮（正序/逆序）独立执行；独立评测（显式分母）。
- 结果：Selective Accuracy 1.0、弃权 66.7%、零 FP、ambiguous 4/4 正确弃权。

### WP7 监控
- 真实 uvicorn API 子进程 5 场景：API 重启（计数清零 + 告警持久化 + since 更新）、
  Worker 停止→恢复（OPEN→condition_cleared RESOLVED）、真实积压 25 条、
  数据源不可用不当作恢复 + TOCTOU、Artifact 缺失 + GC 异常。

### WP8 无障碍
- 首轮发现真实违规：button-name×32、color-contrast×22、scrollable×20、dlitem×16。
- 全部修复：sr-only 搜索按钮、暗色 --brand-text、scroll-x/pre.json/graph-stage
  补 tabindex、dt/dd → .kvt/.kvd、chip 换行 + SVG 自适应；label-content-name
  逐条对齐（aria-label 与可见文本/kbd aria-hidden）。
- 65/65 PASS；前端 typecheck+build+业务闭环 36/36 回归通过。

### WP9 运维
- 工具箱 6 子命令 + 5 项集成测试；真实 PG16 pg_dump 备份恢复（pg_dump 位于
  Postgres.app 卷，find_pg_tool 覆盖）。

### 最终回归与收口
- ruff 全绿；492 unit；542 integration(SQLite)；**558 integration(PG16, 0 skipped)**；
  前端 typecheck/build；UI 业务闭环 36/36。
- 台账统一更新（current-task/phase-status/progress/blockers/resume）。
- 最终报告重写（docs/RUN-018-FINAL-REPORT.md）；最终 HEAD 426f1e6 推送触发 CI。
