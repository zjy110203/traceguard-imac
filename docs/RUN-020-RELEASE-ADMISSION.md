# RUN-019-R2 发布准入：Reliability Gate / 真实 LLM / PR 准备

> 分支 `workbuddy/run019-product-completion`，HEAD `564b21c`（审计见
> `docs/RUN-020-PREPARATION-AUDIT.md`）。本文件只处理“发布前必须由有凭据的人/环境完成”的三件事，
> 每件都给**准入结果 + 可执行步骤 + 判定规则**。**不降低任何要求，不伪造任何证据。**

## 0. 准入总览

| 编号 | 事项 | 当前状态 | 阻塞原因 | 解除条件 |
|---|---|---|---|---|
| R2-B1 | Reliability Gate（CI 长稳 62 分钟） | **NOT_RUN** | `workflow_dispatch` 需 Actions 写权限；本机无 `gh`、无授权令牌 | 有权限者 dispatch 默认参数并全步骤通过 |
| R2-B2 | 真实 LLM（Release 证据） | **CI: BLOCKED / 本地: PASSED** | CI 仓库未配置 `DEEPSEEK_API_KEY` | 配置仓库 Secret 后 Nightly `llm-smoke` 产生非 BLOCKED 证据 |
| R2-B3 | RUN-019-R2 正式 PR | **NOT_RUN** | 未授权创建；本机无 `gh` | 授权后按 `docs/RUN-020-PR-DESCRIPTION.md` 创建（不自动合并） |

---

## 1. R2-B1：Reliability Gate 准入

### 1.1 工作流事实（源码核对）

文件：`.github/workflows/reliability-gate.yml`（6044 B / 157 行）

| 项 | 值 |
|---|---|
| 触发 | **仅 `workflow_dispatch`**（无 push / PR / schedule） |
| 输入 | `soak_minutes`（默认 `"62"`，描述要求 `>=60`）、`min_runs`（默认 `"100"`）、`workers`（默认 `"3"`） |
| 并发 | `group: reliability-gate`，`cancel-in-progress: false`（长稳不会被后来者取消） |
| Runner | `ubuntu-latest`，`timeout-minutes: 120` |
| 数据库 | Service Container `postgres:16`，启动前校验 `server_version` 必须 `16.x` |
| 关键步骤 | ① 安装（含 `pg_dump/pg_restore`）② Playwright Chromium（缺则真实失败）③ 前端构建 ④ PG16 版本校验 ⑤ **`soak_run013.py --minutes 62 --workers 3 --min-runs 100`** ⑥ Regression 10 进程并发 ⑦ Artifact 生命周期 + 10k Evidence ⑧ 备份恢复（SQLite + PG16 真实 dump/restore）⑨ UI 全链路 `--profile full` ⑩ 诊断（always）⑪ 证据上传（always，`reliability-evidence`，保留 30 天） |
| 失败语义 | 任一步失败 → Job 真实失败；`if: always()` 只用于上传证据，不做掩盖 |

**与本地 62 分钟 soak 的关系**：本地证据（`docs/assets/run019/wp5/soak/soak-62min.json`，
墙钟 62.16 分钟 / 122 个真实 Run / 33/33 checks PASS）证明脚本与流程可用，
**但不等于 CI 实跑**（环境、PG 容器、并发度、Worker 拓扑不同）。两者不可互相替代。

### 1.2 所需权限（精确）

| 方式 | 需要的权限 | 说明 |
|---|---|---|
| GitHub UI「Run workflow」 | 仓库 **Write** 及以上（Admin/Maintain/Write） | 私有库还需 Actions 启用 |
| `gh workflow run` | 同上；`gh` 需 `repo` + `workflow` scope 的登录态 | `gh auth login` 或 `GH_TOKEN`（PAT 含 `workflow`） |
| REST API | PAT/App token 需 `actions:write`（经典 PAT 还需 `repo`） | `POST /repos/{owner}/{repo}/actions/workflows/{workflow_id}/dispatches` |
| 组织策略 | Actions 允许 `workflow_dispatch`；若开了 "Require approval for all outside collaborators" 需审批 | 由仓库管理员确认 |

**本机现状（已核实，未尝试绕过）**：

- `gh` 未安装（`command -v gh` → not found）；
- 会话内无 `GITHUB_TOKEN`/`GH_TOKEN` 授权（未读取任何凭据文件；读取凭据存储的行为已被安全策略拦截，
  本审计不重试、不绕过）；
- 匿名 GitHub API 已 403 限流（`api.github.com` 返回 `rate limit exceeded`），
  只能读 `github.com` 页面（PR Gate #115 / Nightly #68 即由此核实）。

