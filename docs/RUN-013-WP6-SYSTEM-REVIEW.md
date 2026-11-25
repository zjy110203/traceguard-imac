# RUN-013 WP6：系统级代码审查与产品成熟度

> 日期：2026-11-25（CST）
> 范围：后端授权/并发/证据链、15 个前端视图、部署与运维。
> 原则：不为增加工作量重写稳定模块；只修真实缺陷，无法验证的明确标注。

---

## 1. 后端审查

### 1.1 发现并修复的真实缺陷

| # | 缺陷 | 影响 | 修复 | 证据 |
|---|---|---|---|---|
| 1 | `/events/stream` 不在认证中间件覆盖范围 | 匿名可订阅事件流；任意用户跨 Workspace 读取（含游标绕过） | 路由内显式会话解析 + `scope_filter` + `list_events_after(workspace_ids)` SQL 下推 | WP2 测试 8 例 + 浏览器 10 项 |
| 2 | 旧 Worker 恢复后继续写 Evidence | 已接管 Run 被追加重复证据（19→38）；可能追加 `run.failed` 事件 | `lease_valid()` 同步校验 + abandon 路径（不写证据/不发事件/不提交） | WP1 W1-⑮ 复验 19→19 |
| 3 | 回放后 Oracle 改版仍可批准 | 用旧 Oracle 的 STABLE_PASS 批准新业务预期 | 审核事务内重核验 Oracle 版本/审批/Workspace | WP3 门禁 12 passed |
| 4 | `/system/health` 计数未按 Workspace 过滤且 `limit=1000` 截断 | 普通用户可推断全局业务规模；大库计数失真 | SQL `COUNT(*)` + `workspace_ids` 过滤（与 `/system/metrics` 同口径） | WP2 健康计数测试 |

### 1.2 审查通过（未改动）

| 领域 | 结论 |
|---|---|
| 认证 / RBAC | 中间件 fail-closed；写操作 CSRF；未知写操作默认 `system.configure`；四角色矩阵与 HTTP 动作映射一致 |
| 对象级授权 | detail/download 以实体 `workspace_id` 为准；列表 `scope_filter` 省略不等于全库 |
| 事务与幂等 | Run 提交 `fencing + idempotency receipt`；Doc 写入 CAS；Review `review_revision` CAS |
| Worker 租约 | 原子条件 UPDATE 领取；过期回收；`fencing_token` 单调递增；旧 owner 提交拒绝 |
| 证据 / Artifact | 内容寻址 + SHA-256；下载前实体授权；证据校验区分存储完整性与来源比对 |
| 审计链 | 登录/拒绝/审批写审计；哈希链校验；敏感字段脱敏 |
| SSE | 持久化事件（非内存快照）；`Last-Event-ID` 续传；DB 短暂不可用保持连接；本次补授权 |
| 诊断/Repair | LLM 仅 PROBABLE，不 `CONFIRMED`；Repair 血缘与审批主体校验；沙箱/晋升/回滚状态机 |
| 迁移 | `tg_schema_migration` 版本表幂等；SQLite/PG 双方言 |

### 1.3 保留风险（未标 VERIFIED）

- SSE 长连接在授权撤销后不会主动断开，只在下一次读取/重连时拒绝；已打开连接可能继续收到撤销前已授权事件。
- `/api/v1/queue/stats` 为全局队列状态（不含业务内容），任意登录用户可读；属于运维观测，未按 Workspace 过滤。
- Artifact 下载在 `get_meta` 缺失时依赖 `read()` 的 404 兜底；未构造“文件存在但元数据缺失”的越权场景。

## 2. 前端审查（15 视图）

| 维度 | 结论与改动 |
|---|---|
| 实际操作能力 | RUN-012 已补齐 Oracle 绑定/回放/LLM 建议；本轮补齐回放**结果可视**与审核条件，核心业务不再只能靠脚本 |
| 加载/空/失败状态 | `AsyncBlock` 统一；回放面板新增空状态、运行中、失败/未满足状态 |
| 认证过期 | 401 清 CSRF、跳登录；本轮补 `resetWorkspaceContext()`，避免旧数据残留 |
| 权限不足 | 403 由视图提示；前端 `can()` 仅隐藏入口，服务端仍是权威 |
| Workspace 切换 | `:key=generation` 重挂载；修复竞态、参数覆盖、换用户残留、多标签不同步 |
| 搜索/筛选/分页 | App 全局搜索按当前空间；列表页 limit 固定，未做服务端分页（保留现状，未见业务阻塞） |
| SSE | RunDetail 订阅/卸载关闭；断线降级轮询；事件按 ID 去重 |
| 证据查看 | Run 详情可查看步骤/证据/下载；跨空间 403 已由后端保证 |
| 深色/移动端/无障碍 | 390px 深色无横向溢出；表单 label/aria 历史已修；本轮未新增长文本溢出 |
| 按钮真实性 | 抽查 15 视图未发现“只 Toast 不落库”的按钮；回放按钮新增服务端状态回读与门禁展示 |

未逐按钮重写 15 个视图：RUN-012 已完成 29 项真实闭环与 15 视图接入；本轮只改与
Workspace/回放相关的真实缺陷，避免为“审查数量”制造无意义改动。

## 3. 部署与运维

- `docs/DEPLOYMENT.md` 新增第 9 节：进程拓扑、健康检查、优雅退出/故障恢复、
  PG/SQLite 备份恢复、迁移升级、日志排查、生产检查清单；
- 更正 PostgreSQL “未验证”旧描述：已在 16.16 真实验证（专项 + 60 分钟长稳）；
- 新增 `TG_WORKER_METRICS_DIR`：Worker 原子写 RSS/CPU 快照，宿主机禁用 `ps` 时仍可观测；
- `/api/v1/system/health` 增加 `process` 资源字段。

## 4. 产品成熟度结论

| 能力 | 状态 |
|---|---|
| 独立 Worker 生产形态 | VERIFIED（SQLite 全链路 + PG16 长稳） |
| Workspace 并发/对象级权限 | VERIFIED（API 8 例 + 浏览器 10 项） |
| 稳定性回放→审核 | VERIFIED（compiler/healed 正向 + 3 类负向） |
| 60 分钟长稳/故障恢复 | 见 WP4 |
| AI 真实 Provider | VERIFIED（Smoke + 6 场景质量评估，样本有限） |
| 部署运维手册 | VERIFIED（文档 + 真实命令） |
| Benchmark 23×2 | NOT_RUN（见 WP5） |
| 宿主级网络隔离 / B-17 / 人工基线 | BLOCKED（外部条件） |
