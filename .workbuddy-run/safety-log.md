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

## RUN-006（2026-11-23）

| 时间 | 检查 | 结论 |
|---|---|---|
| 14:40 | 隔离数据库 | 新增 `.traceguard/run006/{api.db,teamflow.db}` 与独立 artifact 目录；未触碰 `.traceguard/traceguard.db`、`teamflow.db` ✅ |
| 14:40 | 进程边界 | 仅启动本轮创建的 TeamFlow(8011)/API(8010)；结束后通过任务管理停止，未杀其它进程 ✅ |
| 15:05 | 网络访问 | 仅访问 127.0.0.1 本地服务与本地 Chromium；未访问外部站点 ✅ |
| 15:10 | 凭据 | 未读取/输出 `.env` 内容；LLM 调用沿用已有配置，日志无密钥 ✅ |
| 15:12 | 危险命令 | 未使用 `git reset --hard` / `git clean` / 强制推送；未对项目外目录递归删除 ✅ |
| 15:15 | 证据归档 | `docs/assets/run006/` 只含运行结果 JSON 与 SHA-256，无凭据/Token ✅ |

## RUN-007-RESUME（2026-11-23）

| 时间 | 检查 | 结论 |
|---|---|---|
| 12:20 | 分支操作 | 从 `origin/main`(`ac2876e`) 创建 `workbuddy/run007-resume-20261123`；未 `reset --hard` / 未 `clean -fd` / 未强推 ✅ |
| 12:20 | 数据库隔离 | 新增 `.traceguard/run007resume/`、`.traceguard/run007soak/`、`.traceguard/run007resume/wfr/`；未触碰既有 `traceguard.db` / `teamflow.db` ✅ |
| 12:20 | 进程边界 | 仅启动本轮 API(8020/8030)、TeamFlow(8021)、Worker(4+4)；未杀其它进程；`ps` 被沙箱禁止时改用存活探测 ✅ |
| 12:25 | 网络访问 | 仅 127.0.0.1 本地服务与本地 Chromium；GitHub 仅 `ls-remote`/`fetch`（读）；未访问未授权外部站点 ✅ |
| 12:30 | 凭据 | 未读取/输出 `.env`；LLM 调用沿用既有配置；日志与证据无密钥 ✅ |
| 13:00 | 证据保留 | 旧 `e2e-run007.json` / `worker-fault-recovery.json` / `probe-budget-*.json` 原样保留，新证据另起文件名 ✅ |
| 13:40 | 推送尝试 | `git push --dry-run` 因无凭据失败（`could not read Username`）；未使用任何绕过手段 ✅ |
