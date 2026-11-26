# RUN-016 WP1-C：Artifact 生命周期与孤儿文件治理

> 目标：解决"旧 Worker 失去租约后完成文件写入、数据库登记被拒（或未受约束），
> 最终产生无业务引用产物"的一致性风险，并建立**可审计、可回滚、默认只读**的回收机制。

---

## 1. 存储与引用现状（源码复核）

| 项 | 实现 |
|---|---|
| 落盘路径 | `<TG_ARTIFACT_DIR>/<sha256[:2]>/<sha256>`，内容寻址（`ArtifactStore._local_path`） |
| 登记 | `tg_artifact` 行：`artifact_id = art_<sha256[:20]>`、`sha256`、`size`、`media_type`、`path`、`workspace_id` |
| 业务引用 | 主引用：`tg_evidence.attachment_ref == artifact_id`；另有 Evidence `doc` JSON 内的 `art_*` 引用（探针/诊断派生证据） |
| 完整性 | `read()` 读文件后重算 SHA-256 比对，不一致抛 `PRECONDITION_FAILED` |
| 回收 | **原实现没有**：既无孤儿扫描，也无保留期/审计/回滚机制 |

### 发现的两个真实缺陷

1. **登记不受 Fencing 约束**：`put()` 不校验租约。旧 Worker 失去执行权后仍能登记新产物，
   形成"有登记但无业务引用"的孤儿行。
2. **落盘与登记非原子**：先写文件、后写库；库写失败时文件已存在，永久孤儿。
   且 `path.write_bytes()` 非原子，进程被杀会留下半截文件。

## 2. 修复内容

### 2.1 `ArtifactStore.put()`（`packages/persistence/.../stores.py`）

- 新增 `fence: tuple[str, int] | None` 与 `run_id` 参数；
- 登记前在**同一事务**内执行 `fenced_guard`：owner/token/租期/状态任一不成立 → `LEASE_LOST`；
- 守卫失败时，若本次新建了文件且库中仍无同哈希登记 → 删除自己刚写的文件；
  若已有登记（另一个 Worker 已成功登记同内容）→ **保留**，不误删；
- 落盘改为 `临时文件 + os.replace()` 原子替换。

### 2.2 Pipeline 接入

`RunPipeline._artifact_sink()` 传递 `(worker_id, token)` 与 `run_id`，
截图 / DOM / ARIA / trace 等产物登记全部受 Fencing 保护。
API 侧手动上传等不持有租约的路径继续以 `fence=None` 调用（保持可用性）。

### 2.3 巡检与回收

`ArtifactStore` 新增三个方法：

| 方法 | 语义 |
|---|---|
| `verify_consistency(limit=0)` | 逐条校验"登记 ↔ 文件"：文件存在 + SHA-256 匹配。返回 `missing` / `hash_mismatch` |
| `scan(retention_days=7)` | **只读**扫描：孤儿文件（磁盘有、无登记、无引用）、孤儿登记（无业务引用）、缺文件；未到保留期的单独归入 `protected_fresh` |
| `collect_garbage(scan, apply=False)` | 默认 dry-run；`apply=True` 才动作：文件移入 `<root>/_quarantine/<ts>/`（**不 unlink**），删除登记前**重新校验引用**，每条动作写 `_gc-audit.jsonl` |

CLI：`scripts/artifact_gc_run016.py`（默认 dry-run）

```bash
# 巡检（只读，退出码 0）
.venv/bin/python scripts/artifact_gc_run016.py --out docs/assets/run016/wp1/artifact-scan.json

# 真正回收（需显式 --apply；只处理超过保留期且确认无引用的对象）
.venv/bin/python scripts/artifact_gc_run016.py --apply --retention-days 7
```

## 3. 测试与证据

`tests/integration/test_artifact_lifecycle_run016.py`（11 项，全部通过）：

