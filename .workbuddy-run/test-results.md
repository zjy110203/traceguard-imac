# 测试结果汇总

- 更新：2026-11-21 23:45

> 所有数字必须来自真实测试日志。禁止编造。禁止 Mock 冒充真实运行。
> 日志存放：`tests/reports/`、`logs/`（不入库大文件）。

## 环境预检（真实执行）

| 检查项 | 命令 | 结果 |
|---|---|---|
| Python | `python3 --version` | Python 3.13.12 ✅ |
| Node | `node --version` | v22.22.2 ✅ |
| npm | `npm --version` | 10.9.7 ✅ |
| Git | `git --version` | 2.39.5 ✅ |
| Docker | `docker --version` | 不可用（记录为限制） |
| psql | `psql --version` | 不可用（用 SQLite 抽象降级） |
| uv / poetry | 版本检查 | 不可用（用 venv+pip） |
| PyPI | `pip index versions fastapi` | 可达，fastapi 最新 0.165.0 ✅ |
| npm registry | `npm view vue version` | 可达，vue 3.5.46 ✅ |

## 阶段 0 测试结果（真实执行，2026-11-21）

| 测试项 | 命令 | 结果 | 证据 |
|---|---|---|---|
| 单元+契约+集成 | `.venv/bin/pytest tests/unit tests/contracts tests/integration -q` | **22 passed** (0.40s) | 本文件下方粘贴输出 |
| Lint | `.venv/bin/ruff check packages apps tests` | All checks passed | 19 项自动修复后 0 错误 |
| Format | `.venv/bin/ruff format --check packages apps tests` | 通过（格式化后） | 6 文件已格式化 |
| C01-C12 Schema 导出 | `python packages/contracts/export_schemas.py` | 12 契约 / 22 Schema | manifest.json |
| API 健康检查（真实 HTTP） | uvicorn + `curl /health` | `{"status":"ok","app":"TraceGuard",...}` | 真实运行，非 Mock |
| API ready（真实 HTTP） | `curl /health/ready` | `{"ready":true,...}` 且配置无敏感泄露 | 同上 |
| 前端 build（含类型检查） | `npm run build`（vue-tsc --noEmit && vite build） | **通过**：13 模块，exit 0，2 秒，产出 dist/ | 已回填（2026-11-22 修正） |

### pytest 真实输出（阶段0）
```
$ .venv/bin/pytest tests/unit tests/contracts tests/integration -q
......................                                                   [100%]
22 passed in 0.40s
```

### 测试中发现并修复的真实缺陷（非 Mock）
1. `Settings.public_dict()` 未遮蔽 DSN 内联口令 → 已修复（正则遮蔽 `scheme://user:***@host`），并补测试。
2. `OracleSpec.approved_by_agent` 原为普通 bool（默认 False 但可设为 True）→ 改为 `Literal[False]` 从契约层强制"Agent 不得批准正式 Oracle"。
3. 契约测试构造 OracleSpec 漏 `version` 必填字段 → 修正测试数据。
以上均来自真实测试暴露，修复后 22/22 通过。

### 阶段 1 测试结果（真实执行）

| 测试项 | 命令 | 结果 |
|---|---|---|
| TeamFlow 测试 | `.venv/bin/pytest tests/teamflow -q` | **9 passed** (0.64s) |
| 全项目测试 | `pytest tests/unit tests/contracts tests/integration tests/teamflow -q` | **31 passed** (0.67s) |
| lint | `ruff check packages apps tests` | All checks passed |
| format | `ruff format packages apps tests` | 通过 |
| TeamFlow 真实 HTTP 业务闭环 | uvicorn :8011 + curl | 登录/create/独立state 全部正确返回 |
| TF-DB-001 假成功揭穿 | curl create(注入) + state | UI ok=true 但 state count 仍=1 ✅ |
| Ground Truth 隔离 | curl 无令牌读 GT | HTTP 403 ✅ |

### 阶段 2 测试结果（真实执行）

| 测试项 | 命令 | 结果 |
|---|---|---|
| Verdict 四态聚合 | `pytest tests/unit/test_verdict.py -q` | **13 passed** |
| Assertion Evaluator | `pytest tests/unit/test_assertions.py -q` | **6 passed** |
| E2E 确定性执行 | `pytest tests/e2e -q` | **4 passed**（真实 TeamFlow 子进程） |
| 全项目 | `pytest tests/unit tests/contracts tests/integration tests/teamflow tests/e2e -q` | **54 passed** (1.93s) |
| lint / format | `ruff check` + `ruff format` | All checks passed |
| 执行演示 | `python scripts/run_demo.py` | 14 用例跑完，报告 `tests/reports/stage2_demo_runs.json` |

