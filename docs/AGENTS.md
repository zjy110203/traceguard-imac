# Agent 与自愈机制说明

## 1. Agent 是能力层，不是事实层

平台的所有"事实"来自：真实执行观测、独立状态查询、已审批 Oracle、不可变证据。
Agent 只能提出候选（动作、用例、假设、修复），不能批准 Oracle、不能宣布根因已确认、
不能修改正式断言。

## 2. Explorer（探索）

入口：`POST /api/v1/intents/{id}/explore?max_steps=8`

流程：

```
Intent → planner.plan（候选动作）→ ToolGateway 六步检查 → 执行 → 观测 → 记录 Trace
```

ToolGateway 检查顺序（不可颠倒）：工具白名单 → 参数类型 → 域名/端口白名单 → 风险 → 预算 → 执行 → 审计。

- 允许工具：`navigate / click / fill / read_page / read_dom / screenshot`
- 禁止：shell / 任意文件 / 任意网络
- 页面内容是不可信数据：不因页面文字提升权限（Prompt Injection 防护）
- 探索成功 ≠ 测试通过：Trace 不含 verdict

实测（`intent_tf_create_normal`）：3 个动作全部 allowed（navigate → fill → click），
stop_reason=`目标完成`，轨迹落库可复现。

## 3. Compiler（用例编译）

入口：`POST /api/v1/explorations/{id}/compile`

- Locator 优先级：`role > test_id > label > placeholder > css > xpath`；
- 断言从意图生成**候选**，`oracle_id=None`（Oracle 绑定必须人工）；
- 产出 `review_status=PENDING` 的候选用例；
- 无业务状态接口时写入 warning：`NO_BUSINESS_STATE_API`，明确"不能宣称持久化已验证"。

实测：编译产出 1 个候选用例，1 条 warning，审核前不可执行（412）。

## 4. Diagnosis Agent

见 `EVIDENCE-AND-DIAGNOSIS.md`。要点：假设由规则生成，确认由规则裁决，LLM 不得宣布 CONFIRMED。

## 5. Oracle-Protected Self-Healing

### 5.1 允许的变更（Diff 白名单）

```
steps[].target        # 定位器
steps[].value         # 输入值
run_config.wait_timeout
test_data_refs
steps[].action_config
```

禁止任何包含 `assertion / oracle / expected_behavior / required / product_code / business` 的路径，
越界请求在**创建提案时**即返回 412。

### 5.2 双重保护

| 保护 | 实现 | 失败表现 |
|---|---|---|
| Assertion Preservation | 必需断言不得删除；`kind`/`required`/`expected` 不得改变 | `assertion_preservation=FAIL`，状态 VALIDATION_FAILED |
| Oracle Integrity | 断言 → Oracle 绑定不得变更 | `oracle_integrity=FAIL` |
| Diff 白名单 | 纯函数 `diff_allowed` | 提案被拒 |
| 沙箱真实执行 | 候选版本作为 PENDING + `sandbox_only` 执行一次真实 Run | `sandbox_status=FAILED` |
| 人工审批 | `approver` 含 agent/llm 一律拒绝 | 403 |
| CAS 晋升 | 当前正式版本必须等于 base | `PROMOTION_FAILED / VERSION_CONFLICT` |
| 幂等晋升 | `idempotency_key` 重放直接返回 | — |
| 回滚 | 生成恢复版本，保留历史 | 状态 `ROLLED_BACK` |

### 5.3 生命周期

```
DRAFT → VALIDATING → VALIDATED → PENDING_APPROVAL → APPROVED → PROMOTING → PROMOTED → ROLLED_BACK
                 ↘ VALIDATION_FAILED                        ↘ PROMOTION_FAILED
PENDING_APPROVAL → REJECTED
```

`status`（生命周期）与 `sandbox_status`（验证结果）是两个独立维度。

### 5.4 实测（真实数据）

| 步骤 | 结果 |
|---|---|
| 提案（`create-submit` → `role:button:创建`） | VALIDATED，断言保持 PASS，Oracle 完整 PASS |
| 沙箱执行 | 真实 Playwright Run，`sandbox_status=PASSED` |
| 提交审批 | PENDING_APPROVAL |
| 人工批准 | APPROVED（`role:qa-lead`） |
| 晋升 | PROMOTED → 新正式版本 v3（CAS_OK） |
| 回归 | v3 执行 PASSED（8 步） |
| 回滚 | ROLLED_BACK → 生成 v4（base 内容） |

Benchmark C 的另一组结果：
- Oracle-Protected：沙箱验证成功率 1/1，断言保持率 1/1，回归失败率 0/1；
- 对照组（允许削弱断言）：断言保持率 **0/1** —— 证明规则保护的必要性。

## 6. 已知边界

1. 自愈仅覆盖定位器/等待/数据引用，不修复产品缺陷（产品缺陷走 Disposition）。
2. 沙箱执行与正式执行同一浏览器环境，未做网络隔离级沙箱。
3. 审批角色矩阵待 P08 决策（当前只校验"非 agent 的具名角色"，未定义 RBAC）。
4. 未实现自动回归套件编排（当前按单用例回归验证）。
