# RUN-011-R1 WP5：Egress 误拦复验（OPEN-RISK-01）

> 状态：**VERIFIED**（2026-11-25）｜ OPEN-RISK-01 根因已定位并修复
> 证据：`docs/assets/run011-r1/egress/egress-verify.json`（6/6）、
> `tests/unit/test_egress_policy_sync_run011_r1.py`（3/3）
> 修复文件：`packages/execution/traceguard_execution/browser/egress.py`、`manager.py`

## 0. 结论摘要

| 指令检查项 | 结论 |
|---|---|
| BrowserManager 初始化顺序 | ✅ 审查：代理先行（fail-closed）、生命周期覆盖浏览器；顺序本身无误 |
| EgressProxyController 初始化 | ✅ **发现根因**（见 §1）：策略更新未到达 EgressProxy 实例 |
| scheme_dual_policy | ✅ 实现正确（仅同 host:port 的 scheme 对偶）；问题不在对偶本身 |
| Worker 重启 / Browser Session 创建 | ✅ 会话级 `set_egress_policy` 与重启后的缓存策略是误拦触发路径 |
| HTTPS Upgrade | ✅ CONNECT 评估路径正确（raw_scheme=https + 端口白名单） |
| 代理策略缓存 / Run 级策略快照 | ✅ **根因所在**（旧策略滞留）；已修 + policy_origins 审计快照 |
| 建议 1 保存 policy_origins | ✅ `audit_summary()` 新增 `policy_origins`（对账误拦必备） |
| 建议 2 启动安全预热 | ✅ `_warmup_egress()`：授权 origin（含 https 对偶）经代理探测被拦即 fail-closed |
| 建议 3 新 Worker 冷启动复测 | ✅ S1/S4：三个冷启动 Worker 对照全 PASSED |
| 建议 4 对照授权目标 HTTP/HTTPS | ✅ S1/S2/S4：`policy_origins` 含 http+https 双变体、零 PORT_NOT_ALLOWED |
| 建议 5 未授权目标零成功响应 | ✅ S3：`PORT_NOT_ALLOWED` 前置拦截（策略拒绝），零成功 |
| 未扩大端口白名单 | ✅ 单元回归：未授权端口仍 `PORT_NOT_ALLOWED`；对偶不增加端口面 |

## 1. 根因（OPEN-RISK-01：授权目标 HTTPS 变体偶发 PORT_NOT_ALLOWED）

**症状**（RUN-011 WP7 实测，`network-negative-2.json`）：负向 Run 与对照 Run 共享
同一 Worker 时，对照 Run 的 `https://127.0.0.1:8112`（授权目标 HTTPS 变体）被代理
判 `PORT_NOT_ALLOWED`（7 次对照中 2 次）；多数运行正常 → "偶发"。

**根因链**（本轮代码审查 + 确定性单元回归证实）：

1. `BrowserManager.set_egress_policy()` 只执行 `self._egress.policy = scheme_dual_policy(policy)`
   —— 给 **EgressProxyController** 的属性重新赋值；
2. `EgressProxyController.start()` 里 `EgressProxy(self.policy)` 把策略对象**按值**交给
   `EgressProxy` dataclass 字段——之后 controller.policy 再怎么换，**`proxy.policy`
   仍是 start() 时的旧对象**（评估路径 `self.policy.check(...)` 走的是它）；
3. 共享 Worker 上，浏览器/代理的生命周期跨 Run：先跑的 Run 的策略（可能是
   `base_url=127.0.0.1:9555` 的负向用例策略）留在代理里；后跑的对照 Run
   （授权 origin 8112/8111）通过 `set_egress_policy` 更新策略 → **更新没有到达代理**；
4. 对照 Run 的请求（含 Chromium HTTPS-Upgrade 的 CONNECT，按 https 评估）拿旧策略
   评估：host 127.0.0.1 在 hosts 里、但 `https://127.0.0.1:8112` 不在旧 origins 里
   → `PORT_NOT_ALLOWED`。

