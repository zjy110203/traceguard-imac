# 阻塞项（RUN-002）

| 编号 | 阻塞 | 影响 | 处理 |
|---|---|---|---|
| B-01 | 无 LLM API Key / 本地模型 | Benchmark B/D 组的 LLM 基线、LLM 生成用例无法执行 | 代码留 ModelGateway 接口；实验标记 BLOCKED，不产数值 |
| B-02 | 本机无 Docker / PostgreSQL 服务 | PG 运行时行为未验证 | Schema/迁移做方言分支；compose 提供部署配置 |
| B-03 | 外部应用泛化未授权（P-EXT） | 阶段12 真实站点验证 | 仅 TeamFlow 本地目标 |
| B-04 | P03 / P08 / 路线冻结需负责人批准 | 契约冻结 | 输出决策提案，不擅自写入 |
| B-05 | 无真实人类工程师 | 手工修复基线 | 标记 BLOCKED |

---

## RUN-005 更新

| 编号 | 阻塞 | 影响 | 处理 |
|---|---|---|---|
| B-01 | LLM Key 已配置，但多臂 Benchmark 成本/时长受限 | v3 对照无法本轮完成 | 分批计划写入 Benchmark 报告；未输出任何准确性数字 |
| B-02 | 本机无 Docker / PostgreSQL | PG 运行时仍无法本地验证 | parity 测试 + CI Service Container；本地 skip |
| B-06 | 60 分钟长稳 / 4 Worker 复验需独立时间窗 | 长稳证据缺失 | 标记 BLOCKED/PARTIAL，不引用旧结论 |
| B-07 | `vite` dev-only high 漏洞仅能 semver-major 修复 | CI Nightly security job 会失败 | 记录风险与选项，等待决策；不 `--force` |
| B-08 | P03 / P08 未批准 | Oracle 契约与 Repair RBAC 不能改 | 决策包已产出，保持现状 |
