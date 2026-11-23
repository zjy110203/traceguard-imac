# 中断恢复指引（RUN-005 结束点）

> 下一次运行的第一步：读本文件 → `docs/RUN-005-BASELINE.md` → `docs/FEATURE-STATUS.md`
> → `.workbuddy-run/progress.md` → `docs/TraceGuard-持续开发任务台账.md`。
> 不要从零重新开发。不要把 BLOCKED 写成完成。

- 最后保存：2026-11-23（RUN-005-CORE）
- 分支：`workbuddy/traceguard-autonomous`（本轮未推送远程）
- 本轮基线提交：`9d751fe`（RUN-004-FE）

## 当前快照

- 测试：**285 passed / 5 skipped / 0 failed**（skip 全部是 PG parity，本机无 PG）
- Ruff：`check` 全绿；`format` 已格式化本轮改动文件
- 前端：`vue-tsc` + `vite build` 通过；真实浏览器 UI 验收 **30/30**（`docs/assets/run005/`）
- 本轮已修复的四个 P0 可靠性缺陷：探针预算全局化、Active Planner 未闭环、伪 SSE、Inbox 单状态；
  另修复 Event Store 多进程序号竞争
- 本轮新增：LLM 编译/探针/修复三类顾问（真实冒烟通过）、候选稳定性回放、CI 三级门禁、
  Run 时间线投影（Inbox 真实消费者）

## 环境

| 项 | 值 |
|---|---|
| Python | 3.13.12（.venv） |
| Node | 22.22.2 |
| playwright | 1.65.2 + Chromium 1255 |
| 数据库 | SQLite（迁移 **v6**）；隔离验收库 `.traceguard/run005/` |
| PostgreSQL | 本机不可用（无 Docker/psql）→ parity 测试 skip，CI Nightly 执行 |
| LLM | `.env` 内已配置 DeepSeek（本轮真实调用过 compiler/probe/repair 三类顾问） |

## 恢复后立即可做的验证

```bash
# 1) 单元 + 集成（约 25s）
.venv/bin/python -m pytest -q

# 2) 真实服务（隔离库，勿用默认库做演示）
TG_TEAMFLOW_DB="sqlite:///./.traceguard/run005/teamflow.db" \
  .venv/bin/python -m uvicorn traceguard_teamflow.main:app --port 8011 &
TG_DATABASE_URL="sqlite:///./.traceguard/run005/api.db" \
TG_ARTIFACT_DIR=".traceguard/run005/artifacts" TG_TEAMFLOW_BASE_URL="http://127.0.0.1:8011" \
TG_INLINE_WORKER=0 \
  .venv/bin/python -m uvicorn traceguard_api.main:app --port 8010 &

# 3) UI 验收（需前端已 build）
.venv/bin/python scripts/ui_acceptance.py --base-url http://127.0.0.1:8010

# 4) 三类 LLM 顾问真实冒烟（有凭据时）
TG_API_URL=http://127.0.0.1:8010 .venv/bin/python scripts/llm_advisor_smoke.py
```

## 下一步（按阻塞解除顺序）

1. **Benchmark v3 多臂对照**（A/C 先行，无 LLM 成本；LLM 臂按日预算分批）——见 `RUN-005-BENCHMARK-REPORT.md` 第 4 节。
2. **PostgreSQL**：在 CI 手动触发 Nightly（Service Container 已配置）；本地不装 PG。
3. **60 分钟长稳 + 4 Worker 故障恢复**：需独立环境与时间窗，禁止用短测冒充。
4. **CI 首次真实运行**：Nightly security job 会因 `vite` dev-only high 漏洞失败，需先决定
   "迁移 vite 8" 或 "显式豁免 + 期限"（见 `RUN-005-SECURITY-RELIABILITY.md` 第 2 节）。
5. **P03 / P08 决策后落地**：Oracle 冲突规则 / Repair RBAC。
6. **LLM 编译完整链路**：真实探索 → LLM 编译 → 稳定性回放（多次浏览器）→ 人工审核。

## 本轮关键记忆

- 预算 scope 是 `run:<id>` / `diagnosis:<id>`，`started_at` 首次使用时确定；`CLOSED` 是终态，不会被重新打开。
- 事件序号由 `tg_producer_sequence` + `ON CONFLICT … RETURNING` 原子分配；跳号合法，不判丢失。
- SSE 游标 = `pi/seq`（来源）+ `at/eid`（追加顺序）；只承诺 at-least-once，不承诺全局顺序。
- Inbox `UNKNOWN` = 外部副作用不确定，禁止盲目重试；`consume_in_transaction` 才是副作用+记录同事务。
- LLM 三条路径只产候选：`oracle_id` 恒为 None，`review_status` 恒 DRAFT，失败即 fallback 并标记。
- 验收必须用隔离库；`scripts/final_demo.py` 仍禁止对现有库运行。

## 不要做的事

- 不要删除 `.workbuddy-run/` 与 `.traceguard/`。
- 不要 `git reset --hard` / `git clean` / 强推。
- 不要访问外部 Web 应用（P-EXT 未授权）。
- 不要声称 P03/P08 已决策、路线图已冻结。
- 不要把 skip 当 passed，不要把 BLOCKED 写成成功，不要为过测试削弱断言。
