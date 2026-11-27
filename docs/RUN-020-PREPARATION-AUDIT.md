# RUN-020-PREPARATION 第一阶段：最终代码与交付审计

> 审计对象：`zjy110203/traceguard-imac`，分支 `workbuddy/run019-product-completion`，
> 审计时 HEAD `564b21c8280f9ce9f6e925715fd973d74ad570b4`，审计日期 2026-11-27。
> 口径：每条结论都可回读（git 命令 / CI 运行页面 / 本地实跑）。
> 本轮**不改 main、不强推、不自动合并 PR**；除“文档数字回填”外不改任何代码。

## 0. 审计结论

| # | 审计项 | 结论 | 判定 |
|---|---|---|---|
| 1 | 分支与远端 HEAD | 本地 = 远端 = `564b21c`，与指令一致 | **PASS** |
| 2 | 工作区干净 | `git status --porcelain` 0 行，无未跟踪文件、无 stash | **PASS** |
| 3 | 相对 main 的差异 | 线性 172 提交 / 0 合并 / 0 落后；2967 文件 +313,389 −2,354 | **PASS（见 §2 提示）** |
| 4 | RUN-019-R2 七提交 | 7/7 存在且均为 HEAD 祖先 | **PASS** |
| 5 | README / FEATURE-STATUS / FINAL-REPORT 一致性 | 三处仍停在 `#113/#66 @ 19f527f`，落后 2 个提交 | **FIXED（本审计内回填）** |
| 6 | 遗漏提交 / 临时文件 / 无效产物 | 无遗漏提交；存在 1 个冗余 zip、103 个重复入库文件、3 张同字节截图 | **PASS with NOTES** |
| 7 | RUN-014～RUN-018 证据完整 | 5 个 RUN 的 FINAL-REPORT + 专题 + 证据全部在位，未被改写 | **PASS** |
| 8 | 质量门禁复核 | 本地 1144 passed / 16 skipped、ruff 全绿；CI PR #115 + Nightly #68 6/6 | **PASS** |

**总判定：可进入正式 PR 评审；不建议直接合并**（Reliability Gate 未在 CI 实跑、CI 侧真实 LLM 仍 BLOCKED、LICENSE 未决）。

## 1. 基线与身份核对

| 项 | 值 | 证据 |
|---|---|---|
| 仓库 | `https://github.com/zjy110203/traceguard-imac.git` | `git remote -v` |
| 当前分支 | `workbuddy/run019-product-completion` | `git branch --show-current` |
| 本地 HEAD | `564b21c` RUN-019-R2 R2-6: 最终报告与交付台账 | `git log -1` |
| 远端分支 HEAD | `564b21c`（一致，无未推送提交） | `git ls-remote origin refs/heads/workbuddy/run019-product-completion` |
| `origin/main` | `ac2876e`（2026-11-23 19:33，"第五轮"） | `git ls-remote origin refs/heads/main` |
| 分叉点 | `ac2876e` = `origin/main`，即 main 完全被包含 | `git merge-base origin/main HEAD` |
| 相对 main | **0 behind / 172 ahead**（可快进） | `git rev-list --left-right --count origin/main...HEAD` |
| 工作区 | 干净（0 变更 / 0 未跟踪） | `git status --porcelain=v1 -uall` |

> 说明：`origin/main` 仍停在 RUN-007 收口点，因此 RUN-008～RUN-019-R2 的全部成果都在本分支上；
> “相对 main 的 172 个提交”不是 R2 的增量，而是十二轮 RUN 的累计交付。R2 自身只有 7 个提交（§3）。

## 2. 相对 main 的差异

| 指标 | 值 |
|---|---|
| 提交数 | 172（**0 个 merge 提交**，历史线性，未被改写） |
| 文件变更 | 2967（新增 2833 / 修改 134 / 删除 0） |
| 行变更 | +313,389 / −2,354 |
| 入库文件总数 | 3294 |
| 入库内容体积 | ≈254 MB，其中 `docs/assets` **241.7 MB / 2623 文件（95%）** |

