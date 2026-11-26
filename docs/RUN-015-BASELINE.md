# RUN-015 BASELINE：RUN-014 基线复核与执行起点

> 日期：2026-11-26（CST）｜ 执行分支：`workbuddy/run015-execution-consistency`
> 基线：`438c54a4ed689801c24b8cfb62a7496fd7e66d70`（RUN-014 最终交付 HEAD）
> 原则：先核对、后修改；未完成项保留 BLOCKED / NOT_RUN / PARTIAL，不以历史记录冒充本轮证据。

---

## 1. Git 基线核查（WP0）

| 检查项 | 结果 | 依据 |
|---|---|---|
| 当前 HEAD 与远端一致 | PASS | 起点 `workbuddy/run014-product-closure` @ `438c54a`，upstream `origin/workbuddy/run014-product-closure` 同 SHA；已从此提交创建本轮分支 |
| 工作区干净 | PASS | `git status --short --branch` 仅有分支行，无文件变更 |
| `main` 未修改 | PASS | `origin/main = ac2876ecef779b0bed900bdf3b5361ce1d2fa3d6`，本轮不检出、不写入、不合并 |
| RUN-014 代码交付与文档收口关系 | PASS | 代码交付 `a1b3fe8`（WP5），其后 `cf05558`（WP6/WP7）与 `438c54a`（FINAL REPORT）为 docs-only |
| RUN-014 相对 main ahead 94 commits | PASS | `git rev-list --count origin/main..HEAD = 94` |
| PR #1 / #2 集成关系 | UNCHANGED | 按指令保持现状；PR #2 相对 RUN-014 HEAD 落后 29 commits（指令口径），本轮不改变、不合并、不关闭 |
| RUN-014 最终 PR Gate | PASS | run [`45756471627`](https://github.com/zjy110203/traceguard-imac/actions/runs/45756471627)：`head_sha=438c54a`，`event=push`，`status=completed`，`conclusion=success`，`run_attempt=1`（GitHub 公开 API 只读核验） |

本轮禁止事项已确认：不改 main、不自动合并 PR、不关闭 PR #1/#2、不强推、不 `reset --hard`、不 `git clean`、不覆盖用户修改、不改 RUN-011/012/013 原始实验数据。

## 2. RUN-014 文档与证据可定位性

已核对存在：`docs/RUN-014-{FINAL-REPORT,WORKLOG,WP1-REGRESSION,WP2-LEASE-FENCING,WP3-SECURITY,WP4-FULL-UI-LOOP,WP5-BENCHMARK-AI}.md`、`FEATURE-STATUS.md`、任务台账、`.workbuddy-run/*`；测试证据 `docs/assets/run014/wp4/full-loop.json`、`wp5/{v5-2rounds,ai-quality-n32}.json`、`wp6/pg-fencing.json`、三个 `tests/integration/*run014*.py` 与相关脚本均存在。

**明确缺失且已正确声明为 NOT_RUN**：`docs/assets/run014/soak/soak-60min.json`、`soak-samples.jsonl`。RUN-014 只留下约 4 分钟 Worker 日志，不存在 60 分钟汇总，本轮不得引用为 PASS。

## 3. 基线中已发现、留待 WP7 修订的口径问题

1. `docs/RUN-014-WORKLOG.md:24` 称 `.workbuddy-run/*` 已全部更新到 RUN-014，而 `:92` 称本轮未改写；实测五份状态文件顶部仍为 RUN-011 或更早。`FINAL REPORT §13.2` 的修正不完整。
2. `docs/FEATURE-STATUS.md:9` 的「专项 26 passed」无命令/日志支撑；权威证据仅 `tests/integration/test_worker_fencing_run014.py` **7 passed**，另有 PG16 fencing **9/9**。
3. `FINAL REPORT:30/61`、`WP4:5`、`FEATURE-STATUS:12` 称 14 个血缘 ID，而 `full-loop.json` 的 `lineage` 实际 15 项。
4. `docs/TraceGuard-持续开发任务台账.md` 顶部停在 RUN-011-R1.1，完全没有 RUN-012 / RUN-013 / RUN-014 条目；末尾 R8-08/R8-11/R8-12 仍标「进行中」。
5. `.workbuddy-run/{current-task,progress,resume,phase-status,blockers}.md` 未指向 RUN-014/015（本轮必须实际更新，但不取消 gitignore）。
6. `docs/assets/run014/wp4/full-loop.json` 截图数组使用绝对 macOS 路径（WP7 改相对路径，不改截图）。

## 4. 本轮执行环境

- Python 3.13.12（`.venv`），psycopg 3.3.9；Node 22.22.2 / npm 10.9.7。
- **PostgreSQL 16.16**（Postgres.app）本地可达：`127.0.0.1:55432`，用户 `traceguard`；无 psql CLI、无 Docker/Podman。PG 专项可用 `TG_TEST_POSTGRES_URL` 或 `scripts/verify_fencing_pg_run014.py` 的既有连接方式执行。
- 无 `gh` CLI；GitHub 公开 API 经 WebFetch 可读（curl 直连遭遇匿名 rate limit）。推送仍使用既有 git 凭据。
- B-17 所需安全批准未获得，继续 BLOCKED，不绕过安全工具。

## 5. 执行顺序与提交策略

WP0 → WP1 → WP2 → WP3 → WP4 → WP5 → WP6 → WP7。每个 WP：检查现状 → 最小修改 → 专项测试 → 影响面回归 → 原始证据 → 工作日志 → 独立提交。外部阻塞记录原因并继续不依赖该条件的工作，不把局部通过写成生产就绪。

本文件为 WP0 交付物；后续各 WP 专题文档按指令新建。
