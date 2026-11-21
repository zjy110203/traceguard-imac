# 中断恢复指引

> 下一次运行的第一步：读本文件 → progress.md → phase-status.md → current-task.md → decisions.md → test-results.md。
> 不要从零重新开发。不要谎称项目已完成。

- 最后保存：2026-11-22 00:12
- 运行：RUN-001

## 当前快照

- 项目根：`/Users/dlp--test/Desktop/traceguard`
- 真实进度：**阶段 0、1、2、3 已完成并提交**（分支 workbuddy/traceguard-autonomous，最新提交见 git log）。**M0、M1 里程碑达成**。累计 63 测试通过，lint/format 全绿。
- 环境：Python3.13 / Node22 可用；Docker、psql、uv、poetry 不可用；PyPI/npm 可达但极慢(25-45kB/s)。
- 已装依赖：fastapi 0.165.0 / uvicorn 0.54.0 / pydantic 2.14.4 / sqlalchemy 2.1.9 / pytest 9.2.0 / ruff / pytest-cov。mypy、httpx2、playwright 未装完（不阻塞，记录为限制）。

## 断点

- 断点位置：**阶段 4 开始前**（Event 与 Evidence 证据基础设施）。
- 若继续（按顺序）：
  1. 在具备 Docker/CI 的环境安装 Playwright+chromium，运行 PlaywrightObserver 真浏览器 E2E，回填报告。
  2. 在 CI 环境执行 `npm run build` 验证前端（本机 node 构建被沙箱终止）。
  3. 获得 1～2 个外部应用授权后执行阶段12 泛化验证（无 GT 不宣称诊断准确率）。
  4. 完成 P03（Oracle 多来源裁决）/P08（Repair 审批角色）人工裁决并更新契约。
  5. 运行 UI-FaultBench 全量 A～E 对照与消融，产出带置信区间的真实指标。
- 注意：Playwright 真浏览器 PoC 本机网络累计>30min未装成，已记录限制；PlaywrightObserver 代码就绪但未验证浏览器启动，不谎称真实浏览器 E2E。CI(ubuntu) 环境验证。

## 不要做的事

- 不要删除 `.workbuddy-run/`（项目数据，非临时缓存）。
- 不要 `git reset --hard` / `git clean` / 强推 / Docker 全局清理。
- 不要访问外部 Web 应用（阶段12 需授权）。
- 不要声称路线图 v1.1 已正式冻结。
- 不要把 Mock 结果写成真实验收证据。
