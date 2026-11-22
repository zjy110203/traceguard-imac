# RUN-003-E 可靠性与工程质量验证报告（2026-11-22）

> 全部结果来自真实命令与真实运行，未验证项如实标记。

## 1. 多 Worker 并发（本轮新增真实验证 ✅）

`scripts/worker_concurrency_verify.py`：入队 4 个真实 Run → 4 个独立 Worker 进程
（+API 内嵌 Worker）并发领取：

| 验证项 | 结果 |
|---|---|
| 全部 Run 完成 | 4/4 COMPLETED |
| 租约归属 | 4 个不同 owner（无双重执行） |
| fencing 拒绝旧 Worker | 生效（STALE_FENCING_TOKEN 正确拒绝） |

**发现并修复的真实缺陷**：
1. `claim_next_run` 的"先 SELECT 后 UPDATE"在双进程下存在竞态——两个 Worker 可同时
   认为租约空闲并各自领取，导致同一 Run 双重执行（曾触发 `tg_step` 唯一键双写冲突）。
   **修复**：领取改为单条条件 UPDATE（租约 CAS），rowcount=1 才算领取成功
   （`packages/execution/.../tasks/service.py`，SQLite/PG 同构）。
2. 修复过程中引入的 `Column.or_()` 误用（应为 `func.coalesce`）被本轮验证当场暴露并修复。

**诚实记录**：并发下 3/4 Run 的业务断言 FAILED（count=0）——4 个 Worker 并发写
TeamFlow 单文件 SQLite 被测应用产生写竞争，属被测应用特性（真实并发瓶颈），
TraceGuard 如实捕获并报告为 FAILED，未掩盖。

## 2. 其余可靠性项

| 项 | 状态 | 证据 |
|---|---|---|
| Worker 心跳续租 | 已运行验证（既有单测） | ttl/3 续租；被接管后步骤边界停止 |
| 事件幂等/序号/冲突 | 已运行验证 | 12 线程并发持久化测试 |
| Evidence 完整性/篡改检测 | 已运行验证 | 篡改后下载 404 |
| 数据迁移 | 已运行验证 | 迁移 v4；全新库/旧库均可建 |
| API 异常处理 | 已运行验证 | 集成测试覆盖 404/409/412/422/403 |
| 大量数据分页 | 已运行验证 | 本库 300+ Runs，limit=500 分页正常（指挥中心实测） |
| 数据库迁移到 PG 真实运行 | **BLOCKED** | 本机无 Docker/psql，无法起 PG 实例（RUN-002 限制延续） |
| 长时间运行 | 部分验证 | 单轮 30 场景连续执行 ~7 分钟无泄漏；24h+ 长稳测试未执行 |
| 性能瓶颈 | 已有实测基线 | docs/PERFORMANCE.md（API 延迟/吞吐） |

## 3. 环境说明（如实记录）

- 本会话运行环境的沙箱拦截进程内文件删除（safe-delete bulk guard），
  曾导致 trace 清理触发 `SystemExit(1)` 杀死执行线程。
  **已修复**：trace 临时文件删除容错（保留文件优于崩溃，`browser/session.py`）。
- pytest 临时目录限制 → `--basetemp=.pytest-tmp/run`（已固化到 pyproject）。
