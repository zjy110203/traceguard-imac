# TraceGuard 最终交付报告（RUN-002）

> 项目：TraceGuard — Agentic Web UI Testing & Quality Intelligence Platform
> 架构基线 v1.1（评审修订稿，未冻结）· 报告日期：2026-11-22
> 本报告只描述**当前工作区真实可运行的状态**；无法验证的能力单独列出，不与已完成混淆。

---

## 0. 一页结论

| 维度 | 状态 |
|---|---|
| 真实浏览器自动化 | ✅ 已运行（Chromium 159，4 场景，24–25 份证据/Run） |
| 业务事实校验 | ✅ 独立状态查询 + 已审批 Oracle；成功识破 False Pass |
| 证据链 | ✅ 事件双唯一约束、证据不可变、产物 SHA-256 校验、图关联 |
| 诊断 | ✅ 规则诊断 3/3 主因正确；传统报告基线仅 1/3 且假通过 1/1 |
| 自愈 | ✅ 真实沙箱 + 审批 + CAS 晋升 + 回归 + 回滚；无保护对照断言保持 0/1 |
| 平台 | ✅ 81 个 REST 端点；Vue 工作台 9 页；独立 Worker |
| 测试 | ✅ 203 passed；ruff 全绿；前端类型检查 + 构建通过 |
| Benchmark | ⚠️ A/B/C 真实运行；LLM 与手工基线 BLOCKED（无凭据/无人力） |
| PG / Redis / MinIO | ⚠️ PG 未运行时验证；Redis 未采用；S3 未实现 |
| 外部站点泛化 | ⛔ 未授权（P-EXT），保持 BLOCKED |
| P03 / P08 / 路线冻结 | ⏸ 待负责人决策，不擅自冻结 |

---

## 1. 本轮（RUN-002）真正解决了什么

上一轮交付的真实问题不是"缺模块"，而是**链路断裂**：模块单测齐全，但没有执行者、没有持久化、
没有可用前端、没有真实浏览器证据。本轮逐项修复：

| 问题 | 处理 | 证据 |
|---|---|---|
| `POST /tasks/{id}/runs` 只建记录，无执行者 | 实现 `RunPipeline` + 内嵌 Worker + 独立 Worker | 真实 Run：8 步 PASSED；独立 Worker `--once` 执行成功 |
| Playwright 未安装、从未真实启动 | 安装 1.65.2 + Chromium 159，实测 4 场景 | 截图人工核验（含注入故障文案） |
| 事件/证据/图/诊断全在内存，重启即丢 | 统一 SQLite/PG Schema + 迁移 + 持久化适配器 | 关闭 DB 重开后事件/证据仍可读（测试） |
| 前端 45 行只显示 health JSON | 重写为 9 页 Vue 工作台（真实 API、加载/错误/空态、轮询） | 浏览器截图 + 构建通过 |
| 无完整 API（仅 8 个 Task 端点） | 81 个端点覆盖资产/执行/证据/诊断/探针/修复/Benchmark/系统 | OpenAPI + 集成测试 |
| Benchmark 只有口径，无执行器 | 真实运行 A/B/C，落库分子分母 + Wilson CI | `docs/BENCHMARK.md` |
| 无自愈端到端验证 | 提案 → 沙箱真实执行 → 审批 → 晋升 → 回归 → 回滚 | 全链路实测（v3/v4） |

同时修复 9 个真实缺陷（断言崩溃、日志崩溃、Playwright 线程模型、Step 主键覆盖、网络事件时序、
症状当根因、目录入参、错误码映射、LogRecord 保留字冲突），全部补了回归测试。

---

## 2. 架构与实现

详见 `docs/ARCHITECTURE.md`。要点：

- 模块化单体 + 独立 Worker，不引入无必要微服务；
- 执行内核（Playwright）与 Agent（探索/编译/诊断建议/修复提案）严格分层；
- Oracle 独立于 Agent，正式 Oracle 必须具名人类审批；
- Event / Evidence / Verdict / Diagnosis / Disposition / Repair 状态各自独立。

