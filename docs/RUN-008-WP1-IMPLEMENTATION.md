# RUN-008 WP1 实施报告：P0 问题修复与回归

起点 HEAD：`ce1acea5d3778e9518c84f8c4e95496984a4dcdc`
分支：`workbuddy/run008-quality-hardening`
环境：iMac macOS 15（darwin）、Python 3.13.12、Node 22.22.2、Playwright 自带 Chromium。

## 1. 审计结论：任务书两项 P0 在本轮起点已修复，不重复改写

任务书描述的 Nightly 失败（`ModuleNotFoundError: No module named 'traceguard_api'`，512 passed / 3 failed / 5 skipped）对应较早基线。当前 HEAD 的实际代码为：

- `scripts/probe_budget_multiprocess.py:29-39`：模块顶层扫描 `packages/`、`apps/` 全部子目录并注入 `sys.path`。`multiprocessing.spawn` 子进程在导入该脚本为 `__main__` 时执行同样代码，因此独立进程也能解析 `traceguard_api`、`traceguard_persistence`、`traceguard_evidence`。这是脚本层修复，不依赖 pytest 的 `pythonpath`。
- `tests/integration/test_probe_budget_multiprocess.py`：三项测试仍为真实 `subprocess` + `spawn` 独立进程，保留 SIGKILL 与关闭预算场景，未改为线程或 Mock。
- `scripts/soak_run007.py`：旧 `teamflow_ok = ... or True` 已被 `GET /api/state/projects` 真实探测替代（`ok is True` 且含 `count`），失败写入 `teamflow_error` 并进入最终报告错误列表。

处理方式：**保留既有修复，用真实执行验证**，而不是重写已正确的代码。

## 2. 审计中新发现并修复的缺陷

在 `scripts/soak_run007.py` 中发现任务书要求检查但尚未完全解决的问题：

| # | 原问题 | 修复 |
|---|---|---|
| 1 | `checks["api_inline_worker_off"] = True` 是恒真条件 | 每周期记录 `api_health.inline_running` 与 `inline_worker_seen`，改为 `inline_worker_seen == 0`；新增 `api_health_ok_all_cycles` |
| 2 | `_get()` 吞掉异常返回 `{"__error__": ...}`，API 健康失败不会计入 `errors`，最终报告可能全绿 | 新增 `_is_transport_error`、`_teamflow_health`、`_api_health`；传输错误、状态非 ok、缺 Worker 字段均计为失败并写报告 |
| 3 | `_post()` 的 HTTP 错误和业务 `FAILED` 混在一起 | 错误包带异常类型（`HTTPError`/`URLError`/超时）；业务 FAILED 原样返回，由调用方按字段判定 |
| 4 | SQLite 只读连接失败被写成 `ERR:*` 字符串但不影响验收 | 新增 `_has_db_errors`，`db_counts_readable` 明确失败；`integrity.db_path` 与本次实例比对，新增 `db_counts_from_current_instance` |
| 5 | 采样文件用 `"w"` 覆盖；中断后无法恢复，报告文件也可被静默覆盖 | 新增 `--resume-samples`：`read_samples_tail()` 读取最后一行续跑；未指定时若采样/报告已存在直接拒绝（exit 2），不覆盖证据 |
| 6 | 进程指标来源未汇总校验 | 新增 `metric_sources` 集合与 `process_metrics_source_recorded`；`ps` 被沙箱禁止时仍为 `liveness_only`，不伪装为 0 |
| 7 | 报告硬编码 `round=RUN-007-RESUME`、默认路径指向 run007 | 新增 `--round`，RUN-008 使用 `docs/assets/run008/` 与 `.traceguard/run008/` |

所有验收条件由纯函数 `build_checks(...)` 生成，便于自动化验证不存在恒真项。

## 3. 新增测试

新增 `tests/integration/test_soak_run007_guards.py`（13 项）：

