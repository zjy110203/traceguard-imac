# 数据库设计（实际 Schema）

存储：SQLAlchemy 2.x，默认 SQLite（`.traceguard/traceguard.db`），可通过 `TG_DATABASE_URL` 切换 PostgreSQL。
所有表由 `traceguard_persistence.models` 定义，`Database.create_schema()` 建表并执行迁移。

## 1. 表清单

| 表 | 作用 | 关键约束/索引 |
|---|---|---|
| `tg_schema_migration` | 迁移版本记录 | `version` PK |
| `tg_workspace` | 工作区 | — |
| `tg_intent` | TestIntent（C01） | `workspace_id` 索引 |
| `tg_testcase` | 用例聚合（最新版本指针、审核状态） | `workspace_id`、`intent_ref` 索引 |
| `tg_testcase_version` | 用例不可变版本（C02） | **UNIQUE(testcase_id, version)** |
| `tg_oracle` | Oracle 版本（C03） | **UNIQUE(oracle_id, version)** |
| `tg_task` | 逻辑任务 | `idempotency_key` **UNIQUE** |
| `tg_run` | 执行尝试（C04） | `task_id`、`workspace_id` 索引；`execution_status+lease_expires_at` 队列索引 |
| `tg_step` | 步骤轨迹 | PK `id = run_id + ':' + step_id`；**UNIQUE(run_id, step_id)** |
| `tg_event` | 事件（C05） | **UNIQUE(producer_instance_id, sequence)**、**UNIQUE(producer_id, idempotency_key)** |
| `tg_event_conflict` | 同键异载荷审计 | 自增 PK |
| `tg_evidence` | 证据元数据（C06） | `workspace_id`、`run_id`、`kind`、`source_type` 索引 |
| `tg_consumer_inbox` | 消费幂等（C05） | **UNIQUE(consumer_id, event_id)** |
| `tg_artifact` | 产物（截图/DOM/trace） | `sha256` 索引；内容寻址路径 |
| `tg_doc` | 聚合文档（graph/diagnosis/disposition/probe_execution/repair/experiment/exploration） | `(kind, workspace_id)` 索引、`ref_id` 索引、`version` CAS |
| `tg_metric` | Benchmark 指标 | `experiment_id` 索引；保存分子/分母/CI |

## 2. 设计要点

### 2.1 为什么 Step 主键是 run 范围

同一用例每次 Run 的 `step_id` 相同（如 `s1`）。若用 `step_id` 做主键，第二次执行会覆盖第一次的轨迹。
本版本迁移 v4 修正为 `id = "{run_id}:{step_id}"`，并保留 `UNIQUE(run_id, step_id)`。
迁移兼容：全新库跳过、旧库拷贝 `index → step_index` 后重建、PostgreSQL 使用 `ON CONFLICT DO NOTHING`。

### 2.2 事件双唯一约束

```sql
UNIQUE (producer_instance_id, sequence)   -- 同一实例内序号严格唯一
UNIQUE (producer_id, idempotency_key)     -- 跨实例幂等（重启后仍生效）
```

同键异载荷不会覆盖：写入 `tg_event_conflict` 后抛 `CONFLICT`，调用方必须视为错误。

### 2.3 不可变对象

- `tg_testcase_version` 内容写入后不再更新（仅 `review_status` 与审核历史可流转）。
- `tg_evidence` 不可变：重复 `evidence_id` 写入直接报冲突；完整性校验结果单独更新。
- 修复晋升生成新版本，回滚也生成新版本 —— 不删除、不原地改。

### 2.4 队列与租约

Worker 领取：

```sql
SELECT * FROM tg_run
WHERE execution_status IN ('QUEUED','PREPARING','RUNNING')
  AND (lease_expires_at < now OR lease_owner IS NULL)
ORDER BY created_at LIMIT 5
```

领取时 `fencing_token = fencing_token + 1`、写入 `lease_owner/lease_expires_at`。
提交结果必须携带匹配的 `(lease_owner, fencing_token)`，否则返回 `STALE_FENCING_TOKEN`。

### 2.5 DocStore 的 CAS

`DocStore.put(version=N)` 时若实际版本 ≠ N 抛 `CONFLICT`。修复晋升另用业务 CAS
（当前正式用例版本必须等于 base），避免并发晋升覆盖。

## 3. 迁移

| 版本 | 名称 | 内容 |
|---|---|---|
| 2 | event_and_evidence_indexes | 事件/证据复合索引 |
| 3 | doc_store_index | `tg_doc(kind, workspace_id)` |
| 4 | step_primary_key_scoped_by_run | 重建 `tg_step`，修正跨 Run 覆盖缺陷 |

执行：应用/Worker 启动时 `Database.create_schema()`，幂等；并发启动时 DDL 冲突被识别并跳过。

## 4. 未完成项（如实记录）

1. PostgreSQL 运行时未验证（本机无 PG 服务）；SQL 使用通用方言，`JSON` 在 PG 映射为 `JSON` 类型。
2. 未引入 Alembic：迁移为代码内有序列表，足够当前规模；表数量增长后建议迁移到 Alembic。
3. 未做读写分离/分库；事件表增长后的归档策略未实现（当前为本地项目库）。
