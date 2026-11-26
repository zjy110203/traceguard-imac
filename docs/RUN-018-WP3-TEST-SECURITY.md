# RUN-018 WP3：探针清理接口安全加固

> 时间：2026-11-27
> 文件：`apps/teamflow/traceguard_teamflow/api.py`
> 测试：`tests/unit/test_probe_cleanup_security_run018.py`（19 passed）

---

## 1. 问题

`POST /api/test/probe-cleanup` 使用 `Project.name.like(prefix + "%")` 进行前缀匹配。
SQL LIKE 的 `%` 和 `_` 是通配符：

- 如果 token 含 `%`，`bp:abc%:` 会匹配 `bp:abcANYTHING:`——扩大删除范围；
- 如果 token 含 `_`，`bp:abc_:` 会匹配 `bp:abcX:`（单字符通配符）。

`new_probe_token()` 返回 `uuid.uuid4().hex[:12]`（纯 `[0-9a-f]{12}`），不含特殊字符。
但 API 接受调用方任意传入的 token，没有字符集校验。

## 2. 修复

### 2.1 token 字符集严格校验

```python
_PROBE_TOKEN_RE = re.compile(r"^[0-9a-f]{8,64}$")
```

- 只允许小写 hex 字符 `[0-9a-f]`
- 长度 8~64（覆盖 `uuid4().hex[:12]` 的 12 位）
- `%`、`_`、大写、SQL 注入字符一律拒绝

### 2.2 前缀匹配改用 startswith()

```python
# 修改前（不安全）
Project.name.like(prefix + "%")

# 修改后（安全）
Project.name.startswith(prefix)
```

SQLAlchemy 的 `startswith()` 自动转义 LIKE 特殊字符。即使 token 校验被绕过
（不应发生），`startswith("bp:abc%:")` 只匹配字面前缀 `bp:abc%:`，
不会展开为 LIKE 模式。

### 2.3 保留的安全边界（未改动）

- default workspace 需显式 `allow_default=true` + token ≥ 12 位
- 只删 `bp:<token>:` 前缀的项目
- 非 PROBE_WORKSPACE / default 一律拒绝
- 返回删除前后计数，清理失败不报告成功

## 3. 测试矩阵

| 场景 | 预期 | 实际 |
|---|---|---|
| 正常随机 token（hex） | 通过 | ✅ |
| 空 token | 拒绝 | ✅ |
| 长度不足（<8） | 拒绝 | ✅ |
| `%` 通配符 | 拒绝 | ✅ |
| `_` 通配符 | 拒绝 | ✅ |
| 大写 hex | 拒绝 | ✅ |
| SQL 注入 (`' OR 1=1 --`) | 拒绝 | ✅ |
| 特殊字符（!@#$ 等） | 拒绝 | ✅ |
| 超长（>64） | 拒绝 | ✅ |
| workspace 越权 | 拒绝 | ✅ |
| default 未授权 | 拒绝 | ✅ |
| 前缀格式精确性 | 验证 | ✅ |

**19 passed / 0 failed**

## 4. 测试环境标记

当前已有 `PROBE_WORKSPACE = "tg-benchmark-probe"` 作为测试隔离标记。
生产环境的 `default` workspace 需要显式 `allow_default=true` 才能清理，
且只删探针自己创建的数据（名称前缀匹配）。未新增 `TG_ENABLE_TEST_ENDPOINTS`——
现有机制已足够限制测试清理能力。

## 5. 交付

- 代码：`apps/teamflow/traceguard_teamflow/api.py`（+30 行）
- 测试：`tests/unit/test_probe_cleanup_security_run018.py`（19 passed）
- 状态：**VERIFIED**
