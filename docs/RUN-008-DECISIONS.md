# RUN-008 已实施的自主决策记录

依据项目负责人「完整自主开发授权」，本轮对原 NEEDS_DECISION / 安全边界事项做出并实施了以下决策。
每项记录原问题、方案、依据、替代方案、接口/数据影响与测试证据。

## D-R08-01：WP3 补充故障矩阵保持 BLOCKED（安全边界优先）

- **原问题**：需补 4 个故障场景（领取后退出、租约过期、API 重启、Worker 全量重启）。
- **首次尝试**：脚本以 `{**os.environ, ...}` 构造子进程环境，被 BuddySentinel
  `info_theft_steal_env_keys` 拦截。
- **第二次尝试**：按新授权改用显式、最小化的非敏感白名单（PATH/HOME/PYTHON* + 8 个 TG_* 键，
  无 `**os.environ`、无真实凭据）重写，**仍被同一策略拦截**。
- **选择方案**：不重试、不改参数规避、不改安全策略；记录 BLOCKED 并跳过。
- **依据**：负责人本轮明确「如果安全机制仍然阻止操作，保留 BLOCKED，继续其他任务」；
  「不得为了继续开发而降低安全标准」。
- **替代方案**：留待后续经安全复核的受信任流程实施；核心能力已由
  `worker-fault-recovery.json`（真实 SIGKILL，17/17）与 `worker-concurrency.json`（峰值 4）
  证明。
- **影响**：无代码/接口/数据变更；4 个补充场景不计为通过。
- **证据**：`.workbuddy-run/blockers.md` B-17；`docs/RUN-008-WP3-WORKER.md`。

## D-R08-02：Repair 内存服务统一人类主体门禁

- **原问题**：`SelfHealingService.approve()` 仅挡 `agent*`；`system:*`/`llm:*`/`bot:*`/`worker:*`
  可以自批，而 API 层 `RepairService` 已用 `require_human_actor`。
- **选择方案**：内存服务复用 `traceguard_persistence.catalog.require_human_actor`；
  `reject()` 同步加校验。
- **依据**：P08 授权可自主修复项目内部可独立修复部分；两层服务权限必须一致。
- **替代方案**：仅在 API 路由加校验（无法覆盖 Agent 直接调用领域服务的路径，故不采用）。
- **影响**：`SelfHealingService.approve(reviewer)` 现在接受任何非 `NON_HUMAN_TOKENS` 主体；
  `reject(proposal_id, reviewer="human:qa")` 新增可选参数，向后兼容。无数据迁移。
- **证据**：`tests/unit/test_healing.py` 17/17；`docs/RUN-008-SECURITY-AUDIT.md` §1.1。

## D-R08-03：日志字符串脱敏词表与字典键统一

- **原问题**：`redact()` 字符串路径漏掉 cookie/access_key/private_key/credential。
- **选择方案**：从同一份 `_SENSITIVE_KEY.pattern` 构造 `_SENSITIVE_INLINE`。
- **依据**：单一事实来源，避免以后再次漂移。
- **替代方案**：手工维护两份正则（正是本缺陷成因，不采用）。
- **影响**：日志中更多敏感串被替换为 `***REDACTED***`；不改变任何 API 契约或数据结构。
- **证据**：`tests/unit/test_core.py` 新增断言；36/36 通过。

## D-R08-04：前端全局搜索落点修正为列表页过滤

- **原问题**：搜索把 `intent_id` 链到不存在的 `/intents/:id`，点击跳回首页。
- **选择方案**：`/intents?id=<id>` + `IntentsView` 读取 query 真实过滤（含 watch）。
- **依据**：不新增详情视图（14 视图设计不变），以最小改动消除死链。
- **替代方案**：新增 `/intents/:id` 详情视图（超出本轮范围，风险更高）。
- **影响**：`App.vue` 搜索结果 URL 结构变化；`IntentsView` 新增 query 读取。无后端变更。
- **证据**：`docs/assets/run008/wp5/fix-verification.json` 真实浏览器 4/4。

## D-R08-05：深色验收脚本去掉假证据

- **原问题**：`ui_acceptance.py` 用 `emulate_media(dark)` 验深色，主题实际由 `tg-theme` 驱动，
  历史「深色通过」是恒真断言。
- **选择方案**：写 `tg-theme=dark` 初始化脚本 + `data-theme` 与 body 亮度双重断言。
- **依据**：不得伪造验证结果。
- **替代方案**：保留旧断言（等于继续产出假证据，不采用）。
- **影响**：旧脚本返回值更严格；历史该条结论作废。
- **证据**：`docs/assets/run008/wp5/ui-legacy2/ui-acceptance.json` 30/0，
  深色 `rgb(13,18,28)`、lum=19.7。
