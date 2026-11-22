# 阻塞项（RUN-002）

| 编号 | 阻塞 | 影响 | 处理 |
|---|---|---|---|
| B-01 | 无 LLM API Key / 本地模型 | Benchmark B/D 组的 LLM 基线、LLM 生成用例无法执行 | 代码留 ModelGateway 接口；实验标记 BLOCKED，不产数值 |
| B-02 | 本机无 Docker / PostgreSQL 服务 | PG 运行时行为未验证 | Schema/迁移做方言分支；compose 提供部署配置 |
| B-03 | 外部应用泛化未授权（P-EXT） | 阶段12 真实站点验证 | 仅 TeamFlow 本地目标 |
| B-04 | P03 / P08 / 路线冻结需负责人批准 | 契约冻结 | 输出决策提案，不擅自写入 |
| B-05 | 无真实人类工程师 | 手工修复基线 | 标记 BLOCKED |
