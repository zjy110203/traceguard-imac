# TraceGuard 快速开始

> 需要 Python 3.11–3.13、Node 20+。所有命令在项目根目录执行。
> 完整部署与配置见 `docs/DEPLOYMENT.md`。

## 0. 一键准备

```bash
bash scripts/bootstrap.sh
```

## 1. 启动（单机模式）

```bash
# 终端 1：被测应用 TeamFlow
bash scripts/run_teamflow.sh            # http://127.0.0.1:8001

# 终端 2：TraceGuard API + 内嵌 Worker
bash scripts/run_api.sh                 # http://127.0.0.1:8000
```

首次启动自动建表、迁移、写入演示资产（4 需求 / 4 用例 / 5 Oracle）。

打开 <http://127.0.0.1:8000/>：
1. 总览 → 「执行全部已审核用例」
2. 点击任一 Run → 查看步骤截图 / 断言 / 证据 / 证据图
3. 失败的 Run → 「诊断」→ 查看假设、根因、处置
4. 修复中心 → 新建提案 → 规则校验 → 沙箱执行 → 审批 → 晋升
5. Benchmark → 运行实验 B（真实对照实验）

## 2. 命令行验证

```bash
curl -s http://127.0.0.1:8000/api/v1/system/health | jq
curl -s "http://127.0.0.1:8000/api/v1/system/browser?probe=true" | jq '.available,.channel,.version'

# 同步执行一个用例（真实浏览器）
curl -s -X POST http://127.0.0.1:8000/api/v1/testcases/tcase_tf_create_normal/execute \
  -H 'Content-Type: application/json' -d '{"sync": true}' | jq '.execution.test_verdict'

# 四场景脚本
.venv/bin/python scripts/smoke_playwright.py

# 端到端演示（执行→诊断→图→探针→自愈→Benchmark）
.venv/bin/python scripts/final_demo.py
```

## 3. 测试与检查

```bash
.venv/bin/python -m pytest -q                 # 206 passed
.venv/bin/ruff check packages apps tests scripts benchmark
cd apps/web && npm run build                  # vue-tsc + vite
```

## 4. 开发模式

```bash
cd apps/web && npm run dev     # 5173，/api 代理到 8000
```

## 5. 分离 Worker

```bash
TG_INLINE_WORKER=0 bash scripts/run_api.sh
bash scripts/run_worker.sh              # 可多实例
bash scripts/run_worker.sh --once       # CI 用
```

## 6. 常见问题

| 现象 | 处理 |
|---|---|
| 浏览器不可用 | `.venv/bin/playwright install chromium`，或 `TG_BROWSER_CHANNEL=msedge` |
| 执行一直 QUEUED | 启动 Worker 或设置 `TG_INLINE_WORKER=1` |
| `TESTCASE_NOT_APPROVED` | 在用例详情审核通过 |
| 断言 `NOT_EVALUATED` | Oracle 缺失或未审批 |

更多：`docs/DEPLOYMENT.md`、`docs/FEATURE-STATUS.md`。


## 6. 备份、诊断与验证（RUN-017）

```bash
# 1) 环境自检（只读）
.venv/bin/python scripts/doctor.py

# 2) 备份恢复真实验收（SQLite + PG16）
.venv/bin/python scripts/backup_restore_verify_run017.py \
  --out docs/assets/run017/wp5/backup-restore \
  --pg-admin-url "postgresql://traceguard@127.0.0.1:55432/postgres"

# 3) 故障观测资格实验（30 场景 × 2 轮，约 12 分钟）
.venv/bin/python scripts/bench_v2_run017.py --out docs/assets/run017/wp2 --rounds 2 --workers 2
```

注意：`TG_ARTIFACT_BACKEND` 只支持 `local`；配置 `s3` 会启动失败（这是有意为之）。
PostgreSQL 请显式使用 `127.0.0.1`，不要用 `localhost`（同名端口可能指向另一个实例）。
