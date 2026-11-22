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
205 passed in 19.8s

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

新增：租约心跳单测（续期 + 被接管检测）。全量 205 passed。
