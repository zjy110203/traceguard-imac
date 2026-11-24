# 安全测试报告（RUN-002 实测）

## 1. 威胁模型（本地受控环境）

| 威胁 | 攻击面 | 本版本对策 | 验证 |
|---|---|---|---|
| SSRF / 内网探测 | Agent 工具、探针、被测目标 URL | 域名+端口白名单；私有网段解析拦截 | `test_security.py` 12 项 |
| DNS Rebinding | 目标域名解析 | 解析结果必须仍是白名单地址 | 单测（monkeypatch socket） |
| 开放重定向 | 目标页面跳转 | 每一跳重新校验主机/端口；跳转次数上限 | 单测 |
| Prompt Injection | 页面文本诱导 Agent | 页面内容是不可信数据；工具调用必须过网关；`source=page_content` 一律拒绝 | 网关实现 + 探索实测 |
| 越权数据访问 | 探针/证据 API | Workspace 边界校验、模板白名单、任意 SQL 关键字拦截 | API 集成测试 |
| 凭据泄露 | 日志/证据/报告 | 递归脱敏（password/token/secret/…）+ 证据入库前脱敏 | 单测 + API 测试断言 `***REDACTED***` |
| 证据篡改 | 产物文件 | 内容寻址 + 读取时重算 SHA-256 | 单测 + API 404 |
| Agent 自批 | Oracle / Repair | `approved_by_agent: Literal[False]`；审批人含 agent/llm 一律拒绝 | 单测 + API 412/403 |
| 自愈削弱断言 | Repair | 断言保持 + Oracle 完整性 + Diff 白名单 | 单测 + Benchmark 对照 |
| 并发事件写入 | Event Store | 序号唯一约束 + 幂等键唯一约束 + 冲突审计 | 并发单测（12 线程） |
| 旧 Worker 提交 | 租约过期 | fencing token 校验 | 单测 + Worker 实测 |
| 外部未授权访问 | 阶段12 泛化 | 仅本地 TeamFlow；P-EXT 未授权即 BLOCKED | 配置与文档 |

## 2. 关键实现位置

| 能力 | 文件 |
|---|---|
| URL/域名/端口校验、重定向复核 | `packages/core/traceguard_core/security/policy.py` |
| 工具网关六步检查 | `packages/agents/traceguard_agents/gateway/tool_gateway.py` |
| 探针五项校验 | `packages/evidence/traceguard_evidence/probe/planner.py` |
| 日志与证据脱敏 | `packages/core/traceguard_core/logging.py` |
| 证据完整性 | `packages/evidence/traceguard_evidence/evidence_manager.py`、`persistence/stores.py` |
| 产物篡改检测 | `persistence/stores.py::ArtifactStore.read` |
| Oracle 防自批 | `contracts/models.py`、`persistence/catalog.py` |
| 修复保护规则 | `packages/agents/traceguard_agents/healing/rules.py` |

## 3. 实测结果

```
$ .venv/bin/python -m pytest tests/unit/test_security.py -q
12 passed
```

覆盖：私有 IP 拦截、非白名单主机、TeamFlow 本地放行、错误端口拦截、DNS Rebinding、
逐跳重定向复核、跳转次数上限、凭据识别与脱敏、外部访问需授权、并发序号唯一、并发幂等。

API 层新增验证（`tests/integration/test_api_flow.py`）：

- Agent 审核用例 → 412；Agent 批准 Oracle → 412；Agent 审批修复 → 403/409；
- 越权探针模板（`raw_sql` + `select`）→ `status=BLOCKED`、`permission_decision=DENY`；
- 证据附件被篡改 → 下载 404 且错误信息包含 integrity；
- 证据内容中的 password → `***REDACTED***`。

## 4. 未覆盖 / 风险

1. **无用户认证**：本地单租户。生产必须前置 OIDC/网关鉴权并做租户隔离。
2. **Worker 与 API 未做 mTLS**：单机部署；分布式部署需补服务间认证。
3. **浏览器沙箱**：Chromium 默认沙箱；未做容器级隔离（无 Docker 环境）。
4. **速率限制**：仅探针预算；API 层未做全局限流。
5. **供应链**：未做依赖漏洞扫描（无网络策略约束下的 CI 插件）。
6. **日志留存**：未实现轮转与集中采集。

## RUN-008 补充（2026-11-24）

- 本轮真实修复两处权限/脱敏缺陷：`SelfHealingService.approve/reject` 统一
  `require_human_actor`（堵住 `system:*`/`llm:*`/`bot:*`/`worker:*` 自批），
  `redact()` 字符串路径补齐 cookie/access_key/private_key/credential。
  证据：`docs/RUN-008-SECURITY-AUDIT.md`、`test_healing.py` 17/17、`test_core.py` 36/36。
- CI 真实 audit（commit `40efee6`）：high=0 / critical=0 / moderate=3（dev-only）。
- 安全边界不变：无真实用户认证（主体类型门禁不是 RBAC）、顶层 302 出站请求会到达目标、
  DNS 钉住无法覆盖浏览器内核缓存窗口；生产需前置 OIDC/网关与受控出站代理。
- AgentDR `info_theft_steal_env_keys` 两次拦截按策略保留 BLOCKED，未绕过、未读取/传递真实凭据。
