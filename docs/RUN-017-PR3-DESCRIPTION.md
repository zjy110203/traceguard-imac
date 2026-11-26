# PR #3 描述（RUN-017 累计集成候选）

> **状态：NOT_RUN（未创建）** —— 本机没有 `gh` CLI，也没有 GitHub 凭据；
> 按指令"不读取或寻找凭据、不安装额外工具来绕过权限"，本轮**没有**创建 PR。
> 下面是可以直接粘贴到 GitHub 的完整描述（创建方式见文末）。

---

## 标题

```
RUN-017: 产品成熟度提升、智能诊断完善与最终交付闭环（RUN-016 之后的累计集成候选）
```

## 正文

```markdown
## 这是什么

RUN-016 之后的**累计集成候选**分支，基线为 `f18e871`（RUN-016 文档提交）。
本轮不重新设计架构、不大规模重构，重点是把现有能力做**正确、稳定、可重复验收**。

累计包含：RUN-016 分支的全部内容（`workbuddy/run016-finalization`）+ 本轮 RUN-017。

> 本 PR 是**累计**的：PR #1（RUN-014）、PR #2（RUN-008~010）的内容都在此分支中。
> 两者保持 open，本 PR 不替代、不关闭它们。

## 本轮做了什么（全部有可复核证据）

### 1. 独立故障观测：从"测错对象"到"测得对"（WP2）

RUN-016 的 4 类探针有 4 处**测的是错对象**（真实 TeamFlow 上才会暴露）：

- `api_error` 用空载荷 POST → FastAPI 在进入路由前返回 422，注入的 500/403 永不执行；
- `api_timeout` 只有 `status=0` 才算受影响 → 快速 5xx 被判成"没影响"；
- `persistence` 用 name 去 grep 只返回 keys 的状态接口 → **健康环境也判"未持久化"**；
- `state_lag` 用响应耗时测"值过期"（TF-DB-003/004 服务端根本不 sleep）。

重写为：**故障前健康基线 + 故障后独立观测 + 明确分类 + 自清理**，
并新增 `api_update` / `read_authz` / `read_latency` / `state_consistency` 四类探针、
**浏览器级观测**（独立 BrowserContext + role/name 定位 + DOM/ARIA/截图/网络/Console +
业务状态回读）。纯 UI/定位器场景的 UNKNOWN 从 100% 降到 **0**。

### 2. Regression INCONCLUSIVE 人工授权新 Attempt（WP3）

方案 A：上限 2 次 Attempt、禁止自动重试、原因码门禁、版本/Oracle/环境自检、
幂等键绑定 proposal+version+attempt、并发点击只产生一个新 Run、旧 Run/结论/证据永不覆盖。
前端只在服务端 `regression_retry_allowed=true` 时渲染入口，并展示 Attempt 台账与血缘。

### 3. 执行内核与 Artifact 加固（WP4）

- `fenced_session` 同 Run 重入立即失败（此前会自锁到数据库超时）；
- Artifact GC 改为 keyset 分页 + 删除前定向复核；10 万条 Evidence 实测
  scan 0.383 s、内存 +0.3 MB、并发写延迟 6.5 ms；
- `TG_ARTIFACT_BACKEND != local` **启动即失败**（此前静默用本地目录冒充对象存储）。

### 4. 备份恢复真实验收（WP5）

SQLite 与 PostgreSQL 16 各 12 项校验全绿（含 Evidence 完整性重算、Artifact SHA-256、
Event 序号、迁移版本、**恢复后继续写入**）。新增只读环境诊断 `scripts/doctor.py`。

### 5. CI 门禁修复（WP1）

- PR/Nightly 的 push 分支过滤由逐轮追加改为 `workbuddy/run*`（此前 RUN-016 **从未触发**任何 workflow）；
- PR Gate 纳入 RUN-016/017 专项；Nightly 新增 PG16 fencing 重入与审计、浏览器观测、
  真实浏览器业务全链路（独立 job）；新增 `reliability-gate.yml`（手动、120 分钟长稳）。

### 6. 智能诊断质量评估（WP7）

同一实验独立执行两次共 120 样本：CONFIRMED 主因准确率 90.6%（n=32）、
方向性 81.7%（n=60）、Macro-F1 0.913 / 0.733；错误逐条登记。
修复 1 处真实规则缺陷（存在 5xx 时不得确认 UI 渲染），并用 A/B（各 10 轮）证明
该改动在可测范围内**中性**（命中场景 8/8 判 API）——如实报告，不宣称准确率被提升。

## 关键数字

| 指标 | 结果 |
|---|---|
| 全量测试（SQLite） | 932 passed / 0 failed / 16 skipped（跳过=PG 专项） |
| PG16 专项（真实 16.16） | 28 passed |
| 严格长稳 | 62.05 分钟 / 119 Run / 33 检查全 PASS / **0 NOTE** |
| Benchmark 资格实验 | 120 样本 / UNKNOWN 4（3.3%）/ 确认率 84.3% / **False Pass 0** |
| UI 业务闭环 | 58 PASS / 0 FAIL |
| UI 矩阵（16 个路由/视图 × 4 断点 × 双主题） | 128 / 128 PASS |
| 备份恢复 | SQLite + PG16 各 12 项 true |
| 供应链 | npm audit 0 漏洞；密钥扫描无命中；Ruff/vue-tsc/vite 全绿 |

## 评审重点

1. **探针分类规则**是否与真实故障语义一致（`fault_effect.py`）；
2. **重试的状态机与门禁**是否 fail-closed（`repair_service.py` / `regression_retry`）；
3. **GC 的删除前定向复核**是否足以覆盖"扫描后新增引用"的竞态；
4. 诊断规则改动是否值得保留（本轮结论：中性，保留是因为原则一致而非指标收益）。

## 不在本 PR 范围

- LLM 四臂实验 / 人工诊断基线 / 宿主级网络隔离 / 真实外部站点（BLOCKED）；
- Release Gate 与 Reliability Gate 的真实运行（需凭据 / 手动窗口）；
- S3 / MinIO 适配器、多 API 实例（v1 明确不引入）；
- LICENSE（需商业决策，本轮不擅自增加）。

## 证据位置

`docs/RUN-017-*.md`（基线与各工作包报告）、`docs/RUN-017-FINAL-REPORT.md`（总览与完成度矩阵）、
`docs/RUN-017-WORKLOG.md`（逐条日志）、`docs/assets/run017/**`（原始样本、截图、指标 JSON）。

---

**创建方式（需具备 gh CLI 与认证的环境）**：

```bash
gh pr create --base main --head workbuddy/run017-product-maturity --draft \
  --title "RUN-017: 产品成熟度提升、智能诊断完善与最终交付闭环（RUN-016 之后的累计集成候选）" \
  --body-file docs/RUN-017-PR3-DESCRIPTION.md
```

必须为 **Draft**；**不得**自动合并、不得关闭 PR #1 / #2。
```

---

## 附：本轮提交序列（`workbuddy/run017-product-maturity`）

| 提交 | 内容 |
|---|---|
| `4629337` | WP4.1 `fenced_session` 同 Run 重入检测（ContextVar，事务前失败） |
| `b112778` | WP2 故障效果独立观测修复（基线/分类/一致性/浏览器观测/探针数据隔离） |
| `9394a77` / `a51c569` | WP1 Ruff 格式基线（5 个文件不满足 `format --check`） |
| `8a0fe48` | WP1/WP2/WP4/WP5：CI 门禁、30×2 实验、GC 分页与能力边界、备份恢复、doctor |
| `1faebec` | WP3/WP5：适配重试新契约并同步部署/功能状态文档 |
| `e8a2f43` | WP6/WP7：UI 矩阵脚本、诊断规则修复、报告与台账 |
| `91a0d6f` | WP4/WP6/WP7：62 分钟 PG16 长稳、GC 规模实测、UI 矩阵 128/128、诊断 A/B 复核 |
| `1315a2f` | 收口：最终报告、质量门禁证据与状态台账 |
