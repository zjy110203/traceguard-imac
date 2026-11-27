# RUN-020-PREPARATION WORKLOG（2026-11-27）

> 分支 `workbuddy/run019-product-completion`，基线 `564b21c`（本地 = 远端）。
> 本轮定位：**最终交付审计 + 发布准入材料 + RUN-020 设计**；不改业务代码。

## 0. 执行顺序

1. 核对基线（分支 / HEAD / 远端 / 工作区 / 相对 main）；
2. 逐项审计（R2 七提交、文档一致性、临时产物、历史证据）；
3. 复核门禁（本地全量测试、ruff、CI 运行页）；
4. 处理发布准入三件事（Reliability / 真实 LLM / PR 材料）；
5. 产出 RUN-020 设计与工作包；
6. 回填文档与台账、提交。

## 1. 基线核对

- 本地 HEAD = 远端分支 HEAD = `564b21c8280f9ce9f6e925715fd973d74ad570b4`；
- 工作区干净（`git status --porcelain` 0 行，无未跟踪）；
- `origin/main = ac2876e`，`merge-base = ac2876e`，**0 behind / 172 ahead**，历史线性（0 merge）；
- 差异规模：2967 文件 / +313,389 −2,354；入库 3294 文件 ≈254 MB（`docs/assets` 占 241.7 MB）。

## 2. 审计发现

| 类别 | 发现 | 处理 |
|---|---|---|
| 文档不一致 | README / FEATURE-STATUS / FINAL-REPORT / GAP-CLOSURE 的 CI 编号停在 `#113/#66 @ 19f527f`，落后 2 个提交 | 回填 `#115/#68 @ 564b21c`，历史行保留 |
| 提交链 | FEATURE-STATUS 的 R2 提交链漏 `7988210` / `564b21c` | 补全 |
| 临时产物 | `.pytest-tmp` 4.0 GB、`.traceguard` 9.5 GB、`logs` 1.9 MB（均 gitignore，未入库） | 仅记录，不删除 |
| 冗余入库 | 1 个 `.zip` 与其 `.md` 重复；103 个重复文件 / 5.36 MB | 记录，本轮不动历史证据 |
| 证据区分度 | `full-loop/03/04/05` 三张不同步骤截图字节相同 | 记为 NOTE，写入 RUN-020 证据规范 |
| 追溯标签 | a11y 证据内部仍写 “RUN-018 WP8/R1” | 记为 NOTE（数量与时间戳可核） |
| 历史证据 | RUN-014~018 的 FINAL-REPORT + 专题 + assets 全部在位，未被改写 | PASS |
| 安全 | 无 `.env`/密钥/DB 入库；`public_dict()` 对 key 输出 `***REDACTED***` | PASS |

## 3. 门禁复核

| 项 | 结果 |
|---|---|
| `pytest -q` | 1144 passed / 16 skipped / 0 failed（382.40s） |
| `ruff check` / `format --check` | 全绿 / 281 files |
| PR Gate #115 @ `564b21c` | SUCCESS 3m40s |
| Nightly #68 @ `564b21c` | SUCCESS 6/6（ui-acceptance 2m5s） |
| Nightly `llm-smoke` | Job SUCCESS，真实调用 BLOCKED（无仓库 Secret） |
| 本地真实 LLM | **11/11 PASS**（Flash 700ms、Pro 4789ms、5 calls、$0.000639） |

## 4. 发布准入

| 编号 | 结论 |
|---|---|
| R2-B1 | **NOT_RUN**：`workflow_dispatch` 需 Actions 写权限，本机无 `gh`/令牌；已给三种执行方式 + 9 条硬验收条件 |
| R2-B2 | **本地 PASSED / CI BLOCKED**：本地真实调用已证明接入可用；CI 缺 Secret |
| R2-B3 | **材料完成，PR 未创建**：`docs/RUN-020-PR-DESCRIPTION.md` |

> 关于“当前缺少 `DEEPSEEK_API_KEY`”：本轮实测**本地已配置可用凭据**（smoke 11/11），
> 仓库 Secret 未配置。全程未读取、未打印、未导出密钥；只运行仓库自带 smoke 脚本。

## 5. RUN-020 设计产出

- `docs/RUN-020-DESIGN.md`：能力矩阵（L0~L3）、架构分层、四条调用链、
  WP1~WP5 详设（文件作用域 / 接口 / 失败语义 / 验收 / 指标）、迁移 v15/v16、
  Holdout 治理、测试策略、依赖与风险。
- `docs/RUN-020-WORKPACKAGES.md`：30 个任务卡（M1~M5），含依赖 DAG、验收命令、规模、阻塞标记。
- 关键取舍：
  - Compiler 从“只有 trace”扩到“自然语言 → 可执行候选”，但**必须真实回放 ×2 才产候选**；
  - Diagnosis 只扩展“假设生成 + 裁决输入”，不重写状态机；多根因/冲突/弃源自始支持；
  - Repair **刻意停在 L1**：LLM 只产 Proposal，审批/晋升仍人工；
  - Benchmark 统一为一个入口 + manifest 强校验 + 失败样本只追加。

## 6. 本轮改动

仅文档与证据：

- 新增：`RUN-020-PREPARATION-AUDIT.md`、`RUN-020-RELEASE-ADMISSION.md`、`RUN-020-DESIGN.md`、
  `RUN-020-WORKPACKAGES.md`、`RUN-020-PR-DESCRIPTION.md`、`RUN-020-WORKLOG.md`、
  `docs/assets/run020-prep/llm/llm-smoke-20261127.json`；
- 修改：`README.md`、`docs/FEATURE-STATUS.md`、`docs/RUN-019-FINAL-REPORT.md`、
  `docs/RUN-019-R2-GAP-CLOSURE.md`、`.workbuddy-run/{current-task,progress,changes,test-results,phase-status,blockers}.md`；
- **无**业务代码、测试、CI 配置改动。

## 7. 下一步（M1 起点）

1. 切分支 `workbuddy/run020-intelligence`（从 `564b21c`）；
2. 按 `WP1-T1 → T4 → T5 → T7` 打基座（约 7 人日）；
3. 同时准备 `compiler-holdout-v1` / `diagnosis-holdout-v3` 数据（可与 M1 并行）；
4. 基座完成后 M2/M3/M4 并行，M5 收口；
5. 有权限者并行推进 REL-1/REL-2/REL-3。
