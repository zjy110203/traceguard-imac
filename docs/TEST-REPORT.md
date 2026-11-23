# 测试报告

> 最新：**RUN-005（2026-11-23）285 passed / 5 skipped / 0 failed**，详见 `docs/RUN-005-TEST-REPORT.md`。
> 本文件保留 RUN-002 历史记录，不改写。

# RUN-002 测试报告（2026-11-22）

## 1. 总览

```
$ .venv/bin/python -m pytest -q
206 passed in 19.8s
$ .venv/bin/ruff check packages apps tests scripts benchmark
All checks passed!
$ cd apps/web && npm run build
vue-tsc --noEmit && vite build → ✓ built（14 个 chunk）
```

| 类别 | 文件 | 数量 |
|---|---|---|
| 契约 | `tests/contracts/test_contracts.py` | 12 |
| 单元 | verdict / probe / evidence / benchmark / security / healing / explorer / graph / diagnosis / compiler / tasks / persistence / core / assertions / diagnosis_rules | 159 |
| 被测应用 | `tests/teamflow/test_teamflow.py` | 9 |
| 集成 | `test_api_flow.py` / `test_full_pipeline.py` / `test_api_health.py` | 16 |
| E2E | `test_execution_e2e.py` / `test_playwright_real.py` | 7 |
| **合计** | | **203** |

本轮新增：持久化 9、真实浏览器 E2E 3、API 集成 8、诊断规则回归 5。

## 2. 真实浏览器 E2E（`tests/e2e/test_playwright_real.py`）

测试自行启动 TeamFlow（随机端口）+ 真实 Chromium：

| 用例 | 断言 | 结果 |
|---|---|---|
| 正常创建项目 | verdict=PASSED；8 步全部 PASSED；observer=playwright；截图是真实 PNG（>5KB）；事件链含 started/step/completed；无幂等冲突 | PASS |
| False Pass 检测 | verdict=FAILED；`a_biz_count` FAILED 且 reason 含 `got=0` | PASS |
| HTTP 降级标记 | `observer_mode=http-fallback` 写入 Run 与报告，不冒充浏览器 | PASS |

连续在同一进程执行多次（thread-local Browser 复用）已验证无 "Sync API inside asyncio loop"。

## 3. 实测场景（脚本 + API）

`scripts/smoke_playwright.py`（4 场景，真实浏览器）：

| 场景 | verdict | 步骤 | 证据 | 关键断言 |
|---|---|---|---|---|
| 正常创建 | PASSED | 8 | 24 | UI/业务/行渲染 全 PASSED |
| False Pass（TF-DB-001） | FAILED | 9 | 24 | 业务断言 `count>=1 got=0` |
| UI 缺失（TF-UI-001） | FAILED | 9 | 24 | UI 断言 FAILED，业务 PASSED |
| API 500（TF-API-001） | FAILED | 6 | 15 | 错误提示 PASSED，业务断言 FAILED |

截图人工核验：1280×800 PNG，页面含"创建失败：simulated API failure (TF-API-001)"。

## 4. 修复链路实测（API 全链路）

```
propose → VALIDATED（断言保持 PASS / Oracle 完整 PASS / Diff 白名单 True）
→ sandbox-run（真实 Playwright，PASSED）
→ submit → PENDING_APPROVAL
→ approve(role:qa-lead) → APPROVED
→ promote → PROMOTED（v3，CAS_OK）
→ 执行 v3 → PASSED（8 步）
→ rollback → ROLLED_BACK（生成 v4）
```

## 5. 本轮修复的缺陷（均补了回归测试）

| # | 缺陷 | 影响 | 回归 |
|---|---|---|---|
| 1 | `assertions._apply_rule` 在 json 为 dict 且字段为 int 时调用 `.get()` | 业务断言直接抛 AttributeError，Run 变 NOT_EVALUATED | `test_assertions` + 真实执行 |
| 2 | 日志门面不支持 kwarg | 所有 `logger.info("event", key=v)` 调用抛 TypeError，启动即崩 | 全量测试运行 |
| 3 | Playwright sync API 同线程反复 start/stop | 第二次执行抛 "Sync API inside the asyncio loop" | E2E 三次连续执行 |
| 4 | `tg_step` 主键为 step_id | 同用例多次 Run 互相覆盖轨迹 | 迁移 v4 + 图节点 run 范围 |
| 5 | 网络事件未派发即快照 | 500 场景无 `http_errors`，诊断误判为持久化 | `test_diagnosis_rules` + Benchmark B |
| 6 | 5xx 与"未落库"同时出现时把症状当根因 | 首选根因准确率 0.667 | 上述单测 |
| 7 | Catalog 入参未过滤展示字段 | `TestIntent/OracleSpec/TestCaseVersion` 因 extra=forbid 拒绝创建 | API 集成测试 |
| 8 | 错误码映射缺 PRECONDITION_FAILED | 审核/审批失败返回 400 而非 412 | API 集成断言 |
| 9 | Fix `LogRecord` 保留字冲突 | `created` 等字段写入 extra 抛 KeyError | 种子日志实测 |

## 6. 未覆盖

1. PostgreSQL 运行时行为（无 PG 实例）。
2. 跨浏览器（Firefox/WebKit）。
3. 长稳压测（≥1 小时）、Worker 崩溃恢复的自动化脚本（手工验证过租约回收）。
4. 前端单元测试（仅类型检查 + 构建 + 浏览器人工核验）。
5. 外部站点泛化（P-EXT 未授权）。
