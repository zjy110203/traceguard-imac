# RUN-003 测试证据汇总（2026-11-22）

> 原则：所有数字来自真实命令与真实日志；未运行的不写数字。

## 1. 后端全量回归

| 轮次 | 命令 | 结果 |
|---|---|---|
| RUN-003-A 收尾 | `.venv/bin/python -m pytest -q` | 206 passed |
| RUN-003-G 收尾 | 同上 | 222 passed（+16 LLM 网关离线单测） |
| RUN-003-D 收尾 | 同上 | 222 passed |
| RUN-003-E/F 最终 | 同上 | **222 passed**（单元/契约/集成/E2E/TeamFlow） |
| Lint | `ruff check packages apps benchmark tests scripts` | All checks passed |
| 前端构建 | `npm run build`（vue-tsc + vite） | exit 0 |

## 2. LLM 真实验收（RUN-003-G）

- 真实 API Smoke：**11/11**（`.workbuddy-run/llm-smoke-results.json`）；
- 连通性 + 模型存在性：`.workbuddy-run/llm-connectivity-2026-11-22.json`；
- 离线单测 16 项（FakeProvider/MockTransport，不花真钱）；
- Benchmark 真实对照：v1 数据集 7 组（见 RUN-003-G-LLM-Benchmark.md）；
- 成本：全程累计约 **$0.13**（预算 $1/日，应用侧估算）。

## 3. 前端真实浏览器验收（RUN-003-A）

`/tmp/verify_ui.py`（真实 Chromium 1440×900 + 390×844 移动端）：

| 检查 | 结果 |
|---|---|
| 总览指标卡/四态分布/趋势图/待办区 | PASS ×5 |
| 探索与编译中心（含诚实标注"规则实现"） | PASS ×2 |
| Run 详情主从布局/详情面板/步骤截图/网络区/断言表 | PASS ×5 |
| 诊断/修复/Benchmark 页渲染 | PASS ×3 |
| 移动端侧栏隐藏 + 无横向溢出 | PASS ×2 |
| **合计** | **17/17**（`docs/assets/run003/verify-results.json`） |

截图（`docs/assets/run003/`）：dashboard / explorer / run-detail / diagnoses / repairs / benchmark / mobile-dashboard。

## 4. 数据集 v2 逐场景验证（RUN-003-D）

- `scripts/bench_verify.py`：30 场景逐一真实浏览器执行；
- 最终轮：**30/30 verdict 与 GT 预期一致**（`.workbuddy-run/bench-v2-verify.json`）；
- 迭代过程 8 轮，修复真实问题：用例步骤缺观测、步骤 ID 重复、HTTP 缓存干扰、
  UI 断言被业务 count 遮蔽（pipeline 观测合并缺陷）、TF-API-004 钩子缺失。

## 5. 多 Worker 并发（RUN-003-E）

- `scripts/worker_concurrency_verify.py`：4 独立 Worker + 内嵌 Worker；
- 最终轮：**4/4 COMPLETED、4 个独立租约、无双重执行、无 IntegrityError**；
- 修复：claim 竞态（原子条件 UPDATE 租约 CAS）。

## 6. 失败与降级语义验证

- LLM 失败 → 规则兜底并诚实标记（离线单测 + 真实降级发生 1 次，被如实记录）；
- 执行中断 → EXECUTION_INTERRUPTED / NOT_EVALUATED（LOC/TIME-003 场景实证）；
- INCONCLUSIVE 不算 PASSED（DB-004 假通过场景实证）。

## 7. 未验证 / BLOCKED（不伪造）

- PostgreSQL 真实运行：本机无实例 → BLOCKED；
- 24h+ 长稳测试：未执行；
- 全 30 场景的诊断对照实验（含 LLM 组）：框架支持，样本预算未投入。
