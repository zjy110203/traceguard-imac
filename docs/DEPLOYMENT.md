# 安装、启动与部署指南

## 1. 一键环境准备（macOS / Linux）

```bash
bash scripts/bootstrap.sh
```

脚本做四件事：创建 `.venv`、安装 Python 依赖、安装 Playwright + Chromium、生成包路径 `.pth` 并构建前端。

### 手动步骤

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt
.venv/bin/pip install playwright
.venv/bin/playwright install chromium
# 国内网络慢时：
# PLAYWRIGHT_DOWNLOAD_HOST=https://cdn.npmmirror.com/binaries/playwright .venv/bin/playwright install chromium
```

包路径：仓库采用多包布局（`packages/*`、`apps/*`、`benchmark`），需要把它们加入 `sys.path`。
`bootstrap.sh` 会写入 `.venv/.../site-packages/traceguard.pth`；也可以直接设置：

```bash
export PYTHONPATH="$PWD/packages/core:$PWD/packages/contracts:$PWD/packages/execution:$PWD/packages/agents:$PWD/packages/evidence:$PWD/packages/persistence:$PWD/apps/api:$PWD/apps/teamflow:$PWD/apps/workers:$PWD/benchmark"
```

## 2. 启动（单机模式，推荐）

```bash
# 终端 1：被测应用
bash scripts/run_teamflow.sh          # 127.0.0.1:8001

# 终端 2：平台（内嵌 Worker）
bash scripts/run_api.sh               # 127.0.0.1:8000
```

打开 `http://127.0.0.1:8000/`（API 直接托管前端构建产物）。首次启动会自动：
建表 → 迁移 → 写入 TeamFlow 演示资产（4 需求 / 4 用例 / 5 Oracle）→ 启动内嵌 Worker。

验证：

```bash
curl -s http://127.0.0.1:8000/api/v1/system/health | jq
curl -s "http://127.0.0.1:8000/api/v1/system/browser?probe=true" | jq '.available,.channel,.version'
```

## 3. 分离 Worker 模式

```bash
TG_INLINE_WORKER=0 bash scripts/run_api.sh       # API 只入队
bash scripts/run_worker.sh                       # Worker 消费（可多实例）
bash scripts/run_worker.sh --once                # 执行一个任务后退出（CI 用）
```

多个 Worker 之间通过租约 + fencing token 互斥；崩溃后租约过期即可被其它 Worker 回收。

## 4. 前端开发模式

```bash
cd apps/web && npm install && npm run dev   # 127.0.0.1:5173，/api 代理到 8000
```

生产构建：`npm run build`（`vue-tsc --noEmit && vite build`），产物由 API 托管。

## 5. 配置项（全部 `TG_` 前缀，可用 `.env`）

| 变量 | 默认 | 说明 |
|---|---|---|
| `TG_DATABASE_URL` | `sqlite:///./.traceguard/traceguard.db` | 可换 PostgreSQL DSN |
| `TG_DB_ECHO` | `false` | SQL 日志 |
| `TG_API_HOST` / `TG_API_PORT` | 127.0.0.1 / 8000 | 仅脚本读取 |
| `TG_TEAMFLOW_BASE_URL` | `http://127.0.0.1:8001` | 被测应用地址 |
| `TG_BROWSER_HEADLESS` | `true` | 有头调试设 0 |
| `TG_BROWSER_CHANNEL` | 空（内置 Chromium） | `msedge` / `chrome` |
| `TG_BROWSER_TIMEOUT_MS` | 8000 | 元素操作超时 |
| `TG_RUN_MAX_DURATION_S` | 180 | 单 Run 总时长上限 |
| `TG_ARTIFACT_DIR` | `.traceguard/artifacts` | 产物目录 |
| `TG_INLINE_WORKER` | `true` | 单机模式 |
| `TG_WORKER_ID` | host-pid | 固定 Worker 标识 |
| `TG_WORKER_LEASE_TTL_S` | 120 | 租约时长 |
| `TG_ALLOWED_ORIGINS` | `["http://127.0.0.1:5173"]` | CORS |
| `TG_LOG_JSON` / `TG_LOG_LEVEL` | true / INFO | 日志 |

## 6. PostgreSQL / Redis / MinIO

`infra/docker-compose.yml` 提供三个依赖服务（需 Docker）。当前**未在本机验证**：

- PostgreSQL：Schema 用 Atlas 通用类型，迁移有方言分支，但未运行过 PG 实例；
  切换只需 `TG_DATABASE_URL=postgresql+psycopg://...`（需安装 `psycopg`）。
- Redis：**未使用**。队列由数据库实现，不需要 Redis；接入 Celery/Redis 不是当前架构必需。
- MinIO：**未使用**。产物为本地内容寻址存储；S3 适配器尚未实现。

不要把这三个服务当作已完成能力。生产化清单见 `docs/FEATURE-STATUS.md`。

## 7. 目录与数据

| 路径 | 内容 | 是否入库 |
|---|---|---|
| `.traceguard/traceguard.db` | 业务/事件/证据/目录 | 否（运行时数据） |
| `.traceguard/artifacts/` | 截图/DOM/ARIA/trace | 否 |
| `logs/` | 服务日志 | 否 |
| `.workbuddy-run/` | 长期运行记录（恢复用） | 是 |
| `docs/` | 设计/交付文档 | 是 |

## 8. 常见问题

| 现象 | 原因 | 处理 |
|---|---|---|
| `BrowserType.launch: Chromium distribution 'chrome' is not found` | 系统 Chrome 是空壳 | 用内置 Chromium（默认）或 `TG_BROWSER_CHANNEL=msedge` |
| `Sync API inside the asyncio loop` | 在事件循环线程直接调用 Playwright | 已在线程池执行；自定义调用方需注意 |
| 执行一直 QUEUED | 未启动 Worker | 打开 `TG_INLINE_WORKER=1` 或启动 `run_worker.sh` |
| `TESTCASE_NOT_APPROVED` | 用例未审核 | 在用例详情审核通过 |
| `ORACLE_UNAVAILABLE` → NOT_EVALUATED | Oracle 缺失/未审批 | 创建并审批 Oracle 后重新执行 |
| 端口占用 | 8000/8001 被旧进程占用 | `lsof -i:8000` 后停止旧进程 |