**提示（非阻塞）**：本分支作为单个 PR 合入 main 时，评审面 ≈2970 文件 / 31 万行，
其中 95% 体积是历史验收证据（PNG / JSONL / 日志）。这不影响正确性，但影响评审效率与
仓库克隆成本。RUN-020 建议对证据资产做“分层归档”（见 `docs/RUN-020-DESIGN.md` §9 风险 R-07）。

## 3. RUN-019-R2 七提交完整性

| # | 提交 | 主题 | 是否为 HEAD 祖先 | CI |
|---|---|---|---|---|
| 1 | `2b90d6c` | R2-1 UI 验收改走可视化 StepEditor（修 Nightly #65 `#tc-steps` 超时） | 是 | PR #113 / Nightly #66 |
| 2 | `87696a6` | R2-2 `compile_source` 来源由服务端可信流程决定 | 是 | 同上 |
| 3 | `cbc620f` | R2-3 前端操作权限门禁补全（四角色真实验证） | 是 | 同上 |
| 4 | `dfb49b2` | R2-4 Workspace 统计范围显式化 + 版本 `authoring_hash` 校验 | 是 | 同上 |
| 5 | `19f527f` | R2-5 收口差距清单 + 本地验收证据 + 格式化 | 是 | PR #113 / Nightly #66（SUCCESS） |
| 6 | `7988210` | R2-5 CI 全量测试加看门狗（15/35 分钟） | 是 | PR #114 / Nightly #67 |
| 7 | `564b21c` | R2-6 最终报告与交付台账（CI 结果回填） | 是（HEAD） | **PR #115 / Nightly #68** |

验证命令（可复现）：

```bash
for c in 2b90d6c 87696a6 cbc620f dfb49b2 19f527f 7988210 564b21c; do
  git merge-base --is-ancestor "$c" HEAD && echo "$c ANCESTOR"
done
```

**无遗漏提交**：`61ffd4a..HEAD` 之间共 7 个提交，与 R2 报告的声明逐一对应；
`git log --oneline origin/main..HEAD | grep -c 'RUN-019-R2'` = 7。

## 4. 文档与最新 HEAD 的一致性（发现并修复）

### 4.1 发现

| 文档 | 修复前表述 | 与 HEAD 的关系 | 处理 |
|---|---|---|---|
| `README.md` | “PR Gate **#113** 与 Nightly **#66** @ `19f527f` 真实通过” | 落后 2 个提交（缺 #114/#67、#115/#68） | 已回填为 #115/#68 @ `564b21c` |
| `README.md` | “后端全量 1144 passed … Reliability Gate CI NOT_RUN” | 数字正确（本轮本地复跑 1144 passed 一致） | 保留 |
| `docs/FEATURE-STATUS.md` | “R2 代码 `2b90d6c`→`dfb49b2`，证据 `19f527f`” | 漏 `7988210`（看门狗）与 `564b21c`（收口） | 已补全提交链 |
| `docs/FEATURE-STATUS.md` | “PR Gate #113 @ `19f527f` SUCCESS；Nightly #66 …” | 落后 2 个提交 | 已回填 #115/#68 |
| `docs/RUN-019-FINAL-REPORT.md` | §6 表格停在 #113/#66 | 该节标题即“真实结果”，读者会误以为是 HEAD 结果 | 已追加 “R2-6 收口后复验” 行（#114/#67、#115/#68），历史行保留不改写 |
| `docs/RUN-019-R2-GAP-CLOSURE.md` | §5 停在 #113/#66 | 同上 | 已追加两行 |

### 4.2 修复原则

- **只追加/回填事实，不改写历史结论**：原有 #113/#66 行保留（它们对应 `19f527f`，是真实结果），
  新增行标明 HEAD 与运行编号；
- 不修改任何测试、代码、断言；
- 回填依据是 GitHub Actions 运行页（见 §7.2），不是推断。

## 5. 遗漏提交、临时文件与无效产物

### 5.1 未跟踪 / 未入库（正确）

