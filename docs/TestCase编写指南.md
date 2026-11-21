# TestCase 与 Oracle 编写指南（阶段2）

## TestCase 结构

正式用例是 `TestCaseVersion`（C02），不可变版本；存储在 `tests/fixtures/testcases/*.json`。
字段见 `packages/contracts/schemas/C02_TestCase.TestCaseVersion.schema.json`。

关键点：
- `testcase_id` 格式 `tcase_<字母数字下划线>`；
- `steps` 至少 1 条；`assertions` 至少 1 条且至少 1 条 `required=true`（F05）；
- `immutable=true`、`review_status=APPROVED` 才算正式用例；
- `oracle_id` 指向 C03 OracleSpec；
- `run_config.base_url` 指向 TeamFlow。

## Action 类型

`navigate` / `fill` / `select` / `click` / `press` / `assert` / `wait` / `api`

## Locator 优先级（阶段6 Compiler 也遵循）

`role` → `accessible name` → `label` → `placeholder` → `test_id` → `css` → `xpath`（有限兜底）
TeamFlow 页面提供 `data-testid`，优先用 `test_id` 策略（稳定）。

## Assertion expected 规则

| 类型 | expected 示例 | 说明 |
|---|---|---|
| UI 文本包含 | `{"test_id":"banner-success","text_contains":"项目创建成功"}` | 元素文本包含 |
| 元素存在 | `{"test_id":"login-form"}` | 元素存在性 |
| 文本缺失 | `{"text_contains":"创建失败","expected_absent":"项目创建成功"}` | UI 故障检测 |
| 元素计数 | `{"test_id":"project-row","op":"count_gte","value":1}` | 列表行数 |
| 业务状态 | `{"api":"/api/state/projects","field":"count","op":"gte","value":1}` | Trusted Oracle 独立查询 |
| 超时 | `{"expect":"timeout"}` | 网络故障检测 |

## Oracle 编写（C03）

OracleSpec 必须有：
- `requirement_ref`：需求来源；
- `expected_behavior`：预期行为描述；
- `expectation_approval`：人工审批记录（`approved=true`、`approver` 非 agent）；
- `evaluation_rule`：确定性判定规则；
- `approved_by_agent=false`（契约层 `Literal[False]` 强制）。

Agent 可提出候选 Oracle，但不得批准/修改正式 Oracle。

## 生成 fixtures

```bash
.venv/bin/python tests/fixtures/build_fixtures.py   # 生成 10 Oracle + 10 补充用例
```

现有 14 条正式用例覆盖：登录成功/失败、正常创建、API 500、DB 假成功、UI 异常、网络超时、
列表展示、详情、编辑、空表单、权限边界、状态一致性、故障恢复、时序异常。

## 用例执行

```bash
.venv/bin/pytest tests/e2e -q   # 真实 TeamFlow + 确定性执行内核
```
