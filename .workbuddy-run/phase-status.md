# 阶段状态（RUN-017，2026-11-26 21:55 CST）

| 阶段 | 状态 | 证据 |
|---|---|---|
| WP0 基线核查 | DONE | `docs/RUN-017-BASELINE.md` |
| WP1 CI 门禁 | DONE（真实触发；#74/#26 暴露 1 个断言冲突已修） | `docs/RUN-017-WP1-CI.md` |
| WP2 独立观测 | DONE | 60 样本 / UNKNOWN 2 / FP 0 |
| WP3 重试 | DONE | 15 passed |
| WP4.1 重入 | DONE | 6 passed（PG16） |
| WP4.2 GC | DONE（待 10k/50k/100k 实测数字） | `gc-bench.json` |
| WP4.3 边界 | DONE | 启动失败用例 |
| WP4.4 长稳 | RUNNING | soak 目录 |
| WP5 备份/诊断 | DONE | 12 项全 true（SQLite + PG16） |
| WP6 UI | TODO | — |
| WP7 诊断 | 分析 + 1 规则修复 | — |
| WP8 全量 | PARTIAL | 932 passed（本地） |

---

# 阶段状态（RUN-016，2026-11-26）

| 阶段 | 状态 | 证据 |
|---|---|---|
| WP0 基线核对（12 问题逐项） | DONE | `docs/RUN-016-BASELINE.md` |
| WP1-A 严格 60 分钟长稳 | DONE（1 NOTE） | `docs/RUN-016-WP1-SOAK.md` |
| WP1-B Fencing 全链路审查 | DONE | `docs/RUN-016-WP1-FENCING.md` |
| WP1-C Artifact 生命周期 | DONE | `docs/RUN-016-WP1-ARTIFACT.md` |
| WP2.1 Regression 多进程并发 | DONE（13/13） | `docs/assets/run016/wp2/regression-multiprocess.json` |
| WP2.2 INCONCLUSIVE 重试 | DONE（决策稿） | `docs/RUN-016-REGRESSION-RETRY-DECISION.md` |
| WP2.3 Promotion 失败恢复 | DONE（7 测试） | `docs/RUN-016-WP2-REGRESSION.md` |
| WP3 Benchmark 独立故障观测 | DONE；23×2 NOT_RUN | `docs/RUN-016-WP3-BENCHMARK.md` |
| WP4.1 /system/config 权限 | DONE（10 测试） | `docs/RUN-016-WP4-PRODUCT.md` |
| WP4.2 SSE 撤权 5s | DONE | 同上 |
| WP4.3 前端全链路 E2E | DONE（58/0） | `docs/assets/run016/wp4/ui/` |
| WP5 CI 口径 + 全量回归 | DONE（CI NOT_RUN） | `docs/RUN-016-WP5-CI.md` |
| 最终报告 | DONE | `docs/RUN-016-FINAL-REPORT.md` |
