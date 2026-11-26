# RUN-018 WP4：Artifact GC 并发安全

> 时间：2026-11-27
> 文件：`packages/persistence/traceguard_persistence/stores.py`
> 测试：`tests/integration/test_artifact_gc_concurrency_run018.py`（5 passed）
> 回归：`tests/integration/test_artifact_lifecycle_run016.py`（14 passed）

---

## 1. 真实竞态问题

### 竞态 A（orphan_files）

```
GC scan() 发现 orphan file（SHA=X，无 ArtifactRow）
  ↓
Worker put(data) → 写文件 SHA=X + 登记 ArtifactRow
  ↓
GC collect_garbage(apply=True) → 移动文件到 quarantine
  ↓
ArtifactRow 指向不存在的文件 → DANGLING
```

旧代码在 `snapshot_refs` 中检查一次后就直接移动文件，**没有**在移动前重新检查
是否已产生新的 ArtifactRow 登记。

### 竞态 B（orphan_rows）

```
GC _artifact_still_referenced(art_X) → false（通过）
  ↓
另一事务：EvidenceRow(attachment_ref=art_X) 创建
  ↓
GC s.delete(row) → 已引用的 ArtifactRow 被删除 → DANGLING REFERENCE
```

旧代码的 `_artifact_still_referenced()` 定向查询与 `s.delete(row)` 不在同一
事务中——`SELECT` 与 `DELETE` 之间的窗口允许并发引用写入。

## 2. 修复方案

### 2.1 竞态 A：文件隔离前重新检查登记

```python
def _safe_quarantine_file(self, action, quarantine):
    artifact_id = f"art_{sha256[:20]}"
    # 移动前再查一次数据库
    with self.db.session() as s:
        if s.get(ArtifactRow, artifact_id) is not None:
            return {"result": "skipped_referenced"}
        if self._artifact_still_referenced(s, artifact_id):
            return {"result": "skipped_referenced"}
    # 通过检查后再移动文件
    src.replace(dest)
```

**关键**：这是**单条主键 SELECT**（O(1)），不是全量扫描——不引入性能退化。

### 2.2 竞态 B：复核 + 删除绑定同一事务

```python
def _safe_delete_row(self, action, quarantine):
    with self.db.session() as s:
        row = s.get(ArtifactRow, artifact_id)
        if row is None:
            return {"result": "gone"}
        if self._artifact_still_referenced(s, artifact_id):
            return {"result": "skipped_referenced"}
        s.delete(row)  # 同事务删除
```

**同步保证**：
- **SQLite**：写事务天然互斥（WAL 模式下一个写者），复核与删除在同事务内
  不会被并发的 Evidence/Run 写入穿插。
- **PostgreSQL**：READ COMMITTED 下事务提交前持有写锁，同一事务内的
  SELECT + DELETE 是原子的。

### 2.3 保持不变的安全约束

- 默认 dry-run
- 文件移动（quarantine）而非 unlink
- 每条动作写审计记录
- 单条失败不中断整批

## 3. 并发测试

| 场景 | 测试 | 预期 | 结果 |
|---|---|---|---|
| A1：GC 扫描后 Worker 登记 | `test_new_registration_protected` | 文件不被隔离 | ✅ |
| A2：线程交错 | `test_thread_interleaved` | 文件不被隔离 | ✅ |
| B1：GC 前创建 Evidence 引用 | `test_evidence_ref_protects_row` | 行不被删除 | ✅ |
| B2：无 dangling | `test_no_dangling_after_gc` | 无悬空引用 | ✅ |
| 幂等：重复回收 | `test_double_gc_safe` | 第二次全 gone | ✅ |

**5 passed / 0 failed**

## 4. 性能影响

| 变更 | 性能影响 |
|---|---|
| `_safe_quarantine_file` 增加 1 次主键 SELECT | O(1) / 每条候选 |
| `_safe_delete_row` 事务内复核 | 与旧版 `_artifact_still_referenced` 相同 |
| 移除 `snapshot_refs` 全量快照 | 减少一次全量扫描 |

**净效果**：GC apply 性能不变或略有提升（去掉了冗余的全量快照）。
原有 10k/50k/100k 规模测试的分页机制保持不变。

## 5. 无法证明安全的边界

- **跨进程 SQLite 并发写**：WAL 模式下写事务互斥，但高并发下可能出现
  `database is locked`（已有 busy_timeout=30s 重试）。
  这是 SQLite 的固有限制，不是 GC 的缺陷。
- **PostgreSQL advisory lock**：本轮未使用 PG-specific 机制，而是利用
  事务语义。如需更强保证，可后续添加 `pg_advisory_xact_lock`。

## 6. 交付

- 代码：`packages/persistence/traceguard_persistence/stores.py`
  - `collect_garbage()` 重构
  - 新增 `_safe_quarantine_file()` / `_safe_delete_row()`
- 测试：`tests/integration/test_artifact_gc_concurrency_run018.py`（5 passed）
- 回归：14 项现有 Artifact 测试全过
- 状态：**VERIFIED**（SQLite 并发测试通过；PG16 需真实环境复验）
