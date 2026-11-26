# RUN-018 WP10：部署与运维能力完善

> 时间：2026-11-27
> 依赖：`scripts/backup_restore_verify_run017.py`、`scripts/doctor.py`、`docs/DEPLOYMENT.md`

---

## 1. 部署架构（v1 不变）

```
1 API（FastAPI）
N Worker（独立进程）
PostgreSQL 16
1 TeamFlow（被测目标）
本地内容寻址 Artifact 存储
```

本轮**不引入**：Redis / Celery / S3 / MinIO / 多 API 实例 / 微服务拆分。

## 2. PostgreSQL 备份计划

### 2.1 备份策略

| 项 | 配置 |
|---|---|
| 全量备份 | 每日一次（`pg_dump -Fc`） |
| WAL 归档 | 启用（PITR 能力） |
| 保留策略 | 本地 7 天，异地 30 天 |
| 备份验证 | 每次备份后自动运行 `backup_restore_verify_run017.py` |

### 2.2 备份命令

```bash
# 全量备份
pg_dump -Fc -h $PGHOST -U $PGUSER -d traceguard \
  -f /backup/traceguard_$(date +%Y%m%d_%H%M%S).dump

# 验证备份可恢复
.venv/bin/python scripts/backup_restore_verify_run017.py \
  --pg-dump /backup/traceguard_latest.dump \
  --target-db traceguard_restore_test
```

### 2.3 恢复演练

| 步骤 | 命令 | 验收 |
|---|---|---|
| 1. 创建测试库 | `createdb traceguard_restore_test` | 库存在 |
| 2. 恢复 | `pg_restore -d traceguard_restore_test backup.dump` | 无错误 |
| 3. 健康检查 | `scripts/doctor.py` | PASS |
| 4. 数据完整性 | `backup_restore_verify_run017.py` | 12/12 项通过 |
| 5. 清理 | `dropdb traceguard_restore_test` | — |

**所有演练必须使用独立测试库，不在真实数据库上执行清空或重建。**

## 3. Artifact 备份

| 项 | 配置 |
|---|---|
| 内容寻址路径 | `.traceguard/artifacts/<sha256前2位>/<sha256>` |
| 备份方式 | `rsync -a --link-dest`（增量硬链接） |
| 一致性校验 | `sha256sum` 逐文件校验 |
| 哈希不一致 | 标记告警，不覆盖 |

```bash
# Artifact 备份
rsync -a --link-dest=/backup/artifacts_prev \
  .traceguard/artifacts/ /backup/artifacts_$(date +%Y%m%d)/

# 一致性巡检
.venv/bin/python -c "
from traceguard_persistence.stores import ArtifactStore
from traceguard_persistence.db import Database
db = Database('sqlite:///...')  # 或 PG URL
store = ArtifactStore(db, root='.traceguard/artifacts')
scan = store.scan(retention_days=0)  # 全量扫描
print(f'缺失文件: {len(scan[\"missing_files\"])}')
print(f'孤儿文件: {len(scan[\"orphan_files\"])}')
"
```

## 4. 环境诊断（doctor）

```bash
.venv/bin/python scripts/doctor.py
```

检查项（PASS=9 WARN=5 FAIL=0）：
- Python 版本、依赖完整性
- 数据库连接、迁移版本
- Artifact 存储完整性
- 端口可用性
- 磁盘空间
- 环境变量配置

## 5. 部署升级说明

### 5.1 升级流程

```
1. 备份（数据库 + Artifact）
2. 拉取新代码
3. 运行迁移（alembic upgrade head）
4. 重启 API
5. 滚动重启 Worker（逐个）
6. 健康检查（doctor.py + /system/health）
7. 验证版本
```

### 5.2 迁移失败恢复

```
1. 停止 API 和 Worker
2. 恢复数据库备份
3. 检查迁移版本（alembic current）
4. 回滚代码到上一版本
5. 重启服务
6. 健康检查
```

## 6. 运行日志排查指南

### 6.1 常见问题

| 症状 | 检查点 | 命令 |
|---|---|---|
| API 无法启动 | 端口占用、DB 连接 | `lsof -i :8000`、`doctor.py` |
| Worker 领不到任务 | 租约配置、DB 连接 | 检查 Worker 日志 |
| Run 卡在 RUNNING | Worker 崩溃、租约过期 | 检查 Worker 进程 |
| Artifact 缺失 | 磁盘满、GC 误删 | `scan()` 检查 |
| 迁移失败 | 版本冲突、权限 | `alembic current` |

### 6.2 日志位置

| 组件 | 日志 |
|---|---|
| API | stdout / `TG_LOG_JSON=true` 时 JSON 格式 |
| Worker | stdout（每个 Worker 独立进程） |
| GC 审计 | `.traceguard/artifacts/_gc-audit.jsonl` |
| 备份日志 | `/backup/logs/` |

## 7. 环境变量

| 变量 | 说明 | 默认值 |
|---|---|---|
| `TG_DATABASE_URL` | 数据库连接串 | `sqlite:///...` |
| `TG_ARTIFACT_DIR` | Artifact 存储路径 | `.traceguard/artifacts` |
| `TG_ARTIFACT_BACKEND` | 存储后端 | `local` |
| `TG_INLINE_WORKER` | 内联 Worker | `false`（生产） |
| `TG_WORKER_LEASE_TTL_S` | 租约 TTL | 300 |
| `TG_LOG_JSON` | JSON 日志 | `false` |
| `TG_TEAMFLOW_BASE_URL` | TeamFlow 地址 | `http://localhost:8766` |

## 8. 不做的事

- 不擅自删除真实用户备份
- 不在真实数据库上执行清空或重建
- 所有演练使用独立测试库
- 不增加 S3 / Redis / Celery / 多 API 架构

## 9. 交付

- 文档：本文件
- 现有工具：`scripts/backup_restore_verify_run017.py`（12 项校验 × 2 平台）、`scripts/doctor.py`
- 状态：**IMPLEMENTED**（恢复演练需真实 PG16 环境验证）
