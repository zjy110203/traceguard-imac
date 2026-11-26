# RESUME（RUN-017 收口，2026-11-26 23:55 CST）

- 分支：`workbuddy/run017-product-maturity`，最终 HEAD `f34f6b4`（已推送）。
- 先读：`docs/RUN-017-FINAL-REPORT.md` → `RUN-017-BASELINE.md` → 各 WP 专题 → `RUN-017-WORKLOG.md`。
- **下一步第一件事**：`gh run view 45849709837 --log-failed`（Nightly #30 的 ui-acceptance 失败日志）
  与 `gh run download 45849709837 -n ui-acceptance-evidence`（报告 JSON + 截图），
  定位 B-28；随后再考虑 PR #3 的创建（描述见 `docs/RUN-017-PR3-DESCRIPTION.md`）。
- 环境：PG16.16 在 `127.0.0.1:55432`（**不要用 localhost**，IPv6 指向另一个 16.2 实例）。
- 复现入口：`scripts/bench_v2_run017.py`、`scripts/backup_restore_verify_run017.py`、
  `scripts/doctor.py`、`scripts/business_loop_ui_acceptance_run017.py --profile full`、
  `scripts/ui_matrix_run017.py`、`scripts/artifact_gc_bench_run017.py`。

---

# RESUME（RUN-017，2026-11-26 21:55 CST）

- 分支：`workbuddy/run017-product-maturity`；已推送 `1faebec`（CI run #75/#27）。
- 先读：`docs/RUN-017-BASELINE.md` → `RUN-017-WP1-CI.md` → `RUN-017-WP2-BENCHMARK.md`
  → `RUN-017-WP3-REGRESSION-RETRY.md` → `RUN-017-WP5-PRODUCT.md`。
- 环境：PG16.16 在 `127.0.0.1:55432`（**不要用 localhost**，IPv6 指向另一个 16.2 实例）。
- 未完成：WP4.4 长稳收尾、WP6 UI 全链路、WP7/最终报告、WP8 最终 HEAD 复跑。
- 复现入口：`scripts/bench_v2_run017.py`、`scripts/backup_restore_verify_run017.py`、
  `scripts/doctor.py`、`scripts/business_loop_ui_acceptance_run017.py`。

---

# RESUME（RUN-015，2026-11-26 追加；下方历史保留）

- **当前分支**：`workbuddy/run015-execution-consistency`（基线 `438c54a`，勿改 main / 勿合并 PR #1/#2）。
- **恢复顺序**：先读 `docs/RUN-015-BASELINE.md` → `RUN-015-WP{1,2,3,4,6}-*.md` →
  `RUN-015-WORKLOG.md` / `RUN-015-FINAL-REPORT.md`（收口后）→ 本目录其他状态文件。
- **状态**：WP0~WP7 已交付；BG 长稳任务 `tnMzbm`（首轮）与 `07Y1Eh`（复验轮）均已完成，
  最终证据为 `docs/assets/run015/wp5/soak-r2/soak-60min.json`（59.71 分钟 / 115 Run / 31 PASS / 0 FAIL）。
- **PG16 全链路**：`docs/assets/run015/wp5/full-loop/full-loop.json`，40/40 PASS。
- **CI**：最终代码 HEAD `c12ccab` 的 PR Gate `45781959624` / Nightly `45781958909` 均 success；
  无凭据不触发 Release，不伪造结果；证据 `docs/assets/run015/final/ci-runs.json`。
- **环境**：本机 PG16.16 在 `127.0.0.1:55432`（用户 `traceguard`）；无 psql CLI / Docker / gh CLI；
  curl 匿名访问 GitHub API 会 rate limit，可用 WebFetch 只读核验。
- **已知不变量**：B-17 仍 BLOCKED，不绕过；Benchmark 历史归档 SHA 冻结；INCONCLUSIVE 重试为 NEEDS_DECISION。

---

# RESUME（RUN-011-R1.1，2026-11-25 追加；下方 R1 历史保留）

- **交付提交**：`ec05061`（`workbuddy/run011-full-loop`），已推送，本地与 origin HEAD 同步；
  工作区 clean。未改 main / PR #1 / PR #2，未强推。pre-push hook 的 `/bin/ps` 权限警告不阻断推送。
