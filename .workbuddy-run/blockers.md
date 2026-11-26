# 阻塞（RUN-018，2026-11-27 00:40 CST）

| 编号 | 事项 | 状态 |
|---|---|---|
| B-28 | Nightly ui-acceptance 持续失败（#26~#33 全部失败，本地 35/35 通过） | NOT_CLOSED：需 CI job 日志定位 |
| B-26 | PR #3 创建 | NOT_RUN（无 gh CLI） |
| B-27 | gh/API 导出 CI 日志 | BLOCKED（无凭据） |
| B-24 | Release Gate 真实 LLM 证据 | NOT_RUN（缺 Secret） |
| B-25 | Reliability Gate 真实运行 | NOT_RUN（需手动窗口） |

---

# 阻塞（RUN-017，2026-11-26 21:55 CST）

| 编号 | 事项 | 状态 |
|---|---|---|
| B-28 | Nightly `ui-acceptance` 在 GitHub runner 失败（本地同脚本 35/35、58/58 通过） | NOT_CLOSED：定位需 `gh run view --log-failed`（本机无凭据）；已拆 profile + 硬超时 + Job Summary，下轮第一步取日志 |

| B-17 | Worker 补充故障注入 | BLOCKED（沿用历史决定，不绕过） |
| B-21 | 宿主级网络隔离（pf/防火墙） | BLOCKED（安全边界禁止） |
| B-22 | 真实外部站点验收 | BLOCKED（仅本地目标） |
| B-23 | LLM 四臂 / 人工基线 | BLOCKED（无授权凭据/人员） |
| B-24 | Release Gate 真实 LLM 证据 | NOT_RUN（缺 Secret） |
| B-25 | Reliability Gate 真实运行 | NOT_RUN（需手动 120 分钟窗口） |
| B-26 | PR #3 创建 | NOT_RUN（本机无 gh CLI；描述已生成） |
| B-27 | `gh`/API 导出 CI 日志 | BLOCKED（无凭据，仅能读页面） |

---

> **RUN-015 阻塞状态（2026-11-26 追加，历史保留）**：
> - B-17 Worker 补充故障注入：**BLOCKED**（安全审批未获，未重试未绕过）。
> - 宿主级网络隔离 / 外部真实站点：**BLOCKED**（无容器与授权）。
> - 真实人工诊断基线 / LLM 四臂成本实验：**BLOCKED / NOT_RUN**（无评审资源与计费授权）。
> - INCONCLUSIVE 重试语义：**NEEDS_DECISION**（不改状态机；`regression_retry_allowed=false`）。
> - Benchmark 23×2 新一轮：**NOT_RUN**（不与 60 分钟长稳共享资源）；False Pass 保持 PARTIAL。
> - Release Gate：**NOT_RUN**（需 workflow_dispatch 与凭据）；最终代码 HEAD `c12ccab` 的
>   PR Gate `45781959624` 与 Nightly `45781958909` 均 success，见 `docs/RUN-015-FINAL-REPORT.md` §7。
> - RUN-015 长稳首轮 1 FAIL / 1 NOTE 与复验轮 2 NOTE 均已在 WP5 报告如实披露，不改写原始证据。
> - PR #1 / #2 集成策略、LICENSE：**NEEDS_DECISION**，未合并未关闭。

> **RUN-011-R1.1 阻塞状态（2026-11-25 追加，R1 历史保留）**：
> - B-17（Worker 四补充故障场景）：**仍 BLOCKED**。遵守 §八 8.2，未实施被拦操作、
>   未更换工具规避、未拆分绕过、未导出凭据；既有 17/17 故障恢复 + 4 Worker 并发证据保持有效。
> - UI 无 workspace 切换控件：**结构性缺口（如实登记）**。前端硬编码 `workspace_id=default`，
>   跨 Workspace 边界由 API 层隔离测试（23 passed）覆盖，不伪造成 UI 通过。
> - UI 审核成功路径：**未由 UI 点击覆盖**。UI 手工用例因缺 Oracle 绑定/稳定性回放被服务端门禁
>   正确拒绝；成功路径由两轮闭环脚本（86/86 ×2）验证，不绕过门禁。
> - CI：**未标记 PASS**。无真实 GitHub Actions 运行证据；未合并 PR、未改 main。

---

