# TraceGuard

Agentic Web UI Testing & Quality Intelligence Platform（智能 Web UI 自动化测试与质量情报平台）

> 真实浏览器执行 · 业务事实校验 · 证据可追溯 · 诊断与修复闭环

## 现在能做什么

- 用 **真实 Chromium** 执行 Web UI 用例，逐步采集截图 / DOM / ARIA / 网络 / 控制台证据；
- 用 **独立状态查询 + 已审批 Oracle** 校验业务事实，识破"UI 显示成功但数据未落库"（False Pass）；
- 对失败 Run 做 **证据驱动诊断**：多假设、确定性确认规则、处置分流，证据不足就是 `INCONCLUSIVE`；
- 用 **只读探针** 补证据（五项校验 + 白名单 + 预算 + 审计）；
- **Oracle-Protected Self-Healing**：只改定位/等待，真实沙箱验证，人工审批，CAS 晋升，可回滚；
- 通过 **Vue 工作台** 完成全部操作：总览 / 需求 / 用例 / 执行 / Run 详情 / 诊断 / 修复 / Benchmark / 系统。

## 快速开始

```bash
bash scripts/bootstrap.sh          # venv + 依赖 + Chromium + 前端构建
bash scripts/run_teamflow.sh &     # 被测应用 :8001
bash scripts/run_api.sh &          # 平台 :8000（内嵌 Worker）
open http://127.0.0.1:8000/
```

端到端演示（真实执行 19 步，含自愈与 Benchmark）：

```bash
.venv/bin/python scripts/final_demo.py
```

详见 [`docs/quickstart.md`](docs/quickstart.md) 与 [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md)。

## 真实结果（2026-11-22）

| 场景 | Verdict | 说明 |
|---|---|---|
| 正常创建项目 | PASSED | 8 步，24 份证据 |
| False Pass（TF-DB-001） | FAILED | UI 成功但业务 count=0 |
| UI 提示缺失（TF-UI-001） | FAILED | 业务成功但界面无反馈 |
| 接口 500（TF-API-001） | FAILED | 错误提示正确，功能不可用 |

- 测试：**222 passed**；ruff 全绿；前端类型检查 + 构建通过。
- RUN-003：专业前端升级（含新增 Agent 探索与编译中心）、DeepSeek 双模型真实接入
  （Explorer/Diagnosis，规则兜底诚实标记）、Benchmark 数据集 v2（30 场景 30/30 验证）、
  多 Worker 并发竞态修复（详见 docs/RUN-003-IMPLEMENTATION-REPORT.md）。
- Benchmark B（n=3）：传统报告基线首选根因 1/3、假通过 1/1；规则诊断 3/3、假通过 0/1。
- Benchmark C：沙箱 1/1、断言保持 1/1；无保护对照断言保持 0/1。

完整数值与阻塞项见 [`docs/BENCHMARK.md`](docs/BENCHMARK.md)、
功能状态见 [`docs/FEATURE-STATUS.md`](docs/FEATURE-STATUS.md)。

## 架构原则（不可改变）

1. Agent 是能力层，不是事实层。
2. Playwright 负责正式测试的确定性执行。
3. Oracle 独立于 Agent；正式 Oracle 必须来源可追溯并经具名人类审批。
4. Task 与 Run 分离；重试产生新 Run，不覆盖历史。
5. Execution Status / Test Verdict / Diagnosis / Disposition 四者分离。
6. Event 与 Evidence 分离；原始 Evidence 不得被派生结果冒充。
7. 相关性 ≠ 因果；证据不足允许 INCONCLUSIVE，且不等同 PASSED。
8. Self-Healing 不得削弱必需 Assertion、不得修改正式 Oracle。

## 目录

```
apps/api          FastAPI 平台（routers + services + Runtime 组合根）
apps/web          Vue3 + TS 工作台（9 页，真实 API）
apps/teamflow     受控被测应用（页面 + 业务 API + 故障注入 + GT 隔离）
apps/workers      独立 Worker（租约 + fencing + 优雅退出）
packages/contracts    C01–C12 模型与枚举
packages/core         配置 / 日志脱敏 / 错误码 / ID / 安全策略
packages/persistence  统一 Schema / 迁移 / 事件 / 证据 / 产物 / 目录
packages/execution    浏览器会话 / 观察者 / 断言 / Verdict / 任务服务 / Pipeline
packages/agents       Explorer / Compiler / Gateway / 自愈规则
packages/evidence     Event Store / Evidence / Graph / Diagnosis / Probe
benchmark             UI-FaultBench 数据集（GT 私有）与实验执行器
tests                 unit / contracts / integration / e2e / teamflow
docs                  架构 / API / 数据库 / Agent / Playwright / 安全 / 交付报告
.workbuddy-run/       长期运行记录（恢复点、决策、测试结果）
资料/                 权威设计资料（只读）
```

## 文档

| 文档 | 说明 |
|---|---|
| [架构](docs/ARCHITECTURE.md) | 分层、链路、部署形态、与设计差异 |
| [API](docs/API.md) | 81 个端点与语义约定 |
| [数据库](docs/DATABASE.md) | 表结构、约束、迁移 |
| [Agent 与自愈](docs/AGENTS.md) | 探索/编译/诊断/修复机制 |
| [Playwright](docs/PLAYWRIGHT.md) | 浏览器执行、采集、已修缺陷 |
| [证据与诊断](docs/EVIDENCE-AND-DIAGNOSIS.md) | 事件/证据/图/诊断规则 |
| [Benchmark](docs/BENCHMARK.md) | 真实实验与阻塞项 |
| [安全](docs/SECURITY.md) | 威胁模型与测试 |
| [性能与可靠性](docs/PERFORMANCE.md) | 实测延迟、吞吐、可靠性验证 |
| [测试报告](docs/TEST-REPORT.md) | 206 测试与缺陷回归 |
| [部署](docs/DEPLOYMENT.md) | 安装、配置、排错 |
| [功能状态](docs/FEATURE-STATUS.md) | 已运行 / 未验证 / 阻塞 / 待决策 |
| [最终交付报告](docs/FINAL/TraceGuard-Final-Delivery-Report.md) | 一页结论与交付索引 |

## 明确未完成

- LLM：DeepSeek 双模型已接入（Explorer/Diagnosis）；Compiler/Repair 的 LLM 辅助未接入；
- PostgreSQL 运行时未验证（BLOCKED：本机无实例）；
- PostgreSQL 运行时未验证；Redis 未采用；MinIO/S3 未实现（本地内容寻址存储已实现完整性与篡改检测）；
- 外部站点泛化未授权（P-EXT）；
- 认证/RBAC 未实现（本地单租户）；
- P03 / P08 / 路线图冻结需负责人决策（见 `docs/decisions/`）。

## 开发规范

- 分支：`workbuddy/traceguard-autonomous`（本地开发，不推送远程）
- 提交：阶段性本地提交；禁止密钥入库；禁止强制推送/改写历史
- 测试：验收数字必须来自真实测试日志；禁止 Mock 冒充真实运行