演示报告 verdict 分布（真实）：PASSED×3 / FAILED×2 / INCONCLUSIVE×9。
INCONCLUSIVE 来自 HttpObserver 降级路径无法解析客户端 JS 渲染的列表 DOM（COUNT_MISSING），
这是诚实的不确定性，不伪造为 PASSED。TF-DB-001 假成功用例正确判 FAILED（Trusted Oracle 揭穿）。

### 阶段 3 测试结果（真实执行）

| 测试项 | 命令 | 结果 |
|---|---|---|
| Task/Run/Step 服务 | `pytest tests/unit/test_tasks.py -q` | **9 passed** |
| 全项目 | `pytest tests/unit tests/contracts tests/integration tests/teamflow tests/e2e -q` | **63 passed** (1.35s) |
| lint / format | `ruff check` + `ruff format` | All checks passed |
| 平台 API 真实 HTTP | uvicorn :8020 + curl | Task 创建/Run 创建/Task 汇总全部正确 |

阶段3 修复的真实问题：
1. 路径参数误用 Query() → FastAPI 集合期报错 → 修正。
2. pytest 遍历 routes 遇到 _IncludedRouter 无 path → 用 getattr 容错。
3. 测试幂等 key / step_id 固定导致持久化库跨运行污染 → 改唯一值。
4. 状态机约束生效（PREPARING 不能直达 FINALIZING）→ 测试改走合法路径 RUNNING。

### 阶段2 修复的真实 bug
1. 编排器在操作步骤"之前"查询业务状态，导致 reset 后旧状态使正常创建用例假 FAILED → 改为步骤"之后"查询（Trusted Oracle 正确时序）。
2. Assertion 的 `expected_absent` 语义判断写反 → 修正为"want 出现且 absent 不出现才 PASSED"。
3. ID 正则过严（纯 hex）导致可读 fixtures ID 校验失败 → 放宽为字母数字下划线，兼容两种格式。
4. `TestVerdict` 类名被 pytest 误收集 → 测试内用别名 Verdict 导入。

### 环境限制（真实）
- ~~node 构建进程被 SIGTERM~~ 已更正：前端构建初次因排队极慢被判超时，最终 `npm run build`（含 vue-tsc 类型检查）实测通过（exit 0，2 秒）。
- mypy、httpx2 未装完（PyPI 25~45 kB/s）；不阻塞。
- Playwright 包下载极慢（多次后台尝试累计 >25 分钟仍未完成）；真实浏览器 PoC 待包就绪。**PlaywrightObserver 代码已就绪但未在本机验证浏览器启动，不谎称已完成真实浏览器 E2E。**
- 本机阶段2 采用 HttpObserver（真实 HTTP）降级路径，报告明确标记 mode=http-fallback。

## RUN-002（2026-11-22）

```
.venv/bin/python -m pytest -q
206 passed in 19.8s

.venv/bin/ruff check packages apps tests scripts benchmark
All checks passed!

cd apps/web && npm run build
vue-tsc --noEmit && vite build → ✓ built（14 chunk）
```

新增：持久化 9、真实浏览器 E2E 3、API 集成 8、诊断规则 5、契约 Schema 防漂移 1。

真实浏览器执行（Chromium 159）：正常 PASSED / FalsePass FAILED / UI 缺失 FAILED / API500 FAILED。
独立 Worker：`run_179532554450234b50cc4c5e5` COMPLETED PASSED playwright（8 步 / 25 证据）。

Benchmark（真实）：
- B：A_raw 0.333 / C,D,E 1.000（3/3）；A false_pass 1/1，C/D/E 0/1。
- C：sandbox 1/1；assertion preservation 1/1 vs 无保护 0/1；regression 0/1。
- A：human 4/4 执行；compiler 2/2 执行；无可评估断言（Oracle 未绑定，契约要求）。

新增：租约心跳单测（续期 + 被接管检测）。全量 206 passed。

## 最终状态（2026-11-22 14:05）

- 全量：206 passed；ruff check/format 全绿；前端 vue-tsc + vite build 通过。
- 新库冷启动验证：`TG_DATABASE_URL=sqlite:////tmp/...` 启动 → 自动建表/迁移/种子（4 用例）
  → 真实执行 `tcase_tf_create_normal` = PASSED / playwright / 8 步。
- 端到端演示 `scripts/final_demo.py`：19 步全通过（执行/诊断/图/探针/自愈/晋升/回归/回滚/Benchmark）。

新增：Flaky 特征测试（同版本 PASSED/FAILED 历史 → flaky 标记，不作为通过依据）。

---

## RUN-005（2026-11-23）

