# RUN-018 WP4：Artifact GC 真正的跨进程并发安全协议

> 时间：2026-11-27（RUN-018-R1 轮，接替上一轮"竞态 A/B 修复 + 5 线程测试"）
> 状态：**VERIFIED**（SQLite + PostgreSQL 16.16 真实多 OS 进程验证通过）
> 文件：`packages/persistence/traceguard_persistence/stores.py` / `models.py`、
> `packages/execution/traceguard_execution/tasks/service.py`
> 测试：`tests/integration/test_artifact_gc_concurrency_multiprocess_run018.py`（13 passed，
> 含 6 项 PG16 变体）+ 旧 5 项线程测试 + 14 项生命周期回归

---

## 1. 上一轮遗留的缺口（本轮输入）

上一轮（a8d78cd）修复了两个竞态的"检查时机"，但存在未闭环事项：

1. **竞态 A 残余**：`_safe_quarantine_file()` 先查 ArtifactRow、退出数据库事务、
   再执行 `src.replace(dest)`——检查与移动之间没有与 `put()` 共享的
   per-artifact 同步锁，窗口仍在。
2. **竞态 B 残余**：`_safe_delete_row()` 实际使用 `s.get(ArtifactRow, ...)`
   而非注释声称的 `SELECT FOR UPDATE`；且 Evidence `attachment_ref` 是普通
   字符串、Run `report_json` 是 JSON——**引用写入方与 GC 不共享任何锁**，
   同一 Session 内的"复核 + 删除"不能证明 PostgreSQL 并发安全。
3. **测试不足**：5 个测试使用线程 + `put_done.wait()`/`ref_done.wait()` 把一个
   操作完全串行化后才执行另一个，不是真实并发验证；且只在 SQLite 上跑。

## 2. 本轮设计：统一 Artifact Lifecycle Lock

### 2.1 锁原语（`artifact_lifecycle_lock(db, artifact_id)`）

**锁与业务工作共用同一数据库事务**——锁覆盖真正的临界区（复核 + 文件移动 /
复核 + 行删除 / 写文件 + 登记），而不是只保护一次 SELECT。事务 commit/rollback
自动释放锁；持锁进程崩溃时连接断开，锁由数据库回收，不存在泄漏。

| 后端 | 机制 | 粒度 |
|---|---|---|
| PostgreSQL 16 | `pg_advisory_xact_lock(SHA-256(“tg-artifact-lifecycle:”+artifact_id) 前 15 hex)` | 真 per-artifact，不同产物互不阻塞 |
| SQLite | 事务内首先 `INSERT OR REPLACE tg_artifact_lifecycle_lock`（BEGIN IMMEDIATE 等价语义，取得库级写锁） | 全库串行（SQLite 单写者约束下的正确 per-artifact 语义） |

仅 Python `threading.Lock` 不合格（不跨进程）——已弃用。

### 2.2 引用路径梳理与接入（全部 7 条）

| 路径 | 接入点 |
|---|---|
| `ArtifactStore.put` | 整个「fencing 守卫 + 登记 + 写文件（含缺失自愈）」在锁内 |
| Evidence `attachment_ref` | `PersistentEvidenceManager._insert`：引用为 `art_*` 格式时取得锁 + 锁内复核 |
| Evidence `doc` 内嵌 art_* 引用 | 同上：`_ARTIFACT_REF_RE` 扫描 doc，全部引用锁内复核存在性 |
| Run `report_json` 引用 | `TaskService.submit_result`：`lock_and_verify_report_refs()`（同事务内逐条锁 + 复核） |
| GC 文件隔离 | `_safe_quarantine_file`：锁内「复核登记 + 复核引用 + 移动 + journal」 |
| GC ArtifactRow 删除 | `_safe_delete_row`：锁内「复核引用 + 删除行」；DB 提交后再移文件 |
| put 清理未登记文件 | `_remove_unregistered_file`：锁内「检查 + unlink」 |

**拒绝语义（场景 B 情况二）**：引用写入方取得锁后重新检查 Artifact 是否仍然
存在；已回收 → 抛 `ARTIFACT_COLLECTED`（CONFLICT）拒绝创建，不产生
dangling reference。Run 报告同理。

**锁序**：Evidence/Run 的多条引用按排序获取；GC 每个动作只持一把锁 →
无锁序环路。

### 2.3 必须满足的时序（全部由锁协议保证）

- **场景 A 情况一**（GC 先持锁）：put 等待 → GC 复核无登记 → 完成隔离 →
  put 取锁后**重新写文件**并登记 → 最终 ArtifactRow 对应文件真实存在。