> **RUN-011-R1 阻塞状态（2026-11-25）**：
> - B-01 B-17（Worker 四真实故障场景）：**仍 BLOCKED**（安全拦截未解除；本轮零次触碰被拦操作，
>   正式安全批准申请材料已备：`docs/RUN-011-R1-WP7-B17-SECURITY-REQUEST.md`）。
> - B-02 宿主级网络隔离：**仍 BLOCKED**（无 pf 权限）。
> - B-03 Benchmark LLM 臂（统计级使用）：**仍 BLOCKED**（无计费授权；LMP 记录性对照已含 model/prompt_version）。
> - B-04 人工基线：**仍 BLOCKED**（无真人）。
> - B-05 LICENSE：**NEEDS_DECISION**（不变）。
> - B-06 CI 触发 / 增量 PR：**仍 BLOCKED**（无 gh/凭据，不读取凭据）。
> - **R1 解除/修复**：OPEN-RISK-01（Egress 误拦，根因修复+复验 6/6）、OPEN-RISK-02（时序误确认，
>   三态+单查询降权+四场景复测）、OPEN-RISK-03（LLM 候选未去重，dedup 合并+5 场景回归）、
>   OPEN-RISK-04（PG 并行，双根因修复；保留 1 项 SQLite 并发 DDL 锁 flake 限制）。
> - NOT RUN（保持）：UI 全链路点击验收、多 API/DB 断连/长事务、生产部署验收、pytest-xdist 形态。
> 以下历史记录保留不覆盖。

# 阻塞项（RUN-002）

> **RUN-011 阻塞状态（2026-11-25 02:25 CST）**：
> - B-01 B-17（Worker 四真实故障场景）：**仍 BLOCKED**（安全拦截未解除，未绕过/未换工具/未拆写入）。
> - B-02 宿主级网络隔离：**BLOCKED**（无 pf/容器权限）；已验证的仅是应用层三层防护。
> - B-03 Benchmark LLM 臂：**BLOCKED**（无计费授权）。
> - B-04 人工基线：**BLOCKED**（无真人）。
> - B-05 LICENSE：**NEEDS_DECISION**。
> - **新增 NOT RUN**：Benchmark v5（v4 探针臂测量无效，需先修测量口径）、UI 全链路点击验收、
>   多 API 实例/DB 断连/长事务、生产部署验收。
> - **新增 BLOCKED**：CI 触发（本机无 `gh`，且不读取/导出凭据）。
> - **新增 OPEN RISK**：OPEN-RISK-01 授权目标 https 偶发 PORT_NOT_ALLOWED（7 次中 2 次）；
>   OPEN-RISK-02 时序故障错误 CONFIRMED；OPEN-RISK-03 LLM 候选未去重导致结论漂移；
>   OPEN-RISK-04 全量并行测试偶发 PG 资源竞争（串行稳定）。
> - 已解除（本轮）：无（未新增安全/权限阻塞解除）。
> 以下历史记录保留不覆盖。

> **RUN-010-FINAL-CLOSEOUT 阻塞状态（2026-11-25 00:30 CST）**：
> - B-01 B-17（Worker 四真实故障场景）：**仍 BLOCKED**（安全拦截未解除，未绕过、未换工具、未改参数重试）。
> - B-02 宿主级网络隔离：**BLOCKED**（本机无 pf/防火墙权限）；已验证的仅是应用层三层防护。
> - B-03 Benchmark LLM 臂：**BLOCKED**（无计费授权）。
> - B-04 人工基线：**BLOCKED**（无真人）。
> - B-05 LICENSE：**NEEDS_DECISION**（不擅自选定条款）。
> - PARTIAL：Benchmark v4 五个场景缺口、PG 本地运行（skip）、无障碍键盘/读屏/动效覆盖。
> - 已解除（本轮）：CL-01 CI 挂死（#已修 d4d59ba）、CL-02 登录 E2E 缺前端产物（#已修 d4d59ba）。
> 以下历史记录保留不覆盖。

