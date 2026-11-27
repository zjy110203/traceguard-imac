# 阻塞（RUN-018-R1，2026-11-27）

| 编号 | 事项 | 状态 |
|---|---|---|
| B-28 | Nightly ui-acceptance 持续失败 | MITIGATED：readiness 门禁 + Summary 唯一报告修复（#37 根因形态=进程存活但不消费，现会被 READY 门禁带日志快速失败）；待最新 HEAD CI 实跑确认 CLOSED |
| B-26 | PR #3 创建 | NOT_RUN（无 gh CLI，未授权不创建） |
| B-27 | gh/API 导出 CI 日志 | BLOCKED（无凭据，匿名 API 限流 403） |
| B-24 | Release Gate 真实 LLM 证据 | NOT_RUN（缺 Secret，禁止寻找凭据） |
| B-25 | Reliability Gate 真实运行 | NOT_RUN（需手动 120 分钟窗口） |
| B-NEW-1 | WP6 Holdout 弃权率 66.7%（PERSISTENCE/TIMING/LOCATOR 全弃权） | OPEN：引擎设计使然（R1-04 弃权优于硬猜），如需提升 Recall 须修改确认规则 → 必须换新 Holdout |