- **场景 A 情况二**（put 先持锁）：写文件 + 登记完成后 GC 才进入 →
  发现登记 → 不隔离。
- **场景 B 情况一**（Evidence 先持锁）：引用创建提交后 GC 才进入 →
  发现引用 → 不删除。
- **场景 B 情况二**（GC 先持锁）：复核 + 删除 → 引用写入方取锁后复核 →
  拒绝创建。

## 3. 崩溃一致性（§2.4）

文件 rename 与数据库事务不是原子事务。采用**可恢复 GC journal**
（`root/_gc-journal.jsonl`，JSONL 追加，O_APPEND 单行原子）：

1. 每个文件移动 / 行删除前先写 `intent`（jid / op / src / dest / sha）；
2. 操作完成后写 `done`；
3. **删除顺序约定**：DB 删除提交先于文件隔离 → 崩溃最坏留下孤儿文件
   （下一次 GC 回收），**绝不会**出现 ArtifactRow 指向被隔离走的文件；
4. `recover_journal()`：intent 无 done 时按实际状态幂等恢复——
   quarantine 已移动 → 补记 done；DB 已删但文件未移 → 现场补移 +
   补记 done；DB 未删 → abandoned（状态已变化，不动作）；每次恢复写
   `resolved` 记录，审计完整。
5. 同 SHA 重新写入：put 持锁重写，GC 的旧 intent 恢复时发现登记存在 →
   abandoned，不会移动新文件。

默认 dry-run 不变；所有隔离保留审计记录（`_gc-audit.jsonl` 不变）。

## 4. 真实并发测试（§2.5）

`tests/integration/test_artifact_gc_concurrency_multiprocess_run018.py` +
子进程执行器 `scripts/artifact_gc_concurrency_worker.py`：

- **独立 OS 进程**（spawn 子进程，独立数据库连接），**文件轮询同步**
  （吸取 WP1 教训：绝不与可能被 SIGKILL 的进程共享 mp 锁）；
- SQLite + **PostgreSQL 16.16**（本机 Postgres.app :55432，每测试独立建库）；
- 每场景断言不变量：`verify_consistency()`（零登记指向缺失文件）、
  零 dangling reference（全表扫描 Evidence/Run 引用）、被引用产物不丢、
  删除恰好一次。

| # | 场景 | SQLite | PG16 |
|---|---|---|---|
| 1 | GC 与 put 并发（5 轮，覆盖两种锁序） | ✅ ×8 轮稳定 | ✅ ×4 轮 |
| 2 | GC 与 Evidence 引用并发 | ✅ | ✅ |
| 3 | GC 与 Run 报告引用并发 | ✅ | ✅ |
| 4 | 两个 GC 进程同时处理同一 Artifact（删除恰好一次） | ✅ | ✅ |
| 5 | GC 中途被 SIGKILL（journal intent 中途态） | ✅ | ✅ |
| 6 | recover_journal 后再次 GC | ✅ | ✅ |
| 7 | 同 SHA 并发写入（4 进程，登记恰好一条） | ✅ | ✅ |
| 8 | 引用创建与删除竞争（8 轮压力） | ✅ | ✅（6 轮） |

**13/13 passed**（SQLite 7 + PG16 6），证据归档：
`docs/assets/run018/wp4/mp-concurrency-test-results.txt`、
`docs/assets/run018/wp4/gc-bench-10k-run018.json`。

保留测试全部通过：旧 5 项并发测试、14 项 `test_artifact_lifecycle_run016.py`
（含"过期快照喂给 collect_garbage"窗口场景）、492 项单元测试全量。

## 5. 性能（不引入全量加载或 O(N×M)）

10k Evidence 规模基准（`scripts/artifact_gc_bench_run017.py`）：

```
scan 0.058s / 51 条 keyset 分页查询 / RSS Δ0.1MB
referenced_but_orphaned=0 / consistent=true
```

per-action 锁 = 每 action 一个短事务（与旧版每 action 一个 session 相同量级）；
扫描路径（keyset 分页）未改动。50k/100k 保留机制不变（同脚本 `--counts` 可复跑）。

## 6. 边界与如实声明

- SQLite 锁为全库串行：由 SQLite 单写者约束决定，不是 GC 缺陷；
  高并发写由 busy_timeout=30s 承担。
- PG advisory 键来自 artifact_id 哈希：理论上有 63-bit 空间碰撞可能
  （概率可忽略；碰撞只导致额外串行化，不影响正确性）。
- 50k/100k 大规模基准本轮未复跑（10k 已证明无机制性回退）；
  大规模长稳属于 Reliability Gate 范围。
- WP2 状态据此可标 **VERIFIED**（真实 PG16 多进程测试已完成）。