- 全量：`285 passed, 5 skipped, 0 failed`（skip = PostgreSQL parity，本机无 PG）
- 新增专项：61 passed（预算 15 / Inbox 9 / 事件并发 4 / LLM 顾问 10 / 探针服务 6 / SSE 7 / 时间线 3 / 回放 7）
- 真实浏览器：3 个场景（PASSED/FAILED/FAILED）+ Benchmark v2 30/30
- UI 验收：30/30（`docs/assets/run005/ui-acceptance.json`）
- LLM 真实冒烟：compiler / probe_planner / repair_planner 各 1 次（`.workbuddy-run/llm-advisor-smoke.json`）
- 未执行：PG（BLOCKED）、60 分钟长稳（BLOCKED）、多臂 Benchmark（BLOCKED）

## RUN-006（2026-11-23）

| 命令 | 结果 | 备注 |
|---|---|---|
| `.venv/bin/python -m pytest -q` | **367 passed, 5 skipped, 0 failed**（30.0s） | skip 全部为 PG parity（无 `TG_TEST_POSTGRES_URL`） |
| `.venv/bin/python -m pytest tests/e2e -q` | **11 passed**（16.1s） | 真实 Chromium |
| `.venv/bin/ruff check packages apps tests` | All checks passed | — |
| `npm run typecheck` / `npm run build` | 通过 | 前端诊断视图改动 |
| `scripts/ui_acceptance.py` | **30/30 passed** | 4 断点无横向溢出；深色主题可读 |
| 真实环境（隔离库 `.traceguard/run006/`） | 正常 PASSED / TF-DB-001 FAILED / 诊断 CONFIRMED / probe-plan STOP_CONFIRMED(0 探针) / 真实 http_state 回灌使假设被排除 | `docs/assets/run006/*.json` |

未执行（不得视为通过）：PostgreSQL 运行时、4 Worker 故障恢复、60 分钟长稳、CI 真实 Actions、前端截图验收、Benchmark v3。

## RUN-007-RESUME（2026-11-23）

- 起点：515 passed / 5 skipped（`ac2876e`）
- 全量：**523 passed / 11 skipped / 0 failed，46.69s**（采集 534；11 skip = 5 PG parity + 6 PG platform，逐条打印原因）
- WP1 专项：**163 passed**（稳定性回放/门禁/崩溃恢复/CAS/主体校验/预算多进程/目标策略 DNS）
- 真实浏览器出站边界：**5 passed**（`tests/e2e/test_target_policy_browser.py`）
- 迁移 v8：4 passed（新文件；修复前并发用例失败 → 已修 `db.py`）
- E2E（独立 Worker）：**55 步 / 0 失败 / 59.4s**，5 Run 全由 w1–w4 领取，`sandbox_fencing_token=1`
- 长稳：**60.03 min，65/65 周期 PASSED，19/19 checks true**；SSE 65/65 有事件、续传 38+27、心跳 158、重复 event id 0
- 故障恢复：真实 SIGKILL（-9），`killed_after_s=3.02s`，接管 21.3s，同 Run `fencing_token` 1→2、`attempt` 不变，**17/17 checks**
- UI：**75/75 checks**（内容 0 失败、溢出 0、console 0、暗色未生效 0），79 张真实截图 + 四态
- Benchmark：30 场景 / 38 次执行 / 19 失败样本；A 3/19、C/D/E 10/19；假通过 A 4/4 → C/D/E 0/4；LLM Flash 8/19、Pro 5/8
- 静态：`ruff check` + `format --check` 全绿；`npm run build`（含 `vue-tsc`）通过；`npm audit` high 0 / critical 0（moderate 3，dev）

## RUN-007-RESUME CI 三级门禁（2026-11-23，真实运行）

- 同一 commit `cc3339d291cc6b0e328922934ec82f6b689f4769`：
  - PR Gate（push）[#45201761239](https://github.com/zjy110203/traceguard-imac/actions/runs/45201761239) success
  - PR Gate（pull_request）[#45201768385](https://github.com/zjy110203/traceguard-imac/actions/runs/45201768385) success
  - Nightly Gate [#45201774473](https://github.com/zjy110203/traceguard-imac/actions/runs/45201774473) success（5/5 job）
  - Release Gate [#45202185410](https://github.com/zjy110203/traceguard-imac/actions/runs/45202185410) success（4/4 job）
- Nightly `postgres` job：`postgres:16` → `SHOW server_version` = `16.16 (Debian 16.16-1.pgdg13+1)`，6 个平台用例全过。
- Release `nightly-attestation`：`NIGHTLY_OK id=45201774473 head_sha=cc3339d291cc...`（严格等于 `github.sha`）。
- **CI 首跑暴露 8 个本地掩盖的问题**，4 轮迭代修复；其中 2 个是产品缺陷：
  `claim_next_run.previous_status` 取到更新后的状态；`create_schema` 不重试 DDL 锁冲突。
- 迭代轮次：`d2e86ae`❌ → `7fdd2e9`❌ → `f00f149`❌ → `cc3339d`✅。证据：`docs/assets/run007/ci-gates.json`。