- 状态：**交付完成**（除 BLOCKED 项）。先读 `docs/RUN-011-R1.1-FINAL-REPORT.md`（10 问）→
  `RUN-011-R1.1-BASELINE.md` → 四份专题报告（WP1 安全 / WP2 Repair / WP3 Benchmark / WP6 UI）。
- 证据：`docs/assets/run011-r1.1/lineage/loop{1,2}{,-db}.json`（各 86/86 + 24/24）、
  `docs/assets/run011-r1.1/ui/ui-acceptance.json`（15/0 + 6 截图）。
- 复现：隔离/血缘 23 passed；全量 `pytest -q` 690 passed；PG16 专项 13 passed；
  `python scripts/ui_acceptance_run011_r1_1.py --out docs/assets/run011-r1.1/ui` 15/0（可重复运行，自动清库）。
- 边界：前端无 workspace 切换控件（硬编码 default）；B-17 保持 BLOCKED；无真实 CI 证据不标 PASS；未合并 PR、未改 main。

---

# RESUME（RUN-011-R1，2026-11-25）

- 状态：**交付完成**（除 BLOCKED 项）。从 `docs/RUN-011-R1-FINAL-REPORT.md` 九问与交付清单继续。
- 若续作：优先等负责人对 B-17 安全批准（WP7 材料）、LICENSE、PR 的决策；
  其次 UI 全链路点击验收、多 API/DB 断连/长事务（NOT RUN 项）。
- 环境快照：PG16 :55432（traceguard_run011_r1_l1/l2/pgtest）、本机无 gh/凭据、
  测试终态 680/0/0（PG16）。
- 决策记录：`.workbuddy-run/decisions.md`（R1-01~R1-05，含 R1-04 扩展）。

---
# 中断恢复指引（RUN-006 结束点）

> **RUN-010 恢复摘要（2026-11-24 22:05 CST）**：RUN-010 WP0~WP7 已完成并推送
> （分支 `workbuddy/run008-quality-hardening`，HEAD=1479982 与 origin 同步）。
> WP8 闭环测试曾触发 AgentDR info_theft_steal_env_keys（subprocess+env 字典模式），
> **已按安全边界处置**：不绕过、不重试被拦内容，改进程内实现（零子进程/零环境字典/
> 虚拟测试口令）后获放行，见 `tests/e2e/test_full_loop_run010.py` 与 blockers.md B-20。
> 恢复路径：WP8 测试验证 → WP9 文档（RUN-010-FINAL-REPORT.md 等）→ CI 门禁复验 → 收口。
> 恢复时必读：`docs/RUN-010-BASELINE.md` → 各 WP 报告 → `.workbuddy-run/blockers.md`（B-20）。
> 不要重试被 AgentDR 拦截的子进程+环境字典写入；不要绕过 BuddySentinel。
> 以下历史记录保留不覆盖。

> **RUN-009 恢复摘要（2026-11-24 更新）**：RUN-009 已完成并交付，最终状态见 `docs/RUN-009-FINAL-REPORT.md`。
> 权威起点链：`ac2876e`（origin/main）→ `ce1acea`（RUN-007）→ `cb80591`（RUN-008）→ RUN-009 新提交（见 git log）。
> `40efee6` 仍是 RUN-008 代码/CI 基线；PR #2 保持 open/HOLD，合并需按最终报告重新评估。
> 下一步先读 `docs/RUN-009-FINAL-REPORT.md` → `docs/RUN-009-BASELINE.md` → `docs/RUN-009-REVIEW-REPORT.md`。
> 以下历史记录保留不覆盖。

> 下一次运行的第一步：读本文件 → `docs/RUN-006-BASELINE.md` → `docs/RUN-006-IMPLEMENTATION-REPORT.md`
> → `docs/RUN-006-TEST-REPORT.md` → `docs/RUN-006-SECURITY-RELIABILITY.md` → `docs/RUN-006-DECISION-REQUESTS.md`
> → `docs/FEATURE-STATUS.md`（RUN-006 段）→ `.workbuddy-run/progress.md`。
> 不要从零重新开发。不要把 PARTIAL/BLOCKED 写成完成。不要用历史证据替代本轮验证。

