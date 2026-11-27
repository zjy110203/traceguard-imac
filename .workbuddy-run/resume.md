# RESUME（RUN-018-R2，2026-11-27）

- 分支：`workbuddy/run018-diagnostic-closure`，当前 HEAD `05a3639`（已推送）。
- 先读：`docs/RUN-018-FINAL-REPORT.md` §0 → WORKLOG R2 节 → 各 WP 专题文档 → 本目录台账。
- **B-28 已关闭**：PR Gate #106 @ `05a3639` success 3m18s；Nightly #59 @
  `05a3639` success 12m55s（6/6 job，ui-acceptance 2m4s 无 FAIL annotation）。
  后续若 Nightly 再失败，可直接读 Actions 的 `UI 验收失败` annotation
  （R2 已让每条 FAIL 直接生成）。
- B-28 根因：独立 Worker 白名单 PYTHONPATH 漏 `apps/workers`，本地 venv
  `traceguard.pth` 掩盖；CI 无该 .pth → `-m traceguard_worker.main` 找不到包。
  CI 链路同类脚本审计完成：只有 run012 缺该路径（`worker_concurrency_verify.py`
  也有同类写法，但不进入 CI 门禁，留作后续手工脚本清理）。
- 环境：PG16.16 在 `127.0.0.1:55432`（**不要用 localhost**，IPv6 指向另一个 16.2 实例）；
  pg_dump 在 /Volumes/Postgres-2.9.7-16/...（Postgres.app 卷，find_pg_tool 已覆盖）。
- 禁止：改 main、强推、reset --hard、git clean、自动合并 PR、读取凭据。
