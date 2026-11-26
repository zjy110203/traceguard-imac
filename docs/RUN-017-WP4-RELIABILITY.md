# RUN-017 WP4：执行内核与 Artifact 可靠性加固

> 证据：`docs/assets/run017/wp4/soak/soak-62min.json`、`docs/assets/run017/wp4/gc-bench.json`
> 代码：`packages/execution/.../tasks/service.py`、`packages/persistence/.../stores.py`
> 脚本：`scripts/soak_run013.py`、`scripts/artifact_gc_bench_run017.py`、`scripts/regression_multiprocess_run016.py`

---

## 1. 严格 PG16 长稳（本轮重跑）

| 项 | 实测 |
|---|---|
| 墙钟时长 | **62.05 分钟**（要求 ≥62，无容差；观测窗口 61.62 min） |
| 真实 Run | **119**（要求 ≥100），全部到达终态 |
| 判定 | 119 PASSED / 0 FAILED |
| 检查项 | **33 / 33 PASS**，**0 NOTE**（RUN-016 为 1 NOTE） |
| 拓扑 | 独立 API（`TG_INLINE_WORKER=false`）+ **3 个独立 Worker** + 真实 Chromium + 真实 TeamFlow |
| 数据库 | PostgreSQL **16.16**（`127.0.0.1:55432`，独立库 `traceguard_run013_soak`） |
| API 延迟 | p50 4.6 ms / max 10.6 ms（60 个采样点） |
| 采样 | 每 30 s 一次，共 60 个采样（API 进程 RSS/CPU、队列、库大小/WAL、Worker 存活） |

### 故障编排（全部真实触发，33 项检查全 PASS）

| 故障 | 证据 |
|---|---|
| Worker SIGTERM（正常退出） | `exit=0` |
| Worker SIGKILL | `exit=-9`；存活 Worker 租约接管，`fencing=2`，执行到 `COMPLETED` |
| Worker SIGSTOP（暂停） | 租约过期 → 第三个 Worker 接管（`owner=r15-soak-w3 fencing=2`） |
| 旧 Worker 恢复 | 终态与证据数不变（`evidence=25->25`），过期提交被 Fencing 拒绝 |
| API 重启（强杀） | API 不可达 → 重启后恢复健康；期间 Worker 继续消费，重启后可回读终态 |
| 数据库连接中断 | 中断后恢复，API + Worker 继续（`status=COMPLETED`） |
| SSE 断续 | 断开后用游标续传，`cursor=True resumed=True`（不重复已读事件） |
| 全部 Worker 重启 | 全部恢复消费 |
| 状态冻结 | 终态后不再出现非终态；数据库无重复步骤 |

> 长稳中的 `RESET_DATA` 场景**串行**执行（脚本单线程），不并发提交。

## 2. `fenced_session` 重入防护（WP4.1）

**问题**：同一 Run 嵌套 `fenced_session` 会自锁——外层事务持有 run 行锁，
内层"条件空更新"等同一把锁，表现为挂到数据库超时。

**修复**：用 `ContextVar` 记录当前上下文已持有的 Run：

```python
_FENCED_SCOPE: ContextVar[frozenset[str]] = ContextVar("traceguard_fenced_runs", default=frozenset())
...
if run_id in _FENCED_SCOPE.get():
    raise FencedReentryError(run_id)   # 在打开事务之前失败
```

- 选 ContextVar 而非线程局部变量：asyncio 任务天然隔离、新线程不继承、异常路径可精确回滚；
- **不改成 RLock**：嵌套事务在"外层未提交时内层已提交"语义下无法证明安全，
  明确拒绝比"看起来能跑"可靠；
- 不改变正常事务原子性与 Fencing 语义。

**测试（6 项，PG16 真实库）**：

| 用例 | 结果 |
|---|---|
| 正常单次调用 | PASS |
| 同 Run 嵌套 → 立即失败（耗时 < 2s，不等锁） | PASS |
| 不同 Run 嵌套 → 允许 | PASS |
| 异常退出后上下文清理（可再次进入） | PASS |
| 新线程不继承持有标记（不误判重入） | PASS |
| 子进程内嵌套调用秒级退出（证明不会永久挂起） | PASS |

## 3. Artifact GC 可扩展性（WP4.2）

### 3.1 修复前的复杂度