- 最后保存：2026-11-23（RUN-006，WP1 完成）
- 分支：`workbuddy/traceguard-autonomous`（未推送远程）
- 指令基线：`dadb4ae`；本轮起点 HEAD：`78a09f6`

## 当前快照

- 测试：**367 passed / 5 skipped / 0 failed**（`pytest -q`，30.0s）；skip 全部是 PG parity（本机无 PG）
- 真实浏览器：`pytest tests/e2e -q` → **11 passed**
- Lint：`ruff check packages apps tests` 全绿；本轮改动文件已 `ruff format`
- 真实环境证据：`docs/assets/run006/`（正常/故障 Run、诊断、probe-plan、真实探针回灌、证据图、Oracle 门禁实测）
- 数据库迁移：**v7**（新增 `tg_testcase_version.review_revision`）

## 本轮真正修好的东西（VERIFIED）

1. **Oracle 闭环**：绑定 API + required assertion 门禁 + 版本固定 + 主体类型校验 + 并发审核 CAS。
2. **诊断闭环**：探针证据回灌假设、重诊断、冲突真实计算、全 `STOP_*` 原因；探针失败不当证据。
3. **LLM 确认漏洞**：`llm:*` 假设不再能保留 CONFIRMED（原实现是精确匹配 `"llm"` 的真实漏洞）。
4. **预算故障安全**：读/写失败一律阻断；`reserve()` 用文档版本 CAS 做跨进程扣减。
5. **晋升/回滚原子性**：单事务"比较 + 条件更新 + 插入版本"；陈旧提案不会覆盖新版本。
6. **网络管控**：`TargetPolicy` 统一 Explorer 与 Runner；真实 Chromium 下未授权目标服务端 0 次请求。

## 已知限制（必须在后续报告中继续披露）

- 无身份认证：审批人仍是请求体声明；只有"主体类型门禁"，不是 RBAC。
- 顶层 302 跳转的**出站请求会到达目标**（route 不拦顶层跳转）；产品保证是"落地拒绝 + 步骤失败"。
- 预算跨进程一致性用两个 Registry 实例验证，**未做双 OS 进程压测**。
- PostgreSQL 行锁/迁移 v7 未实测；4 Worker、60 分钟长稳未执行；CI 未真实触发。
- 前端只改了诊断视图（PARTIAL）：30/30 UI 验收、4 断点 + 深色截图；其余 13 视图未审查。Benchmark v3 未做。

## 环境

| 项 | 值 |
|---|---|
| Python | 3.13.12（`.venv`） |
| Node | 22.22.2 |
| playwright | 1.65.2（自带 Chromium） |
| 数据库（本地） | SQLite（迁移 v7）；隔离验收库 `.traceguard/run006/` |
| PostgreSQL | 本机不可用（无 Docker/psql）→ parity 测试 skip |
| LLM | `.env` 内已配置 DeepSeek（本轮真实诊断调用发生） |

## 恢复后立即可做的验证

```bash
# 1) 单元 + 集成 + E2E（约 46s）
.venv/bin/python -m pytest -q

# 2) 只跑真实浏览器
.venv/bin/python -m pytest tests/e2e -q

# 3) 真实服务（隔离库，勿用默认库）
TG_TEAMFLOW_DB="sqlite:///./.traceguard/run006/teamflow.db" \
  .venv/bin/python -m uvicorn traceguard_teamflow.main:app --host 127.0.0.1 --port 8011
TG_DATABASE_URL="sqlite:///./.traceguard/run006/api.db" \
TG_ARTIFACT_DIR=".traceguard/run006/artifacts" TG_TEAMFLOW_BASE_URL="http://127.0.0.1:8011" \
TG_INLINE_WORKER=0 \
  .venv/bin/python -m uvicorn traceguard_api.main:app --host 127.0.0.1 --port 8010

# 4) 关键 API 复现
curl -X POST http://127.0.0.1:8010/api/v1/testcases/tcase_tf_create_normal/execute \
  -H 'Content-Type: application/json' -d '{"sync": true}'
curl -X POST "http://127.0.0.1:8010/api/v1/runs/<run_id>/diagnose"
curl -X POST "http://127.0.0.1:8010/api/v1/runs/<run_id>/probe-plan?diagnosis_id=<diag_id>"
```

