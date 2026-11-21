# Benchmark 场景元数据（阶段1 开始积累）

> 依据路线图：Benchmark 从阶段1 起积累场景、Ground Truth 与实验元数据，阶段11 建成完整系统。
> Ground Truth 答案存于 TeamFlow 的隔离域（`ground_truth.py`），本文件仅记录场景元数据与引用，不写答案细节到 Agent 可读处。

## 场景表

| scenario_id | fault_mechanism_id | 注入配置 | 预期影响 | TeamFlow 版本 | 数据种子 | GT 引用 | 清理方法 |
|---|---|---|---|---|---|---|---|
| TF-NORMAL-001 | NONE | 无 | UI 成功且持久化 | 1.0.0 | workspace:default | TF-NORMAL-001 | /api/test/reset |
| TF-API-001 | TF-API-001 | POST /api/faults/TF-API-001/activate | 创建接口 500 | 1.0.0 | workspace:default | TF-API-001 | /api/faults/TF-API-001/clear |
| TF-NET-001 | TF-NET-001 | params: {delay_ms:3000} | 请求延迟/超时 | 1.0.0 | workspace:default | TF-NET-001 | clear + reset |
| TF-DB-001 | TF-DB-001 | activate | UI 成功但未持久化 | 1.0.0 | workspace:default | TF-DB-001 | clear + reset |
| TF-UI-001 | TF-UI-001 | activate | 成功提示缺失/错误显示 | 1.0.0 | workspace:default | TF-UI-001 | clear |
| TF-TIME-001 | TF-TIME-001 | params: {delay_ms:1500} | 列表刷新延迟/竞态 | 1.0.0 | workspace:default | TF-TIME-001 | clear + reset |

## 每个场景记录的字段（路线图阶段1 要求）

- `scenario_id`：场景唯一标识
- `fault_mechanism_id`：故障机制 ID
- 注入配置：激活 API + 参数
- 预期影响：对业务/UI 的影响
- `application_version`：TeamFlow 版本 1.0.0
- 数据种子：workspace:default、测试账户 tester
- Ground Truth 引用：隔离域中的 scenario_id
- 清理方法：reset / clear

## Ground Truth 隔离验证

- Agent（Explorer/Diagnosis）无评测令牌 → 读取 GT 返回 403（已由测试 `test_ground_truth_isolated_from_agent` 验证）。
- `/api/scenarios` 仅返回 scenario_id 引用，不含预期答案。
- 评测 Harness 使用进程内令牌读取 GT（不经过 Agent 工具网关）。