| 路径 | 体积 | 状态 |
|---|---|---|
| `.pytest-tmp/` | 4.0 GB（379 个 run 目录） | 已 gitignore，未入库 ✅ |
| `.traceguard/` | 9.5 GB（运行时 DB / 产物 / llm_usage.jsonl） | 已 gitignore，未入库 ✅ |
| `logs/` | 1.9 MB | 已 gitignore（保留 `.gitkeep`）✅ |
| `.venv/`、`node_modules/`、`apps/web/dist/` | — | 已 gitignore ✅ |
| `.DS_Store`（`docs/FINAL/`、`/`） | 6 KB | 磁盘存在但未入库 ✅ |
| `.env` | 161 B | 已 gitignore；**本审计未读取其内容** ✅ |

> 本机 13.5 GB 运行期垃圾（`.pytest-tmp` + `.traceguard`）属本地磁盘卫生问题，
> 不进仓库、不影响交付；建议由项目负责人决定是否清理（本轮未删除任何文件）。

### 5.2 已入库但属“非源码”的内容

| 类别 | 数量 | 判定 |
|---|---|---|
| PNG 截图（验收证据） | 894 | 有据可查，保留；体积主要来源 |
| `.log`（Worker / CI / 长稳日志） | 55 | 属证据，保留；建议 RUN-020 起改为“摘要入库 + 原始日志走 artifact” |
| `.jsonl`（Benchmark 样本） | 29 | 证据，保留 |
| `.zip` | 1（`docs/FINAL/TraceGuard-Final-Delivery-Report.md.zip`） | **冗余**：与同目录 `.md` 内容重复，且是不可 diff 的二进制；建议后续清理（本轮未删，避免动历史证据） |
| `.err` | 3 | 证据（失败现场），保留 |
| `资料/TraceGuard-开源项目调研/`（32 文件 / 0.5 MB） | 调研底稿 | 已入库；与产品代码无关但不影响构建 |

### 5.3 重复与证据区分度

- 全仓重复 blob：**47 组 / 103 个冗余文件 / 5.36 MB**（占入库体积 2.1%，可接受）。
- **需注意**：`docs/assets/run014|015|019/wp*/full-loop/03-compiled-candidate.png`、
  `04-oracle-bound.png`、`05-stability-replay.png` 三者**字节完全相同**（SHA 相同）。
  即“编译候选 / 绑定 Oracle / 稳定性回放”三个步骤保存了同一帧画面。
  这不影响该轮结论（同轮还有 JSON 断言与血缘 ID 作为主证据），但**截图本身不能证明步骤差异**。
  RUN-020 的证据规范要求“每个关键步骤至少一张状态可区分的截图 + 机器可读断言”（见设计 §7.4）。
- 1 个脚本未被任何文档/工作流引用：`scripts/bench_llm_arm.py`（历史 LLM 实验臂）；
  属保留的历史工具，不构成无效产物。

### 5.4 安全面

- CI `security` Job 每轮执行 `npm audit` + 真实密钥形态扫描（`git grep` 仅扫已跟踪文件），
  Nightly #68 该 Job SUCCESS（20s）。
- 入库文件不含 `.env`、`*.key`、`*.pem`、数据库文件；
  `Settings.public_dict()` 对 `deepseek_api_key` 输出 `***REDACTED***`（本轮实测确认）。
- 本审计**未读取、未导出、未搜索任何凭据**；LLM 证据来自官方 smoke 脚本的输出（§7.3）。

## 6. RUN-014～RUN-018 历史证据完整性

| RUN | 文档数（`docs/RUN-0xx-*.md`） | 证据文件 | 证据体积 | FINAL-REPORT | 判定 |
|---|---|---|---|---|---|
| RUN-014 | 8 | 28 | 2.8 MB | ✅ `RUN-014-FINAL-REPORT.md` | 完整 |
| RUN-015 | 9 | 49 | 3.5 MB | ✅ | 完整 |
| RUN-016 | 11 | 812 | 31.0 MB | ✅ | 完整 |
| RUN-017 | 11 | 255 | 27.9 MB | ✅ | 完整 |
| RUN-018 | 13 | 179 | 20.1 MB | ✅ | 完整 |
| RUN-019 | 9 | 573 | 23.5 MB | ✅ | 完整 |
| RUN-019-R2 | 1（GAP-CLOSURE） | 47 | 4.4 MB | ✅（并入 `RUN-019-FINAL-REPORT.md`） | 完整 |

