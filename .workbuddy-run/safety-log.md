# 安全检查与异常记录

- 更新：2026-11-21 23:45

## 已执行的安全检查

| 时间 | 检查 | 结论 |
|---|---|---|
| 23:40 | 识别 TraceGuard 项目根目录是否唯一/有歧义 | `资料/` 内含资料 zip，**无第二份含代码的 TraceGuard 目录**，无歧义 ✅ |
| 23:40 | 检查 Git 分支/提交/未跟踪/工作区状态 | 非 Git 仓库；无用户未提交改动需保护 ✅ |
| 23:40 | 扫描 `.env` / 密钥 / Token / 私钥 / 证书 | 资料中仅见 `.env.example`、`config.yaml.example`、`mcp_servers.json.example` 等示例文件；未发现真实凭据 ✅（后续编码严禁写入真实凭据） |
| 23:42 | 网络出口 | 仅访问 PyPI / npm 官方仓库做依赖解析，符合授权范围 ✅ |
| 23:42 | 系统环境 | 未修改系统 PATH / 注册表 / 电源 / 服务；未申请管理员权限 ✅ |

## 安全边界声明（持续有效）

- 所有操作限制在 `/Users/dlp--test/Desktop/traceguard`。
- 禁止：`rm -rf`、`git clean`、`git reset --hard`、强制推送、Docker 全局清理、`docker compose down -v` 等。
- 不读取/修改项目外的个人文件、系统文件、其他项目、真实凭据。
- 外部 Web 应用未获授权前不访问（阶段12 保持 BLOCKED）。
- Ground Truth 与 Agent 上下文隔离；正式 Oracle 不由 Agent 批准/修改。

## 异常
- 无。