> 后台起服务请用宿主工具的后台任务机制（不要 `nohup ... &`，进程会随 shell 退出被回收）。

## 下一步（按解锁顺序）

1. **CI 触发一次真实 Nightly**（含 PostgreSQL Service Container）：验证迁移 v7、`review_revision` CAS、
   `promote_version_cas` 行锁行为（决策请求 D-07）。
2. **60 分钟长稳 + 4 Worker 故障恢复**：独立时间窗，脚本化采集指标并归档原始 JSON（D-08）。
3. **完整端到端全链**：Intent → … → Repair 晋升 → 回归 → 回滚，全部走真实 API/DB/浏览器/Worker（WP2-02）。
4. **前端工作包（剩余）**：其余 13 视图信息层级、Run 详情失败步骤↔证据联动、Evidence Graph 交互、SSE 断线状态（WP3）。
5. **决策落地**：D-01（认证）、D-02（P08 角色）、D-05（vite 升级）、D-06（队列）——需负责人批准。
6. **Benchmark v3 多臂**：A/C/D/E + LLM 分批，报告分子分母与样本量（WP6）。

## 本轮关键记忆

- 审核门禁：`review_testcase(APPROVED)` 会调用 `_oracle_gate()`；缺 Oracle → 412，不要"先放宽再补"。
- 绑定：`bind_oracles()` 只允许在 `DRAFT/PENDING`；APPROVED 版本不可重绑（要新建版本）。
- 版本固定：`AssertionSpec.oracle_version`；`pipeline._load_oracles()` **不回退**到最新版本。
- 主体校验：`require_human_actor()` 按命名空间首段判断；`role:`/`human:`/`qa-team` 合法。
- 探针回灌：`feedback.assess()` 只看 `SUCCEEDED`；失败/超时返回 `PROBE_*_NOT_EVIDENCE`。
- 冲突：`unresolved_conflicts` 落库；循环顶部检测 → `STOP_CONFLICT_UNRESOLVED` 且 0 消耗。
- 预算：`ProbeBudgetRegistry.reserve()` 先写库后计入；`PROBE_BUDGET_UNAVAILABLE` 一律阻断。
- 晋升：`catalog.promote_version_cas()` / `rollback_version_cas()`；不要在服务层再写"先比较再创建"。
- 网络：`TargetPolicy` 是唯一策略；`SecurityPolicy` 仍用于 SSRF/DNS/凭证扫描（两者结论一致性有测试）。

## 不要做的事

- 不要删除 `.workbuddy-run/`、`.traceguard/`、`docs/assets/run006/`。
- 不要 `git reset --hard` / `git clean` / 强推。
- 不要访问外部 Web 应用（P-10 未授权）。
- 不要声称 P03/P06/P08 已决策、路线图已冻结。
- 不要把 skip 当 passed，不要把 PARTIAL/BLOCKED 写成成功，不要为过测试削弱断言。
- 不要把顶层 302 说成"请求已被阻断"——它只是"落地被拒绝"。

## RUN-007-RESUME 关键记忆（2026-11-23）

- 执行归属：E2E/沙箱/回归一律 `{"sync": false}` 入队 + 轮询 `GET /runs/{id}`；
  校验点是 `lease_owner` 非空且不等于 API Worker ID、`fencing_token >= 1`。
- 证据来源：`report.evidence_ids` 可能为空，**必须**用 `GET /runs/{id}/evidence`，
  再校验证据图里有对应 `Evidence` 节点与来源关系。
- 缓存生存期：`RunPipeline._state_cache` 只允许活一个 Run（`execute()` 开头清空），否则常驻 Worker 会漏检假通过。
- 沙箱：`RepairService` 不再 `execute_run`，改 `wait_for_run`；无 Worker → `PRECONDITION_FAILED`，Run 保持 QUEUED。
- 建表并发：`create_schema` 必须容忍 "already exists"；迁移每条语句用 `conn.begin_nested()`（PG aborted 事务）。
- 长稳：`teamflow_ok` 必须是真实 `ok is True` + 有 `count`；SSE 要统计事件/心跳/retry 帧并做 `Last-Event-ID` 续传。
- 故障恢复：接管验证必须"同 Run + `fencing_token` 严格递增 + `attempt` 不变"；`killed_after_s` 必须来自真实计时。
- UI 暗色：前端主题由 `localStorage['tg-theme']` 决定，**不是** `prefers-color-scheme`；
  必须 `add_init_script` 写入后再截图，并用亮度断言验证（否则会交付"假深色"证据）。
