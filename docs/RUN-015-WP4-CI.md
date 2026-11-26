# RUN-015 WP4：关键测试纳入 CI 门禁

> 分支：`workbuddy/run015-execution-consistency`｜状态：配置完成；真实 CI 证据见 §4（最终推送后补录）

---

## 1. PR Gate 变更（`.github/workflows/pr-gate.yml`）

1. 触发分支新增 `workbuddy/run015-*`，确保本轮提交在合并前有快速门禁；
2. Fast service integration 加入 RUN-014 三个专项：
   - `tests/integration/test_repair_regression_run014.py`
   - `tests/integration/test_worker_fencing_run014.py`
   - `tests/integration/test_sse_revocation_and_global_rbac_run014.py`
3. 同时加入 RUN-015 新增专项（SQLite 快速部分）：
   - `test_repair_regression_run015.py`
   - `test_worker_fencing_run015.py`
   - `test_sse_recheck_limit_run015.py`

PG16 专项与 60 分钟长稳**不进 PR 快速门禁**（依赖 Service Container、耗时过长），交由 Nightly。

## 2. Nightly Gate 变更（`.github/workflows/nightly-gate.yml`）

1. 新增 `push` 触发：`workbuddy/run015-*`，每次推送都用该 HEAD 真实跑 Nightly；
2. PostgreSQL job 新增：
   - `test_fencing_pg_run015.py`（等锁期间租约过期、原子 submit、并发接管单 owner）；
   - `scripts/verify_fencing_pg_run014.py`（9 项 PG16 fencing 契约，可自动复核）。

脚本现支持 `TG_TEST_POSTGRES_URL`（Service Container），并在该模式下把结果写到
`docs/assets/run015/wp4/pg-fencing-ci.json`，**不覆盖 RUN-014 历史归档**。

## 3. Release Gate

未改触发条件；`nightly-attestation` 已强制要求存在 `head_sha == github.sha` 的成功 Nightly，
保证 Nightly 与 Release 绑定同一成功 HEAD。本轮不自动合并 PR、不打 Release Tag。

## 4. 门禁运行证据

### 4.1 本地等价 PR Gate 快检

| 命令 | 结果 | 原始证据 |
|---|---|---|
| `pytest -q`（上述 17 个快速集成文件，含全部新增专项） | **114 passed / 0 failed**（30.30s） | `docs/assets/run015/wp4/pr-gate-local-raw.txt` |

### 4.2 真实 GitHub Actions

| 门禁 | 结果 | 说明 |
|---|---|---|
| PR Gate | 待最终 HEAD 推送后核验（WP7/最终报告补录 run ID 与结论） | 本轮分支首次推送才会触发 |
| Nightly Gate | 待最终 HEAD 推送后核验，或明确 NOT_RUN | 已配置 push 触发；无 workflow_dispatch 凭据不手工触发 |
| Release Gate | **NOT_RUN** | 需 `workflow_dispatch` + 凭据；本轮不创建 Tag，不以历史结果冒充 |

不以历史 run（`45756471627` 等）标记本轮通过；最终报告必须写明本轮 HEAD 对应的真实 run。