- TeamFlow 健康判定 6 组参数化：`ok is True` + `count` 才健康；`ok:false`、缺 `count`、错误包、非字典均不健康。
- `_get` 网络异常返回可识别错误包；`_post` 区分 HTTPError 与业务 FAILED。
- `_api_health` 识别传输错误与内嵌 Worker。
- SQLite 损坏库/缺表 → `_has_db_errors=True`；正常库计数为 0 且 `db_path` 正确。
- `build_checks` 健康输入全绿；注入 API 健康失败、内嵌 Worker、TeamFlow 异常、DB 读错、错误实例、未知指标来源后对应 6 项条件全部转 false。
- 采样续跑读取与损坏行拒绝；AST 断言脚本中不存在 `or True` 且 `api_inline_worker_off` 非常量。
- 测试以 `importlib` 加载脚本，不执行 `main()`，无副作用。

## 4. 真实执行证据

### 4.1 多进程预算（macOS，直接运行脚本，非 pytest）

```
.venv/bin/python scripts/probe_budget_multiprocess.py --db .traceguard/run008/wp1-normal.db \
  --scope run008-normal --procs 4 --budget 20 --attempts 20 \
  --out docs/assets/run008/wp1/probe-budget-normal.json
# 同样方式执行 --kill-one 与 --close-first
```

| 场景 | 结果 | 证据 |
|---|---|---|
| 正常 | `reserved_total=20`、`final_probes_used=20`、`no_overdraft=true`、4 个独立 PID（71025–71028） | `docs/assets/run008/wp1/probe-budget-normal.json` |
| SIGKILL | `killed_pid=71091`，存活子进程 3 个（71093/71094/71096），预算保持 20 未重置，无子进程错误 | `docs/assets/run008/wp1/probe-budget-kill.json` |
| 关闭预算 | `reserved_total=0`、全部子进程拒绝、`final_probes_used=0` | `docs/assets/run008/wp1/probe-budget-close.json` |

### 4.2 pytest 与 lint

```
.venv/bin/python -m pytest tests/integration/test_probe_budget_multiprocess.py -q   → 3 passed / 7.25s
.venv/bin/python -m pytest tests/integration/test_soak_run007_guards.py -q           → 13 passed / 0.37s
合并执行                                                                            → 16 passed / 7.97s
.venv/bin/ruff check scripts/soak_run007.py tests/integration/test_soak_run007_guards.py … → All checks passed
.venv/bin/ruff format scripts/soak_run007.py tests/integration/test_soak_run007_guards.py
```

原始日志：`.pytest-tmp/run008-wp1-final.log`、`.pytest-tmp/run008-wp1-direct-*.log`。

### 4.3 长稳脚本短时冒烟（完整 60 分钟留待 WP4）

```
.venv/bin/python scripts/soak_run007.py --teamflow http://127.0.0.1:8021 --port 8032 \
  --minutes 0.5 --interval 20 --root .traceguard/run008/smoke2 \
  --out docs/assets/run008/wp1/soak-smoke2.json \
  --samples docs/assets/run008/wp1/soak-smoke2.jsonl --round RUN-008-SMOKE2
```

真实 API + 4 个独立 Worker + Chromium + TeamFlow，2 周期全通过、SSE 收到事件且续传成功、22 项 checks 全 true。第一次冒烟（`soak-smoke.json`）抓到 `db_counts_from_current_instance=false`，修复后第二次冒烟通过；两份原始报告均保留，不删除失败证据。

## 5. 尚未完成/保留口径

- Ubuntu CI 环境验证：待本分支推送后由 WP2 的真实 GitHub Actions 执行，不在本地伪造。
- 60 分钟真实长稳：WP4 独立执行；本轮冒烟仅为脚本正确性验证，不得计为长稳通过。
- `ps` 在此沙箱返回 `operation not permitted`、无 `/proc`，资源指标继续标 `liveness_only`；不得写成 CPU/RSS 为 0。
- 任务书 1.2 中 `_get`/`_post`、SQLite 只读、计数来源、采样恢复、进程指标各项均已在本节覆盖并有测试。
