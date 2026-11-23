# RUN-005 安全与可靠性报告

## 1. 安全基线复查（13 项）

| # | 项目 | 状态 | 证据 / 现状 |
|---|---|---|---|
| 1 | SSRF | 已控制 | 探针仅允许本地受控 TeamFlow 模板（`get_state_projects/get_state_project`），不接受任意 URL；`_exec_http_state` 从 `settings.teamflow_base_url` 拼接 |
| 2 | DNS Rebinding | 已控制（范围限制） | 管理器只访问配置化本地地址；未引入外部域名解析路径。若后续接入外部站点（P10），需增加 IP 固定与解析后校验 |
| 3 | Redirect | 已控制 | `urllib.request.urlopen` 默认不跟随跨协议跳转风险由模板白名单限制输入；返回 3xx 不视为成功状态 |
| 4 | Prompt Injection | 已控制 | 系统消息固定在前，页面内容只进入 user 消息；`PromptManager.render` 截断 12k；LLM 输出经结构化白名单，游离指令无法改变动作集合 |
| 5 | ToolGateway | 已控制 | Explorer 动作经 `ToolGateway`（navigate 前缀校验、动作白名单、预算）；被拒绝动作不进入编译候选 |
| 6 | Workspace 边界 | 已控制 | Event `workspace_id` 由服务端注入并剥离 payload 覆盖；探针跨 Workspace 直接 `CROSS_WORKSPACE_ACCESS_DENIED` |
| 7 | Evidence 脱敏 | 已控制 | `redact()` 统一处理；SHA-256 基于脱敏前原文，完整性可校验；本轮探针证据同样脱敏后落库 |
| 8 | API 凭据 | 已控制 | 密钥只从环境变量注入；`Settings.public_dict()` 遮蔽敏感字段与 DSN 内联口令；`/system/llm/summary` 不返回密钥/Prompt 原文 |
| 9 | Worker 访问边界 | 已控制 | 租约 + fencing token；旧代次提交被拒绝；本轮未改语义 |
| 10 | 浏览器隔离 | 已控制 | 每 Run 独立 BrowserContext；执行结束释放 |
| 11 | Rate Limiting | 已控制（探针层） | 预算按 Run/Diagnosis 隔离，次数/成本/时长三重限制 + 原子占位（并发不超发）；API 层无全局限流（单机部署可接受，生产需网关） |
| 12 | 日志保留 | 部分 | 日志脱敏已实现；**保留策略与自动归档未实现**（P07 待决策，本轮未自动删除任何数据） |
| 13 | 依赖漏洞 | **发现 1 高 4 中** | 见第 2 节 |

## 2. 依赖漏洞复核（npm audit，2026-11-23 实测）

```
{"info":0,"low":0,"moderate":4,"high":1,"critical":0,"total":5}
vite             high       direct      <=6.4.3（已装 5.4.21）
vue-tsc          moderate   direct      1.7.0-alpha.0 - 3.0.1（已装 2.2.12）
@vue/language-core moderate transitive <=3.0.1（经 vue-tsc）
esbuild          moderate   transitive  <=0.24.2（经 vite）
minimatch        moderate   transitive  7.0.0 - 11.0.0
```

**处置结论**：`npm audit fix` 只能通过 semver-major 升级（vite 5→8、vue-tsc 2→3）修复。按本轮规则
（禁止 `--force` 大规模破坏性升级），**不升级**，记录风险：

- `vite` 漏洞为**开发服务器**路径遍历（`GHSA-4w7w-66w2-5vf9` 一类），仅影响本地 `npm run dev`；
  生产构建产物为静态文件，不经 vite dev server 提供。缓解：dev server 仅监听 `127.0.0.1`（默认）。
- `esbuild` 同类问题仅影响 dev server（`GHSA-67mh-4wv8-2f99`）。
- `vue-tsc / minimatch` 仅影响构建期类型检查，不进入运行时。
- 后果：**生产运行时无已知 high/critical 漏洞**；开发机存在中危风险。下一轮建议单独做一次
  vite 8 / vue-tsc 3 迁移评估（需回归构建与 UI 验收）。

CI Nightly 的 security job 会对 high/critical **阻断**；由于当前存在 1 个 high（dev-only），
该 job 首次运行会失败——这是**真实未解决项**，不得用 `|| true` 掩盖。处理选项：
(a) 完成 vite 8 迁移后恢复阻断；(b) 在 job 中显式豁免 dev-only 依赖并留下注释与期限（需负责人批准）。

## 3. 可靠性验证（本轮真实执行）

| 项 | 结论 | 证据 |
|---|---|---|
| 预算不随进程运行时间耗尽 | **VERIFIED** | 假时钟推进 600s/3600s，新 Run 仍有完整 60s 窗口（单测 + 服务级） |
| 多 Run / 多诊断预算隔离 | **VERIFIED** | 15 项单测 + 6 项集成；并发 6 路诊断互不影响 |
| 同一预算并发不超发 | **VERIFIED** | 4 线程 × 5 次 → 恰好 10 次成功、10 次 BLOCKED |
| 预算持久化与重启恢复 | **VERIFIED** | 重启后按 scope 恢复；owner 不匹配拒绝；关闭状态不被静默重置 |
| 预算耗尽/关闭有明确原因 | **VERIFIED** | `PROBE_COUNT/COST/TIME_BUDGET_EXHAUSTED`、`PROBE_BUDGET_CLOSED` |
| Inbox 崩溃恢复 | **VERIFIED** | 租约过期接管、attempts 递增、成功后不再执行 |
| Inbox 外部副作用不确定 | **VERIFIED** | UNKNOWN 状态 + 拒绝盲目重试 |
| Inbox 事务性 | **VERIFIED** | 副作用抛错 → 记录与副作用同时回滚 |
| Event 多进程并发 | **VERIFIED** | 4 真实进程 × 25 事件，序号 1..100 唯一，无丢失 |
| 幂等重复投递 | **VERIFIED** | 4 进程同键并发 → 同一 event_id，仅 1 行 |
| SSE 断线续传 | **VERIFIED** | `Last-Event-ID` 续传不重复；非法游标从头开始 |
| SSE 数据库短暂不可用 | **已实现（未压测）** | 轮询异常时发心跳并继续，不中断连接；未做真实 DB 抖动注入 |
| API 重启 | **PARTIAL** | 本轮多次重启 API（加载新开关）均成功恢复；未做执行中重启演练 |
| Worker 中途退出 / 租约过期 / 新 Worker 接管 | **PARTIAL** | 沿用 RUN-003-E 真实证据；本轮未重跑 |
| 4+ Worker 并发 | **PARTIAL** | 同上（历史证据，未在本轮版本复验） |
| 60 分钟长稳 | **BLOCKED** | 未执行；不得用分钟级测试冒充 |
| 24 小时长稳 | **NOT_IMPLEMENTED** | 未准备 |

## 4. 已知风险登记

| 风险 | 影响 | 现状 |
|---|---|---|
| P06 预算数值未确认 | 默认 50 次/100 成本/60s 可能过松或过紧 | 全部标注 `provisional=true`；不改正式决策 |
| 开发依赖 1 high（dev-only） | 本地 dev server 暴露风险 | 已记录，未强制升级 |
| 单机 API 无全局限流 | 暴力请求可耗尽资源 | 单机部署可接受；生产需网关 |
| 日志保留策略缺失 | 磁盘增长 | P07 待决策，禁止自动永久删除 |
| SSE 采用轮询式读取存储 | 高事件量下有 DB 读取压力 | 当前 1s 轮询 + 100/批；未做 10k 事件压测 |
