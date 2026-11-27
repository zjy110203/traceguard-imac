# RESUME（RUN-018-R1 收口，2026-11-27）

- 分支：`workbuddy/run018-diagnostic-closure`，最终 HEAD 见 `docs/RUN-018-FINAL-REPORT.md`（已推送）。
- 先读：`docs/RUN-018-FINAL-REPORT.md` → 各 WP 专题文档 → 本目录台账。
- **下一步第一件事**：核对最新 HEAD 的 PR Gate / Nightly Gate 结果（每个 Job 的 head_sha），
  确认 WP1 修复在 CI 实跑闭环（B-28 是否 CLOSED）；有 gh 凭据后
  `gh run download <id> -n ui-acceptance-evidence-<run_id>` 读取本次 run 专属证据。
- 环境：PG16.16 在 `127.0.0.1:55432`（**不要用 localhost**，IPv6 指向另一个 16.2 实例）；
  pg_dump 在 /Volumes/Postgres-2.9.7-16/...（Postgres.app 卷，find_pg_tool 已覆盖）。
- 禁止：改 main、强推、reset --hard、git clean、自动合并 PR、读取凭据。