→ **结论：本机不具备合法 dispatch 条件，R2-B1 = NOT_RUN（外部阻塞），不放宽、不代跑。**

### 1.3 负责人执行步骤（复制即可）

**方式 A：网页（最简）**

1. 打开 `https://github.com/zjy110203/traceguard-imac/actions/workflows/reliability-gate.yml`
2. 右上「Run workflow」→ Branch 选 `workbuddy/run019-product-completion`
3. 三个输入保持默认：`soak_minutes=62`、`min_runs=100`、`workers=3`（**不得下调**）
4. Run → 等待约 75–110 分钟（超时上限 120 分钟）

**方式 B：CLI**

```bash
gh auth status                                   # 确认已登录且有 workflow scope
gh workflow run reliability-gate.yml \
  --repo zjy110203/traceguard-imac \
  --ref workbuddy/run019-product-completion \
  -f soak_minutes=62 -f min_runs=100 -f workers=3

# 跟踪
gh run list --repo zjy110203/traceguard-imac --workflow=reliability-gate.yml --limit 3
gh run watch <run_id> --repo zjy110203/traceguard-imac --exit-status
```

**方式 C：REST API**

```bash
curl -X POST \
  -H "Authorization: Bearer $GH_TOKEN" \
  -H "Accept: application/vnd.github+json" \
  https://api.github.com/repos/zjy110203/traceguard-imac/actions/workflows/reliability-gate.yml/dispatches \
  -d '{"ref":"workbuddy/run019-product-completion","inputs":{"soak_minutes":"62","min_runs":"100","workers":"3"}}'
```

### 1.4 结果判定（硬条件，逐条核）

| # | 判定项 | 通过条件 |
|---|---|---|
| 1 | Run 结论 | `conclusion = success`（不是 `cancelled`/`neutral`） |
| 2 | 时长 | ≥ 62 分钟 soak 段真实执行（总时长通常 ≥ 75 分钟）；明显偏短视为未真实执行 |
| 3 | soak 证据 | artifact 中 `soak-*.json`：`minutes ≥ 62`、`runs ≥ 100`、`checks_passed == checks_total`（33/33）、`failed == 0`、全部 Run 处于终态 |
| 4 | Worker | 报告内 `workers == 3`，且存在 ≥3 个独立 worker 日志 |
| 5 | Regression 并发 | `regression-multiprocess.json` 10 进程全部通过 |
| 6 | Artifact 规模 | `artifact_gc_bench` 10k 规模一致（`--fail-on-inconsistent` 未触发） |
| 7 | 备份恢复 | `backup-restore` 结果全项 PASS（SQLite + PG16） |
| 8 | UI 全链路 | `--profile full` 全项 PASS（含独立 Worker 故障注入段），有截图与 JSON |
| 9 | 证据留存 | artifact `reliability-evidence` 已上传且可下载（30 天） |

任一不满足 → **FAIL / INCONCLUSIVE**，不得写成“通过”；修复后重跑，不得调低参数。

### 1.5 失败排查顺序

1. `postgres` 服务未就绪 → 看 `pg_isready` 重试次数（20×5s）；
2. Chromium 安装失败 → `playwright install --with-deps` 的 apt 源问题（步骤会真实失败，不会静默跳过）；
3. soak 中途 Run 未终态 → 取 `soak-*.json` 的 `stuck_runs` 与 worker 日志；
4. UI full profile 超时 → `timeout 2400` 上限，看截图与 `ui/*.json` 最后一个 PASS 项。

---

## 2. R2-B2：真实 LLM 接入

### 2.1 三层来源口径（必须严格区分）

| 级别 | 定义 | 代码/证据特征 | 能否作为 Release 证据 |
|---|---|---|---|
| **RULE** | 纯确定性规则，无模型调用 | `RuleBasedPlanner.version="rule-based-v1"`；`suggested_by="rule"`；`llm_meta.provider="rule"` | ✅ 可（但必须标明不是 AI 能力） |
| **FAKE_PROVIDER** | 测试替身，返回固定/脚本化 payload，不发网络请求 | `tests/unit/test_llm_advisors.py::FakeProvider` 等；无 `usage` 真实 token、无真实延迟 | ❌ **绝不可**（不得计入能力指标或 Release） |
| **REAL_PROVIDER** | 真实 HTTPS 调用计费模型，返回真实 usage/latency | `UsageRecord.request_status="ok"` + `input_tokens>0` + `latency_ms>0`；`.traceguard/llm_usage.jsonl` 有记录 | ✅ 可（须标注模型/时间/HEAD） |

降级链：`REAL_PROVIDER` 失败（超时/限流/无效 JSON/预算耗尽）→ 显式 `LLMError(kind)` →
调用方回落到 `RULE` 并在结果里写 `fallback` 原因，**不得把 fallback 写成模型成功**。