> **RUN-010 安全事件与阻塞状态（2026-11-24 22:05 CST）**：
> - **B-20 AgentDR info_theft_steal_env_keys 拦截**：WP8 闭环测试首次写入被拦两次。
>   根因：文件含 `subprocess` + `env=` 环境字典传递模式（父 shell 环境可能含真实凭据，
>   向子进程传递被识别为凭据外传）。**未绕过、未改参数重试被拦内容、未触碰 AgentDR 策略**。
>   处置：改用进程内实现（monkeypatch.setenv + TestClient + 直接服务调用，零子进程/零环境字典），
>   与已过审的 test_auth_rbac_run010.py 同构——该写入获放行，WP8 闭环已恢复推进。
>   全程只用虚拟测试口令，未读取/打印/传递任何真实凭据。
> - B-17（Worker 四真实故障场景）：仍 BLOCKED（AgentDR 拦截未解除，未绕过），契约测试 4/4 保留。
> - 宿主级网络隔离：BLOCKED（macOS 无 pf 权限；用户态代理三层防护已验证，不外推）。
> - Benchmark LLM 臂 / 人工基线：BLOCKED（无计费授权 / 无真人），不冒充。
> - LICENSE：NEEDS_DECISION（建议已出，待负责人选定，不擅自选定条款）。
> - Benchmark v4 两轮已真实执行（C 臂 64.3%）；5 个场景缺口如实记录于 WP5 报告。
> 以下历史记录保留不覆盖。

> **RUN-009 最终状态（2026-11-24）**：B-17 四个 Worker 故障场景仍 **BLOCKED**（AgentDR 拦截未解除，本轮未绕过、未重试）。
> 宿主级出站封锁 BLOCKED（无防火墙权限）；axe 自动化无障碍审查 BLOCKED（无 axe-core）；
> Benchmark LLM 臂 BLOCKED（未获计费授权）；人工基线 BLOCKED（无真人）；vue-tsc@3 升级 NEEDS_DECISION。
> 其余 RUN-009 目标已完成，见 `docs/RUN-009-FINAL-REPORT.md`。以下历史记录保留不覆盖。

> **RUN-009 WP0 权威状态摘要（2026-11-24 16:05 CST）**：下方表格为历史记录。
> 当前权威起点 HEAD `cb80591`；B-17（四个 Worker 补充故障场景，AgentDR 两次拦截）保持 **BLOCKED**，不得换工具/拆写入/改参数规避；
> B-18（60 分钟长稳）已于 RUN-008 解除，下方「仍在运行、JSON 未生成」为陈旧中间态；
> LLM smoke、Benchmark LLM/人工基线 BLOCKED；真实认证/RBAC 为 NOT_IMPLEMENTED；dev-only moderate×3 已登记待 WP8 处置。
> 详见 `docs/RUN-009-BASELINE.md`。以下历史记录保留不覆盖。

| 编号 | 阻塞 | 影响 | 处理 |
|---|---|---|---|
| B-01 | 无 LLM API Key / 本地模型 | Benchmark B/D 组的 LLM 基线、LLM 生成用例无法执行 | 代码留 ModelGateway 接口；实验标记 BLOCKED，不产数值 |
| B-02 | 本机无 Docker / PostgreSQL 服务 | PG 运行时行为未验证 | Schema/迁移做方言分支；compose 提供部署配置 |
| B-03 | 外部应用泛化未授权（P-EXT） | 阶段12 真实站点验证 | 仅 TeamFlow 本地目标 |
| B-04 | P03 / P08 / 路线冻结需负责人批准 | 契约冻结 | 输出决策提案，不擅自写入 |
| B-05 | 无真实人类工程师 | 手工修复基线 | 标记 BLOCKED |

---

## RUN-005 更新

| 编号 | 阻塞 | 影响 | 处理 |
|---|---|---|---|
| B-01 | LLM Key 已配置，但多臂 Benchmark 成本/时长受限 | v3 对照无法本轮完成 | 分批计划写入 Benchmark 报告；未输出任何准确性数字 |
| B-02 | 本机无 Docker / PostgreSQL | PG 运行时仍无法本地验证 | parity 测试 + CI Service Container；本地 skip |
| B-06 | 60 分钟长稳 / 4 Worker 复验需独立时间窗 | 长稳证据缺失 | 标记 BLOCKED/PARTIAL，不引用旧结论 |
| B-07 | `vite` dev-only high 漏洞仅能 semver-major 修复 | CI Nightly security job 会失败 | 记录风险与选项，等待决策；不 `--force` |
| B-08 | P03 / P08 未批准 | Oracle 契约与 Repair RBAC 不能改 | 决策包已产出，保持现状 |

## RUN-006 更新