- 沙箱限制：`ps` 被禁止、无 `/proc`；`_proc_stats` 已回退为存活探测并标 `source=liveness_only`，资源字段为 `null`。
- 无 Git 凭据：https 无 keychain、无 SSH key、无 `gh`/token → push/PR/CI 触发全部 BLOCKED。

## 不要做的事（RUN-007 追加）

- 不要覆盖 `docs/assets/run007/e2e-run007.json`（第五轮旧证据）。
- 不要用 `use_llm=false` 的结果冒充 LLM 能力；LLM 候选状态是 CANDIDATE，不能当根因。
- 不要把 PG 用例的 skip 写成 pass；不要声称 CI 门禁"已通过"。
- 不要在长稳运行期间跑故障注入（会污染 TeamFlow 状态与周期判定）。

## RUN-007-RESUME CI 调试经验（2026-11-23）

- **`str(SQLAlchemy URL)` 会把密码渲染成 `***`**（`hide_password` 默认 True）。凡是要拼 URL 字符串给
  另一个 `create_engine` 用的地方，必须 `render_as_string(hide_password=False)`。本轮在此踩了两次
  （测试 fixture + workflow 内联脚本）。
- **pytest 的 `pythonpath`（pyproject）只对 pytest 生效**：内联 `python - <<'PY'` 与 spawn 子进程
  都拿不到。CI 里一律给 job 级 `PYTHONPATH`，脚本内部再自行 `sys.path` 注入作双保险。
- **ORM 实体在 `session.execute(update(...))` 后会被 expire**：紧接着读它的字段会重新 SELECT，
  拿到的是新值。要记录"更新前的状态"，必须在 UPDATE 前快照。
- **SQLite 的 `busy_timeout` 不覆盖 DDL 排他锁竞争**：并发 `CREATE TABLE` 仍会直接抛
  `database is locked`；必须把这类错误纳入重试（并兼容 PG 的 `deadlock detected`）。
- **状态机是 `QUEUED→PREPARING→RUNNING→FINALIZING→COMPLETED`**：测试不能假设"领取即可提交完成"，
  真实流程见 `pipeline.py:130` 的 `mark_running`。
- **门禁要真实跑过才算数**：本地 523 passed 掩盖了 8 个问题，CI 首跑全暴露。
- 网络：`api.github.com` 稳定；`github.com`（git 传输）经代理间歇性 `Empty reply` / `CONNECT 502`，
  push 需要重试循环。

## RUN-008 恢复点（2026-11-24，本轮最新，优先于上文旧快照）

> **中断恢复第一步**：读本节 → `docs/RUN-008-BASELINE.md` → 各 WP 实施报告 →
> `docs/assets/run008/` 原始证据 → `.workbuddy-run/progress.md`（WP8 更新后）。
> 上文「RUN-006 结束点」「无 Git 凭据」「不要声称 P03/P06/P08 已决策」等条目均为历史快照，
> 已被本项目负责人新授权覆盖：允许自主实施待决策事项，但必须完整记录方案与证据。

- 起点 HEAD：`ce1acea5d3778e9518c84f8c4e95496984a4dcdc`（`origin/main` 仍为指令基线 `ac2876e`，落后 19 提交）
- 当前分支：`workbuddy/run008-quality-hardening`（从起点 HEAD 创建；起点工作区干净）
- WP0 状态：**完成**；基线文档 `docs/RUN-008-BASELINE.md` 已写入。
- 新增数据目录：`.traceguard/run008/`；正式证据目录：`docs/assets/run008/`（旧证据一律保留，不覆盖）。
- 当前任务与进度：见 `.workbuddy-run/current-task.md` 与 `.workbuddy-run/progress.md` 的 RUN-008 段（WP8 收尾时更新）。

### RUN-008 必守要点

