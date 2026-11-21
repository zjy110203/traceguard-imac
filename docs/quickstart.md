# TraceGuard 本地启动与测试

> 阶段0 产物。所有命令在项目根目录执行。需要 Python 3.11～3.13、Node 20+。

## 1. 后端环境

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
```

默认使用项目内 SQLite（`.traceguard/traceguard.db`）。切换 PostgreSQL：

```bash
export TG_DATABASE_URL="postgresql://user:pass@127.0.0.1:5432/traceguard"
```

## 2. 启动 API

```bash
.venv/bin/python -m uvicorn traceguard_api.main:app --app-dir apps/api --host 127.0.0.1 --port 8000 --reload
```

验证：

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/health/ready
```

## 3. 启动前端

```bash
cd apps/web && npm install && npm run dev
```

访问 http://127.0.0.1:5173 ，页面显示后端健康检查状态。

前端构建与类型检查：

```bash
cd apps/web && npm run build
```

## 4. 导出 C01～C12 JSON Schema

```bash
.venv/bin/python packages/contracts/export_schemas.py
```

产物在 `packages/contracts/schemas/`。

## 5. 运行测试

```bash
.venv/bin/pytest tests/unit tests/contracts -q
```

## 6. 质量检查

```bash
.venv/bin/ruff check packages apps tests
.venv/bin/ruff format --check packages apps tests
```

## 环境限制说明

当前机器无 Docker / psql，因此：
- `infra/docker-compose.yml` 产出但不作为本机验收依赖；
- 数据库走 SQLite（SQLAlchemy 抽象），完整 PostgreSQL 验收需在具备 Docker 的环境执行。
- 不修改系统环境（PATH/注册表/服务），所有依赖装在项目内 `.venv` 与 `apps/web/node_modules`。
