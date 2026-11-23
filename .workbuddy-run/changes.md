# 代码修改记录

- 更新：2026-11-21 23:45

格式：`时间 | 文件/范围 | 动作 | 目的`

## RUN-001

- 23:45 | `.workbuddy-run/*` | 新增 | 建立八份长期运行记录基线文件
- 23:45 | 项目根目录 | 只读审查 | 确认真实起点、安全边界、资料完整性、Git 状态与环境约束

## RUN-001 阶段0
- 23:45 | 项目根 | git init + 分支 workbuddy/traceguard-autonomous | 安全基线
- 23:50 | packages/core/* | 新增 config/logging/errors/ids/database | 统一基础设施（含日志脱敏、DSN 口令遮蔽）
- 23:52 | packages/contracts/* | 新增 C01-C12 模型 + 导出脚本 + 22 Schema | 契约单一事实来源
- 23:55 | apps/api/* | 新增 FastAPI 入口 + 健康检查 + SSE 占位 | 平台入口
- 23:58 | apps/web/* | 新增 Vue3+TS 骨架 | 工作台前端
- 00:05 | infra/ + .github/ | Docker Compose + PR Gate CI | 部署与门禁
- 00:10 | docs/* | quickstart/安全基线/兼容矩阵/契约治理 | 文档
- 修复：`Settings.public_dict()` DSN 口令遮蔽；`OracleSpec.approved_by_agent` 改 Literal[False]
- 提交 c9d3a45

## RUN-001 阶段1
- 00:15 | apps/teamflow/* | 新增 models/api/faults/ground_truth/web/main | TeamFlow 被测应用
- 00:20 | benchmark/scenarios/ | 场景元数据 | C12 基础积累
- 00:22 | tests/teamflow/* | 新增 9 测试 | 阶段1 验收
- 修复：`create_project` 直接调用时 Header 对象归一为 str
- 提交 fc1397c

（后续每次重要修改追加）

## RUN-006（2026-11-23）

- `packages/core/.../security/target_policy.py` | 新增 | 统一目标访问策略（origin 白名单/重定向/内网阻断）
- `packages/execution/.../browser/session.py` | 修改 | context route guard、blocked_requests、落地复核入口
- `packages/execution/.../runtime/playwright_observer.py` | 修改 | navigate/click 目标校验 + 落地复核 + blocked_requests 入证据
- `packages/agents/.../gateway/tool_gateway.py` | 修改 | 复用 TargetPolicy、`detail` 字段、落地复核审计
- `apps/api/.../services/exploration_service.py` | 修改 | Explorer 与 Runner 共用同一策略
- `packages/evidence/.../probe/feedback.py` | 新增 | 探针证据 → 支持/反驳/排除的确定性映射
- `packages/evidence/.../probe/planner.py` | 修改 | 预算 fail-closed、CAS reserve、BudgetUnavailable
- `packages/evidence/.../diagnosis/engine.py` | 修改 | EXCLUDED、unresolved_conflicts、LLM 前缀降级修复
- `apps/api/.../services/probe_service.py` | 修改 | 探针回灌、冲突/停止原因映射、图刷新、LLM 诚实标注
- `apps/api/.../services/diagnosis_service.py` | 修改 | reassess()、Disposition 刷新、冲突落库
- `apps/api/.../services/graph_service.py` | 修改 | ProbeExecution 节点/边
- `packages/persistence/.../catalog.py` | 修改 | bind_oracles、审核门禁、版本固定、原子晋升/回滚、主体校验
- `packages/persistence/.../db.py` + `models.py` | 修改 | 迁移 v7（review_revision）
- `packages/contracts/.../models.py` + schemas | 修改 | `AssertionSpec.oracle_version` + 重新导出
- `apps/api/.../services/repair_service.py` | 修改 | 原子 CAS 晋升/回滚、主体校验
- `apps/api/.../routers/catalog.py` | 修改 | `/oracle-bindings` 端点、AssertionReq.oracle_version
- `apps/api/.../seed.py` | 修改 | 新增 2 个 Oracle，消除跨断言隐式复用
- `packages/execution/.../runtime/pipeline.py` | 修改 | 按 pinned Oracle 版本加载、按模型字段过滤文档
- 测试 | 新增 6 文件 | 62 个测试函数 / 82 采集用例
- `docs/RUN-006-*.md` + `docs/assets/run006/` | 新增 | 交付文档与证据
