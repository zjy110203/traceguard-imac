# 测试结果汇总

- 更新：2026-11-21 23:45

> 所有数字必须来自真实测试日志。禁止编造。禁止 Mock 冒充真实运行。
> 日志存放：`tests/reports/`、`logs/`（不入库大文件）。

## 环境预检（真实执行）

| 检查项 | 命令 | 结果 |
|---|---|---|
| Python | `python3 --version` | Python 3.13.12 ✅ |
| Node | `node --version` | v22.22.2 ✅ |
| npm | `npm --version` | 10.9.7 ✅ |
| Git | `git --version` | 2.39.5 ✅ |
| Docker | `docker --version` | 不可用（记录为限制） |
| psql | `psql --version` | 不可用（用 SQLite 抽象降级） |
| uv / poetry | 版本检查 | 不可用（用 venv+pip） |
| PyPI | `pip index versions fastapi` | 可达，fastapi 最新 0.165.0 ✅ |
| npm registry | `npm view vue version` | 可达，vue 3.5.46 ✅ |

## 单元 / 契约 / 集成 / E2E 测试

暂无（阶段0 尚未产出测试）。阶段验收时在此登记测试命令、通过数、失败数、日志路径。