| 编号 | 阻塞 | 影响 | 处理 |
|---|---|---|---|
| B-02 | 本机无 Docker / PostgreSQL | PG 行锁与迁移 v7 未实测 | parity 测试 skip；请求授权 CI 触发（D-07） |
| B-06 | 60 分钟长稳 / 4 Worker 需独立时间窗 | 长稳与故障恢复无本轮证据 | 标 BLOCKED，不引用历史结论（D-08） |
| B-07 | `vite` dev-only high 漏洞仅能 semver-major 修复 | CI Nightly 会失败 | 未升级，请求决策（D-05） |
| B-08 | P03/P06/P08 未批准 | 冲突裁决、预算数值、RBAC 不能冻结 | 决策请求已产出，保持现状 |
| B-09 | 无身份认证基础设施 | 审批人身份不可强验证 | 仅主体类型门禁，安全文档明确披露（D-01） |
| B-10 | 前端工作包仅部分排期 | 诊断视图已升级并验收；其余 13 视图未审查 | 标 PARTIAL，不声称全面改善 |

## RUN-007-RESUME 更新

| 编号 | 阻塞 | 影响 | 处理 |
|---|---|---|---|
| B-11 | 本机无 GitHub 凭据（无 token / SSH key / `gh`） | 分支无法推送、PR 无法创建、CI 无法触发 | 本地提交保留；请求授权（D-02）；报告中标 BLOCKED |
| B-12 | 本机无 Docker / PostgreSQL | PG16 平台用例无法本地执行 | 6 用例 + Nightly PG16 作业就绪；本地 skip 并打印原因 |
| B-13 | 沙箱禁止 `ps` 且 macOS 无 `/proc` | 长稳无法采集 CPU/RSS | 回退为存活探测并标注 `source=liveness_only`；不伪造 0 |
| B-14 | 前端主题走 localStorage | 只设 `prefers-color-scheme` 会得到假深色截图 | 已修：init script 写入 + 亮度断言 |
| B-15 | 人工诊断基线无人力 | Benchmark 少一条对照臂 | 标 BLOCKED，不用 Mock 补齐 |
| B-16 | LLM 模型由服务端路由决定 | 无法在同一进程内切 Flash/Pro | 按批次重启 + 记录实际 `models_seen`；不外推 |

## RUN-007-RESUME 阻塞解除（2026-11-23 深夜）

| 编号 | 原阻塞 | 现状 |
|---|---|---|
| B-11 | 无 GitHub 凭据 | **已解除**：用户临时提供 fine-grained PAT（单仓库，Contents/Workflows/Pull requests/Actions 写权）；push、PR、dispatch 全部完成。**收尾须撤销该 token** |
| B-12 | 本机无 Docker / PG | 仍存在（本机 skip），但 **CI 的 postgres:16 已真跑**，6 个平台用例通过 → 该验证不再阻塞 |

## RUN-008 更新（2026-11-24，AgentDR 安全拦截）

| 编号 | 阻塞 | 影响 | 处理 |
|---|---|---|---|
| B-17 | `Write` 创建 `scripts/worker_fault_matrix_run008.py` 被 BuddySentinel 以 `info_theft_steal_env_keys` 拦截 | WP3 的 4 个补充故障场景（领取后退出、租约过期、API 重启、Worker 全量重启）无本轮证据 | **跳过该操作，不重试、不改写规避**。文件未生成；工作区无残留。见下方根因与替代方案 |
| B-18 | 长稳实验仍在运行（cycle 15/60，约 15 分钟） | `docs/assets/run008/soak-60min.json` 尚未生成 | 保留后台任务 `BZ1Hp8`，不重启、不重复采样；完成后直接归档 |

### B-17 根因分析（仅分析源码，未读取任何真实凭据）

- 被拦截内容是一份**纯源代码**脚本，未硬编码任何密钥；触发点是源码中 3 处
  `env = { **os.environ, "TG_DATABASE_URL": ... }` / `env={**os.environ, ...}`：
  把**父进程完整环境变量**（可能含 API Key / Token / 密码）整体复制进
  `subprocess.Popen` 子进程，而子进程同时会访问本地 TeamFlow / API。
  该模式与「读取环境变量中的凭据并外传」的特征吻合，因此被安全策略拦截。
- 拦截发生在写入阶段：文件**未创建**（`ls scripts/worker_fault_matrix_run008.py` → No such file）。
  未读取、未打印、未复制、未上传任何真实凭据；CI 验证使用的是既有 git credential store，
  凭据仅发往 GitHub 官方 API，未传递给第三方。
