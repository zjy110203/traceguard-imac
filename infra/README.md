# infra 目录说明

- `docker-compose.yml`：本地依赖（PostgreSQL/Redis/MinIO）。安全约束：仅本地端口绑定、专用容器名与数据卷、密钥通过 secrets 文件注入、无 privileged、无 host network、无 docker socket。
- `secrets/`：真实凭据放置处（被 `.gitignore` 排除，绝不入库）。部署者需自行创建 `pg_password.txt`、`minio_user.txt`、`minio_password.txt`。
- CI 门禁在仓库根 `.github/workflows/`：
  - PR Gate：lint / format / 前端 typecheck+build / unit / contract（快速确定性）；
  - Nightly Gate、Release Gate：阶段11/13 补齐（含完整 Benchmark 与 E2E）。

## 门禁规则（路线图 F09/G04 冻结）

1. PR Gate 失败不得合并。
2. Nightly 失败必须能定位到具体用例与环境；不可笼统归因为"偶发"。
3. Release 关键失败项不得带风险豁免放行；豁免需记录有效期与责任人。
4. 三门禁职责不重叠，PR 不跑完整 Benchmark。