- 各 RUN 的 `BASELINE` / `WORKLOG` / 专题报告齐全（`RUN-014` 起每轮均有 3 类文档）。
- 历史报告未被改写：R2 的修订以“追加章节/追加行”的方式落在 `RUN-019-FINAL-REPORT.md`
  与 `RUN-019-R2-GAP-CLOSURE.md`，`RUN-014`～`RUN-018` 文件在本轮零改动
  （`git log 19f527f..HEAD -- docs/RUN-01[4-8]*` 为空）。

## 7. 质量门禁复核（本轮实跑）

### 7.1 本地

| 门禁 | 命令 | 结果 |
|---|---|---|
| 后端全量 | `pytest -q`（SQLite，Python 3.13） | **1144 passed / 16 skipped / 0 failed，382.40s**（与报告一致） |
| Lint | `ruff check apps packages scripts tests` | All checks passed |
| 格式 | `ruff format --check apps/api packages scripts tests` | **281 files already formatted**（报告口径 280，差异来自 `7988210` 新增的 `scripts/ci_pytest_watchdog.py`） |
| 前端 | `npm run typecheck` / `npm run build` | 由 PR Gate #115 覆盖（本轮未重复构建） |

16 个 skip 全部是 `TG_TEST_POSTGRES_URL 未配置（本地无 PG）`，逐条打印跳过原因，不是静默跳过。

### 7.2 CI（GitHub Actions，HEAD `564b21c`）

| 门禁 | 运行 | 结论 | 关键数据 |
|---|---|---|---|
| PR Gate | **#115**（run 46008658111） | **SUCCESS** | 3m40s（backend + frontend 类型检查/构建） |
| Nightly Gate | **#68**（run 46008658134） | **SUCCESS（6/6 Job）** | backend-full 9m33s、ui-acceptance **2m5s**、postgres 1m3s、security 20s、llm-smoke 23s、gate-verdict 4s |
| ├ llm-smoke | 同上 | Job SUCCESS，**真实调用 BLOCKED** | runner 无 `DEEPSEEK_API_KEY`；gate-verdict 注释：“LLM smoke BLOCKED（缺 Secret）；该证据不得用于 Release 放行” |
| └ gate-verdict | 同上 | SUCCESS | 按既有规则接受 LLM BLOCKED（不伪造 PASS） |
| Reliability Gate | — | **NOT_RUN** | 仅 `workflow_dispatch`；本机无 `gh` CLI / 无授权令牌（详见 `RUN-020-RELEASE-ADMISSION.md`） |

对照：Nightly #65 @ `61ffd4a` FAILURE（`#tc-steps` 超时）→ R2-1 修复后 #66/#67/#68 连续三次 SUCCESS。

### 7.3 真实 LLM（本地，REAL_PROVIDER）

| 项 | 结果 |
|---|---|
| 命令 | `.venv/bin/python scripts/llm_smoke.py` |
| 结果 | **11/11 PASS**（连通性、模型存在性、Flash 文本、Token 统计、JSON 输出、Pro 推理、超时边界、截断处理、成本估算标记、密钥脱敏 x2） |
| 真实调用证据 | `38in/77out 700ms`、`deepseek-v4-pro 4789ms`、`calls=5 cost=$0.000639` |
| 证据文件 | `docs/assets/run020-prep/llm/llm-smoke-20261127.json`（含 HEAD 与时间戳，**不含任何密钥**） |
| 状态判定 | **本地 REAL_PROVIDER = PASSED**；**CI REAL_PROVIDER = BLOCKED（无仓库 Secret）** |

> 该结果与指令中的“当前缺少 `DEEPSEEK_API_KEY`”存在差异：**本地已配置可用凭据，仓库 Secret 未配置**。
> 本审计只运行了仓库自带的 smoke 脚本读取配置，未查看、未打印、未导出密钥任何部分。

### 7.4 其他验收证据核对

