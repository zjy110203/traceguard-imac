# RUN-007 待决策请求（DECISION REQUESTS）

- 时间：2026-11-23（GMT+8）
- 分支：`workbuddy/run007-resume-20261123`
- 规则：以下事项**未被冻结、未被静默实现**。在负责人明确批准前，Agent 不擅自扩大范围，
  也不把"当前实现"写成"已满足安全要求"。

---

## D-01 身份认证与多租户隔离（最高优先级）

**现状**：平台是**单机可信部署**。审批入口有主体校验（`require_human_actor()`），
只拒绝 `agent:` / `llm:` / `system:` / `bot:` / `service:` 命名空间，**不做身份认证**。
任何调用方只要在请求体中写 `"actor": "role:qa-lead"` 即可完成审批。

**风险**：若部署到多人/多团队环境，等同于"审批门禁可被任意人伪造"。

**可选方案**：

| 方案 | 内容 | 成本 | 影响面 |
|---|---|---|---|
| A | 静态 API Key（每角色一个），`Authorization: Bearer`，主体由 Key 映射，忽略请求体 `actor` | 低 | 中间件 + 全部写接口；需密钥分发 |
| B | OIDC / OAuth2（如公司 SSO），JWT 校验 + 角色 claim 映射 | 中 | 需 IdP、回调、会话；前端登录态 |
| C | 保持单机可信部署，但在文档与 UI 明确标注"无认证，仅限本机" | 极低 | 仅文档；风险自担 |

**建议**：先 A（能立刻消除"任意伪造主体"），后续再评估 B。
**不做的理由**：认证方案涉及部署形态与账号体系，属产品决策，Agent 不擅自选定。

---

## P03 外部站点泛化（真实第三方目标）

**现状**：`TargetPolicy` 只允许显式授权的 origin；本轮与历史轮次**未访问任何未授权外部站点**，
外部泛化实验（跨站点、真实登录、真实 CDN）全部未执行。

**需要的决策**：
1. 允许访问的域名/端口清单（谁维护、如何审计）？
2. 是否允许访问含真实用户数据的站点（合规与数据留存策略）？
3. 外部站点的稳定性如何计入 Benchmark（外部波动会污染指标）？

**当前状态**：BLOCKED（缺授权清单）。Benchmark 只报本地固定场景，不冒充外部泛化结论。

---

## P08 凭据与密钥管理

**现状**：LLM Key 通过 `.env`（本地）/ GitHub Secret（CI）注入；无 Vault、无轮换策略、
无最小权限划分；`.env` 不进仓库（Nightly 有 secret pattern scan 兜底）。

**需要的决策**：
1. 是否引入密钥管理服务（Vault / KMS / 云 Secret Manager）？
2. 轮换周期与责任人？
3. CI 的 `DEEPSEEK_API_KEY` 由谁授权、额度上限多少（影响 Benchmark 成本）？

**当前状态**：NEEDS_DECISION。缺 Secret 时 Nightly 的记录是 **BLOCKED**，不计通过。

---

## P06 Release 制品与供应链

**现状**：Release Gate 只做"代码 + 测试 + 文档 + 证据 + Nightly 归属"校验，
**不产出制品**（无 wheel/镜像/SBOM），**不创建 Tag**（遵循本轮指令）。

**需要的决策**：
1. 发布形态：Python wheel？容器镜像？前端静态包？
2. 是否要求 SBOM（CycloneDX）与镜像签名（cosign）？
3. Release Tag 命名与打标人（当前 workflow 仅响应已有 `v*` tag，不主动创建）。

**当前状态**：NEEDS_DECISION。Release Gate 已按"缺证据 fail-closed"改造，但制品环节未定义。

---

## D-02 CI 触发凭据（本轮直接阻塞项）

**现状**：本机无 GitHub 凭据（无 token、无 SSH key、无 `gh` CLI），
`git push` 返回 `could not read Username`，因此：
- 分支 `workbuddy/run007-resume-20261123` 的**本地提交无法推送**；
- 无法创建指向 `main` 的 PR；
- 无法 dispatch PR Gate / Nightly / Release，CI 改动**无运行证据**。

**需要的决策 / 支持**：
1. 提供一个具备 `repo` + `workflow` 权限的临时 token（或配置 SSH key），由 Agent 完成 push + PR；
2. 或由负责人在本地执行 `git push -u origin workbuddy/run007-resume-20261123` 并创建 PR；
3. 明确是否允许在本环境配置长期凭据（涉及安全，需负责人确认）。

**当前状态**：BLOCKED。本地提交与全部证据保留在工作区，未丢弃任何内容。

---

## D-03 沙箱与正式执行是否允许"共用 Worker 池"

**现状**：沙箱 Run 与正式 Run 使用同一队列/同一 Worker 池（靠 Run 类型区分），
沙箱无独立资源配额；极端情况下沙箱长任务会挤占正式执行。

**可选方案**：A 保持共用（简单、可证明队列被消费）；B 独立 Worker 池 + 独立队列（隔离好、复杂度高）。
**建议**：A（当前阶段），并在文档标注"沙箱无独立配额"。
**当前状态**：NEEDS_DECISION（不阻塞本轮，但需登记）。

---

## D-04 Benchmark 的 LLM 成本与模型口径

**现状**：Benchmark v3 的 LLM 对照臂在既有预算内运行；本轮 DeepSeek 真实调用次数受限，
未做大规模多模型对比（Flash / Pro / Routed 全量跑需要更多额度与时间）。

**需要的决策**：
1. 是否允许扩大 LLM 预算做四模型全量对照？
2. 人工基线（人工诊断）由谁执行、如何计时（当前 BLOCKED，无人力投入）？

**当前状态**：NEEDS_DECISION。报告中 LLM 对照与人工基线**如实标 BLOCKED**，不用 Mock 补齐。
