# RUN-017 WP5：备份恢复、环境诊断与部署文档

> 代码：`scripts/backup_restore_verify_run017.py`、`scripts/doctor.py`
> 证据：`docs/assets/run017/wp5/backup-restore/backup-restore.json`、`docs/assets/run017/wp5/doctor.json`
> 结论：SQLite 与 PostgreSQL 16 **全部检查项通过**（含恢复后继续写入）。

---

## 1. 备份恢复验收（真实执行，不是文档示例）

脚本流程（每个用例都从零构建数据）：

```
创建 TestCase(v1, APPROVED) → Task → Run(租约) → Artifact → Evidence(附件) → submit(PASSED)
→ 备份 → 恢复到全新目录/全新数据库 → 新 Runtime 回读并逐项校验 → 恢复后再写一次
```

### 1.1 校验项（12 项，全部必须为 true）

| 校验 | 说明 |
|---|---|
| `testcase_version` | 版本仍存在且 `APPROVED` |
| `run_exists` | Run 可读 |
| `run_verdict` | verdict 仍为 PASSED（未被改写） |
| `run_report_preserved` | 报告里的 `run_id` 不变 |
| `evidence_count` | 证据条数与备份前一致 |
| `evidence_integrity` | `verify_storage_integrity()` 重算持久化内容摘要 = PASS |
| `artifact_file_exists` | 产物文件在恢复目录可定位 |
| `artifact_sha256` | 文件重算 SHA-256 == 登记值 |
| `event_seqs_preserved` | 事件序号序列完全一致 |
| `migration_versions` | `tg_schema_migration` 与代码 `MIGRATIONS` 一致 |
| `post_restore_write` | 恢复后能创建新 Task/Run（不是只读快照） |

### 1.2 SQLite 结果

```
kind=sqlite  backup=file copy (db + artifacts)
checks: testcase_version=true run_exists=true run_verdict=true
        run_report_preserved=true evidence_count=true evidence_integrity=true
        artifact_file_exists=true artifact_sha256=true event_seqs_preserved=true
        migration_versions=true post_restore_write=true
all_passed=true
```

### 1.3 PostgreSQL 16 结果

```
kind=postgres16  backup=pg_dump --format=custom  restore=pg_restore --no-owner
source_db=traceguard_run017_bak_xxxx  restore_db=..._restored
checks: 与 SQLite 相同的 12 项全部 true
all_passed=true
```

- 真实 `pg_dump` / `pg_restore`（PostgreSQL 16 客户端），不是 `cp` 数据库文件；
- 恢复目标是**新数据库**，源库与恢复库在用例结束后都被删除；
- 只使用临时目录与临时库，未触碰任何真实用户数据。

复现：

```bash
.venv/bin/python scripts/backup_restore_verify_run017.py \
  --out docs/assets/run017/wp5/backup-restore \
  --pg-admin-url 'postgresql://traceguard@127.0.0.1:55432/postgres'
# exit 0；任一检查失败则 exit 1；pg 客户端缺失则 exit 3（BLOCKED）
```

## 2. 环境诊断（`scripts/doctor.py`）

设计约束：

- **默认只读**：不写配置、不改库、不启停服务；
- 输出 `PASS` / `WARN` / `FAIL` / `BLOCKED`；`FAIL` 才返回非 0；
- 输出前统一脱敏（DSN 口令、`*_key/token/password` 形态）；
- 不读取 `.env` 内容（只判断是否存在）。

本机实测（API/TeamFlow 未启动时）：

```
[PASS] python                3.13.12
[PASS] dependencies          fastapi / sqlalchemy / pydantic / uvicorn 可用
[PASS] config                .env 存在（不打印内容）
[PASS] playwright_chromium   chromium 159.0.8154.6
[PASS] node                  v22.22.2
[PASS] frontend_dist         index.html 存在，共 372 KiB
[PASS] database_migrations   12 个迁移已应用
[PASS] artifact_dir          .traceguard/artifacts 可读写
[WARN] api_health            Connection refused（未启动）
[WARN] teamflow_health       Connection refused（未启动）
[WARN] worker_queue          未启动或未授权
[PASS] egress_policy         默认目标 http://127.0.0.1:8001；额外放行 0 个
[WARN] port_api              127.0.0.1:8000 free
[WARN] port_teamflow         127.0.0.1:8001 free
summary: PASS=9 WARN=5 FAIL=0 BLOCKED=0
```

> `WARN` 是"服务没起"，本地演示阶段正常；`BLOCKED` 只用于"依赖不可用"
> （例如 Chromium 未安装），不会被当成通过。

## 3. 部署文档同步

本轮同步了：

- `README.md`：当前能力边界、部署形态、doctor 入口；
- `docs/DEPLOYMENT.md`：PostgreSQL 16 推荐配置、独立 Worker、备份/恢复、Artifact 存储限制、
  PG 双实例拓扑提醒（`127.0.0.1` vs `localhost`）；
- `docs/FEATURE-STATUS.md`：RUN-017 后的功能状态（含 BLOCKED / NOT_RUN）；
- `docs/quickstart.md`：本地演示与正式部署两条路径；
- `.env.example`：新增/修正 `TG_ARTIFACT_BACKEND`（只支持 local）、`TG_SOAK_PG_ADMIN` 等说明。

许可证：仓库**没有 LICENSE 文件**，本轮不擅自增加开源授权。

## 4. 已知限制

- 备份脚本只验证"逻辑备份 + 产物目录一起恢复"，不覆盖 PITR / WAL 归档；
- `pg_dump --format=custom` 需要客户端版本 >= 服务端大版本（CI 安装 `postgresql-client-16`）；
- SQLite 备份用文件复制，要求备份时无长事务写入（生产建议停机或使用 `.backup`）；
- `doctor.py` 的 Chromium 检查会真实启动一次浏览器（约 1–2 s），
  CI 中可用 `--skip-browser` 跳过。