| 证据 | 声明 | 实测文件 | 判定 |
|---|---|---|---|
| UI 全链路 | 42 项 / 41 PASS / 0 FAIL / 1 NOTE | `run019-r2/local/ui/run017-ui-core.json`：`total=42, passed=41, failed=0`，`git_head=dfb49b2`，sqlite + 2 独立 Worker | 一致 |
| RBAC 四角色 | 24/24 | `run019-r2/local/rbac/rbac-roles.json`：`total=24` | 一致 |
| a11y | 77/77 | `run019-r2/local/a11y/a11y-summary.json`：`cells_total=77, passed=77, failed=0, duration_s=107` | 数量一致；**标签仍写 “RUN-018 WP8/R1 accessibility”**（脚本常量未随轮次更新）→ NOTE |
| 版本完整性 | 5/5 | `tests/integration/test_testcase_version_integrity_run019_r2.py` | 在 1144 passed 内 |
| 来源可信 | 8/8 | `tests/integration/test_testcase_source_provenance_run019_r2.py` | 在 1144 passed 内 |

## 8. 主要风险与未决事项（如实记录）

| 编号 | 事项 | 状态 | 影响 | 建议 |
|---|---|---|---|---|
| A-01 | Reliability Gate 未在 CI 实跑 | NOT_RUN | 发布准入缺一项硬证据 | 由有 `actions:write` 权限者 dispatch（步骤见准入文档 §1.4） |
| A-02 | CI 侧真实 LLM 无 Secret | BLOCKED | Release Gate 不能用 LLM 证据 | 配置仓库 Secret 后重跑 Nightly |
| A-03 | LICENSE 缺失 | NEEDS_DECISION | 法律风险，外部不可按任何条款使用 | 负责人决策后补 `LICENSE` |
| A-04 | PR #1 / #2 仍 open | 待处理 | 与本分支有祖先关系，若分别合并会造成重复/冲突 | 由 RUN-019-R2 PR 统一承载，合并后关闭 #1/#2 |
| A-05 | 证据资产 241.7 MB 入库 | NOTE | 评审与克隆成本高 | RUN-020 起“摘要入库 + 原始证据走 artifact” |
| A-06 | 3 张同字节截图（full-loop 03/04/05） | NOTE | 截图不能证明步骤差异 | RUN-020 证据规范：步骤级可区分截图 |
| A-07 | a11y 证据标签未更新轮次 | NOTE | 追溯时易误读为 RUN-018 证据 | 脚本增加 `--label` 参数，R2 证据补注 |
| A-08 | 匿名 GitHub API 403 限流 | BLOCKED | 无法用 API 导出 job log（B-27 同源） | 有令牌后用 `gh run view --log` |
| A-09 | 历史版本无 `authoring_hash` 基线 | 已知残余风险 | 无法追溯更早的改库 | 已由 R2 报告记录，不重复处理 |

## 9. 审计复现命令

```bash
# 1 基线与干净度
git rev-parse HEAD && git ls-remote origin refs/heads/workbuddy/run019-product-completion
git status --porcelain=v1 -uall | wc -l
git rev-list --left-right --count origin/main...HEAD

# 2 七提交
git log --oneline 61ffd4a..HEAD

# 3 差异规模
git diff --shortstat origin/main...HEAD
git ls-files | wc -l

# 4 入库体积与重复
git ls-files -z | xargs -0 stat -f '%z %N' | sort -rn | head

# 5 本地门禁
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check apps packages scripts tests
.venv/bin/python -m ruff format --check apps/api packages scripts tests

# 6 真实 LLM（有本地凭据时；无凭据会打印 BLOCKED 退出码 1）
.venv/bin/python scripts/llm_smoke.py
```

## 10. 本阶段改动清单

| 文件 | 改动 |
|---|---|
| `README.md` | CI 编号回填 #115/#68 @ `564b21c` |
| `docs/FEATURE-STATUS.md` | 补全 R2 提交链（+`7988210`/`564b21c`），CI 编号回填 |
| `docs/RUN-019-FINAL-REPORT.md` | §6 追加收口后复验行（历史行保留） |
| `docs/RUN-019-R2-GAP-CLOSURE.md` | §5 追加两行 CI 记录 |
| `docs/RUN-020-PREPARATION-AUDIT.md` | 本文件（新增） |
| `docs/assets/run020-prep/llm/llm-smoke-20261127.json` | 真实 LLM smoke 证据（新增，无密钥） |

> 本轮未改动任何业务代码、测试、CI 配置与历史证据文件。
