# 中断恢复指引（RUN-006 结束点）

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
