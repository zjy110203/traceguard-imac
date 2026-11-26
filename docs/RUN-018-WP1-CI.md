# RUN-018 WP1：Nightly UI 验收与 CI 最终闭环

> 时间：2026-11-27
> 状态：**PARTIAL**（PR Gate 全绿，Nightly ui-acceptance 仍失败）

---

## 1. CI 状态（2026-11-27 核查）

### PR Gate

| Run | head_sha | 结果 |
|---|---|---|
| **#81** | `c807284` | **success** (2m28s) |
| #80 | `feae308` | success |
| #78 | `4c2f851` | success |
| #77 | `2d35998` | success |
| #76 | `1315a2f` | success |
| #75 | `1faebec` | success |

### Nightly Gate

| Run | head_sha | 结果 | 失败 Job |
|---|---|---|---|
| **#33** | `c807284` | **failed** (22m11s) | ui-acceptance |
| #32 | `feae308` | failed (23m38s) | ui-acceptance |
| #31 | `f34f6b4` | failed (11m54s) | ui-acceptance |
| #30 | `4c2f851` | failed (11m48s) | ui-acceptance |
| #29 | `2d35998` | failed (11m52s) | ui-acceptance |

**其他 Job（backend-full / postgres / security / llm-smoke）全部通过。**

## 2. B-28：Nightly ui-acceptance 失败

### 2.1 事实

- 本地：core profile 35/35、full profile 58/58 通过
- CI（GitHub runner）：全部失败（退出码 1）
- 耗时：本地 < 5 分钟，CI 11~33 分钟
- gate-verdict 正确判失败（门禁未被掩盖）

### 2.2 已做处置（RUN-017）

1. 从 backend-full 拆出独立 job
2. 拆 core / full profile
3. 加 `timeout 1500` 硬上限
4. UI 验收结果写入 Job Summary

### 2.3 未闭环原因

本机无 `gh` CLI / GitHub API 凭据（匿名 API 已限流）。
无法读取 CI Job Logs 来定位根因。

### 2.4 交接（下轮第一步）

```bash
gh run view 45856160342 --log-failed   # Nightly #33
gh run download 45856160342 -n ui-acceptance-evidence
```

## 3. CI 门禁完整性

| 门禁 | 状态 | 说明 |
|---|---|---|
| PR Gate | ✅ 全绿 | 快速验证 |
| Nightly Gate | ❌ ui-acceptance 失败 | 其他 Job 通过 |
| Reliability Gate | NOT_RUN | 需手动 120 分钟窗口 |
| Release Gate | NOT_RUN | 缺 LLM Secret |

## 4. 核心专项覆盖检查

| 专项 | 进 CI | PG16 | 真实 Chromium |
|---|---|---|---|
| backend-full | ✅ | — | — |
| postgres | ✅ | ✅ | — |
| ui-acceptance | ✅ | — | ✅ |
| security | ✅ | — | — |
| llm-smoke | ✅ | — | — |

## 5. 交付

- 文档：本文件
- CI 结果：已核对（head_sha 逐条确认）
- 状态：**PARTIAL**（PR Gate 全绿，Nightly ui-acceptance 需 CI 日志定位）
