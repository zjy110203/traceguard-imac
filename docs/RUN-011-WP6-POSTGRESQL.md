# RUN-011 WP6：PostgreSQL 与数据一致性

> 执行时间：2026-11-25 01:10–02:10 CST。
> 环境：PostgreSQL **16.16**（Postgres.app 16，`/tmp/traceguard-f2/pgdata`，端口 55432）。
> 证据：
> - `docs/assets/run011/full-loop/loop1-db.json` / `loop2-db.json`（各 8/8）
> - `docs/assets/run011/wp6/loop1.dump`、`restore-verify.json`、`restore-api-verify.json`

## 0. 结论

在一个**没有 Docker / 没有系统 psql / 没有 brew** 的 macOS 环境中，用 Postgres.app 的
PG 16.16 二进制起隔离实例，完成了：并发建表/迁移、多 Worker 同库、审计哈希链、CAS 回执、
备份与恢复（含恢复后证据校验与 Repair 状态校验）。**本地不再 skip PG 用例**：
13 个 PG 平台/一致性用例在真实 16.16 上全部通过。

## 1. 环境与迁移

| 项 | 值 |
|---|---|
| server_version | `16.16 (Postgres.app)` |
| 数据库 | loop1 `traceguard_run011_l1`、loop2 `traceguard_run011_l2`（各自独立） |
| 迁移版本 | 12 条记录（v1..v13 中实际 12 个版本，见 `migration_versions`） |
| 迁移执行方式 | API + 3 Worker **同时** `create_schema()`（空库） |
| 结果 | 无 `pg_type_typname_nsp_index` 冲突、无重复迁移、启动全部成功 |

这一点直接验证 RUN-010 CL-03 的修复：`pg_advisory_xact_lock` + SAVEPOINT 在**真实并发启动**
（4 个进程）下有效，而不仅是单测里的 4 线程。

## 2. 多 Worker 同库

| 检查 | 结果 |
|---|---|
| 3 个 Worker + 1 API 同库 | 通过 |
| Run 领取互斥 | 每个 Run 只有一个 `lease_owner`；`verify_db` 中 loop1 三个 Worker 各 1 个 Run |
| 终态 Run 均有 Evidence | 0 个孤儿（`LEFT JOIN` 计数为 0） |
| Event `(producer_instance_id, sequence)` 全局唯一 | 0 重复 |
| Event 可追溯到 Run | `context_refs->>'run'` 非空，0 条缺失 |
| 审计 seq | loop1 16 条（1..16），loop2 16 条（1..16），无空洞 |

## 3. 审计哈希链（本轮修了一个真 bug）

首轮 loop 前，`GET /auth/audit/verify` 在 PG 上返回：

```
{"entries": 36, "broken": ["aud_…", …], "intact": false}
```

根因：`tg_audit_log.before_json/after_json` 是 PG `json` 列，读回是 **dict**；
写入哈希时用的是 JSON **字符串**。校验时未归一化 → 所有带 before/after 的条目摘要不一致。
（不是数据被篡改。）

修复：`verify_audit_chain()` 先把 JSON 列还原为写入时的规范化字符串（`sort_keys`、`ensure_ascii=False`）。
回归：`test_audit_verify_normalizes_json_columns_for_postgres`。

修复后两轮均 `intact=true`，seq 连续；恢复库上同样 `intact=true`。

## 4. CAS 与回执

`tg_promotion_receipt`（独立表，不是 doc）：

| 列 | loop1 值 |
|---|---|
| idempotency_key | `run011-repair_17955418361089015db3385fe` |
| kind | `PROMOTE` |
| base_version / target_version | 1 / 2 |
| result | `{"version":2,"testcase_id":"tcase_bench_loc001","kind":"PROMOTE"}` |
| 回滚行 | `kind=ROLLBACK`，base=2，target=3 |

`verify_db_run011.py` 断言：PROMOTE 与 ROLLBACK 都存在，且 `target > base`。

## 5. 备份与恢复（真实执行）

```
pg_dump -Fc traceguard_run011_l1 → docs/assets/run011/wp6/loop1.dump (137,502 bytes)
createdb traceguard_run011_restore
pg_restore -d traceguard_run011_restore loop1.dump
```

恢复后：

| 检查 | 结果 |
|---|---|
| `verify_db_run011.py`（8 项） | **8/8** |
| `tg_evidence` | 216 行 |
| `tg_event` | 87 行 |
| `tg_promotion_receipt` | 2 行 |
| 用恢复库启动 API，抽样 5 条 Evidence `POST /evidence/{id}/verify` | **5/5 `integrity_check=PASS`** |
| `GET /auth/audit/verify` | `intact=true` |
| `GET /repairs/{id}` | `status=ROLLED_BACK`，`promoted_version=2`，有 `rollback_record` |

证据：`docs/assets/run011/wp6/restore-verify.json`、`restore-api-verify.json`。

## 6. 测试套件在真实 PG 上的结果

| 运行方式 | 结果 |
|---|---|
| 默认（无 `TG_TEST_POSTGRES_URL`） | 650 passed / 13 skipped（PG 用例 skip） |
| `TG_TEST_POSTGRES_URL=…pgtest`（仅 PG 用例） | **13 passed** |
| `TG_TEST_POSTGRES_URL=…` + 全量 + `-x` | **663 passed / 0 skipped** |

> 注：曾出现一次全量 603 passed + 60 errors，原因是并行执行时 PG 连接/数据库创建的资源竞争；
> 单独/串行执行稳定通过。该现象已记录为测试稳定性风险，未在本轮修复。

## 7. 未完成

| 项 | 状态 |
|---|---|
| 多 API 实例同时启动 | **NOT_RUN**（本轮只启动 1 个 API + 3 Worker） |
| 数据库断连恢复 | **NOT_RUN**（未主动 kill 连接/重启 PG） |
| 长事务 / 锁等待 | **NOT_RUN** |
| 连接池参数压测 | **NOT_RUN**（仅观察未耗尽） |
| 迁移重复执行（已有库上再跑） | ✅ 隐含验证：每个 Worker 启动都会 create_schema，实际重复执行 4 次无副作用 |
| PG 15/17 兼容 | **NOT_RUN** |

## 8. 结论

PostgreSQL 16 在本轮是**真实运行**的（不是 CI 替代、不是 SQLite 冒充）：
迁移、并发、审计链、CAS 回执、备份恢复、恢复后证据/状态校验都有可复核证据。
异常场景（断连、长事务、多 API）仍未验证，不得宣称"生产级 PG 已验证"。