```
Intent → Explorer(真实探索) → Trace → Compiler(候选) → 人工审核 → Approved Case
       → Playwright 执行 → Task/Run/Step → Event → Evidence → Verdict
       → Diagnosis → Evidence Graph → Active Diagnosis(Probe) → Disposition
       → RepairProposal → 沙箱执行 → 审批 → CAS 晋升 → 回归 → 回滚
       → Benchmark(真实对照实验)
```

整条链路在 `tests/integration/test_full_pipeline.py`、`tests/e2e/test_playwright_real.py`
与 API 实测中均有覆盖；上游（探索→编译）已在 `POST /intents/{id}/explore` 打通。

---

## 3. 量化结果（真实数值）

### 3.1 执行

| 场景 | Verdict | 步骤 | 证据 | 关键信息 |
|---|---|---|---|---|
| 正常创建项目 | PASSED | 8 | 24 | UI + 业务状态 + 行渲染全通过 |
| False Pass（TF-DB-001） | FAILED | 9 | 24 | UI 成功但 `count=0`，业务断言 FAILED |
| UI 提示缺失（TF-UI-001） | FAILED | 9 | 24 | UI FAILED / 业务 PASSED |
| 接口 500（TF-API-001） | FAILED | 6 | 15 | 错误提示 PASSED，业务断言 FAILED |

单 Run 耗时 4–6 s（含截图/DOM/ARIA/trace 采集）。

### 3.2 诊断对照（Benchmark B，n=3 故障样本）

| 指标 | 传统报告 | 规则诊断 | 证据图 | +主动探针 |
|---|---|---|---|---|
| 首选根因准确率 | 0.333 | **1.000** | 1.000 | 1.000 |
| 假通过率 | **1.000** | 0.000 | 0.000 | 0.000 |
| 覆盖率 | 1.000 | 1.000 | 1.000 | 1.000 |

诚实说明：D/E 未超过 C（n=3，且单点证据已足够确认）；这不是"图无效"的结论，而是当前数据集
无法体现图增益。置信区间很宽（3/3 → [0.438, 1.000]）。

### 3.3 自愈对照（Benchmark C）

| 指标 | Oracle-Protected | 无保护对照 |
|---|---|---|
| 沙箱验证成功率 | 1.000 (1/1) | — |
| 断言保持率 | **1.000 (1/1)** | **0.000 (0/1)** |
| 回归失败率 | 0.000 (0/1) | — |
| 审批通过率 | 1.000 (1/1) | — |

### 3.4 用例生成（Benchmark A）

| 指标 | 人工 | 规则编译器 |
|---|---|---|
| 执行成功率 | 1.000 (4/4) | 1.000 (2/2) |
| 可评估断言通过率 | 0.625 (5/8) | 0/0（未绑定 Oracle，契约要求 NOT_EVALUATED） |

---

## 4. 已验证能力清单

1. 真实 Chromium 执行、每 Run 独立 Context、异常释放、trace 落盘；
2. 逐步证据（截图/DOM/ARIA/网络/控制台）落库，内容寻址 + 篡改检测；
3. Trusted Oracle 独立状态查询（不采信页面文本）；
4. Task/Run/Step 状态机、重试新 Run、租约 + fencing token、过期回收；
5. 事件幂等/序号唯一/同键异载荷冲突审计；证据不可变与派生链；
6. 用例不可变版本 + 人工审核门禁 + Oracle 防 Agent 自批；
7. 规则诊断（6 类机制）+ 多假设 + Disposition 分流 + INCONCLUSIVE 不被当作 PASSED；
8. 证据图（节点/边/basis）+ 图快照持久化；
9. 只读探针五项校验、白名单、预算、审计；
10. 自愈 Diff 白名单、断言/Oracle 保护、真实沙箱、审批、CAS 晋升、回滚；
11. 81 个 REST 端点 + 9 页 Vue 工作台 + 单端口部署；
12. 独立 Worker 进程（`--once`/常驻）。

---

