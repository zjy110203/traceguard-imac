# RUN-011 WP9：部署、监控与运维准备（PARTIAL）

> 状态：**PARTIAL**。本轮补齐了一份**可复现**的独立部署流程与运维探针清单，
> 并把它作为 WP1 的实际启动方式验证过两次；但没有生产环境，**不宣称生产部署已验收**。

## 1. 可复现部署流程（本轮实际执行）

```bash
# 0) 依赖：Python 3.13 + .venv（requirements.txt 已装）；Node 22（前端已构建到 apps/web/dist）
# 1) PostgreSQL 16（本机用 Postgres.app 二进制起隔离实例；生产用托管 PG）
BIN=/Volumes/Postgres-2.9.7-16/Postgres.app/Contents/Versions/16/bin
$BIN/pg_ctl -D /tmp/traceguard-f2/pgdata -o "-p 55432 -k /tmp/traceguard-f2/pgsock" start
$BIN/createdb -h 127.0.0.1 -p 55432 -U traceguard traceguard_run011_l1

# 2) 初始化四个角色（密码只经环境变量/CLI，不写入仓库）
for u in viewer:viewer qa-engineer:qa-engineer qa-lead:qa-lead platform-admin:platform-admin; do
  TG_BOOTSTRAP_PASSWORD='***' .venv/bin/python scripts/auth_bootstrap.py \
    --db "postgresql+psycopg://traceguard@127.0.0.1:55432/traceguard_run011_l1" \
    --username "${u%%:*}" --roles "${u##*:}" --workspaces '["*"]'
done

# 3) TeamFlow（独立进程）
TG_TEAMFLOW_DB="sqlite:///./.traceguard/run011/loop1/teamflow.db" \
  .venv/bin/python -m uvicorn traceguard_teamflow.main:app --host 127.0.0.1 --port 8111

# 4) API（独立进程，认证 + 强制代理 + 不内嵌 Worker）
TG_DATABASE_URL="postgresql+psycopg://traceguard@127.0.0.1:55432/traceguard_run011_l1" \
TG_ARTIFACT_DIR=".traceguard/run011/loop1/artifacts" \
TG_TEAMFLOW_BASE_URL="http://127.0.0.1:8111" \
TG_INLINE_WORKER=0 TG_AUTH_ENABLED=true TG_ENVIRONMENT=test TG_EGRESS_ENFORCED=true \
TG_WORKER_ID=run011-api \
  .venv/bin/python -m uvicorn traceguard_api.main:app --host 127.0.0.1 --port 8110

# 5) Worker × 3（每个独立进程、独立 ID）
for w in w1 w2 w3; do
  TG_DATABASE_URL="…" TG_ARTIFACT_DIR="…" TG_TEAMFLOW_BASE_URL="…" \
  TG_INLINE_WORKER=0 TG_EGRESS_ENFORCED=true TG_WORKER_ID=run011-$w \
  TG_WORKER_POLL_INTERVAL_S=0.7 \
    .venv/bin/python -m traceguard_worker.main &
done
```

## 2. 启动期校验（已存在，本轮验证有效）

| 校验 | 行为 |
|---|---|
| `auth_policy.evaluate_auth_policy()` | `staging/production` 显式关闭认证 → **启动失败**；production 还要求 Cookie Secure + TTL ≤ 24h |
| 迁移 advisory lock | 4 个进程同时启动，`tg_schema_migration` 唯一、无 DDL 冲突（WP6 §1） |
| `TG_EGRESS_ENFORCED` 默认 true | 显式关闭会打 `egress_enforcement_disabled_explicitly` warning |
| 前端产物缺失 | 不挂载 SPA（不 fail-closed，易被误判为"页面 404"）→ 建议生产加启动校验（见 §5） |

## 3. 健康 / 就绪 / 监控探针

| 用途 | 端点 | 返回要点 |
|---|---|---|
| 健康（需认证） | `GET /api/v1/system/health` | `database.{ok,dialect}`、`worker.{id,inline_running}`、`counts.{intents,testcases,tasks,runs}` |
| 浏览器自检 | `GET /api/v1/system/browser?probe=true` | `available`、`version`、`channel`、`egress` |
| 队列 | `GET /api/v1/queue/stats` | 待领取/运行中/终态 |
| 收件箱 | `GET /api/v1/inbox/stats` | 消费滞后 |
| 指标 | `GET /api/v1/system/metrics` | runs / verdicts 分布 |
| 审计链 | `GET /api/v1/auth/audit/verify` | `intact`、`entries`、`broken` |
| LLM 用量 | `GET /api/v1/system/llm/summary` | 调用数/模型/预算（不含密钥） |

结构化日志：`TG_LOG_JSON=true` 时所有服务输出单行 JSON（`ts/level/logger/message/...`）。

## 4. 失败告警与备份

| 项 | 现状 |
|---|---|
| 失败告警 | **未实现推送**（无邮件/IM webhook）。可用 `queue/stats` + `runs?status=` 轮询 |
| 备份 | 已实测 `pg_dump -Fc`（137 KB / 216 evidence / 87 events） |
| 恢复 | 已实测 `pg_restore` 到新库 + API 校验（WP6 §5） |
| 保留策略 | 未定义（artifact 目录会持续增长） |

## 5. 未完成 / 建议

| 项 | 状态 | 建议 |
|---|---|---|
| 生产环境部署 | **NOT_RUN** | 需要真实主机/域名/TLS；`TG_ENVIRONMENT=production` + Cookie Secure |
| 启动时校验前端产物 | **NOT_IMPLEMENTED** | production 下 `dist/index.html` 不存在应启动失败 |
| 指标导出（Prometheus） | **NOT_IMPLEMENTED** | 现有 `/system/metrics` 是进程内聚合 |
| 告警 | **NOT_IMPLEMENTED** | 至少对 `Run FAILED`、`queue 积压`、`audit broken` 三类 |
| 备份定时化 | **NOT_IMPLEMENTED** | cron + 保留 N 天 + 恢复演练 |
| LICENSE | **NEEDS_DECISION** | 本轮未擅自选择（与 RUN-010 一致） |

## 6. 结论

部署流程可复现、关键配置有启动期校验、健康探针可用、备份恢复实测通过；
但监控/告警/保留策略未产品化，且没有生产环境验证。按指令要求：
**不因本机跑通就宣称生产部署已验收**。