- 两个任务书 P0 缺陷在起点 HEAD 的代码中**已经修复**：`probe_budget_multiprocess.py` 在模块顶层扫描
  `packages/`/`apps/` 注入 `sys.path`（spawn 子进程同样执行）；`soak_run007.py` 的 `teamflow_ok`
  已改为真实 `GET /api/state/projects` 且要求 `ok is True` + 有 `count`。必须以真实执行验证，不重复改坏。
- 任何「通过」必须有本轮真实执行证据；skip、BLOCKED、历史证据、CI 文件存在都不等于通过。
- 长稳实验先检查 `docs/assets/run008/soak-60min.json` 是否已存在有效本轮报告，避免重复启动；
  任何中断保留原始采样，重跑必须完整 60 分钟。
- 不覆盖 `docs/assets/run007/` 下任何证据；不删除保护目录；不 reset/clean/强推/改写历史。
- 不输出 `.env`、Token、密码；不访问未授权外部站点；不创建正式 Release tag。

### RUN-008 安全拦截与恢复状态（2026-11-24 13:22 CST）

- **本轮安全事件**：`Write` 创建 `scripts/worker_fault_matrix_run008.py`
  被 `info_theft_steal_env_keys` 拦截。根因是源码 3 处 `**os.environ` 整体复制进
  `subprocess.Popen(env=...)`（子进程同时访问本地服务），并非硬编码密钥。
  文件**未创建**，无真实凭据被读取/打印/上传。详见 `.workbuddy-run/blockers.md` B-17。
- **处理决定**：不重试、不改参数规避、不改安全策略；补充故障矩阵 4 场景标记 BLOCKED 并跳过。
  第二次以「显式非敏感白名单环境」（无 `**os.environ`、无真实凭据）重写仍被同一策略拦截，
  按指令停止尝试；后续会话不要重试该脚本的写入，直接沿用 BLOCKED 结论。
- **Git 现状（2026-11-24 13:53 CST 刷新）**：分支 `workbuddy/run008-quality-hardening`，
  HEAD `49d700e`，相对 `origin/main` 领先 27 提交。已推送到 `f68298a`（CI 基于 `40efee6`
  三段全绿；后续 `790bad3`/`2e32594`/`f68298a`/`49d700e` 是文档提交，待收尾推送）。
  未提交项**必须保留**：`docs/assets/run008/soak-samples.jsonl`、`docs/assets/run008/wp7/`、
  长稳最终报告（生成后）。**禁止 reset/clean/强推**。
- **后台任务现状（不要重复启动）**：
  - `BZ1Hp8`：60 分钟长稳 running；采样 cycle 46/60，47 行全部 PASSED/COMPLETED/teamflow_ok=true、
    单调唯一；`docs/assets/run008/soak-60min.json` 尚未生成。结束后直接归档，不重跑。
  - `3TCEEN`：Benchmark v3 本轮实验 running；`samples.jsonl` 38 行 + `samples.repeat1.jsonl` 约 28 行；
    `benchmark-v3.json` 尚未生成。结束后核验 2 轮真实数据，不引用历史数字。
  - 服务：`8041`(长稳 TeamFlow)/`8042`(长稳 API)/`8043`(WP3 TeamFlow)/`8050`+`8000`+`5173`(WP5)/
    `8061`+`8060`+Worker `run008-bench-w1`(WP7)。均为隔离端口，勿重启、勿占用。
- **已完成工作包**：WP0、WP1、WP2（本机 567 passed/13 skipped；E2E 12/12；
  PR `45335765297` / Nightly `45335777381` / Release `45336007166` 在 commit `40efee6` 全绿）、
  WP3 核心、WP5（60/60 + 4/4）、WP6（2 个真实安全修复 + 36/36）。
- **未完成**：WP3 补充矩阵（B-17 BLOCKED，勿重试）、WP4 长稳收尾、WP7 Benchmark 收尾、
  WP8 最终报告定稿与末次推送。
- **恢复第一步**：检查 `BZ1Hp8` 与 `3TCEEN` 是否结束 → 读取各自最终 JSON → 完成
  `docs/RUN-008-FINAL-REPORT.md` 中 WP4/WP7 章节 → git add/commit/push → 收尾服务。
  全程不需要也不得读取真实凭据。

### RUN-008 完成状态（2026-11-24 14:10 CST，终稿）

