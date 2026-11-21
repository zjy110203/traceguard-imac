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
| 前端 build | `npm run build` / `vite build` | 进行中（环境对 node 构建进程有限制，见下） | 待回填 |

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

### 环境限制（真实）
- node 构建进程（vite/vue-tsc）在本沙箱被 SIGTERM（Exit 137），前台与后台均出现；疑似沙箱对 node 子进程资源/时间限制，非代码错误。正在以后台日志方式确认。
- mypy、httpx2 因 PyPI 下载极慢（25~45 kB/s）未装完；不阻塞阶段0，CI（ubuntu）会安装。
- 集成测试采用直接调用路由函数，规避 TestClient 对 httpx2 的依赖；真实 HTTP 已用 curl 单独验证。
