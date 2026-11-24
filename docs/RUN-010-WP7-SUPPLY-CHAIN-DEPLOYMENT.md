# RUN-010 WP7：供应链、部署和运行环境治理

> 状态：**IMPLEMENTED / VERIFIED**（lock 一致性 4 测试全绿；npm 漏洞清零；vue-tsc 3.x 验证合入）。
> 提交：`e38ccac`（含 vue-tsc 升级 `b7a2aaf` 评估提交）。

## 1. Python 依赖 lock 完整性（§7.1）

### 发现并修复的真实问题

| 问题 | 修复 |
|---|---|
| `requirements.lock.txt` 仅 32 项：缺 mypy、httpx2、playwright、greenlet、psycopg 等直接/传递依赖（新环境不可复现安装） | 从实际验证通过的 venv 重新冻结 **44 项**完整依赖 |
| **pytest-cov 约束冲突**：dev 声明 `>=5,<7` 而实际运行 `7.1.0`（超范围） | 约束放宽为 `>=5,<8` |
| **mypy 约束冲突**：dev 声明 `>=1.9,<2` 而最新 `2.4.1` | 约束放宽为 `>=1.9,<3` |
| CI（pr/nightly/release）用范围声明安装（`pip install -r requirements-dev.txt`），不可复现 | 改为优先 `requirements.lock.txt` 安装，失败才回退范围声明 |

### 防回归机制

新增 `tests/test_lock_consistency.py`（4 用例，全部通过）：
1. 运行时直接依赖全部锁定；
2. 开发直接依赖全部锁定；
3. **lock 版本满足声明范围**（正是 pytest-cov/mypy 冲突的自动检测）；
4. lock 条目全部 `name==version` 完全钉住。

SBOM：RUN-009 已产出三份（pip-audit 0 漏洞）；本轮 lock 更新后内容与实际依赖一致
（lock 即安装清单，`pip freeze` 语义）。

## 2. npm 漏洞（§7.2）

`vue-tsc → @vue/language-core → minimatch` 链路 3 个 moderate（dev-only，生产零影响，
RUN-009 已三重验证）。

**独立评估分支 `workbuddy/vue-tsc3-eval` 验证流程**（提交 `b7a2aaf`）：

| 验证项 | 结果 |
|---|---|
| vue-tsc 2.1.0 → 3.3.14 升级 | ✅ |
| `npm audit` | **3 moderate → 0 漏洞** |
| `vue-tsc --noEmit`（typecheck） | ✅ 通过 |
| `vite build` | ✅ 通过 |
| 登录 E2E（真实 Chromium） | ✅ 2/2 |
| axe 无障碍审查 | ✅ 13 路由 0 violations |
| PR Gate | 合入主分支后随 CI 验证（见 §5 追记） |

**结论：升级风险合理，已合入主分支**（`e38ccac`）——RUN-009 的 NEEDS_DECISION 项关闭。

## 3. 部署（§7.3）

`.env.example`（WP1 已扩充）覆盖：LLM、认证（环境策略矩阵）、数据库、网络出口。
配套部署说明（与 `docs/DEPLOYMENT.md` 并行，本轮新增内容）：

1. **认证初始化**：`scripts/auth_bootstrap.py`（CLI/环境变量两种方式，详见 `.env.example` 注释）；
2. **Cookie 安全**：production 必须 `TG_AUTH_COOKIE_SECURE=true`（启动期强制，否则启动失败）；
3. **数据库迁移**：`Database.create_schema()` 启动期幂等应用（v1~v13）；PostgreSQL 生产用
   `TG_DATABASE_URL=postgresql+psycopg://...`；
4. **Worker 启动**：单机 `TG_INLINE_WORKER=true`（默认）；独立 Worker 设 `TG_WORKER_ID` +
   `TG_INLINE_WORKER=false`，指向同一 `TG_DATABASE_URL`；
5. **Egress Proxy 配置**：默认强制（`TG_EGRESS_ENFORCED` 默认 true）；目标白名单
   `TG_TARGET_EXTRA_ORIGINS`；**不要**使用 `--host-resolver-rules=MAP * ~NOTFOUND`（实测破坏代理路径）；
6. **数据备份**：SQLite 备份 `.traceguard/` 目录；PG 用 pg_dump；证据在 `tg_evidence` 表 +
   `TG_ARTIFACT_DIR` 目录，两者需一致备份；
7. **健康检查**：`GET /health`（liveness）、`GET /health/ready`（readiness）、
   `GET /api/v1/system/health`（业务健康）；
8. **日志脱敏**：`Settings.public_dict()` 对 secret/password/token/key/credential 字段与
   DSN 内联口令自动遮蔽；审计日志不记录密码/令牌（`auth.py` 契约）。

不把开发默认配置用于生产：`TG_ENVIRONMENT=production` 时认证强制、Cookie 强制 Secure、
TTL ≤ 24h（`auth_policy.py` 启动期 fail-closed）。

## 4. LICENSE（§7.4）——NEEDS_DECISION

现状：仓库无 LICENSE 文件、README 无许可证声明、`pyproject.toml` 无 license 字段。
按红线**不擅自选择法律授权条款**，形成建议供项目负责人决策：

| 选项 | 适用场景 | 说明 |
|---|---|---|
| Apache-2.0 | 商业友好、希望被企业采用 | 带专利授权条款、需 NOTICE |
| MIT | 最简宽松 | 仅保留版权声明 |
| GPL-3.0 | 强 copyleft、衍生作品开源 | 对内部工具影响小，对外分发约束大 |
| 专有（Proprietary） | 仅内部使用 | 全部权利保留，不开源 |

建议动作（负责人选择后执行）：添加 LICENSE 文件 → README 声明 → `pyproject.toml` license 字段。
在此之前，代码默认保留全部权利。

## 5. CI 门禁追记

- PR Gate：HEAD `3ac6725` 全绿（WP4 后）；WP5/WP7 提交后随 push 自动验证（追记见文末）；
- Nightly Gate / Release Gate：WP0 已触发于 `8220479`（Nightly success；Release 因
  Chromium 安装缺失失败——已修复 workflow，重触发待最新提交稳定后执行）。

（CI 最终结论在 `docs/RUN-010-FINAL-REPORT.md` 汇总）
