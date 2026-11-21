# 中断恢复指引

> 下一次运行的第一步：读本文件 → progress.md → phase-status.md → current-task.md → decisions.md → test-results.md。
> 不要从零重新开发。不要谎称项目已完成。

- 最后保存：2026-11-21 23:45
- 运行：RUN-001

## 当前快照

- 项目根：`/Users/dlp--test/Desktop/traceguard`
- 真实进度：阶段 0 尚未开始编码；只读审查 + 运行记录基线已完成。
- Git：非仓库（即将初始化）；分支计划 `workbuddy/traceguard-autonomous`。
- 环境：Python3.13 / Node22 可用；Docker、psql、uv、poetry 不可用；PyPI/npm 可达。

## 断点

- 断点位置：阶段 0 实施开始前。
- 下一步（按顺序）：
  1. `git init` + `.gitignore` + 分支 + 基线提交。
  2. 建项目目录骨架与根 `pyproject.toml`、`README.md`。
  3. 建 venv 并安装后端基础依赖（FastAPI/Pydantic/SQLAlchemy/pytest/uvicorn），实测版本兼容并记录。
  4. 写统一 config / logging / errors / health。
  5. 建 C01～C12 JSON Schema 与契约测试。
  6. 建前端 Vite+Vue3+TS 骨架并实测 build。
  7. 建 CI 三级门禁配置与安全基线。
  8. 阶段0 单元/契约测试通过后更新 phase-status 为 READY_FOR_REVIEW，再按验收证据判定 ACCEPTED。

## 不要做的事

- 不要删除 `.workbuddy-run/`（项目数据，非临时缓存）。
- 不要 `git reset --hard` / `git clean` / 强推 / Docker 全局清理。
- 不要访问外部 Web 应用（阶段12 需授权）。
- 不要声称路线图 v1.1 已正式冻结。
- 不要把 Mock 结果写成真实验收证据。
