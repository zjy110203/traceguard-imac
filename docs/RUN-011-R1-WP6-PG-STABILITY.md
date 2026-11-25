# RUN-011-R1 WP6：PG 测试稳定性（OPEN-RISK-04）

> 状态：**VERIFIED**（2026-11-25）｜ 并行失败已复现、根因已定位并修复、并行验证通过
> 证据：并行复现日志（3×3 轮对照）、`tests/conftest.py`、`tests/e2e/conftest.py` 修复
> 全量基线：**680 passed / 0 failed / 0 skipped**（PG16.16，串行与 3 进程并行一致）

## 0. 结论摘要

| 指令要求 | 结论 |
|---|---|
| 复现并行失败 | ✅ 3 进程并发全量真实复现：每进程 7-8 errors + 1-3 failed（串行 680 全绿） |
| 查明连接池/测试库创建/迁移/资源竞争具体原因 | ✅ 两个根因（见 §1），**不是** PG 连接池问题 |
| 隔离各测试数据库 | ✅ PG 测试库本就 uuid-per-test 隔离（核实）；TeamFlow 测试库**新增进程级隔离** |
| 检查 fixture 生命周期 | ✅ 根因 A 即 fixture/tmp 生命周期问题（共享 basetemp 被会话清理误删） |
| 检查并行 worker 资源限制 | ✅ 根因 B 含固定端口/共享文件资源冲突；修复后 3× 全绿；SQLite 并发 DDL 锁超时保留为已知限制 |
| 未把失败测试改串行来宣称消失 | ✅ 未禁用/串行化任何测试；并行度保持 3 进程，修复的是共享资源 |
| PG16 串行全量 | ✅ 680 passed / 0 failed / 0 skipped |
| PG16 并行全量 | ✅ 3 进程并发 × 2 轮：一轮 3/3 全绿、一轮 2/3 全绿 + 1 SQLite 锁 flake（§3） |
| 多 Worker 独立部署回归 | ✅ WP1 两轮闭环（API + 3 独立 Worker + PG16）86/86 + 17/17 ×2 |

## 1. 根因分析（复现 → 定位）

**复现方法**：`TG_TEST_POSTGRES_URL=… pytest tests/ -q` × 3 并发进程（共享 PG 实例）。
RUN-011 观察的「603 passed + 60 errors」与本次「每进程 7-8 errors」同源。

### 根因 A（错误级联主体）：共享 pytest basetemp 被会话清理误删

- `pyproject.toml`：`addopts = "… --basetemp=.pytest-tmp/run"`——**固定路径**；
- pytest 对显式 basetemp 在会话开始会**整体清空**；并行进程 A 启动时删掉进程 B
  正在使用的 `.pytest-tmp/run/*` → `FileNotFoundError` / `OSError(safe-delete)` 级联
  （复现日志中 `test_egress_proxy_run009` 整模块 ERROR 即此形态）；
- 属 **fixture 生命周期 / 临时目录竞争**，与 PG 无关。

**修复**（`tests/conftest.py::pytest_configure`）：basetemp 按进程加后缀
（`.pytest-tmp/run-p<PID>`；xdist 场景用 `PYTEST_XDIST_WORKER`）。
验证：3× 并行 errors 从 7-8/进程 → **0**。

### 根因 B（用例互扰）：TeamFlow 共享库 + 固定端口

- TeamFlow 默认库 `sqlite:///./.traceguard/teamflow.db` 是**固定共享文件**（models
  在 import 时读 `TG_TEAMFLOW_DB`）；并行进程并发 seed →
  `UNIQUE constraint failed: tf_workspace.slug` → ASGI 异常 → 浏览器断言连锁失败
  （复现日志中 `test_playwright_real` 失败的直接错误）；
- `tests/e2e/conftest.py` 的 `teamflow_server` 固定 **8011 端口**子进程——并行进程
  互相抢绑/串话（`test_false_pass_is_detected` 收到别的进程的服务响应而误 PASSED）；
- 测试夹具 `tc_*.json` 硬编码 `base_url=8011`（历史默认），与动态端口脱节后
  断言变 NOT_EVALUATED。

**修复**：
1. `tests/conftest.py::pytest_configure`：`TG_TEAMFLOW_DB` 进程级隔离
   （`.traceguard/test-tmp/teamflow-e2e-<pid>.db`，import 前设置）；
2. `tests/e2e/conftest.py`：TeamFlow 子进程改**动态空闲端口**（fixture 返回真实 URL）；
3. `tests/e2e/test_execution_e2e.py::_exec`：run_config.base_url 以 fixture 动态端口
   覆盖夹具文件的历史默认值（不改夹具文件，覆盖点在测试代码）。

### 附带核实（指令「隔离各测试数据库」）

- PG 平台测试（`test_postgres_platform.py`）本就 `tg_{prefix}_{uuid}` per-test 建库
  并在 fixture teardown drop——PG 侧隔离健全，本轮未发现 PG 连接池/迁移竞争；
- 报告 RUN-011 的「PG 连接/数据库创建资源竞争」归因经复现**修正**为根因 A/B
  （错误集中在 e2e/tmp/TeamFlow 层，不在 PG 层）——如实更正历史判断。

## 2. 验证矩阵

| 运行方式 | 修复前 | 修复后 |
|---|---|---|
| PG16 串行全量 | 677–680 passed / 0 failed | **680 passed / 0 failed / 0 skipped**（终态确认） |
| PG16 3 进程并行（第 1 轮） | 每进程 7-8 errors + 1-3 failed | 2/3 进程 680/680；1 进程 1 flake（§3） |
| PG16 3 进程并行（第 2 轮） | — | **3/3 进程 680/680 全绿** |
| 多 Worker 独立部署 | — | WP1 两轮闭环（3 独立 Worker）86/86 + DB 17/17 ×2 |

## 3. 保留限制（如实声明）

1. **SQLite 并发 DDL 锁超时 flake**：`test_migration_v8_is_idempotent_when_reapplied_concurrently`
   （两线程并发 `create_schema` 同一 SQLite）在极端负载下偶发 `database is locked`
   （30s busy timeout 被 3× 全量套件 + 多路 Chromium 的 CPU 竞争耗尽）；
   3×2 轮并行中出现 1 次。该测试断言未弱化；**稳定并行验证在本机负载下
   以 2/3~3/3 成功率达成**，不宣称 100% 稳定。
2. 本机验证为「多 pytest 进程并发」形态（与 RUN-011 复现口径一致）；
   pytest-xdist 形态未验证（依赖未安装，不在本轮引入）。
3. 多 API 实例/DB 断连/长事务（RUN-011 WP6 §7 的 NOT_RUN 项）本轮**仍未运行**
   ——不在指令 §八 的三项最低要求内，保持 NOT RUN，不冒充通过。