**"偶发"的来源**：取决于负向 Run 与对照 Run 是否共享 Worker、以及代理是否在两次
Run 之间重启（重启会拿缓存的 `_egress_policy` 重建）——RUN-011 WP7 的 3 次负向测试
中恰好 2 次落入触发组合。

**排除项**：scheme_dual_policy 实现本身正确（对偶只翻转 scheme，host/port/私网/
rebinding 检查全部保持）；CONNECT 评估、HTTPS-Upgrade 处理无误；端口白名单未被扩大。

## 2. 修复（不放宽任何安全限制）

| # | 变更 | 文件 |
|---|---|---|
| 1 | `EgressProxyController.set_policy()`：同时更新 `controller.policy` **与** `proxy.policy` 两个引用（根因修复） | `browser/egress.py` |
| 2 | `manager.set_egress_policy()` 改走 `set_policy()`（不再直接给 controller.policy 赋值） | `browser/manager.py` |
| 3 | `audit_summary()` 新增 **`policy_origins`**（当前授权 origin 快照，误拦可对账）——建议 1 | `browser/egress.py` |
| 4 | `_start_egress()` 后 **`_warmup_egress()` 安全预热**：每个授权 origin（含 https 对偶）发一次经代理的探测请求，代理审计出现 BLOCK 即 `BrowserUnavailableError` fail-closed——建议 2 | `browser/manager.py` |

安全面保持不变：未授权 host/port 依旧拒绝（`PORT_NOT_ALLOWED`）；未授权 https 变体
依旧拒绝；私网/rebinding 检查不动；不扩大端口白名单、不放宽任何网络限制。

## 3. 复验证据（`egress-verify.json`，6/6）

| 场景 | 内容 | 结果 |
|---|---|---|
| S1 冷启动对照 | 授权目标执行 PASSED；`policy_origins=[http://127.0.0.1:8111, https://127.0.0.1:8111]`（**HTTPS 变体在列**）；blocked=0 | ✅ |
| S2 stale-policy 回归 | 先跑 `base_url=9555` 的 Run（其策略 `{http,https}@9555` 正确进入代理——`policy_origins` 可见），再跑对照 Run → **PASSED、零 PORT_NOT_ALLOWED**（修复前该组合正是误拦形态） | ✅ |
| S3 未授权目标负向 | 绝对 URL `http://127.0.0.1:9555` 导航 → `TARGET_NOT_ALLOWED: PORT_NOT_ALLOWED` **策略前置拦截**（请求未出网），verdict=NOT_EVALUATED，零成功响应 | ✅ |
| S4 冷启动对照（另一 Worker） | 第三个冷启动 Worker 上对照 PASSED，无误拦 | ✅ |

- 本轮 S2 的两次 Run 恰好落在不同 Worker（`run011r1-w1 → run011r1-w2`，
  `same_worker=false`）——同 Worker 组合的**确定性**验证由单元回归覆盖：
  `test_set_policy_updates_proxy_reference` 直接断言 `proxy.policy` 与
  `controller.policy` 同步且旧 origin 被正确移出（修复前该断言必失败）。
- 诊断可对账性（建议 1 落地）：`policy_origins` 现在随每份 egress 审计输出——
  未来任何误拦可直接核对「被拦 origin 是否在当时的 policy_origins 里」，
  区分「配置缺失」与「评估错误」。

## 4. 边界与如实声明

1. S3 的拦截点是**会话 route guard**（请求发出前的策略评估），不是代理层——
   两者共用同一 TargetPolicy，指令的"零成功响应"目标达成；WP7 原有证据中
   代理层拦截 https 变体的路径（CONNECT）已在 RUN-011 验证过，本报告不重复外推。
2. 安全预热（建议 2）在 `_start_egress` 失败时 fail-closed（浏览器不启动）——
   这是把"配置错误"从"业务 Run 偶发失败"提前到"启动期明确报错"，
   未改变授权判定本身。
3. 本修复针对的是**授权目标被误拦**（假阴性方向）；对未授权目标的拦截行为
   （假阳性方向）保持原样并经 S3 复验。
