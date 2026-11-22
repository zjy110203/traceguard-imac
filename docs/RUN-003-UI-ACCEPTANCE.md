# RUN-003 UI 验收报告（2026-11-22）

> 验收方式：真实 Chromium 交互验证（Playwright，非截图证明）+ 截图目检。
> 截图目录：`docs/assets/run003/`；机读结果：`docs/assets/run003/verify-results.json`。

## 1. 六大核心页面

| 页面 | 路由 | 升级内容 | 交互验证 |
|---|---|---|---|
| 质量指挥中心 | `/` | 四态结论分布（含 INCONCLUSIVE/NOT_EVALUATED）、SVG 趋势图（真实数据）、失败类型聚合（诊断 failure_domain）、待办区（待诊断/待审批修复）、队列与 Worker 状态 | 指标/图例/待办 5 项 PASS |
| 浏览器执行中心 | `/runs` + `/runs/:id` | 主从布局：左步骤时间线（可点击）+ 右详情面板（截图放大/网络请求/Console/观察数据/关联证据）；断言跳证据；GraphView 节点点击详情 | 主从布局/详情面板/截图/网络/断言 5 项 PASS |
| Agent 探索与编译中心 | `/explorer`（新增） | 探索列表+详情抽屉：动作序列（gateway allowed/denied）、ToolGateway 审计、编译产物与诚实警告；页面明示当前为规则实现 | 诚实标注 + 渲染 2 项 PASS |
| 智能诊断与证据分析 | `/diagnoses` + 详情 | 判定引擎标注（rule-v1 / rule-v1+llm-advisor）、LLM 候选假设（suggested_by 标注）、GraphView 节点点击 | 渲染 PASS |
| Self-Healing 修复中心 | `/repairs` | 生命周期状态、审批具名、后端错误码透出 | 渲染 PASS |
| UI-FaultBench | `/benchmark` | 组别指标（分子/分母/CI）、BLOCKED 组显示原因 | 渲染 PASS |

## 2. 视觉检查

| 检查项 | 结果 |
|---|---|
| 页面溢出（移动端 390px 无横向溢出） | PASS |
| 侧栏窄屏隐藏 | PASS |
| 控件重叠/错位（1440px 与 390px 目检 7 张截图） | 未发现 |
| 字体一致性（等宽字体用于 ID/数字） | 通过 |
| 图表显示（SVG 趋势图/分布条渲染正常） | PASS |
| 空态/错误态（AsyncBlock 三态覆盖全部页面） | 保持 |
| 状态语义唯一性（INCONCLUSIVE=紫色，新增 badge.incon） | 通过 |
| 深色主题 | Token 已预留（`data-theme="dark"`），**未启用**（避免半成品主题，如实记录） |

## 3. 已知限制（不伪装）

- 探索与编译中心的 LLM 状态展示：仅在真实 LLM 接入后展示 model/prompt 版本；
  当前页面明示"规则实现（rule-based-v1），不是 LLM"。
- 移动端为降级布局（单列 + 侧栏隐藏），未做触控专门优化（设计规范 §9）。
- Benchmark 页 BLOCKED 基线显示阻塞原因而非数值（诚实原则）。