- **不涉及真实凭据的替代实现（已确认可行，但本轮不实施）**：
  脚本实际只需要 8 个非敏感变量
  `TG_DATABASE_URL` / `TG_ARTIFACT_DIR` / `TG_TEAMFLOW_BASE_URL` / `TG_INLINE_WORKER` /
  `TG_WORKER_LEASE_TTL_S` / `TG_WORKER_POLL_INTERVAL_S` / `TG_LOG_JSON` / `TG_WORKER_ID`。
  改用**显式白名单**构造 `env`（仅上述键 + `PATH`/`PYTHONPATH`）即可去掉 `**os.environ`，
  不需要 `.env`、不需要真实 Key、只需按名判断变量是否存在。
  但按负责人本轮指令「不修改参数规避检测、不重复被拦截命令」，**不在本会话重试该写入**；
  方案留待后续经安全复核后由受信任流程实施。
- **第二次尝试同样被拦截（2026-11-24 13:31）**：按负责人授权改用「显式、最小化的非敏感环境配置」
  重写脚本（只含 PATH / HOME / PYTHONPATH / PYTHONDONTWRITEBYTECODE / PYTHONIOENCODING /
  PYTHONUTF8 与 8 个 TG_* 配置项，完全没有 `**os.environ`，不读取任何真实凭据），
  `Write` 仍被 `info_theft_steal_env_keys` 拦截。按指令**不再重试、不再改写参数**。
  结论：B-17 维持 **BLOCKED**，不因方案调整而解除；4 个补充场景不计为通过。

- **本轮已完成、不依赖该脚本的 WP3 证据**：
  `docs/assets/run008/wp3/worker-fault-recovery.json`（真实执行中途 SIGKILL，17/17 checks）与
  `docs/assets/run008/wp3/worker-concurrency.json`（4 OS Worker 并行，峰值 4，单 Run 单 owner）。
  补充 4 场景标记为 **BLOCKED / 跳过**，不冒充通过。

### 安全边界（本轮起持续生效）

- 不读取、不打印、不复制、不上传真实 API Key / Token / 密码；不做 `**os.environ` 整体外传。
- 不修改 AgentDR 策略、不绕过 BuddySentinel；被拦截即记录并跳过。
- 不再使用 `pgrep -fl` 等会回显含凭据命令行的检查方式，改用端口健康探测与文件状态判断。
- 不执行 `git reset` / `git clean` / 强制覆盖；保留 21 个领先提交与全部未提交修改。

## RUN-008 收尾状态（2026-11-24 14:10 CST）

| 编号 | 项 | 最终状态 |
|---|---|---|
| B-17 | WP3 补充故障矩阵 4 场景 | **BLOCKED（保持）**：AgentDR 两次拦截，不重试不绕过 |
| B-18 | 60 分钟长稳 | **已解除**：60.14 分钟 / 66 周期 / 23 checks 全真 |
| — | LLM smoke | **BLOCKED**：CI 缺 Secret，明确不得用于 Release 放行 |
| — | Benchmark LLM/人工基线臂 | **BLOCKED**：无 Secret / 无真实人类工程师，未伪造 |
| — | 真实身份认证 / RBAC | **NOT_IMPLEMENTED**：主体类型门禁 ≠ 认证 |
| — | dev-only moderate ×3 | **已登记**：high=0/critical=0，semver-major 未实施 |

---

## RUN-016（2026-11-26）阻塞与决策

| 项 | 状态 | 依赖 |
|---|---|---|
| B-17 Worker 补充故障注入 | BLOCKED | 安全负责人批准（未绕过、未换工具规避） |
| 宿主级网络隔离 | BLOCKED | 宿主安全策略授权 |
| 外部真实站点 | BLOCKED | 授权目标清单 |
| 人工诊断基线 | BLOCKED | 真实人工标注 |
| LLM 四臂计费实验 / LLM Smoke | BLOCKED | 明确授权使用凭据与预算（本轮未读取 .env 密钥） |
| GitHub Actions 三级门禁 | NOT_RUN | `gh`/token + 推送权限 |
| Release Gate | NOT_RUN | 发布授权 |
| 23×2 新 Benchmark 实验 | NOT_RUN | 独占窗口 + LLM 臂预算 + 人工基线 |
| INCONCLUSIVE 重试 | NEEDS_DECISION | 决策稿已提交 |
| LICENSE / PR #1-#2 | NEEDS_DECISION | 负责人决策 |

本轮新增已知限制：
1. 纯 UI/定位器类故障无法独立确认业务影响（新口径下为 UNKNOWN）。
2. `fenced_session` 同 Run 不可重入（误用会挂死，非产品路径）。
3. Artifact S3 后端无孤儿回收。
4. 长稳 SIGSTOP 场景因脚本未补位 victim 而 NOTE（脚本已修，等待下次长稳复验）。
