# RUN-008 最终交付报告

> **RUN-009 WP0 权威更正（2026-11-24 16:05 CST）**：本文件正文是 RUN-008 终稿历史快照，以下 git 事实以实测为准：
> 实际最终 HEAD 为 `cb8059196db2aeffe8bdaba04f03011d4f5fa36b`（仅改恢复文件），以其前一提交 `804fc5d`（仅改本报告）提交的终稿；
> `40efee6` 是最终**代码**提交和三段 CI 的真实基线，不能把它的 CI 结果表述为 `cb80591` 上的结果。
> 正文中「工作区仅剩本报告待提交」及止于 `0a6e3c3` 的提交链均已过时；权威基线见 `docs/RUN-009-BASELINE.md`。历史正文保留不覆盖。

> 状态：**终稿**（2026-11-24 14:10 CST）。所有「VERIFIED」均有本轮真实证据；
> BLOCKED / PARTIAL 如实登记，不伪造通过。

## 1. 基线、分支与 Git 状态

- 指令基线：`ac2876e`（`origin/main`）；本轮起点：`ce1acea`（相对基线领先 19 提交）。
- 开发分支：`workbuddy/run008-quality-hardening`，已推送远程。
- 起点工作区干净；全程未执行 `reset` / `clean` / 强推 / 历史改写。
- 本轮提交：`00d2ee0`(WP0/WP1) → `1f8df24`(WP2) → `40efee6`(WP5/WP6) →
  `790bad3`(台账/决策) → `2e32594`/`f68298a`(CI) → `49d700e`(台账/测试报告) →
  `a093b49`(恢复状态) → `144af57`(交叉文档) → `29326d2`(WP7) → `0a6e3c3`(WP4)。
- 三段 CI 基于 `40efee6`（最终**代码**提交）真实全绿；其后均为文档/证据提交，不改变代码。
- 所有运行证据（长稳、Benchmark、前端、Worker）均已提交；工作区仅剩本报告待提交。

## 2. 验收矩阵（截至草稿）

| 工作包 | 状态 | 关键结果 |
|---|---|---|
| WP0 基线审计 | **VERIFIED** | `docs/RUN-008-BASELINE.md`；确认两个 P0 缺陷在起点已修，以真实执行复核 |
| WP1 P0 修复与回归 | **VERIFIED** | 多进程 16/16；真实 SIGKILL 3 场景；长稳脚本 22 checks 冒烟 |
| WP2 回归与 CI | **VERIFIED** | 本机 567 passed/13 skipped；E2E 12/12；Ruff 全绿；前端 build OK；PR/Nightly 真实全绿 |
| WP3 Worker 并发/故障恢复 | **PARTIAL** | 并发峰值 4（14/14）、真实 SIGKILL 恢复（17/17）VERIFIED；补充 4 场景 BLOCKED |
| WP4 60 分钟长稳 | **VERIFIED** | 真实 60.14 分钟 / 66 周期 / 23 checks 全真；`soak-60min.json` + 66 行采样 |
| WP5 前端 14 视图 | **VERIFIED** | 60/60 检查（48 亮 + 12 暗）、64 截图；修复 5 个真实缺陷 + 4/4 定点回归 |
| WP6 安全与权限 | **PARTIAL** | 修复 2 个真实缺陷（主体门禁、脱敏）；其余为已披露设计边界 |
| WP7 Benchmark v3 | **VERIFIED（含 BLOCKED 臂）** | 本轮真实 2 轮 × 30 场景（608.5s）；A 假通过 4/4 → C/D/E 0/4；LLM 与人工基线 BLOCKED |
| WP8 交付 | **完成** | 本报告、台账、决策记录、CI/安全/前端/长稳/Benchmark 报告齐备 |

## 3. 已完成工作包摘要

### WP1
- `probe_budget_multiprocess.py` 顶层 `sys.path` 注入支持 spawn；3/3 多进程测试通过。
- `soak_run007.py`：修掉 `_get()` 错误未计入、`api_inline_worker_off` 恒真、DB 快照未校验、
  samples 不可续跑；新增 `tests/integration/test_soak_run007_guards.py`。16/16 回归通过。
- 直接运行脚本 3 场景（正常/kill/close）均产出真实 PID 与 SIGKILL 证据。

### WP2
- CI 配置：`pr-gate.yml` 增加 `workbuddy/run008-*` 分支与新增回归测试。
- 本机：567 passed / 13 skipped / 12 warnings；E2E 12 passed；Ruff check/format 全绿；typecheck/build OK。
- 真实 Actions（commit `1f8df24`）：PR `45331287430` success；Nightly `45331346274` success（PG16 6 用例真跑）。
- 真实 Actions（最终代码 `40efee6`）：PR `45335765297` success；Nightly `45335777381` success。
- LLM smoke 在两次 Nightly 中均为 **BLOCKED（缺 Secret）**，明确「不得用于 Release 放行」。

