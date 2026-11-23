# 当前任务（RUN-002 结束点）

## 已完成

- 真实浏览器执行链路（Chromium + 证据 + Pipeline + Worker）
- 统一持久化层（事件/证据/产物/目录/迁移）
- 完整 REST API（81 端点）+ 9 页 Vue 工作台 + 单端口部署
- Explorer → Compiler → 审核 → 执行 → 诊断 → 图 → 探针 → 修复 → Benchmark 全链路
- 22 个契约 Schema 导出 + 防漂移测试
- 文档体系重写（架构/API/DB/Agent/Playwright/证据/自愈/Benchmark/安全/测试/部署/交付）

## 下一步建议

1. 接入 LLM 适配器，补 Benchmark 缺失基线；
2. 扩数据集（≥30 场景）+ Macro-F1 / Time to Diagnosis 埋点；
3. PG 实测、Worker 心跳续租、多实例压测；
4. P08 RBAC 落地；P03 契约调整（待批准）；
5. 前端 SSE 与图交互增强。

---

## RUN-004-FE 更新（2026-11-23，追加，不改写上方 RUN-002 历史记录）

- 已完成：前端产品化重构（统一设计系统 / AppShell / 14 视图）、Explorer 真实 LLM 信息展示、新增 `/models` 与只读 `GET /api/v1/system/llm/summary`。
- 验证：224 passed、ruff 全绿、typecheck/build 通过、真实 Chromium 53/53、六档断点无溢出。
- 证据：`docs/RUN-004-FE-IMPLEMENTATION-REPORT.md`、`docs/RUN-004-FE-UI-ACCEPTANCE.md`、`docs/assets/run004-fe/`。
- 上方“下一步建议”第 1 项（LLM 适配器 / Benchmark 基线）已由 RUN-003-G 完成；本轮不再重复。其余项状态未变。
