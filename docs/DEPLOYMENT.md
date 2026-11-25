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

`infra/docker-compose.yml` 提供三个依赖服务（需 Docker）。

- PostgreSQL：**已在真实 PostgreSQL 16.16 上验证**（RUN-012 专项 16 passed、RUN-013 60 分钟长稳）；
  切换只需 `TG_DATABASE_URL=postgresql+psycopg://...`（需安装 `psycopg`）。
  迁移由 `Database.create_schema()` 内的 `tg_schema_migration` 版本表幂等执行，启动即可完成。
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


## 9. 运维手册（RUN-013）

### 9.1 进程拓扑

| 进程 | 启动命令 | 必需 | 说明 |
|---|---|---|---|
| TeamFlow（被测目标） | `bash scripts/run_teamflow.sh` | 执行/回放需要 | 故障注入与演示站点 |
| API | `TG_INLINE_WORKER=0 bash scripts/run_api.sh` | 是 | 只入队、查询、SSE |
| Worker 1..N | `bash scripts/run_worker.sh` | 是（分离模式） | 执行 Run；可水平扩展 |
| PostgreSQL | 外部服务 | 生产推荐 | 独立数据库与备份策略 |

单机演示可保留 `TG_INLINE_WORKER=1`；生产/验收必须显式设为 `0`，
并用 `/api/v1/system/config` 确认 `worker.inline=false`。

### 9.2 健康检查

```bash
curl -fsS http://127.0.0.1:8000/health                  # 存活
curl -fsS http://127.0.0.1:8000/health/ready            # 数据库就绪 + worker 信息
curl -fsS http://127.0.0.1:8000/api/v1/system/health    # 业务/进程计数（需登录）
```

`/api/v1/system/health` 返回：

- `database.ok`：数据库可用；
- `process.rss_bytes` / `cpu_user_s` / `cpu_sys_s`：API 进程资源；
- `counts`：按调用方 Workspace 授权范围统计（platform-admin 为全局）；
- `worker.inline_running`：是否误开内嵌 Worker。

Worker 资源：设置 `TG_WORKER_METRICS_DIR=/var/lib/traceguard/metrics`，
每个 Worker 原子写入 `worker-<id>.json`（RSS/CPU），供监控采集；不设置则不写。

### 9.3 优雅退出与故障恢复

- Worker 收到 SIGTERM 后完成当前 Run 再退出；`SIGKILL` 后租约到期（默认 120s）由其他 Worker 接管，
  `fencing_token` 递增，旧 Worker 的提交会被拒绝。
- API 重启不影响已入队 Run：Worker 直接读数据库继续消费；客户端重连后回读终态。
- 数据库连接中断：SQLAlchemy 连接池失效重连；API/Worker 不需重启（RUN-013 WP4 实测）。
- SSE 断线：浏览器带 `Last-Event-ID` 自动续传；服务端按 Workspace 授权过滤，越权游标不会返回数据。

### 9.4 备份与恢复

PostgreSQL：

```bash
pg_dump --format=custom --file=traceguard-$(date +%F).dump "$TG_DATABASE_URL"
pg_restore --clean --if-exists --dbname="$TG_DATABASE_URL" traceguard-2026-11-25.dump
```

SQLite（先 checkpoint，避免 WAL 未落盘）：

```bash
sqlite3 .traceguard/traceguard.db "PRAGMA wal_checkpoint(TRUNCATE);"
cp .traceguard/traceguard.db backup/traceguard-$(date +%F).db
```

Artifact 目录必须与数据库一起备份（证据记录引用内容寻址文件）；只恢复数据库会导致证据下载 404。

### 9.5 迁移与升级

1. 停 Worker（API 可保持只读）；
2. 备份数据库与 Artifact；
3. 启动新版本 API，`create_schema()` 按 `tg_schema_migration` 顺序幂等执行；
4. 启动 Worker；
5. 用 `/health/ready` 与一条真实用例冒烟。

迁移失败时保留原库并回滚版本，不要手工删除迁移记录。

### 9.6 日志与排查

- 结构化日志默认 JSON（`TG_LOG_JSON=true`），关键事件：
  `worker_started` / `run_claimed` / `run_finished` / `lease_lost_abandon` /
  `submit_rejected_stale` / `sse_poll_failed` / `worker_loop_error`。
- 队列积压：`GET /api/v1/queue/stats`（按状态聚合）；确认 Worker 存活与租约 TTL。
- 任务永久 RUNNING：检查 `tg_run.lease_expires_at`，等待 TTL 或重启 Worker 触发 reap。
- 证据缺失：检查 `TG_ARTIFACT_DIR` 权限与磁盘空间；`/api/v1/evidence/{id}/verify` 可重算完整性。

### 9.7 生产检查清单

- [ ] `TG_INLINE_WORKER=0`，至少 2 个 Worker 进程；
- [ ] `TG_AUTH_ENABLED=true`，Cookie Secure（HTTPS 环境）与强密码；
- [ ] PostgreSQL 独立实例 + 每日备份 + Artifact 同步备份；
- [ ] `TG_ARTIFACT_DIR` 独立磁盘/卷，容量告警；
- [ ] 反向代理设置 SSE 不缓冲（`X-Accel-Buffering: no` 已由服务端返回）；
- [ ] 监控 `/health/ready`、API 延迟、队列深度、Worker 存活、DB 大小/WAL；
- [ ] 日志脱敏检查（不得出现 API Key、Cookie、密码）。