- 最终 HEAD：`804fc5d`，已推送远程；工作区干净；相对 `origin/main` 领先 31 提交。
- WP0/WP1/WP2/WP4/WP5/WP6/WP7/WP8 完成；WP3 核心完成、补充矩阵 BLOCKED（B-17）。
- 长稳：60.14 分钟 / 66 周期 / 23 checks 全真，`docs/assets/run008/soak-60min.json`。
- Benchmark：本轮 2 轮 × 30 场景真实执行（608.5s），报告 `docs/RUN-008-BENCHMARK-REPORT.md`；
  LLM/人工基线 BLOCKED，未伪造。
- CI（代码提交 `40efee6`）：PR `45335765297` / Nightly `45335777381` /
  Release `45336007166` 全 success；LLM smoke 缺 Secret BLOCKED。
- 已停止本轮新增的隔离服务：5173/8000/8050（WP5）、8060/8061 + Worker（WP7）、8043（WP3）。
  保留 8041/8042（长稳启动前既存，可能共享）。后续会话不要假定这些端口可用，需重新检查。
- 最终报告：`docs/RUN-008-FINAL-REPORT.md`；决策：`docs/RUN-008-DECISIONS.md`。

## RUN-010-FINAL-CLOSEOUT 断点续跑（2026-11-25 00:30 CST）

- 分支 `workbuddy/run008-quality-hardening`；代码 HEAD 基线 `d4d59ba`（CL-01/CL-02 修复），
  文档提交后 HEAD 前进一个 docs-only 提交；远程同步 `origin/workbuddy/run008-quality-hardening`。
- 已完成的验收（可直接引用，不必重跑）：
  - PR Gate `45433891889`(push) / `45433916576`(PR) success；
  - Nightly `45433925669` success 5/5（backend-full 644 passed / 13 skipped，2m29s）；
  - Release `45434549738` success 4/4（full-verification 644 passed / 13 skipped，`NIGHTLY_OK` 匹配 `d4d59ba`）。
- 本机复现/定位要点（后续若要再查同类挂死）：
  - `pytest -q` 默认 basetemp `.pytest-tmp/run` 会被沙箱安全删除保护拦截 → 用 `--basetemp=/tmp/xxx`；
  - 抓解释器退出期线程栈：写 pytest 插件在 `pytest_unconfigure` 里
    `faulthandler.dump_traceback_later(6, repeat=True, file=<真实文件对象>)`（必须传有 fileno 的文件）；
  - macOS 上 `py-spy` 需要 root，`sample` 只能看到 C 栈。
- 若重跑 CI：推送后等 PR Gate 自动跑；Nightly 需 `workflow_dispatch`；
  **Release 依赖同 SHA 的成功 Nightly（attestation 严格匹配）**，顺序不能颠倒。
- 仍未解除：B-17（需安全批准）、宿主级网络隔离（需 pf/容器权限）、Benchmark LLM 臂/人工基线、
  LICENSE（NEEDS_DECISION）、Benchmark 5 个场景缺口。
- 未做且不得冒充：生产环境部署验证、PG 生产参数验证、无障碍键盘/读屏/动效覆盖。

> **RUN-011 恢复摘要（2026-11-25 02:25 CST）**：分支 `workbuddy/run011-full-loop`（从
> `b470518` 创建）。两轮独立部署闭环证据：`docs/assets/run011/full-loop/loop{1,2}.json`
> + `loop{1,2}-db.json`（各 62/62、8/8）。探针：`docs/assets/run011/wp2/probe-lab.json`（10/10）。
> 网络负向：`docs/assets/run011/wp7/network-negative*.json`。PG 备份恢复：`docs/assets/run011/wp6/`。
> 恢复时先读 `docs/RUN-011-FINAL-REPORT.md` → `RUN-011-BASELINE.md` → 各 WP 报告。
> 不要重跑已完成的闭环（会覆盖同路径证据）；如需重跑，先把旧 JSON 改名归档。
> 未完成：Benchmark v5（NOT RUN）、B-17（BLOCKED）、UI 全链路（NOT RUN）、CI（BLOCKED）。
> 复现命令见 `docs/RUN-011-FINAL-REPORT.md` §7 与 `docs/RUN-011-WP9-OPERATIONS.md` §1。