## 5. 未完成 / 受限（不隐瞒）

| 项 | 状态 | 原因与影响 |
|---|---|---|
| LLM 诊断/生成/修复基线 | BLOCKED | 无 API Key、无本地模型；Benchmark 不产生任何估计值 |
| 手工修复基线 | BLOCKED | 需真实人类工程师 |
| PostgreSQL 运行时 | 未验证 | 本机无 PG；Schema 与迁移已做方言兼容 |
| Redis | 未采用 | 队列由 DB 实现；非架构必需 |
| MinIO / S3 | 未实现 | 本地内容寻址存储已满足完整性；S3 适配器缺失 |
| 外部站点泛化 | BLOCKED | P-EXT 未授权，未访问任何外部应用 |
| 认证 / RBAC | 未实现 | 本地单租户；生产需前置鉴权 |
| Worker 心跳续租 | 未实现 | 长任务（>租约）可能被回收；当前租约 600s/120s |
| Flaky 统计判定 | 部分 | 仅"历史结论不一致"，未做显著性检验 |
| Macro-F1 / Time to Diagnosis | 未实现 | 样本量不足 / 未埋点 |
| P03 / P08 / 路线冻结 | 待决策 | 见 `docs/decisions/`，未擅自写入冻结语义 |

---

## 6. 如何复现

```bash
bash scripts/bootstrap.sh
bash scripts/run_teamflow.sh &
bash scripts/run_api.sh &
# 浏览器打开 http://127.0.0.1:8000/
# 总览 → 执行全部已审核用例
# Run 详情 → 诊断 → 生成证据图 → 探针建议
# 修复中心 → 新建提案 → 规则校验 → 沙箱执行 → 审批 → 晋升
# Benchmark → 运行实验 B
```

命令行核对：

```bash
.venv/bin/python -m pytest -q                     # 203 passed
.venv/bin/python scripts/smoke_playwright.py      # 4 场景真实执行
curl -s http://127.0.0.1:8000/api/v1/system/health
```

---

## 7. 交付物索引

| 文档 | 内容 |
|---|---|
| `docs/ARCHITECTURE.md` | 架构、分层、链路、差异 |
| `docs/API.md` | 81 个端点与语义约定 |
| `docs/DATABASE.md` | 表结构、约束、迁移 |
| `docs/AGENTS.md` | 探索/编译/诊断/自愈机制 |
| `docs/PLAYWRIGHT.md` | 浏览器执行、采集、已修缺陷 |
| `docs/EVIDENCE-AND-DIAGNOSIS.md` | 证据与诊断规则 |
| `docs/BENCHMARK.md` | 真实实验数值与阻塞项 |
| `docs/SECURITY.md` | 安全测试报告 |
| `docs/PERFORMANCE.md` | 性能与可靠性实测 |
| `docs/TEST-REPORT.md` | 203 测试与缺陷回归 |
| `docs/DEPLOYMENT.md` | 安装/启动/配置/排错 |
| `docs/FEATURE-STATUS.md` | 功能状态矩阵（已运行/未验证/阻塞/待决策） |
| `docs/decisions/P03-*.md` / `P08-*.md` | 待评审决策提案 |
| `.workbuddy-run/` | 长期运行记录与恢复点 |

> 注：`docs/FINAL/TraceGuard-Final-Delivery-Report.md.zip` 为上一轮的历史快照，
> 与本文冲突时以**本文与真实代码/运行记录**为准。

---

## 8. 后续路线（按价值排序）

1. 接入一个真实 LLM（含 prompt/model 版本管理），补 B/D 组基线与 LLM 生成用例；
2. 扩充 Benchmark 数据集（≥30 场景，含 flaky、时序、权限），计算 Macro-F1 与置信区间；
3. PostgreSQL 实测 + 事件分区/归档；Worker 心跳续租与多实例压测；
4. 认证与 RBAC（P08 决策后落地），审批审计表；
5. 外部站点泛化（获得 P-EXT 授权后）；
6. 前端：SSE 实时事件流、证据图交互增强、移动端布局。
