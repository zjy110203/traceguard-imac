"""TeamFlow：受控最小被测 Web 应用（阶段1）。

- 业务功能：登录/测试账户、Workspace、项目创建/列表/详情/编辑。
- 独立业务状态查询：供 Trusted Oracle 验证，不依赖 UI。
- 可重复故障注入：TF-API-001 / TF-NET-001 / TF-DB-001 / TF-UI-001 / TF-TIME-001。
- Ground Truth 隔离：仅评测侧可读，Agent 运行上下文不可访问。
"""

__version__ = "1.0.0-teamflow"
