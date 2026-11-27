# 当前任务（RUN-018-R1 收口，2026-11-27 09:37 CST 开始，进行中）

> 分支 `workbuddy/run018-diagnostic-closure`（起点 `7674842`）。
> 主题：核心正确性修复、智能诊断闭环验证与产品验收收口。
>
> 本轮完成（提交 2f26686 → 6b891a7 → 22901a4 之后）：
> - WP1 CI 门禁：Ruff 16 项清零 + SIGKILL 多进程确定性同步（READY 文件协议）
>   + Worker readiness 门禁（心跳校验）+ Job Summary 本次 run 专属报告；
> - WP2 GC：统一跨进程生命周期锁（PG advisory + SQLite 锁表）+ 引用守卫
>   （ARTIFACT_COLLECTED）+ GC journal 崩溃恢复 + 13 项真实多进程并发测试
>   （SQLite+PG16）→ VERIFIED；
> - WP3 探针清理：真实 TeamFlow 集成测试 8 项 → 与 19 项单测共 27 项；
> - WP4/WP6 双提交：真实浏览器实验 A/B → FRONTEND_DEBOUNCE 确认 + 服务端
>   无幂等产生重复数据（诚实发现）→ VERIFIED；
> - WP5 诊断反馈：真实 TeamFlow 端到端闭环（真实故障→真实探针→新证据→图
>   更新→revision 链）2/2 → VERIFIED；
> - WP6 Holdout：规则冻结 manifest + 24 新变体 + 2 轮独立执行 + 显式分母
>   评测（Selective Accuracy 1.0 / 弃权 66.7% 如实报告）→ VERIFIED；
> - WP7 监控：真实 uvicorn API 5 场景（重启/Worker停止恢复/积压/数据源不可用
>   +TOCTOU/Artifact缺失/GC异常）→ 32 项测试；
> - WP8 无障碍：16 视图 × 2 主题 × 2 断点 = 65/65 PASS，5 类真实违规全部修复
>   → VERIFIED；
> - WP9 运维：工具箱 6 子命令 + 5 项集成测试（含真实 PG16 pg_dump 备份恢复）。
>
> 最终回归：ruff 全绿；492 unit + 542 integration passed（SQLite）；
> 前端 typecheck + build 通过；UI 业务闭环 36/36。
>
> 禁止：改 main、强推、reset --hard、git clean、自动合并 PR、读取凭据。
