# 中断恢复指引

> 下一次运行的第一步：读本文件 → `docs/FEATURE-STATUS.md` → `.workbuddy-run/progress.md`
> → `docs/TraceGuard-持续开发任务台账.md`。
> 不要从零重新开发。不要谎称项目已完成。

- 最后保存：2026-11-22 23:59（RUN-003）
- 分支：`workbuddy/traceguard-autonomous`

## 当前快照

- 项目根：`/Users/dlp--test/Desktop/traceguard`
- **产品链路已打通**：Intent → Explorer → Compiler → 审核 → Playwright 执行 → 证据 → Verdict →
  Diagnosis → Graph → Probe → Disposition → Repair（沙箱/审批/晋升/回归/回滚）→ Benchmark。
- 真实浏览器：Chromium 159（内置）+ Edge 158 均验证；4 场景真实执行有截图证据。
- 前端：10 页 Vue 工作台，真实 API；`npm run build` 通过；API 单端口托管。
- 测试：**222 passed**，ruff 全绿；RUN-003 已交付（前端升级/DeepSeek LLM/30场景数据集/并发修复）。
- Benchmark 真实结果：B 组传统基线 1/3 主因 + 1/1 假通过；规则诊断 3/3 + 0/1；C 组沙箱 1/1。

## 环境

| 项 | 值 |
|---|---|
| Python | 3.13.12（.venv） |
| Node | 22.22.2 |
| playwright | 1.65.2 + Chromium 159 |
| 数据库 | SQLite `.traceguard/traceguard.db`（迁移 v4） |
| 已装额外依赖 | httpx（TestClient）、playwright |
| Docker / PG / Redis | 不可用（未使用 / 未验证） |

## 恢复后立即可做的验证

```bash
bash scripts/run_teamflow.sh &     # :8001
bash scripts/run_api.sh &          # :8000
curl -s "http://127.0.0.1:8000/api/v1/system/browser?probe=true" | jq '.available'
curl -s -X POST http://127.0.0.1:8000/api/v1/testcases/tcase_tf_create_normal/execute \
  -H 'Content-Type: application/json' -d '{"sync":true}' | jq '.execution.test_verdict'
.venv/bin/python -m pytest -q
cd apps/web && npm run build
```

## 下一步（按价值）

1. Compiler/Self-Healing/Active Probe 的 LLM 辅助接入（ModelGateway 接口已备）。
2. 全 30 场景诊断对照实验（提高 MAX_CASES_PER_EXPERIMENT，预算约 $0.3/轮）。
3. PostgreSQL 实测（需本机起 PG 实例）。
4. P08 决策后落地 RBAC 与审计表；P03 决策后调整 Oracle 契约。
5. 外部站点泛化（需 P-EXT 授权）。
6. 前端：深色主题验收后启用、SSE 实时事件流。

## RUN-003 关键记忆

- .env 存 DEEPSEEK_API_KEY（gitignored）+ TG_LLM_PROVIDER=deepseek；
- 沙箱按方法名拦截（budget_exhausted 被强制 False → 改名 should_skip_llm）；
- pytest 需 --basetemp（已固化 pyproject）；
- claim 竞态修复：原子条件 UPDATE 租约 CAS；
- UI 断言 DOM 观测不被业务 count 遮蔽（pipeline _observation_for）；
- Pro 推理模型 reasoning tokens 计入 max_tokens（预算小则 content 空）。

## 不要做的事

- 不要删除 `.workbuddy-run/` 与 `.traceguard/`（项目数据）。
- 不要 `git reset --hard` / `git clean` / 强推 / Docker 全局清理。
- 不要访问外部 Web 应用（P-EXT 未授权）。
- 不要声称路线图 v1.1 已冻结，或 P03/P08 已决策。
- 不要把 Blocked 的 LLM 基线写成数值，不要把 NOT_EVALUATED 写成 PASSED。