### 2.2 Provider 配置（源码事实）

`packages/core/traceguard_core/config.py`（pydantic-settings，`env_prefix="TG_"`，读取项目根 `.env`）：

| 变量 | 默认 | 说明 |
|---|---|---|
| `DEEPSEEK_API_KEY` 或 `TG_DEEPSEEK_API_KEY` | 空 | **唯一密钥入口**；只在内存中使用，`public_dict()` 输出 `***REDACTED***` |
| `TG_LLM_PROVIDER` | 空 | `deepseek` 才启用；空 = 不启用（不伪装 LLM） |
| `TG_LLM_BASE_URL` | `https://api.deepseek.com` | OpenAI 兼容端点 |
| `TG_LLM_DEFAULT_MODEL` | `deepseek-flash` | 常规任务 |
| `TG_LLM_REASONING_MODEL` | `deepseek-v4-pro` | 推理/升级任务 |
| `TG_LLM_TIMEOUT_S` | `60` | 单请求超时 |
| `TG_LLM_MAX_RETRIES` | `2` | 仅 429/5xx/网络错误有限重试（≤6s 退避） |
| `TG_LLM_DAILY_BUDGET_USD` | `1.0` | 应用侧预算（非平台硬顶），超额 → `budget_exhausted` |
| `TG_LLM_MAX_CALLS_PER_RUN` | `24` | 单 Run 调用上限 |
| `TG_LLM_EXPLORER_ENABLED` | `true` | Explorer planner |
| `TG_LLM_DIAGNOSIS_ENABLED` | `true` | 诊断顾问 |
| `TG_LLM_COMPILER_ENABLED` / `TG_LLM_COMPILER_MODE` | `true` / `rule_only` | 编译模式：`rule_only` / `llm_only` / `llm_plus_rule_validation` |
| `TG_LLM_PROBE_PLANNER_ENABLED` | `false` | 探针规划（默认关，产生成本） |
| `TG_LLM_REPAIR_PLANNER_ENABLED` | `false` | 修复规划（默认关，产生成本） |

> 现状实测（脱敏读取）：`provider=deepseek`、`default=deepseek-flash`、`reasoning=deepseek-v4-pro`、
> `compiler_mode=rule_only`、`probe/repair planner=false`。即**默认保守**：只有 Explorer/诊断顾问会用模型。

### 2.3 Secret 配置步骤（本地 / CI）

**本地（开发与验收）**

```bash
# 1. 复制模板（.env 已被 .gitignore 排除，禁止入库）
cp .env.example .env

# 2. 编辑 .env —— 只填这两行（其余保持默认）
#    DEEPSEEK_API_KEY=<在 DeepSeek 控制台创建的 key>
#    TG_LLM_PROVIDER=deepseek

# 3. 确认没有被 git 跟踪（必须输出空）
git check-ignore -v .env
git ls-files .env

# 4. 确认脱敏生效（不得打印真实 key）
.venv/bin/python -c "from traceguard_core.config import get_settings; \
print(get_settings().public_dict()['deepseek_api_key'])"
# 期望输出：***REDACTED***
```

**CI（GitHub Actions）**

1. 仓库 → Settings → Secrets and variables → Actions → **New repository secret**
2. Name：`DEEPSEEK_API_KEY`；Secret：粘贴 key（**只粘一次，页面不会回显**）
3. 组织级可用 Organization secret（需授予本仓库访问权）
4. 轮换：控制台新建 key → 更新 Secret → 观察下一次 Nightly → 再吊销旧 key
5. 禁止：写进 workflow yaml、写进 `docs/`、写进提交信息、`echo $DEEPSEEK_API_KEY`

> Nightly 的 `llm-smoke` Job 只在 **Secret 存在**时才真实调用；缺失时 Job 仍 success 但
> 结果写 `BLOCKED`，并由 `gate-verdict` 打印 “LLM smoke BLOCKED（缺 Secret）；该证据不得用于 Release 放行”。

### 2.4 真实 Smoke 执行命令

```bash
# 本地（真实调用，无凭据时打印 BLOCKED 并返回 1）
.venv/bin/python scripts/llm_smoke.py
# 结果落盘：.workbuddy-run/llm-smoke-results.json（不含密钥）

# CI（有 Secret 时自动执行）
# Nightly Gate → Job: llm-smoke → Artifact: llm-smoke-status
```

**11 项检查**（缺一不可）：`connectivity_models`、`configured_models_exist`、`flash_text_call`、
`token_usage_recorded`、`flash_json_output`、`pro_reasoning_call`、`timeout_boundary`、
`truncated_output_handled`、`cost_estimated_marked`、`key_redaction_settings`、`key_redaction_error_msg`。