### WP3
- `worker-concurrency.json`：4 OS Worker、峰值并行 4、单 Run 单 owner、14/14 checks。
- `worker-fault-recovery.json`：执行 2.77s 后真实 SIGKILL PID 80665，同 Run 接管、fencing 递增、
  `stale_write_rejected`、17/17 checks。（证据 JSON 的 `round` 字段是脚本硬编码旧标签，文件为本轮真实生成。）
- 补充 4 场景：AgentDR 两次拦截（含显式非敏感白名单重写），按安全边界维持 **BLOCKED**。

### WP5
- 14 视图矩阵 + 真实浏览器：60 checks 全过（content/overflow/console/pageerror/dark 均 0 fail），64 截图。
- 修复：全局搜索意图死链、诊断详情静默失败、修复中心未捕获拒绝、系统探测误导提示、
  Run 详情深色对比度、SSE 轮询计时器重置、深色验收假证据。
- 旧验收脚本修复后 30/0（深色实测 `rgb(13,18,28)`）；定点回归 4/4。

### WP6
- 修复 `SelfHealingService.approve/reject`：统一 `require_human_actor`，堵住 `system:*`/`llm:*`/`bot:*`/`worker:*` 自批。
- 修复 `redact()` 字符串路径敏感词表与字典键统一（cookie/access_key/private_key/credential）。
- 设计边界如实披露：无真实认证、顶层 302 出站请求已发出、DNS 钉住窗口、moderate=3、CORS 默认值。

## 4. 已实施决策

见 `docs/RUN-008-DECISIONS.md`：D-R08-01（WP3 安全阻塞保持 BLOCKED）、
D-R08-02（Repair 主体门禁统一）、D-R08-03（脱敏词表统一）、
D-R08-04（搜索落点改列表过滤）、D-R08-05（深色验收去假证据）。
每项含原问题、方案、依据、替代方案、接口/数据影响、测试证据。

## 5. 未解决 / 阻塞

| 项 | 状态 | 说明 |
|---|---|---|
| WP3 补充故障矩阵 4 场景 | **BLOCKED** | AgentDR 两次拦截；不重试、不绕过 |
| LLM smoke | **BLOCKED** | CI 缺 Secret，明确不得用于 Release 放行 |
| 身份认证 / RBAC | **NOT_IMPLEMENTED** | 主体类型门禁 ≠ 真实认证 |
| dev-only moderate 依赖漏洞 ×3 | **已登记** | high=0/critical=0；semver-major 升级未实施 |
| 顶层 302 出站请求 | **设计边界** | 落地拒绝，但请求已到达目标 |
| DNS rebinding 短时窗口 | **设计边界** | 需出站代理；生产部署事项 |
| WP4 / WP7 | **完成** | 长稳 23/23；Benchmark 2 轮真实执行（见各自报告） |

## 6. 下一阶段建议

1. 若安全团队复核通过，再以独立受信任流程实现 WP3 补充故障矩阵（白名单环境配置已设计）。
2. 生产化：接真实认证（OIDC/SSO）、受控出站代理、`vite` semver-major 升级决策。
3. Benchmark：补充多源冲突数据集以验证 D/E 臂价值；LLM 臂需配置 Secret 后真实运行。
4. 资源指标需在有 `/proc` 或允许 `ps` 的环境补测 CPU/内存曲线。

## 7. 证据索引

| 类别 | 路径 |
|---|---|
| 基线 | `docs/RUN-008-BASELINE.md` |
| WP1 | `docs/RUN-008-WP1-IMPLEMENTATION.md`、`docs/assets/run008/wp1/` |
| WP2 | `docs/RUN-008-WP2-REGRESSION-CI.md`、`logs/run008/` |
| WP3 | `docs/RUN-008-WP3-WORKER.md`、`docs/assets/run008/wp3/` |
| WP5 | `docs/RUN-008-WP5-FRONTEND-AUDIT.md`、`docs/assets/run008/wp5/` |
| WP6 | `docs/RUN-008-SECURITY-AUDIT.md` |
| 决策 | `docs/RUN-008-DECISIONS.md` |
| 恢复 | `.workbuddy-run/resume.md`、`blockers.md` |
| WP4 | `docs/RUN-008-WP4-SOAK.md`、`docs/assets/run008/soak-60min.json`、`soak-samples.jsonl`(66 行) |
| WP7 | `docs/RUN-008-BENCHMARK-REPORT.md`、`docs/assets/run008/wp7/benchmark-v3.json`、`samples*.jsonl` |