- `scan()` 用 `select(EvidenceRow.doc)` / `select(RunRow.report_json)` **一次性加载全部文档**；
- `collect_garbage(apply=True)` 对**每条候选**重扫全库（O(候选数 × 全库文档数)）。

### 3.2 修复

1. `_referenced_artifact_ids()` 改为 **keyset 分页**（按主键 `> last_id` 每批 500 条），
   每批只保留瞬时文档内存；
2. `scan()` 只取必要列（不实例化 ORM 对象），登记行降到 5 个标量；
3. `collect_garbage()` 整个批次只做**一次**全量引用快照，之后每条候选做
   **定向复核**（只查这一条是否被引用：`attachment_ref` / `doc LIKE` / `report_json LIKE`）；
4. 删除前再确认一次，缩小"扫描后新增引用"的竞态窗口。

### 3.3 规模实测

（见 `docs/assets/run017/wp4/gc-bench.json`）

| 规模 | scan 耗时 | SQL 查询数 | 峰值内存增量 | 扫描期间并发写延迟 | 引用保护 |
|---|---|---|---|---|---|
| 1 万 Evidence | **0.058 s** | 51 | +0.1 MB | 3.5 ms | 一致（0 违规） |
| 5 万 Evidence | **0.198 s** | 212 | +0.1 MB | 6.0 ms | 一致（0 违规） |
| 10 万 Evidence | **0.383 s** | 412 | +0.3 MB | 6.5 ms | 一致（0 违规） |

结论：**扫描耗时随规模线性增长（O(N)），峰值内存基本不变（+0.1~0.3 MB）**，
SQL 查询数 = 批次数 + 常数（每批 500 条），扫描期间的业务写入延迟仍在个位毫秒，
没有出现"GC 扫描阻塞业务"的现象；"被引用却进入孤儿列表"的条数始终为 0。

> 口径：查询数 = 扫描期间实际执行的 SQL 条数（SQLAlchemy 事件计数）；
> 内存 = 扫描前后 `ru_maxrss` 差值；并发写延迟 = 扫描进行中插入一条真实 Evidence 的耗时。
> "引用保护"= 被业务引用却进入孤儿列表的条数（必须为 0）。

## 4. Artifact 后端能力边界（WP4.3）

- `ArtifactStore` 现在**只接受 `local`**：配置 `s3`/`minio` 时启动即抛
  `ARTIFACT_BACKEND_UNSUPPORTED`（不再静默用本地目录冒充对象存储）；
- `TG_ARTIFACT_BACKEND` 已接入 API 与 Worker 的构造路径（此前该配置被完全忽略）；
- 文档同步说明：备份必须同时覆盖数据库与 `TG_ARTIFACT_DIR`。

## 5. Regression 多进程并发（PG16）

- 脚本：`scripts/regression_multiprocess_run016.py`（10 个 OS 进程 + PG16）；
- PG 端点已参数化（`TG_R16_PG_ADMIN`），不再硬编码本机端口；
- **本轮实测：13 / 13 PASS**（10 进程并发 → 唯一 Claim / 唯一 Run / 唯一 History /
  唯一提交回执 / 结论 PASSED / 新 Runtime 可恢复 / 重复请求不再建 Run /
  失败请求不破坏赢家状态），证据：`docs/assets/run017/wp4/regression-multiprocess.json`。

## 6. 迁移幂等与依赖锁定（本轮本地复核）

- PostgreSQL 16 上重复 `create_schema()` **幂等**：`tg_schema_migration` 版本
  `[2,3,4,5,6,7,8,9,10,11,12,13]`，与代码 `MIGRATIONS` 完全一致且无重复；
- `requirements.lock.txt`（ruff 固定 0.17.6）在干净环境可完整安装（CI 用它装依赖）；
  `requirements.txt` 为范围声明，lock 为全量固定集——两者角色不同，不做文本等同比较。

## 7. 仍未覆盖

- **多 API 实例**压测（v1 明确不支持多 API 实例部署）；
- **WAL/PITR** 级备份（当前只验证逻辑备份 `pg_dump --format=custom` + 产物目录）；
- **GC 在 PG 上的 keyset 分页实测**：规模测试用 SQLite（同一 SQLAlchemy 路径），
  PG 行为由既有 PG16 专项覆盖，但未做 10 万级 PG 压测；
- 存储后端只有本地实现（v1 明确不引入 S3/MinIO）。