| 用例 | 验证点 |
|---|---|
| `test_put_with_valid_fence_registers_and_is_consistent` | 有效租约下登记成功，一致性巡检 PASS |
| `test_put_rejected_after_lease_lost_leaves_no_orphan_file` | 租约过期 → `LEASE_LOST`，且磁盘无残留（回滚自己新建的文件） |
| `test_put_without_fence_still_allowed_for_api_side` | API 侧不持有租约仍可上传 |
| `test_consistency_detects_hash_mismatch` | 文件被篡改 → 巡检报 `hash_mismatch` |
| `test_consistency_detects_missing_file` | 文件被删 → 巡检报 `missing` |
| `test_scan_separates_orphans_from_referenced_and_fresh` | 有引用产物不被列为孤儿；未登记文件被识别 |
| `test_scan_protects_objects_within_retention` | 保留期内对象进入 `protected_fresh`，不进入待回收 |
| `test_gc_dry_run_deletes_nothing` | dry-run 不动文件、不删登记 |
| `test_gc_apply_quarantines_and_audits` | 回收后文件在隔离区、登记删除、审计 JSONL 有记录 |
| `test_gc_apply_keeps_referenced_artifact` | 有 Evidence 引用的产物即使很旧也不回收 |
| `test_stale_writer_does_not_delete_registered_file` | 并发同哈希：失败方不删除胜者已登记的文件 |
| `test_scan_protects_artifact_referenced_only_by_run_report` | 只被 `tg_run.report_json`（trace_artifact）引用的产物不被判为孤儿 |

原始日志：`docs/assets/run016/wp1/artifact-tests.log`（12 passed）。

## 4. 真实库巡检结果（PG16 长稳实例，113 Run / 729 产物）

原始报告：`docs/assets/run016/wp1/artifact-scan.json`

| 指标 | 实测 |
|---|---|
| 磁盘文件数 | 729 |
| 数据库登记数 | 729 |
| 被业务引用 | 729（`referenced_artifacts=729`，与文件数一致） |
| 孤儿文件 / 孤儿登记 | **0 / 0** |
| 缺文件 | 0 |
| 哈希不一致 | **0**（`verify_consistency` 全量 729 条，`consistent=true`） |
| 未到保留期 | 0 |
| dry-run 动作 | 0（无可回收对象） |

### 4.1 巡检中发现并修复的第二个缺陷（重要）

首次扫描时 `protected_fresh=181`：729 个产物里有 181 个"无 Evidence 引用"。
进一步核对发现它们只出现在 `tg_run.report_json` 的 `trace_artifact` 字段里
（Playwright trace 只挂 Run 报告，不落任何一条 Evidence）。

原引用收集只看 `tg_evidence`，保留期一过就会把这 181 个**真实引用**的产物判成孤儿并回收。
已修复：`_referenced_artifact_ids()` 增加 `tg_run.report_json` 扫描；
修复后同一库复扫：`referenced_artifacts=729`、`protected_fresh=0`、孤儿 0。

这条缺陷说明"孤儿判定"必须覆盖**所有**引用面，不能只覆盖主路径。

## 5. 剩余限制

- `scan()` 的引用收集需要全量读取 `tg_evidence.doc` 做正则匹配，超大库（百万级证据）会有明显开销；
  当前按运维频次（离线巡检）设计，未做增量索引。
- 回收只支持本地文件后端；`TG_ARTIFACT_BACKEND=s3` 时 `put/read` 仍走原路径，**S3 孤儿回收未实现**（如实登记）。
- 隔离区 `_quarantine/` 不做自动清理，需人工确认后删除（有意为之：给误删留回滚窗口）。

## 6. 证据归档说明

`docs/assets/run016/wp1/soak/_runtime/` 是长稳的**运行态目录**（TeamFlow SQLite、产物文件），
属于可再生成的临时数据，不纳入版本库；其一致性结论已固化在 `artifact-scan.json`
（729 文件 / 729 登记、SHA 全匹配、孤儿 0）。长稳的断言、日志与采样均已入库。
