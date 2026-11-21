# 中断恢复指引

> 下一次运行的第一步：读本文件 → progress.md → phase-status.md → current-task.md → decisions.md → test-results.md。
> 不要从零重新开发。不要谎称项目已完成。

- 最后保存：2026-11-21 23:45
- 运行：RUN-001

## 当前快照

- 项目根：`/Users/dlp--test/Desktop/traceguard`
- 真实进度：**阶段 0、1 已完成并提交**（Git: c9d3a45、fc1397c，分支 workbuddy/traceguard-autonomous）。**M0 里程碑达成**。累计 31 测试通过，lint/format 全绿。
- 环境：Python3.13 / Node22 可用；Docker、psql、uv、poetry 不可用；PyPI/npm 可达但极慢(25-45kB/s)。
- 已装依赖：fastapi 0.165.0 / uvicorn 0.54.0 / pydantic 2.14.4 / sqlalchemy 2.1.9 / pytest 9.2.0 / ruff / pytest-cov。mypy、httpx2 未装完（不阻塞）。

## 断点

- 断点位置：**阶段 2 开始前**（专业 Web UI 自动化测试框架）。
- 下一步（按顺序）：
  1. 后台安装 playwright + pytest 插件（网络慢，务必后台+耐心），实测浏览器 chromium 可启动（PoC）。
  2. 建 packages/execution：TestCase/TestCaseVersion 加载、Browser Manager/Context/Page Manager、Locator 管理、Action Executor、Assertion Executor。
  3. 建 Oracle Manager（来源+审批记录）与 Verdict Aggregator（阶段2 已冻结四态聚合规则 + 8 补充规则）。
  4. 写 10-20 条 TeamFlow 用例，覆盖正常/异常/表单/列表/权限边界/网络场景。
  5. 验证 Test Verdict 合法/非法样例可执行、NOT_EVALUATED 与 INCONCLUSIVE 可区分、execution_status 与 test_verdict 独立、历史 Run 不覆盖。
  6. 阶段2 测试通过后更新 phase-status，判定 ACCEPTED，进入阶段3。
- 注意：若 Playwright 浏览器在本环境无法安装/启动，记录真实限制，先用轻量 DOM 驱动的确定性执行内核验证 Verdict 逻辑（标记清楚与真实浏览器的差异），不谎称完成浏览器 E2E。

## 不要做的事

- 不要删除 `.workbuddy-run/`（项目数据，非临时缓存）。
- 不要 `git reset --hard` / `git clean` / 强推 / Docker 全局清理。
- 不要访问外部 Web 应用（阶段12 需授权）。
- 不要声称路线图 v1.1 已正式冻结。
- 不要把 Mock 结果写成真实验收证据。