### 2.5 结果验证规则（PASSED 的硬条件）

| # | 条件 | 证据位置 |
|---|---|---|
| 1 | 11/11 `ok=true` | 结果 JSON |
| 2 | 至少 1 次真实文本调用，`input_tokens>0 且 output_tokens>0` | `flash_text_call` detail |
| 3 | 至少 1 次推理模型真实调用（`model=deepseek-v4-pro`） | `pro_reasoning_call` detail |
| 4 | 延迟为真实量级（文本 ≥100ms、推理 ≥500ms）；**<10ms 视为伪造** | detail 的 `xxxms` |
| 5 | JSON 输出可解析且含约定键 | `flash_json_output` |
| 6 | 失败路径被真实覆盖：超时 `kind=timeout`、截断 `kind=invalid_json` | 对应两项 |
| 7 | 成本标记为估算（`cost_is_estimated=true`），且与 token 数一致 | `cost_estimated_marked` |
| 8 | 密钥脱敏：`public_dict` 与错误信息都不含 key | 两个 redaction 检查 |
| 9 | 证据带 **HEAD + 时间戳 + 命令**，且不包含密钥 | 归档 JSON |

**状态判定矩阵**

| 场景 | 判定 | 允许的表述 |
|---|---|---|
| 无 key，脚本打印 `DEEPSEEK_API_KEY 未配置` | **BLOCKED** | “未执行真实调用” |
| 有 key，11/11 且满足上表 | **PASSED（REAL_PROVIDER）** | “真实模型调用通过（HEAD/时间/模型）” |
| 部分失败（如 rate_limit） | **FAILED / INCONCLUSIVE** | 如实记录 kind，不得降级为“规则通过” |
| 只有 FakeProvider 单测 | **NOT_REAL** | 只可称“离线单测通过” |

### 2.6 当前状态与证据

| 环境 | HEAD | 结果 | 证据 |
|---|---|---|---|
| **本地** | `564b21c` | **PASSED（11/11）**：Flash `38in/77out 700ms`、Pro `4789ms`、`calls=5 cost=$0.000639` | `docs/assets/run020-prep/llm/llm-smoke-20261127.json` |
| **CI** | `564b21c` | **BLOCKED（无仓库 Secret）** | Nightly #68 `llm-smoke` Job + gate-verdict 注释 |

> 结论：**Provider 接入本身已被真实调用验证**（本地）；**Release 准入仍为 BLOCKED**，
> 因为 CI/生产侧没有 Secret，无法产出可放行的 CI 证据。

---

## 3. R2-B3：正式 PR 准备

材料已单独产出：**`docs/RUN-020-PR-DESCRIPTION.md`**（标题、变更摘要、测试结果、风险清单、
历史分支整合、迁移与回滚、合并检查表）。本节只记录准入状态与创建方式。

| 项 | 状态 |
|---|---|
| 分支已推送 | ✅ `origin/workbuddy/run019-product-completion` = `564b21c` |
| PR 是否存在 | ❌ 未创建（现有 open PR 为 **#1**、**#2**，均早于本分支） |
| 授权 | 未获得“创建 PR”的明确授权；本机亦无 `gh` |
| 自动合并 | **禁止**（本轮明确不合并） |

创建命令（授权后执行）：

```bash
gh pr create \
  --repo zjy110203/traceguard-imac \
  --base main \
  --head workbuddy/run019-product-completion \
  --title "RUN-019-R2: 产品收口、UI E2E 修复与发布准入（含 RUN-008~RUN-019）" \
  --body-file docs/RUN-020-PR-DESCRIPTION.md
# 不要加 --auto / 不要 squash 合并；HOLD 状态由人工复核后决定
```

**关键提醒**：PR #1、#2 的分支是本分支的**祖先**（`merge-base = origin/main`），
三者不可分别合入 main（会造成重复提交/冲突）。正确做法：以本 PR 为唯一入口，
合并后关闭 #1/#2，并在 #1/#2 留评论指向本 PR。

---

## 4. 三项准入的依赖关系

```
R2-B2 (Secret)  ──┐
                  ├──> Nightly 全绿（llm-smoke 非 BLOCKED）──┐
R2-B1 (Actions 权限) ─> Reliability Gate 全绿 ────────────────┼──> 允许合并 / 发布
                  │                                          │
R2-B3 (PR 授权) ──┴──> PR 评审通过（HOLD 解除）───────────────┘
```

- 三者互不替代：LLM PASS 不能替代长稳；长稳不能替代 PR 评审；PR 存在不等于可合并。
- 任一项 BLOCKED，发布结论只能是 **“代码可评审、不可发布”**。
