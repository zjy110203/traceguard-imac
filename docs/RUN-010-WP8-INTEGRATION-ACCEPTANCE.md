# RUN-010 WP8：完整集成验收

> 状态：**VERIFIED**（进程内完整闭环 3/3 通过 + 全量回归 601 passed + E2E 批次回归）。
> 产物：`tests/e2e/test_full_loop_run010.py`。
> 安全事件处置：首轮写入被 AgentDR `info_theft_steal_env_keys` 拦截两次
> （`subprocess`+`env=` 环境字典传递模式），**未绕过、未重试被拦内容**，
> 改用进程内实现（零子进程/零环境字典/虚拟测试口令）后获放行。详见
> `.workbuddy-run/blockers.md` B-20。

## 1. 完整业务闭环（§8.1，真实执行）

`test_full_business_loop_with_auth_and_egress`（真实数据库 + 真实 Chromium + 真实诊断）：

```
Intent（qa-engineer 创建）
→ TestCase（真实 TeamFlow 目标，含 6 步交互）
→ qa-engineer 评审尝试 → 服务端真实 403（请求体 reviewer 声明无效）
→ qa-lead 创建 Oracle（approved）
→ Oracle Binding（绑定到断言，绑定 ≠ 审批）
→ qa-lead 评审 APPROVED
→ Task → Run → 真实执行（浏览器强制经 Egress Proxy）
→ Evidence 真实落库
→ 清库 + TF-DB-001 故障注入 → 第二轮真实执行 → 真实 FAILED
→ Diagnosis（POST /runs/{id}/diagnose）→ CONFIRMED/PROBABLE/INCONCLUSIVE + 可追溯假设
```

全部通过真实 API 端点执行（TestClient 进程内 HTTP 栈，非 Mock）。

## 2. 认证场景（§8.2）

`test_unauthenticated_and_rbac_matrix`：
- 未登录访问 `/api/v1/runs` → 401（fail-closed）；
- viewer 写操作 → 403 `PERMISSION_DENIED`；
- qa-engineer 访问用户管理 → 403；
- qa-lead 尝试 `repair.promote`（仅 admin）→ 403；
- 全部为服务端强制（前端遮挡不计数）。

## 3. 网络安全（§8.3）

正式 Run 的报告携带受控出站代理审计（本轮新增：`report.browser.egress`，
含 `enforced=True`、代理 URL、run 关联审计）——
断言 `egress.enforced is True` + `url` 为本地代理端口。
代理死亡 fail-closed、未授权目标 0 请求的深度验证由 WP2 集成测试覆盖
（5 个真实浏览器用例）。

## 4. 故障恢复（§8.4）

B-17（Worker 四真实故障场景）保持 **BLOCKED**（未获安全批准，未绕过 AgentDR）；
纯契约测试 4/4 + 恢复状态机测试保留通过（WP3 报告）。
已批准路径（TF-DB-001 受控故障注入）在本 WP 真实执行。

## 5. 最终回归（§8.5）

| 测试集 | 结果 |
|---|---|
| 单元 + 契约 + 集成 + E2E | **657 收集 → 644 passed / 13 skipped / 0 failed**（skip 全部为本地无 PG）<br>*收尾更正（2026-11-25）：原文写 "601 passed"，为 WP8 时点的过期快照；同一套件在 `d4d59ba` 上实测 644 passed，CI（Python 3.11）亦为 644 passed / 13 skipped。* |
| E2E（分批，内存约束） | 共 **30** 个真实浏览器用例：登录 2、egress 代理 8、egress 集成 5、目标策略 5、执行链 4、真实浏览器 3、**WP8 完整闭环 3**，全部通过（原文写"27 + 3"为重复计数） |
| Ruff check + format | 全绿（196 files formatted） |
| TypeScript + vite build | 全绿（vue-tsc 3.3.14；CI `PR Gate / frontend` 复验） |
| axe 无障碍 | 13 路由 0 violations（WP6） |
| CI（PR Gate） | 最新 HEAD 全绿（Nightly/Release 复验见 FINAL-REPORT） |

## 6. 本轮顺带修复的真实产品缺口

1. **运行报告缺 egress 审计**：`playwright_observer` 的 `browser_info` 此前不含
   代理审计；现在 setup 携带快照、teardown 刷新终值 → 每份运行报告都可核验
   "正式 Run 经代理"（指令 §2.5 的可核验形态）。
2. WP8 过程中修正的 API 契约认知（`testcase_ref` 字段名、`/runs/{id}/diagnose`
   端点、Oracle Binding 必要性）已固化在测试中防回归。

## 7. 状态

- 业务闭环 + 认证 + 网络安全 + 回归：**VERIFIED**；
- B-17 / 宿主级网络隔离 / LLM 臂 / 人工基线 / LICENSE：保持 BLOCKED / NEEDS_DECISION
  （如实记录，未伪装通过）。
